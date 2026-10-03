# game/tes/tes_checks.py
import math
import random
from .tes_context import TESContext
from .tes_outcomes import TESOutcome
from .tes_state import TackleResult
from . import tes_multi_body as _multi_body

def check_offload(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    """TES PARTICIPANT FILTER APPLIED"""
    if outcome.tackle_result == TackleResult.DOMINANT:
        outcome.offload_success = False
        return

    att = ctx.attacker
    deff = ctx.defender

    base_chance = att.offload_skill * 0.5
    deff_penalty = deff.tackle_skill * 0.3
    chance = max(0.0, base_chance - deff_penalty)
    chance *= 1.0 + rng.uniform(-0.2, 0.2)

    # ── MULTI-BODY OFFLOAD WINDOW (2026-09-23) ──
    # A declaration that described a body holding a tackle ROLE *outside* the
    # contact radius is a declaration with a defender arriving who is not yet
    # in contact.  That IS the explicit offload window, and it lifts the chance
    # by a declared bonus, capped — a window, never a certainty.
    #
    # With no window open — and with no multi-body model at all, which is every
    # solo tackle — ``offload_chance_with_window`` returns the base chance
    # UNCHANGED, so the formula above is bit-identical for every contest that
    # opened no window.  The DOMINANT short-circuit above still wins: a
    # dominant tackle offers no window whatever the declaration says.
    chance = _multi_body.offload_chance_with_window(
        chance, getattr(ctx, "wrap_model", None),
    )

    outcome.offload_success = rng.random() < chance

def check_strip(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    if outcome.tackle_result == TackleResult.WEAK:
        base_chance = 0.02
    elif outcome.tackle_result == TackleResult.NEUTRAL:
        base_chance = 0.05
    else:
        base_chance = 0.10

    deff = ctx.defender
    att = ctx.attacker

    deff_bonus = deff.tackle_skill * 0.08
    att_penalty = att.strength * 0.05

    chance = max(0.0, min(1.0, base_chance + deff_bonus - att_penalty))
    chance *= 1.0 + rng.uniform(-0.3, 0.3)

    outcome.strip_success = rng.random() < chance
    if outcome.strip_success:
        outcome.tackle_result = TackleResult.STRIP

def check_fumble(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    base_chance = 0.02
    if outcome.tackle_result == TackleResult.DOMINANT:
        base_chance += 0.04

    base_chance += (1.0 - ctx.attacker.stamina) * 0.03

    # Physics-driven modifiers: instability increases fumble risk
    stability_factor = (1.0 - getattr(ctx, "stability", 1.0)) * 0.05
    if getattr(ctx, "collapsed", False):
        stability_factor += 0.06
    # Lateral drive increases fumble chance (knock-on from high-side impact)
    lateral_mag = abs(getattr(ctx, "drive_target_y", 0.0) - getattr(ctx, "bc_y0", 0.0)) * 0.02
    base_chance += stability_factor + lateral_mag

    chance = max(0.0, min(1.0, base_chance * (1.0 + rng.uniform(-0.5, 0.5))))

    if rng.random() < chance:
        outcome.tackle_result = TackleResult.FUMBLE

def check_penalty(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    base_chance = 0.03
    deff = ctx.defender

    aggression_risk = deff.aggression * 0.04
    tackle_risk = max(0, ctx.tackle_number - 4) * 0.02
    pos_risk = max(0, 1.0 - ctx.dist_from_tryline_m / 30.0) * 0.03

    chance = base_chance + aggression_risk + tackle_risk + pos_risk
    chance *= 1.0 + rng.uniform(-0.4, 0.4)
    chance = max(0.0, min(1.0, chance))

    outcome.penalty_committed = rng.random() < chance
    if outcome.penalty_committed:
        outcome.tackle_result = TackleResult.PENALTY

def check_six_again(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    if outcome.penalty_committed:
        return

    if outcome.tackle_result == TackleResult.DOMINANT:
        base_chance = 0.15
    elif outcome.tackle_result == TackleResult.NEUTRAL:
        base_chance = 0.05
    else:
        base_chance = 0.02

    chance = base_chance * (1.0 + rng.uniform(-0.5, 0.5))
    outcome.six_again_awarded = rng.random() < chance

    if outcome.six_again_awarded:
        outcome.tackle_result = TackleResult.SIX_AGAIN

def check_line_break(ctx: TESContext, outcome: TESOutcome, rng: random.Random) -> None:
    if outcome.tackle_result != TackleResult.WEAK:
        return

    attacker_dom = abs(outcome.attacker_dominance)
    base_chance = (attacker_dom - 0.35) * 1.5

    att = ctx.attacker
    speed_bonus = min(0.2, math.hypot(att.vx, att.vy) / 50.0)

    chance = base_chance + speed_bonus
    chance = max(0.0, min(1.0, chance * (1.0 + rng.uniform(-0.3, 0.3))))

    outcome.line_break = rng.random() < chance
    if outcome.line_break:
        outcome.tackle_result = TackleResult.LINE_BREAK
