# dl-picker architecture

How the pieces fit, and why they were chosen that way. The governing constraint
from `SPEC.md` §8: *an office wants dinner, and the whole stack must run from one
command on one laptop.* Simplicity beats scale everywhere in this codebase.

---

## 1. Process topology

```
                       ┌────────────────────────────────────┐
   TV browser  ◄──WS──►│  one Python process (uvicorn)      │
                       │                                    │
   Phone       ◄──WS──►│   RoomManager                      │
   Phone       ◄──WS──►│     └─ Room "TACO"                 │
   Phone       ◄──WS──►│          ├─ RaceEngine (20 Hz)     │
                       │          ├─ Participants           │
                       │          └─ broadcast()            │
                       │                                    │
                       │   StaticFiles: /, /play, /static   │
                       └────────────────────────────────────┘
                                     ▲
                          cloudflared quick tunnel
                                     ▲
                              phones on cellular
```

No database, no Redis, no message broker, no second service. Room state lives in
memory; the only thing on disk is `data/session_stats.json`, written after each
race. Losing a room costs ten seconds (make a new one) which is cheaper than
operating a database.

## 2. Threading and concurrency

Everything runs on one asyncio event loop:

- Each room's race is a single `asyncio.Task` (`Room._race_lifecycle`).
- Websocket handlers are coroutines on the same loop.

Because all mutation happens on that loop, **there are no locks anywhere**. The
race loop is a fixed-timestep scheduler: it computes `started_at + tick × 0.05`
and sleeps the remainder, so tick rate doesn't drift with load, and it re-bases
if the host machine stalls for more than a second.

## 3. The simulation

`server/engine.py` is the only place the outcome of a race is decided.

**Hard rule: it performs no I/O, reads no clock, and takes all randomness from
`self.rng` (seeded per race).** Consequences:

- The same `(config, seed, input trace)` always replays the same race.
- Balance can be measured by Monte Carlo (`tests/test_balance.py`) rather than
  argued about.
- Bugs reproduce from a seed.

Race time is *derived* (`tick × dt − countdown`), never accumulated, because
repeated `+= 0.05` drifts enough to miss an exact phase boundary.

### Velocity model

Per horse, per tick:

```
v = BASE × noise × max(0.25, 1 + T + P + E + Z + R)
```

| Term | Meaning | Source |
|---|---|---|
| `BASE` | track length ÷ duration × calibration | `constants.py` |
| `noise` | the horse's *form*, retargeted every 6–10s | `_update_noise` |
| `T` | combined tap bonus, asymptotic | `_tap_bonuses` |
| `P` | powerup effects (clamped as a group) | `effects.sum_effects` |
| `E` | world-event effects (clamped separately) | same |
| `Z` | track zones the horse is standing in | `_zone_multiplier` |
| `R` | catch-up band with a deadzone | `_rubber_band` |

Hard states (freeze, stumble) override the multiplier rather than adding to it.

### The one balance insight worth knowing

A *sustained* speed bonus and zero-mean noise fight each other, and the catch-up
band scales both by the same factor. So the win rate of a tapped horse is set by
the **ratio** `TAP_BONUS_MAX / noise amplitude` — not by the band constants.
Tuning the band alone does nothing; this was measured, not assumed. See the
docstring on `TAP_BONUS_MAX`.

Two related decisions follow from the same measurements:

- **Noise retargets slowly (6–10s).** Fast jitter averages out over a race and
  reads as no variation at all. Slow drift gives horses visible form.
- **Powerup drops are scheduled per *horse*, not per player**, with a sub-linear
  bonus for extra backers. Per-player scheduling multiplied a bandwagon horse's
  item power by its backer count and pushed every other option below its floor.
- **Backing several horses does not multiply your taps.** A player may back up to
  `MAX_BACKED_HORSES` options and gets one tap button per horse, but the honesty
  cap applies to the *person*: `RacePlayer.tap_allocation` measures each horse's
  share of their tapping and divides one capped budget between them. Four buttons
  are a choice about where your support goes, never four times the power.

## 4. The unlock gate

`server/challenges.py`. A powerup lands in an :class:`InventorySlot` **locked**,
with a challenge attached; `use_powerup` trusts nothing but `slot.armed`.

Two shapes, both a few seconds and neither needing a keyboard:

* **Multiple choice** — arithmetic, sequences, odd-one-out, biggest/smallest. The
  answer index stays on the server (`client_meta` deliberately omits it), so the
  gate cannot be skipped by a patched client. A wrong answer starts a cooldown
  and re-rolls the question — *excluding* pace tasks, so a slot never changes
  discipline mid-attempt.
* **Pace** — hold a target tap rate inside a tolerance band for a couple of
  seconds. Judged in `_update_pace_challenges` from the tap stream the server
  already receives, so it needs no new trust and no new messages. Drifting out of
  the band decays progress rather than resetting it, because one stray tap
  shouldn't erase two seconds of careful thumbing.

Challenges come from the engine's seeded RNG, so races still replay exactly.

**Event delivery.** A cast or an answer arrives *between* ticks, and both emit
events. `RaceEngine.drain_events()` exists because `step()` used to return its
queue by reference: anything emitted after the room had finished dispatching that
list vanished on the next tick, which silently swallowed powerup notifications.
Any room method that lets a player change the race must drain afterwards.

Two knock-on effects worth knowing:

* Items can be **much stronger** than an ungated economy would allow, because
  firing one costs tapping time.
* The gate taxes mashers hardest, so `PACE_TARGETS` spans fast rates too. Without
  that, a 12 tap/sec player could never satisfy any band and effort would be
  punished — which the balance suite caught the first time round.

## 5. Breeds

`server/breeds.py` is ten horses plus a party parrot, each a dictionary of render
parameters (proportions, gait, markings, horn, wings) consumed by
`static/display/horses.js`. One procedural rig draws all eleven, so a new breed
costs a dictionary rather than an art pipeline.

Breeds are **cosmetic, and enforced as such**: nothing in `render` reaches the
simulation, and `tests/test_breeds.py` asserts both that the vocabulary of render
keys is closed and that the same seed produces the same race whichever animal is
chosen. Otherwise picking the parrot would stop being a joke and start being a
strategy.

## 6. Effects, zones and traps

`server/effects.py` has one `Effect` type (a timed additive modifier, grouped
into independently-clamped categories) and one `Zone` type that covers mud, oil
slicks, syrup pools, boost pads, banana peels and collectibles. The engine walks
one list per tick and the renderer only has to understand one shape.

All the shared powerup rules live on the engine, not in individual items:

| Rule | Where |
|---|---|
| Shield absorbs one hostile effect | `apply_hostile` |
| Ghost fizzles targeted effects and ignores traps | `apply_hostile`, `_zone_multiplier` |
| Diamond Hands immunity | `apply_hostile` |
| Golden Carrot ignores common debuffs | `apply_hostile` |
| Mercy rule (half duration on last place) | `apply_hostile` |
| Freeze softens in the final stretch | `apply_freeze` |
| Same-id effects refresh instead of stacking | `effects.upsert` |
| At most 3 visible non-protective effects | `effects.prune` |
| Traps are thrown forward, and never hit their thrower | `trap_placement`, `_zone_hits` |

An individual powerup is then a few lines that describe *intent*. New items
inherit every guard rail for free.

## 7. Protocol

JSON over native websockets. Full message tables are in `SPEC.md` §7.2; the
models live in `server/protocol.py` and unknown message types are ignored so a
stale phone never hard-fails.

Design choices that matter:

- **Full snapshots, not deltas.** ~1 KB at 20 Hz for 12 horses. Any single
  snapshot fully describes the visual state, which makes reconnect trivial.
  Deltas would be complexity with no payoff at this scale.
- **Taps are a rate, not events.** Phones batch every 100 ms; the server
  integrates over a 1s window. Tap latency up to ~200 ms is therefore invisible,
  and upstream traffic is capped at 10 msg/s per player no matter how fast
  somebody taps.
- **The server never sends render instructions.** Snapshots carry `fx` tags
  ("boost", "muddy", "shield"); the display decides what those look like.
- **Encode once, fan out.** `Room.broadcast` serialises a frame a single time and
  sends the same string to every socket.

### Reconnect

Every client gets a UUID token in `welcome` and stores it. On reconnect it sends
`hello{token}` and the server restores the same participant, horse and inventory.
Seats are held for the whole session. The display can reload mid-race and resume
from the next snapshot — the simulation never depended on it being connected.

Mid-race joiners become spectators; they may not claim a horse (no bandwagoning
the leader at t=50s). The one exception is Last Bite, where an eliminated
option's backers are released and may re-back mid-race.

## 8. Display rendering

`static/display/` — one Canvas 2D pass per frame:

```
sky → parallax hills → crowd + props → track surface + lanes → zones →
finish line → horses → particles → weather → reactions → vignette → flash
```

- **Interpolation, not extrapolation.** The display renders a fixed 150 ms in the
  past and lerps between the two bracketing snapshots (`interpolate.js`, unit
  tested). Extrapolation overshoots and rubber-bands on every correction.
- **Camera** (`camera.js`, unit tested) follows `0.6 × leader + 0.4 × pack mean`
  on a spring and zooms out up to 25% rather than cropping a horse. Near the line
  it holds *back* so the finish sits at 70% of screen width and horses run into
  it, and a hard rule keeps the last horse in frame no matter what — pushing the
  camera forward instead sweeps the field off the left edge, which the room reads
  as "the race ended".
- **Horses are procedural** (`horses.js`): ~8 shapes, a gallop cycle whose
  frequency tracks actual speed, secondary motion on mane and tail, and eyes that
  look toward the leader. The food emoji rides in the saddle.
- **One particle pool** (620 slots, recycled) serves confetti, dust, sparks,
  money, feathers, mud and emoji bursts, so frame cost is bounded no matter how
  chaotic the race gets.
- **Clutter budget is enforced in code.** `NotificationLane` shows at most two
  cards and queues the rest.

Audio (`audio.js`) is entirely synthesised: three music layers that fade in as
the race tightens, a crowd bed of filtered noise whose gain follows race
excitement, hooves driven by the leader's leg frequency, and one synth recipe per
sound effect. A limiter protects the office TV speakers.

## 9. Rooms, modes and orchestration

`Room` owns the parts of a race night that aren't simulation: roster, config,
betting pools, tournament bracket, and the phase machine.

```
LOBBY → [BETTING] → RACING → [PHOTO_FINISH] → CEREMONY → RESULTS
                                                    └→ BRACKET → RACING …
```

Modes are a frozen dataclass of constant overrides (`modes.py`). Only three need
orchestration — Last Bite (in the engine), Tournament and Betting (in the room) —
and none of them needed a plugin system. Tracks are a theme dictionary plus one
twist object with `on_start`/`on_tick` hooks, instantiated fresh per race.

## 10. The optional tunnel

`server/tunnel.py` spawns `cloudflared`, scrapes the assigned hostname out of its
log stream, and hands it to the app through the `PUBLIC_URL` environment
variable — which `Room` already reads. Keeping it in the environment rather than
in-process means the tunnel sits entirely outside the request path and survives
`uvicorn --reload` (the child inherits the environment).

It only runs behind an explicit `--tunnel` flag, because starting it publishes
the machine to the internet. `main.py` owns the process and stops it in a
`finally`, so quitting the server closes the tunnel.

## 11. Deliberate non-goals

No accounts, no persistence beyond one stats file, no admin panel, no delta
compression, no server-side rendering, no plugin system, no i18n, no config files
beyond `constants.py`. Each of those would add operational surface between "we're
hungry" and "they're off!".

## 12. Where to change things

| I want to… | Go to |
|---|---|
| Retune the game | `server/constants.py`, then run `tests/test_balance.py` |
| Add a powerup | `server/powerups.py` (`_register(...)`) — guard rails are inherited |
| Add an unlock challenge | `server/challenges.py` (`GENERATORS`) |
| Add a breed | `server/breeds.py`, then teach `horses.js` any new render key |
| Add a world event | `server/events.py` (`_register(...)`) |
| Add a track | `server/tracks.py` — a theme dict plus an optional twist class |
| Add a mode | `server/modes.py` — prefer pure constant overrides |
| Write commentary | `server/commentary.py` |
| Change how something looks | `static/display/renderer.js` / `horses.js` |
| Change a sound | `static/display/audio.js` |
| Change the phone | `static/play/` |
