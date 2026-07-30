/**
 * World renderer (SPEC.md §5.1, §5.2, §13.4).
 *
 * One Canvas 2D pass per frame: parallax sky → hills → crowd → track surface →
 * zones → finish line → horses → particles → weather → vignette. Track identity
 * comes entirely from the theme object the server sends, so all six tracks share
 * this single renderer.
 */

import { clamp, hashNoise, roundRect, spring } from "../shared/motion.js";
import { BASE_VISIBLE_UNITS, cameraTargetX, visibleUnitsFor } from "./camera.js";
import { HorseView, drawHorse, shade } from "./horses.js";
import { Ambience, Particles } from "./particles.js";

const CAMERA_STIFFNESS = 4.5;
/** Hue rotation speed for rainbow tracks (Party Parrot Paradise). */
const RAINBOW_DEGREES_PER_SECOND = 70;
const ZOOM_STIFFNESS = 4;
const TRACK_TOP = 0.30;
const TRACK_BOTTOM = 0.94;

export class Renderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false });
    this.particles = new Particles();
    this.ambience = new Ambience();
    this.views = new Map();
    this.theme = null;
    this.horses = [];
    this.trackLength = 1000;
    this.camera = { x: 0, scale: 1, zoom: 1 };
    this.crowd = [];
    this.props = [];
    this.crowdSurge = 0;
    this.flash = 0;
    this.slowMotion = 1;
    this.dpr = 1;
    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  resize() {
    const { canvas } = this;
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.width = canvas.clientWidth;
    this.height = canvas.clientHeight;
    canvas.width = Math.floor(this.width * this.dpr);
    canvas.height = Math.floor(this.height * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  }

  /**
   * Cycle the palette of a rainbow track.
   *
   * Only the world colours move — horse colours, name plates and the finish are
   * left alone, because a horse whose colour drifts is a horse you can no longer
   * pick out of the pack.
   */
  _cycleRainbow(time) {
    const hue = (time * RAINBOW_DEGREES_PER_SECOND) % 360;
    const at = (offset, saturation, lightness) =>
      `hsl(${(hue + offset) % 360} ${saturation}% ${lightness}%)`;
    const theme = this.theme;
    theme.skyTop = at(0, 85, 62);
    theme.skyBottom = at(60, 95, 74);
    theme.hills = [at(210, 80, 46), at(150, 85, 58)];
    theme.ground = at(260, 70, 38);
    theme.groundDark = at(260, 70, 28);
    theme.lane = at(275, 68, 42);
    theme.laneAlt = at(295, 72, 47);
    theme.railPost = at(45, 95, 70);
    theme.accent = at(0, 95, 62);
  }

  /** Called when a race begins: adopt the theme and build decoration. */
  setRace({ horses, theme, trackLength, breeds }) {
    // Keep the original: a rainbow track mutates its own colours every frame.
    this.baseTheme = theme;
    this.theme = { ...theme };
    this.horses = horses;
    this.trackLength = trackLength || 1000;
    // Fold the chosen breed's render params into each horse's spec.
    this.views = new Map(
      horses.map((spec) => [
        spec.id,
        new HorseView({ ...spec, render: breeds?.get(spec.breed)?.render ?? {} }),
      ])
    );
    this.camera = { x: 0, scale: 1, zoom: 1 };
    this.particles.clear();
    this.ambience.setTheme(theme.ambient, theme.ambientColor);
    this.buildDecor();
  }

  buildDecor() {
    const theme = this.theme;
    this.crowd = Array.from({ length: 110 }, (_, index) => ({
      emoji: theme.crowd[index % theme.crowd.length],
      worldX: (index / 110) * this.trackLength * 1.15 - 40,
      row: index % 2,
      phase: hashNoise(index * 3.7) * Math.PI * 2,
      bounce: 0.6 + hashNoise(index * 1.9) * 0.8,
    }));
    this.props = Array.from({ length: 26 }, (_, index) => ({
      emoji: theme.props[index % theme.props.length],
      worldX: index * (this.trackLength / 22) + hashNoise(index) * 40,
      scale: 0.8 + hashNoise(index * 5.1) * 0.9,
    }));
  }

  surgeCrowd(strength = 1) {
    this.crowdSurge = Math.min(1.6, this.crowdSurge + strength);
  }

  whiteFlash(strength = 1) {
    this.flash = Math.min(1, strength);
  }

  // ------------------------------------------------------------------ camera

  updateCamera(dt, live) {
    const positions = live.map((entry) => entry.pos);
    if (!positions.length) return;
    const field = {
      leader: Math.max(...positions),
      trail: Math.min(...positions),
      mean: positions.reduce((sum, value) => sum + value, 0) / positions.length,
    };

    const { zoom } = visibleUnitsFor(field.leader - field.trail);
    this.camera.zoom = spring(this.camera.zoom, zoom, dt, ZOOM_STIFFNESS);
    const visible = BASE_VISIBLE_UNITS / this.camera.zoom;
    this.camera.scale = this.width / visible;

    const targetX = cameraTargetX(field, visible, this.trackLength);
    this.camera.x = spring(this.camera.x, targetX, dt, CAMERA_STIFFNESS);
  }

  worldToScreen(worldX) {
    return (worldX - this.camera.x) * this.camera.scale;
  }

  laneY(index, count) {
    const top = this.height * TRACK_TOP;
    const bottom = this.height * TRACK_BOTTOM;
    const laneHeight = (bottom - top) / Math.max(1, count);
    return top + laneHeight * (index + 0.72);
  }

  // ------------------------------------------------------------------- frame

  /**
   * @param {number} dt seconds since last frame (already slow-mo scaled)
   * @param {object} frame { live: [{id,pos,speedMultiplier,state,fx,rank}], zones, time, phase, countdown }
   */
  draw(dt, frame) {
    const ctx = this.ctx;
    const { live, zones = [], time } = frame;
    if (!this.theme) return;
    if (this.baseTheme?.rainbow) this._cycleRainbow(time);

    this.updateCamera(dt, live);
    this.crowdSurge *= Math.exp(-2.4 * dt);
    this.flash *= Math.exp(-6 * dt);
    this.ambience.update(performance.now());

    const leaderPos = live.reduce((best, entry) => Math.max(best, entry.pos), 0);
    for (const entry of live) {
      const view = this.views.get(entry.id);
      if (view) view.update(dt, entry, leaderPos);
    }

    this.drawSky();
    this.drawHills(time);
    this.drawCrowd(time);
    this.drawTrack(live.length);
    this.drawZones(zones, live.length);
    this.drawFinish(live.length, frame.phase);
    this.drawHorses(live);
    this.particles.update(dt);
    this.particles.draw(ctx);
    this.ambience.draw(ctx, this.width, this.height, time);
    this.drawVignette();
    if (this.flash > 0.01) {
      ctx.fillStyle = `rgba(255,255,255,${this.flash})`;
      ctx.fillRect(0, 0, this.width, this.height);
    }
  }

  drawSky() {
    const ctx = this.ctx;
    const theme = this.theme;
    const sky = ctx.createLinearGradient(0, 0, 0, this.height * 0.7);
    sky.addColorStop(0, theme.skyTop);
    sky.addColorStop(1, theme.skyBottom);
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, this.width, this.height);

    // Sun / moon: a soft disc parked at a theme-defined height.
    const sunY = this.height * (theme.sunY ?? 0.26);
    const glow = ctx.createRadialGradient(this.width * 0.78, sunY, 8, this.width * 0.78, sunY, 190);
    glow.addColorStop(0, theme.sunColor);
    glow.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(this.width * 0.78, sunY, 190, 0, Math.PI * 2);
    ctx.fill();
  }

  drawHills(time) {
    const ctx = this.ctx;
    const [near, far] = [this.theme.hills[1], this.theme.hills[0]];
    const horizon = this.height * TRACK_TOP;

    // Two parallax bands at 0.1× and 0.4× camera speed.
    this.drawHillBand(far, horizon - 46, 0.1, 62, time);
    this.drawHillBand(near, horizon - 18, 0.28, 44, time);

    ctx.fillStyle = this.theme.groundDark;
    ctx.fillRect(0, horizon - 4, this.width, this.height - horizon + 4);
    void ctx;
  }

  drawHillBand(color, baseY, parallax, amplitude, time) {
    const ctx = this.ctx;
    const offset = this.camera.x * this.camera.scale * parallax;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(0, baseY + amplitude);
    for (let x = 0; x <= this.width + 40; x += 40) {
      const wave =
        Math.sin((x + offset) * 0.004) * amplitude * 0.5 +
        Math.sin((x + offset) * 0.011 + 1.3) * amplitude * 0.25;
      ctx.lineTo(x, baseY - wave);
    }
    ctx.lineTo(this.width, baseY + amplitude + 60);
    ctx.lineTo(0, baseY + amplitude + 60);
    ctx.closePath();
    ctx.fill();
    void time;
  }

  drawCrowd(time) {
    const ctx = this.ctx;
    const horizon = this.height * TRACK_TOP;
    const surge = this.crowdSurge;

    // Trackside props sit behind the crowd at 0.8× parallax.
    ctx.textAlign = "center";
    ctx.textBaseline = "alphabetic";
    for (const prop of this.props) {
      const x = this.worldToScreen(prop.worldX) * 0.8 + this.width * 0.1;
      if (x < -80 || x > this.width + 80) continue;
      ctx.font = `${Math.round(46 * prop.scale)}px system-ui`;
      ctx.globalAlpha = 0.9;
      ctx.fillText(prop.emoji, x, horizon - 34);
    }
    ctx.globalAlpha = 1;

    // Grandstand band
    ctx.fillStyle = shade(this.theme.groundDark, -0.25);
    ctx.fillRect(0, horizon - 30, this.width, 34);

    for (const person of this.crowd) {
      const x = this.worldToScreen(person.worldX);
      if (x < -40 || x > this.width + 40) continue;
      const bounce =
        Math.abs(Math.sin(time * 3.4 * person.bounce + person.phase)) * (5 + surge * 16);
      ctx.font = `${person.row ? 20 : 24}px system-ui`;
      ctx.fillText(person.emoji, x, horizon - 6 - person.row * 15 - bounce);
    }
  }

  drawTrack(horseCount) {
    const ctx = this.ctx;
    const theme = this.theme;
    const top = this.height * TRACK_TOP;
    const bottom = this.height * TRACK_BOTTOM;

    ctx.fillStyle = theme.ground;
    ctx.fillRect(0, top, this.width, bottom - top);

    const laneHeight = (bottom - top) / Math.max(1, horseCount);
    for (let index = 0; index < horseCount; index += 1) {
      ctx.fillStyle = index % 2 ? theme.laneAlt : theme.lane;
      ctx.fillRect(0, top + laneHeight * index, this.width, laneHeight);
      ctx.strokeStyle = "rgba(255,255,255,0.10)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, top + laneHeight * index);
      ctx.lineTo(this.width, top + laneHeight * index);
      ctx.stroke();
    }

    // Furlong markers give the eye something to measure speed against.
    ctx.strokeStyle = "rgba(255,255,255,0.16)";
    ctx.lineWidth = 2;
    const step = 50;
    const first = Math.floor(this.camera.x / step) * step;
    for (let world = first; world < this.camera.x + BASE_VISIBLE_UNITS / this.camera.zoom + step; world += step) {
      const x = this.worldToScreen(world);
      ctx.globalAlpha = world % 250 === 0 ? 0.5 : 0.18;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // Rails
    ctx.fillStyle = theme.rail;
    ctx.fillRect(0, top - 5, this.width, 5);
    ctx.fillRect(0, bottom, this.width, 6);
    ctx.fillStyle = theme.railPost;
    for (let world = first; world < this.camera.x + 300; world += 25) {
      const x = this.worldToScreen(world);
      ctx.fillRect(x, bottom, 3, 12);
    }
  }

  drawZones(zones, horseCount) {
    const ctx = this.ctx;
    const top = this.height * TRACK_TOP;
    const bottom = this.height * TRACK_BOTTOM;
    for (const zone of zones) {
      const x1 = this.worldToScreen(zone.s);
      const x2 = this.worldToScreen(zone.e);
      if (x2 < -60 || x1 > this.width + 60) continue;
      const width = Math.max(4, x2 - x1);
      ctx.save();
      switch (zone.k) {
        case "mud":
          ctx.fillStyle = "rgba(96, 62, 33, 0.75)";
          ctx.fillRect(x1, top, width, bottom - top);
          break;
        case "oil":
          ctx.fillStyle = "rgba(18, 16, 30, 0.8)";
          ctx.fillRect(x1, top, width, bottom - top);
          ctx.fillStyle = "rgba(120, 200, 255, 0.18)";
          ctx.fillRect(x1, top, width, (bottom - top) * 0.4);
          break;
        case "syrup":
          ctx.fillStyle = "rgba(160, 70, 20, 0.55)";
          ctx.fillRect(x1, top, width, bottom - top);
          break;
        case "pad":
          ctx.fillStyle = "rgba(0, 229, 255, 0.35)";
          ctx.fillRect(x1, top, width, bottom - top);
          ctx.fillStyle = "rgba(255,255,255,0.85)";
          for (let i = 0; i < horseCount; i += 1) {
            const y = this.laneY(i, horseCount);
            ctx.beginPath();
            ctx.moveTo(x1 + 4, y - 8);
            ctx.lineTo(x1 + width - 4, y);
            ctx.lineTo(x1 + 4, y + 8);
            ctx.closePath();
            ctx.fill();
          }
          break;
        case "crater":
          ctx.fillStyle = "rgba(30, 22, 18, 0.8)";
          ctx.beginPath();
          ctx.ellipse((x1 + x2) / 2, (top + bottom) / 2, width / 2, (bottom - top) / 3, 0, 0, Math.PI * 2);
          ctx.fill();
          break;
        case "banana":
        case "apple":
        case "sugar_cube": {
          // One object, drawn once. A peel repeated down every lane reads as
          // litter strewn behind the field rather than a single hazard.
          const emoji = zone.k === "banana" ? "🍌" : zone.k === "apple" ? "🍎" : "🍬";
          const wobble = Math.sin(performance.now() / 220 + zone.i) * 4;
          ctx.font = "44px system-ui";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          const centre = (x1 + x2) / 2;
          const midLane = this.laneY((horseCount - 1) / 2, horseCount);
          if (zone.k !== "banana") {
            ctx.shadowColor = "rgba(255,255,255,0.9)";
            ctx.shadowBlur = 24;
          }
          ctx.fillText(emoji, centre, midLane + wobble);
          ctx.shadowBlur = 0;
          break;
        }
        default:
          ctx.fillStyle = "rgba(255,255,255,0.12)";
          ctx.fillRect(x1, top, width, bottom - top);
      }
      ctx.restore();
    }
  }

  drawFinish(horseCount, phase) {
    const ctx = this.ctx;
    const x = this.worldToScreen(this.trackLength);
    if (x < -120 || x > this.width + 200) return;
    const top = this.height * TRACK_TOP;
    const bottom = this.height * TRACK_BOTTOM;

    // Chequered line
    const squares = 14;
    const size = (bottom - top) / squares;
    for (let i = 0; i < squares; i += 1) {
      ctx.fillStyle = i % 2 ? "#12121c" : "#f6f4ee";
      ctx.fillRect(x - 8, top + i * size, 16, size);
    }

    // Arch + banner
    ctx.strokeStyle = this.theme.finishArch;
    ctx.lineWidth = 10;
    ctx.beginPath();
    ctx.moveTo(x - 46, top - 6);
    ctx.quadraticCurveTo(x, top - 96, x + 46, top - 6);
    ctx.stroke();

    ctx.fillStyle = this.theme.finishArch;
    roundRect(ctx, x - 92, top - 92, 184, 40, 12);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.font = "700 24px var(--font-display), system-ui";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("FINISH", x, top - 71);
    void phase;
    void horseCount;
  }

  drawHorses(live) {
    const ctx = this.ctx;
    const count = live.length;
    // Draw back-to-front by lane so overlaps look right.
    const ordered = [...live].sort((a, b) => a.lane - b.lane);
    for (const entry of ordered) {
      const view = this.views.get(entry.id);
      if (!view) continue;
      const x = this.worldToScreen(entry.pos);
      const y = this.laneY(entry.lane, count);
      if (x < -140 || x > this.width + 140) continue;

      const scale = clamp(this.height / 900, 0.7, 1.5) * (1 - entry.lane * 0.004);
      this.drawShadow(x, y, scale);
      drawHorse(ctx, view, entry, { x, y, scale });
      this.drawNamePlate(entry, x, y - 86 * scale, scale);
      this.emitHorseParticles(entry, x, y, scale);
    }
  }

  drawShadow(x, y, scale) {
    const ctx = this.ctx;
    ctx.fillStyle = "rgba(0,0,0,0.22)";
    ctx.beginPath();
    ctx.ellipse(x, y + 8 * scale, 30 * scale, 6 * scale, 0, 0, Math.PI * 2);
    ctx.fill();
  }

  drawNamePlate(entry, x, y, scale) {
    const ctx = this.ctx;
    const spec = this.views.get(entry.id)?.spec;
    if (!spec) return;
    const label = `${spec.emoji} ${spec.name}`;
    ctx.font = `800 ${Math.round(19 * scale)}px system-ui`;
    const textWidth = ctx.measureText(label).width;
    const padding = 12 * scale;
    const width = textWidth + padding * 2;
    const height = 27 * scale;

    ctx.save();
    ctx.globalAlpha = entry.state === "eliminated" ? 0.35 : 1;
    ctx.fillStyle = "rgba(16,17,30,0.82)";
    roundRect(ctx, x - width / 2, y - height, width, height, height / 2);
    ctx.fill();
    ctx.strokeStyle = spec.color;
    ctx.lineWidth = 2.5 * scale;
    ctx.stroke();

    ctx.fillStyle = "#fff";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, x, y - height / 2);

    // Backer pips: how many humans are sweating for this dinner option.
    if (entry.backers > 0) {
      ctx.fillStyle = spec.color;
      const pips = Math.min(entry.backers, 6);
      const pipWidth = 7 * scale;
      const startX = x - (pips * pipWidth) / 2;
      for (let i = 0; i < pips; i += 1) {
        ctx.beginPath();
        ctx.arc(startX + i * pipWidth + pipWidth / 2, y + 6 * scale, 2.6 * scale, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  }

  /** Continuous VFX driven by horse state — dust, flames, ice, mud spray. */
  emitHorseParticles(entry, x, y, scale) {
    const fx = entry.fx || [];
    const chance = Math.min(1, entry.speedMultiplier * 0.35);
    if (entry.state !== "frozen" && Math.random() < chance * 0.5) {
      this.particles.emit("dust", {
        x: x - 26 * scale,
        y: y + 6 * scale,
        count: 1,
        vx: -40,
        vy: -10,
        spread: 22,
        color: shade(this.theme.groundDark, 0.35),
        scale: scale * 0.9,
      });
    }
    if (fx.includes("rocket") && Math.random() < 0.7) {
      this.particles.emit("flame", {
        x: x - 24 * scale,
        y: y + 2 * scale,
        count: 2,
        vx: -160,
        spread: 30,
        color: "#ffb347",
        scale,
      });
    }
    if (fx.includes("muddy") && Math.random() < 0.15) {
      this.particles.emit("mud", {
        x: x - 20 * scale,
        y: y + 4 * scale,
        count: 1,
        vx: -70,
        vy: -60,
        color: "#6b4423",
        scale,
      });
    }
    if (entry.state === "tumble" && Math.random() < 0.5) {
      this.particles.emit("star", { x, y: y - 50 * scale, count: 1, vy: -40, scale: scale * 0.7 });
    }
  }

  drawVignette() {
    const ctx = this.ctx;
    const gradient = ctx.createRadialGradient(
      this.width / 2,
      this.height / 2,
      Math.min(this.width, this.height) * 0.35,
      this.width / 2,
      this.height / 2,
      Math.max(this.width, this.height) * 0.75
    );
    gradient.addColorStop(0, "rgba(0,0,0,0)");
    gradient.addColorStop(1, this.theme.vignette || "rgba(0,0,0,0.35)");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, this.width, this.height);
    if (this.theme.fog) {
      ctx.fillStyle = this.theme.fog;
      ctx.fillRect(0, 0, this.width, this.height);
    }
  }

  /** Big one-shot bursts requested by the event layer. */
  burst(kind, worldX, lane, horseCount, options = {}) {
    const x = this.worldToScreen(worldX ?? this.camera.x + 100);
    const y = lane != null ? this.laneY(lane, horseCount) : this.height * 0.6;
    this.particles.emit(kind, { x, y, ...options });
  }

  screenPositionOf(worldX, lane, horseCount) {
    return { x: this.worldToScreen(worldX), y: this.laneY(lane, horseCount) };
  }

  celebrate(colors) {
    this.particles.celebrate(this.width, this.height, colors);
  }
}
