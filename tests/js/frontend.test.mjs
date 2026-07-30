/**
 * Frontend logic tests: `node --test tests/js`
 *
 * Only the pure, testable parts of the display are covered here — snapshot
 * interpolation, the particle pool, the horse rig's animation state and the
 * colour helper. Anything touching canvas or the DOM is verified by running the
 * app; anything that decides how the race *looks over time* is verified here.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { INTERPOLATION_DELAY_MS, interpolateFrame, pushSnapshot } from "../../static/display/interpolate.js";
import {
  BASE_VISIBLE_UNITS,
  FINISH_LOCK_FRACTION,
  cameraTargetX,
  screenFraction,
  visibleUnitsFor,
} from "../../static/display/camera.js";
import { HorseView, shade } from "../../static/display/horses.js";
import { Ambience, Particles } from "../../static/display/particles.js";
import { Shake, clamp, spring } from "../../static/shared/motion.js";

const laneOf = (horseId) => horseId;

function snapshot(tick, positions, extra = {}) {
  return {
    t: "snapshot",
    k: tick,
    rt: tick * 0.05,
    ph: "running",
    h: positions.map((pos, index) => ({
      i: index,
      p: pos,
      l: 0,
      v: 1,
      r: index + 1,
      b: 1,
      st: "run",
      fx: [],
    })),
    o: positions.map((_, index) => index),
    z: [],
    ...extra,
  };
}

describe("snapshot interpolation", () => {
  it("returns null with an empty buffer", () => {
    assert.equal(interpolateFrame([], 1000, laneOf), null);
  });

  it("holds a single snapshot rather than jumping", () => {
    const buffer = pushSnapshot([], snapshot(1, [100, 90]), 1000);
    const frame = interpolateFrame(buffer, 1000 + INTERPOLATION_DELAY_MS, laneOf);
    assert.equal(frame.live[0].pos, 100);
    assert.equal(frame.interpolation, 1);
  });

  it("lerps positions between the two bracketing snapshots", () => {
    const buffer = [];
    pushSnapshot(buffer, snapshot(1, [100]), 1000);
    pushSnapshot(buffer, snapshot(2, [120]), 1050);
    // Render time sits exactly halfway between the two arrival times.
    const frame = interpolateFrame(buffer, 1025 + INTERPOLATION_DELAY_MS, laneOf);
    assert.equal(frame.live[0].pos, 110);
    assert.equal(frame.interpolation, 0.5);
  });

  it("renders in the past, never ahead of the newest snapshot", () => {
    const buffer = [];
    pushSnapshot(buffer, snapshot(1, [100]), 1000);
    pushSnapshot(buffer, snapshot(2, [200]), 1050);
    const frame = interpolateFrame(buffer, 1050, laneOf);
    assert.ok(frame.live[0].pos <= 200, "must not extrapolate past the last snapshot");
  });

  it("takes visual state from the newer snapshot", () => {
    const buffer = [];
    pushSnapshot(buffer, snapshot(1, [100]), 1000);
    const boosted = snapshot(2, [130]);
    boosted.h[0].st = "boost";
    boosted.h[0].fx = ["boost", "shield"];
    pushSnapshot(buffer, boosted, 1050);
    const frame = interpolateFrame(buffer, 1025 + INTERPOLATION_DELAY_MS, laneOf);
    assert.equal(frame.live[0].state, "boost");
    assert.deepEqual(frame.live[0].fx, ["boost", "shield"]);
  });

  it("survives a horse missing from the newer snapshot", () => {
    const buffer = [];
    pushSnapshot(buffer, snapshot(1, [100, 80]), 1000);
    const partial = snapshot(2, [130]);
    pushSnapshot(buffer, partial, 1050);
    const frame = interpolateFrame(buffer, 1025 + INTERPOLATION_DELAY_MS, laneOf);
    assert.equal(frame.live.length, 2);
    assert.equal(frame.live[1].pos, 80);
  });

  it("caps the buffer so memory cannot grow during a long race", () => {
    const buffer = [];
    for (let tick = 0; tick < 200; tick += 1) {
      pushSnapshot(buffer, snapshot(tick, [tick]), 1000 + tick * 50, 24);
    }
    assert.equal(buffer.length, 24);
    assert.equal(buffer[buffer.length - 1].snapshot.k, 199);
  });

  it("falls back to lane 0 for an unknown horse id", () => {
    const buffer = pushSnapshot([], snapshot(1, [10]), 1000);
    const frame = interpolateFrame(buffer, 1200, () => -1);
    assert.equal(frame.live[0].lane, 0);
  });
});

describe("particle pool", () => {
  it("emits and expires particles", () => {
    const particles = new Particles();
    particles.emit("confetti", { x: 10, y: 10, count: 20 });
    assert.equal(particles.activeCount, 20);
    for (let step = 0; step < 400; step += 1) particles.update(0.05);
    assert.equal(particles.activeCount, 0);
  });

  it("never exceeds its fixed pool size", () => {
    const particles = new Particles();
    for (let burst = 0; burst < 50; burst += 1) {
      particles.emit("spark", { x: 0, y: 0, count: 100 });
    }
    assert.ok(particles.activeCount <= 620, `pool grew to ${particles.activeCount}`);
  });

  it("keeps positions finite under gravity and drag", () => {
    const particles = new Particles();
    particles.emit("money", { x: 0, y: 0, count: 5, vy: -600 });
    for (let step = 0; step < 40; step += 1) particles.update(0.016);
    for (const particle of particles.pool.filter((p) => p.alive)) {
      assert.ok(Number.isFinite(particle.x) && Number.isFinite(particle.y));
    }
  });

  it("clears on demand for a fresh race", () => {
    const particles = new Particles();
    particles.emit("dust", { x: 0, y: 0, count: 30 });
    particles.clear();
    assert.equal(particles.activeCount, 0);
  });
});

describe("ambience", () => {
  it("expires weather after its duration", () => {
    const ambience = new Ambience();
    ambience.setWeather("rain", 1);
    ambience.update(0);
    assert.equal(ambience.weather, "rain");
    ambience.update(performance.now() + 5000);
    assert.equal(ambience.weather, null);
  });
});

describe("horse rig", () => {
  const spec = { id: 0, name: "PIZZA", emoji: "🍕", color: "#FF5D5D", jockey: "🤠" };

  it("advances the gallop faster when the horse runs faster", () => {
    const slow = new HorseView(spec);
    const fast = new HorseView(spec);
    fast.gallopPhase = slow.gallopPhase;
    slow.update(0.1, { pos: 0, speedMultiplier: 0.6, state: "run" }, 100);
    fast.update(0.1, { pos: 0, speedMultiplier: 2.0, state: "run" }, 100);
    assert.ok(fast.gallopPhase > slow.gallopPhase);
  });

  it("leans forward on a boost and back when slowed", () => {
    const view = new HorseView(spec);
    for (let step = 0; step < 30; step += 1) {
      view.update(0.05, { pos: 0, speedMultiplier: 1.5, state: "boost" }, 100);
    }
    assert.ok(view.bodyLean > 0.5);
    for (let step = 0; step < 40; step += 1) {
      view.update(0.05, { pos: 0, speedMultiplier: 0.6, state: "slow" }, 100);
    }
    assert.ok(view.bodyLean < 0);
  });

  it("throws the jockey's hat on a tumble and lands it again", () => {
    const view = new HorseView(spec);
    view.update(0.05, { pos: 0, speedMultiplier: 1, state: "tumble" }, 100);
    assert.ok(view.hatFlyoff, "hat should fly off");
    for (let step = 0; step < 200; step += 1) {
      view.update(0.05, { pos: 0, speedMultiplier: 1, state: "run" }, 100);
    }
    assert.equal(view.hatFlyoff, null);
  });

  it("looks toward the leader", () => {
    const view = new HorseView(spec);
    for (let step = 0; step < 40; step += 1) {
      view.update(0.05, { pos: 10, speedMultiplier: 1, state: "run" }, 500);
    }
    assert.ok(view.eyeTarget > 0.5, "should look forward at a horse ahead");
  });
});

describe("colour helper", () => {
  it("lightens and darkens without leaving the byte range", () => {
    assert.equal(shade("#000000", 1), "rgb(255,255,255)");
    assert.equal(shade("#ffffff", -1), "rgb(0,0,0)");
    assert.match(shade("#FF5D5D", -0.3), /^rgb\(\d+,\d+,\d+\)$/);
  });

  it("accepts three-digit hex", () => {
    assert.equal(shade("#fff", 0), "rgb(255,255,255)");
  });
});

describe("motion helpers", () => {
  it("springs toward the target without overshooting", () => {
    let value = 0;
    for (let step = 0; step < 200; step += 1) value = spring(value, 10, 0.016, 10);
    assert.ok(Math.abs(value - 10) < 0.01);
    assert.ok(value <= 10.0001, "critically damped: no overshoot");
  });

  it("is frame-rate independent within tolerance", () => {
    let slow = 0;
    let fast = 0;
    for (let step = 0; step < 60; step += 1) slow = spring(slow, 1, 1 / 60, 8);
    for (let step = 0; step < 120; step += 1) fast = spring(fast, 1, 1 / 120, 8);
    assert.ok(Math.abs(slow - fast) < 0.01, `${slow} vs ${fast}`);
  });

  it("clamps", () => {
    assert.equal(clamp(5, 0, 1), 1);
    assert.equal(clamp(-5, 0, 1), 0);
  });

  it("decays screen shake to nothing", () => {
    const shake = new Shake();
    shake.add(1);
    assert.ok(shake.amount > 0);
    for (let step = 0; step < 200; step += 1) shake.update(0.016);
    assert.equal(shake.amount, 0);
    assert.deepEqual(shake.offset(), { x: 0, y: 0 });
  });

  it("caps shake so a chaotic race cannot make the screen unreadable", () => {
    const shake = new Shake();
    for (let hit = 0; hit < 20; hit += 1) shake.add(1);
    assert.ok(shake.amount <= 1.4);
  });
});

describe("camera framing", () => {
  const TRACK = 1000;

  it("keeps the field on screen through the finish", () => {
    // The bug this guards: framing the finish line lurched the camera forward
    // and pushed every horse off the left edge, so the race looked over.
    for (let leader = TRACK * 0.8; leader <= TRACK; leader += 5) {
      const trail = leader - 60;
      const mean = leader - 30;
      const { visible } = visibleUnitsFor(leader - trail);
      const cameraX = cameraTargetX({ leader, trail, mean }, visible, TRACK);
      const trailAt = screenFraction(trail, cameraX, visible);
      assert.ok(trailAt >= 0, `last horse fell off screen at leader=${leader} (${trailAt})`);
      assert.ok(trailAt <= 1, `last horse ahead of the frame at leader=${leader}`);
    }
  });

  it("settles the finish line in the right half of the screen", () => {
    const leader = TRACK * 0.99;
    const { visible } = visibleUnitsFor(20);
    const cameraX = cameraTargetX({ leader, trail: leader - 20, mean: leader - 10 }, visible, TRACK);
    const lineAt = screenFraction(TRACK, cameraX, visible);
    assert.ok(lineAt > 0.5 && lineAt <= 0.9, `finish line at ${lineAt.toFixed(2)} of screen`);
  });

  it("does not jump when the leader crosses into finish framing", () => {
    const before = TRACK * FINISH_LOCK_FRACTION - 1;
    const after = TRACK * FINISH_LOCK_FRACTION + 1;
    const { visible } = visibleUnitsFor(40);
    const a = cameraTargetX({ leader: before, trail: before - 40, mean: before - 20 }, visible, TRACK);
    const b = cameraTargetX({ leader: after, trail: after - 40, mean: after - 20 }, visible, TRACK);
    assert.ok(Math.abs(b - a) < visible * 0.25, `camera jumped ${Math.abs(b - a).toFixed(1)} units`);
  });

  it("zooms out for a strung-out field instead of cropping it", () => {
    const tight = visibleUnitsFor(20);
    const strung = visibleUnitsFor(400);
    assert.equal(tight.visible, BASE_VISIBLE_UNITS);
    assert.ok(strung.visible > tight.visible);
    assert.ok(strung.zoom >= 0.75, "zoom is clamped so horses never get tiny");
  });

  it("never scrolls behind the start line", () => {
    const { visible } = visibleUnitsFor(10);
    const cameraX = cameraTargetX({ leader: 5, trail: 0, mean: 2 }, visible, TRACK);
    assert.ok(cameraX >= -30);
  });
});
