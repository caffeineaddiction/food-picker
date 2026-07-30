/**
 * dl-picker phone controller (SPEC.md §4.3, §5.3).
 *
 * One page, six screens, one websocket. The phone is a *controller*: it owns the
 * tap loop and the two powerup slots, and it deliberately shows almost nothing
 * else — the TV is where the race happens (§2.2).
 *
 * Concerns are one small class each: socket wiring lives in `PhoneController`,
 * everything it drives (tap batching, tap zone feel, powerup slots, target
 * sheet, betting panel, notification lane, toasts) is a separate unit below.
 */

import { ConnectionState, GameSocket, store } from "../shared/ws.js";
import { clamp, spring } from "../shared/motion.js";

// --------------------------------------------------------------- tunables ---
// Anything the server owns (tap cap, batch interval, inventory size, min bet)
// arrives in `welcome.catalogs.tuning`. These are phone-only feel constants.

/** Mirrors server/constants.py ROOM_CODE_LENGTH — the hello frame is validated on it. */
const ROOM_CODE_LENGTH = 4;
/** Mirrors server/constants.py MAX_PLAYER_NAME_LENGTH (and the input's maxlength). */
const NAME_MAX_LENGTH = 14;
/** Ship a batch early once a burst gets this big, so hard tapping stays responsive. */
const TAP_FLUSH_AT = 10;
/** Mirrors server/constants.py MAX_TAPS_PER_MESSAGE. */
const TAP_MAX_PER_MESSAGE = 40;
const DEFAULT_BATCH_MS = 100;
/** Mirrors server/constants.py MAX_BACKED_HORSES; `tuning` overrides it. */
const DEFAULT_MAX_BACKED = 4;
/** Mirrors server/constants.py INVENTORY_SLOTS; `tuning` overrides it. */
const DEFAULT_SLOT_COUNT = 2;
/** Mirrors server/constants.py COUNTDOWN_SECONDS; `tuning` overrides it. */
const DEFAULT_COUNTDOWN_SECONDS = 9;
/** Length of the closing 3-2-1; `tuning.countdownNumbersSeconds` overrides it. */
const DEFAULT_COUNTDOWN_NUMBERS_SECONDS = 3;
/** Mirrors server/constants.py CHALLENGE_RETRY_SECONDS; `tuning` overrides it. */
const DEFAULT_RETRY_SECONDS = 2.5;
/** Mirrors server/constants.py TAP_TPS_CAP; `tuning` overrides it. */
const DEFAULT_TAP_CAP = 12;
/** Mirrors server/constants.py TAP_WINDOW_SECONDS — the window the server judges pace over. */
const TAP_WINDOW_SECONDS = 1;
/** Mirrors engine._update_pace_challenges: drifting out of band loses ground 1.5× as fast. */
const PACE_DRAIN_RATE = 1.5;
/** Needle smoothing. Fast enough to feel live, slow enough not to strobe. */
const PACE_SMOOTHING = 16;
/** Snapshot-driven DOM writes are throttled to this; ~10/s is invisible and cheap. */
const HUD_WRITE_MS = 100;
const REACTION_COOLDOWN_MS = 1000;
/** Combo ring: fills at a sustained ~10 taps/s, drains in about a second. */
const COMBO_PER_TAP = 0.09;
const COMBO_DECAY_PER_S = 0.9;
const RIPPLE_POOL_SIZE = 14;
/** Circumference of the combo ring circle in index.html (r=44). */
const RING_LENGTH = 2 * Math.PI * 44;
const SPRING_STIFFNESS = 14;

const EASE_SPRING = "cubic-bezier(0.34, 1.56, 0.64, 1)";
const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)";

/** Mirrors Room._can_pick_horse(): the only phases where a horse can be claimed. */
const SEAT_PHASES = new Set(["lobby", "betting", "results", "bracket"]);

const REDUCED_MOTION = matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Failure reasons from engine.use_powerup / powerups.CastResult, in plain English. */
const ERROR_COPY = {
  no_such_room: "That room isn't running any more.",
  needs_target: "Pick a rival horse first.",
  no_target: "Nothing to aim at right now.",
  cooldown: "That one is still cooling down.",
  too_late: "Too close to the line for that.",
  already_leading: "You're already out in front!",
  not_enough_horses: "Not enough horses left for that.",
  unavailable: "That item can't fire right now.",
  bad_slot: "That slot is empty.",
  not_a_player: "Spectators can't fire powerups.",
  locked: "Still locked — unlock it first.",
  cooling_down: "Wrong answer — wait for the timer.",
  pace_challenge: "That one unlocks on the tap button.",
  nothing_to_unlock: "Nothing to unlock there.",
};

/** What the tap button says while a pace unlock is being judged. */
const PACE_SUB = { slow: "FASTER", hold: "HOLD IT", fast: "EASE OFF" };

/** Slot appearance, driven entirely by the server's inventory shape. */
const SlotState = {
  EMPTY: "empty",
  ARMED: "armed",
  LOCKED: "locked",
  COOLING: "cooling",
  PACE: "pace",
};

/**
 * `scopeLabel` reads well everywhere except the global items, where "EVERY HORSE"
 * undersells what is about to happen to the whole field.
 */
function scopeText(powerup) {
  if (!powerup) return "";
  return powerup.scope === "everyone" ? "ALL HORSES" : (powerup.scopeLabel ?? "");
}

// ------------------------------------------------------------------ helpers ---

const byId = (id) => document.getElementById(id);
const allOf = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** Skip no-op writes: text assignments are the only layout-invalidating ones we do. */
function setText(node, value) {
  const next = String(value);
  if (node.textContent !== next) node.textContent = next;
}

function setAttr(node, name, value) {
  if (node.getAttribute(name) !== value) node.setAttribute(name, value);
}

function ordinal(n) {
  if (!Number.isFinite(n) || n < 1) return "—";
  const tens = n % 100;
  if (tens >= 11 && tens <= 13) return `${n}th`;
  return `${n}${["th", "st", "nd", "rd"][n % 10] || "th"}`;
}

function clockText(seconds) {
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

const canVibrate = typeof navigator.vibrate === "function";

/** Haptics are Android-only in practice; iOS ignores them silently (§5.3). */
function buzz(ms) {
  if (canVibrate) navigator.vibrate(ms);
}

/** One rAF loop for everything that animates from live data. */
class Painter {
  constructor(onFrame) {
    this.onFrame = onFrame;
    this.running = false;
    this.last = 0;
    this._tick = this._tick.bind(this);
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    requestAnimationFrame(this._tick);
  }

  stop() {
    this.running = false;
  }

  _tick(now) {
    if (!this.running) return;
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    this.onFrame(dt, now);
    requestAnimationFrame(this._tick);
  }
}

class RateLimiter {
  constructor(intervalMs) {
    this.intervalMs = intervalMs;
    this.last = -Infinity;
  }

  take() {
    const now = performance.now();
    if (now - this.last < this.intervalMs) return false;
    this.last = now;
    return true;
  }
}

/** Keeps the screen on during a race; released automatically when hidden. */
class WakeLock {
  constructor() {
    this.sentinel = null;
  }

  async acquire() {
    if (!("wakeLock" in navigator) || this.sentinel || document.hidden) return;
    try {
      this.sentinel = await navigator.wakeLock.request("screen");
      this.sentinel.addEventListener("release", () => {
        this.sentinel = null;
      });
    } catch (error) {
      this.sentinel = null; // unsupported or blocked — the game still works
    }
  }
}

// ------------------------------------------------------------------ router ---

/** Shows exactly one `[data-screen]` section; CSS owns the transition. */
class ScreenRouter {
  constructor(root) {
    this.sections = new Map(
      allOf("[data-screen]", root).map((node) => [node.dataset.screen, node])
    );
    this.active = null;
    this.onChange = () => {};
  }

  show(name) {
    if (this.active === name || !this.sections.has(name)) return;
    for (const [key, node] of this.sections) {
      const on = key === name;
      node.dataset.active = String(on);
      setAttr(node, "aria-hidden", on ? "false" : "true");
    }
    const previous = this.active;
    this.active = name;
    // Deliberately not `data-screen`: that name belongs to the sections, and a
    // document-wide `[data-screen]` query would otherwise match <body> first.
    document.body.dataset.activeScreen = name;
    this.onChange(name, previous);
  }
}

// ------------------------------------------------------------- tap batching ---

/**
 * Taps are counted locally and shipped in batches (§7.3): a frame per tap would
 * be ~12 messages/second per phone. Taps are perishable, so while the socket is
 * down we drop them instead of replaying a stale burst on reconnect — and we
 * tell the caller, which keeps the on-screen counter honest.
 */
class TapBatcher {
  constructor({ send, isOnline, batchMs = DEFAULT_BATCH_MS }) {
    this.send = send;
    this.isOnline = isOnline;
    this.batchMs = batchMs;
    this.pending = 0;
    this.timer = null;
    this.armed = false;
  }

  arm(on) {
    this.armed = on;
    if (!on) this.discard();
  }

  /** @returns {boolean} true when the tap will actually reach the server. */
  hit() {
    if (!this.armed || !this.isOnline()) return false;
    this.pending += 1;
    if (this.pending >= TAP_FLUSH_AT) this.flush();
    else if (this.timer === null) this.timer = setTimeout(() => this.flush(), this.batchMs);
    return true;
  }

  flush() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    if (!this.pending) return;
    const count = Math.min(this.pending, TAP_MAX_PER_MESSAGE);
    this.pending = 0;
    this.send(count);
  }

  discard() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.pending = 0;
  }
}

/**
 * Local tap rate, for pace challenges.
 *
 * The server judges pace from the tap stream it receives and only reports back
 * twice a second — far too coarse for a needle you are trying to hold steady. So
 * the rate is measured here, over the same one-second window the server uses,
 * and only the *progress* is reconciled from the server (which owns the verdict).
 */
class TapMeter {
  constructor() {
    this.stamps = [];
    this.rate = 0;
  }

  hit(now) {
    this.stamps.push(now);
  }

  /** @returns {number} smoothed taps/sec over the last `TAP_WINDOW_SECONDS`. */
  sample(now, dt) {
    const cutoff = now - TAP_WINDOW_SECONDS * 1000;
    while (this.stamps.length && this.stamps[0] < cutoff) this.stamps.shift();
    this.rate = spring(this.rate, this.stamps.length / TAP_WINDOW_SECONDS, dt, PACE_SMOOTHING);
    return this.rate;
  }

  reset() {
    this.stamps.length = 0;
    this.rate = 0;
  }
}

// ---------------------------------------------------------------- tap zone ---

/**
 * One tap button, bound to one horse.
 *
 * Every tap must feel physical: a ripple from the touch point, a scale punch, a
 * haptic tick and a combo ring that rewards sustained tapping. Multi-touch
 * counts — `pointerdown` fires once per finger.
 *
 * Each zone builds its own markup so the grid can hold one to four of them.
 */
class TapZone {
  constructor(horse, { onTap, onDead }) {
    this.horse = horse;
    this.onTap = onTap;
    this.onDead = onDead;
    this.combo = 0;
    this.shownCombo = -1;
    this.punchAnim = null;

    const root = element("button", "tapzone");
    root.type = "button";
    root.style.setProperty("--horse-color", horse.color ?? "var(--hero)");
    root.setAttribute("aria-label", `Tap for ${horse.name}`);
    root.innerHTML = `
      <svg class="tapzone__ring" viewBox="0 0 100 100" aria-hidden="true">
        <circle class="ring ring--track" cx="50" cy="50" r="44" />
        <circle class="ring ring--fill" cx="50" cy="50" r="44" />
      </svg>
      <span class="tapzone__stack">
        <span class="tapzone__horse">
          <span class="tapzone__emoji"></span>
          <span class="tapzone__name"></span>
        </span>
        <span class="tapzone__label">TAP!</span>
        <span class="tapzone__sub"></span>
      </span>
      <span class="ripples" aria-hidden="true"></span>
    `;
    this.root = root;
    this.stack = root.querySelector(".tapzone__stack");
    this.label = root.querySelector(".tapzone__label");
    this.sub = root.querySelector(".tapzone__sub");
    this.ring = root.querySelector(".ring--fill");
    setText(root.querySelector(".tapzone__emoji"), horse.emoji ?? "🐎");
    setText(root.querySelector(".tapzone__name"), horse.name ?? "");

    const ripples = root.querySelector(".ripples");
    this.ripples = Array.from({ length: RIPPLE_POOL_SIZE }, () => {
      const node = element("i", "ripple");
      ripples.append(node);
      return node;
    });
    this.rippleIndex = 0;

    // preventDefault needs a non-passive listener: it kills the synthetic click,
    // the 300ms delay, text selection and iOS double-tap zoom in one go.
    root.addEventListener("pointerdown", (event) => this._onPointer(event), { passive: false });
    root.addEventListener("contextmenu", (event) => event.preventDefault());
    root.addEventListener("keydown", (event) => {
      if (event.key !== " " && event.key !== "Enter") return;
      event.preventDefault();
      this._fire(root.offsetWidth / 2, root.offsetHeight / 2);
    });

    this.setState("idle");
  }

  _onPointer(event) {
    event.preventDefault();
    if (this.root.dataset.state === "out") {
      this.onDead?.(this.horse.id);
      return;
    }
    // Children are pointer-events:none, so offsetX/Y is always button-relative.
    this._fire(event.offsetX, event.offsetY);
  }

  _fire(x, y) {
    const counted = this.onTap(this.horse.id);
    this._ripple(x, y, counted);
    this._punch();
    if (!counted) return;
    this.combo = clamp(this.combo + COMBO_PER_TAP, 0, 1);
    buzz(8);
  }

  _ripple(x, y, counted) {
    const node = this.ripples[this.rippleIndex];
    this.rippleIndex = (this.rippleIndex + 1) % this.ripples.length;
    node.style.left = `${x}px`;
    node.style.top = `${y}px`;
    node.dataset.dead = String(!counted);
    node.animate(
      [
        { transform: "scale(0.24)", opacity: 0.85 },
        { transform: "scale(1.5)", opacity: 0 },
      ],
      { duration: 430, easing: EASE_OUT }
    );
  }

  _punch() {
    if (this.punchAnim) this.punchAnim.cancel();
    this.punchAnim = this.stack.animate(
      [{ transform: "scale(1)" }, { transform: "scale(0.9)" }, { transform: "scale(1)" }],
      { duration: 150, easing: EASE_SPRING }
    );
  }

  /** @param {"idle"|"countdown"|"live"|"done"|"out"} state */
  setState(state) {
    if (this.root.dataset.state === state) return;
    this.root.dataset.state = state;
    if (state !== "live") this.setMaxed(false);
  }

  setLabel(text, sub = "") {
    setText(this.label, text);
    setText(this.sub, sub);
    const wantsSub = String(Boolean(sub));
    if (this.root.dataset.sub !== wantsSub) this.root.dataset.sub = wantsSub;
  }

  setMaxed(on) {
    const next = String(Boolean(on));
    if (this.root.dataset.maxed === next) return;
    this.root.dataset.maxed = next;
  }

  /** @param {""|"slow"|"hold"|"fast"} mode ring colour while a pace unlock is live. */
  setPace(mode) {
    if (this.root.dataset.pace === mode) return;
    this.root.dataset.pace = mode;
  }

  /** Called every frame: the ring is local state, it must not wait on the network. */
  decay(dt) {
    if (this.combo > 0) {
      this.combo *= Math.exp(-COMBO_DECAY_PER_S * dt);
      if (this.combo < 0.002) this.combo = 0;
    }
    if (Math.abs(this.combo - this.shownCombo) < 0.004) return;
    this.shownCombo = this.combo;
    this.ring.style.strokeDashoffset = String(RING_LENGTH * (1 - this.combo));
  }

  reset() {
    this.combo = 0;
    this.decay(0);
  }
}

/**
 * One button per backed horse (up to four).
 *
 * Separate buttons rather than one button with a selector: with four horses you
 * want to bounce between them a few taps at a time, and a mode switch in the
 * middle of that is a tax on every single change of mind.
 */
class TapGrid {
  constructor(container, { onTap, onDead }) {
    this.container = container;
    this.onTap = onTap;
    this.onDead = onDead;
    this.zones = new Map();
    this.signature = "";
  }

  /** Rebuild only when the set of horses actually changes. */
  sync(horses) {
    const signature = horses.map((horse) => `${horse.id}:${horse.emoji}`).join("|");
    if (signature === this.signature) return;
    this.signature = signature;
    this.zones = new Map();
    this.container.replaceChildren(
      ...horses.map((horse) => {
        const zone = new TapZone(horse, { onTap: this.onTap, onDead: this.onDead });
        this.zones.set(horse.id, zone);
        return zone.root;
      })
    );
    this.container.dataset.count = String(horses.length);
  }

  get(horseId) {
    return this.zones.get(horseId);
  }

  each(fn) {
    for (const zone of this.zones.values()) fn(zone);
  }

  decay(dt) {
    this.each((zone) => zone.decay(dt));
  }

  setPace(mode) {
    this.each((zone) => zone.setPace(mode));
  }

  /** @param {number} countdown length of the gate countdown, from `tuning`. */
  reset(countdown) {
    this.each((zone) => {
      zone.reset();
      zone.setPace("");
      zone.setState("countdown");
      zone.setLabel(String(countdown), "GATES CLOSED");
    });
  }
}

// ----------------------------------------------------------- powerup slots ---

/**
 * Two big squares (count comes from `tuning.inventorySlots`).
 *
 * A slot is never just "has an item": items land *locked* behind a challenge, so
 * the square has to say what the item is, what it will do to whom, and what it
 * costs to arm it — at a glance, mid-race, on a phone being shaken by a thumb.
 * Hence one badge per question: polarity icon (helps/hurts), scope pill (who it
 * lands on), and a call to action (unlock it / fire it / wait out the penalty).
 *
 * The server's `inventory` is authoritative for all of it; the only local state
 * is the timers it would be silly to round-trip (cooldown, pace progress, the
 * fired item's duration) and a "pending" flag covering the ~50 ms between firing
 * and the confirming frame.
 */
class PowerupSlots {
  constructor(container, { count, retrySeconds, onActivate }) {
    this.container = container;
    this.onActivate = onActivate;
    this.retrySeconds = retrySeconds;
    container.style.setProperty("--slot-count", String(count));
    this.slots = Array.from({ length: count }, (unused, index) => this._build(index));
    this.render(new Array(count).fill(null));
  }

  _build(index) {
    const root = element("button", "slot");
    root.type = "button";
    root.innerHTML = `
      <i class="slot__tier"></i>
      <i class="slot__fill"></i>
      <span class="slot__top">
        <span class="slot__pol"></span>
        <span class="slot__lock"></span>
      </span>
      <span class="slot__emoji"></span>
      <span class="slot__name"></span>
      <span class="slot__foot">
        <span class="scopetag slot__scope"></span>
        <span class="slot__cta"></span>
      </span>
      <i class="slot__bar"></i>
    `;
    const slot = {
      root,
      pol: root.querySelector(".slot__pol"),
      lock: root.querySelector(".slot__lock"),
      emoji: root.querySelector(".slot__emoji"),
      name: root.querySelector(".slot__name"),
      scope: root.querySelector(".slot__scope"),
      cta: root.querySelector(".slot__cta"),
      fill: root.querySelector(".slot__fill"),
      bar: root.querySelector(".slot__bar"),
      powerup: null,
      challenge: null,
      state: SlotState.EMPTY,
      pending: false,
      signature: "",
      activeUntil: 0,
      duration: 0,
      cooldownUntil: 0,
      paceRatio: 0,
      shownFill: 0,
      shownBar: 0,
    };
    root.addEventListener("pointerdown", () => {
      if (slot.pending || slot.state === SlotState.EMPTY) return;
      this.onActivate(index, {
        state: slot.state,
        powerup: slot.powerup,
        challenge: slot.challenge,
        retryLeft: Math.max(0, (slot.cooldownUntil - performance.now()) / 1000),
      });
    });
    this.container.append(root);
    return slot;
  }

  /** @param {Array<object|null>} inventory `InventorySlot.client_meta` per slot. */
  render(inventory, now = performance.now()) {
    this.slots.forEach((slot, index) => this._apply(slot, inventory?.[index] ?? null, now));
  }

  /** A drop landed (§15.6) — animate the square filling so it can't be missed. */
  grant(index, powerup, challenge) {
    const slot = this.slots[index];
    if (!slot || !powerup) return;
    this._apply(
      slot,
      { powerup_id: powerup.id, armed: false, challenge, retryIn: 0, paceHeld: 0 },
      performance.now()
    );
    slot.root.animate(
      [
        { transform: "scale(0.72) rotate(-6deg)", opacity: 0.2 },
        { transform: "scale(1.08) rotate(2deg)", opacity: 1, offset: 0.6 },
        { transform: "scale(1)", opacity: 1 },
      ],
      { duration: 420, easing: EASE_SPRING }
    );
    buzz(14);
  }

  /** The unlock landed: a pop that says "this one is yours now". */
  flourish(index) {
    const slot = this.slots[index];
    if (!slot?.powerup) return;
    slot.root.animate(
      [
        { transform: "scale(1)", filter: "brightness(1)" },
        { transform: "scale(1.1)", filter: "brightness(1.7)", offset: 0.45 },
        { transform: "scale(1)", filter: "brightness(1)" },
      ],
      { duration: 380, easing: EASE_SPRING }
    );
  }

  /** Optimistic state between `use_powerup` and the confirming `inventory` frame. */
  markFired(index) {
    const slot = this.slots[index];
    if (!slot?.powerup) return;
    this._setPending(slot, true);
    // Watchdog: a rejected cast never produces an inventory frame.
    clearTimeout(slot.watchdog);
    slot.watchdog = setTimeout(() => this._setPending(slot, false), 1500);
  }

  /** Show a self/global item's remaining duration on the slot it was fired from. */
  showDuration(index, powerup, now) {
    const slot = this.slots[index];
    if (!slot || !powerup?.duration) return;
    slot.duration = powerup.duration * 1000;
    slot.activeUntil = now + slot.duration;
  }

  /** Locally integrated pace progress, 0..1, painted as the square filling up. */
  setPace(index, ratio) {
    const slot = this.slots[index];
    if (slot) slot.paceRatio = ratio;
  }

  tick(now) {
    for (const slot of this.slots) {
      if (slot.state === SlotState.COOLING) {
        const left = Math.max(0, (slot.cooldownUntil - now) / 1000);
        // The penalty is a race-time deadline, so the phone can run it down itself
        // instead of leaving the square dead for up to half a second past zero.
        if (left <= 0) this._reopen(slot);
        else {
          setText(slot.cta, `${left.toFixed(1)}s`);
          this._fillTo(slot, this.retrySeconds ? left / this.retrySeconds : 0);
        }
      } else {
        this._fillTo(slot, slot.state === SlotState.PACE ? slot.paceRatio : 0);
      }
      const left = slot.activeUntil - now;
      const scale = left > 0 ? clamp(left / slot.duration, 0, 1) : 0;
      if (slot.shownBar === scale) continue;
      slot.shownBar = scale;
      slot.bar.style.transform = `scaleX(${scale})`;
    }
  }

  reset() {
    for (const slot of this.slots) {
      slot.activeUntil = 0;
      slot.cooldownUntil = 0;
      slot.paceRatio = 0;
      this._apply(slot, null, performance.now());
    }
  }

  lookup(id) {
    return id ? this.catalog?.get(id) ?? null : null;
  }

  setCatalog(catalog) {
    this.catalog = catalog;
  }

  _apply(slot, held, now) {
    const powerup = this.lookup(held?.powerup_id);
    const challenge = powerup ? (held.challenge ?? null) : null;
    const retryIn = powerup ? (held.retryIn ?? 0) : 0;
    const isPace = challenge?.kind === "pace";
    // Same precedence the server uses in `answer_challenge`: a live penalty wins,
    // and a wrong-answer re-roll never hands back a pace task, so the two states
    // are mutually exclusive in practice.
    const state = !powerup
      ? SlotState.EMPTY
      : held.armed
        ? SlotState.ARMED
        : retryIn > 0
          ? SlotState.COOLING
          : isPace
            ? SlotState.PACE
            : SlotState.LOCKED;

    if (state === SlotState.COOLING) {
      const counting = Math.max(0, (slot.cooldownUntil - now) / 1000);
      // Re-anchor only on a real change, so a stream of `you` frames doesn't keep
      // nudging a timer the player is watching tick down.
      if (Math.abs(counting - retryIn) > 0.15) slot.cooldownUntil = now + retryIn * 1000;
    } else {
      slot.cooldownUntil = 0;
    }
    if (!isPace) slot.paceRatio = 0;

    slot.powerup = powerup;
    slot.challenge = challenge;
    slot.state = state;
    this._setPending(slot, false);

    const signature = `${powerup?.id ?? ""}|${state}|${challenge?.prompt ?? ""}`;
    if (slot.signature === signature) return;
    slot.signature = signature;
    slot.root.dataset.state = state;

    if (!powerup) {
      slot.root.className = "slot";
      setText(slot.pol, "");
      setText(slot.lock, "");
      setText(slot.emoji, "◇");
      setText(slot.name, "empty");
      setText(slot.scope, "");
      setText(slot.cta, "WAITING FOR A DROP");
      setAttr(slot.scope, "data-scope", "none");
      setAttr(slot.root, "aria-label", "Empty powerup slot");
      return;
    }

    slot.root.className = `slot tier-${powerup.tier} pol-${powerup.polarity}`;
    setText(slot.pol, powerup.polarityIcon ?? "");
    setText(slot.lock, state === SlotState.ARMED ? "" : isPace ? "⏱️" : "🔒");
    setText(slot.emoji, powerup.emoji);
    setText(slot.name, powerup.name);
    setText(slot.scope, scopeText(powerup));
    setAttr(slot.scope, "data-scope", powerup.scope ?? "none");
    setText(slot.cta, this._callToAction(state, challenge));
    setAttr(
      slot.root,
      "aria-label",
      state === SlotState.ARMED
        ? `${powerup.name}, ready. ${powerup.blurb} Hits ${scopeText(powerup)}.`
        : `${powerup.name}, locked. ${this._callToAction(state, challenge)}`
    );
  }

  _callToAction(state, challenge) {
    if (state === SlotState.ARMED) return "FIRE →";
    if (state === SlotState.PACE) return `HOLD ${Math.round(challenge?.targetRate ?? 0)}/SEC`;
    if (state === SlotState.COOLING) return "…";
    return "TAP TO UNLOCK";
  }

  /** A spent cooldown becomes an ordinary locked slot again. */
  _reopen(slot) {
    slot.state = SlotState.LOCKED;
    slot.cooldownUntil = 0;
    slot.signature = "";
    slot.root.dataset.state = SlotState.LOCKED;
    setText(slot.cta, this._callToAction(SlotState.LOCKED, slot.challenge));
    this._fillTo(slot, 0);
  }

  _fillTo(slot, ratio) {
    const next = clamp(ratio, 0, 1);
    if (Math.abs(next - slot.shownFill) < 0.004) return;
    slot.shownFill = next;
    slot.fill.style.transform = `scaleY(${next.toFixed(3)})`;
  }

  _setPending(slot, on) {
    if (!on) clearTimeout(slot.watchdog);
    slot.pending = on;
    slot.root.dataset.pending = String(on);
  }
}

// ------------------------------------------------------------ target sheet ---

/** One dimmer behind whichever sheet is up; tapping it dismisses that sheet. */
class Scrim {
  constructor(node) {
    this.node = node;
    this.onDismiss = null;
    node.addEventListener("pointerdown", () => this.onDismiss?.());
  }

  show(onDismiss) {
    this.onDismiss = onDismiss;
    this.node.dataset.visible = "true";
  }

  hide() {
    this.onDismiss = null;
    this.node.dataset.visible = "false";
  }
}

/** Bottom sheet of rival horses. Only TARGET-class items need it (§9.0). */
class TargetSheet {
  constructor(scrim) {
    this.root = byId("target-sheet");
    this.scrim = scrim;
    this.list = byId("sheet-list");
    this.title = byId("sheet-title");
    this.blurb = byId("sheet-blurb");
    this.emoji = byId("sheet-emoji");
    this.polarity = byId("sheet-polarity");
    this.scope = byId("sheet-scope");
    this.resolve = null;
    byId("sheet-cancel").addEventListener("click", () => this._close(null));
  }

  get open() {
    return this.resolve !== null;
  }

  /**
   * @param {object} powerup catalog entry being fired
   * @param {Array<object>} rivals horses, own horse already excluded
   * @returns {Promise<number|null>} chosen horse id, or null if cancelled
   */
  choose(powerup, rivals) {
    this._close(null);
    setText(this.title, powerup.name.toUpperCase());
    setText(this.emoji, powerup.emoji);
    setText(this.blurb, powerup.blurb);
    // The re-pick flow borrows this sheet without a catalog entry behind it.
    this.root.className = powerup.polarity ? `sheet pol-${powerup.polarity}` : "sheet";
    setText(this.polarity, powerup.polarityIcon ?? "");
    setText(this.scope, scopeText(powerup));
    setAttr(this.scope, "data-scope", powerup.scope ?? "none");
    this.list.replaceChildren(
      ...rivals.map((horse) => {
        const button = element("button", "targetbtn");
        button.type = "button";
        button.style.setProperty("--horse-color", horse.color);
        button.append(
          element("span", "targetbtn__emoji", horse.emoji),
          element("span", "targetbtn__name", horse.name),
          element("span", "targetbtn__rank", horse.rank ? ordinal(horse.rank) : "")
        );
        button.addEventListener("click", () => this._close(horse.id));
        return button;
      })
    );
    this._visible(true);
    return new Promise((resolve) => {
      this.resolve = resolve;
    });
  }

  _close(value) {
    const resolve = this.resolve;
    this.resolve = null;
    this._visible(false);
    if (resolve) resolve(value);
  }

  _visible(on) {
    this.root.dataset.visible = String(on);
    if (on) this.scrim.show(() => this._close(null));
    else this.scrim.hide();
  }
}

// -------------------------------------------------------- challenge sheet ---

/**
 * The unlock gate (§9.0, `server/challenges.py`).
 *
 * A locked item costs a couple of seconds of not tapping, so the sheet is built
 * to be *answered and dismissed*: prompt huge and central, three or four targets
 * the size of a thumb, no prose to read. The server owns the answer, so a pick
 * only ever puts the sheet into a waiting state; what closes it is the server
 * agreeing — an `unlocked` frame or an armed slot in `inventory`, whichever the
 * network delivers first.
 */
class ChallengeSheet {
  constructor(scrim, { onAnswer }) {
    this.root = byId("challenge-sheet");
    this.scrim = scrim;
    this.onAnswer = onAnswer;
    this.item = byId("challenge-item");
    this.emoji = byId("challenge-emoji");
    this.polarity = byId("challenge-polarity");
    this.scope = byId("challenge-scope");
    this.kicker = byId("challenge-kicker");
    this.prompt = byId("challenge-prompt");
    this.choices = byId("challenge-choices");
    this.hint = byId("challenge-hint");
    this.slot = null;
    this.buttons = [];
    this.timeout = 0;
    this.settled = false;
    byId("challenge-cancel").addEventListener("click", () => this.close());
  }

  get open() {
    return this.slot !== null;
  }

  openFor(slot, powerup, challenge) {
    this.slot = slot;
    this.settled = false;
    // One timer handle for every deferred close, so reopening can't inherit a
    // dismissal that was queued for the previous item.
    clearTimeout(this.timeout);
    this.root.className = `sheet sheet--challenge pol-${powerup.polarity}`;
    this.root.dataset.busy = "false";
    delete this.root.dataset.wrong;
    setText(this.item, powerup.name.toUpperCase());
    setText(this.emoji, powerup.emoji);
    setText(this.polarity, powerup.polarityIcon ?? "");
    setText(this.scope, scopeText(powerup));
    setAttr(this.scope, "data-scope", powerup.scope ?? "none");
    setText(this.kicker, "ANSWER TO ARM IT");
    setText(this.prompt, challenge.prompt);
    setText(this.hint, challenge.hint);
    this.buttons = (challenge.choices ?? []).map((choice, index) => {
      const button = element("button", "choicebtn", choice);
      button.type = "button";
      button.addEventListener("pointerdown", () => this._pick(index, button));
      return button;
    });
    this.choices.dataset.count = String(this.buttons.length);
    this.choices.replaceChildren(...this.buttons);
    this._visible(true);
  }

  /**
   * Reconcile against the server. Called on every inventory update: the slot is
   * either armed now (unlocked — get out of the way) or gone (the item was spent
   * or the race ended).
   */
  settle(inventory) {
    if (this.slot === null) return;
    const held = inventory?.[this.slot] ?? null;
    if (!held) this.close();
    else if (held.armed) this.confirm(this.slot);
  }

  /** The slot is armed. Either frame can say so first; only the first one lands. */
  confirm(slot) {
    if (this.slot !== slot || this.settled) return;
    this.settled = true;
    this.root.dataset.busy = "false";
    setText(this.kicker, "ARMED ✓");
    buzz(20);
    clearTimeout(this.timeout);
    this.timeout = setTimeout(() => this.close(), 260);
  }

  /** A wrong answer: the item stays locked behind a fresh question and a penalty. */
  reject(retrySeconds) {
    if (this.slot === null) return;
    this.root.dataset.wrong = "true";
    setText(this.kicker, `WRONG — ${retrySeconds.toFixed(1)}s PENALTY`);
    buzz(60);
    clearTimeout(this.timeout);
    this.timeout = setTimeout(() => this.close(), 900);
  }

  close() {
    if (this.slot === null) return;
    this.slot = null;
    clearTimeout(this.timeout);
    this._visible(false);
  }

  _pick(index, button) {
    if (this.root.dataset.busy === "true" || this.slot === null || this.settled) return;
    this.root.dataset.busy = "true";
    button.dataset.picked = "true";
    setText(this.kicker, "CHECKING…");
    buzz(10);
    this.onAnswer(this.slot, index);
    // The server answers a correct guess with an inventory frame, which arrives
    // on the HUD tick at the latest. If nothing lands at all, hand the sheet back
    // rather than trapping the player in a dead dialog.
    clearTimeout(this.timeout);
    this.timeout = setTimeout(() => {
      this.root.dataset.busy = "false";
      delete button.dataset.picked;
      setText(this.kicker, "ANSWER TO ARM IT");
    }, 1600);
  }

  _visible(on) {
    this.root.dataset.visible = String(on);
    if (on) this.scrim.show(() => this.close());
    else this.scrim.hide();
  }
}

// ------------------------------------------------------------- pace meter ---

/**
 * The other kind of lock: hold ~N taps/sec for a couple of seconds.
 *
 * Mashing overshoots the band, so the tax is thumb control rather than thumb
 * absence — which only works if the player can *see* the band, where they are in
 * it, and how much credit they have banked. Hence three stacked readouts: the
 * verdict in words, the needle against the band, and the hold bar.
 */
class PaceMeter {
  constructor() {
    this.root = byId("pace-meter");
    this.emoji = byId("pace-emoji");
    this.title = byId("pace-title");
    this.verdict = byId("pace-verdict");
    this.band = byId("pace-band");
    this.needle = byId("pace-needle");
    this.fill = byId("pace-fill");
    this.signature = "";
    this.shownFill = -1;
  }

  hide() {
    if (this.root.dataset.visible === "false") return;
    this.root.dataset.visible = "false";
    this.signature = "";
  }

  /**
   * @param {object} powerup the item being unlocked
   * @param {object} challenge its pace challenge
   * @param {number} rate live local taps/sec
   * @param {number} held seconds banked inside the band
   * @param {"slow"|"hold"|"fast"} verdict
   * @param {number} ceiling top of the scale in taps/sec
   */
  update({ powerup, challenge, rate, held, verdict, ceiling }) {
    const signature = `${powerup.id}|${challenge.targetRate}|${ceiling}`;
    if (signature !== this.signature) {
      this.signature = signature;
      setText(this.emoji, powerup.emoji);
      setText(this.title, `HOLD ${Math.round(challenge.targetRate)} TAPS/SEC`);
      const low = clamp((challenge.targetRate - challenge.tolerance) / ceiling, 0, 1);
      const high = clamp((challenge.targetRate + challenge.tolerance) / ceiling, 0, 1);
      this.band.style.left = `${(low * 100).toFixed(1)}%`;
      this.band.style.width = `${((high - low) * 100).toFixed(1)}%`;
    }
    this.root.dataset.visible = "true";
    setAttr(this.root, "data-verdict", verdict);
    setText(
      this.verdict,
      verdict === "hold" ? "HOLD IT ✓" : verdict === "slow" ? "FASTER ▶▶" : "◀◀ EASE OFF"
    );
    this.needle.style.left = `${(clamp(rate / ceiling, 0, 1) * 100).toFixed(1)}%`;
    const progress = clamp(held / challenge.holdSeconds, 0, 1);
    if (Math.abs(progress - this.shownFill) > 0.004) {
      this.shownFill = progress;
      this.fill.style.transform = `scaleX(${progress.toFixed(3)})`;
    }
  }

  /** Tapping a pace slot can't open anything, so point at the thing that matters. */
  flash() {
    if (this.root.dataset.visible !== "true") return;
    this.root.animate(
      [{ transform: "scale(1)" }, { transform: "scale(1.04)" }, { transform: "scale(1)" }],
      { duration: 320, easing: EASE_SPRING }
    );
  }
}

// ---------------------------------------------------------------- primer ---

/**
 * Countdown crib sheet: every item, what it does, who it lands on.
 *
 * The gates take long enough now that the wait is the best teaching moment the
 * game gets — and this is the cheap version, glanceable in the corner of an eye
 * while the TV shows the real thing.
 */
class Primer {
  constructor() {
    this.root = byId("primer");
    this.grid = byId("primer-grid");
    this.count = byId("primer-count");
  }

  build(catalog) {
    setText(this.count, `THE ${catalog.length} ITEMS`);
    this.grid.replaceChildren(
      ...catalog.map((powerup) => {
        const cell = element("div", `primeritem pol-${powerup.polarity}`);
        const top = element("span", "primeritem__top");
        top.append(
          element("span", "primeritem__emoji", powerup.emoji),
          element("i", "primeritem__pol", powerup.polarityIcon ?? "")
        );
        cell.append(top, element("span", "primeritem__name", powerup.name));
        setAttr(cell, "data-scope", powerup.scope ?? "none");
        setAttr(cell, "title", `${powerup.blurb} (${scopeText(powerup)})`);
        return cell;
      })
    );
  }

  show(on) {
    const next = String(Boolean(on) && this.grid.childElementCount > 0);
    if (this.root.dataset.visible === next) return;
    this.root.dataset.visible = next;
  }
}

// ----------------------------------------------------- notifications & toasts ---

/** Single-line marquee for other people's casts. Never overlaps the tap zone. */
class NotifyLane {
  constructor(node) {
    this.node = node;
    this.queue = [];
    this.busy = false;
  }

  push({ player, powerup, emoji, tier, target, outcome }) {
    const nodes = [element("span", null, emoji || "✨"), element("b", null, player || "someone")];
    nodes.push(document.createTextNode(" used "));
    nodes.push(element("b", null, powerup || "something"));
    if (target) {
      nodes.push(document.createTextNode(" on "));
      nodes.push(element("b", null, target));
    }
    if (outcome && outcome !== "applied") {
      nodes.push(document.createTextNode(` — ${outcome}!`));
    }
    // The TV is the real venue for these; a backlog on the phone is noise.
    if (this.queue.length > 3) this.queue.shift();
    this.queue.push({ nodes, tier: tier || "common" });
    if (!this.busy) this._next();
  }

  _next() {
    const item = this.queue.shift();
    if (!item) {
      this.busy = false;
      return;
    }
    this.busy = true;
    this.node.replaceChildren(...item.nodes);
    this.node.dataset.tier = item.tier;
    const animation = this.node.animate(
      [
        { transform: `translateX(${REDUCED_MOTION ? 0 : 45}%)`, opacity: 0 },
        { transform: "translateX(0)", opacity: 1, offset: 0.22 },
        { transform: "translateX(0)", opacity: 1, offset: 0.76 },
        { transform: `translateX(${REDUCED_MOTION ? 0 : -45}%)`, opacity: 0 },
      ],
      { duration: 2400, easing: "linear" }
    );
    animation.onfinish = () => this._next();
  }
}

class Toasts {
  constructor(root) {
    this.root = root;
  }

  push(text, { emoji = "", kind = "info", ms = 3000 } = {}) {
    while (this.root.childElementCount >= 3) this.root.firstElementChild.remove();
    const node = element("div", "toast");
    node.dataset.kind = kind;
    if (emoji) node.append(element("span", "toast__emoji", emoji));
    node.append(element("span", "toast__text", text));
    this.root.append(node);
    node.animate(
      [
        { transform: "translateY(-14px) scale(0.94)", opacity: 0 },
        { transform: "none", opacity: 1 },
      ],
      { duration: 240, easing: EASE_SPRING }
    );
    setTimeout(() => {
      const out = node.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200 });
      out.onfinish = () => node.remove();
    }, ms);
  }
}

// ----------------------------------------------------------- betting panel ---

/** The Punters' Club window (§11.5): bankroll, stake stepper, live odds. */
class BettingPanel {
  constructor({ onBet }) {
    this.onBet = onBet;
    this.list = byId("bet-list");
    this.bankrollNode = byId("bet-bankroll");
    this.clockNode = byId("bet-clock");
    this.noteNode = byId("bet-note");
    this.stakeNode = byId("stake-value");
    this.up = byId("stake-up");
    this.down = byId("stake-down");
    this.minBet = 50;
    this.stake = 50;
    this.available = 0;
    this.closesAt = 0;
    this.open = false;
    this.myBet = null;
    this.rows = new Map();
    this.signature = "";
    this.up.addEventListener("click", () => this._nudge(1));
    this.down.addEventListener("click", () => this._nudge(-1));
  }

  configure(minBet) {
    this.minBet = minBet;
    this.stake = minBet;
  }

  /** Clear the local bet memory when a fresh window opens. */
  reset() {
    this.myBet = null;
    this.stake = this.minBet;
  }

  update(state, me) {
    const betting = state.betting;
    if (!betting) return;
    this.open = betting.open;
    this.closesAt = performance.now() + betting.closesIn * 1000;
    const bankroll = me?.bankroll ?? 0;
    // The server refunds a replaced bet, so the current stake is spendable again.
    this.available = bankroll + (this.myBet?.amount ?? 0);
    setText(this.bankrollNode, bankroll);

    const signature = state.horses.map((horse) => `${horse.id}:${horse.emoji}:${horse.name}`).join("|");
    if (signature !== this.signature) {
      this.signature = signature;
      this._buildRows(state.horses);
    }
    for (const horse of state.horses) {
      const row = this.rows.get(horse.id);
      if (!row) continue;
      const pool = betting.pool?.[String(horse.id)] ?? 0;
      const odds = betting.odds?.[String(horse.id)] ?? 0;
      setText(row.odds, `×${odds.toFixed(2)}`);
      setText(row.pool, pool ? `${pool} 🥇 backing` : "no money on it");
      row.root.dataset.mine = String(this.myBet?.horseId === horse.id);
      row.root.disabled = !this.open;
    }
    this._syncStake();
  }

  tick(now) {
    const left = Math.max(0, (this.closesAt - now) / 1000);
    setText(this.clockNode, this.open ? `${Math.ceil(left)}s` : "closed");
    this.clockNode.dataset.urgent = String(this.open && left <= 5);
  }

  _buildRows(horses) {
    this.rows.clear();
    this.list.replaceChildren(
      ...horses.map((horse) => {
        const root = element("button", "betrow");
        root.type = "button";
        const id = element("div", "betrow__id");
        const name = element("div", "betrow__name", horse.name);
        const pool = element("div", "betrow__pool");
        id.append(name, pool);
        const odds = element("span", "betrow__odds");
        root.append(element("span", "betrow__emoji", horse.emoji), id, odds);
        root.addEventListener("click", () => this._place(horse));
        this.rows.set(horse.id, { root, odds, pool });
        return root;
      })
    );
  }

  _place(horse) {
    if (!this.open || this.stake < this.minBet || this.stake > this.available) return;
    this.myBet = { horseId: horse.id, amount: this.stake };
    for (const [id, row] of this.rows) row.root.dataset.mine = String(id === horse.id);
    setText(this.noteNode, `You're on ${horse.emoji} ${horse.name} for ${this.stake} 🥇`);
    buzz(12);
    this.onBet(horse.id, this.stake);
  }

  _nudge(direction) {
    const ceiling = Math.max(this.minBet, Math.floor(this.available / this.minBet) * this.minBet);
    this.stake = clamp(this.stake + direction * this.minBet, this.minBet, ceiling);
    this._syncStake();
  }

  _syncStake() {
    const ceiling = Math.max(this.minBet, Math.floor(this.available / this.minBet) * this.minBet);
    this.stake = clamp(this.stake, this.minBet, ceiling);
    setText(this.stakeNode, this.stake);
    this.down.disabled = this.stake <= this.minBet;
    this.up.disabled = this.stake >= ceiling;
    if (!this.myBet) {
      setText(
        this.noteNode,
        this.open ? `Min bet ${this.minBet} 🥇. Tap a horse to back it.` : "Bets are closed — good luck."
      );
    }
  }
}

// ------------------------------------------------------- live standings list ---

/** Spectator view: the field in race order, rows sliding as positions swap. */
class Standings {
  constructor(container) {
    this.container = container;
    this.rows = new Map();
    this.signature = "";
  }

  build(horses) {
    const signature = horses.map((horse) => `${horse.id}:${horse.emoji}`).join("|");
    if (signature === this.signature) return;
    this.signature = signature;
    this.rows.clear();
    this.container.replaceChildren(
      ...horses.map((horse) => {
        const root = element("div", "standing");
        root.style.setProperty("--horse-color", horse.color);
        const bar = element("div", "standing__bar");
        const fill = element("i");
        bar.append(fill);
        const rank = element("span", "standing__rank", "—");
        root.append(
          rank,
          element("span", "standing__emoji", horse.emoji),
          element("span", "standing__name", horse.name),
          bar
        );
        this.rows.set(horse.id, { root, rank, fill, shownIndex: -1, shownProgress: -1 });
        return root;
      })
    );
    this.layout();
  }

  layout() {
    const count = Math.max(1, this.rows.size);
    const height = this.container.clientHeight;
    const row = Math.max(34, Math.min(56, height / count - 4));
    this.container.style.setProperty("--row", `${row}px`);
    this.rowHeight = row + 4;
    for (const entry of this.rows.values()) entry.shownIndex = -1;
  }

  /** @param {number[]} order horse ids, first to last */
  update(order, progressById) {
    order.forEach((horseId, index) => {
      const entry = this.rows.get(horseId);
      if (!entry) return;
      if (entry.shownIndex !== index) {
        entry.shownIndex = index;
        entry.root.style.transform = `translateY(${index * (this.rowHeight || 44)}px)`;
        setText(entry.rank, ordinal(index + 1));
        entry.root.dataset.lead = String(index === 0);
      }
      const progress = clamp(progressById.get(horseId) ?? 0, 0, 1);
      if (Math.abs(progress - entry.shownProgress) > 0.005) {
        entry.shownProgress = progress;
        entry.fill.style.transform = `scaleX(${progress})`;
      }
    });
  }
}

// ------------------------------------------------------------- controller ---

class PhoneController {
  constructor() {
    this.router = new ScreenRouter(byId("screens"));
    this.toasts = new Toasts(byId("toasts"));
    this.lane = new NotifyLane(byId("notify-text"));
    this.scrim = new Scrim(byId("sheet-scrim"));
    this.sheet = new TargetSheet(this.scrim);
    this.challengeSheet = new ChallengeSheet(this.scrim, {
      onAnswer: (slot, choice) => this.send({ t: "answer", slot, choice }),
    });
    this.paceMeter = new PaceMeter();
    this.primer = new Primer();
    this.standings = new Standings(byId("standings"));
    this.wakeLock = new WakeLock();
    this.reactionLimiter = new RateLimiter(REACTION_COOLDOWN_MS);

    this.room = readRoomCode();
    this.token = store.get("token");
    this.storedName = store.get("name", "");

    // Server truth, kept whole rather than copied field by field.
    this.state = null;
    this.you = null;
    this.snapshot = null;
    this.result = null;
    this.phase = "lobby";
    this.catalogs = null;
    this.powerupCatalog = new Map();
    this.breedCatalog = new Map();
    this.trackLength = 1000;
    this.duration = 0;
    this.countdownSeconds = DEFAULT_COUNTDOWN_SECONDS;
    this.countdownNumbers = DEFAULT_COUNTDOWN_NUMBERS_SECONDS;
    this.retrySeconds = DEFAULT_RETRY_SECONDS;
    this.tapCap = DEFAULT_TAP_CAP;

    // Local view state.
    this.desiredSeat = null;
    this.pendingPicks = null;
    this.maxBacked = DEFAULT_MAX_BACKED;
    this.forceJoin = false;
    this.dead = false;
    this.everOnline = false;
    this.localTaps = 0;
    this.inventory = [];
    // Pace unlocks are judged server-side but reported twice a second, so the
    // held-seconds are integrated locally between frames and snapped on arrival.
    this.tapMeter = new TapMeter();
    this.paceHeld = new Map();
    this.paceFocus = null;
    this.paceVerdict = null;
    this.shownRank = 0;
    this.shownProgress = 0;
    this.shownChips = null;
    this.hudDirty = true;
    this.lastHudWrite = 0;
    this.horseSignature = "";
    this.pendingCast = null;

    this._cacheDom();
    this._wireChrome();

    // No room code means no networking at all: GameSocket reconnects on its own
    // (visibility, `online`), so it must not exist until we have somewhere to go.
    // The room may only be discovered asynchronously, hence `_ensureSocket`.
    this.socket = null;

    // One batcher per horse: taps are a rate per horse, so they must not be
    // merged into a single stream on the way out.
    this.tapBatchers = new Map();
    this.tapGrid = new TapGrid(byId("tap-grid"), {
      onTap: (horseId) => this._onTap(horseId),
      onDead: (horseId) => this.replaceEliminatedHorse(horseId),
    });
    this._buildSlots(DEFAULT_SLOT_COUNT);
    this.betting = new BettingPanel({ onBet: (horseId, amount) => this.placeBet(horseId, amount) });

    this.painter = new Painter((dt, now) => this.onFrame(dt, now));
    this.router.onChange = (name) => this.onScreenChange(name);
  }

  // ------------------------------------------------------------ bootstrap ---

  async start() {
    this.nameInput.value = this.storedName;
    for (const tag of this.roomTags) setText(tag, this.room ?? "····");
    if (!this.room) {
      // People type the tunnel URL instead of scanning. An office runs one room
      // at a time, so adopt the active one rather than dead-ending on a code
      // they can see on the TV but shouldn't have to retype.
      this.room = await this.findActiveRoom();
      if (!this.room) {
        this.showDeadEnd({
          emoji: "🐴",
          title: "Which race?",
          text: "No race running yet — type the four letters shown on the TV.",
          code: true,
        });
        return;
      }
      for (const tag of this.roomTags) setText(tag, this.room);
    }
    this.router.show("join");
    this._ensureSocket().connect();
  }

  /** Build the socket once a room is known; safe to call repeatedly. */
  _ensureSocket() {
    if (!this.socket && this.room) {
      this.socket = new GameSocket({
        hello: () => ({ t: "hello", room: this.room, role: "player", token: this.token }),
        onMessage: (message) => this.onMessage(message),
        onState: (state) => this.onConnectionState(state),
      });
    }
    return this.socket;
  }

  /** @returns {Promise<string|null>} code of the room to join, if any. */
  async findActiveRoom() {
    try {
      const response = await fetch("/api/rooms/active");
      if (!response.ok) return null;
      return normaliseCode((await response.json()).code);
    } catch (error) {
      return null;
    }
  }

  /** Lazily create (and cache) the tap batcher for one horse. */
  batcherFor(horseId) {
    let batcher = this.tapBatchers.get(horseId);
    if (!batcher) {
      batcher = new TapBatcher({
        send: (n) => this.send({ t: "tap", n, horse_id: horseId }),
        isOnline: () => this.socket?.readyState === WebSocket.OPEN,
        batchMs: this.batchMs,
      });
      this.tapBatchers.set(horseId, batcher);
    }
    return batcher;
  }

  armTaps(on, horseIds = null) {
    for (const [horseId, batcher] of this.tapBatchers) {
      batcher.arm(on && (horseIds === null || horseIds.includes(horseId)));
    }
  }

  discardTaps() {
    for (const batcher of this.tapBatchers.values()) batcher.discard();
  }

  _cacheDom() {
    this.nameInput = byId("name-input");
    this.nameField = this.nameInput.closest(".namefield");
    this.horseRow = byId("horse-row");
    this.joinScreen = this.router.sections.get("join");
    this.raceScreen = this.router.sections.get("race");
    this.roomTags = allOf("[data-room]");
    this.connDots = allOf("[data-conn-dot]");
    this.readyToggles = allOf("[data-ready-toggle]");
    this.raceCard = byId("race-card");
    this.raceEmoji = byId("race-emoji");
    this.raceName = byId("race-name");
    this.racePos = byId("race-pos");
    this.raceClock = byId("race-clock");
    this.raceProgress = byId("race-progress");
    this.statTaps = byId("stat-taps");
    this.statTps = byId("stat-tps");
    this.statChips = byId("stat-chips");
  }

  _wireChrome() {
    // iOS: pinch-zoom is not covered by touch-action, and long-press selection
    // in a game controller is never intentional.
    document.addEventListener("gesturestart", (event) => event.preventDefault());
    document.addEventListener("selectstart", (event) => {
      // Long-press selection is never intentional on a controller — except in
      // the two text fields (name, room code).
      if (!(event.target instanceof HTMLInputElement)) event.preventDefault();
    });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) this.wakeLock.acquire();
    });
    window.addEventListener("resize", () => {
      this.standings.layout();
      this.hudDirty = true;
    });

    this.nameInput.addEventListener("change", () => store.set("name", this.readName()));
    byId("spectate-btn").addEventListener("click", () => this.join([]));
    byId("confirm-picks").addEventListener("click", () => {
      const picks = this.pendingPicks ?? this.me()?.horse_ids ?? [];
      if (picks.length) this.join(picks);
    });
    byId("lobby-swap").addEventListener("click", () => {
      this.forceJoin = true;
      this.route();
    });
    for (const toggle of this.readyToggles) {
      toggle.addEventListener("click", () => {
        const next = toggle.getAttribute("aria-pressed") !== "true";
        for (const other of this.readyToggles) setAttr(other, "aria-pressed", String(next));
        this.send({ t: "ready", ready: next });
      });
    }
    byId("code-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const code = normaliseCode(byId("code-input").value);
      if (code) location.search = `?room=${code}`;
    });
  }

  // -------------------------------------------------------------- socket ---

  /** All client intent goes through here; a code-less page has no socket at all. */
  send(frame) {
    return this.socket ? this.socket.send(frame) : false;
  }

  onConnectionState(state) {
    document.body.dataset.conn = state;
    // ConnectionState values are exactly the states .conn-dot styles (theme.css).
    for (const dot of this.connDots) setAttr(dot, "data-state", state);
    if (state === ConnectionState.ONLINE) this.everOnline = true;
    else this.discardTaps(); // taps are perishable: never replay them on reconnect
    setText(
      byId("offlinebar-text"),
      state === ConnectionState.CONNECTING
        ? this.everOnline
          ? "Reconnecting…"
          : "Connecting…"
        : "Offline — taps aren't counting"
    );
  }

  onMessage(message) {
    switch (message.t) {
      case "welcome":
        this.onWelcome(message);
        break;
      case "room_state":
        this.applyRoomState(message);
        break;
      case "phase":
        this.onPhase(message);
        break;
      case "snapshot":
        this.snapshot = message;
        this.hudDirty = true;
        break;
      case "you":
        this.onYou(message);
        break;
      case "grant":
        this.slots.grant(message.slot, message.powerup, message.challenge ?? null);
        if (message.powerup) {
          this.toasts.push(`${message.powerup.name} — locked`, {
            emoji: "🔒",
            kind: "good",
            ms: 1800,
          });
        }
        break;
      case "inventory":
        this.applyInventory(message.inventory);
        break;
      case "unlocked":
        this.onUnlocked(message);
        break;
      case "notify":
        this.lane.push(message);
        break;
      case "intel":
        this.toasts.push(message.text, { emoji: message.emoji || "🕵️", kind: "intel", ms: 4500 });
        buzz(16);
        break;
      case "result":
        this.onResult(message);
        break;
      case "error":
        this.onError(message);
        break;
      case "kicked":
        this.socket?.close();
        this.showDeadEnd({
          emoji: "👋",
          title: "You're out of this one",
          text: "The host removed you from the room. No hard feelings — dinner is dinner.",
        });
        break;
      // event / commentary / reaction are for the TV; the phone stays quiet.
      default:
        break;
    }
  }

  onWelcome(message) {
    if (message.token) {
      this.token = message.token;
      store.set("token", message.token);
    }
    if (message.catalogs) this.applyCatalogs(message.catalogs);
    if (message.live) this.applyLive(message.live);
    if (message.state) this.applyRoomState(message.state);
    // The room forgot us (server restart, room recycled) but we know what we
    // asked for — retake the seat instead of dumping the player back to Join.
    if (!message.token && this.desiredSeat) this.join(this.desiredSeat.horseIds);
  }

  applyCatalogs(catalogs) {
    if (this.catalogs) return; // arrives on every reconnect; it never changes
    this.catalogs = catalogs;
    this.powerupCatalog = new Map((catalogs.powerups ?? []).map((item) => [item.id, item]));
    this.breedCatalog = new Map((catalogs.breeds ?? []).map((breed) => [breed.id, breed]));
    this.slots.setCatalog(this.powerupCatalog);
    this.primer.build(catalogs.powerups ?? []);
    const tuning = catalogs.tuning ?? {};
    this.batchMs = tuning.tapBatchMs ?? DEFAULT_BATCH_MS;
    for (const batcher of this.tapBatchers.values()) batcher.batchMs = this.batchMs;
    this.maxBacked = tuning.maxBackedHorses ?? DEFAULT_MAX_BACKED;
    this.countdownSeconds = tuning.countdownSeconds ?? DEFAULT_COUNTDOWN_SECONDS;
    this.countdownNumbers =
      tuning.countdownNumbersSeconds ?? DEFAULT_COUNTDOWN_NUMBERS_SECONDS;
    this.retrySeconds = tuning.challengeRetrySeconds ?? DEFAULT_RETRY_SECONDS;
    this.tapCap = tuning.tapCap ?? DEFAULT_TAP_CAP;
    this.betting.configure(tuning.minBet ?? 50);
    // The default matches INVENTORY_SLOTS; rebuild only if this server disagrees.
    const wanted = tuning.inventorySlots ?? DEFAULT_SLOT_COUNT;
    if (wanted !== this.slots.slots.length) this._buildSlots(wanted);
    else this.slots.retrySeconds = this.retrySeconds;
    this.mountReactions(catalogs.reactions ?? []);
  }

  _buildSlots(count) {
    const container = byId("slots");
    container.replaceChildren();
    this.slots = new PowerupSlots(container, {
      count,
      retrySeconds: this.retrySeconds,
      onActivate: (index, view) => this.activateSlot(index, view),
    });
    this.slots.setCatalog(this.powerupCatalog);
  }

  applyLive(live) {
    this.trackLength = live.trackLength ?? this.trackLength;
    this.duration = live.duration ?? this.duration;
    if (live.snapshot) {
      this.snapshot = live.snapshot;
      this.hudDirty = true;
    }
  }

  /**
   * Authoritative inventory (§9.0): per-slot objects carrying the lock state, so
   * this is also how an unlock, a wrong-answer penalty and a landed cast are all
   * confirmed. Nothing about a slot is ever assumed locally.
   */
  applyInventory(inventory) {
    const slots = inventory ?? [];
    const cast = this.pendingCast;
    if (cast && !slots[cast.index]) {
      this.pendingCast = null;
      this.slots.showDuration(cast.index, cast.powerup, performance.now());
    }
    this.inventory = slots;
    this._reconcilePace(slots);
    this.slots.render(slots);
    this.challengeSheet.settle(slots);
  }

  /**
   * A slot armed — by a correct answer or by a completed pace hold.
   *
   * `inventory` is still what flips the square; this frame only supplies the
   * moment worth celebrating, which a pace unlock otherwise doesn't have: it ends
   * with the thumb still tapping and nothing on screen saying "that worked".
   */
  onUnlocked(message) {
    const powerup = this.powerupCatalog.get(message.powerup_id);
    this.paceHeld.delete(message.slot);
    if (this.paceFocus === message.slot) this.paceFocus = null;
    this.slots.flourish(message.slot);
    this.challengeSheet.confirm(message.slot);
    this.toasts.push(`${powerup?.name ?? "Item"} unlocked`, {
      emoji: "🔓",
      kind: "good",
      ms: 1600,
    });
    buzz(24);
  }

  /** Snap the locally integrated pace progress back onto the server's numbers. */
  _reconcilePace(slots) {
    for (const index of [...this.paceHeld.keys()]) {
      if (slots[index]?.challenge?.kind !== "pace") this.paceHeld.delete(index);
    }
    slots.forEach((held, index) => {
      if (held?.challenge?.kind === "pace") this.paceHeld.set(index, held.paceHeld ?? 0);
    });
    if (this.paceFocus !== null && !this.paceHeld.has(this.paceFocus)) this.paceFocus = null;
    if (this.paceFocus === null) {
      const first = this.paceHeld.keys().next();
      this.paceFocus = first.done ? null : first.value;
    }
  }

  /**
   * Phase changes arrive two ways: on `room_state` and on standalone `phase`
   * frames (photo finish and ceremony only broadcast the latter).
   */
  setPhase(next) {
    if (next === this.phase) return;
    this.phase = next;
    if (next === "betting") this.betting.reset();
    // Never leave someone stranded on the picker when the gates open.
    if (!SEAT_PHASES.has(next)) this.forceJoin = false;
  }

  applyRoomState(state) {
    this.state = state;
    this.setPhase(state.phase);
    const me = this.me();

    for (const tag of this.roomTags) setText(tag, state.code ?? this.room);
    this.renderHorsePicker(state.horses, me);
    this.renderLobby(state, me);
    if (state.betting) this.betting.update(state, me);
    for (const toggle of this.readyToggles) setAttr(toggle, "aria-pressed", String(Boolean(me?.ready)));
    this.route();
  }

  onPhase(message) {
    const data = message.data ?? {};
    if (typeof data.trackLength === "number") this.trackLength = data.trackLength;
    if (typeof data.duration === "number") this.duration = data.duration;
    this.setPhase(message.phase);
    // `countdown` in the payload is what distinguishes a race *starting* from a
    // late join into one already running.
    if (message.phase === "racing" && data.countdown !== undefined) this.resetForRace();
    // Snapshots stop arriving at the finish, so photo_finish/ceremony would
    // otherwise never get the repaint that disarms the tap zone.
    this.hudDirty = true;
    this.route();
  }

  onYou(message) {
    this.you = message;
    // Server is authoritative; the optimistic counter only leads by an in-flight
    // batch, so a bigger gap means a new race reset it.
    if (message.taps > this.localTaps || this.localTaps - message.taps > TAP_MAX_PER_MESSAGE) {
      this.localTaps = message.taps;
    }
    this.applyInventory(message.inventory);
    this.hudDirty = true;
  }

  onResult(message) {
    this.result = message;
    this.renderResults();
    this.route();
  }

  onError(message) {
    const copy = ERROR_COPY[message.message] ?? message.message;
    this.pendingCast = null;
    // A wrong answer belongs in the sheet the player is looking at, not in a
    // toast above it — and the slot's own penalty timer says the rest.
    if (message.message === "wrong") {
      this.challengeSheet.reject(this.retrySeconds);
      return;
    }
    if (message.fatal) {
      this.socket?.close();
      this.showDeadEnd({
        emoji: "🏚",
        title: "Room's not there",
        text: `${copy} Check the code on the TV.`,
        code: true,
      });
      return;
    }
    this.toasts.push(copy, { emoji: "⚠️", kind: "error", ms: 2600 });
  }

  // --------------------------------------------------------------- intent ---

  readName() {
    return this.nameInput.value.trim().slice(0, NAME_MAX_LENGTH);
  }

  /** True while this player is allowed to claim horses right now. */
  canClaimHorses() {
    if (SEAT_PHASES.has(this.phase)) return true;
    // Last Bite frees a backer whose option was discontinued (§11.3).
    return (
      this.phase === "racing" &&
      this.canRepick() &&
      !(this.me()?.horse_ids ?? []).length
    );
  }

  /** Add or remove a horse from the local selection (max `maxBacked`). */
  toggleHorse(horseId) {
    if (!this.canClaimHorses()) return;
    const picks = [...(this.pendingPicks ?? this.me()?.horse_ids ?? [])];
    const at = picks.indexOf(horseId);
    if (at >= 0) {
      picks.splice(at, 1);
    } else if (picks.length >= this.maxBacked) {
      this.toasts.push(`Four horses is the limit`, { emoji: "🐴", ms: 1800 });
      return;
    } else {
      picks.push(horseId);
    }
    this.pendingPicks = picks;
    buzz(6);
    this.hudDirty = true;
    this.renderHorsePicker(this.state?.horses ?? [], this.me());
  }

  /** Send the current selection. Empty selection joins as a spectator (§4.3). */
  join(horseIds) {
    const name = this.readName();
    if (!name) {
      this.nameField.dataset.nudge = "true";
      setTimeout(() => delete this.nameField.dataset.nudge, 450);
      this.nameInput.focus();
      this.toasts.push("Add your name first", { emoji: "✍️", ms: 2000 });
      return;
    }
    const picks = horseIds ?? [];
    store.set("name", name);
    this.desiredSeat = { name, horseIds: picks };
    this.forceJoin = false;
    this.pendingPicks = null;
    this.send({
      t: "join",
      name,
      horse_id: picks.length ? picks[0] : null,
      horse_ids: picks,
    });
    this.wakeLock.acquire();
    buzz(12);
  }

  /**
   * One tap on a slot means whatever that slot's lock means: fire it, answer for
   * it, or — for a pace lock — nothing but a nudge toward the tap button, because
   * that challenge is answered with the thumb already resting on it.
   */
  activateSlot(index, view) {
    const { state, powerup, challenge, retryLeft } = view;
    if (!powerup) return;
    if (state === SlotState.ARMED) {
      this.fireSlot(index, powerup);
      return;
    }
    if (state === SlotState.PACE) {
      this.paceFocus = index;
      this.paceMeter.flash();
      buzz(10);
      return;
    }
    if (state === SlotState.COOLING) {
      this.toasts.push(`Locked for ${retryLeft.toFixed(1)}s more`, { emoji: "⏳", ms: 1400 });
      buzz(6);
      return;
    }
    if (challenge) this.challengeSheet.openFor(index, powerup, challenge);
  }

  async fireSlot(index, powerup) {
    let target = null;
    if (powerup.target === "target") {
      const rivals = this.rivals();
      if (!rivals.length) {
        this.toasts.push(ERROR_COPY.no_target, { emoji: "🎯", kind: "error", ms: 2000 });
        return;
      }
      target = await this.sheet.choose(powerup, rivals);
      if (target === null) return;
    } else if (powerup.target === "self" || powerup.target === "trap") {
      // With several horses, "which of mine?" is a real decision — ask it. With
      // one, never interrupt: the server defaults to the only horse they back.
      const mine = this.myLiveHorses();
      if (mine.length > 1) {
        target = await this.sheet.choose(powerup, mine);
        if (target === null) return;
      }
    }
    this.slots.markFired(index);
    this.pendingCast = { index, powerup };
    this.send({ t: "use_powerup", slot: index, target_horse_id: target });
    buzz(18);
  }

  placeBet(horseId, amount) {
    this.send({ t: "bet", horse_id: horseId, amount });
  }

  mountReactions(emojis) {
    const buttons = [];
    for (const container of allOf("[data-reactions]")) {
      container.replaceChildren(
        ...emojis.map((emoji) => {
          const button = element("button", "reaction", emoji);
          button.type = "button";
          setAttr(button, "aria-label", `React ${emoji}`);
          button.addEventListener("click", () => {
            if (!this.reactionLimiter.take()) return;
            this.send({ t: "react", emoji });
            buzz(6);
            for (const other of buttons) other.dataset.cooling = "true";
            setTimeout(() => {
              for (const other of buttons) other.dataset.cooling = "false";
            }, REACTION_COOLDOWN_MS);
          });
          buttons.push(button);
          return button;
        })
      );
    }
  }

  // ------------------------------------------------------------- rendering ---

  me() {
    if (!this.state || !this.token) return null;
    return this.state.participants?.find((person) => person.id === this.token) ?? null;
  }

  myHorse() {
    const me = this.me();
    if (!me || me.horse_id === null || me.horse_id === undefined) return null;
    return this.state?.horses?.find((horse) => horse.id === me.horse_id) ?? null;
  }

  /** Rival horses for the target sheet: never your own, ranked if the race is live. */
  /** The horses you back that are still running, best-placed first. */
  myLiveHorses() {
    const backed = this.me()?.horse_ids ?? [];
    const live = new Map((this.snapshot?.h ?? []).map((entry) => [entry.i, entry]));
    return (this.state?.horses ?? [])
      .filter((horse) => backed.includes(horse.id) && live.get(horse.id)?.st !== "eliminated")
      .map((horse) => ({ ...horse, rank: live.get(horse.id)?.r ?? 0 }))
      .sort((a, b) => (a.rank || 99) - (b.rank || 99));
  }

  rivals() {
    const mine = this.me()?.horse_id;
    const ranks = new Map((this.snapshot?.h ?? []).map((entry) => [entry.i, entry.r]));
    return (this.state?.horses ?? [])
      .filter((horse) => horse.id !== mine)
      .map((horse) => ({ ...horse, rank: ranks.get(horse.id) ?? 0 }))
      .sort((a, b) => (a.rank || 99) - (b.rank || 99));
  }

  renderHorsePicker(allHorses, me) {
    const live = new Map((this.snapshot?.h ?? []).map((entry) => [entry.i, entry]));
    const horses =
      this.phase === "racing"
        ? allHorses.filter((horse) => live.get(horse.id)?.st !== "eliminated")
        : allHorses;
    const signature = horses
      .map((horse) => `${horse.id}:${horse.emoji}:${horse.name}:${horse.backers}:${horse.breed}`)
      .join("|");
    if (signature !== this.horseSignature) {
      this.horseSignature = signature;
      this.horseRow.replaceChildren(
        ...horses.map((horse) => {
          const card = element("button", "horsepick");
          card.type = "button";
          card.setAttribute("role", "listitem");
          card.style.setProperty("--horse-color", horse.color);
          card.dataset.horseId = String(horse.id);
          card.append(
            element("span", "horsepick__emoji", horse.emoji),
            element("span", "horsepick__name", horse.name)
          );
          // The host picks the breed, so this is pure "know what you're backing":
          // a 🦄 called SUSHI is a different bet from a 🫏 called SUSHI.
          const breed = this.breedCatalog.get(horse.breed);
          if (breed) {
            const tag = element("span", "horsepick__breed");
            tag.append(
              element("span", "horsepick__breedicon", breed.icon),
              element("span", null, breed.name)
            );
            card.append(tag);
          }
          card.append(
            element(
              "span",
              "horsepick__backers",
              horse.backers === 1 ? "1 backer" : `${horse.backers} backers`
            ),
            element("span", "horsepick__order", "")
          );
          card.addEventListener("click", () => this.toggleHorse(horse.id));
          return card;
        })
      );
    }
    // Selection is local until confirmed, so picking several feels instant.
    const chosen = this.pendingPicks ?? me?.horse_ids ?? [];
    const active = me?.horse_id;
    for (const card of this.horseRow.children) {
      const id = Number(card.dataset.horseId);
      card.dataset.selected = String(chosen.includes(id));
      card.dataset.active = String(id === active);
      const order = chosen.indexOf(id);
      setText(card.querySelector(".horsepick__order"), order < 0 ? "" : String(order + 1));
    }
    const canPick = this.canClaimHorses();
    this.joinScreen.dataset.locked = String(!canPick);
    setText(
      byId("join-title"),
      this.phase === "bracket"
        ? "NEXT HEAT — PICK AGAIN"
        : this.phase === "racing"
          ? "YOU'RE A FREE AGENT"
          : "PICK YOUR HORSES"
    );
    setText(
      byId("join-hint"),
      canPick
        ? this.phase === "racing"
          ? "Your option is out — back a survivor and keep tapping."
          : `Back up to ${this.maxBacked} — tap to pick, then lock in.`
        : "Horses are locked while this race plays out — watch along, then grab one when the lobby reopens."
    );

    const confirm = byId("confirm-picks");
    confirm.hidden = !canPick;
    confirm.disabled = chosen.length === 0;
    setText(
      confirm,
      chosen.length > 1 ? `RIDE ${chosen.length} HORSES →` : "RIDE THIS HORSE →"
    );
  }

  renderLobby(state, me) {
    const horse = this.myHorse();
    const card = byId("lobby-card");
    if (horse) {
      card.style.setProperty("--horse-color", horse.color);
      setText(byId("lobby-kicker"), "YOU BACK");
      setText(byId("lobby-emoji"), horse.emoji);
      setText(byId("lobby-name"), horse.name);
      const others = Math.max(0, (horse.backers ?? 1) - 1);
      setText(
        byId("lobby-backers"),
        others === 0 ? "You're its only backer" : others === 1 ? "1 other backer" : `${others} other backers`
      );
    } else {
      card.style.removeProperty("--horse-color");
      setText(byId("lobby-kicker"), "YOU ARE");
      setText(byId("lobby-emoji"), "👀");
      setText(byId("lobby-name"), "WATCHING");
      setText(byId("lobby-backers"), me?.name ? `Signed in as ${me.name}` : "No horse, no thumb cramp");
    }
    byId("lobby-swap").hidden = !SEAT_PHASES.has(this.phase);

    const players = (state.participants ?? []).filter((person) => person.horse_id !== null).length;
    setText(byId("lobby-count"), `${players} in`);
    const bracket = state.tournament;
    setText(
      byId("lobby-status"),
      this.phase === "bracket"
        ? `${bracket?.label ?? "Next heat"} coming up…`
        : state.canStart
          ? "Waiting for the host to start the race…"
          : "Waiting for the host to finish setting up…"
    );
    setText(
      byId("lobby-sub"),
      horse ? "Keep the phone in your hand — the tap zone appears when the gates open." : ""
    );
  }

  renderResults() {
    const result = this.result;
    if (!result) return;
    setText(byId("winner-emoji"), result.winner_emoji ?? "🏆");
    setText(byId("winner-name"), result.winner ?? "—");

    const mine = result.players?.find((row) => row.player_id === this.token);
    const myHorseId = this.me()?.horse_id;
    const myFinish = result.order?.find((row) => row.horse_id === myHorseId);
    setText(
      byId("winner-note"),
      myFinish
        ? myFinish.rank === 1
          ? "Your horse took it. Order it loudly."
          : `Your horse came ${ordinal(myFinish.rank)}.`
        : "The office has spoken."
    );

    setText(byId("statcard-title"), mine ? "YOUR RACE" : "YOU WATCHED");
    setText(byId("res-taps"), mine?.taps ?? 0);
    setText(byId("res-peak"), (mine?.peak_tps ?? 0).toFixed(1));
    setText(byId("res-used"), mine?.powerups_used ?? 0);
    setText(byId("res-hits"), mine?.hits ?? 0);
    setText(byId("res-unlocks"), mine?.unlocks ?? 0);
    setText(byId("res-fumbles"), mine?.fumbles ?? 0);
    setText(
      byId("res-fastest"),
      typeof mine?.fastest_unlock === "number" ? `${mine.fastest_unlock.toFixed(1)}s` : "—"
    );

    const payout = result.payouts?.find((row) => row.name === (this.me()?.name ?? ""));
    setText(
      byId("res-line"),
      payout
        ? payout.hit
          ? `Your ${payout.staked} 🥇 bet paid ${payout.won} 🥇.`
          : `Your ${payout.staked} 🥇 bet went to the pool. Next time.`
        : mine
          ? `${mine.taps} taps, ${mine.unlocks ?? 0} items unlocked, ${mine.hits} hits landed.`
          : "Grab a horse next race — tapping is the fun part."
    );
    setText(byId("res-phase"), this.phase === "ceremony" ? "CEREMONY" : "RESULTS");
  }

  showDeadEnd({ emoji, title, text, code = false }) {
    this.dead = true;
    setText(byId("dead-emoji"), emoji);
    setText(byId("dead-title"), title);
    setText(byId("dead-text"), text);
    byId("code-form").hidden = !code;
    this.router.show("dead");
  }

  // ---------------------------------------------------------------- router ---

  route() {
    if (this.dead) return;
    const me = this.me();
    const needsSeat = !me || (me.role === "player" && (me.horse_id === null || me.horse_id === undefined));
    let screen;
    if (needsSeat || this.forceJoin) screen = "join";
    else if (this.phase === "betting") screen = this.state?.betting ? "betting" : "lobby";
    else if (this.phase === "racing" || this.phase === "photo_finish") screen = "race";
    else if (this.phase === "ceremony" || this.phase === "results") screen = "results";
    else screen = "lobby";
    this.router.show(screen);
    if (screen === "race") this.syncRaceChrome();
  }

  onScreenChange(name) {
    // The rAF loop only exists for the two live screens.
    if (name === "race" || name === "betting") {
      this.painter.start();
      this.hudDirty = true;
    } else {
      this.painter.stop();
    }
    if (name === "race") this.wakeLock.acquire();
    // Arming is otherwise owned by _paintTapZone, which knows the engine phase.
    if (name === "race") return;
    this.armTaps(false);
    // The challenge sheet is a fixed overlay: it would outlive its screen.
    this.challengeSheet.close();
    this.paceMeter.hide();
  }

  /** Static-per-race bits of the race screen: identity, spectator variant. */
  syncRaceChrome() {
    const horse = this.myHorse();
    this.raceScreen.dataset.spectator = String(!horse);
    if (horse) {
      this.raceCard.style.setProperty("--horse-color", horse.color);
      setText(this.raceEmoji, horse.emoji);
      setText(this.raceName, horse.name);
      return;
    }
    setText(this.raceEmoji, "👀");
    setText(this.raceName, "SPECTATING");
    // Row height needs the container laid out, which only happens once the
    // spectator variant is on the screen.
    this.standings.build(this.state?.horses ?? []);
    this.standings.layout();
  }

  resetForRace() {
    this.result = null;
    this.pendingCast = null;
    this.localTaps = 0;
    this.you = null;
    this.snapshot = null;
    this.shownRank = 0;
    this.shownProgress = 0;
    this.inventory = [];
    this.paceHeld.clear();
    this.paceFocus = null;
    this.paceVerdict = null;
    this.tapMeter.reset();
    this.paceMeter.hide();
    this.challengeSheet.close();
    this.slots.reset();
    this.tapGrid.reset(Math.ceil(this.countdownSeconds));
    this._setPrimer((this.me()?.horse_ids ?? []).length > 0);
    this.hudDirty = true;
  }

  /** The primer only exists during the countdown, and only for people who tap. */
  _setPrimer(on) {
    this.primer.show(on);
    setAttr(this.raceScreen, "data-primer", String(Boolean(on)));
  }

  // ----------------------------------------------------------- paint loop ---

  onFrame(dt, now) {
    if (this.router.active === "betting") {
      this.betting.tick(now);
      return;
    }
    // Local feel first — the ring, the pace needle and the duration bars must
    // never wait on a snapshot. Snapshot-derived text is written at ~10/s behind
    // a dirty flag.
    this.tapGrid.decay(dt);
    this.paceVerdict = this._tickPace(dt, now);
    this.slots.tick(now);
    if (!this.hudDirty || now - this.lastHudWrite < HUD_WRITE_MS) return;
    const elapsed = (now - this.lastHudWrite) / 1000;
    this.lastHudWrite = now;
    this.hudDirty = false;
    this.paintRaceHud(Math.min(0.5, elapsed));
  }

  /**
   * Advance every live pace unlock; returns the focused slot's verdict.
   *
   * The rate is measured locally because the server's is half a second stale by
   * the time it lands, but the *rule* is the server's, copied exactly: banked
   * time grows a second per second inside the band and drains half again as fast
   * outside it. Progress is snapped back to the server on every inventory frame,
   * so drifting apart is self-correcting and never advantageous.
   *
   * @returns {"slow"|"hold"|"fast"|null}
   */
  _tickPace(dt, now) {
    const rate = this.tapMeter.sample(now, dt);
    const running = this.snapshot?.ph === "running";
    if (!this.paceHeld.size || !running) {
      this.paceMeter.hide();
      this.tapGrid.setPace("");
      return null;
    }
    let focusVerdict = null;
    for (const [index, banked] of this.paceHeld) {
      const held = this.inventory[index];
      const challenge = held?.challenge;
      const powerup = this.slots.lookup(held?.powerup_id);
      if (!challenge || !powerup) continue;
      const verdict =
        Math.abs(rate - challenge.targetRate) <= challenge.tolerance
          ? "hold"
          : rate < challenge.targetRate
            ? "slow"
            : "fast";
      const advanced = clamp(
        banked + (verdict === "hold" ? dt : -dt * PACE_DRAIN_RATE),
        0,
        challenge.holdSeconds
      );
      this.paceHeld.set(index, advanced);
      this.slots.setPace(index, advanced / challenge.holdSeconds);
      if (index !== this.paceFocus) continue;
      focusVerdict = verdict;
      const ceiling = Math.max(this.tapCap, challenge.targetRate + challenge.tolerance + 1);
      this.paceMeter.update({ powerup, challenge, rate, held: advanced, verdict, ceiling });
    }
    this.tapGrid.setPace(focusVerdict ?? "");
    return focusVerdict;
  }

  paintRaceHud(dt) {
    const snapshot = this.snapshot;
    const backed = this.me()?.horse_ids ?? [];
    const raceTime = snapshot?.rt ?? 0;
    const length = Math.max(1, this.trackLength);

    setText(this.raceClock, this.duration ? clockText(this.duration - clamp(raceTime, 0, this.duration)) : "–");

    // The header tracks your best-placed horse: with four buttons, "how am I
    // doing" means the front-runner of your stable.
    const entries = (snapshot?.h ?? []).filter((entry) => backed.includes(entry.i));
    const best = entries.reduce(
      (front, entry) => (front === null || entry.r < front.r ? entry : front),
      null
    );

    if (best) {
      const field = this.you?.field ?? snapshot.h.length;
      const horse = (this.state?.horses ?? []).find((option) => option.id === best.i);
      setText(this.raceEmoji, backed.length > 1 ? "🐎" : (horse?.emoji ?? "🐎"));
      setText(
        this.raceName,
        backed.length > 1 ? `BEST: ${horse?.name ?? "—"}` : (horse?.name ?? "—")
      );
      setText(this.racePos, `${ordinal(best.r)} of ${field}`);
      if (this.shownRank && best.r !== this.shownRank) {
        this._flashCard(best.r < this.shownRank ? "up" : "down");
      }
      this.shownRank = best.r;
      const target = clamp((best.p % length) / length, 0, 1);
      this.shownProgress = spring(this.shownProgress, target, dt, SPRING_STIFFNESS);
      this.raceProgress.style.transform = `scaleX(${this.shownProgress.toFixed(3)})`;
    } else if (!backed.length) {
      setText(this.racePos, `${snapshot?.h?.length ?? 0} horses running`);
    }

    if (snapshot?.o?.length) {
      const progress = new Map(
        (snapshot.h ?? []).map((entry) => [entry.i, (entry.p % length) / length])
      );
      this.standings.update(snapshot.o, progress);
    }

    this._paintTapGrid(snapshot, raceTime);
    this._paintStats();
  }

  /** The engine's own phase (`snapshot.ph`) decides whether taps count at all. */
  _paintTapGrid(snapshot, raceTime) {
    const me = this.me();
    const backed = me?.horse_ids ?? [];
    const byId_ = new Map((this.state?.horses ?? []).map((horse) => [horse.id, horse]));
    this.tapGrid.sync(backed.map((id) => byId_.get(id)).filter(Boolean));

    if (this.raceScreen.dataset.spectator === "true" || !backed.length) {
      this._setPrimer(false);
      this.armTaps(false);
      return;
    }
    if (this.phase === "photo_finish") {
      this._setPrimer(false);
      this.tapGrid.each((zone) => {
        zone.setState("done");
        zone.setLabel("📸", "PHOTO FINISH");
      });
      this.armTaps(false);
      return;
    }
    if (!snapshot) {
      this.armTaps(false);
      return;
    }
    if (snapshot.ph === "countdown") {
      const count = String(Math.max(1, Math.ceil(-raceTime)));
      // Clear the crib sheet for the closing 3-2-1: the buttons get their full
      // height back a beat before anyone needs to hit them.
      this._setPrimer(-raceTime > this.countdownNumbers);
      this.tapGrid.each((zone) => {
        zone.setState("countdown");
        zone.setLabel(count, "GATES CLOSED");
      });
      this.armTaps(false);
      return;
    }
    this._setPrimer(false);
    if (snapshot.ph === "finished") {
      this.tapGrid.each((zone) => {
        zone.setState("done");
        zone.setLabel("🏁", "THAT'S DINNER");
      });
      this.armTaps(false);
      return;
    }

    const live = new Map((snapshot.h ?? []).map((entry) => [entry.i, entry]));
    const rates = this.you?.rates ?? {};
    const maxed = Boolean(this.you?.maxed);
    // A pace unlock takes over the sub-label: while one is live, "hold it" is
    // more actionable than a place and a rate.
    const paceSub = PACE_SUB[this.paceVerdict] ?? null;
    const tappable = [];
    for (const horseId of backed) {
      const zone = this.tapGrid.get(horseId);
      if (!zone) continue;
      const entry = live.get(horseId);
      if (entry?.st === "eliminated") {
        zone.setState("out");
        zone.setLabel("OUT", this.canRepick() ? "TAP TO SWAP" : "");
        continue;
      }
      zone.setState("live");
      const rate = Number(rates[String(horseId)] ?? 0);
      zone.setLabel(
        "TAP!",
        paceSub ?? (entry?.r ? `${ordinal(entry.r)} · ${rate.toFixed(0)}/s` : "")
      );
      zone.setMaxed(maxed);
      tappable.push(horseId);
    }
    this.armTaps(true, tappable);
  }

  /** True when the room lets a stranded player grab another horse (§11.3). */
  canRepick() {
    return this.state?.config?.mode === "last_bite";
  }

  /**
   * An eliminated horse's button becomes a swap: pick any survivor and keep
   * tapping. Without this, a Last Bite backer is a spectator for the rest of
   * the race the moment their option is discontinued.
   */
  async replaceEliminatedHorse(deadHorseId) {
    if (!this.canRepick()) {
      this.toasts.push("That option is out of the race", { emoji: "🪦", ms: 2000 });
      return;
    }
    const live = new Map((this.snapshot?.h ?? []).map((entry) => [entry.i, entry]));
    const backed = this.me()?.horse_ids ?? [];
    const options = (this.state?.horses ?? []).filter(
      (horse) => live.get(horse.id)?.st !== "eliminated" && !backed.includes(horse.id)
    );
    if (!options.length) {
      this.toasts.push("Nothing left to back", { emoji: "🤷", ms: 2000 });
      return;
    }
    const chosen = await this.sheet.choose(
      { name: "Back another horse", emoji: "🐎", blurb: "Your option is out. Pick a survivor." },
      options.map((horse) => ({ ...horse, rank: live.get(horse.id)?.r ?? 0 }))
    );
    if (chosen === null) return;
    const replacement = backed.map((id) => (id === deadHorseId ? chosen : id));
    this.join(replacement);
  }

  _paintStats() {
    setText(this.statTaps, this.localTaps);
    setText(this.statTps, (this.you?.tps ?? 0).toFixed(1));
    const chips = [];
    if (this.you?.espresso > 0) chips.push({ kind: "espresso", text: `☕ ${this.you.espresso.toFixed(1)}s` });
    if (this.you?.hits) chips.push({ kind: "hits", text: `🎯 ${this.you.hits}` });
    const signature = chips.map((chip) => chip.text).join("|");
    if (signature === this.shownChips) return;
    this.shownChips = signature;
    this.statChips.replaceChildren(
      ...chips.map((chip) => {
        const node = element("span", "microchip", chip.text);
        node.dataset.kind = chip.kind;
        return node;
      })
    );
  }

  _flashCard(direction) {
    delete this.raceCard.dataset.flash;
    void this.raceCard.offsetWidth; // reflow, so the animation restarts on a repeat
    this.raceCard.dataset.flash = direction;
    buzz(direction === "up" ? 12 : 6);
  }

  _onTap(horseId) {
    const counted = this.batcherFor(horseId).hit();
    if (counted) {
      this.localTaps += 1;
      // Only taps the server will actually see may move the pace needle.
      this.tapMeter.hit(performance.now());
    }
    this.hudDirty = true;
    return counted;
  }
}

// -------------------------------------------------------------------- boot ---

function normaliseCode(raw) {
  const code = String(raw ?? "")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "");
  return code.length === ROOM_CODE_LENGTH ? code : null;
}

function readRoomCode() {
  return normaliseCode(new URLSearchParams(location.search).get("room"));
}

new PhoneController().start();
