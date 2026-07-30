# dl-picker — Game Design & Engineering Specification

**Project:** `food-picker` (game title: **dl-picker**)
**Document status:** Implementation-ready. This is the handoff document for the coding agent.
**Target stack:** Python 3.12 backend managed with `uv`, zero-build-step browser frontend.
**Do not deviate from the numeric constants in §15 without re-running the balance tests in §18.6.**

---

## 1. Executive Summary

dl-picker turns the nightly "what do we order?" argument into a 60-second televised horse race. Each dinner option (Chipotle, Sushi, Pizza…) becomes a named racehorse. The office TV shows the race; everyone's phone becomes a controller. Players back a horse, spam-tap to speed it up, and fire powerups at rivals ("Cullen used Diamond Hands on Sushi"). The horse that crosses the line first decides dinner.

The design target is **Mario Kart energy in a Jackbox delivery model**: one hero screen, phones as controllers, join by QR code, no installs, no accounts. Player skill (tapping, powerup timing) and chaos (random events, rubber-banding, item luck) are balanced roughly 50/50 so that the race is winnable but never safe — the leader can always be rug-pulled at the line.

Key engineering decisions, made for maximum simplicity:

- **Single Python process.** FastAPI + WebSockets, in-memory state, server-authoritative simulation at 20 Hz. No database, no auth, no Redis, no message broker.
- **Frontend is plain HTML/JS/Canvas** served as static files by the same process. No bundler, no npm, no framework.
- **Emoji-first art direction.** Horses are code-drawn vector shapes; jockeys, props, particles, and crowd are emoji. Zero asset pipeline, instantly funny, readable from across a room.
- **Cloudflare Quick Tunnel** for phones on cellular/other networks: one command, one public URL, QR code generated from the request host so it "just works".

Estimated implementation effort: a focused agent can reach a playable race (M0–M3, §17) quickly; the full experience including powerups, events, tracks, modes, and polish is milestones M4–M8.

---

## 2. Gameplay Vision & Design Pillars

### 2.1 The fantasy

You are a degenerate racehorse owner whose horse happens to be a burrito. For 60 seconds, dinner is decided by a screaming photo finish, not a spreadsheet. The room should sound like the last furlong at the Kentucky Derby.

### 2.2 Design pillars (tie-breakers for every decision)

1. **The TV is the show.** Every system must earn its place on the main display. If a feature makes the display busier without making the room louder, cut it.
2. **Laughing > winning.** A hilarious loss ("the loose cow took out Five Guys") must feel better than a quiet win. Randomness is comedy fuel, not noise.
3. **Everyone matters, nobody is safe.** Tapping and powerups visibly move your horse (≈50% influence), but no lead survives on autopilot (≈50% chaos + catch-up).
4. **Ten seconds to fun.** Scan QR → type name → you're in. No menus on the phone, no tutorials. The tap button teaches itself.
5. **Simplicity is a feature.** One process, one command to run, one URL to share. Anything that adds operational surface (DB, build step, second service) must justify itself against "an office wants dinner in 5 minutes."

### 2.3 The influence budget

Target outcome distribution over many races with 6 horses where one horse has 3 active tappers and the rest have zero:

- Backed horse wins ~35–45% of races (feels strong, worth the thumb cramps).
- Any single untapped horse still wins ~8–13% (every option always has a chance).

This is the tuning north star. §15 gives the math; §18.6 gives the headless Monte Carlo test that enforces it.

---

## 3. Core Gameplay Loop

### 3.1 Session flow (one evening)

```
Host opens display page on TV ──► creates room, enters dinner options
        │
        ▼
QR code fills the screen ──► players + spectators scan, pick a name & horse
        │
        ▼
Host picks mode / track / duration ──► presses START
        │
        ▼
3-2-1 countdown ──► THE RACE (≈60s): tapping, powerups, events, commentary
        │
        ▼
Photo finish check ──► winner ceremony (confetti, podium, "DINNER IS: SUSHI")
        │
        ▼
Results & session stats ──► host taps "Race Again" (options persist) or edits menu
```

### 3.2 The race minute (moment-to-moment)

- **0–3 s — Countdown.** Gates, crowd hush, marching-band sting. Phones show a pulsing "GET READY" over the tap button.
- **3–15 s — The break.** Field spreads out from noise + early taps. First powerups drop around t≈8s. Commentary establishes narrative ("Pizza breaks fast from the gate!").
- **15–45 s — The middle war.** Powerups fly, 1–2 global random events land, rubber-band keeps the pack camera-tight. This is the comedy core.
- **45–55 s — The squeeze.** Powerup drop rate ramps ×1.5, rubber band strengthens, commentary escalates, music tempo rises.
- **55 s–finish — The line.** If top two are within photo-finish distance, slow-mo zoom + freeze-frame + "PHOTO FINISH" reveal. Otherwise clean win with confetti cannon.
- **Post — Ceremony.** Winner horse trots to podium, loser reactions, "DINNER IS: X" banner, per-player stats (taps, powerups used, damage dealt).

### 3.3 Player loop (phone, ~2-second cycle)

Tap-tap-tap → watch TV → powerup slot lights up → decide: boost me now, or hold to snipe the leader at 50s? → fire → see your name on the TV notification → back to tapping. The phone never demands sustained attention; the TV owns eyes, the phone owns thumbs.

---

## 4. Player Experience

### 4.1 Roles

| Role | How they join | Powers |
|---|---|---|
| **Host** | Opens `/` on the TV machine; holds host token | Enter options, configure race, start/restart, kick players, pause, skip ceremony |
| **Player** | Scans QR → `/play` → name → picks a horse | Tap, hold/fire powerups, emoji reactions |
| **Spectator** | Same QR → "Just watching" | Emoji reactions; in Betting mode: place bets |

Multiple players may back the same horse (taps combine, §15.3). A horse with zero backers still runs at baseline — every dinner option always finishes.

### 4.2 Host journey

1. `uv run main.py` → console prints local URL + (if tunnel detected) public URL.
2. TV browser at `/`: big title screen → "New Race Night".
3. Options entry: one text box, one option per line, 2–12 options. Each gets an auto-assigned emoji + color (host can cycle emoji by clicking). Duplicate names rejected inline.
4. Settings drawer (all defaulted, host can ignore): mode (Classic), track (random), duration (60s), powerups (on), events (on).
5. Lobby screen: giant QR left, roster right (name chips fly in as people join, with a pop sound). Start button enables at ≥1 player.
6. During race: host UI hides; a thin hover-reveal bar offers Pause / Abort.
7. After: "Race Again" (same options, new track suggestion) is one tap. Re-rolls should take <10 seconds — losers always demand a rematch, and rematches are where the night's fun compounds.

### 4.3 Player journey

1. Scan QR (camera app, no install). Lands on `/play?room=XXXX`.
2. One screen: name field (pre-filled from localStorage on return visits), horse picker as a horizontal card row showing each horse's emoji, name, and current backer count. "Just watching" link at the bottom.
3. Tap a horse → in. Phone shows lobby card ("You back 🌯 CHIPOTLE — 2 other backers") until start.
4. Race: giant tap zone. Everything else is glanceable, not interactive, except the two powerup slots.
5. Post-race: personal stat card ("You tapped 312 times — office record!") + "Ready for rematch" toggle so the host can see who's still in.

### 4.4 Emotional beats to engineer deliberately

- **Recognition:** your username on the TV when your powerup fires. Non-negotiable; it's the #1 social hook.
- **Injustice comedy:** events that rob the leader must be theatrical (loose cow, rug pull) so the room laughs *at* the victim.
- **Redemption:** trailing-horse mechanics (Dead Cat Bounce, rubber band, Second Wind event) create comeback stories worth retelling.
- **Shared blame:** dinner was decided by everyone's chaos, so nobody resents the outcome — this is the actual product requirement.

---

## 5. UI/UX Specification

### 5.1 Main display (the hero) — layout

16:9, designed for 1080p TV at 3–5 m viewing distance. Minimum text size ≈ 24 px at 1080p for anything that must be readable mid-race.

```
┌──────────────────────────────────────────────────────────────┐
│ TOP BAR (8%): race clock ─ mode chip ─ minimap progress bar  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│ TRACK VIEWPORT (~70%)                                        │
│   parallax sky/backdrop layers                               │
│   horizontal lanes, one per horse, side-scrolling camera     │
│   each horse: body + emoji jockey + name plate + backer pips │
│   powerup/event VFX live here                                │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ NOTIFICATION LANE (7%): "Cullen used Diamond Hands on Sushi" │
├──────────────────────────────────────────────────────────────┤
│ COMMENTARY TICKER (7%): scrolling one-liners                 │
├──────────────────────────────────────────────────────────────┤
│ LEADERBOARD RAIL (8%): ordered chips 1st→last, live reorder  │
└──────────────────────────────────────────────────────────────┘
```

- **Minimap** (top bar): a thin full-race progress strip with a dot per horse — answers "how far to go?" at a glance without cluttering the viewport.
- **Name plates:** horse name in a high-contrast pill above the horse; backer count as small avatar pips (▲▲▲) under it. Plates never overlap: lanes are fixed vertical slots.
- **Leaderboard rail:** chips animate position swaps with a 300 ms slide + a tiny screen-shake when 1st place changes.
- **Notification lane** holds max 2 concurrent notifications; excess queue. Each: 2.5 s in-hold-out (slide + spring). Powerup notifications use the caster's name, item icon, and target name — exactly the "Cullen used Diamond Hands on Sushi" format.
- **Clutter budget:** at any instant the viewport may contain at most 1 global-event banner + 2 notifications + ambient VFX. Anything more queues. Enforce in code (a `DisplayBudget` queue), not by convention.

### 5.2 Main display — camera

- Side-scrolling camera; X follows `0.6 × leader + 0.4 × pack centroid`, critically damped spring (no jitter). The rubber-band math (§15.5) keeps the pack within one viewport ~95% of the time by design.
- If the field still exceeds viewport width, zoom out up to 25% (scale transform) rather than cutting horses off.
- **Final 12% of track:** camera locks so the finish line is at 70% of screen width and horses run into it — the classic broadcast finish shot.
- **Photo finish:** on trigger (§15.8), 0.9 s slow-mo (sim continues, render interpolation stretched), zoom to the two noses, freeze-frame with a white flash + film-grain overlay, 1.5 s of fake deliberation ("REVIEWING…"), then winner reveal. This is the single highest-value polish moment in the game.

### 5.3 Phone UI — layout

Portrait, thumb-first. No scrolling, ever. `touch-action: manipulation` everywhere; disable double-tap zoom and pull-to-refresh.

```
┌───────────────────────┐
│ HORSE CARD (18%)      │  emoji, horse name, live position ("3rd of 6"),
│                       │  connection dot (green/amber/red)
├───────────────────────┤
│ POWERUP SLOTS (14%)   │  two big square buttons; filled = icon + name,
│                       │  empty = dimmed outline; cooldown radial wipe
├───────────────────────┤
│                       │
│ TAP ZONE (55%)        │  one giant button: "TAP!"
│                       │  ripple per tap, combo meter ring fills with
│                       │  sustained tapping, subtle haptic (vibrate 8ms)
├───────────────────────┤
│ STATS STRIP (13%)     │  your taps · taps/sec · emoji reaction row
└───────────────────────┘
```

- **Tap feedback loop:** every tap = ripple + counter increment + `navigator.vibrate(8)` (Android; iOS silently ignores — acceptable). At ≥8 taps/s the tap button glows and shows "MAX POWER" — this communicates the tap cap (§15.3) honestly so players don't shred their screens for nothing.
- **Powerup fire flow:** tap slot → if targeted item, horse picker sheet slides up (big buttons, rival horses only where applicable) → fire → slot empties with a satisfying "shhk" + the TV notification is the real reward. Untargeted items fire immediately on one tap.
- **Emoji reactions:** 5 fixed emoji (😂 🔥 😱 🍕 💀). Tapping one floats it up the TV screen edges as crowd particles. Rate-limited to 1/s per person. Cheap, hilarious, makes spectators feel present.
- **Position changes:** phone card flashes green/red on gain/loss so players feel the race without watching the phone.

### 5.4 Screens inventory

| Surface | Screens |
|---|---|
| Display | Title → Options entry → Lobby (QR) → Countdown → Race → Photo finish → Ceremony → Results |
| Phone | Join (name+horse) → Lobby card → Race controller → Personal results |
| Both | "Connection lost — reconnecting…" overlay (auto-retry, §7.4) |

### 5.5 Accessibility & readability

- All text on solid or heavily-scrimmed backgrounds; WCAG-AA contrast on both surfaces.
- Horse identity is never color-only: emoji + name + lane position are redundant identifiers (color-blind safe).
- Display works muted (offices!): every audio cue has a visual twin.
- Phone works one-handed; nothing interactive in the top 20% of the screen.

---

## 6. Multiplayer Architecture

### 6.1 Topology

```
                     ┌─────────────────────────────┐
                     │  Python process (FastAPI)   │
                     │                             │
  TV browser ◄─WS──► │  RoomManager                │
                     │   └─ Room "TACO"            │
  Phone ◄────WS────► │       ├─ RaceEngine (20Hz)  │
  Phone ◄────WS────► │       ├─ Players/Spectators │
  Phone ◄────WS────► │       └─ Broadcaster        │
                     │                             │
                     │  StaticFiles (/, /play)     │
                     └─────────────────────────────┘
                                 ▲
                     cloudflared quick tunnel
                                 ▲
                        phones on cellular
```

- **Server-authoritative everything.** Clients send *intents* (taps, powerup use); the server simulates and broadcasts state. No client ever computes race outcome. This kills cheating (competitive office!) and desync in one move.
- **One `asyncio` task per room** runs the fixed-timestep loop. 5–10 players at 20 Hz is trivially cheap; a laptop hosts it.
- **Rooms:** 4-letter code (unambiguous alphabet, no 0/O/1/I). Supports multiple concurrent rooms for free, though the office needs one.
- **State is in-memory only.** A crash loses the lobby; recovery is "make a new room" (10 seconds). A database is not worth its weight here. Session-night stats (§19) live in a single JSON file written post-race — still no DB.

### 6.2 Authoritative game state (single source of truth)

```python
Room:
  code: str
  phase: LOBBY | COUNTDOWN | RUNNING | PHOTO_FINISH | CEREMONY | RESULTS
  config: RaceConfig(mode, track, duration_s, powerups_on, events_on)
  horses: [Horse(id, name, emoji, color, pos, speed_mult_state, effects[])]
  players: {token: Player(name, horse_id|None, taps_total, inventory[2], connected)}
  race: RaceState(t, seed, finish_order[], pending_events[], commentary_state)
```

Determinism: the race RNG is seeded per race (`seed = race counter`), and all randomness flows through it — this makes headless balance tests (§18.6) and bug reproduction possible.

### 6.3 Host authority

The display client that created the room receives a `host_token`. Host-only messages (config, start, kick, abort) are validated against it. If the TV tab reloads, it re-attaches using the token from localStorage and the room resumes rendering from the next snapshot — the sim never depended on the display being connected.

---

## 7. Networking Design

### 7.1 Transport

Native WebSockets (FastAPI/Starlette built-in), JSON text frames. At our scale (≤ ~40 clients, 20 snapshots/s, snapshots ≈ 1–2 KB) this is far below any performance threshold; binary framing/MessagePack is complexity with no payoff. Snapshot JSON keys are kept short (`p`, `v`, `fx`) since they dominate bandwidth.

### 7.2 Message protocol

All messages: `{"t": "<type>", ...fields}`. Unknown types are ignored (forward compatibility).

**Client → Server**

| type | sender | payload | notes |
|---|---|---|---|
| `hello` | all | `{token?, room, role}` | token from localStorage → reconnect path |
| `join` | player | `{name, horse_id | null}` | null horse = spectator |
| `tap` | player | `{n}` | batched count since last send (§7.3) |
| `use_powerup` | player | `{slot, target_horse_id?}` | server validates inventory + target |
| `react` | any | `{emoji}` | rate-limited server-side 1/s |
| `host_config` | host | `{options?, mode?, track?, duration?, ...}` | lobby only |
| `host_start` / `host_restart` / `host_abort` / `host_kick` | host | | validated vs host_token |
| `bet` | any | `{horse_id, amount}` | Betting mode, during betting window |
| `ping` | all | `{ts}` | keepalive; server echoes `pong` |

**Server → Client**

| type | audience | payload | cadence |
|---|---|---|---|
| `welcome` | joiner | `{token, room_state}` | once |
| `room_state` | all | full lobby/config/roster | on any lobby change |
| `snapshot` | all | `{t, horses:[{id,p,fx[]}], order[]}` | 20 Hz while RUNNING |
| `event` | all | `{event_id, kind, params, headline}` | as fired |
| `notify` | all | `{text_parts:{player,item,target}}` | powerup casts etc. |
| `grant` | one player | `{slot, powerup_id}` | on item drop |
| `phase` | all | `{phase, data}` | countdown/photo-finish/ceremony transitions |
| `result` | all | `{winner, finish_order, stats}` | once |
| `you` | one player | `{horse_pos_rank, taps, tps}` | 2 Hz (phone HUD) |

The full snapshot every frame (not deltas) keeps clients stateless-ish and makes reconnect trivial: any single snapshot fully describes the visual state. At 6–12 horses this is ~1 KB — deltas would be over-engineering.

### 7.3 Tap ingestion & latency tolerance

- Phone counts taps locally and sends `{"t":"tap","n":k}` every **100 ms** (or immediately at n≥10). This caps upstream rate at 10 msg/s/player regardless of tap speed, and makes individual-tap latency irrelevant: taps are a *rate*, not discrete events.
- Server credits taps into the current tick window. Up to ~200 ms of tap latency is completely invisible in gameplay because tap effect is integrated over a 1 s smoothing window (§15.3).
- Race-critical timing (powerup at the finish line) tolerates ~150 ms comfortably; the server timestamps on receipt — no client clock trust, no clock sync protocol needed.
- Display renders **150 ms behind** latest snapshot with linear interpolation between the two most recent snapshots → perfectly smooth motion at 20 Hz updates even with jitter.

### 7.4 Disconnect / reconnect

- Every client gets a UUID `token` in `welcome`; stored in localStorage.
- WS drop → client shows "reconnecting" overlay and retries with exponential backoff (0.5 s→4 s, forever). On reconnect it sends `hello{token}` and the server restores identity: same player, same horse, same inventory.
- Server keeps disconnected players' seats for **the entire session** (state is tiny); their horse simply stops receiving their taps while away.
- Phone screen-lock kills the socket on some devices: a `visibilitychange` handler reconnects instantly on wake. Also request `navigator.wakeLock` during RUNNING to prevent sleep (best-effort).
- Display reload mid-race: reconnects, receives current `phase` + next snapshot, resumes rendering seamlessly.

### 7.5 Mid-race joining

- Scanning the QR mid-race lands in **spectator mode immediately** (they see a live mirror of the leaderboard + emoji reactions) with a "You'll ride next race" banner. They pre-pick a name so the rematch starts instantly.
- Players may **not** attach to a horse mid-race (prevents bandwagon-joining the leader at t=50s). Exception: Betting mode's betting window.

### 7.6 Deployment & reachability (phones not on office Wi-Fi)

Recommended: **Cloudflare Quick Tunnel** — zero account, zero config:

```
uv run main.py                                   # serves on :8000
cloudflared tunnel --url http://localhost:8000    # prints https://<random>.trycloudflare.com
```

- The app builds the QR from the **request's Host header** (`X-Forwarded-Host` aware), so whatever URL the TV loaded is what phones get. No PUBLIC_URL config needed; works identically on LAN, tunnel, or a named tunnel.
- Optional `PUBLIC_URL` env var overrides detection for edge cases.
- Quick-tunnel latency adds ~30–80 ms round trip — well inside our tolerance (§7.3).
- Fallback for LAN-only nights: QR encodes `http://<lan-ip>:8000/play?room=XXXX` (server can print both).

---

## 8. Technology Recommendations

Optimization target: **an office wants dinner; the whole stack must run from one command on one laptop.** Every choice below is the simplest thing that delivers the §2 pillars.

| Layer | Choice | Why it's the simplest good option |
|---|---|---|
| Language/runtime | **Python 3.12** | Mandated; `uv` manages venv + deps + run |
| Web framework | **FastAPI + uvicorn** | WS + static files + one process; huge documentation surface for the coding agent; pydantic included for message validation |
| Realtime | **Raw WebSockets (Starlette)** | No socket.io dependency mismatch hell; our protocol is 15 message types, a library adds nothing |
| Game loop | **`asyncio` fixed-timestep task, 20 Hz** | One coroutine per room; no threads, no locks (all mutation on the event loop) |
| Frontend | **Vanilla JS (ES modules) + Canvas 2D** | Zero build step, zero node_modules; Canvas 2D easily draws 12 horses + particles at 60 fps; the agent can't get stuck in tooling |
| Art | **Code-drawn vectors + system emoji** | No asset pipeline, no licensing, instantly funny, crisp at any resolution |
| Audio | **Web Audio API, fully procedural** (§14) | No audio files to source/license/ship; a 300-line synth module covers everything |
| QR | **`segno`** (pure-python, zero deps) → SVG inline | One function call server-side; no JS lib to vendor |
| Database | **None.** JSON file for session stats | Nothing here needs durability beyond one evening |
| Auth | **None.** Random tokens + host token | Threat model is coworkers; tokens stop casual impersonation, which is all that's needed |
| Tunnel | **cloudflared quick tunnel** | One command, no account, HTTPS included (required for wake-lock/vibrate APIs) |
| Tests | **pytest** + seeded headless sim | The RaceEngine runs without any I/O → balance and logic are unit-testable |

Python dependencies (complete list): `fastapi`, `uvicorn[standard]`, `segno`. Dev: `pytest`. That's it — this is deliberate; hold this line.

Explicitly rejected: React/Vite (build step for 3 screens), socket.io (version-matching pain), Redis (one process), Postgres/SQLite (no durable data), Phaser/PixiJS (heavier than our needs; Canvas 2D suffices), server-side rendering of race on video (absurd), WebRTC (nothing peer-to-peer here).

---

## 9. Detailed Powerup Catalog

### 9.0 System rules

- **Inventory:** 2 slots per player. Drops arrive automatically (§15.6); full inventory = the roll is skipped (no banking pressure — fire your items!).
- **Rarity tiers & drop weights:** Common 60% · Uncommon 27% · Rare 10% · Epic 3%.
- **Global stacking rules:** same-effect same-target does **not** stack, it refreshes duration; different effects stack additively into `P` (clamped, §15.4). One shield charge blocks one hostile effect. A horse can hold at most 3 active effects; the oldest non-shield effect is culled beyond that (keeps the display readable).
- **Targeting classes:** SELF (own horse), TARGET (choose a rival), LEADER (auto-targets 1st place), GLOBAL (everyone), TRAP (placed on the track at your horse's position ± offset).
- **Anti-pile-on:** hostile effects on a horse in **last place** have half duration ("mercy rule") — kicking the loser is unfunny.
- **The final-10% lockout:** Freeze-class effects (hard stops) cannot be applied within the final 8% of the track (they resolve as a 20% slow instead). A win must never feel *stolen by stun* — slowed at the line is funny; frozen at the line breeds real resentment.
- Every activation fires the display notification: **"{player} used {item} on {target}"**, with item icon and a per-tier flourish (Epic gets a half-second full-screen glint).

### 9.1 Catalog overview

| # | Powerup | Tier | Class | One-liner |
|---|---|---|---|---|
| 1 | Turbo Boost | C | SELF | +40% speed, 3 s |
| 2 | Sugar Rush | C | SELF | +25% speed 5 s, but jittery wobble |
| 3 | Espresso Shot | C | SELF | Your taps count double, 5 s |
| 4 | Shield | C | SELF | Blocks next hostile effect |
| 5 | Banana Peel | C | TRAP | Dropped behind; next horse stumbles 1.2 s |
| 6 | Headwind | C | LEADER | Leader −20% speed, 4 s |
| 7 | Tailwind | C | SELF | +15% speed, 8 s (long & gentle) |
| 8 | Hay Bale | C | TARGET | Target hops the bale: brief stumble + 1 s −25% |
| 9 | Rocket Horseshoes | U | SELF | +70% speed, 2 s, flame trail |
| 10 | Short Seller | U | TARGET | Target −30% speed, 4 s |
| 11 | Oil Slick | U | TRAP | 60-unit slick zone, 25% slow + skid VFX, 10 s life |
| 12 | Ghost Horse | U | SELF | Untargetable & immune to traps, 5 s |
| 13 | Diamond Hands | U | SELF | Immune to all slows/debuffs, 6 s (doesn't block Swap) |
| 14 | Pump & Dump | U | SELF | +60% for 2 s, then −30% for 2 s |
| 15 | Dead Cat Bounce | U | SELF | Weak now; huge if you're last: +15%, or +50% 3 s when in last place |
| 16 | Magnet Draft | U | SELF | Pulled toward the horse ahead (+% scales with gap), 4 s |
| 17 | Reply-All Storm | U | GLOBAL | Every horse except yours −15% for 2 s (they check email) |
| 18 | Freeze (Circuit Breaker) | R | TARGET | "Trading halted": full stop 1.2 s (§9.0 lockout applies) |
| 19 | Fed Rate Cut | R | GLOBAL | All horses +25%, yours +45%, 3 s — chaos accelerant |
| 20 | Insider Trading | R | SELF | Phone shows next global event 3 s early + +10% for 3 s |
| 21 | Market Manipulation | R | SELF | Two random horses (never yours) swap positions |
| 22 | Bull Run | R | SELF | +50% 4 s; horses you pass stumble briefly |
| 23 | Rug Pull | E | LEADER | Leader trips spectacularly: 1 s tumble + 2 s −30% |
| 24 | Swap Places | E | SELF | Instantly swap positions with the horse directly ahead |
| 25 | Photo Finish Lunge | E | SELF | Armed passively; in final 8% auto-fires a 1 s +90% lunge |
| 26 | Golden Carrot | E | SELF | +35% speed AND tap cap raised 50% AND minor debuff immunity, 5 s |

### 9.2 Detail blocks

Format: **Effect · Duration · Visual/Audio · Balance & strategy · Counters · Stacking.**

**1. Turbo Boost 🚀 (Common, SELF)**
Effect: `P += 0.40`. Duration 3 s. Visual: blue speed lines + afterimage; sound: rising whoosh. Balance: the vanilla unit of tempo; ~1.7% of track gained. Strategy: best fired out of a stumble or into the final stretch. Counters: Headwind/Short Seller cancel it arithmetically. Stacking: refreshes itself; stacks with tap bonus.

**2. Sugar Rush 🍭 (Common, SELF)**
Effect: `P += 0.25` for 5 s, horse Y-position wobbles and noise multiplier range widens (±0.04 extra). Visual: candy sparkles, googly-eyed horse expression. Sound: fast xylophone trill. Balance: slightly more total gain than Turbo but variance-added — on-theme comedy. Strategy: mid-race value pick. Counters: none needed. Stacking: refreshes.

**3. Espresso Shot ☕ (Common, SELF)**
Effect: caster's personal tap contribution weight ×2 for 5 s (affects `X` in §15.3). Visual: steam wisps + horse's legs blur. Sound: espresso machine hiss. Balance: only strong for active tappers — rewards the sweaty. Strategy: fire when your thumb is fresh. Counters: indirect only. Stacking: does not stack with itself across supporters — strongest instance applies.

**4. Shield 🛡 (Common, SELF)**
Effect: absorbs the next hostile effect (TARGET/LEADER class) entirely; traps too. One charge. No duration (persists until spent). Visual: soft golden bubble; pops with a glass "ting" when consumed. Balance: the fundamental counter-unit; commonness keeps aggression honest. Strategy: hold when leading late. Counters: GLOBAL effects ignore shields; Market Manipulation ignores shields (it targets positions, not horses). Stacking: max 1 charge; second Shield roll is rerolled at grant time.

**5. Banana Peel 🍌 (Common, TRAP)**
Effect: placed 10 units behind caster; first horse entering within its 15 s lifetime stumbles (speed →0.3× for 1.2 s with tumble animation). Visual: iconic yellow peel, comic slip with legs windmilling; slide-whistle + splat. Balance: skill-shot flavored; often hits mid-pack. Strategy: drop while leading through a rubber-band compression. Counters: Ghost Horse phases it; Shield eats it. Stacking: max 3 live traps per race track-wide (oldest despawns).

**6. Headwind 🌬 (Common, LEADER)**
Effect: current leader `P −= 0.20` for 4 s. Visual: leaning-into-wind pose, flying hat. Sound: gust. Balance: the people's anti-runaway tool; auto-target keeps phone UI one-tap. Strategy: spam-fired by the pack; part of the catch-up economy by design. Counters: Shield, Diamond Hands, Ghost. Stacking: refreshes on same target.

**7. Tailwind 🍃 (Common, SELF)** — Effect: `P += 0.15`, 8 s. Visual: leaves swirl past. Balance: highest total displacement of any Common (1.2 s-equivalent) but low burst — bad for photo finishes, great mid-race. Stacking: refreshes.

**8. Hay Bale 🌾 (Common, TARGET)** — Effect: bale thuds in front of the target: 0.4 s hop animation + `P −= 0.25` for 1 s. Visual: hay explosion on contact. Sound: thump + horse snort. Balance: small guaranteed disruption, teaches targeting UI. Counters: Shield/Ghost/Diamond Hands. Stacking: refreshes.

**9. Rocket Horseshoes 🚀🐴 (Uncommon, SELF)** — Effect: `P += 0.70`, 2 s. Visual: flame cones from all four hooves, horse leans like a dragster; jet roar. Balance: same displacement as Turbo but front-loaded — the photo-finish premium. Strategy: hold for the last 3 seconds. Counters: Freeze erases it. Stacking: refreshes.

**10. Short Seller 📉 (Uncommon, TARGET)** — Effect: target `P −= 0.30`, 4 s; a red candlestick chart crashes above their head. Sound: sad trombone + cash register reversing. Balance: the premium targeted debuff. Strategy: hit the horse *about to pass you*, not the leader (Headwind's job). Counters: Shield, Diamond Hands. Stacking: refreshes; does not stack with Headwind beyond clamp.

**11. Oil Slick 🛢 (Uncommon, TRAP)** — Effect: 60-unit zone at caster's position −15; horses inside get −25% and skid VFX (sparks, wiggle). Life 10 s. Sound: slosh on placement, tires-screech (comically wrong for a horse) on entry. Balance: area denial; can hit multiple horses = highest total value trap. Counters: Ghost phases; rubber-band means victims recover. Stacking: zones don't overlap-stack (max effect −25%).

**12. Ghost Horse 👻 (Uncommon, SELF)** — Effect: 5 s untargetable (TARGET/LEADER effects fizzle with a "whiff" sound) + trap immunity + 40% transparency render. Balance: defense that also grants trap-lane freedom; no speed change keeps it honest. Strategy: pop when you see rivals hoarding items late. Counters: GLOBAL effects still land. Stacking: refreshes.

**13. Diamond Hands 💎🙌 (Uncommon, SELF)** — Effect: 6 s immunity to slows/stumbles/freezes (buffs still work; Swap Places still works — it's positional). Visual: horse crystallizes, sparkling facets; "shing!" sound. Balance: the marquee themed item; pure defense, zero speed. Strategy: cast at t≈50 s when leading — the classic play. Counters: Swap Places, Market Manipulation, outrunning. Stacking: refreshes.

**14. Pump & Dump 📈📉 (Uncommon, SELF)** — Effect: +60% for 2 s then −30% for 2 s (net positive ~0.6 s of Turbo, shaped violently). Visual: green candles then red candles trail. Balance: high skill ceiling — time the dump into a rubber-band recovery or a syrup pool you'd be slow in anyway. Counters: your own greed. Stacking: cannot re-cast during its own cycle.

**15. Dead Cat Bounce 🐈 (Uncommon, SELF)** — Effect: if in last place: +50% for 3 s; else +15% for 3 s. Visual: cartoon cat springs off the horse's saddle. Balance: comeback-flavored; weak insurance if fired rich. Strategy: intentionally sandbagging is negated by the rubber band already helping last place — the bounce is comedy, not exploit. Stacking: refreshes.

**16. Magnet Draft 🧲 (Uncommon, SELF)** — Effect: 4 s; `P += clamp(0.006 × gap_to_next_horse_ahead, 0.05, 0.45)` recomputed per tick (weakens as you close). No effect if you're 1st (converts to +10%). Visual: dotted attraction line to the horse ahead. Balance: self-balancing burst — huge when far behind, mild when close. Counters: target can Ghost (breaks the line — it retargets next horse ahead).

**17. Reply-All Storm 📧 (Uncommon, GLOBAL)** — Effect: every horse except caster's −15% for 2 s; envelope emoji rain over the field, notification-ping barrage sound. Balance: small edge spread wide; ignores shields (global). Office-comedy staple. Stacking: refreshes globally; cannot fire twice within 6 s (global cooldown).

**18. Freeze / Circuit Breaker 🛑 (Rare, TARGET)** — Effect: target halts completely 1.2 s ("TRADING HALTED" stamp over the horse, air-horn + record-scratch). Final-8% lockout converts it to −20% for 2 s (§9.0). Balance: strongest single-target denial; rare tier + lockout keeps it delightful instead of infuriating. Counters: Shield, Diamond Hands, Ghost. Stacking: a frozen horse cannot be re-frozen for 5 s.

**19. Fed Rate Cut 🏦 (Rare, GLOBAL)** — Effect: ALL horses +25%, caster's +45%, 3 s. Confetti of dollar bills; printer "brrr" sound. Balance: net +20% relative edge but accelerates everyone into chaos — often cast purely for the spectacle; that's fine, spectacle is the product. Stacking: refreshes.

**20. Insider Trading 🕵️ (Rare, SELF)** — Effect: caster's phone privately shows the next scheduled global event and its ETA 3 s before it fires ("MUD RAIN in 3s") + `P += 0.10` for 3 s. Visual (display): briefcase glint on the horse — others know *someone* knows. Balance: information asymmetry as a mechanic; mild speed so it's never a dead draw. Strategy: pre-position powerups around the leaked event. Stacking: n/a.

**21. Market Manipulation 🎭 (Rare, SELF)** — Effect: two random horses **excluding the caster's** swap positions instantly (teleport swap with smoke-puff VFX + "poof"). Balance: pure chaos dice — can help or hurt your rivals arbitrarily; excluded-self prevents suicide swaps but keeps it a gamble. Ignores shields (positional). Cannot fire in final 8%. Stacking: 8 s global cooldown.

**22. Bull Run 🐂 (Rare, SELF)** — Effect: +50% for 4 s; each horse you overtake during it stumbles 0.5 s (shoulder-check animation, "olé!" crowd shout). Balance: the aggressive comeback tool — value scales with how far back you are (more horses to pass). Counters: Shield blocks the stumble, not the pass. Stacking: refreshes; stumble-on-pass ICD 1 s per victim.

**23. Rug Pull 🧻 (Epic, LEADER)** — Effect: the literal track rug is yanked from under the leader: 1 s spectacular tumble (flip animation, dust cloud) + −30% for 2 s after. Sound: fabric riiip + crash cymbals. Balance: the nuclear anti-runaway; Epic rarity means ~1 per 2–3 races appears. Mercy rule doesn't apply (leader is never last). Counters: Shield/Diamond Hands/Ghost — a protected leader making an Epic fizzle is itself a great moment. Stacking: leader immune to a second Rug Pull for 8 s.

**24. Swap Places 🔀 (Epic, SELF)** — Effect: instantly swap positions with the horse directly ahead of you (no effect if 1st — rerolled at grant). Teleport-flash VFX both ends. Balance: the single strongest positional item; Epic + adjacency-only (not "swap with leader") keeps it from deciding races from last place. Ignores shields. Locked in final 5% (feel-bad prevention). Stacking: n/a.

**25. Photo Finish Lunge 📸 (Epic, SELF)** — Effect: passive once activated (slot consumed on activation, horse gets a subtle camera-flash aura): when the horse enters the final 8%, auto-fires +90% for 1 s with a dramatic neck-stretch animation. Balance: pre-committed finisher — rivals SEE the aura and can plan (counterplay via Freeze-before-the-lockout-zone or their own lunge). Sound at trigger: camera shutter burst. Stacking: one armed lunge per horse.

**26. Golden Carrot 🥕 (Epic, SELF)** — Effect: 5 s of +35% speed, tap-bonus cap raised from 0.35 to 0.50 for supporters, and Common-tier debuffs are ignored. Visual: horse glows gold, gnawing an enormous carrot. Balance: the "everything" buff — Epic-gated; strongest when the horse has many active tappers (multiplies group effort, on-theme for team play). Stacking: refreshes.

### 9.3 Drop-table integrity rules

- Grant-time rerolls: Shield when already shielded; Swap Places when in 1st; Dead Cat Bounce twice in a row.
- Pity timer: a player who has received only Commons for 4 consecutive grants is guaranteed ≥ Uncommon on the 5th.
- Leader tax: while your horse is in 1st, your personal drop interval is ×1.3 (fewer toys for the front-runner — invisible, effective).
- Last-place charity: while your horse is last, drop interval ×0.75 and +5% rare-tier weight.

---

## 10. Track Catalog

Tracks are theme packages: palette, parallax props, ambient audio bed, crowd flavor, plus **exactly one light gameplay twist** each (twists are visible, telegraphed, and identical-odds for all lanes — flavor pressure, never unfairness). Track hazards respect Ghost Horse immunity.

### 10.1 Churchill Yowns (Classic Derby) — default
- **Look:** white rails, manicured grass, garland of roses at the finish, big straw-hat emoji crowd (🎩👒), golden-hour light, dust motes.
- **Audio bed:** organ riffs, murmuring crowd, distant bugle "Call to Post" during countdown.
- **Twist:** none — this is the control track. Rain/Mud events linger 25% longer here (turf).
- **Why it exists:** the mode you use when you want zero asterisks on the result.

### 10.2 Neon Circuit (Cyber)
- **Look:** black track, magenta/cyan neon rails, gridline horizon, digital billboard crowd of 🤖👾, horses leave RGB tron-trails.
- **Audio bed:** synthwave arps; hooves are slightly vocoded.
- **Twist:** 3 fixed **boost pads** (at 25%/50%/75% of track, visible chevrons): any horse crossing gets +20% for 1.5 s with a laser "zap". Fixed positions = pure common knowledge, zero luck.

### 10.3 Wall Street
- **Look:** running through a canyon of skyline windows, ticker-tape rain, crowd in suits 🤵💼, a giant stock chart in the sky mirrors the live race order.
- **Audio bed:** opening-bell strikes, phone chatter, "buy! sell!" shouts.
- **Twist:** **Market Regime** — alternating bull/bear phases every ~10 s (global +8% / −8%, announced by the sky chart turning green/red + bell). Affects everyone equally; changes *when* boosts matter (fire into bull, defend in bear).
- Trading-themed powerups get +10% drop weight here (flavor coherence).

### 10.4 Lunar Colony
- **Look:** grey regolith, Earth hanging huge in a star field, dome finish line, astronaut crowd 👨‍🚀🛸, dust kicks in slow arcs.
- **Audio bed:** airless quiet — muffled hooves, radio-crackle crowd, beeps.
- **Twist:** **Low gravity** — all stumble/tumble animations are 40% longer (hilarious slow flailing) but their speed penalties are 30% weaker; every ~8 s horses do a long float-hop (pure animation). Comedy net-neutral.

### 10.5 Candy Canyon
- **Look:** licorice rails, gumdrop hills, chocolate-river backdrop, marshmallow crowd 🍬🧁, permanent faint sprinkle-fall.
- **Audio bed:** music-box melody, squishy hoofsteps.
- **Twist:** 2 **syrup pools** (fixed at 35%/65%, −15% while inside, gooey ripple VFX) and 1 **sugar cube** spawning mid-race at a random position — first horse through gets +15% for 2 s (announced with a sparkle chime).

### 10.6 The Office
- **Look:** carpet-tile track down an endless hallway, cubicle parallax, fluorescent lighting flicker, coworker crowd 🧑‍💻☕ leaning out of doorframes, finish line is the elevator.
- **Audio bed:** keyboard clatter, phone rings, HVAC hum; crowd cheers are muffled "wooo"s.
- **Twist:** **Meeting Pull** — every ~15 s a door opens and a random mid-pack horse (never 1st or last) is yanked into a meeting for 0.8 s ("QUICK SYNC" stamp). Short, mid-pack-only, equal odds.
- Office-themed events (§12) get +15% weight here.

### 10.7 Track selection UX
Host picker shows animated postcard thumbnails; "Random" is the default and shows a slot-machine roll on the display at race start (a fun beat in itself). Rematch suggests a different track than last race.

---

## 11. Game Modes

Six shipped modes. Each changes the *shape of the evening*, not just constants.

### 11.1 Classic Derby (default)
One race, ~60 s, full powerups/events. Winner = dinner. Tuned by everything in §15. This is the benchmark experience; every other mode is a knob-set away from it.

### 11.2 Chaos Buffet
For nights when the room wants a circus. Powerup drop interval ×0.5, two events may run concurrently, noise band widened to 0.85–1.15, Epic weight doubled, Reply-All Storm global cooldown removed. Display gets a permanent subtle screen-shake and the commentary uses its unhinged line set. **Influence shifts to ~35% player / 65% chaos — stated on the mode card so nobody's surprised.**

### 11.3 Last Bite (Elimination)
No finish line — the track loops. Every **12 s**, the horse in last place is eliminated with a theatrical exit (trapdoor + "ORDER DISCONTINUED" stamp; the eliminated option's backers are auto-reassigned as free agents who can tap for anyone they like — keeps eliminated players engaged as kingmakers). Last horse standing wins. Race length auto-scales: `12 × (n_horses − 1)` seconds. Best mode for large option lists (8–12); brutal fun for 4–6.

### 11.4 Tournament of Champions
For >6 options or when the office wants a longer show. Options are split into heats of 3–4 (Classic rules, 40 s each); heat winners advance to a final (60 s, Epic drop weight doubled). Bracket graphic between heats. Total ceremony ≈ 5 minutes of entertainment. Players re-pick horses each heat.

### 11.5 The Punters' Club (Betting)
Race is Classic, but before the countdown there's a **20 s betting window**: everyone (players AND spectators) gets a session bankroll of 1000 🥇 and places win-bets. Live odds on the display are computed from bet distribution (pari-mutuel — the house is honest and the display shows where the money is, itself hilarious). Payouts post-race; bankrolls persist across the session so the night builds a gambling leaderboard. Spectators finally have real skin in the game. Note: betting doesn't affect the race sim at all — influence stays clean.

### 11.6 Lightning Round (Photo Finish)
A 20-second knife fight: everyone starts with 1 random powerup, drop interval 5 s, rubber band ×1.5, track length 350 units. Photo-finish probability is deliberately high (§15.8 threshold doubled). Perfect tiebreaker after a disputed race or as a best-of-3 evening format.

### 11.7 Mode framework note (for the agent)
Modes are a `ModeConfig` dataclass overriding Classic's constants + up to 3 hook points (`on_interval`, `on_finish_check`, `pre_race_phase`). Elimination and Betting are the only modes needing hooks; the rest are pure constant overrides. Do not build a plugin system — one file, six configs.

---

## 12. Random Events

### 12.1 System

- Scheduler: first event at t≈12 s, then every 12–20 s (uniform), skewed to avoid the final 6 s (nothing global lands after 90% track — finishes must resolve on player action + momentum).
- Selection: weighted table below; no repeats within one race; track-specific weight bonuses (§10).
- **Telegraph rule:** every event gets a 1.5 s incoming banner ("🌧 RAIN INCOMING") + sound sting before it applies. Insider Trading (§9.20) hooks this pipeline 3 s earlier.
- Events ignore shields (world-scale, not player attacks) but respect the mercy rule where targeted.

### 12.2 Event table

| Event | Wt | Effect (duration) | Display spectacle |
|---|---|---|---|
| **Crowd Wave** | 10 | Random horse +20% (3 s) — "the crowd is behind Sushi!" | Emoji crowd does a synchronized wave toward the horse; roar swell |
| **Rain** | 9 | Everyone −10%, tap effectiveness +20% (8 s) — effort weather | Rain streaks, puddle splashes, umbrella emojis pop over crowd |
| **Mud Patch** | 8 | 80-unit zone spawns ahead of the pack: −20% inside | Brown splatter zone; horses exit visibly filthy (persistent mud decals — the room notices) |
| **Tailwind Gust** | 8 | Trailing half of field +15% (4 s) | Leaves + flying papers blow past left-to-right |
| **Loose Cow** 🐄 | 7 | A cow wanders across the track: the 2–3 horses nearest it stumble 0.8 s | Cow moseys with total indifference, "MOO" caption; the single funniest event — over-animate it |
| **Meteor** ☄️ | 5 | Strikes a random empty patch: horses within 40 units get 0.6 s stumble + screenshake | Fireball, crater with smoke that persists; distant car-alarm sound |
| **Pigeon Flock** 🐦 | 7 | Current leader −15% (3 s) — birds harass the front-runner | Pigeon cloud pecks at leader's jockey; feathers everywhere |
| **Second Wind** | 8 | Last-place horse +35% (3 s) with heroic music sting | Golden glow, slow-zoom on the horse, crowd chant of the horse's name |
| **Horse Gets Excited** | 7 | Random horse +25% (2 s) but wobbles into brief zigzag | Heart emojis, happy whinny, prancing gait |
| **False Finish** | 3 | A fake finish line appears ~10% early, dissolves on contact ("PSYCH!") | Banner + tape that turns out to be a mirage; commentary sells it |
| **Office Manager Interruption** | 5 | ALL horses freeze 1 s: "QUICK ANNOUNCEMENT" over the field; race resumes with +10% global for 2 s (make up time) | A giant 👔 slides in, mumbles (trombone voice), slides out; the pause itself is the joke |
| **Photo Drone** | 6 | No gameplay effect: 2 s cinematic — camera does a sweep of the field | Pure spectacle beat; lets the room breathe; drone emoji with camera flashes |
| **Jockey Swap** | 4 | Two random horses swap *jockey emojis* only — cosmetic chaos, crowd loves it; +5% to both (excitement) | Jockeys leap between horses mid-gallop |
| **Golden Apple** 🍎 | 6 | Spawns 150 units ahead of leader; first horse to reach it: +30% (3 s) | Glinting apple with halo; fanfare on pickup |
| **Earthquake** | 4 | Everyone's Y wobbles, all speeds ±8% randomized per horse (4 s) | Screenshake, cracks in track, crowd emoji tumble |

Weights are relative within the race-appropriate pool. The `Photo Drone` no-op event is intentional pacing design — not every event should change the standings; some should just be television.

### 12.3 Commentary system (text)

A rule-based one-liner engine drives the ticker (and the optional TTS stretch goal). Priority queue with cooldowns; higher priority interrupts ticker scroll.

Trigger classes & sample lines (ship ≥8 lines per class, picked randomly, never repeat within a race):

- **Race start:** "And they're off! Six dinners, one destiny!"
- **Lead change (P1):** "{A} takes the lead — {B} is furious, presumably!"
- **Powerup (P1):** "{player} just used {item} on {target} — HR has been notified!"
- **Event flavor (P2):** "Rain at the derby! The burritos will not be deterred."
- **Idle color (P4, every ~8 s if quiet):** "Sushi's been training on a treadmill. A sushi treadmill."; "Reminder: the loser buys nothing, this isn't about money, it's about pride."
- **Final stretch (P1):** "FINAL FURLONG! Grip your phones!"
- **Photo finish (P0):** "TOO CLOSE TO CALL! Going to the replay booth!"
- **Ceremony:** "{winner} WINS IT! Somebody get this horse a {winner_food}!"

Author ~120 lines total in a `commentary.py` string table; it's an hour of writing that carries half the game's personality.

---

## 13. Animation & Visual Direction

### 13.1 Art direction statement

**"Saturday-morning cartoon broadcast of a prestige sporting event."** Flat, bold, rounded vector shapes; saturated candy palette; every animation over-eager (squash & stretch, anticipation, overshoot). Emoji are first-class citizens — used for jockeys, crowd, props, particles — which sets a comedic register no bespoke art could hit faster, and ships with zero assets.

### 13.2 Typography & palette

- **Display font:** a chunky rounded display face via system stack + one bundled woff2 (recommend *Fredoka* or *Baloo 2*, OFL-licensed, single file vendored into `/static/fonts/`). Numbers/HUD: system-ui bold.
- **Base palette (UI chrome, track themes override the world):**
  - Ink `#1A1B2E` (backgrounds/UI scrims)
  - Cloud `#F8F7F2` (text on dark)
  - Hero Yellow `#FFC53D` (primary accent, buttons, winner)
  - Racing Red `#FF5D5D` (alerts, debuffs)
  - Go Green `#3EDC81` (boosts, positive)
  - Sky `#4EA8FF` (info, water/rain)
- Horse identity colors: 12-slot colorblind-considerate wheel (each horse = color + emoji + name; never color alone).

### 13.3 Horse rig (procedural, code-drawn)

Each horse ≈ 8 shapes: body capsule, head, neck, tail, 4 legs. Animated procedurally:
- **Gallop cycle:** legs as 2 phase-offset sine pairs; period scales with current speed (fast horse = frantic legs — this alone communicates speed better than motion lines).
- **Body bob:** ±3 px sine at 2× leg frequency; tail and mane trail with 1-frame lag (cheap secondary motion).
- **States:** run, boost (body stretches +15%, lean forward, afterimages), stumble (tumble rotation + dust puff + stars ⭐), frozen (ice-cube block 🧊 encases horse), ghost (40% alpha + wavy offset), victory (rear up + head toss), dejected (head droop, walk cycle at 0.3×).
- **Jockey:** the food emoji rides in a little saddle, bouncing on the bob cycle with 50 ms lag; during boost it holds a tiny 🤠 hat that flies off on stumbles. This is the game's mascot moment — spend polish here.
- Eyes: two white circles with pupils that look toward the leader. Googly eyes during Sugar Rush. Do not skip the eyes; they are 90% of the personality per pixel.

### 13.4 World rendering

- 3 parallax layers (sky 0.1×, backdrop 0.4×, trackside 0.8×) + track surface + horses + foreground particles. All drawn to one Canvas 2D at devicePixelRatio; target 60 fps, budget tested with 12 horses + 300 particles.
- **Crowd:** two rows of emoji picked per track theme, each with independent 2-frame bounce at random phase; they surge (amplitude ×2) on lead changes and the finish. Spectator emoji reactions fly up from the crowd line.
- **Weather/event VFX:** rain = 150 streak particles + splash rings; mud = brown zone decal + splatter on horses (persists to ceremony!); meteor = fireball arc + crater decal + screenshake (8 px, 300 ms, damped).
- **Particles:** one pooled particle system (position, velocity, life, drawFn). Confetti, dust, sparks, leaves, feathers, money, sprinkles all reuse it.

### 13.5 Signature moments (polish priority order)

1. **Photo finish sequence** (§5.2) — slow-mo, freeze-frame, film grain, "REVIEWING…", reveal. 
2. **Victory ceremony:** winner trots to a podium against a curtain backdrop, confetti cannons (200 particles from both bottom corners), "DINNER IS: {WINNER}" in 120 px type with a spring-scale entrance, runner-up horses sulk visibly in the background, per-player stat cards fan out.
3. **Countdown:** gate doors slam in per-lane, spotlight sweep, 3-2-1 with bass hits, gates burst open with dust wall.
4. **Powerup casts:** every item has a unique 0.5 s cast flourish on the horse + the notification card. Epic items additionally get a 400 ms full-screen vignette pulse.
5. **Lobby joins:** name chip flies in with a pop + the crowd emoji briefly cheers — joining should already feel like being introduced at the track.

### 13.6 Motion rules

- Everything springs: use a single `spring(current, target, stiffness, damping)` helper for UI positions/scales; no linear tweens for anything the eye tracks.
- Nothing pops in/out without 100–250 ms of transition.
- Screenshake is a scarce resource: reserve for meteor, rug pull, lead change (2 px), finish (6 px).
- Respect a global `reducedMotion` query param that disables shake + slow-mo (someone's motion sickness shouldn't ruin dinner).

---

## 14. Audio Direction

All audio is **procedurally synthesized in Web Audio** (no asset files, no licensing): one `audio.js` module exposing `sfx.play(name)` and layered music. Browser autoplay policy: audio unlocks on the host's first click ("Start Race" counts); phones default to muted with a tiny unmute toggle (the TV is the sound system — phones staying silent is a feature).

### 14.1 Music (display only)

- **Lobby:** laid-back two-chord synth vamp with vinyl crackle, 84 BPM.
- **Race:** driving 8-bit-adjacent loop at 128 BPM built from square-wave bass, arp, and noise hats; a second intensity layer (countermelody + double-time hats) fades in during the final 25%; a third (key change up a whole step) in the final 10%. Implemented as scheduled oscillator patterns — ~150 lines.
- **Photo finish:** music cuts to a held string-pad tremolo. Silence sells tension.
- **Ceremony:** triumphant I-IV-V fanfare (3 s) into a celebratory loop.

### 14.2 SFX inventory (each = one short synth recipe)

Countdown beeps (3 rising sine blips + gate-open noise burst) · hoof loop per horse (filtered noise pulses at leg frequency, mixed down when many horses) · tap ripple (phone-only optional click) · powerup grant chime (2-note bell) · per-tier cast stings (Common: blip; Uncommon: chord; Rare: riser; Epic: riser + boom) · stumble slide-whistle + thud · freeze air-horn + record scratch · crowd bed (looped shaped noise, swells driven by game events) · MOO (honestly, a low square-wave glide is funnier than a real moo) · photo-finish camera shutter burst · confetti pop · sad trombone (pitch-bent triangle) for last place at ceremony.

### 14.3 Commentary audio (ship-decision)

Ship text-ticker only in v1. Web Speech API TTS is a stretch goal (§19) — voice quality varies by machine and a bad robot voice runs *against* the polish bar. The text ticker with good writing is reliably funny.

### 14.4 Mix rules

Crowd bed at −18 dB under music; SFX duck music by 3 dB for 300 ms; never more than 3 concurrent cast stings (queue excess); master limiter node to protect the office TV speakers.

---

## 15. Balancing Recommendations (the actual math)

All constants live in `server/constants.py`. The simulation is deliberately simple enough to reason about on paper.

### 15.1 Core quantities

```
TRACK_LENGTH        = 1000 units
RACE_TARGET_S       = 60 s        (host-configurable 20–120)
BASE_SPEED  B       = TRACK_LENGTH / RACE_TARGET_S  ≈ 16.67 u/s
TICK_RATE           = 20 Hz (dt = 0.05 s)
SNAPSHOT_RATE       = 20 Hz
```

### 15.2 Velocity model (per horse, per tick)

```
v = B × N(t) × max(0.25, 1 + T + P + E + R)        …then clamped ≥ 0
pos += v × dt
```

- `N(t)` — **wander noise**: each horse holds a noise value retargeted every 2–3 s (uniform in [0.92, 1.08]) and lerped toward over 1 s. This creates organic lead trading between untapped horses. Chaos mode widens to [0.85, 1.15].
- `T` — tap bonus (§15.3), range [0, 0.35].
- `P` — sum of active powerup modifiers, clamped to [−0.60, +0.90].
- `E` — event modifier (per event tables), typically ±0.10–0.35.
- `R` — rubber band (§15.5), range [−0.06, +0.10].
- Hard overrides (Freeze, stumble) set a `speed_scale` multiplier (0 or 0.3) outside this sum, with explicit timers.

### 15.3 Tap contribution

```
per-player effective tps  e_i = min(raw_tps_i, 12) × espresso_i(×2)
horse combined            X   = Σ e_i            (all backers)
tap bonus                 T   = 0.35 × (1 − e^(−X / 8))
```

`raw_tps` is measured over a rolling 1 s window (smooths network batching). Properties:

| Backers × tps | X | T |
|---|---|---|
| 1 × 6 (casual) | 6 | +0.185 |
| 1 × 12 (maxed) | 12 | +0.272 |
| 2 × 10 | 20 | +0.321 |
| 3 × 12 | 36 | +0.346 (near cap) |

One sweaty player is worth ~+27%; a whole team approaches but never exceeds +35%. Diminishing returns mean a 3-backer horse beats a 1-backer horse by ~7 points, not 3× — popular options get an edge, not a lock. The 12 tps cap (honestly telegraphed by the phone's "MAX POWER" glow) also removes any incentive for autoclicker-style abuse.

**Influence audit vs the 50/50 goal:** max sustained player-driven edge = T(≈0.27–0.35) + offensive powerup economy; chaos side = noise (±8%), events (±10–35% bursts), item luck, rubber band. Monte Carlo (§18.6) is the enforcement mechanism; the analytical estimate lands at ~52% player / 48% chaos for a lone max tapper — on target.

### 15.4 Powerup economy

```
DROP_INTERVAL   = uniform(7, 13) s per player   (first roll at t=8 s)
FINAL_RAMP      : after 75% race elapsed, interval ×0.66
INVENTORY       = 2 slots (full ⇒ roll skipped)
RARITY WEIGHTS  = C 60 / U 27 / R 10 / E 3   (+pity & charity rules §9.3)
```

Expected items per player per 60 s race ≈ 5–6 granted, so a 6-player race sees ~30 casts — one notification every ~2 s in the back half, which is exactly the desired "constant fireworks without spam" cadence given the 2-concurrent notification budget (§5.1).

### 15.5 Rubber band (anti-runaway / catch-up)

```
p̄ = mean position of all horses
R_i = clamp(0.004 × (p̄ − pos_i), −0.06, +0.10)
```

25 units behind the mean ⇒ +10% (full boost); 15 ahead ⇒ −6% (full drag). Gentle enough that earned leads survive (~max drag 6% vs tap bonus 27%+), strong enough that the pack stays camera-tight and last place stays alive. Lightning mode multiplies R by 1.5. **Design intent: rubber band compresses the pack; it does not pick winners — winners are picked by bursts (taps/items) timed near the line.**

### 15.6 Event cadence

First event t≈12 s, then every 12–20 s ⇒ 3–4 events per 60 s race; none after 90% of track. Chaos mode: every 7–12 s, may overlap 2.

### 15.7 Baseline-horse viability check

An untapped, item-less horse runs at `B × N(t) × (1 + R)`. With noise averaging 1.0 and R giving up to +10% when trailing, it finishes ~55–66 s — within one Crowd Wave/Second Wind of contention. Required outcome (§2.3): 8–13% win rate for each untapped horse in the 3-tappers-on-one-horse scenario. Tune `0.004` band constant first, tap cap second, if the Monte Carlo drifts.

### 15.8 Photo finish & finish resolution

- Finish order = server tick order of crossing `pos ≥ TRACK_LENGTH` (interpolate sub-tick crossing time for fairness: `t_cross = t + (L − pos_prev)/v`).
- **Photo finish triggers** when the top two horses' projected crossing times are within 120 ms (Lightning: 240 ms) at the moment the first crosses. The sim resolves the winner immediately and truthfully; the *presentation* withholds it for the slow-mo replay + "REVIEWING…" beat. Never fudge results for drama — the drama is in the reveal timing, and result integrity is sacred (people are eating this outcome).

### 15.9 Duration guidance

- 60 s = sweet spot (2 powerup waves, 3–4 events, arm fatigue just starting).
- <30 s: suppress one event slot; drop interval floor 5 s. >90 s: taps fatigue — auto-enable Rain-style "tap efficiency" events and warn the host ("long races favor the lazy").

### 15.10 Fairness invariants (encode as tests)

1. No mechanic may reference *which player* backs a horse (only how many/tap rates) — no favoritism paths.
2. All track twists are lane-agnostic and position-symmetric in expectation.
3. Freeze-class lockout near the line (§9.0) always enforced.
4. The winner reported = the winner simulated (no presentation-layer overrides).
5. Same seed + same input trace ⇒ identical result (determinism, powers §18.6).

---

## 16. Technical Risks & Mitigations

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| 1 | Phone screens sleep mid-race → socket drops, taps stop | High | `navigator.wakeLock` (needs HTTPS — tunnel provides it); `visibilitychange` instant reconnect; taps are stateless so recovery is invisible |
| 2 | iOS Safari quirks (no vibration, aggressive tab freezing, audio unlock) | High | Vibration is progressive enhancement; phones are muted by default; test join→race flow on iOS early (M3 acceptance) |
| 3 | Quick Tunnel URL changes every launch / occasional tunnel flakiness | Medium | QR always derives from live request host (§7.6); document a named-tunnel upgrade path; LAN fallback printed at boot |
| 4 | Canvas perf on an old TV-stick browser | Medium | Display targets a laptop plugged into the TV (state this in README); perf budget test with 12 horses + 300 particles; particle pool caps |
| 5 | Tap message flood (10 players × 10 msg/s) | Low | Already batched (§7.3) — 100 msg/s total is nothing; server-side per-token rate limits as belt-and-braces |
| 6 | Event-loop blocking (JSON encode of snapshots ×40 clients) | Low | Encode each snapshot once, broadcast the same string to all sockets; measure tick time, alert if >10 ms |
| 7 | Two people claim host / host tab lost | Medium | Host token in localStorage; "reclaim host" only via token; abort+recreate room is the documented 10-second recovery |
| 8 | Cheating (replayed WS frames, forged tap counts) | Medium | Server-side tap cap (12 tps) makes forging useless; all validation server-side; office threat model ends there |
| 9 | Balance drifts as items are added (fun rot) | Medium | §18.6 Monte Carlo suite is a CI gate: win-rate envelopes must hold before merge |
| 10 | Scope spiral (this doc is big) | High | Milestones are strictly ordered (§17); the game is *shippable at M3* and every milestone after is additive |

---

## 17. Implementation Roadmap

Each milestone ends in a demoable state with explicit acceptance criteria. Do not start milestone N+1 with N's criteria unmet.

**M0 — Skeleton (foundation)**
FastAPI app; static file serving; `/` and `/play` shells; WS echo endpoint; `uv run main.py` works; constants.py created.
✅ *Two browsers exchange WS messages through the server.*

**M1 — Rooms & Lobby**
RoomManager, 4-letter codes, host token, join/name/horse-pick flow, roster broadcast, QR (segno, request-host-derived), options entry UI on display, lobby screen with flying name chips.
✅ *Phone scans QR from the TV, joins, name appears on TV within 1 s.*

**M2 — The Race Exists**
RaceEngine: 20 Hz loop, velocity model with noise + rubber band (no taps/items yet), countdown/running/finish phases, snapshot broadcast, display renders moving horses (basic rig: capsule + legs + emoji jockey + name plates), camera follow, finish detection, minimal winner screen.
✅ *Six untapped horses run a complete, smooth, organic-looking 60 s race; a winner is declared; headless sim test passes determinism check.*

**M3 — Players Matter (shippable core)**
Tap batching phone→server, T-bonus math, phone race UI (tap zone, ripple, tps meter, position card, connection dot), `you` messages, reconnect flow, leaderboard rail with animated reorder, basic countdown + finish audio.
✅ *A tapped horse observably beats untapped ones ~but not always~; phone reconnect mid-race resumes seamlessly; iOS + Android both playable. This is a usable dinner-picker.*

**M4 — Powerups**
Inventory, drop scheduler with rarity/pity/charity, 12-item starter set (rows 1–13 of §9.1 minus Espresso if time), targeting sheet on phone, effect engine (buff/debuff/trap/shield/ghost stacking rules), display notifications + cast VFX, per-tier stings.
✅ *"{Player} used {Item} on {Target}" appears on TV; shields block; traps trigger; Monte Carlo envelopes still pass.*

**M5 — Events & Commentary**
Event scheduler + 10 events from §12.2 (must include Loose Cow), telegraph banners, commentary engine + ≥80 lines, ticker.
✅ *A full race feels alive with zero player input; the cow gets a laugh.*

**M6 — Spectacle Polish**
Photo-finish sequence, victory ceremony + confetti + stat cards, countdown gates, crowd system + emoji reactions, race music layers, particle pool, screenshake, springs everywhere.
✅ *The finish of a close race makes a test audience audibly react.*

**M7 — Tracks & Modes**
All 6 tracks (themes + twists), mode framework + all 6 modes (Betting UI on phones, Elimination loop logic), host settings drawer, remaining powerups (full catalog) + remaining events.
✅ *Each mode/track combination completes without error (automated sweep); Betting persists bankrolls across races.*

**M8 — Ship It**
README (run + tunnel instructions), boot-time URL/QR printout, session stats JSON, `reducedMotion`, error overlays, final balance pass against §18.6, load test (10 players scripted + 30 spectators).
✅ *A stranger can go from `git clone` to office race night with only the README.*

---

## 18. Coding Agent Handoff

### 18.1 Ground rules

- Python ≥3.12, managed by **uv** exclusively: `uv add fastapi "uvicorn[standard]" segno`, `uv add --dev pytest`, run with `uv run main.py`, tests with `uv run pytest`. Never pip, never manual venvs.
- Frontend: **no build step, no npm, no CDN imports** (must work offline on LAN). Vanilla ES modules under `/static`. The only vendored binary asset allowed: one .woff2 font file.
- All gameplay constants in `server/constants.py` — nothing tunable hardcoded elsewhere.
- Type hints throughout; pydantic models for WS messages (`protocol.py`); dataclasses for game state.
- The RaceEngine must be **pure-Python, I/O-free, and deterministic given (config, seed, input-trace)** — the network layer feeds it inputs and reads snapshots. This is the load-bearing architectural requirement; it enables every test in §18.6.

### 18.2 Repository layout

```
food-picker/
├── main.py                  # entry: uvicorn.run(app), prints URLs + QR at boot
├── pyproject.toml
├── SPEC.md                  # this document
├── server/
│   ├── app.py               # FastAPI app, routes, WS endpoint, static mounts
│   ├── rooms.py             # RoomManager, Room, join/host logic, broadcaster
│   ├── engine.py            # RaceEngine: tick loop, velocity model, finish
│   ├── powerups.py          # catalog, effects, drop scheduler, stacking
│   ├── events.py            # event table, scheduler, telegraphs
│   ├── modes.py             # ModeConfig × 6
│   ├── tracks.py            # track metadata + twist hooks
│   ├── commentary.py        # line tables + trigger engine
│   ├── protocol.py          # pydantic message models (client↔server)
│   └── constants.py         # every number in §15
├── static/
│   ├── display/  index.html, display.js, renderer.js, camera.js,
│   │             particles.js, horses.js, ceremony.js, audio.js
│   ├── play/     index.html, play.js
│   └── shared/   ws.js (reconnecting socket), springs.js, theme.css
└── tests/
    ├── test_engine.py        # determinism, velocity model, finish order
    ├── test_powerups.py      # stacking, shields, lockouts, mercy rule
    ├── test_events.py
    └── test_balance.py       # Monte Carlo envelopes (§18.6)
```

### 18.3 Key implementation notes

- **One room loop:** `asyncio.create_task(room.run())`; the loop drains an input queue (taps/casts arrived since last tick), steps the engine, broadcasts one pre-encoded snapshot string to all sockets. All room mutation happens inside the loop or the WS handler on the same event loop — no locks.
- **Snapshot format** (keep keys short): `{"t":"snapshot","k":tick,"h":[{"i":0,"p":412.3,"fx":["boost","mud"]},…],"o":[2,0,1,…]}`. `fx` is a list of active visual-effect tags — the display maps tags to VFX; the server never sends render instructions.
- **Client interpolation:** render at `now − 150 ms` between the two bracketing snapshots (§7.3).
- **Effects engine:** each active effect = `(kind, magnitude, expires_at, source_player)`; `P = clamp(Σ magnitudes)`; hard overrides (freeze/stumble) as separate timers. Implement §9.0 rules as small guard functions with unit tests, not inline conditionals.
- **QR:** `segno.make(url).svg_inline(scale=12)` embedded in lobby HTML; URL = `{scheme}://{host}/play?room={code}` from request headers (respect `X-Forwarded-Proto/Host`).
- **Boot printout:** LAN IP URL + note about `cloudflared tunnel --url http://localhost:8000`.

### 18.4 What NOT to build

No user accounts · no persistence beyond a stats JSON · no admin panel · no i18n · no mobile-app wrappers · no delta compression · no server-side rendering · no plugin systems · no config files beyond constants.py · no framework migrations. When in doubt, choose the boring option and keep the fun budget for §13.5.

### 18.5 Definition of done (per feature)

Server logic unit-tested; visible on display AND phone where applicable; survives a phone reconnect; constants in constants.py; no console errors on Chrome + iOS Safari.

### 18.6 Balance test suite (CI gate — build this at M2, run forever)

Headless Monte Carlo using the deterministic engine with scripted input traces (seeds 0–999):

1. **Untapped field:** 6 horses, no input → each horse wins 16.7% ± 5pts; mean duration 58–66 s.
2. **Lone wolf:** 1 horse with a scripted 10 tps tapper, 5 untapped → tapped horse wins 30–45%; every untapped horse ≥ 6%.
3. **Team vs solo:** 3 tappers on A, 1 on B → A wins more than B; B wins more than untapped; nobody > 55%.
4. **Item smoke:** random item casts from all players → no crashes, invariants (§15.10) hold, positions always finite & ordered.
5. **Mode sweep:** every mode × track completes 50 seeded races without exception.

Envelope failures block the merge that caused them. This suite is what keeps the game fun after fifty tweaks.

---

## 19. Stretch Goals / Future Backlog (post-ship, roughly ordered by fun-per-effort)

1. **Season stats & office league** — persist the stats JSON across nights: career wins per food, per player; "Chipotle: 9-time champion" banners at lobby.
2. **AI commentator (TTS)** — Web Speech API first; later, pre-generated LLM color commentary lines per race night's food list.
3. **Instant replay / highlights** — ring-buffer the last 8 s of snapshots; auto-replay the photo finish from a second camera angle; "GIF this finish" export.
4. **Achievements** — "Rug Puller", "Diamond Hooves" (win while shielded), "The People's Champion" (win with 4+ backers), toast at ceremony.
5. **Horse cosmetics** — hats/skins earned by wins ("Pizza has earned the crown 👑"); pure ceremony flair.
6. **Custom powerups** — host-authored name + emoji mapped onto existing effect archetypes ("Kevin's Casserole" = Turbo).
7. **Draft night mode** — snake-draft horses before entry, banning phase for options ("veto sushi").
8. **Relay mode** — teams of options; baton-pass midpoint (needs new sim hooks — that's why it's back-logged).
9. **Holiday event packs** — seasonal tracks/events (Halloween: ghost jockeys; December: sleigh horses).
10. **Streaming mode** — chroma-friendly layout + delay-tolerant spectator page for remote offices.
11. **Bracket night** — persistent tournament across evenings; the monthly FOOD CHAMPION banner.
12. **Named Cloudflare tunnel setup script** — stable URL + printed QR sticker for the office wall.

---

## 20. Final Recommendations

1. **Build M0–M3 before touching a single powerup.** A smooth, organic-feeling race with satisfying taps is the whole foundation; items and events multiply fun that already exists — they can't create it.
2. **Spend the polish budget where the room is looking:** photo finish, ceremony, loose cow, powerup notifications. One perfectly-executed photo finish is worth ten extra powerups.
3. **Keep the influence math honest and visible.** The tap cap glow, the telegraphed events, the stated Chaos-mode odds — players forgive losing to chaos they can see; they never forgive invisible thumbs on the scale. Never fudge a result.
4. **Guard the simplicity budget as fiercely as the fun budget.** One process, three dependencies, no build step. Every operational step between "we're hungry" and "they're off!" is churn that kills adoption.
5. **The commentary writing matters more than it looks.** Budget a real writing pass; 120 good lines are the personality of the product.
6. **Ship, then rematch.** The session-loop (Race Again in ≤10 s, bankrolls and stats accruing across the night) is what turns a picker into a ritual. The rematch button is the most important button in the game.

*Now go make dinner the most exciting decision of the day.* 🏇
