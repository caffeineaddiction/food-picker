/**
 * One pooled particle system for everything (SPEC.md §13.4).
 *
 * Confetti, dust, sparks, leaves, money, feathers, rain, sprinkles and emoji
 * bursts all reuse the same fixed-size pool, so the frame cost is bounded no
 * matter how chaotic the race gets.
 */

import { hashNoise } from "../shared/motion.js";

const MAX_PARTICLES = 620;

const KINDS = {
  dust: { gravity: -20, drag: 2.6, size: [3, 8], life: [0.4, 0.9], shape: "puff" },
  spark: { gravity: 380, drag: 0.6, size: [2, 4], life: [0.3, 0.7], shape: "line" },
  confetti: { gravity: 260, drag: 0.9, size: [6, 12], life: [1.8, 3.4], shape: "rect" },
  money: { gravity: 150, drag: 1.4, size: [12, 18], life: [1.4, 2.6], shape: "emoji", emoji: "💵" },
  leaf: { gravity: 40, drag: 1.1, size: [8, 14], life: [1.2, 2.2], shape: "emoji", emoji: "🍃" },
  feather: { gravity: 30, drag: 1.6, size: [8, 13], life: [1.4, 2.4], shape: "emoji", emoji: "🪶" },
  sprinkle: { gravity: 90, drag: 0.8, size: [3, 6], life: [1.6, 3.0], shape: "rect" },
  star: { gravity: -10, drag: 2.0, size: [10, 16], life: [0.6, 1.1], shape: "emoji", emoji: "⭐" },
  mud: { gravity: 420, drag: 0.7, size: [3, 7], life: [0.4, 0.8], shape: "puff" },
  emoji: { gravity: -60, drag: 1.2, size: [22, 34], life: [1.6, 2.4], shape: "emoji" },
  smoke: { gravity: -40, drag: 2.2, size: [10, 22], life: [0.6, 1.2], shape: "puff" },
  flame: { gravity: -120, drag: 3.0, size: [6, 14], life: [0.2, 0.45], shape: "puff" },
};

function pick(range, random) {
  return range[0] + (range[1] - range[0]) * random;
}

export class Particles {
  constructor() {
    this.pool = Array.from({ length: MAX_PARTICLES }, () => ({ alive: false }));
    this.cursor = 0;
  }

  /** Grab the next slot, recycling the oldest when saturated. */
  _slot() {
    for (let i = 0; i < MAX_PARTICLES; i += 1) {
      const index = (this.cursor + i) % MAX_PARTICLES;
      if (!this.pool[index].alive) {
        this.cursor = (index + 1) % MAX_PARTICLES;
        return this.pool[index];
      }
    }
    const fallback = this.pool[this.cursor];
    this.cursor = (this.cursor + 1) % MAX_PARTICLES;
    return fallback;
  }

  /**
   * @param {string} kind key of KINDS
   * @param {object} options { x, y, count, vx, vy, spread, color, emoji, scale }
   */
  emit(kind, { x, y, count = 8, vx = 0, vy = 0, spread = 60, color = "#fff", emoji, scale = 1 }) {
    const config = KINDS[kind] || KINDS.dust;
    for (let i = 0; i < count; i += 1) {
      const particle = this._slot();
      const seed = hashNoise(x + y + i * 3.13 + performance.now() * 0.001);
      particle.alive = true;
      particle.kind = kind;
      particle.x = x + (Math.random() - 0.5) * 8;
      particle.y = y + (Math.random() - 0.5) * 8;
      particle.vx = vx + (Math.random() - 0.5) * spread;
      particle.vy = vy + (Math.random() - 0.5) * spread;
      particle.life = pick(config.life, Math.random());
      particle.maxLife = particle.life;
      particle.size = pick(config.size, seed) * scale;
      particle.color = color;
      particle.emoji = emoji || config.emoji || "✨";
      particle.spin = (Math.random() - 0.5) * 8;
      particle.rotation = Math.random() * Math.PI;
      particle.config = config;
    }
  }

  /** Confetti cannons for the ceremony (§13.5.2). */
  celebrate(width, height, colors) {
    for (const side of [0, 1]) {
      this.emit("confetti", {
        x: side ? width - 40 : 40,
        y: height - 20,
        count: 90,
        vx: side ? -420 : 420,
        vy: -640,
        spread: 340,
        color: colors[side % colors.length],
      });
    }
  }

  update(dt) {
    for (const particle of this.pool) {
      if (!particle.alive) continue;
      const config = particle.config;
      particle.life -= dt;
      if (particle.life <= 0) {
        particle.alive = false;
        continue;
      }
      const drag = Math.exp(-config.drag * dt);
      particle.vx *= drag;
      particle.vy = particle.vy * drag + config.gravity * dt;
      particle.x += particle.vx * dt;
      particle.y += particle.vy * dt;
      particle.rotation += particle.spin * dt;
    }
  }

  draw(ctx) {
    for (const particle of this.pool) {
      if (!particle.alive) continue;
      const fade = Math.min(1, particle.life / (particle.maxLife * 0.4));
      ctx.save();
      ctx.globalAlpha = fade;
      ctx.translate(particle.x, particle.y);
      ctx.rotate(particle.rotation);
      switch (particle.config.shape) {
        case "rect":
          ctx.fillStyle = particle.color;
          ctx.fillRect(-particle.size / 2, -particle.size / 4, particle.size, particle.size / 2);
          break;
        case "line":
          ctx.strokeStyle = particle.color;
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(0, 0);
          ctx.lineTo(particle.size * 2, 0);
          ctx.stroke();
          break;
        case "emoji":
          ctx.font = `${particle.size}px system-ui`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(particle.emoji, 0, 0);
          break;
        default:
          ctx.fillStyle = particle.color;
          ctx.beginPath();
          ctx.arc(0, 0, particle.size / 2, 0, Math.PI * 2);
          ctx.fill();
      }
      ctx.restore();
    }
  }

  get activeCount() {
    return this.pool.reduce((total, particle) => total + (particle.alive ? 1 : 0), 0);
  }

  clear() {
    for (const particle of this.pool) particle.alive = false;
  }
}

/**
 * Ambient weather/atmosphere layer, kept separate from the pool because it is
 * continuous and cheap to compute analytically.
 */
export class Ambience {
  constructor() {
    this.mode = "dust";
    this.color = "#fff";
    this.items = Array.from({ length: 150 }, (_, index) => ({
      x: Math.random(),
      y: Math.random(),
      speed: 0.2 + Math.random() * 0.8,
      size: 1 + Math.random() * 3,
      phase: Math.random() * Math.PI * 2,
    }));
    this.weather = null;
    this.weatherUntil = 0;
  }

  setTheme(mode, color) {
    this.mode = mode || "dust";
    this.color = color || "#fff";
  }

  setWeather(kind, seconds) {
    this.weather = kind;
    this.weatherUntil = performance.now() + seconds * 1000;
  }

  update(now) {
    if (this.weather && now > this.weatherUntil) this.weather = null;
  }

  draw(ctx, width, height, time) {
    const mode = this.weather === "rain" ? "rain" : this.mode;
    ctx.save();
    switch (mode) {
      case "rain":
        ctx.strokeStyle = "rgba(190, 220, 255, 0.55)";
        ctx.lineWidth = 1.6;
        for (const item of this.items) {
          const y = (item.y + time * item.speed * 0.9) % 1;
          const x = (item.x + y * 0.08) % 1;
          ctx.beginPath();
          ctx.moveTo(x * width, y * height);
          ctx.lineTo(x * width - 6, y * height + 18);
          ctx.stroke();
        }
        break;
      case "stars":
        for (const item of this.items) {
          const twinkle = 0.35 + 0.65 * Math.abs(Math.sin(time * 1.5 + item.phase));
          ctx.fillStyle = `rgba(255,255,255,${twinkle * 0.9})`;
          ctx.fillRect(item.x * width, item.y * height * 0.6, item.size * 0.8, item.size * 0.8);
        }
        break;
      case "confetti": {
        // Rainbow ticker-tape, permanently falling.
        for (const item of this.items) {
          const y = (item.y + time * item.speed * 0.3) % 1;
          const hue = (item.phase * 360 + time * 90) % 360;
          ctx.fillStyle = `hsl(${hue} 95% 65%)`;
          ctx.save();
          ctx.translate(item.x * width, y * height);
          ctx.rotate(item.phase * 6 + time * 3);
          ctx.fillRect(0, 0, item.size * 2.4, item.size * 1.1);
          ctx.restore();
        }
        break;
      }
      case "sprinkles":
        for (const item of this.items) {
          const y = (item.y + time * item.speed * 0.25) % 1;
          ctx.fillStyle = ["#fff", "#FF4D9D", "#4EA8FF", "#3EDC81"][Math.floor(item.phase * 4) % 4];
          ctx.save();
          ctx.translate(item.x * width, y * height);
          ctx.rotate(item.phase + time);
          ctx.fillRect(0, 0, item.size * 1.6, item.size * 0.7);
          ctx.restore();
        }
        break;
      case "ticker":
        ctx.fillStyle = "rgba(240, 236, 220, 0.75)";
        for (const item of this.items) {
          const y = (item.y + time * item.speed * 0.35) % 1;
          ctx.save();
          ctx.translate(item.x * width, y * height);
          ctx.rotate(Math.sin(time + item.phase) * 0.6);
          ctx.fillRect(0, 0, item.size * 5, item.size * 0.9);
          ctx.restore();
        }
        break;
      case "grid":
        ctx.strokeStyle = "rgba(0, 229, 255, 0.13)";
        ctx.lineWidth = 1;
        for (let i = 0; i < 26; i += 1) {
          const x = ((i / 26 + time * 0.02) % 1) * width;
          ctx.beginPath();
          ctx.moveTo(x, height * 0.32);
          ctx.lineTo(x, height);
          ctx.stroke();
        }
        break;
      case "paper":
        ctx.fillStyle = "rgba(255,255,255,0.5)";
        for (const item of this.items.slice(0, 40)) {
          const x = (item.x + time * item.speed * 0.12) % 1;
          ctx.save();
          ctx.translate(x * width, item.y * height);
          ctx.rotate(time * item.speed);
          ctx.fillRect(0, 0, item.size * 3, item.size * 4);
          ctx.restore();
        }
        break;
      default:
        ctx.fillStyle = this.color;
        ctx.globalAlpha = 0.22;
        for (const item of this.items.slice(0, 70)) {
          const x = (item.x + time * item.speed * 0.05) % 1;
          const drift = Math.sin(time * item.speed + item.phase) * 8;
          ctx.beginPath();
          ctx.arc(x * width, item.y * height + drift, item.size, 0, Math.PI * 2);
          ctx.fill();
        }
    }
    ctx.restore();
  }
}
