"""
game/tes/tes.py — Simplified TES physics functions.

The old TES class with its own FSM has been replaced by inline phase handling
in OpenPlayEngine. Only pure physics helper functions remain here.

This file now also provides the central TES entry function:

    tes_start_tackle(bc, defender, world) -> TESContext

All modules MUST use this function as the single entry point for TES.
No other code path may perform TES entry steps directly.
"""

import math

from .tes_context import TESContext, PlayerContext, TackleEngagement
from .tes_state import TESPhase


# ------------------------------------------------------------------
# Public entry — the ONLY way to start a TES tackle engagement
# ------------------------------------------------------------------

def tes_start_tackle(bc, defender, world):
    """
    Initialise a TES tackle engagement.

    This is the single entry point for all TES operations.  The entry
    sequence is carefully ordered (11 steps) to ensure correct pipeline
    behaviour:

       1.  Establish primary tackler
       2a. Movement freeze
       2b. Physics freeze
       3.  Momentum snapshot
       4.  Angle snapshot
       5.  Contact point snapshot
       6.  TES state initialisation
       7.  Animation state initialisation
       8.  TES lock
       9.  Joiner gate open
      10.  TESContext populate
      11.  Return TESContext

    Parameters
    ----------
    bc : Player
        The ball carrier (attacker).
    defender : Player
        The primary tackler (defender).
    world : WorldState
        The current world snapshot (used for sim_time, tackle_count, etc.).

    Returns
    -------
    TESContext
        Fully initialised TES context ready for TACKLE_INIT phase.
    """
    # ----------------------------------------------------------------
    # STEP 1 — Establish primary tackler
    # ----------------------------------------------------------------
    # Must happen before any freeze or snapshot so the TES system
    # knows which defender is the primary tackler.
    eng = TackleEngagement()
    eng.started_at = getattr(world, "sim_time_s", 0.0)

    # Snapshot defender into PlayerContext first so we can add to engagement
    deff = _make_ctx(defender)
    att = _make_ctx(bc)

    eng.add(deff, eng.started_at)
    # eng.primary_id is now set to defender.player_id

    # ----------------------------------------------------------------
    # STEP 2a — Movement freeze  (velocity and target-seeking stop)
    # ----------------------------------------------------------------
    bc.frozen = True
    defender.frozen = True

    # ----------------------------------------------------------------
    # STEP 2b — Physics freeze  (collision/contact/solidification stop)
    # ----------------------------------------------------------------
    # Currently uses the same `frozen` flag as movement freeze.
    # Kept as a distinct step for separation of concerns — a future
    # refactor may split bc.frozen into two independent flags.
    # (No additional code needed here — flag already set above.)

    # ----------------------------------------------------------------
    # STEP 3 — Momentum snapshot  (pre-freeze velocity vectors captured)
    # ----------------------------------------------------------------
    # Momentum was already computed in _make_ctx() via mass × speed.
    # These explicit snapshots make the step visible and auditable.
    bc_momentum = att.mass * math.hypot(att.vx, att.vy)
    def_momentum = deff.mass * math.hypot(deff.vx, deff.vy)

    # ----------------------------------------------------------------
    # STEP 4 — Angle snapshot  (approach angle before TES state init)
    # ----------------------------------------------------------------
    approach_angle_deg = _approach_angle(bc, defender)

    # ----------------------------------------------------------------
    # STEP 5 — Contact point snapshot  (contact coordinates)
    # ----------------------------------------------------------------
    contact_x_m = bc.x
    contact_y_m = bc.y

    # ----------------------------------------------------------------
    # STEP 6 — TES state initialisation  (phase machine, timers, stability)
    # ----------------------------------------------------------------
    ctx = TESContext()
    ctx.attacker = att
    ctx.defender = deff
    ctx.engagement = eng
    ctx.phase = TESPhase.CONTACT       # phase = "contact" (CONTACT_START equivalent)
    ctx.stability = 1.0
    ctx.collapsed = False
    ctx.release_timer = 0.0
    ctx.ptb_ready = False

    # ----------------------------------------------------------------
    # STEP 7 — Animation state initialisation
    # ----------------------------------------------------------------
    bc.anim_state = "TES_CONTACT"
    defender.anim_state = "TES_CONTACT"

    # ----------------------------------------------------------------
    # STEP 8 — TES lock  (marks players as owned by TES system)
    # ----------------------------------------------------------------
    bc.tes_locked = True
    defender.tes_locked = True

    # Contact flags are operational details — applied after the lock
    # boundary so TES ownership is established before contact system
    # can see these flags.
    bc.in_contact = True
    defender.in_contact = True
    defender.is_tackler = True

    # ----------------------------------------------------------------
    # STEP 9 — Secondary tackler registration gate
    # ----------------------------------------------------------------
    # Opens the door for _maybe_add_joiners() to register additional
    # defenders after the primary engagement is locked down.
    ctx.joiner_gate_open = True

    # ----------------------------------------------------------------
    # STEP 10 — TESContext population (remaining fields)
    # ----------------------------------------------------------------
    ctx.contact_x_m = contact_x_m
    ctx.contact_y_m = contact_y_m
    ctx.approach_angle_deg = approach_angle_deg
    ctx.tackle_number = max(1, getattr(world, "tackle_count", 0) + 1)
    ctx.dist_from_tryline_m = 100.0 - bc.x
    ctx.initial_impact_score = 1.5
    ctx.randomness = 0.7
    ctx.fatigue = 1.0
    ctx.max_joiners = 3

    # ----------------------------------------------------------------
    # STEP 11 — Return TES state
    # ----------------------------------------------------------------
    return ctx


# ------------------------------------------------------------------
# Internal helpers (moved from OPE to live with the TES domain)
# ------------------------------------------------------------------

def _make_ctx(p: "Player") -> PlayerContext:
    """Snapshot a Player into a PlayerContext for TES."""
    c = PlayerContext()
    c.player_id = p.player_id
    c.x_m = p.x
    c.y_m = p.y
    c.vx = p.vx
    c.vy = p.vy
    c.mass = getattr(p, "mass_kg", 90.0)
    c.stamina = getattr(p, "stamina", 1.0)
    c.aggression = getattr(p, "aggression", 0.5)
    c.tackle_skill = getattr(p, "tackle_skill", 0.5)
    c.offload_skill = getattr(p, "offload_skill", 0.5)
    c.strength = getattr(p, "strength", 0.5)
    c.facing = p.facing
    c.rot = getattr(p, "rot", 0.0)
    # state: enum or string -> always stored as string
    if p.state is None:
        c.state = "IDLE"
    elif isinstance(p.state, str):
        c.state = p.state
    else:
        c.state = p.state.name
    c.momentum = c.mass * math.hypot(c.vx, c.vy)
    return c


def _approach_angle(bc: "Player", d: "Player") -> float:
    """Angle (degrees) between defender's movement direction and BC."""
    dx = bc.x - d.x
    dy = bc.y - d.y
    dist = math.hypot(dx, dy)
    if dist < 0.01:
        return 0.0

    spd = math.hypot(d.vx, d.vy)
    if spd < 0.01:
        to_bc = math.atan2(dy, dx)
        diff = d.facing - to_bc
        return abs(math.degrees(diff))

    dxn = d.vx / spd
    dyn = d.vy / spd
    tx = dx / dist
    ty = dy / dist
    dot = max(-1.0, min(1.0, dxn * tx + dyn * ty))
    return math.degrees(math.acos(dot))
