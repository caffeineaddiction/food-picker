/**
 * Camera framing maths (SPEC.md §5.2).
 *
 * Pure functions so the framing rules are unit-testable. The rules, in priority
 * order:
 *
 *   1. Follow `0.6 × leader + 0.4 × pack mean` so the front of the race leads
 *      the frame without abandoning the pack.
 *   2. Near the line, settle the finish at 70% of screen width so horses run
 *      *into* it — the classic broadcast shot.
 *   3. Above all else, keep the last horse on screen. If framing the finish
 *      would push the field out of frame, the field wins. Losing sight of the
 *      horses reads as "the race ended".
 */

import { clamp } from "../shared/motion.js";

export const BASE_VISIBLE_UNITS = 240;
export const FINISH_LOCK_FRACTION = 0.86;
export const FINISH_LINE_SCREEN_X = 0.7;
export const TRAIL_MARGIN = 0.12;
export const MIN_ZOOM = 0.75;

/**
 * How many track units should be visible for a given field spread.
 * Zooming out is always preferable to cropping a horse off the screen.
 */
export function visibleUnitsFor(spread) {
  const needed = Math.max(BASE_VISIBLE_UNITS, spread * 1.45 + 70);
  const zoom = clamp(BASE_VISIBLE_UNITS / needed, MIN_ZOOM, 1);
  return { zoom, visible: BASE_VISIBLE_UNITS / zoom };
}

/**
 * Left edge of the camera in world units.
 *
 * @param {object} field  { leader, trail, mean }
 * @param {number} visible track units on screen
 * @param {number} trackLength
 */
export function cameraTargetX({ leader, trail, mean }, visible, trackLength) {
  let target = leader * 0.6 + mean * 0.4 - visible * 0.42;

  // Near the line, hold the camera *back* so the finish sits at 70% of the
  // screen and the horses visibly run into it. Pushing the camera forward
  // instead (a `max` here) shoves the field off the left edge, which reads as
  // the race having ended.
  if (leader > trackLength * FINISH_LOCK_FRACTION) {
    target = Math.min(target, trackLength - visible * FINISH_LINE_SCREEN_X);
  }

  // Non-negotiable: the last horse stays in frame.
  target = Math.min(target, trail - visible * TRAIL_MARGIN);
  return clamp(target, -30, trackLength + 60);
}

/** Screen fraction (0–1) a world position lands at. Handy for assertions. */
export function screenFraction(worldX, cameraX, visible) {
  return (worldX - cameraX) / visible;
}
