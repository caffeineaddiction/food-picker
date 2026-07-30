/**
 * Display HUD: the DOM layer over the canvas (SPEC.md §5.1).
 *
 * Everything here is designed to be read from across a room: big type, high
 * contrast, and a hard cap on how much can be on screen at once. The
 * NotificationLane enforces the clutter budget in code rather than by
 * convention — at most two cards, everything else queues.
 */

const MAX_VISIBLE_NOTIFICATIONS = 2;
const NOTIFICATION_MS = 2600;
const BANNER_MS = 1900;

/** Ordered 1st→last chips that slide when positions change. */
export class LeaderboardRail {
  constructor(root) {
    this.root = root;
    this.chips = new Map();
    this.chipHeight = 0;
  }

  build(horses) {
    this.root.innerHTML = "";
    this.chips = new Map();
    for (const horse of horses) {
      const chip = document.createElement("div");
      chip.className = "rail__chip";
      chip.style.setProperty("--horse-color", horse.color);
      chip.innerHTML = `
        <span class="rail__pos">1</span>
        <span class="rail__emoji">${horse.emoji}</span>
        <span class="rail__name">${horse.name}</span>
      `;
      this.root.appendChild(chip);
      this.chips.set(horse.id, chip);
    }
  }

  /** @param {number[]} order horse ids, 1st first */
  update(order, states = {}) {
    order.forEach((horseId, index) => {
      const chip = this.chips.get(horseId);
      if (!chip) return;
      chip.style.transform = `translateY(${index * 100}%)`;
      chip.dataset.rank = String(index + 1);
      chip.querySelector(".rail__pos").textContent = String(index + 1);
      const state = states[horseId];
      chip.dataset.state = state || "run";
    });
  }
}

/** Thin full-race progress strip: answers "how far to go?" at a glance. */
export class Minimap {
  constructor(root) {
    this.root = root;
    this.dots = new Map();
  }

  build(horses) {
    this.root.innerHTML = '<div class="minimap__line"></div>';
    this.dots = new Map();
    for (const horse of horses) {
      const dot = document.createElement("div");
      dot.className = "minimap__dot";
      dot.style.background = horse.color;
      dot.textContent = horse.emoji;
      this.root.appendChild(dot);
      this.dots.set(horse.id, dot);
    }
  }

  update(live, trackLength) {
    for (const entry of live) {
      const dot = this.dots.get(entry.id);
      if (!dot) continue;
      const fraction = Math.max(0, Math.min(1, entry.pos / trackLength));
      dot.style.left = `${fraction * 100}%`;
      dot.dataset.leader = entry.rank === 1 ? "true" : "false";
    }
  }
}

/** "Cullen used Diamond Hands on Sushi" — the game's #1 social hook. */
export class NotificationLane {
  constructor(root) {
    this.root = root;
    this.queue = [];
    this.visible = 0;
  }

  push(notification) {
    this.queue.push(notification);
    this.drain();
  }

  drain() {
    while (this.visible < MAX_VISIBLE_NOTIFICATIONS && this.queue.length) {
      this.show(this.queue.shift());
    }
  }

  show({ player, powerup, emoji, tier, target, outcome }) {
    const card = document.createElement("div");
    card.className = `notification tier-${tier || "common"}`;
    const blocked = outcome && !["applied", "softened"].includes(outcome);
    const verb = blocked ? "tried" : "used";
    card.innerHTML = `
      <span class="notification__icon">${emoji || "✨"}</span>
      <span class="notification__text">
        <b>${escapeHtml(player)}</b> ${verb} <b class="notification__item">${escapeHtml(powerup)}</b>
        ${target ? `on <b>${escapeHtml(target)}</b>` : ""}
      </span>
      ${blocked ? `<span class="notification__blocked">${outcomeLabel(outcome)}</span>` : ""}
    `;
    this.root.appendChild(card);
    this.visible += 1;
    requestAnimationFrame(() => card.setAttribute("data-in", "true"));
    setTimeout(() => {
      card.removeAttribute("data-in");
      setTimeout(() => {
        card.remove();
        this.visible -= 1;
        this.drain();
      }, 320);
    }, NOTIFICATION_MS);
  }

  clear() {
    this.queue = [];
    this.root.innerHTML = "";
    this.visible = 0;
  }
}

/** Big centred banners for event telegraphs and headlines. */
export class BannerStage {
  constructor(root) {
    this.root = root;
    this.current = null;
  }

  show(text, emoji, { tone = "neutral", duration = BANNER_MS } = {}) {
    if (!text) return;
    if (this.current) this.current.remove();
    const banner = document.createElement("div");
    banner.className = "banner";
    banner.dataset.tone = tone;
    banner.innerHTML = `
      <span class="banner__emoji">${emoji || ""}</span>
      <span class="banner__text">${escapeHtml(text)}</span>
    `;
    this.root.appendChild(banner);
    this.current = banner;
    requestAnimationFrame(() => banner.setAttribute("data-in", "true"));
    setTimeout(() => {
      banner.removeAttribute("data-in");
      setTimeout(() => {
        banner.remove();
        if (this.current === banner) this.current = null;
      }, 300);
    }, duration);
  }
}

/** Scrolling commentary line; higher priority interrupts. */
export class CommentaryTicker {
  constructor(root) {
    this.root = root;
    this.priority = 99;
    this.timer = null;
  }

  say(text, priority = 2) {
    if (priority > this.priority && this.timer) return;
    this.priority = priority;
    this.root.textContent = text;
    this.root.setAttribute("data-in", "true");
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.priority = 99;
      this.timer = null;
    }, 3200);
  }

  clear() {
    this.root.textContent = "";
    this.root.removeAttribute("data-in");
  }
}

/**
 * The powerup briefing shown while the gates are shut.
 *
 * The countdown exists to be read: it runs long enough to take in every item,
 * and each card says at a glance whether the thing helps or hurts and who it
 * lands on. It clears itself once the 3-2-1 takes over.
 */
export class PowerupPrimer {
  constructor(root) {
    this.root = root;
    this.built = false;
  }

  show(powerups) {
    if (!powerups.length) return;
    this.root.innerHTML = `
      <div class="primer__head">
        <span class="primer__title">TONIGHT'S POWERUPS</span>
        <span class="primer__sub">Unlock each one by answering a quick challenge on your phone</span>
      </div>
      <div class="primer__grid">
        ${powerups.map((powerup) => this.card(powerup)).join("")}
      </div>
      <div class="primer__key">
        <span><b>⬆️</b> speeds up</span>
        <span><b>⬇️</b> slows down</span>
        <span><b>🛡️</b> protects</span>
        <span><b>🎲</b> chaos</span>
      </div>
    `;
    this.root.dataset.visible = "true";
    this.built = true;
  }

  card(powerup) {
    return `
      <div class="primer__card" data-polarity="${powerup.polarity}" data-tier="${powerup.tier}">
        <span class="primer__emoji">${powerup.emoji}</span>
        <span class="primer__name">${escapeHtml(powerup.name)}</span>
        <span class="primer__effect">
          <span class="primer__polarity">${powerup.polarityIcon}</span>
          <span class="primer__scope">${escapeHtml(powerup.scopeLabel)}</span>
        </span>
        <span class="primer__blurb">${escapeHtml(powerup.blurb)}</span>
      </div>`;
  }

  /** Fade out as the numbers begin, so the two never fight for the screen. */
  update(raceTime) {
    if (!this.built) return;
    const visible = raceTime < -NUMBERS_TAKE_OVER_AT;
    const next = String(visible);
    if (this.root.dataset.visible !== next) this.root.dataset.visible = next;
    if (!visible && raceTime > 0.5) this.hide();
  }

  hide() {
    this.root.dataset.visible = "false";
    this.root.innerHTML = "";
    this.built = false;
  }
}

/** Seconds of countdown reserved for the big 3-2-1 (server: tuning.countdownNumbersSeconds). */
let NUMBERS_TAKE_OVER_AT = 3.2;

/** Keep the primer/numbers hand-off in step with the server's countdown split. */
export function setCountdownSplit(seconds) {
  if (seconds > 0) NUMBERS_TAKE_OVER_AT = seconds + 0.2;
}

/** 3-2-1-GO with gate slam and spotlight sweep. */
export class Countdown {
  constructor(root) {
    this.root = root;
    this.lastShown = null;
  }

  update(raceTime, onBeat) {
    if (raceTime >= 0.35 || raceTime < -NUMBERS_TAKE_OVER_AT) {
      // Early countdown belongs to the primer; the numbers come in at the end.
      this.hide();
      return;
    }
    const remaining = Math.ceil(-raceTime);
    const label = remaining <= 0 ? "GO!" : String(remaining);
    if (label !== this.lastShown) {
      this.lastShown = label;
      this.root.dataset.visible = "true";
      this.root.innerHTML = `<span class="countdown__number">${label}</span>`;
      const number = this.root.firstElementChild;
      requestAnimationFrame(() => number.setAttribute("data-in", "true"));
      if (onBeat) onBeat(remaining, label);
    }
  }

  hide() {
    if (this.root.dataset.visible === "true") {
      this.root.dataset.visible = "false";
      this.root.innerHTML = "";
      this.lastShown = null;
    }
  }
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character];
  });
}

function outcomeLabel(outcome) {
  return (
    {
      shielded: "BLOCKED",
      ghosted: "PHASED",
      immune: "IMMUNE",
      softened: "SOFTENED",
    }[outcome] || ""
  );
}

/** Race clock, mode chip and heat label in the top bar. */
export class TopBar {
  constructor({ clock, mode, label }) {
    this.clock = clock;
    this.mode = mode;
    this.label = label;
  }

  setRace({ mode, label, duration }) {
    this.mode.textContent = `${mode.emoji} ${mode.name}`;
    this.label.textContent = label || "";
    this.label.hidden = !label;
    this.duration = duration;
  }

  update(raceTime) {
    const shown = Math.max(0, raceTime);
    this.clock.textContent = shown.toFixed(1);
    this.clock.dataset.hot = this.duration && shown > this.duration * 0.75 ? "true" : "false";
  }
}
