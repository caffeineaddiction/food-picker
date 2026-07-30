# 🏇 dl-picker

**Dinner, decided by horse race.**

Every dinner option becomes a racehorse. The office TV shows the race, everyone's
phone is a controller, and the winning horse decides what you order. Players tap
to make their horse run faster and fire powerups at each other ("Cullen used
Diamond Hands on Sushi"), while loose cows, meteors and Fed rate cuts do their
best to ruin it for everybody.

Built to the specification in [`SPEC.md`](SPEC.md).

---

## Quickstart

```bash
uv sync
```

```bash
uv run main.py
```

Then:

1. Open **http://localhost:8000/** on the TV (a laptop plugged into it is ideal).
2. Click **New Race Night**, type your dinner options — one per line.
3. Pick a mode and a track, or press on through with the defaults.
4. Everyone scans the QR code, types a name, and picks up to **four** horses.
   Spectators welcome.
5. Press **START THE RACE** and stop talking about dinner for sixty seconds.

`Race again` re-runs the same menu in one click — the rematch is where a race
night actually gets good.

### Phones not on the office wifi

One flag does everything — it starts a Cloudflare quick tunnel, reads back its
public hostname, and points the QR code at it:

```bash
uv run main.py --tunnel
```

Needs `cloudflared` on PATH (`brew install cloudflared`). The tunnel is
**opt-in** because it publishes this machine's server to the public internet,
and it is shut down with the server.

If you would rather run the tunnel yourself, either open its `https://…` URL on
the TV (the QR follows the address the display was loaded from), paste that URL
into the **"Phones off this network?"** box under the QR code, or export
`PUBLIC_URL` before starting.

A QR generated while the TV sits on `localhost` encodes `localhost` — which on a
phone means *the phone itself*, so it goes nowhere. Any of the routes above fixes
that. HTTPS also unlocks phone vibration and screen-wake. On the same wifi no
tunnel is needed; the startup banner prints your LAN URL.

Typing the bare tunnel URL on a phone works too: phone-sized screens are sent to
the controller, and a controller with no room code joins the running race.

---

## The game

| | |
|---|---|
| **Players** | 5–10 is the sweet spot; unlimited spectators |
| **Race length** | 60s by default (20–120s) |
| **Options** | 2–12 dinner options per race |
| **Horses per player** | Up to 4, each with its own tap button. Your tap rate is capped per *person*, so four buttons split your influence rather than multiplying it |
| **Breeds** | 10 horses plus a party parrot; the host picks one per dinner option. Purely cosmetic |
| **Influence** | Roughly half player skill, half chaos — a well-backed horse wins ~40% of races, an ignored one still wins ~10% |

### Modes

| Mode | What it changes |
|---|---|
| 🏇 **Classic Derby** | One race, full powerups and events. The benchmark. |
| 🌪️ **Chaos Buffet** | Double items, double events, wilder swings, permanent screen shake. |
| 🪓 **Last Bite** | The track loops and the trailing option is eliminated every 12s. Backers of a dead option pick a survivor and keep tapping — their button turns into a swap. |
| 🏆 **Tournament** | Heats of up to four, then a final with double Epic odds. Bracket screen between rounds. |
| 🎰 **The Punters' Club** | A 20s betting window with live pari-mutuel odds. Spectators get skin in the game; bets never touch the simulation. |
| ⚡ **Lightning Round** | 20 seconds, short track, everyone starts armed. Photo finishes guaranteed-ish. |

### Breeds

Thoroughbred · Mustang · Appaloosa (spotted) · Pinto (patched) · Clydesdale
(enormous, feathered hooves) · Shetland Pony (small, furious) · Unicorn (horn,
glow) · Pegasus (flapping wings) · Zebra (striped) · Shadow Steed (dark, glowing)
· **Party Parrot** (hops instead of galloping, cycles the whole rainbow).

Click a horse in the paddock to change its breed. Breeds are cosmetic — same
seed, same result, whichever animal you pick, and there's a test that proves it.

### Tracks

Churchill Yowns (classic turf) · Neon Circuit (boost pads) · Wall Street
(bull/bear regimes) · Lunar Colony (low gravity) · Candy Canyon (syrup pools and
a sugar cube) · **Party Parrot Paradise** (a hue-cycling rainbow and a beat drop
that surges the whole field) · The Office (meeting pull). Each has one light
gameplay twist that is visible, telegraphed and identical for every lane.

### Powerups

Thirteen items across four rarities — a short list on purpose, because every one
has to be legible on a phone and fit on the countdown primer. Each carries an
icon saying what it does and who it hits:

| | |
|---|---|
| ⬆️ | speeds a horse up |
| ⬇️ | slows one down |
| 🛡️ | protects |
| 🎲 | chaos, affects everything |

Global items are badged **ALL HORSES** so nobody fires one expecting a private
advantage. Traps are thrown **forwards**, landing in front of the horse ahead — a
peel dropped behind you only punishes horses already losing, so throwing it
forward is how the pack takes places off the leaders. You are immune to your own
trap.

### Earning a powerup

Items arrive **locked**. To arm one you clear a quick challenge on your phone —
no typing, a few seconds, always one of:

- **Multiple choice**: mental arithmetic, "what comes next", odd-one-out, or
  biggest/smallest. Three or four big buttons.
- **Hold the pace**: keep a target tap rate (say 5/sec) inside a band for a
  couple of seconds, shown live on the tap button. Mashing overshoots — this one
  is about control, not effort.

A wrong answer costs a short cooldown and a fresh question, so guessing is
expensive. That trade — momentum for firepower — is why the items hit as hard as
they do, and why using one is a decision rather than a reflex. The pace bands
span slow *and* fast rates deliberately: if every band sat below a masher's
natural rate, the gate would quietly punish effort.

The countdown runs 9 seconds and the TV spends it showing the full item list, so
a new player can learn the game before the gates open. Shields block one hostile effect, Ghost Horse phases through
traps, Diamond Hands ignores slows, and freeze-class items soften near the finish
line so no win is ever *stolen* by a stun. Full catalog in `SPEC.md` §9 and
[`server/powerups.py`](server/powerups.py).

---

## Architecture

One Python process serves everything; there is no build step, no database and no
accounts. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for detail.

```
                   ┌──────────────────────────────┐
   TV browser ◄─WS─┤  FastAPI + uvicorn           │
   Phone     ◄─WS─►│    RoomManager → Room        │
   Phone     ◄─WS─►│      └─ RaceEngine @ 20 Hz   │
                   │    StaticFiles (/, /play)    │
                   └──────────────────────────────┘
```

- **Server-authoritative.** Clients send intents (taps, powerup casts); the
  server simulates and broadcasts full snapshots 20×/second. No client can
  compute the outcome, which matters in a competitive office.
- **Deterministic engine.** `RaceEngine` is pure Python with no I/O and a seeded
  RNG, so the same seed and inputs always reproduce a race. That is what makes
  the Monte Carlo balance suite possible.
- **Vanilla frontend.** ES modules and Canvas 2D. No npm, no bundler, no CDN —
  it runs offline on a LAN.
- **Emoji-first art.** Horses are code-drawn vectors; jockeys, crowd, props and
  particles are emoji. Zero assets to ship.
- **Procedural audio.** Music, crowd, hooves and every sting are synthesised in
  Web Audio. No audio files.

Dependencies, in full: `fastapi`, `uvicorn[standard]`, `segno`.

---

## Development

```bash
uv run pytest
```

```bash
node --test tests/js/frontend.test.mjs
```

| Suite | Covers |
|---|---|
| `tests/test_engine.py` | Determinism, velocity model, finish resolution, elimination |
| `tests/test_powerups.py` | Shields, ghosting, immunity, lockouts, stacking, drop economy |
| `tests/test_balance.py` | Monte Carlo win-rate envelopes (the fun guard rail) |
| `tests/test_rooms.py` | Rosters, horse locking, pari-mutuel payouts, tournament brackets |
| `tests/test_network.py` | Real ASGI protocol: joining, host authority, reconnect, snapshot stream |
| `tests/test_assets.py` | Every asset URL and DOM id actually resolves when served |
| `tests/test_challenges.py` | Unlock gate: arming, cooldowns, the pace task, fairness to every style |
| `tests/test_engine.py` | …including that events from between-tick intents are never dropped |
| `tests/test_breeds.py` | Breeds stay cosmetic and never touch the simulation |
| `tests/test_tunnel.py` | Tunnel URL scraping, failure paths and teardown (stub binary) |
| `tests/js/frontend.test.mjs` | Snapshot interpolation, camera framing, particle pool, horse rig, motion |

**Every gameplay number lives in [`server/constants.py`](server/constants.py).**
Change one and re-run `tests/test_balance.py` — it enforces the outcome
distribution, not just that the code runs. The two constants that matter most are
documented in place: `TAP_BONUS_MAX` and `NOISE_MAX` are tied together by a
ratio, and that ratio *is* the game's balance.

```bash
uv run main.py --port 9000 --reload
```

Display keyboard shortcuts: `f` fullscreen, `m` mute. The display honours the
browser's reduce-motion setting.

---

## Layout

```
main.py                 entry point; prints URLs and the tunnel hint
server/
  app.py                FastAPI routes, websocket endpoint, QR
  rooms.py              rooms, connections, race orchestration, betting, bracket
  engine.py             the simulation (pure, deterministic, 20 Hz)
  powerups.py           26-item catalog + drop economy
  events.py             15 world events
  tracks.py             6 tracks: themes + gameplay twists
  modes.py              6 modes as constant overrides
  breeds.py             10 horses + the party parrot (cosmetic only)
  challenges.py         unlock challenges: maths, patterns, tap-pace
  commentary.py         commentary lines + trigger rules
  effects.py            speed modifiers, zones, traps
  state.py              horses, players, config, engine events
  protocol.py           websocket message models
  constants.py          every tunable number
  roster.py             options → horses (emoji assignment)
  stats.py              session stats JSON
  tunnel.py             optional cloudflared quick tunnel
static/
  display/              the TV: canvas renderer, HUD, ceremony, audio
  play/                 the phone controller
  shared/               theme tokens, reconnecting socket, motion helpers
```

Session history is written to `data/session_stats.json`. Delete it to reset the
office league.
