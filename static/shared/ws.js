/**
 * Reconnecting websocket shared by the display and the phone (SPEC.md §7.4).
 *
 * Reconnect is transparent: the socket re-sends `hello` with the stored token,
 * so the server restores the same identity, horse and inventory. Callers only
 * see `onMessage` and a connection-state callback for the UI dot.
 */

const BACKOFF_START_MS = 500;
const BACKOFF_MAX_MS = 4000;
const PING_INTERVAL_MS = 15000;

export const ConnectionState = {
  CONNECTING: "connecting",
  ONLINE: "online",
  OFFLINE: "offline",
};

export class GameSocket {
  /**
   * @param {object} options
   * @param {() => object} options.hello  Builds the hello frame on every connect.
   * @param {(msg: object) => void} options.onMessage
   * @param {(state: string) => void} [options.onState]
   */
  constructor({ hello, onMessage, onState }) {
    this.buildHello = hello;
    this.onMessage = onMessage;
    this.onState = onState ?? (() => {});
    this.socket = null;
    this.backoff = BACKOFF_START_MS;
    this.closedByUs = false;
    this.pingTimer = null;
    this.latency = 0;
    this._queue = [];

    // Phones drop their socket on screen lock; wake straight back up.
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && this.readyState !== WebSocket.OPEN) this.connect();
    });
    window.addEventListener("online", () => this.connect());
  }

  get readyState() {
    return this.socket ? this.socket.readyState : WebSocket.CLOSED;
  }

  get url() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    return `${scheme}://${location.host}/ws`;
  }

  connect() {
    if (this.readyState === WebSocket.OPEN || this.readyState === WebSocket.CONNECTING) return;
    this.closedByUs = false;
    this.onState(ConnectionState.CONNECTING);
    let socket;
    try {
      socket = new WebSocket(this.url);
    } catch (error) {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.addEventListener("open", () => {
      this.backoff = BACKOFF_START_MS;
      this.onState(ConnectionState.ONLINE);
      this.send(this.buildHello());
      this._queue.forEach((frame) => socket.send(frame));
      this._queue = [];
      this.startPing();
    });

    socket.addEventListener("message", (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (error) {
        return;
      }
      if (payload.t === "pong") {
        if (payload.ts) this.latency = Math.round((performance.now() - payload.ts) / 2);
        return;
      }
      this.onMessage(payload);
    });

    socket.addEventListener("close", () => {
      this.stopPing();
      if (!this.closedByUs) {
        this.onState(ConnectionState.OFFLINE);
        this.scheduleReconnect();
      }
    });

    socket.addEventListener("error", () => socket.close());
  }

  scheduleReconnect() {
    const delay = this.backoff;
    this.backoff = Math.min(BACKOFF_MAX_MS, Math.round(this.backoff * 1.7));
    setTimeout(() => this.connect(), delay);
  }

  startPing() {
    this.stopPing();
    this.pingTimer = setInterval(() => this.send({ t: "ping", ts: performance.now() }), PING_INTERVAL_MS);
  }

  stopPing() {
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  /** Send a frame, queueing it if the socket is briefly down. */
  send(message) {
    const frame = JSON.stringify(message);
    if (this.readyState === WebSocket.OPEN) {
      this.socket.send(frame);
      return true;
    }
    // Only queue intent that still makes sense later; taps are perishable.
    if (message.t !== "tap" && this._queue.length < 20) this._queue.push(frame);
    return false;
  }

  close() {
    this.closedByUs = true;
    this.stopPing();
    if (this.socket) this.socket.close();
  }
}

/** Small helper: persist and recall a value across sessions. */
export const store = {
  get(key, fallback = null) {
    try {
      const raw = localStorage.getItem(`dlpicker.${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (error) {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(`dlpicker.${key}`, JSON.stringify(value));
    } catch (error) {
      /* private browsing — non-fatal */
    }
  },
};
