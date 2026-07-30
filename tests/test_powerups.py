"""Powerup rules: protections, lockouts, stacking, mercy rule, drop economy."""

from __future__ import annotations

import pytest
from conftest import DEFAULT_OPTIONS

from server import constants as C
from server.effects import Category, Effect, HostileOutcome
from server.engine import RaceEngine
from server.powerups import (
    BY_TIER,
    POWERUPS,
    Polarity,
    Scope,
    TargetClass,
    Tier,
    is_dead_draw,
    powerup_catalog,
    roll_tier,
)
from server.roster import build_horses
from server.state import RaceConfig, RacePhase, RacePlayer


@pytest.fixture()
def engine() -> RaceEngine:
    """A running engine with two players on different horses."""

    players = [
        RacePlayer(id="p1", name="Cullen", horse_id=0),
        RacePlayer(id="p2", name="Dana", horse_id=1),
    ]
    engine = RaceEngine(
        RaceConfig(seed=5, powerups_on=True, events_on=False),
        build_horses(DEFAULT_OPTIONS),
        players,
    )
    while engine.phase is not RacePhase.RUNNING:
        engine.step()
    return engine


def give(engine: RaceEngine, player_id: str, powerup_id: str, slot: int = 0) -> None:
    """Hand over an item already unlocked — the gate has its own tests."""

    held = engine.players[player_id].inventory[slot]
    held.clear()
    held.powerup_id = powerup_id
    held.armed = True


def hostile(engine: RaceEngine, target, *, tier: Tier = Tier.UNCOMMON, magnitude=-0.3):
    return engine.apply_hostile(
        target,
        Effect(
            id="test_debuff",
            label="Test",
            magnitude=magnitude,
            expires_at=engine.race_time + 3.0,
            hostile=True,
            tier=tier.value,
        ),
        duration=3.0,
    )


# ---------------------------------------------------------------------------
# Catalog integrity
# ---------------------------------------------------------------------------


def test_catalog_is_short_and_every_item_says_what_it_does():
    """A slim catalog is the point: each item must read at a glance on a phone."""

    assert 8 <= len(POWERUPS) <= 16, f"{len(POWERUPS)} items is too many to teach"
    for powerup in POWERUPS.values():
        assert powerup.name and powerup.emoji and powerup.blurb
        assert isinstance(powerup.tier, Tier)
        assert isinstance(powerup.target, TargetClass)
        assert isinstance(powerup.polarity, Polarity)
        assert isinstance(powerup.scope, Scope)
        meta = powerup.client_meta()
        assert meta["polarityIcon"], powerup.id
        assert meta["scopeLabel"], powerup.id
    assert all(BY_TIER[tier] for tier in Tier), "every tier needs at least one item"


def test_global_items_are_flagged_as_hitting_everyone():
    for powerup in POWERUPS.values():
        if powerup.target is TargetClass.GLOBAL:
            assert powerup.scope is Scope.EVERYONE, powerup.id
            assert "EVERY" in powerup.client_meta()["scopeLabel"]


def test_self_items_point_at_your_own_horse():
    for powerup in POWERUPS.values():
        if powerup.target is TargetClass.SELF:
            assert powerup.scope is Scope.MINE, powerup.id
            assert powerup.polarity in (Polarity.GOOD, Polarity.PROTECT), powerup.id


def test_client_catalog_is_serialisable():
    catalog = powerup_catalog()
    assert len(catalog) == len(POWERUPS)
    assert {"id", "name", "emoji", "tier", "target", "blurb"} <= set(catalog[0])


# ---------------------------------------------------------------------------
# Protections (§9.0)
# ---------------------------------------------------------------------------


def test_shield_absorbs_exactly_one_hostile_effect(engine):
    target = engine.horses_by_id[2]
    target.shield_charges = 1
    assert hostile(engine, target) is HostileOutcome.SHIELDED
    assert target.shield_charges == 0
    assert hostile(engine, target) is HostileOutcome.APPLIED


def test_ghost_fizzles_hostile_effects(engine):
    target = engine.horses_by_id[2]
    target.ghost_until = engine.race_time + 5.0
    assert hostile(engine, target) is HostileOutcome.GHOSTED
    assert not any(effect.hostile for effect in target.effects)


def test_diamond_hands_is_immune_but_still_takes_buffs(engine):
    target = engine.horses_by_id[2]
    target.diamond_until = engine.race_time + 6.0
    assert hostile(engine, target) is HostileOutcome.IMMUNE
    engine.add_effect(
        target,
        Effect(id="buff", label="Buff", magnitude=0.4, expires_at=engine.race_time + 2.0),
    )
    assert any(effect.magnitude > 0 for effect in target.effects)


def test_golden_carrot_ignores_common_debuffs_only(engine):
    target = engine.horses_by_id[2]
    target.golden_until = engine.race_time + 5.0
    assert hostile(engine, target, tier=Tier.COMMON) is HostileOutcome.IMMUNE
    assert hostile(engine, target, tier=Tier.RARE) is HostileOutcome.APPLIED


def test_mercy_rule_halves_duration_for_last_place(engine):
    last = engine.last_place()
    assert last is not None
    hostile(engine, last)
    effect = next(effect for effect in last.effects if effect.id == "test_debuff")
    assert effect.expires_at == pytest.approx(
        engine.race_time + 3.0 * C.MERCY_RULE_DURATION_MULTIPLIER
    )


def test_diamond_hands_clears_existing_debuffs(engine):
    horse = engine.horses_by_id[0]
    hostile(engine, horse)
    engine.stumble_horse(horse, 1.0)
    give(engine, "p1", "diamond_hands")
    assert engine.use_powerup("p1", 0, None)[0]
    assert not any(effect.hostile for effect in horse.effects)
    assert horse.stumble_until == 0.0


# ---------------------------------------------------------------------------
# Freeze lockout (§9.0)
# ---------------------------------------------------------------------------


def test_freeze_stops_a_horse_mid_race(engine):
    target = engine.horses_by_id[2]
    target.pos = C.TRACK_LENGTH * 0.4
    assert engine.apply_freeze(target, seconds=1.2, tag="halted") is HostileOutcome.APPLIED
    assert target.is_frozen(engine.race_time)


def test_freeze_softens_in_the_final_stretch(engine):
    target = engine.horses_by_id[2]
    target.pos = C.TRACK_LENGTH * (C.FINAL_STRETCH_FRACTION + 0.02)
    outcome = engine.apply_freeze(target, seconds=1.2, tag="halted")
    assert outcome is HostileOutcome.SOFTENED
    assert not target.is_frozen(engine.race_time)
    assert any(effect.id == "halted_soft" for effect in target.effects)


def test_a_frozen_horse_cannot_be_refrozen_immediately(engine):
    target = engine.horses_by_id[2]
    target.pos = C.TRACK_LENGTH * 0.3
    engine.apply_freeze(target, seconds=1.2, tag="halted")
    assert engine.apply_freeze(target, seconds=1.2, tag="halted") is HostileOutcome.IMMUNE


# ---------------------------------------------------------------------------
# Targeting rules
# ---------------------------------------------------------------------------


def test_leader_class_never_targets_its_own_caster(engine):
    leader = engine.leader()
    assert leader is not None
    caster = engine.players["p1"]
    caster.horse_id = leader.id
    engine.recount_backers()
    give(engine, "p1", "headwind")
    assert engine.use_powerup("p1", 0, None)[0]
    assert not any(effect.id == "headwind" for effect in leader.effects)


def test_targeted_item_requires_a_valid_rival(engine):
    give(engine, "p1", "short_seller")
    assert engine.use_powerup("p1", 0, None) == (False, "needs_target")
    assert engine.use_powerup("p1", 0, 0) == (False, "needs_target"), "cannot target own horse"
    assert engine.use_powerup("p1", 0, 3)[0]


def test_using_an_empty_slot_fails_cleanly(engine):
    assert engine.use_powerup("p1", 0, None) == (False, "empty_slot")
    assert engine.use_powerup("p1", 9, None) == (False, "bad_slot")
    assert engine.use_powerup("nobody", 0, None) == (False, "not_a_player")


def test_failed_cast_does_not_consume_the_item(engine):
    give(engine, "p1", "reply_all")
    assert engine.use_powerup("p1", 0, None)[0]
    give(engine, "p1", "reply_all")
    assert engine.use_powerup("p1", 0, None) == (False, "cooldown")
    assert engine.players["p1"].inventory[0].powerup_id == "reply_all", "a fizzle keeps the item"


# ---------------------------------------------------------------------------
# Stacking (§9.0)
# ---------------------------------------------------------------------------


def test_same_effect_refreshes_instead_of_stacking(engine):
    horse = engine.horses_by_id[0]
    for _ in range(3):
        engine.add_effect(
            horse,
            Effect(id="turbo_boost", label="Turbo", magnitude=0.4, expires_at=engine.race_time + 3),
        )
    matching = [effect for effect in horse.effects if effect.id == "turbo_boost"]
    assert len(matching) == 1


def test_effect_categories_clamp_independently(engine):
    horse = engine.horses_by_id[0]
    for index in range(6):
        engine.add_effect(
            horse,
            Effect(
                id=f"powerup_{index}",
                label="Big",
                magnitude=0.5,
                expires_at=engine.race_time + 5,
                category=Category.POWERUP,
            ),
        )
    total = engine._dynamic_effect_total(horse)
    assert total <= C.POWERUP_SUM_MAX + 1e-9


def test_effect_list_stays_readable(engine):
    horse = engine.horses_by_id[0]
    horse.shield_charges = 1
    for index in range(8):
        engine.add_effect(
            horse,
            Effect(id=f"e{index}", label="E", magnitude=0.05, expires_at=engine.race_time + 9),
        )
    assert len([e for e in horse.effects if not e.protective]) <= C.MAX_VISIBLE_EFFECTS


# ---------------------------------------------------------------------------
# Traps
# ---------------------------------------------------------------------------


def place_field(engine, caster_pos: float, ahead_pos: float | None) -> tuple:
    """Put the caster and (optionally) one horse ahead somewhere unambiguous."""

    mine, rival = engine.horses_by_id[0], engine.horses_by_id[1]
    mine.pos = caster_pos
    for horse in engine.horses:
        if horse.id not in (0, 1):
            horse.pos = 4.0  # far behind, out of the way
    rival.pos = ahead_pos if ahead_pos is not None else 4.0
    return mine, rival


def test_a_thrown_peel_lands_in_front_of_the_horse_ahead(engine):
    """Traps are offensive: they go where the horse in front is heading."""

    mine, rival = place_field(engine, 200.0, 240.0)
    give(engine, "p1", "banana")
    assert engine.use_powerup("p1", 0, None)[0]
    peel = [zone for zone in engine.zones if zone.trap][-1]
    assert peel.start > mine.pos, "a peel behind the caster only helps the leader"
    assert peel.contains(rival.pos + C.TRAP_FORWARD_LEAD)


def test_the_horse_ahead_runs_onto_the_peel(engine):
    mine, rival = place_field(engine, 200.0, 240.0)
    give(engine, "p1", "banana")
    engine.use_powerup("p1", 0, None)
    for _ in range(40):
        rival.pos += 1.5
        engine._resolve_zones()
        if rival.is_stumbling(engine.race_time):
            break
    assert rival.is_stumbling(engine.race_time)
    assert not mine.is_stumbling(engine.race_time)


def test_a_leader_drops_its_trap_behind_as_a_rear_guard(engine):
    """Nobody ahead to aim at, so it becomes defensive."""

    mine, chaser = place_field(engine, 300.0, None)
    chaser.pos = 280.0
    give(engine, "p1", "banana")
    assert engine.use_powerup("p1", 0, None)[0]
    peel = [zone for zone in engine.zones if zone.trap][-1]
    assert peel.end < mine.pos


def test_a_thrower_is_immune_to_their_own_trap(engine):
    """A forward throw would otherwise catch its caster on the way past."""

    for item in ("banana", "oil_slick"):
        mine, rival = place_field(engine, 200.0, 205.0)
        give(engine, "p1", item)
        engine.use_powerup("p1", 0, None)
        zone = [z for z in engine.zones if z.trap][-1]
        mine.pos = (zone.start + zone.end) / 2  # drive straight through it
        assert not engine._zone_hits(zone, mine), f"{item} caught its own thrower"
        engine._resolve_zones()
        assert not mine.is_stumbling(engine.race_time)
        assert engine._zone_multiplier(mine) == 0.0


def test_ghost_horse_walks_through_traps(engine):
    mine, victim = place_field(engine, 200.0, 240.0)
    give(engine, "p1", "banana")
    engine.use_powerup("p1", 0, None)
    peel = [zone for zone in engine.zones if zone.trap][-1]
    victim.ghost_until = engine.race_time + 5.0
    victim.pos = (peel.start + peel.end) / 2
    engine._resolve_zones()
    assert not victim.is_stumbling(engine.race_time)


def test_live_trap_count_is_capped(engine):
    for index in range(C.MAX_LIVE_TRAPS + 3):
        place_field(engine, 100.0 + index * 30, 140.0 + index * 30)
        give(engine, "p1", "banana")
        engine.use_powerup("p1", 0, None)
    assert len([zone for zone in engine.zones if zone.trap]) <= C.MAX_LIVE_TRAPS


# ---------------------------------------------------------------------------
# Drop economy (§9.3)
# ---------------------------------------------------------------------------


def test_pity_timer_excludes_commons(engine):
    tiers = {
        roll_tier(
            engine.rng, C.RARITY_WEIGHTS, common_streak=C.PITY_COMMON_STREAK, last_place=False
        )
        for _ in range(60)
    }
    assert Tier.COMMON not in tiers


def test_dead_draws_are_rejected(engine):
    horse = engine.horses_by_id[0]
    horse.shield_charges = 1
    assert is_dead_draw(
        POWERUPS["shield"], horse, now=engine.race_time, is_leader=False, last_granted_id=None
    )
    assert is_dead_draw(
        POWERUPS["turbo_boost"],
        horse,
        now=engine.race_time,
        last_granted_id="turbo_boost",
    ), "two identical commons in a row reads as the game having stopped rolling"
    assert not is_dead_draw(
        POWERUPS["turbo_boost"], horse, now=engine.race_time, last_granted_id="tailwind"
    )


def test_backers_share_one_horse_item_stream(engine):
    """Two backers on one horse must not double that horse's item income."""

    engine.players["p2"].horse_id = 0
    engine.recount_backers()
    horse = engine.horses_by_id[0]
    horse.next_drop_at = engine.race_time
    engine._maybe_grant_powerups()
    granted = sum(
        1
        for player in engine.players.values()
        if player.horse_id == 0
        for held in player.inventory
        if held.powerup_id
    )
    assert granted == 1


def test_inventory_full_skips_the_roll(engine):
    player = engine.players["p1"]
    give(engine, "p1", "turbo_boost", 0)
    give(engine, "p1", "shield", 1)
    horse = engine.horses_by_id[0]
    horse.next_drop_at = engine.race_time
    engine._maybe_grant_powerups()
    assert [held.powerup_id for held in player.inventory] == ["turbo_boost", "shield"]


def test_global_cooldown_blocks_repeat_casts(engine):
    assert engine.claim_global_cooldown("reply_all_storm")
    assert not engine.claim_global_cooldown("reply_all_storm")


def test_every_powerup_can_be_cast_without_error(engine):
    """Smoke test: no item may raise, whatever the race state."""

    for powerup_id in POWERUPS:
        give(engine, "p1", powerup_id)
        engine.use_powerup("p1", 0, 2)
        engine.step()
        for horse in engine.horses:
            assert horse.pos == horse.pos and horse.pos >= 0
