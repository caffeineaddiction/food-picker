/**
 * The finish: photo-finish replay and victory ceremony (SPEC.md §5.2, §13.5).
 *
 * These two moments are the highest-value polish in the game — the whole room
 * is looking at the screen — so they get their own module and their own
 * choreography rather than being a results table.
 */

import { escapeHtml } from "./hud.js";

const PHOTO_STEPS = [
  { at: 0, label: "PHOTO FINISH" },
  { at: 1200, label: "REVIEWING…" },
];

export class PhotoFinish {
  constructor(root, audio) {
    this.root = root;
    this.audio = audio;
    this.timers = [];
  }

  /**
   * @param {object} results engine results payload
   * @param {() => void} onFlash called at the freeze-frame so the renderer can flash
   */
  play(results, onFlash) {
    this.clear();
    const [first, second] = results.order || [];
    this.root.dataset.visible = "true";
    this.root.innerHTML = `
      <div class="photo__grain"></div>
      <div class="photo__stage">
        <div class="photo__label">PHOTO FINISH</div>
        <div class="photo__noses">
          ${[first, second]
            .filter(Boolean)
            .map(
              (row, index) => `
            <div class="photo__nose" data-place="${index}">
              <span class="photo__emoji">${row.emoji}</span>
              <span class="photo__name">${escapeHtml(row.name)}</span>
              <span class="photo__time">${row.time != null ? `${row.time.toFixed(2)}s` : ""}</span>
            </div>`
            )
            .join("")}
        </div>
      </div>
    `;
    this.audio?.shutter();
    this.audio?.photoFinishDrone();
    if (onFlash) onFlash();

    const label = this.root.querySelector(".photo__label");
    for (const step of PHOTO_STEPS) {
      this.timers.push(
        setTimeout(() => {
          label.textContent = step.label;
          label.setAttribute("data-pulse", "true");
          setTimeout(() => label.removeAttribute("data-pulse"), 300);
        }, step.at)
      );
    }
    this.timers.push(
      setTimeout(() => {
        const winner = this.root.querySelector('.photo__nose[data-place="0"]');
        if (winner) winner.setAttribute("data-winner", "true");
        label.textContent = "CONFIRMED";
        this.audio?.confettiPop();
      }, 2400)
    );
  }

  clear() {
    this.timers.forEach(clearTimeout);
    this.timers = [];
    this.root.dataset.visible = "false";
    this.root.innerHTML = "";
  }
}

export class Ceremony {
  constructor(root, audio) {
    this.root = root;
    this.audio = audio;
  }

  /**
   * @param {object} payload { results, payouts, stats, tournament }
   * @param {(colors: string[]) => void} onConfetti
   * @param {Map<number, object>} horseSpecs
   */
  show(payload, onConfetti, horseSpecs) {
    const results = payload.results || {};
    const order = results.order || [];
    const winner = order[0];
    if (!winner) return;

    const winnerSpec = horseSpecs.get(winner.horse_id);
    const color = winnerSpec?.color || "var(--hero)";

    this.root.dataset.visible = "true";
    this.root.innerHTML = `
      <div class="ceremony__curtain"></div>
      <div class="ceremony__content">
        <div class="ceremony__eyebrow">TONIGHT'S WINNER</div>
        <div class="ceremony__winner" style="--winner-color:${color}">
          <span class="ceremony__emoji">${winner.emoji}</span>
          <span class="ceremony__dinner">DINNER IS<br /><b>${escapeHtml(winner.name)}</b></span>
        </div>
        ${this.podium(order, horseSpecs)}
        <div class="ceremony__columns">
          ${this.playerCards(results.players || [], results.winner_id)}
          ${this.payoutCard(payload.payouts || [])}
        </div>
        ${this.bracketNote(payload.tournament)}
      </div>
    `;

    this.audio?.fanfare();
    onConfetti?.([color, "#FFC53D", "#4EA8FF", "#3EDC81"]);
    const winnerNode = this.root.querySelector(".ceremony__winner");
    requestAnimationFrame(() => winnerNode.setAttribute("data-in", "true"));

    // Last place gets a trombone, because losing should also be funny.
    if (order.length > 2) setTimeout(() => this.audio?.sadTrombone(), 2200);
  }

  podium(order, horseSpecs) {
    const rows = order
      .map((row, index) => {
        const spec = horseSpecs.get(row.horse_id);
        const time = row.time != null ? `${row.time.toFixed(2)}s${row.projected ? "*" : ""}` : "—";
        return `
        <div class="podium__row" data-rank="${index + 1}" style="--horse-color:${spec?.color || "#888"}"
             ${row.eliminated ? 'data-eliminated="true"' : ""}>
          <span class="podium__place">${index + 1}</span>
          <span class="podium__emoji">${row.emoji}</span>
          <span class="podium__name">${escapeHtml(row.name)}</span>
          <span class="podium__backers">${"▲".repeat(Math.min(row.backers || 0, 5))}</span>
          <span class="podium__time">${row.eliminated ? "OUT" : time}</span>
        </div>`;
      })
      .join("");
    return `<div class="podium">${rows}</div>`;
  }

  playerCards(players, winningHorseId) {
    if (!players.length) {
      return `<div class="ceremony__card ceremony__card--empty">Nobody tapped. Bold strategy.</div>`;
    }
    const sorted = [...players].sort((a, b) => b.taps - a.taps);
    const rows = sorted
      .slice(0, 8)
      .map(
        (player) => `
      <div class="statrow" ${player.horse_id === winningHorseId ? 'data-won="true"' : ""}>
        <span class="statrow__name">${escapeHtml(player.name)}</span>
        <span class="statrow__stat">${player.taps} taps</span>
        <span class="statrow__stat">${player.peak_tps}/s peak</span>
        <span class="statrow__stat">${player.powerups_used} items</span>
        <span class="statrow__stat">${player.hits} hits</span>
      </div>`
      )
      .join("");
    return `<div class="ceremony__card"><h3>THE THUMBS</h3>${rows}</div>`;
  }

  payoutCard(payouts) {
    if (!payouts.length) return "";
    const rows = payouts
      .slice(0, 8)
      .map(
        (row) => `
      <div class="statrow" ${row.hit ? 'data-won="true"' : ""}>
        <span class="statrow__name">${escapeHtml(row.name)}</span>
        <span class="statrow__stat">staked ${row.staked}</span>
        <span class="statrow__stat">${row.won ? `won ${row.won} 🥇` : "lost it"}</span>
      </div>`
      )
      .join("");
    return `<div class="ceremony__card"><h3>THE BOOKS</h3>${rows}</div>`;
  }

  bracketNote(tournament) {
    if (!tournament) return "";
    if (tournament.champion) {
      return `<div class="ceremony__bracket">🏆 CHAMPION: <b>${escapeHtml(
        tournament.champion
      )}</b></div>`;
    }
    return `<div class="ceremony__bracket">Advancing: ${tournament.winners
      .map((name) => escapeHtml(name))
      .join(" · ")}</div>`;
  }

  hide() {
    this.root.dataset.visible = "false";
    this.root.innerHTML = "";
  }
}

/** Bracket screen shown between tournament heats. */
export class BracketScreen {
  constructor(root) {
    this.root = root;
  }

  show(bracket) {
    if (!bracket) return;
    this.root.dataset.visible = "true";
    this.root.innerHTML = `
      <div class="bracket">
        <h2>${escapeHtml(bracket.label)}</h2>
        <div class="bracket__heats">
          ${bracket.heats
            .map(
              (heat, index) => `
            <div class="bracket__heat" ${index === bracket.heatIndex ? 'data-current="true"' : ""}>
              <div class="bracket__heat-title">HEAT ${index + 1}</div>
              ${heat
                .map(
                  (name) => `<div class="bracket__option" ${
                    bracket.winners.includes(name.toUpperCase()) ||
                    bracket.winners.includes(name)
                      ? 'data-advanced="true"'
                      : ""
                  }>${escapeHtml(name)}</div>`
                )
                .join("")}
            </div>`
            )
            .join("")}
        </div>
        <div class="bracket__final">
          <div class="bracket__heat-title">FINAL</div>
          ${
            bracket.winners.length
              ? bracket.winners.map((name) => `<div class="bracket__option">${escapeHtml(name)}</div>`).join("")
              : '<div class="bracket__option bracket__option--tbd">TBD</div>'
          }
        </div>
        <p class="bracket__hint">Pick your horse on your phone — next heat starts shortly.</p>
      </div>
    `;
  }

  hide() {
    this.root.dataset.visible = "false";
    this.root.innerHTML = "";
  }
}
