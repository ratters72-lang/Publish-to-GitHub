"""freeze_expiry_pursuit.py — the freeze-expiry HAND-BACK decision.

THE DEFECT THIS MODULE REMOVES ("post-miss hesitation")
------------------------------------------------------
A defender beaten by a line break is marked by
``OpenPlayEngine._handle_broken_tackle`` with ``frozen = True`` and
``freeze_timer = 1.0 s`` (1.2 s on the BUMP_OFF variant).  While that timer
is live the target-generation chain pins ``target_x`` to the body's own
``x`` three times over:

  1. ``defensive_behaviour_unified._apply_structured_open_play`` step 7
     (``freeze_timer > 0`` -> ``frozen = True``, ``target_x = d.x``,
     ``target_source = "struct_freeze_timer"``),
  2. the engine's FREEZE-STATE COMPLIANCE latch
     (``open_play_engine.update`` -> ``p.target_x = p._freeze_pin_x``),
  3. the caller's own legacy expiry branch -- ``decay_freeze_timers``
     re-pins the target to the body's position on the expiry tick.

and EVERY recovery writer declines the body, because they all exempt a live
``freeze_timer`` / ``frozen``:

  - ``line_break_static_guard.is_static_guard_eligible`` (the dispatch's
    LAST X write, whose entire contract is to re-target a static post-line-
    break body),
  - ``seed_hold.is_seed_stale``,
  - ``target_x_glide.is_target_x_glide_eligible``,
  - ``writer_priority_bands.is_band_eligible``,
  - ``movement_mode_decision.resolve_pre_tackle_stuck``,
  - ``pretackle_machine._invalid_candidate``,
  - ``pre_tackle_authority.is_pre_tackle_eligible``.

So while the timer runs there is no author of a pursuit point at all, and
``target_x == x`` means the accel-lerp produces ZERO velocity while the
carrier runs clear.  Measured shape of a latched body-frame: ``state=CHASE``
with ``move_speed ~ 7.9-8.3`` and ``off_x = 0.0`` -- a body ordered to
sprint to where it already stands.

THE LAW (this module)
---------------------
The pin is a MEASUREMENT of where the body fell over; it is never a command
to hold station.  The tick that BURNS THE TIMER TO ZERO is the first frame
on which the recovery writers are eligible again, so the hand-back must
already be a live command rather than a second self-pin.  On expiry, for a
genuine line defender with a GENUINE LINE BREAK live and no tackle-side
system actually holding the body:

    ``target_x``       := ``float(bc.x)``
    ``target_y``       := ``float(bc.y)``
    ``movement_state`` := ``MovementState.CHASE``
    plus: ``frozen = False``, the freeze-pin latch cleared, all timing /
    retreat locks cleared, and ``move_speed`` restored to the governed
    defensive ceiling, stamped ``target_source = FREEZE_EXPIRY_PURSUIT_SOURCE``.

Nothing else is touched: no roles, no spacing, no width, no anchor
authority, no depth role.  The body is handed back to the ordinary
OPEN_PLAY dispatch (which runs immediately AFTER this call) as a live
pursuer, exactly as ``line_break_static_guard`` would have made it had the
guard been allowed to see it.

Everything else keeps the LEGACY behaviour byte-for-byte: no break live,
not an eligible line body, a tackle-side owner actually driving the body,
or the ``RLM_FREEZE_EXPIRY_PURSUIT=0`` A-B / rollback lever -- in every
one of those cases the caller's historical self-pin stands unchanged.

IMPORT SAFETY
-------------
No module-scope PROJECT imports (``player.movement_states`` only, a leaf).
Every project reach is FUNCTION-LOCAL, so ``world/open_play_engine.py``
imports this lazily inside ``decay_freeze_timers`` and no load order can
create a cycle.
"""

from __future__ import annotations

import os

from player.movement_states import MovementState

__all__ = [
    "DEFENSIVE_CEILING_FALLBACK_MPS",
    "FREEZE_EXPIRY_PURSUIT_ENV_VAR",
    "FREEZE_EXPIRY_PURSUIT_SOURCE",
    "defensive_ceiling_mps",
    "freeze_expiry_pursuit_enabled",
    "is_expiry_pursuit_eligible",
    "hand_back_expiry_pursuit",
]

# ── A-B / ROLLBACK LEVER ─────────────────────────────────────────────
# Read at consumption time, so flipping it off restores the pre-change
# engine exactly (the expiry branch's self-pin) with no code revert --
# the same pattern as every other runtime lever in this package.
FREEZE_EXPIRY_PURSUIT_ENV_VAR: str = "RLM_FREEZE_EXPIRY_PURSUIT"

# The ``target_source`` stamp this hand-back writes, so the writer-priority
# bands / target-write log / reactivity audit can attribute the frame.
FREEZE_EXPIRY_PURSUIT_SOURCE: str = "freeze_expiry_pursuit"

# Last-resort value for the unified defensive ceiling when the tuner
# authority cannot be reached.  It is the documented shipped default of
# ``DefenceTuners.max_defensive_speed_mps``; it is NOT a second declaration
# of the law -- the authority is consulted first and this only exists so a
# stub world cannot crash the frame boundary.
DEFENSIVE_CEILING_FALLBACK_MPS: float = 8.3

# Resolved once per process: the ceiling is a static tuning constant, and
# the expiry tick runs inside the frame loop.
_CEILING_CACHE: dict = {}


def defensive_ceiling_mps(world=None, d=None) -> float:
    """The GOVERNED unified defensive ceiling (m/s) for an expiry hand-back.

    Resolution order, so the single source of the law is the tuner
    authority and never this module:

      1. a tuner object stamped on the world (``world.def_tuners`` /
         ``world.defence_tuners``) -- the live authority when the engine
         has one to hand;
      2. ``d.max_defensive_speed_mps`` -- a per-body governed ceiling;
      3. ``DefenceTuners().max_defensive_speed_mps`` -- the authority
         itself, resolved once and cached;
      4. ``DEFENSIVE_CEILING_FALLBACK_MPS`` for a stub world.

    It is NOT the player's physical ``max_speed_mps``: the hand-back must
    restore the same pace ``line_break_static_guard`` restores, or the two
    recovery writers would disagree about how fast a beaten defender runs.
    """
    for holder in (
        getattr(world, "def_tuners", None),
        getattr(world, "defence_tuners", None),
        getattr(d, "def_tuners", None),
    ):
        value = getattr(holder, "max_defensive_speed_mps", None)
        if value:
            return float(value)

    body_value = getattr(d, "max_defensive_speed_mps", None)
    if body_value:
        return float(body_value)

    cached = _CEILING_CACHE.get("value")
    if cached is not None:
        return float(cached)

    try:
        from game.defence.defence_lvl_1.defensive_tuners import (
            DefenceTuners as _DefenceTuners,
        )
        resolved = float(_DefenceTuners().max_defensive_speed_mps)
    except Exception:
        resolved = DEFENSIVE_CEILING_FALLBACK_MPS
    _CEILING_CACHE["value"] = resolved
    return resolved


def freeze_expiry_pursuit_enabled() -> bool:
    """True when the expiry hand-back may write a live pursuit target.

    Default ON.  ``RLM_FREEZE_EXPIRY_PURSUIT=0`` (or false/no/off/empty)
    disarms it: expiry keeps the historical self-pin on every body.
    """
    return os.environ.get(
        FREEZE_EXPIRY_PURSUIT_ENV_VAR, "1"
    ).strip().lower() not in ("0", "false", "no", "off", "")


def is_expiry_pursuit_eligible(d, world=None, bc=None) -> bool:
    """True when *d*'s expiry may be handed back as a LIVE pursuit.

    Reads the SAME exemption set ``line_break_static_guard`` uses for its
    recovery write (delegated to
    ``line_break_static_guard.is_static_guard_recovery_eligible``), so the
    two writers can never disagree about which bodies are genuine line
    bodies.  The freeze gates themselves (``frozen`` / ``freeze_timer``)
    are deliberately NOT consulted here: this predicate runs on the expiry
    tick, which is precisely the frame on which they are being cleared.

    Additionally requires:
      - the ``RLM_FREEZE_EXPIRY_PURSUIT`` lever on,
      - no live TES (the engine owns every body's movement there),
      - a live ball carrier,
      - a GENUINE line break, via the canonical
        ``movement_mode_decision.carrier_past_defensive_line`` predicate,
      - no tackle-side system ACTUALLY driving the body
        (``fullback_role.fullback_tackle_side_owner_live`` -- a stamp is not
        ownership).
    """
    if d is None or not freeze_expiry_pursuit_enabled():
        return False
    # ── FULLBACK RECOVERY OWNERSHIP (Stage 3, 2026-09-30) ──
    # The tagged fullback owns its OWN recovery, so the freeze-expiry hand-back
    # may not name a pursuit point for it.  The gate is the controller's own
    # ownership predicate, and it is the SAME one ``line_break_static_guard``
    # (the shared-recovery predicate this hand-back delegates to) and
    # ``post_miss_pursuit`` read, so the three pursuit writers cannot drift
    # apart about the last defender.  An unreachable controller reports
    # "allowed", which leaves this module's historical fullback check (inside
    # the delegated recovery predicate) as the sole arbiter -- the pre-Stage-3
    # behaviour, never a silent widening of the hand-back's reach.
    try:
        from game.defence.defence_lvl_1.fullback_controller import (
            shared_recovery_allowed as _shared_recovery_allowed,
        )
        if not _shared_recovery_allowed(world, d):
            return False
    except Exception:                                    # noqa: BLE001
        pass
    if getattr(world, "tes_active", False):
        return False
    if bc is None:
        bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False

    # A tackle-side system that is genuinely writing the body this frame
    # keeps it -- the hand-back is only for an UNOWNED body.
    try:
        from game.defence.defence_lvl_1.fullback_role import (
            fullback_tackle_side_owner_live as _owner_live,
        )
        if _owner_live(d):
            return False
    except Exception:
        pass

    # Shared exemption set with the static guard (no drift possible).
    try:
        from game.defence.defence_lvl_1.line_break_static_guard import (
            is_static_guard_recovery_eligible as _recovery_eligible,
        )
    except Exception:
        return False
    if not _recovery_eligible(d, world):
        return False

    # The single break definition, so expiry / CHASE / tackle candidacy can
    # never disagree about what a broken line is.
    try:
        from game.defence.defence_lvl_1.movement_mode_decision import (
            carrier_past_defensive_line as _past_line,
        )
    except Exception:
        return False
    return bool(_past_line(world, bc))


def _clear_freeze_pin_latch(d) -> None:
    """Drop the engine's freeze-pin latch attributes on *d*.

    The FREEZE-STATE COMPLIANCE loop latches ``target_x``/``target_y`` while
    ``d.frozen`` is True and re-pins them every frame.  A hand-back that
    cleared ``frozen`` but left the latch armed would be re-pinned on the
    same frame's compliance pass.  The latch is normally cleared by that
    loop's own thaw branch; doing it here makes the hand-back independent of
    the loop's ordering.
    """
    d._freeze_pin_active = False
    for attr in ("_freeze_pin_x", "_freeze_pin_y"):
        if hasattr(d, attr):
            try:
                delattr(d, attr)
            except AttributeError:
                pass


def hand_back_expiry_pursuit(d, world=None, bc=None) -> bool:
    """Hand an expiring body back as a LIVE pursuer of the carrier.

    Returns True only when the body was actually handed back; False leaves
    the caller's legacy self-pin to run unchanged (rollback / no break /
    owned body / ineligible role).

    Writes (and nothing else): ``target_x`` / ``target_y`` /
    ``movement_state`` / ``frozen`` / the freeze-pin latch / the timing and
    retreat locks / ``move_speed`` / ``target_source``.
    """
    if not is_expiry_pursuit_eligible(d, world, bc):
        return False
    if bc is None:
        bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False

    # The GOVERNED ceiling, not the player's physical max: a slow body is
    # clamped DOWN to its own max by the ``min`` below, and a fast body is
    # held to the unified defensive pace every other recovery writer uses.
    ceiling = defensive_ceiling_mps(world, d)

    # ── OVERWRITE with the LIVE carrier ──
    try:
        d.target_x = float(bc.x)
        d.target_y = float(bc.y)
    except (TypeError, ValueError):
        return False

    d.frozen = False
    d.timing_locked = False
    d.reaction_timer = 0.0
    d.retreat_delay = 0.0
    d.lateral_hesitation = 0.0
    d.step_stagger = 0.0
    d.step_stagger_active = False
    # CHASE: non-smooth-damp, excluded from the target stabiliser, so the
    # pursuit runs on the fast accel-lerp path and the tackle pipeline's
    # ``is_closing_posture`` (CHASE / SHOOT) can arm again.
    d.movement_state = MovementState.CHASE
    d.move_speed = min(
        float(getattr(d, "max_speed_mps", ceiling) or ceiling), ceiling
    )
    _clear_freeze_pin_latch(d)
    d.target_source = FREEZE_EXPIRY_PURSUIT_SOURCE
    d._target_writer = FREEZE_EXPIRY_PURSUIT_SOURCE
    return True
