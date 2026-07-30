/**
 * Snapshot interpolation (SPEC.md §7.3).
 *
 * The server is authoritative at 20 Hz; the display renders at 60 fps. Rather
 * than extrapolating (which overshoots and rubber-bands on every correction),
 * the display renders a fixed 150 ms in the past and lerps between the two
 * snapshots that bracket that moment. Jitter in arrival times then costs
 * latency, never smoothness.
 *
 * This is pure so it can be tested without a canvas.
 */

import { clamp } from "../shared/motion.js";

export const INTERPOLATION_DELAY_MS = 150;

/**
 * @param {Array<{at: number, snapshot: object}>} buffer newest last
 * @param {number} now performance.now()
 * @param {(horseId: number) => number} laneOf
 * @param {number} [delayMs]
 * @returns {object|null} frame ready for the renderer
 */
export function interpolateFrame(buffer, now, laneOf, delayMs = INTERPOLATION_DELAY_MS) {
  if (!buffer.length) return null;
  const renderAt = now - delayMs;

  let older = buffer[0];
  let newer = buffer[buffer.length - 1];
  for (let index = 0; index < buffer.length - 1; index += 1) {
    if (buffer[index].at <= renderAt && buffer[index + 1].at >= renderAt) {
      older = buffer[index];
      newer = buffer[index + 1];
      break;
    }
  }

  // Before the buffer has filled (or after a stall) both ends collapse to one
  // snapshot, which renders as a held frame rather than a jump.
  const span = newer.at - older.at;
  const t = span > 0 ? clamp((renderAt - older.at) / span, 0, 1) : 1;

  const newerById = new Map(newer.snapshot.h.map((entry) => [entry.i, entry]));
  const live = older.snapshot.h.map((entry) => {
    const next = newerById.get(entry.i) || entry;
    const lane = laneOf(entry.i);
    return {
      id: entry.i,
      lane: lane < 0 ? 0 : lane,
      pos: entry.p + (next.p - entry.p) * t,
      speedMultiplier: entry.v + (next.v - entry.v) * t,
      state: next.st,
      fx: next.fx || [],
      rank: next.r,
      backers: next.b,
      laps: next.l ?? 0,
    };
  });

  return {
    live,
    zones: newer.snapshot.z || [],
    order: newer.snapshot.o || [],
    phase: newer.snapshot.ph,
    raceTime: newer.snapshot.rt,
    time: now / 1000,
    interpolation: t,
  };
}

/** Keep the newest `limit` snapshots; returns the trimmed buffer. */
export function pushSnapshot(buffer, snapshot, at, limit = 24) {
  buffer.push({ at, snapshot });
  while (buffer.length > limit) buffer.shift();
  return buffer;
}
