/**
 * Floating emoji reactions (SPEC.md §5.3, §13.5).
 *
 * These live in the DOM rather than on the canvas for one reason: the ceremony
 * and photo-finish overlays sit *above* the canvas, so canvas-drawn reactions
 * vanish exactly when the room is most likely to be reacting. A DOM layer above
 * everything works in every phase.
 *
 * Each reaction gets its own flight path via the Web Animations API, so the
 * browser animates it off the main thread and a burst of thirty costs nothing.
 */

const MAX_LIVE = 60;

/** The party parrot is not a normal reaction and should not behave like one. */
const PARTY_PARROT = "🦜";

/** Flight styles, picked at random so a burst never looks like a formation. */
const FLIGHTS = ["rise", "arc", "tumble", "zigzag"];

export class ReactionLayer {
  constructor(root) {
    this.root = root;
    this.live = 0;
    this.partyUntil = 0;
  }

  /**
   * Ceremony mode: bigger, wilder, and launched in bursts. Winning is the
   * moment people actually want to spam 🔥 at each other.
   */
  setParty(on, seconds = 20) {
    this.partyUntil = on ? performance.now() + seconds * 1000 : 0;
  }

  get partying() {
    return performance.now() < this.partyUntil;
  }

  /** @param {string} emoji @param {{burst?: number}} [options] */
  spawn(emoji, { burst } = {}) {
    const parrot = emoji === PARTY_PARROT;
    const base = this.partying ? 3 : 1;
    const count = burst ?? (parrot ? base * 3 : base);
    for (let index = 0; index < count; index += 1) {
      this._one(emoji, index * (parrot ? 55 : 90));
    }
  }

  /** A full-screen flock, for the moment a winner is crowned. */
  parrotStorm(count = 18) {
    for (let index = 0; index < count; index += 1) {
      this._one(PARTY_PARROT, index * 70);
    }
  }

  _one(emoji, delay) {
    if (this.live >= MAX_LIVE) return;
    const node = document.createElement("span");
    node.className = "reaction";
    node.textContent = emoji;
    node.setAttribute("aria-hidden", "true");

    const parrot = emoji === PARTY_PARROT;
    const party = this.partying;
    const size = (party ? 46 : 34) + Math.random() * (party ? 44 : 22);
    if (parrot) node.dataset.parrot = "true";
    const fromLeft = Math.random() < 0.5;
    const startX = fromLeft
      ? 2 + Math.random() * 26
      : 72 + Math.random() * 26;
    node.style.fontSize = `${size}px`;
    node.style.left = `${startX}vw`;

    const flight = FLIGHTS[Math.floor(Math.random() * FLIGHTS.length)];
    const drift = (fromLeft ? 1 : -1) * (10 + Math.random() * 26);
    // Parrots spin hard and always one way, like the gif.
    const spin = parrot ? 1440 : (Math.random() - 0.5) * (party ? 900 : 320);
    const duration = (party ? 2600 : 3000) + Math.random() * 1400;

    this.live += 1;
    const animation = node.animate(this._keyframes(flight, drift, spin, party), {
      duration,
      delay,
      easing: "cubic-bezier(0.22, 0.61, 0.36, 1)",
      fill: "forwards",
    });
    animation.addEventListener("finish", () => {
      node.remove();
      this.live -= 1;
    });
    animation.addEventListener("cancel", () => {
      node.remove();
      this.live -= 1;
    });
    this.root.append(node);
  }

  _keyframes(flight, drift, spin, party) {
    const pop = party ? 1.45 : 1.15;
    switch (flight) {
      case "arc":
        return [
          { transform: "translate(0, 0) scale(0.2) rotate(0deg)", opacity: 0 },
          { transform: `translate(${drift * 0.5}vw, -22vh) scale(${pop}) rotate(${spin * 0.4}deg)`, opacity: 1, offset: 0.25 },
          { transform: `translate(${drift}vw, -60vh) scale(${pop * 0.9}) rotate(${spin * 0.8}deg)`, opacity: 0.9, offset: 0.7 },
          { transform: `translate(${drift * 1.3}vw, -104vh) scale(0.5) rotate(${spin}deg)`, opacity: 0 },
        ];
      case "tumble":
        return [
          { transform: "translate(0, 0) scale(0.2) rotate(0deg)", opacity: 0 },
          { transform: `translate(${drift * 0.3}vw, -30vh) scale(${pop}) rotate(${spin}deg)`, opacity: 1, offset: 0.3 },
          { transform: `translate(${drift * 1.1}vw, -105vh) scale(0.45) rotate(${spin * 3}deg)`, opacity: 0 },
        ];
      case "zigzag":
        return [
          { transform: "translate(0, 0) scale(0.2)", opacity: 0 },
          { transform: `translate(${drift}vw, -25vh) scale(${pop})`, opacity: 1, offset: 0.22 },
          { transform: `translate(${-drift * 0.6}vw, -55vh) scale(${pop * 0.92})`, opacity: 1, offset: 0.5 },
          { transform: `translate(${drift * 0.8}vw, -80vh) scale(${pop * 0.8})`, opacity: 0.8, offset: 0.75 },
          { transform: `translate(0vw, -108vh) scale(0.5)`, opacity: 0 },
        ];
      default:
        return [
          { transform: "translate(0, 0) scale(0.2)", opacity: 0 },
          { transform: `translate(${drift * 0.2}vw, -18vh) scale(${pop}) rotate(${spin * 0.2}deg)`, opacity: 1, offset: 0.2 },
          { transform: `translate(${drift * 0.6}vw, -102vh) scale(0.55) rotate(${spin * 0.6}deg)`, opacity: 0 },
        ];
    }
  }

  clear() {
    this.root.replaceChildren();
    this.live = 0;
  }
}
