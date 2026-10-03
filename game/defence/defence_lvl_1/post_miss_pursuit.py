"""post_miss_pursuit.py — POST-MISS PURSUIT ENTITLEMENT (the beaten body's chase).

THE DEFECT THIS MODULE REMOVES ("the chase is withheld, never proposed")
-----------------------------------------------------------------------
`PURSUIT_DECISION_HIERARCHY_REPORT.md` measured a beaten defender over 77,681
post-miss body-frames.  PURSUIT was never overridden (`OVERRIDDEN_BY=0`): the
chase is *withheld before it is ever proposed*.  Three gates own that
withholding, and they compound:

  1. HELD_BY_FREEZE_PIN (30.3% of frames, 59.1% of bump-off frames).  The miss
     teardown marks the beaten body ``frozen`` with a 1.0 s (bump-off 1.2 s)
     timer, and every writer declines it.  This is a DESIGN CHOICE and is left
     exactly as it is here.

  2. EXEMPT_BY_COMMIT_LATCH (9.6%).  `_handle_broken_tackle` clears
     ``_tackle_commit_active`` / ``_pursuit_commit_active`` only on the
     ``bump_off=True`` path (``_clear_bump_commit_lock``).  The plain
     broken-tackle path leaves the latches set, so `writer_priority_bands`
     deems the body ineligible and NO proposal reaches the arbiter at all.

  3. NO_PURSUIT_INTENT + ``retreat_non_compliant`` (5.6% + 11,111 frames).  On
     the expiry tick the body is handed back, and the very next frame the
     obligated-body retreat authority (the dispatch's FINAL target write,
     ``ruck_x + 10``) seizes it and drives it AWAY from the carrier: measured
     ``dist_to_bc`` rising 1.51 -> 3.13 m and climbing, with ``chase=-``
     throughout.  The two pursuit recovery writers that exist
     (``line_break_static_guard``, ``freeze_expiry_pursuit``) both decline the
     body, because ``is_static_guard_recovery_eligible`` carries a
     RETREAT-OBLIGATION VETO and the hand-back delegates to it.

THE LAW (this module)
---------------------
The engine already has the intent: `_handle_broken_tackle` finishes with a
BROKEN-TACKLE CHASE RESET that forces every unfrozen line defender to
``target_x = bc.x`` + CHASE.  The beaten primary is skipped by that reset
SOLELY because it was set ``frozen`` a few lines earlier.  The intent is
therefore already correct; it is the beaten body's PARTICIPATION in it that
is missing, and it is missing for a bounded time only (the freeze).

This module owns that participation.  A defender the carrier beat outright is
ENTITLED TO PURSUE for a bounded window after the miss: while the entitlement
is live, and while the line break is genuine and no tackle-side system is
actually driving it, the body

  * is exempt from the obligated-body retreat write (it must not be driven
    away from the carrier it was beaten by),
  * is skipped by the MMDL's own integration (so it is handed to the engine's
    ``Player.update`` exactly as the retreat-obligation skip does), and
  * is given the frame's FINAL target write — a pursuit point on the live
    carrier, ``movement_state = CHASE``, and the GOVERNED defensive ceiling.

Nothing else is touched: no roles, no spacing, no width, no anchor authority,
no depth role, and no priority tier is reordered.  The arbiter already lets a
chase point win every contest it enters; the measured problem was that no
chase point was ever entered for these bodies.

THE FREEZE IS NOT CHANGED
-------------------------
A body that is still ``frozen`` or still carrying a live ``freeze_timer`` is
NOT entitled: it stays pinned, exactly as before.  The entitlement exists to
make the EXPIRY hand-back a live command that SURVIVES, which is precisely
the frame range the probe measured as ``NO_PURSUIT_INTENT`` +
``retreat_non_compliant``.

SINGLE SOURCING (no drift possible)
-----------------------------------
  * the break test is the canonical
    ``movement_mode_decision.carrier_past_defensive_line`` (``bc.x >
    anchor_x + 1.0``) — the same predicate ``line_break_static_guard`` and
    ``freeze_expiry_pursuit`` use, so the three pursuit writers can never
    disagree about what a broken line is;
  * the owner gate is ``fullback_role.fullback_tackle_side_owner_live`` — a
    stamp is not ownership;
  * the speed is ``freeze_expiry_pursuit.defensive_ceiling_mps`` — the SAME
    governed ceiling every other recovery writer restores;
  * the exempt roles mirror
    ``line_break_static_guard._EXEMPT_ROLE_NAMES`` (markers / DH / TES
    tacklers / kick-chase), so the dedicated owners keep their contracts.

RELEASE / PTB SAFETY
--------------------
The break predicate requires ``bc.x > anchor_x + 1.0``.  At RELEASE and PTB
the anchor is ``ruck_x + 10`` and the carrier sits AT the ruck, so the
predicate is unsatisfiable by construction and no entitlement can fire there.
The retreat writer's exemption is therefore unreachable outside OPEN_PLAY.

IMPORT SAFETY
-------------
No module-scope PROJECT imports (``player.movement_states`` only, a leaf).
Every project reach is FUNCTION-LOCAL, so this module may be imported from any
defensive module in any load order with no cycle.
"""

from __future__ import annotations

import os

from player.movement_states import MovementState

__all__ = [
    "DEFENSIVE_CEILING_FALLBACK_MPS",
    "POST_MISS_PURSUIT_ARM_ATTR",
    "POST_MISS_PURSUIT_ENV_VAR",
    "POST_MISS_PURSUIT_SOURCE",
    "POST_MISS_PURSUIT_WINDOW_FRAMES",
    "arm_post_miss_pursuit",
    "clear_post_miss_pursuit",
    "defensive_ceiling_mps",
    "owned_bodies",
    "owns_post_miss_pursuit",
    "post_miss_pursuit_enabled",
    "post_miss_pursuit_until",
    "post_miss_pursuit_window_frames",
    "pursue_carrier",
]

# ── A-B / ROLLBACK LEVER ─────────────────────────────────────────────
# Read at consumption time, so flipping it off restores the pre-change
# engine exactly -- including the plain broken-tackle path's surviving
# commit latches and the retreat writer's unqualified authority -- with no
# code revert.  The same pattern as every other runtime lever here.
POST_MISS_PURSUIT_ENV_VAR: str = "RLM_POST_MISS_PURSUIT"

# The ``target_source`` stamp this module writes, so the writer-priority
# bands / target-write log / reactivity audit can attribute the frame.
POST_MISS_PURSUIT_SOURCE: str = "post_miss_pursuit"

# The per-body deadline attribute.  A frame number, like the MMDL's own
# ``_mmdl_state_until_frame``, so the window is deterministic and a fixture
# can be built without a clock.
POST_MISS_PURSUIT_ARM_ATTR: str = "_post_miss_pursuit_until_frame"

# Bounded window after the miss, in frames.  120 frames is 2.0 s at the
# engine's 60 Hz: comfortably past the 1.2 s bump-off freeze plus a pursuit
# beat, and short enough that a beaten body rejoins the line when the play
# has clearly moved on.
POST_MISS_PURSUIT_WINDOW_FRAMES: int = 120

# Env override for the window (frames), for A/B sweeps.
POST_MISS_PURSUIT_WINDOW_ENV_VAR: str = "RLM_POST_MISS_PURSUIT_FRAMES"

# Last-resort value for the governed defensive ceiling when the authority
# module cannot be reached.  It is the documented shipped default of
# ``DefenceTuners.max_defensive_speed_mps``; it is NOT a second declaration
# of the law -- ``freeze_expiry_pursuit`` is consulted first.
DEFENSIVE_CEILING_FALLBACK_MPS: float = 8.3

# Roles that never pursue: markers play tight to the ruck by design, the DH
# is an attacker, TACKLER_* are TES-owned, KICK_CHASE has its own contract.
# Mirrors ``line_break_static_guard._EXEMPT_ROLE_NAMES``.
_EXEMPT_ROLE_NAMES = frozenset({
    "MARKER_A",
    "MARKER_B",
    "DUMMY_HALF",
    "TACKLER_1",
    "TACKLER_2",
    "TACKLER_3",
    "KICK_CHASE",
})


def _role_name(d) -> str | None:
    """Return *d*'s defender-role name (or the value verbatim), else None."""
    role = getattr(d, "defender_role", None)
    if role is None:
        return None
    return getattr(role, "name", None) or str(role)


def post_miss_pursuit_enabled() -> bool:
    """True when a beaten body may be handed back as a live pursuer.

    Default ON.  ``RLM_POST_MISS_PURSUIT=0`` (or false/no/off/empty)
    disarms it: the miss teardown leaves the commit latches alone, no body is
    entitled, and the retreat authority keeps its unqualified contract -- the
    pre-change engine, byte for byte.
    """
    return os.environ.get(
        POST_MISS_PURSUIT_ENV_VAR, "1"
    ).strip().lower() not in ("0", "false", "no", "off", "")


def post_miss_pursuit_window_frames() -> int:
    """The entitlement window in frames (env overridable, never negative)."""
    raw = os.environ.get(POST_MISS_PURSUIT_WINDOW_ENV_VAR)
    if raw is None or not str(raw).strip():
        return POST_MISS_PURSUIT_WINDOW_FRAMES
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return POST_MISS_PURSUIT_WINDOW_FRAMES


def post_miss_pursuit_until(d) -> int | None:
    """The frame *d*'s entitlement expires on, or None when never armed."""
    value = getattr(d, POST_MISS_PURSUIT_ARM_ATTR, None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clear_post_miss_pursuit(d) -> None:
    """Drop *d*'s entitlement (play end / tackle start / admin reset)."""
    if d is None:
        return
    if hasattr(d, POST_MISS_PURSUIT_ARM_ATTR):
        try:
            delattr(d, POST_MISS_PURSUIT_ARM_ATTR)
        except AttributeError:
            pass
    if hasattr(d, "_post_miss_pursuit_bump_off"):
        try:
            delattr(d, "_post_miss_pursuit_bump_off")
        except AttributeError:
            pass


def is_exempt_role(d, world=None) -> bool:
    """True when *d* holds a dedicated movement owner and never pursues.

    ``world`` is optional and only ever consulted through the controller's
    recovery-ownership predicate (Stage 3), which reads the per-actor tag
    rather than the world, so an existing one-argument caller keeps its exact
    behaviour.
    """
    if d is None:
        return True
    # ── FULLBACK RECOVERY OWNERSHIP (Stage 3, 2026-09-30) ──
    # The tagged fullback owns its OWN recovery: its misses, owed-ground
    # travel and break re-engagement are ``fullback_role`` +
    # ``fullback_controller`` behaviours, so it must never be handed this
    # module's post-miss chase ENTITLEMENT.  The gate is the controller's own
    # ownership predicate -- the SAME one ``line_break_static_guard`` and
    # ``freeze_expiry_pursuit`` read -- with the historical tag test kept
    # beside it, so the denied set is identical with the recovery lever ON or
    # OFF (OFF simply leaves the tag test as the sole arbiter).
    try:
        from game.defence.defence_lvl_1.fullback_controller import (
            recovery_contract_owned as _recovery_owned,
        )
        _fb_owns_recovery = bool(_recovery_owned(world, d))
    except Exception:                                    # noqa: BLE001
        _fb_owns_recovery = False
    if _fb_owns_recovery or bool(getattr(d, "fullback_role_active", False)):
        return True
    return _role_name(d) in _EXEMPT_ROLE_NAMES


def arm_post_miss_pursuit(world, d, *, bump_off: bool = False) -> bool:
    """Entitle the beaten body *d* to pursue for the window.  Returns armed.

    Called by ``OpenPlayEngine._handle_broken_tackle`` on the beaten primary
    (both the plain broken-tackle and the BUMP_OFF variants) and, mirroring
    the existing ``_clear_bump_commit_lock`` scope, on the joiners of a
    bump-off.  Declines -- leaving the body exactly as the pre-change engine
    left it -- when the lever is disarmed, the body is None / the carrier /
    a dedicated owner, or the world cannot supply a frame number.
    """
    if not post_miss_pursuit_enabled():
        return False
    if d is None:
        return False
    bc = getattr(world, "ball_carrier", None)
    if bc is not None and d is bc:
        return False
    if is_exempt_role(d, world):
        return False
    frame = getattr(world, "frame_count", None)
    if frame is None:
        return False
    try:
        frame = int(frame)
    except (TypeError, ValueError):
        return False
    setattr(
        d,
        POST_MISS_PURSUIT_ARM_ATTR,
        frame + post_miss_pursuit_window_frames(),
    )
    setattr(d, "_post_miss_pursuit_bump_off", bool(bump_off))
    return True


def defensive_ceiling_mps(world=None, d=None) -> float:
    """The GOVERNED unified defensive ceiling (m/s) for a pursuit write.

    Delegates to ``freeze_expiry_pursuit.defensive_ceiling_mps`` so this
    writer and the expiry hand-back can never restore different paces.  The
    fallback is only reachable if that module cannot be imported at all.
    """
    try:
        from game.defence.defence_lvl_1.freeze_expiry_pursuit import (
            defensive_ceiling_mps as _ceiling,
        )
        return float(_ceiling(world, d))
    except Exception:
        return DEFENSIVE_CEILING_FALLBACK_MPS


def _carrier_past_line(world, bc) -> bool:
    """The canonical genuine-line-break predicate (single definition)."""
    try:
        from game.defence.defence_lvl_1.movement_mode_decision import (
            carrier_past_defensive_line as _past_line,
        )
    except Exception:
        return False
    try:
        return bool(_past_line(world, bc))
    except Exception:
        return False


def _tackle_side_owner_live(d) -> bool:
    """True when a tackle-side system is genuinely driving *d* this frame."""
    try:
        from game.defence.defence_lvl_1.fullback_role import (
            fullback_tackle_side_owner_live as _owner_live,
        )
    except Exception:
        return False
    try:
        return bool(_owner_live(d))
    except Exception:
        return False


def owns_post_miss_pursuit(world, d) -> bool:
    """True when *d* is entitled to pursue the live carrier this frame.

    This is THE predicate the other writers consult (the retreat authority
    exempts it, the MMDL skips it, the recovery writers may re-arm it).  It
    is a pure read of measured state:

      * the ``RLM_POST_MISS_PURSUIT`` lever is on and the window is live;
      * the body is NOT frozen and carries no live ``freeze_timer`` -- the
        freeze pin is a design choice and is left untouched;
      * the engine is not in TES (the engagement system owns every body);
      * a live ball carrier exists and the line break is GENUINE;
      * the body is a genuine line body (not a dedicated owner, not in
        contact / attached / tackle-state-held / pre-tackle-stanced);
      * no tackle-side system is actually driving it.
    """
    if d is None or not post_miss_pursuit_enabled():
        return False
    until = post_miss_pursuit_until(d)
    if until is None:
        return False
    frame = getattr(world, "frame_count", None)
    if frame is None:
        return False
    try:
        if int(frame) > until:
            return False
    except (TypeError, ValueError):
        return False

    if bool(getattr(d, "frozen", False)):
        return False
    if float(getattr(d, "freeze_timer", 0.0) or 0.0) > 0.0:
        return False
    if bool(getattr(world, "tes_active", False)):
        return False
    if is_exempt_role(d, world):
        return False
    for flag in (
        "tackle_state_active",
        "in_contact",
        "tes_attached",
        "pre_tackle_stance",
    ):
        if bool(getattr(d, flag, False)):
            return False
    if _tackle_side_owner_live(d):
        return False

    bc = getattr(world, "ball_carrier", None)
    if bc is None or d is bc:
        return False
    return _carrier_past_line(world, bc)


def owned_bodies(world, defenders) -> list:
    """Every body in *defenders* entitled to pursue this frame."""
    out: list = []
    for d in (defenders or ()):
        if owns_post_miss_pursuit(world, d):
            out.append(d)
    return out


def _clear_freeze_pin_latch(d) -> None:
    """Drop the engine's freeze-pin latch attributes on *d*.

    Delegated to ``freeze_expiry_pursuit`` so the two recovery writers share
    ONE definition of the latch; the inline fallback exists only so a broken
    import cannot leave a stale latch re-pinning the body on the compliance
    pass.
    """
    try:
        from game.defence.defence_lvl_1.freeze_expiry_pursuit import (
            _clear_freeze_pin_latch as _clear,
        )
        _clear(d)
        return
    except Exception:
        pass
    d._freeze_pin_active = False
    for attr in ("_freeze_pin_x", "_freeze_pin_y"):
        if hasattr(d, attr):
            try:
                delattr(d, attr)
            except AttributeError:
                pass


def pursue_carrier(world, d, bc=None) -> bool:
    """Write the live pursuit command for *d*.  Returns True when written.

    Writes (and nothing else): ``target_x`` / ``target_y`` /
    ``movement_state`` / the frame locks / ``move_speed`` / ``target_source``
    / ``_target_writer``.  Returns False -- leaving the caller's target
    untouched -- when the body is not entitled.

    The body is handed back to the engine's own integration loop as a live
    pursuer: CHASE is a non-stabilised, non-smooth-damp state, so the
    accel-lerp runs on the raw carrier point at the governed ceiling.
    """
    if not owns_post_miss_pursuit(world, d):
        return False
    if bc is None:
        bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return False

    try:
        d.target_x = float(bc.x)
        d.target_y = float(bc.y)
    except (TypeError, ValueError):
        return False

    ceiling = defensive_ceiling_mps(world, d)
    d.movement_state = MovementState.CHASE
    d.timing_locked = False
    d.reaction_timer = 0.0
    d.retreat_delay = 0.0
    d.lateral_hesitation = 0.0
    d.step_stagger = 0.0
    d.step_stagger_active = False
    d.move_speed = min(
        float(getattr(d, "max_speed_mps", ceiling) or ceiling), ceiling
    )
    _clear_freeze_pin_latch(d)
    d.target_source = POST_MISS_PURSUIT_SOURCE
    d._target_writer = POST_MISS_PURSUIT_SOURCE
    try:
        from game.debug.target_write_log import stamp_target_write
        stamp_target_write(
            world, d, "post_miss_pursuit", detail=POST_MISS_PURSUIT_SOURCE,
        )
    except Exception:
        pass
    return True
