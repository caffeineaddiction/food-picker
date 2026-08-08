/**
 * Main display orchestration (SPEC.md §5, §7).
 *
 * Responsibilities, in order of importance:
 *   1. Keep the race looking smooth — snapshots arrive at 20 Hz and are rendered
 *      at 60 fps by interpolating 150 ms in the past (§7.3).
 *   2. Route server events into spectacle (VFX, sounds, banners, notifications).
 *   3. Host flow: create room → enter options → lobby with QR → start.
 */

import { ConnectionState, GameSocket, store } from "../shared/ws.js";
import { clamp, Shake } from "../shared/motion.js";
import { INTERPOLATION_DELAY_MS, interpolateFrame, pushSnapshot } from "./interpolate.js";
import { AudioEngine } from "./audio.js";
import { BracketScreen, Ceremony, PhotoFinish } from "./ceremony.js";
import {
  BannerStage,
  CommentaryTicker,
  Countdown,
  PowerupPrimer,
  LeaderboardRail,
  Minimap,
  NotificationLane,
  TopBar,
} from "./hud.js";
import { ReactionLayer } from "./reactions.js";
import { Renderer } from "./renderer.js";

const SNAPSHOT_BUFFER = 24;
/** Quiet period before typed options are sent, so a paste is one update. */
const OPTIONS_DEBOUNCE_MS = 450;
/** How long reactions stay in celebration mode after a winner is crowned. */
const C_CEREMONY_PARTY_SECONDS = 25;

const $ = (id) => document.getElementById(id);

/** Escape HTML special characters to prevent XSS from server-sourced strings. */
function esc(str) {
  const el = document.createElement("span");
  el.textContent = str;
  return el.innerHTML;
}

class Display {
  constructor() {
    this.renderer = new Renderer($("track"));
    this.audio = new AudioEngine();
    this.shake = new Shake();

    this.rail = new LeaderboardRail($("rail"));
    this.minimap = new Minimap($("minimap"));
    this.notifications = new NotificationLane($("notifications"));
    this.banners = new BannerStage($("banners"));
    this.ticker = new CommentaryTicker($("ticker"));
    this.countdown = new Countdown($("countdown"));
    this.primer = new PowerupPrimer($("primer"));
    this.topbar = new TopBar({ clock: $("raceClock"), mode: $("raceMode"), label: $("raceLabel") });
    this.photoFinish = new PhotoFinish($("photoFinish"), this.audio);
    this.ceremony = new Ceremony($("ceremony"), this.audio);
    this.bracket = new BracketScreen($("bracketScreen"));
    this.reactions = new ReactionLayer($("reactionLayer"));

    this.state = {
      code: null,
      hostToken: null,
      roomState: null,
      catalogs: null,
      horses: [],
      horseSpecs: new Map(),
      trackLength: 1000,
      duration: 60,
      phase: "lobby",
      raceTime: -3,
      selectedMode: "classic",
      selectedTrack: "random",
    };

    this.buffer = [];
    this.lastFrameAt = performance.now();
    this.slowMotion = 1;
    this.muted = store.get("muted", false);

    this.bindUi();
    this.boot();
    requestAnimationFrame(() => this.frame());
  }

  // ------------------------------------------------------------------ boot

  async boot() {
    const params = new URLSearchParams(location.search);

    // Somebody typed the tunnel URL on their phone instead of scanning the QR.
    // The TV interface is useless to them, so hand them the controller — unless
    // they explicitly asked to host from this device.
    const phoneSized = Math.min(window.innerWidth, window.innerHeight) < 520;
    if (phoneSized && !store.get("hostRoom") && params.get("host") === null) {
      location.replace("/play");
      return;
    }

    const existingCode = params.get("room") || store.get("hostRoom");
    const existingToken = store.get("hostToken");

    if (existingCode && existingToken) {
      // Display reload mid-race: re-attach and resume rendering (§7.4).
      const response = await fetch(`/api/rooms/${existingCode}`).catch(() => null);
      if (response && response.ok) {
        this.state.code = existingCode;
        this.state.hostToken = existingToken;
        this.connect();
        return;
      }
    }
    this.showScreen("title");
  }

  async createRoom() {
    const response = await fetch("/api/rooms", { method: "POST" });
    const data = await response.json();
    this.state.code = data.code;
    this.state.hostToken = data.hostToken;
    this.state.joinUrl = data.joinUrl;
    store.set("hostRoom", data.code);
    store.set("hostToken", data.hostToken);
    this.connect();
    this.showScreen("setup");
  }

  connect() {
    this.socket = new GameSocket({
      hello: () => ({
        t: "hello",
        room: this.state.code,
        role: "host",
        host_token: this.state.hostToken,
        token: store.get("hostParticipant"),
      }),
      onMessage: (message) => this.onMessage(message),
      onState: (connection) => this.onConnectionState(connection),
    });
    this.socket.connect();
  }

  onConnectionState(connection) {
    $("connDot").dataset.state =
      connection === ConnectionState.ONLINE
        ? "online"
        : connection === ConnectionState.CONNECTING
        ? "connecting"
        : "offline";
    $("reconnectOverlay").dataset.visible = String(connection === ConnectionState.OFFLINE);
  }

  // --------------------------------------------------------------- messages

  onMessage(message) {
    switch (message.t) {
      case "welcome":
        this.onWelcome(message);
        break;
      case "room_state":
        this.onRoomState(message);
        break;
      case "phase":
        this.onPhase(message);
        break;
      case "snapshot":
        this.onSnapshot(message);
        break;
      case "notify":
        this.onNotify(message);
        break;
      case "event":
        this.onEvent(message);
        break;
      case "commentary":
        this.ticker.say(message.text, message.priority);
        break;
      case "reaction":
        this.reactions.spawn(message.emoji);
        break;
      case "result":
        this.state.lastResults = message;
        break;
      case "error":
        if (message.fatal) this.onFatalError(message);
        break;
      default:
        break;
    }
  }

  onWelcome(message) {
    this.state.catalogs = message.catalogs;
    this.state.breeds = new Map((message.catalogs?.breeds ?? []).map((breed) => [breed.id, breed]));
    setCountdownSplit(message.catalogs?.tuning?.countdownNumbersSeconds ?? 3);
    if (message.token) store.set("hostParticipant", message.token);
    this.buildPickers();
    this.onRoomState(message.state);
    if (message.live) {
      this.beginRace(message.live);
      if (message.live.snapshot) this.onSnapshot(message.live.snapshot);
    }
  }

  onFatalError() {
    // The stored room is gone (server restarted): start clean.
    store.set("hostRoom", null);
    store.set("hostToken", null);
    this.showScreen("title");
  }

  onRoomState(state) {
    if (!state) return;
    this.state.roomState = state;
    this.state.code = state.code;
    this.state.horses = state.horses;
    this.state.horseSpecs = new Map(state.horses.map((horse) => [horse.id, horse]));

    $("roomCode").textContent = state.code;
    this.renderLobby(state);
    this.syncConfigInputs(state.config);

    const phase = state.phase;
    $("startRace").disabled = !state.canStart;
    $("startRace").textContent = state.raceNumber > 0 ? "RACE AGAIN" : "START THE RACE";

    // Navigate on phase *transitions* only. room_state also arrives on every
    // keystroke in the options box, and re-asserting a screen each time yanked
    // the host out of the editor mid-sentence.
    const changed = phase !== this.lastRoomPhase;
    this.lastRoomPhase = phase;
    if (!changed) return;
    if (phase === "lobby" || phase === "results") {
      // The setup screen is a deliberate place to be: never steal it.
      if (document.body.dataset.screen !== "setup") this.showScreen("lobby");
    }
  }

  onPhase(message) {
    const { phase, data } = message;
    this.state.phase = phase;

    switch (phase) {
      case "racing":
        this.reactions.setParty(false);
        this.reactions.clear();
        this.beginRace(data);
        break;
      case "betting":
        this.showScreen("lobby");
        this.banners.show("BETTING IS OPEN", "🎰", { duration: 2600, tone: "good" });
        break;
      case "photo_finish":
        this.slowMotion = 0.25;
        this.photoFinish.play(data.results, () => this.renderer.whiteFlash(0.9));
        break;
      case "ceremony":
        this.slowMotion = 1;
        this.reactions.setParty(true, C_CEREMONY_PARTY_SECONDS);
        this.reactions.parrotStorm();
        this.photoFinish.clear();
        this.audio.stopMusic(1.2);
        this.audio.stopHooves();
        this.audio.setCrowd(0.9);
        this.ceremony.show(
          data,
          (colors) => this.renderer.celebrate(colors),
          this.state.horseSpecs
        );
        break;
      case "results":
        this.ceremony.hide();
        this.bracket.hide();
        this.showScreen("lobby");
        break;
      case "bracket":
        this.ceremony.hide();
        this.bracket.show(data.bracket);
        break;
      default:
        break;
    }
  }

  beginRace(data) {
    if (!data) return;
    this.state.trackLength = data.trackLength || 1000;
    this.state.duration = data.duration || 60;
    this.buffer = [];
    this.slowMotion = 1;
    this.notifications.clear();
    this.ticker.clear();
    this.ceremony.hide();
    this.bracket.hide();
    this.photoFinish.clear();

    this.renderer.setRace({
      horses: this.state.horses,
      theme: data.track.theme,
      trackLength: this.state.trackLength,
      breeds: this.state.breeds,
    });
    this.primer.show(this.state.catalogs?.powerups ?? []);
    this.rail.build(this.state.horses);
    this.minimap.build(this.state.horses);
    this.topbar.setRace({ mode: data.mode, label: data.label, duration: this.state.duration });

    this.showScreen("race");
    this.audio.unlock();
    this.audio.setMuted(this.muted);
    this.audio.setCrowd(0.35);
    this.banners.show(data.track.name, "🏁", { duration: 2200 });
  }

  onSnapshot(snapshot) {
    pushSnapshot(this.buffer, snapshot, performance.now(), SNAPSHOT_BUFFER);
    this.state.raceTime = snapshot.rt;

    if (snapshot.ph === "running" && !this.musicStarted) {
      this.musicStarted = true;
      this.audio.startMusic(this.renderer.theme?.music || "derby");
    }
  }

  onNotify(message) {
    this.notifications.push(message);
    this.audio.cast(message.tier);
    if (message.tier === "epic") {
      this.shake.add(0.35);
      this.renderer.whiteFlash(0.25);
    }
    this.spawnCastVfx(message);
  }

  /** Powerup-specific bursts at the affected horse (§9 visual column). */
  spawnCastVfx(message) {
    const horseCount = this.state.horses.length;
    const targetId = message.target_horse_id ?? message.caster_horse_id;
    const entry = this.latestEntry(targetId);
    if (!entry) return;
    const lane = this.laneOf(targetId);
    const { x, y } = this.renderer.screenPositionOf(entry.pos, lane, horseCount);

    const byId = {
      turbo_boost: () => this.renderer.particles.emit("spark", { x, y, count: 14, vx: -180, color: "#4EA8FF" }),
      rocket_horseshoes: () => this.renderer.particles.emit("flame", { x, y, count: 18, vx: -220, color: "#ffb347" }),
      sugar_rush: () => this.renderer.particles.emit("sprinkle", { x, y: y - 30, count: 20, color: "#FF6FB5" }),
      espresso: () => this.renderer.particles.emit("smoke", { x, y: y - 40, count: 10, color: "#d9c6a5" }),
      shield: () => this.audio.shield(),
      ghost_horse: () => this.audio.ghost(),
      diamond_hands: () => this.renderer.particles.emit("star", { x, y: y - 40, count: 8, emoji: "💎" }),
      banana: () => this.renderer.particles.emit("emoji", { x, y, count: 1, emoji: "🍌", scale: 1.2 }),
      oil_slick: () => this.renderer.particles.emit("smoke", { x, y, count: 12, color: "#2b2b3a" }),
      short_seller: () => this.renderer.particles.emit("emoji", { x, y: y - 50, count: 3, emoji: "📉" }),
      headwind: () => this.renderer.particles.emit("leaf", { x: x + 60, y: y - 20, count: 10, vx: -260 }),
      tailwind: () => this.renderer.particles.emit("leaf", { x, y: y - 10, count: 10, vx: -160 }),
      fed_rate_cut: () => this.renderer.particles.emit("money", { x: this.renderer.width / 2, y: 40, count: 26, vy: 60, spread: 400 }),
      reply_all: () => this.renderer.particles.emit("emoji", { x: this.renderer.width / 2, y: 60, count: 14, emoji: "📧", spread: 500 }),
      circuit_breaker: () => this.audio.freeze(),
      rug_pull: () => {
        this.shake.add(0.8);
        this.renderer.particles.emit("smoke", { x, y, count: 22, color: "#c9b7a0" });
        this.audio.stumble();
      },
      swap_places: () => this.renderer.particles.emit("smoke", { x, y, count: 16, color: "#c77dff" }),
      golden_carrot: () => this.renderer.particles.emit("star", { x, y: y - 40, count: 12, emoji: "🥕" }),
      bull_run: () => this.renderer.particles.emit("dust", { x, y, count: 16, color: "#e0c9a6" }),
      market_manipulation: () => this.renderer.particles.emit("smoke", { x, y, count: 14, color: "#9b8cff" }),
      hay_bale: () => this.renderer.particles.emit("emoji", { x, y, count: 4, emoji: "🌾" }),
      pump_dump: () => this.renderer.particles.emit("emoji", { x, y: y - 40, count: 4, emoji: "📈" }),
      dead_cat: () => this.renderer.particles.emit("emoji", { x, y: y - 30, count: 1, emoji: "🐈", scale: 1.4 }),
      magnet: () => this.renderer.particles.emit("spark", { x, y, count: 8, color: "#9be15d" }),
      insider: () => this.renderer.particles.emit("emoji", { x, y: y - 40, count: 1, emoji: "🕵️" }),
      photo_lunge: () => this.renderer.particles.emit("star", { x, y: y - 40, count: 6, emoji: "📸" }),
    };
    byId[message.powerup_id]?.();
  }

  onEvent(message) {
    const horseCount = this.state.horses.length;
    switch (message.kind) {
      case "event_telegraph":
        this.banners.show(message.headline, message.emoji, { tone: "danger", duration: 1500 });
        this.audio.telegraph();
        break;
      case "event_fired":
        this.onWorldEvent(message);
        break;
      case "lead_change":
        this.renderer.surgeCrowd(0.9);
        this.shake.add(0.12);
        this.audio.cheer(0.6);
        break;
      case "eliminated":
        this.banners.show(`${message.horse} IS OUT`, "🪓", { tone: "danger", duration: 2400 });
        this.audio.sadTrombone();
        break;
      case "horse_finished":
        if (message.rank === 1) {
          this.renderer.surgeCrowd(1.6);
          this.renderer.whiteFlash(0.5);
          this.shake.add(0.5);
          this.audio.cheer(1.4);
        }
        break;
      case "track_moment":
        if (message.headline) {
          this.banners.show(message.headline, message.emoji, { duration: 1700 });
        }
        if (message.kind === "beat_drop") {
          this.reactions.spawn("🦜", { burst: 4 });
          this.renderer.surgeCrowd(1.2);
          this.shake.add(0.18);
        }
        if (message.kind === "lunge") this.audio.shutter();
        break;
      case "pickup":
        if (message.kind === "banana" || message.kind === "oil") {
          this.onSlip(message);
          break;
        }
        if (!message.blocked && message.label) {
          const entry = this.latestEntry(message.horse_id);
          if (entry) {
            const { x, y } = this.renderer.screenPositionOf(
              entry.pos,
              this.laneOf(message.horse_id),
              horseCount
            );
            this.renderer.particles.emit("star", { x, y: y - 40, count: 6 });
          }
        }
        break;
      case "race_finished":
        this.musicStarted = false;
        break;
      default:
        break;
    }
  }

  /** Somebody hit a trap: sell the wipeout hard (peel, stars, dust, whistle). */
  onSlip(message) {
    const entry = this.latestEntry(message.horse_id);
    if (!entry) return;
    const lane = this.laneOf(message.horse_id);
    const { x, y } = this.renderer.screenPositionOf(entry.pos, lane, this.state.horses.length);
    if (message.blocked) {
      this.renderer.particles.emit("spark", { x, y, count: 10, color: "#ffd666" });
      this.audio.shield();
      return;
    }
    this.renderer.particles.emit("emoji", {
      x,
      y,
      count: 1,
      emoji: message.kind === "banana" ? "🍌" : "🛢️",
      scale: 1.1,
      vy: -220,
      vx: -120,
    });
    this.renderer.particles.emit("star", { x, y: y - 46, count: 7, spread: 90 });
    this.renderer.particles.emit("dust", {
      x,
      y: y + 6,
      count: 14,
      spread: 120,
      color: "#e6d8bf",
    });
    this.shake.add(0.22);
    this.audio.stumble();
    this.banners.show(`${message.horse} SLIPS!`, "🍌", { tone: "danger", duration: 1500 });
  }

  onWorldEvent(message) {
    this.banners.show(message.headline || message.name, message.emoji, {
      tone: message.event_id === "second_wind" ? "good" : "neutral",
      duration: 2200,
    });
    this.audio.playFor(message.event_id, { shake: message.shake });
    if (message.shake) this.shake.add(message.shake);

    switch (message.event_id) {
      case "rain":
        this.renderer.ambience.setWeather("rain", message.duration || 8);
        break;
      case "cow":
        this.spawnWorldEmoji("🐄", message.pos, 1.8);
        break;
      case "meteor":
        this.spawnWorldEmoji("☄️", message.pos, 1.6);
        this.renderer.whiteFlash(0.4);
        break;
      case "pigeons":
        this.renderer.particles.emit("feather", {
          x: this.renderer.width * 0.6,
          y: this.renderer.height * 0.5,
          count: 18,
          spread: 220,
        });
        break;
      case "crowd_wave":
        this.renderer.surgeCrowd(1.5);
        break;
      case "drone":
        this.spawnWorldEmoji("🚁", null, 1.4);
        break;
      case "apple":
        this.spawnWorldEmoji("🍎", message.pos, 1.2);
        break;
      case "earthquake":
        this.shake.add(0.9);
        break;
      case "office_manager":
        this.spawnWorldEmoji("👔", null, 2.4);
        break;
      case "jockey_swap":
        this.applyJockeySwap(message.jockeys);
        break;
      default:
        break;
    }
  }

  applyJockeySwap(jockeys) {
    if (!jockeys) return;
    for (const [horseId, jockey] of Object.entries(jockeys)) {
      const view = this.renderer.views.get(Number(horseId));
      if (view) view.spec.jockey = jockey;
    }
  }

  spawnWorldEmoji(emoji, worldX, scale) {
    const x =
      worldX != null
        ? this.renderer.worldToScreen(worldX)
        : this.renderer.width * (0.3 + Math.random() * 0.4);
    this.renderer.particles.emit("emoji", {
      x,
      y: this.renderer.height * 0.55,
      count: 1,
      emoji,
      scale,
      vy: -20,
      spread: 10,
    });
  }

  // ---------------------------------------------------------------- helpers

  latestEntry(horseId) {
    const latest = this.buffer[this.buffer.length - 1];
    if (!latest) return null;
    const entry = latest.snapshot.h.find((horse) => horse.i === horseId);
    return entry ? { pos: entry.p } : null;
  }

  laneOf(horseId) {
    return this.state.horses.findIndex((horse) => horse.id === horseId);
  }

  // ------------------------------------------------------------ frame loop

  frame() {
    const now = performance.now();
    const rawDt = Math.min(0.05, (now - this.lastFrameAt) / 1000);
    this.lastFrameAt = now;
    const dt = rawDt * this.slowMotion;

    this.shake.update(rawDt);
    const frame = this.interpolate(now);
    if (frame) {
      const offset = this.shake.offset();
      const ctx = this.renderer.ctx;
      ctx.save();
      ctx.translate(offset.x, offset.y);
      this.renderer.draw(dt, frame);
      ctx.restore();

      if (document.body.dataset.screen === "race") this.updateRaceHud(frame);
    }
    requestAnimationFrame(() => this.frame());
  }

  /** Render 150 ms in the past (§7.3); the maths lives in interpolate.js. */
  interpolate(now) {
    if (!this.renderer.theme) return null;
    return interpolateFrame(
      this.buffer,
      now,
      (horseId) => this.laneOf(horseId),
      INTERPOLATION_DELAY_MS
    );
  }

  updateRaceHud(frame) {
    this.topbar.update(frame.raceTime);
    this.minimap.update(frame.live, this.state.trackLength);

    const states = {};
    for (const entry of frame.live) states[entry.id] = entry.state;
    this.rail.update(frame.order, states);

    this.primer.update(frame.raceTime);
    this.countdown.update(frame.raceTime, (remaining, label) => {
      this.audio.unlock();
      if (label === "GO!") {
        this.audio.gatesOpen();
        this.renderer.surgeCrowd(1.4);
        this.shake.add(0.3);
      } else {
        this.audio.countdownBeep(3 - remaining);
      }
    });

    // Music and crowd track how close and how late the race is.
    const progress = clamp(
      Math.max(...frame.live.map((entry) => entry.pos)) / this.state.trackLength,
      0,
      1
    );
    this.audio.setIntensity(progress);
    this.audio.setCrowd(0.3 + progress * 0.6);
    const leader = frame.live.reduce(
      (best, entry) => (entry.speedMultiplier > best ? entry.speedMultiplier : best),
      0
    );
    this.audio.setHoofRate(leader);
  }

  // ------------------------------------------------------------------ views

  showScreen(name) {
    document.body.dataset.screen = name;
  }

  buildPickers() {
    const catalogs = this.state.catalogs;
    if (!catalogs || this.pickersBuilt) return;
    this.pickersBuilt = true;

    const modeGrid = $("modeGrid");
    modeGrid.innerHTML = "";
    for (const mode of catalogs.modes) {
      const button = document.createElement("button");
      button.className = "pick";
      button.dataset.value = mode.id;
      button.innerHTML = `<span class="pick__title">${esc(mode.emoji)} ${esc(mode.name)}</span>
        <span class="pick__sub">${esc(mode.tagline)}<br /><i>${esc(mode.influence)}</i></span>`;
      button.addEventListener("click", () => this.selectMode(mode.id));
      modeGrid.appendChild(button);
    }

    const trackGrid = $("trackGrid");
    trackGrid.innerHTML = "";
    const tracks = [
      { id: "random", name: "🎲 Surprise us", tagline: "Random track each race" },
      ...catalogs.tracks,
    ];
    for (const track of tracks) {
      const button = document.createElement("button");
      button.className = "pick";
      button.dataset.value = track.id;
      button.innerHTML = `<span class="pick__title">${esc(track.name)}</span>
        <span class="pick__sub">${esc(track.tagline)}${track.twist ? `<br /><i>${esc(track.twist)}</i>` : ""}</span>`;
      button.addEventListener("click", () => this.selectTrack(track.id));
      trackGrid.appendChild(button);
    }
    this.selectMode(this.state.selectedMode);
    this.selectTrack(this.state.selectedTrack);
  }

  selectMode(modeId) {
    this.state.selectedMode = modeId;
    for (const button of $("modeGrid").children) {
      button.dataset.selected = String(button.dataset.value === modeId);
    }
    const mode = this.state.catalogs.modes.find((entry) => entry.id === modeId);
    $("durationInput").disabled = Boolean(mode?.durationLocked);
    if (mode?.durationLocked) {
      $("durationOut").textContent = `${mode.defaultDuration}s (fixed)`;
    }
    this.sendConfig({ mode: modeId });
  }

  selectTrack(trackId) {
    this.state.selectedTrack = trackId;
    for (const button of $("trackGrid").children) {
      button.dataset.selected = String(button.dataset.value === trackId);
    }
    this.sendConfig({ track: trackId });
  }

  syncConfigInputs(config) {
    if (!config) return;
    if (!$("optionsInput").matches(":focus")) {
      const text = config.options.join("\n");
      if ($("optionsInput").value.trim() === "") $("optionsInput").value = text;
    }
    if (!config.durationLocked) {
      $("durationInput").value = String(config.duration);
      $("durationOut").textContent = `${config.duration}s`;
    }
    $("powerupsToggle").checked = config.powerups;
    $("eventsToggle").checked = config.events;
    const tunnel = $("publicUrlInput");
    if (document.activeElement !== tunnel) tunnel.value = config.publicUrl || "";
  }

  renderLobby(state) {
    const mode = this.state.catalogs?.modes.find((entry) => entry.id === state.config.mode);
    const track = this.state.catalogs?.tracks.find(
      (entry) => entry.id === state.config.resolvedTrack
    );
    $("lobbyMode").textContent = mode ? `${mode.emoji} ${mode.name}` : "";
    $("lobbyTrack").textContent =
      state.config.track === "random" ? "🎲 Random track" : track?.name || "";
    $("lobbyDuration").textContent = `⏱ ${Math.round(state.config.duration)}s`;

    const horsesRoot = $("lobbyHorses");
    horsesRoot.innerHTML = "";
    state.horses.forEach((horse, index) => {
      const card = document.createElement("div");
      card.className = "horsecard";
      card.style.setProperty("--horse-color", horse.color);
      card.style.animationDelay = `${index * 40}ms`;
      const breed = this.state.breeds?.get(horse.breed);
      card.innerHTML = `
        <span class="horsecard__emoji">${esc(horse.emoji)}</span>
        <span class="horsecard__name">${esc(horse.name)}</span>
        <span class="horsecard__breed">${breed ? `${esc(breed.icon)} ${esc(breed.name)}` : ""}</span>
        ${horse.backers ? `<span class="horsecard__backers">${"▲".repeat(Math.min(horse.backers, 5))}</span>` : ""}
      `;
      card.title = `Click to change ${horse.name}'s breed`;
      card.addEventListener("click", () => this.openBreedPicker(horse));
      horsesRoot.appendChild(card);
    });

    const roster = $("roster");
    const people = state.participants.filter((person) => person.role !== "host");
    if (people.length !== this.lastRosterCount) {
      if (people.length > (this.lastRosterCount ?? 0)) this.audio.join();
      this.lastRosterCount = people.length;
    }
    roster.innerHTML = "";
    for (const person of people) {
      const chip = document.createElement("div");
      chip.className = "namechip";
      chip.dataset.role = person.role;
      chip.dataset.connected = String(person.connected);
      const horse = this.state.horseSpecs.get(person.horse_id);
      chip.style.setProperty("--horse-color", horse?.color || "#8a8aa0");
      chip.innerHTML = `<span class="namechip__dot"></span>${esc(person.name)}${
        person.ready ? " ✅" : ""
      }${horse ? ` ${esc(horse.emoji)}` : ""}`;
      roster.appendChild(chip);
    }
    $("rosterCount").textContent = String(people.length);

    const stats = state.stats;
    $("sessionStats").innerHTML = stats?.races
      ? `<span>🏁 <b>${stats.races}</b> races tonight</span>
         ${stats.topFoods[0] ? `<span>👑 <b>${esc(stats.topFoods[0].name)}</b> leads with ${stats.topFoods[0].value}</span>` : ""}
         ${stats.topTappers[0] ? `<span>👍 <b>${esc(stats.topTappers[0].name)}</b> has tapped ${stats.topTappers[0].value} times</span>` : ""}`
      : "";

    if (state.betting?.open) {
      $("lobbyTitle").textContent = `Betting closes in ${Math.ceil(state.betting.closesIn)}s`;
    } else {
      $("lobbyTitle").textContent = state.raceNumber ? "Ready for another?" : "The Paddock";
    }

    this.loadQr();
  }

  /** Host picks which animal a dinner option runs as. Cosmetic only. */
  openBreedPicker(horse) {
    const breeds = this.state.catalogs?.breeds ?? [];
    if (!breeds.length) return;
    this.breedPickerFor = horse.id;
    $("breedPickerFor").textContent = `${horse.emoji} ${horse.name} runs as…`;
    const grid = $("breedPickerGrid");
    grid.replaceChildren(
      ...breeds.map((breed) => {
        const chip = document.createElement("button");
        chip.className = "breedchip";
        chip.type = "button";
        chip.dataset.selected = String(breed.id === horse.breed);
        chip.title = breed.blurb;
        chip.innerHTML = `
          <span class="breedchip__icon">${esc(breed.icon)}</span>
          <span class="breedchip__name">${esc(breed.name)}</span>
        `;
        chip.addEventListener("click", () => {
          this.socket?.send({ t: "host_set_breed", horse_id: horse.id, breed: breed.id });
          this.closeBreedPicker();
        });
        return chip;
      })
    );
    $("breedPicker").dataset.visible = "true";
  }

  closeBreedPicker() {
    this.breedPickerFor = null;
    $("breedPicker").dataset.visible = "false";
  }

  loadQr() {
    const publicUrl = this.state.roomState?.config?.publicUrl || "";
    const signature = `${this.state.code}|${publicUrl}`;
    if (this.qrLoadedFor === signature) return;
    this.qrLoadedFor = signature;
    // The SVG is generated server-side by segno (a QR library), not from user
    // input, so inline insertion is safe and required for CSS sizing rules.
    fetch(`/api/rooms/${encodeURIComponent(this.state.code)}/qr.svg`)
      .then((response) => response.text())
      .then((svg) => {
        $("qrHolder").innerHTML = svg;
      })
      .catch(() => {
        $("qrHolder").textContent = "QR unavailable";
      });
    const origin = (publicUrl || location.origin).replace(/\/$/, "");
    $("joinUrl").textContent = `${origin}/play?room=${this.state.code}`;
  }

  // --------------------------------------------------------------------- ui

  sendConfig(patch) {
    this.socket?.send({ t: "host_config", ...patch });
  }

  parsedOptions() {
    return $("optionsInput")
      .value.split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
  }

  bindUi() {
    $("newRace").addEventListener("click", () => {
      this.audio.unlock();
      this.createRoom();
    });
    $("backToTitle").addEventListener("click", () => this.showScreen("title"));
    $("editOptions").addEventListener("click", () => this.showScreen("setup"));

    const optionsInput = $("optionsInput");
    const pushOptions = () => {
      const options = this.parsedOptions();
      if (options.length) this.sendConfig({ options });
    };
    optionsInput.addEventListener("input", () => {
      const options = this.parsedOptions();
      const tuning = this.state.catalogs?.tuning;
      const min = tuning?.minOptions ?? 2;
      const max = tuning?.maxOptions ?? 12;
      const hint = $("optionsHint");
      if (options.length < min) {
        hint.dataset.error = "true";
        hint.textContent = `Add at least ${min} options.`;
      } else if (options.length > max) {
        hint.dataset.error = "true";
        hint.textContent = `That's ${options.length}. Maximum is ${max} horses.`;
      } else {
        hint.dataset.error = "false";
        hint.textContent = `${options.length} horses ready to run.`;
      }
      // Debounced: a keystroke-per-frame rebuild of the field is pure noise, and
      // pasting a list should register as one change, not forty.
      clearTimeout(this.optionsTimer);
      this.optionsTimer = setTimeout(pushOptions, OPTIONS_DEBOUNCE_MS);
    });
    optionsInput.addEventListener("blur", () => {
      clearTimeout(this.optionsTimer);
      pushOptions();
    });

    $("toLobby").addEventListener("click", () => {
      clearTimeout(this.optionsTimer);
      pushOptions();
      this.showScreen("lobby");
    });

    $("durationInput").addEventListener("input", (event) => {
      $("durationOut").textContent = `${event.target.value}s`;
      this.sendConfig({ duration: Number(event.target.value) });
    });
    $("powerupsToggle").addEventListener("change", (event) =>
      this.sendConfig({ powerups_on: event.target.checked })
    );
    $("eventsToggle").addEventListener("change", (event) =>
      this.sendConfig({ events_on: event.target.checked })
    );

    const tunnelInput = $("publicUrlInput");
    const pushTunnel = () => this.sendConfig({ public_url: tunnelInput.value.trim() });
    tunnelInput.addEventListener("change", pushTunnel);
    tunnelInput.addEventListener("blur", pushTunnel);
    tunnelInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        tunnelInput.blur();
      }
    });

    $("breedPickerClose").addEventListener("click", () => this.closeBreedPicker());

    $("startRace").addEventListener("click", () => {
      this.audio.unlock();
      this.socket?.send({ t: "host_start" });
    });
    $("rematchBtn").addEventListener("click", () => this.socket?.send({ t: "host_start" }));
    $("abortBtn").addEventListener("click", () => this.socket?.send({ t: "host_abort" }));
    $("skipBtn").addEventListener("click", () => this.socket?.send({ t: "host_skip" }));

    const muteBtn = $("muteBtn");
    const paintMute = () => {
      muteBtn.textContent = this.muted ? "🔇 Sound off" : "🔊 Sound on";
    };
    muteBtn.addEventListener("click", () => {
      this.muted = !this.muted;
      store.set("muted", this.muted);
      this.audio.unlock();
      this.audio.setMuted(this.muted);
      paintMute();
    });
    paintMute();

    // Host bar reveals on pointer movement, then hides so it never spoils the show.
    const hostbar = $("hostbar");
    let hideTimer = null;
    const reveal = () => {
      hostbar.dataset.visible = "true";
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(() => (hostbar.dataset.visible = "false"), 2600);
    };
    window.addEventListener("pointermove", reveal);
    window.addEventListener("keydown", (event) => {
      if (event.key === "f") document.documentElement.requestFullscreen?.();
      if (event.key === "m") muteBtn.click();
      reveal();
    });
  }
}

new Display();
