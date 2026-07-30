"""Every tunable number in dl-picker.

SPEC.md §15 is the source of truth for these values. Nothing gameplay-tunable
should be hardcoded anywhere else in the codebase; import from here instead.

The balance suite in ``tests/test_balance.py`` enforces the outcome envelopes
that these constants produce, so changing anything here means re-running it.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Simulation core (§15.1)
# ---------------------------------------------------------------------------

TRACK_LENGTH = 1000.0
"""Track length in abstract "units". Base speed derives from this."""

DEFAULT_RACE_SECONDS = 60.0
MIN_RACE_SECONDS = 20.0
MAX_RACE_SECONDS = 120.0

BASE_SPEED_CALIBRATION = 0.93
"""Trim on base speed so a race lands near its configured duration.

Buffs outnumber debuffs across the item and event tables, so an uncalibrated
race finishes ~7% early. This keeps "60 seconds" honest for the host.
"""

TICK_RATE = 20
TICK_DT = 1.0 / TICK_RATE

COUNTDOWN_SECONDS = 9.0
"""Gates-closed pre-race window. Race time runs negative during it.

Long enough to actually read the powerup primer on the display — three seconds
was only ever enough to say "3, 2, 1". The last three seconds are the numbers;
everything before that is the briefing.
"""

COUNTDOWN_NUMBERS_SECONDS = 3.0
"""Tail of the countdown given over to the big 3-2-1."""

CEREMONY_SECONDS = 12.0
PHOTO_FINISH_PRESENTATION_SECONDS = 4.0

# ---------------------------------------------------------------------------
# Horses / roster limits
# ---------------------------------------------------------------------------

MIN_OPTIONS = 2
MAX_OPTIONS = 12
MAX_OPTION_NAME_LENGTH = 22
MAX_PLAYER_NAME_LENGTH = 14
MAX_BACKED_HORSES = 4
"""How many dinner options one person may back at once.

Taps only ever feed the *active* horse, so backing several is about hedging and
moving your support around mid-race, not about tapping harder."""

# ---------------------------------------------------------------------------
# Wander noise (§15.2) — organic lead trading between untapped horses
# ---------------------------------------------------------------------------

NOISE_MIN = 0.75
NOISE_MAX = 1.25
NOISE_RETARGET_MIN_S = 6.0
NOISE_RETARGET_MAX_S = 10.0
NOISE_LERP_SECONDS = 1.5
"""Time constant for easing toward a new noise target.

Noise is a horse's *form*, not jitter. Retargeting slowly (6–10s) is what makes
a horse visibly surge for a while and then fade — fast jitter averages out to
nothing over a race and reads as no variation at all on screen.

The amplitude here is load-bearing for fairness, not just for looks. See
:data:`TAP_BONUS_MAX` for the invariant that ties the two together.
"""

# ---------------------------------------------------------------------------
# Tap contribution (§15.3)
# ---------------------------------------------------------------------------

TAP_WINDOW_SECONDS = 1.0
"""Rolling window used to derive taps-per-second (smooths network batching)."""

TAP_TPS_CAP = 12.0
"""Per-player taps/sec cap. Telegraphed to players as the MAX POWER glow."""

TAP_BONUS_MAX = 0.18
"""Asymptotic ceiling of the combined tap bonus T.

**Tuning invariant.** A sustained speed bonus and zero-mean wander noise fight
each other, and the catch-up band scales both by the same factor, so the win
rate of a tapped horse is governed by the *ratio*
``TAP_BONUS_MAX / noise amplitude`` — not by the band constants. At a ratio near
0.72 (0.18 vs ±0.25) a well-backed horse wins roughly 35–40% of races while every
untapped option keeps a ~8–13% chance, which is the §2.3 target. The ratio sits
higher than it used to because powerups are now gated behind an unlock challenge
(:mod:`server.challenges`), which costs tapping time and thins the item economy. Raising this
value without widening :data:`NOISE_MAX` makes tapping deterministic; the Monte
Carlo suite in ``tests/test_balance.py`` will catch it.
"""

TAP_BONUS_MAX_GOLDEN = 0.26
"""Raised ceiling while a horse is under Golden Carrot (same ratio as §9.26)."""

TAP_BONUS_SCALE = 8.0
"""Softness of diminishing returns: T = max * (1 - e^(-X / scale))."""

# ---------------------------------------------------------------------------
# Effect stacking clamps (§15.2)
# ---------------------------------------------------------------------------

POWERUP_SUM_MIN = -0.60
POWERUP_SUM_MAX = 0.90
EVENT_SUM_MIN = -0.50
EVENT_SUM_MAX = 0.50
TRACK_SUM_MIN = -0.30
TRACK_SUM_MAX = 0.30

SPEED_MULTIPLIER_FLOOR = 0.25
"""Nothing short of a hard freeze may take a horse below this multiplier."""

ZONE_SUM_MIN = -0.35
ZONE_SUM_MAX = 0.35
"""Clamp on the combined continuous effect of overlapping track zones."""

MAX_VISIBLE_EFFECTS = 3
"""Concurrent effects per horse before the oldest non-protective one is culled."""

# ---------------------------------------------------------------------------
# Hard states
# ---------------------------------------------------------------------------

STUMBLE_SPEED_SCALE = 0.3
FREEZE_SPEED_SCALE = 0.0
FREEZE_REAPPLY_IMMUNITY_S = 5.0
RUG_PULL_IMMUNITY_S = 8.0

# ---------------------------------------------------------------------------
# Rubber band (§15.5)
# ---------------------------------------------------------------------------

RUBBER_BAND_GAIN = 0.006
RUBBER_BAND_MIN = -0.28
"""Maximum drag on a breakaway leader. Must exceed :data:`TAP_BONUS_MAX` so a
sustained tap advantage can actually be reeled in rather than running away."""
RUBBER_BAND_MAX = 0.13
RUBBER_BAND_DEADZONE = 6.0
"""Units of lead/deficit the band ignores entirely.

Small deadzone = the pack jostles freely at close quarters and only real
breakaways get corrected. Keeps the elastic from feeling like it is steering."""

# ---------------------------------------------------------------------------
# Powerup economy (§15.4)
# ---------------------------------------------------------------------------

CHALLENGE_RETRY_SECONDS = 2.5
"""Cooldown after a wrong unlock answer.

Long enough that guessing costs real tapping time, short enough that a genuine
slip isn't a death sentence.
"""

INVENTORY_SLOTS = 2
FIRST_DROP_AT_S = 8.0
DROP_INTERVAL_MIN_S = 10.0
DROP_INTERVAL_MAX_S = 16.0
"""Roughly 4–5 items per player per 60s race.

Tuned against the unlock gate: an item only reaches the track if its owner solves
a challenge, so raw drop rate over-states real throughput. Faster than this and a
horse with three backers accumulates enough item power to win outright; slower and
backing a popular option stops mattering at all.
"""
DROP_FINAL_RAMP_FROM = 0.75
"""Race-progress fraction after which drops accelerate."""
DROP_FINAL_RAMP_MULTIPLIER = 0.66
BACKER_DROP_BONUS = 0.25
"""Extra drop rate per additional backer (sub-linear, see engine docstring)."""

DROP_LEADER_TAX_MULTIPLIER = 1.3
DROP_LAST_PLACE_MULTIPLIER = 0.75

RARITY_WEIGHTS = {"common": 60, "uncommon": 27, "rare": 10, "epic": 3}
LAST_PLACE_RARE_BONUS = 5
"""Flat weight added to rare+epic while your horse runs last (§9.3 charity)."""
PITY_COMMON_STREAK = 4
"""After this many consecutive commons, the next grant is uncommon or better."""

# ---------------------------------------------------------------------------
# Powerup guard rails (§9.0)
# ---------------------------------------------------------------------------

FINAL_STRETCH_FRACTION = 0.92
"""Beyond this track fraction, freeze-class effects soften instead of stopping."""
SWAP_LOCKOUT_FRACTION = 0.95
FREEZE_SOFTENED_MAGNITUDE = -0.20
FREEZE_SOFTENED_DURATION_S = 2.0
MERCY_RULE_DURATION_MULTIPLIER = 0.5
"""Hostile effects on the last-place horse last half as long."""

GLOBAL_POWERUP_COOLDOWNS_S = {"reply_all_storm": 6.0}

MAX_LIVE_TRAPS = 3
TRAP_LIFETIME_S = 15.0
BANANA_PLACEMENT_OFFSET = -10.0
TRAP_FORWARD_LEAD = 16.0
"""How far *in front* of the targeted horse a thrown trap lands.

Traps are thrown forward, at the horses ahead. Dropping them behind only ever
helps whoever is already winning; throwing them forward is what lets a horse in
the pack claw back ground, which is the whole point of carrying one.
"""

TRAP_SELF_CLEARANCE = 6.0
"""Gap kept between a dropped trap's hit box and the horse that dropped it."""

BANANA_CATCH_RADIUS = 9.0
"""Half-width of a peel's hit box. Wide enough to connect at 20 Hz sampling."""

# ---------------------------------------------------------------------------
# Random events (§15.6)
# ---------------------------------------------------------------------------

EVENT_FIRST_AT_S = 12.0
EVENT_INTERVAL_MIN_S = 12.0
EVENT_INTERVAL_MAX_S = 20.0
EVENT_TELEGRAPH_SECONDS = 1.5
EVENT_LAST_CALL_FRACTION = 0.90
"""No global event may fire once the leader is past this track fraction."""

# ---------------------------------------------------------------------------
# Finish resolution (§15.8)
# ---------------------------------------------------------------------------

PHOTO_FINISH_WINDOW_S = 0.12
"""Projected crossing-time gap between 1st and 2nd that triggers the replay."""

RACE_WRAPUP_SECONDS = 3.5
"""Grace period after the winner crosses before stragglers are ranked as-is."""

RACE_HARD_TIMEOUT_MULTIPLIER = 3.0
"""Safety valve: never simulate longer than duration × this."""

INSIDER_LEAK_LEAD_SECONDS = 3.0
"""How far ahead of an event Insider Trading whispers to its owner."""

# ---------------------------------------------------------------------------
# Networking (§7)
# ---------------------------------------------------------------------------

SNAPSHOT_RATE = TICK_RATE
PLAYER_HUD_RATE = 2
"""Frequency of the per-player `you` message (phone HUD)."""
CLIENT_INTERPOLATION_DELAY_MS = 150
REACTION_MIN_INTERVAL_S = 1.0
TAP_BATCH_INTERVAL_MS = 100
MAX_TAPS_PER_MESSAGE = 40
"""Server-side sanity clamp on a single batched tap message."""

ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_CODE_LENGTH = 4
ROOM_IDLE_TIMEOUT_S = 60 * 60 * 6

# ---------------------------------------------------------------------------
# Betting mode (§11.5)
# ---------------------------------------------------------------------------

BETTING_WINDOW_SECONDS = 20.0
BETTING_STARTING_BANKROLL = 1000
BETTING_MIN_BET = 50
BETTING_TAKEOUT = 0.0
"""Pari-mutuel house cut. Zero: the house is honest."""

# ---------------------------------------------------------------------------
# Elimination mode (§11.3)
# ---------------------------------------------------------------------------

ELIMINATION_INTERVAL_S = 12.0

# ---------------------------------------------------------------------------
# Presentation palette (§13.2) — mirrored in static/shared/theme.css
# ---------------------------------------------------------------------------

HORSE_COLORS = [
    "#FF5D5D",  # racing red
    "#4EA8FF",  # sky
    "#3EDC81",  # go green
    "#FFC53D",  # hero yellow
    "#C77DFF",  # orchid
    "#FF8F3F",  # tangerine
    "#2EE6D6",  # teal
    "#FF6FB5",  # bubblegum
    "#9BE15D",  # lime
    "#7C8CFF",  # periwinkle
    "#E8E1CF",  # oat
    "#FF4D9D",  # magenta
]

JOCKEY_EMOJI = ["🤠", "🧑‍🚀", "🧙", "🥷", "🤖", "👨‍🍳", "🧝", "🦸", "🕵️", "🧛", "👺", "🐵"]

FOOD_EMOJI_HINTS: dict[str, str] = {
    "chipotle": "🌯",
    "burrito": "🌯",
    "taco": "🌮",
    "taco bell": "🌮",
    "panda": "🥡",
    "chinese": "🥡",
    "thai": "🍜",
    "ramen": "🍜",
    "noodle": "🍜",
    "pho": "🍜",
    "sushi": "🍣",
    "japanese": "🍣",
    "poke": "🍥",
    "pizza": "🍕",
    "italian": "🍝",
    "pasta": "🍝",
    "burger": "🍔",
    "five guys": "🍔",
    "shake shack": "🍔",
    "in-n-out": "🍔",
    "wing": "🍗",
    "chicken": "🍗",
    "chick-fil-a": "🍗",
    "kfc": "🍗",
    "bbq": "🍖",
    "barbecue": "🍖",
    "steak": "🥩",
    "salad": "🥗",
    "greek": "🥙",
    "gyro": "🥙",
    "shawarma": "🥙",
    "falafel": "🧆",
    "indian": "🍛",
    "curry": "🍛",
    "korean": "🍲",
    "hotpot": "🍲",
    "soup": "🥣",
    "sandwich": "🥪",
    "sub": "🥪",
    "deli": "🥪",
    "breakfast": "🥞",
    "pancake": "🥞",
    "brunch": "🍳",
    "bagel": "🥯",
    "donut": "🍩",
    "dessert": "🍰",
    "ice cream": "🍦",
    "seafood": "🦞",
    "shrimp": "🍤",
    "fish": "🐟",
    "hot dog": "🌭",
    "pretzel": "🥨",
    "cheese": "🧀",
    "vegan": "🥦",
    "veg": "🥦",
    "smoothie": "🥤",
    "boba": "🧋",
    "coffee": "☕",
    "beer": "🍺",
    "leftover": "🥫",
}

FALLBACK_FOOD_EMOJI = ["🍽️", "🥘", "🍱", "🍲", "🧆", "🥟", "🌭", "🍟", "🥙", "🍛", "🥡", "🍜"]
