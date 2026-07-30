/**
 * Motion primitives (SPEC.md §13.6).
 *
 * Everything the eye tracks moves on a spring, never a linear tween. These are
 * frame-rate independent so a 144 Hz monitor and a 30 fps TV stick agree.
 */

/** Critically-damped-ish spring step toward a target. */
export function spring(current, target, dt, stiffness = 10) {
  const blend = 1 - Math.exp(-stiffness * dt);
  return current + (target - current) * blend;
}

export function lerp(a, b, t) {
  return a + (b - a) * t;
}

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function easeOutCubic(t) {
  return 1 - Math.pow(1 - t, 3);
}

export function easeOutBack(t) {
  const c = 1.70158;
  return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2);
}

export function easeInOutQuad(t) {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}

/** Deterministic-ish jitter for decoration (crowd phases, prop offsets). */
export function hashNoise(seed) {
  const x = Math.sin(seed * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

/**
 * Damped screen shake. Call `add()` on impact, `value()` each frame.
 */
export class Shake {
  constructor() {
    this.amount = 0;
  }

  add(strength) {
    this.amount = Math.min(1.4, this.amount + strength);
  }

  update(dt) {
    this.amount *= Math.exp(-6 * dt);
    if (this.amount < 0.001) this.amount = 0;
  }

  offset(maxPixels = 14) {
    if (!this.amount) return { x: 0, y: 0 };
    const magnitude = this.amount * maxPixels;
    return {
      x: (Math.random() * 2 - 1) * magnitude,
      y: (Math.random() * 2 - 1) * magnitude,
    };
  }
}

/** Rounded rectangle path, used all over the canvas UI. */
export function roundRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}
