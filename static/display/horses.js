/**
 * Procedural horse rig (SPEC.md §13.3).
 *
 * A horse is ~8 code-drawn shapes plus an emoji jockey. Nothing is a sprite, so
 * it is crisp at any TV resolution and there is no asset pipeline. All the
 * personality lives in three places: the gallop cycle speeding up with actual
 * speed, secondary motion on mane/tail, and the eyes looking toward the leader.
 */

import { clamp, hashNoise, lerp, roundRect } from "../shared/motion.js";

const GALLOP_BASE_HZ = 1.9;
/** Revolutions per second while tumbling — fast enough to read as a wipeout. */
const SPIN_REVS_PER_SECOND = 1.6;

/** Defaults for every breed knob, so a breed only lists what it changes. */
const BREED_DEFAULTS = {
  bodyScale: 1,
  legLength: 1,
  legWidth: 1,
  neckLength: 1,
  tail: "flow",
  mane: "tufts",
  pattern: "none",
  horn: false,
  wings: false,
  glow: false,
  hop: false,
  beak: false,
  feathers: false,
  rainbow: false,
  tint: 0,
};

/** Per-horse animation memory that must persist between frames. */
export class HorseView {
  constructor(spec) {
    this.spec = spec;
    this.breed = { ...BREED_DEFAULTS, ...(spec.render || {}) };
    this.gallopPhase = hashNoise(spec.id * 7.7) * Math.PI * 2;
    this.bodyLean = 0;
    this.tailSway = 0;
    this.maneSway = 0;
    this.tumbleSpin = 0;
    this.spinning = false;
    this.hatFlyoff = null;
    this.lastState = "run";
    this.trail = [];
    this.eyeTarget = 0;
    this.plateScale = 1;
  }

  /**
   * @param {number} dt seconds
   * @param {object} live  { pos, speedMultiplier, state, fx }
   * @param {number} leaderPos world position of the current leader
   */
  update(dt, live, leaderPos) {
    const { speedMultiplier, state } = live;
    const frantic = clamp(speedMultiplier, 0.15, 2.2);
    // Hoppers (the parrot) bounce on a slower, bigger cycle than a gallop.
    const rate = this.breed.hop ? GALLOP_BASE_HZ * 0.75 : GALLOP_BASE_HZ;
    this.gallopPhase += dt * Math.PI * 2 * rate * frantic;

    const leanTarget = state === "boost" ? 1 : state === "slow" ? -0.5 : 0;
    this.bodyLean = lerp(this.bodyLean, leanTarget, 1 - Math.exp(-6 * dt));

    // Secondary motion trails the body by a frame or so — cheap, reads as weight.
    const sway = Math.sin(this.gallopPhase * 0.5) * (0.3 + frantic * 0.35);
    this.tailSway = lerp(this.tailSway, sway, 1 - Math.exp(-10 * dt));
    this.maneSway = lerp(this.maneSway, sway * 0.6, 1 - Math.exp(-14 * dt));

    if (state === "tumble") {
      // A slip has to read from across the room: whole revolutions, not a
      // wobble. Spinning by a fixed rate also lands the horse back upright
      // because the rotation is wound down explicitly when the state clears.
      this.tumbleSpin += dt * Math.PI * 2 * SPIN_REVS_PER_SECOND;
      this.spinning = true;
      if (!this.hatFlyoff) this.hatFlyoff = { x: 0, y: 0, vx: 90, vy: -160, spin: 0 };
    } else if (state === "stumble") {
      this.tumbleSpin = Math.sin(this.gallopPhase * 3) * 0.18;
      this.spinning = false;
    } else if (this.spinning) {
      // Unwind to the nearest whole turn so it finishes level, not crooked.
      const turns = Math.round(this.tumbleSpin / (Math.PI * 2));
      this.tumbleSpin = lerp(this.tumbleSpin, turns * Math.PI * 2, 1 - Math.exp(-9 * dt));
      if (Math.abs(this.tumbleSpin - turns * Math.PI * 2) < 0.02) {
        this.tumbleSpin = 0;
        this.spinning = false;
      }
    } else {
      this.tumbleSpin = lerp(this.tumbleSpin, 0, 1 - Math.exp(-8 * dt));
    }

    if (this.hatFlyoff) {
      const hat = this.hatFlyoff;
      hat.vy += 420 * dt;
      hat.x += hat.vx * dt;
      hat.y += hat.vy * dt;
      hat.spin += dt * 9;
      if (hat.y > 120) this.hatFlyoff = null;
    }

    this.eyeTarget = lerp(this.eyeTarget, leaderPos > live.pos ? 1 : -0.2, 1 - Math.exp(-4 * dt));
    this.lastState = state;
  }
}

/**
 * Draw one horse centred on (x, y) in screen space.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {HorseView} view
 * @param {object} live  { state, fx, speedMultiplier, rank }
 * @param {object} opts  { x, y, scale, muddy }
 */
export function drawHorse(ctx, view, live, { x, y, scale }) {
  const { spec } = view;
  const breed = view.breed;
  const fx = live.fx || [];
  const state = live.state;
  const ghost = fx.includes("ghost");
  const frozen = state === "frozen";
  // Rainbow breeds cycle their own colour; everything else uses the horse's.
  const color = breed.rainbow
    ? `hsl(${(performance.now() / 6) % 360} 90% 62%)`
    : breed.tint
      ? shade(spec.color, breed.tint)
      : spec.color;

  ctx.save();
  ctx.translate(x, y);
  ctx.scale(scale * breed.bodyScale, scale * breed.bodyScale);
  if (ghost) ctx.globalAlpha = 0.45;

  drawAuras(ctx, fx, live);

  ctx.save();
  if (state === "tumble" || view.spinning) {
    // Rotate about the body, and hop as it goes over — a flat spin looks like a
    // turntable; a spin with air under it looks like a fall.
    const hop = Math.abs(Math.sin(view.tumbleSpin)) * 10;
    ctx.translate(0, -16 - hop);
    ctx.rotate(view.tumbleSpin);
    ctx.translate(0, 16);
  } else if (state === "stumble") {
    ctx.rotate(view.tumbleSpin * 0.25);
  }

  // Hoppers spend part of the cycle airborne, which reads as a bounce.
  const hop = breed.hop ? Math.max(0, Math.sin(view.gallopPhase)) * 12 : 0;
  const bob = Math.sin(view.gallopPhase * 2) * 1.6 - hop;
  const stretch = 1 + view.bodyLean * 0.12;
  ctx.translate(0, bob);
  ctx.rotate(view.bodyLean * -0.06);

  if (breed.glow) drawGlow(ctx, color);
  if (breed.wings) drawWings(ctx, view, color);
  drawTail(ctx, view, color, breed.tail);
  drawLegs(ctx, view, color, frozen, breed);
  drawBody(ctx, color, stretch, fx.includes("muddy"), breed);
  drawNeckAndHead(ctx, view, spec, color, breed);
  drawJockey(ctx, view, spec);

  ctx.restore();

  if (frozen) drawIceBlock(ctx);
  ctx.restore();
}

// --------------------------------------------------------------------- parts

function drawBody(ctx, color, stretch, muddy, breed = {}) {
  ctx.save();
  ctx.scale(stretch, 1 / stretch);
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.ellipse(0, -16, 26, 14, 0, 0, Math.PI * 2);
  ctx.fill();

  // Belly shading gives the flat vector shape a little volume.
  ctx.fillStyle = "rgba(0, 0, 0, 0.16)";
  ctx.beginPath();
  ctx.ellipse(0, -11, 24, 8, 0, 0, Math.PI * 2);
  ctx.fill();

  drawPattern(ctx, breed.pattern, color);

  if (muddy) {
    ctx.fillStyle = "rgba(88, 57, 32, 0.75)";
    for (let i = 0; i < 4; i += 1) {
      const px = -18 + i * 11;
      ctx.beginPath();
      ctx.ellipse(px, -9 + (i % 2) * 3, 5, 3.2, 0, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  ctx.restore();
}

function drawLegs(ctx, view, color, frozen, breed = {}) {
  const phase = frozen ? 0 : view.gallopPhase;
  const length = breed.legLength ?? 1;
  ctx.strokeStyle = shade(color, -0.28);
  ctx.lineWidth = 4.6 * (breed.legWidth ?? 1);
  ctx.lineCap = "round";

  // Two phase-offset pairs read as a gallop without any keyframes.
  const legs = [
    { x: -15, offset: 0 },
    { x: -9, offset: Math.PI * 0.85 },
    { x: 11, offset: Math.PI },
    { x: 17, offset: Math.PI * 0.15 },
  ];
  // A hopper's legs move together instead of in phase-offset pairs.
  const pairs = breed.hop ? legs.map((leg) => ({ ...leg, offset: 0 })) : legs;
  for (const leg of pairs) {
    const swing = Math.sin(phase + leg.offset);
    const lift = Math.max(0, Math.cos(phase + leg.offset)) * 6;
    ctx.beginPath();
    ctx.moveTo(leg.x, -8);
    ctx.lineTo(leg.x + swing * 7, (-1 - lift * 0.4) * length);
    ctx.lineTo(leg.x + swing * 11, (6 - lift) * length);
    ctx.stroke();
  }

  if (breed.feathers) {
    // Clydesdale hooves: shaggy cuffs that read even at TV distance.
    ctx.fillStyle = shade(color, 0.55);
    for (const leg of pairs) {
      const swing = Math.sin(phase + leg.offset);
      ctx.beginPath();
      ctx.ellipse(leg.x + swing * 11, 4 * length, 5, 4, 0, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

/** Markings. Drawn inside the body transform, so they stretch with it. */
function drawPattern(ctx, pattern, color) {
  if (!pattern || pattern === "none") return;
  ctx.save();
  ctx.beginPath();
  ctx.ellipse(0, -16, 26, 14, 0, 0, Math.PI * 2);
  ctx.clip();
  if (pattern === "spots") {
    ctx.fillStyle = shade(color, -0.45);
    for (const [px, py, r] of [[-12, -20, 3.4], [-2, -14, 4.2], [9, -21, 3], [14, -13, 3.6], [2, -24, 2.6]]) {
      ctx.beginPath();
      ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fill();
    }
  } else if (pattern === "patches") {
    ctx.fillStyle = shade(color, 0.72);
    ctx.beginPath();
    ctx.ellipse(-11, -14, 12, 9, 0.3, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(12, -22, 9, 7, -0.4, 0, Math.PI * 2);
    ctx.fill();
  } else if (pattern === "stripes") {
    ctx.fillStyle = shade(color, -0.62);
    for (let i = -4; i <= 4; i += 1) {
      ctx.save();
      ctx.translate(i * 6, -16);
      ctx.rotate(0.22);
      ctx.fillRect(-1.6, -14, 3.2, 28);
      ctx.restore();
    }
  }
  ctx.restore();
}

/** A soft aura for breeds that are, canonically, magic. */
function drawGlow(ctx, color) {
  const glow = ctx.createRadialGradient(0, -18, 4, 0, -18, 52);
  glow.addColorStop(0, "rgba(255,255,255,0.28)");
  glow.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(0, -18, 52, 0, Math.PI * 2);
  ctx.fill();
  void color;
}

function drawWings(ctx, view, color) {
  const flap = Math.sin(view.gallopPhase * 1.6);
  ctx.save();
  ctx.fillStyle = shade(color, 0.6);
  for (const side of [-1, 1]) {
    ctx.save();
    ctx.translate(-2, -22);
    ctx.rotate(side * (0.5 + flap * 0.45));
    ctx.beginPath();
    ctx.ellipse(-14, 0, 18, 7, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
  ctx.restore();
}

function drawTail(ctx, view, color, style = "flow") {
  if (style === "none") return;
  const sway = view.tailSway;
  if (style === "puff") {
    ctx.fillStyle = shade(color, -0.4);
    ctx.beginPath();
    ctx.ellipse(-28 - sway, -16 + sway * 2, 8, 9, 0, 0, Math.PI * 2);
    ctx.fill();
    return;
  }
  if (style === "fan") {
    // Tail feathers, for wings and parrots.
    ctx.strokeStyle = shade(color, -0.25);
    ctx.lineWidth = 4;
    ctx.lineCap = "round";
    for (const spread of [-0.35, 0, 0.35]) {
      ctx.save();
      ctx.translate(-24, -20);
      ctx.rotate(spread + sway * 0.12);
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(-16, 6);
      ctx.stroke();
      ctx.restore();
    }
    return;
  }
  ctx.strokeStyle = shade(color, -0.4);
  ctx.lineWidth = 5;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(-24, -20);
  ctx.quadraticCurveTo(-34 - sway * 3, -16 + sway * 4, -38, -4 + sway * 6);
  ctx.stroke();
}

function drawNeckAndHead(ctx, view, spec, color = spec.color, breed = {}) {
  const lean = view.bodyLean;
  const neck = breed.neckLength ?? 1;

  const headY = -44 * neck;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(16, -22);
  ctx.quadraticCurveTo(30 + lean * 4, headY + 10, 32 + lean * 6, headY);
  ctx.lineTo(24 + lean * 5, headY);
  ctx.quadraticCurveTo(22 + lean * 2, -32 * neck, 12, -18);
  ctx.closePath();
  ctx.fill();

  // Head
  ctx.save();
  ctx.translate(29 + lean * 6, headY - 2);
  ctx.rotate(lean * 0.12);
  ctx.fillStyle = color;
  roundRect(ctx, -6, -7, 20, 13, 5);
  ctx.fill();

  if (breed.beak) {
    // A parrot's face: hooked beak instead of a muzzle.
    ctx.fillStyle = "#ffb703";
    ctx.beginPath();
    ctx.moveTo(7, -3);
    ctx.lineTo(19, 1);
    ctx.lineTo(7, 6);
    ctx.closePath();
    ctx.fill();
  } else {
    // Muzzle + nostril
    ctx.fillStyle = shade(color, -0.2);
    roundRect(ctx, 8, -2, 8, 7, 3);
    ctx.fill();
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    ctx.beginPath();
    ctx.ellipse(13.5, 1.5, 1.1, 1.4, 0, 0, Math.PI * 2);
    ctx.fill();

    // Ears
    ctx.fillStyle = shade(color, -0.12);
    ctx.beginPath();
    ctx.moveTo(-2, -7);
    ctx.lineTo(1, -15);
    ctx.lineTo(4, -7);
    ctx.closePath();
    ctx.fill();
  }

  if (breed.horn) {
    ctx.fillStyle = "#ffe9a8";
    ctx.beginPath();
    ctx.moveTo(0, -8);
    ctx.lineTo(3, -24);
    ctx.lineTo(6, -8);
    ctx.closePath();
    ctx.fill();
  }

  drawEyes(ctx, view);
  ctx.restore();

  drawMane(ctx, view, color, breed, lean, headY);
}

/** Mane styles, drawn after the neck so they overlap it. */
function drawMane(ctx, view, color, breed, lean, headY) {
  const style = breed.mane ?? "tufts";
  if (style === "none") return;
  ctx.fillStyle = shade(color, -0.45);
  if (style === "mohawk") {
    for (let i = 0; i < 5; i += 1) {
      const t = i / 4;
      const mx = lerp(16, 27 + lean * 5, t);
      const my = lerp(-24, headY, t);
      ctx.beginPath();
      ctx.moveTo(mx, my);
      ctx.lineTo(mx - 3 - view.maneSway, my - 11);
      ctx.lineTo(mx + 3, my);
      ctx.closePath();
      ctx.fill();
    }
    return;
  }
  const tufts = style === "wild" ? 5 : 3;
  const size = style === "wild" ? 7 : 5.5;
  for (let i = 0; i < tufts; i += 1) {
    const t = i / (tufts - 1);
    const mx = lerp(16, 27 + lean * 5, t);
    const my = lerp(-24, headY, t);
    ctx.beginPath();
    ctx.ellipse(mx - view.maneSway * 2, my, size, size * 0.72, -0.5, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawEyes(ctx, view) {
  const googly = view.lastState !== "frozen" && view.spec.googly;
  ctx.fillStyle = "#fff";
  ctx.beginPath();
  ctx.ellipse(4, -2, googly ? 3.6 : 2.8, googly ? 3.6 : 2.8, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#14131f";
  const look = view.eyeTarget * 1.2;
  ctx.beginPath();
  ctx.ellipse(4.6 + look, -1.6 + (googly ? Math.sin(view.gallopPhase * 4) * 1.2 : 0), 1.5, 1.7, 0, 0, Math.PI * 2);
  ctx.fill();
}

function drawJockey(ctx, view, spec) {
  const bob = Math.sin(view.gallopPhase * 2 - 0.5) * 2.2;
  ctx.save();
  ctx.translate(-2, -34 + bob);
  ctx.rotate(view.bodyLean * -0.12);
  ctx.font = "20px system-ui";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(spec.jockey, 0, 0);

  // The food emoji is the point of the whole game: give it the saddle.
  ctx.font = "22px system-ui";
  ctx.fillText(spec.emoji, -14, 6);
  ctx.restore();

  if (view.hatFlyoff) {
    const hat = view.hatFlyoff;
    ctx.save();
    ctx.translate(hat.x, -40 + hat.y);
    ctx.rotate(hat.spin);
    ctx.font = "14px system-ui";
    ctx.fillText("🤠", 0, 0);
    ctx.restore();
  }
}

function drawIceBlock(ctx) {
  ctx.save();
  ctx.fillStyle = "rgba(150, 220, 255, 0.42)";
  ctx.strokeStyle = "rgba(220, 245, 255, 0.85)";
  ctx.lineWidth = 2;
  roundRect(ctx, -34, -58, 70, 66, 8);
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

// --------------------------------------------------------------------- auras

function drawAuras(ctx, fx, live) {
  if (fx.includes("shield")) {
    ctx.save();
    ctx.strokeStyle = "rgba(255, 214, 102, 0.9)";
    ctx.fillStyle = "rgba(255, 214, 102, 0.13)";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.ellipse(0, -24, 44, 40, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }
  if (fx.includes("diamond")) {
    ctx.save();
    ctx.strokeStyle = "rgba(180, 240, 255, 0.95)";
    ctx.lineWidth = 2;
    for (let i = 0; i < 6; i += 1) {
      const angle = (i / 6) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(Math.cos(angle) * 30, -24 + Math.sin(angle) * 26);
      ctx.lineTo(Math.cos(angle) * 44, -24 + Math.sin(angle) * 38);
      ctx.stroke();
    }
    ctx.restore();
  }
  if (fx.includes("golden")) {
    const glow = ctx.createRadialGradient(0, -24, 6, 0, -24, 60);
    glow.addColorStop(0, "rgba(255, 215, 80, 0.55)");
    glow.addColorStop(1, "rgba(255, 215, 80, 0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(0, -24, 60, 0, Math.PI * 2);
    ctx.fill();
  }
  if (fx.includes("lunge")) {
    ctx.save();
    ctx.globalAlpha = 0.5 + Math.sin(performance.now() / 180) * 0.3;
    ctx.fillStyle = "#fff";
    ctx.font = "13px system-ui";
    ctx.textAlign = "center";
    ctx.fillText("📸", 0, -62);
    ctx.restore();
  }
  if (live.state === "boost") {
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,0.55)";
    ctx.lineWidth = 2;
    for (let i = 0; i < 4; i += 1) {
      const y = -34 + i * 9;
      const len = 26 + i * 4;
      ctx.beginPath();
      ctx.moveTo(-30 - len, y);
      ctx.lineTo(-30, y);
      ctx.stroke();
    }
    ctx.restore();
  }
}

// -------------------------------------------------------------------- colour

/** Lighten (positive) or darken (negative) a hex colour. */
export function shade(hex, amount) {
  const value = hex.replace("#", "");
  const num = parseInt(value.length === 3 ? value.replace(/./g, "$&$&") : value, 16);
  const channels = [(num >> 16) & 255, (num >> 8) & 255, num & 255].map((channel) => {
    const shifted = amount >= 0 ? channel + (255 - channel) * amount : channel * (1 + amount);
    return clamp(Math.round(shifted), 0, 255);
  });
  return `rgb(${channels.join(",")})`;
}
