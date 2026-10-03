"""
game/debug/fullback_audit.py — Ship-ready defence + fullback audit.

Frame-accurate diagnostic that answers, for the CURRENT engine (not the
fictional fields in the original spec):

  - who the engine thinks is in the defensive line
  - who the engine thinks is the fullback (fullback_role_active)
  - what anchor the fullback is fed (target_x/target_y + world.fullback_*)
  - which defenders are contaminating the geometry (lane_index / depth_class /
    target_source / movement_writer)
  - which flags are leaking (frozen, arrival_mode, tes_attached, is_tackler,
    tes_tackler_role, in_contact)
  - whether kickoff is actually clean (phase / in_kickoff /
    defence_ran_this_frame)
  - whether in_kickoff is true
  - whether defensive_line_anchor is sane (None during kickoff!)
  - whether lane classification is polluted (per-defender lane_index + the
    writer that set the target)

WIRING (BOTH call sites required for a complete picture):
  1. KickoffEngine.update()  — every KICKOFF frame (ball in flight).
  2. OpenPlayEngine._update_defence() — every frame defence AI actually runs
     (post-catch OPEN_PLAY).

SELF-LIMITING: prints at most AUDIT_PER_PHASE_FRAMES frames per game phase
(KICKOFF, OPEN_PLAY) across AUDIT_MAX_PHASES distinct phases, then goes
silent.  Call rearm_fullback_audit() to re-arm for another capture run.
"""

import math

from game.defence.defence_lvl_1.fullback_role import (
    _is_available,
    compute_fullback_anchor,
)

# ── Audit gates ───────────────────────────────────────────────────────
DEBUG_FULLBACK_AUDIT: bool = True
AUDIT_PER_PHASE_FRAMES: int = 5
AUDIT_MAX_PHASES: int = 4

_printed_per_phase: dict[str, int] = {}
_phases_printed: int = 0


def rearm_fullback_audit() -> None:
    """Reset the per-phase print budget for another capture run."""
    global _printed_per_phase, _phases_printed
    _printed_per_phase = {}
    _phases_printed = 0


def _format_player_id(player) -> str:
    pid = getattr(player, "player_id", None)
    return f"#{pid}" if pid is not None else "#?"


def audit_fullback_state(world):
    """Print one frame of the defence + fullback audit (self-limiting)."""
    global _phases_printed

    if not DEBUG_FULLBACK_AUDIT:
        return

    raw_phase = getattr(world, "phase", None)
    phase_key = getattr(raw_phase, "value", None) or str(raw_phase)
    if _phases_printed >= AUDIT_MAX_PHASES:
        return
    if _printed_per_phase.get(phase_key, 0) >= AUDIT_PER_PHASE_FRAMES:
        return
    _printed_per_phase[phase_key] = _printed_per_phase.get(phase_key, 0) + 1
    if _printed_per_phase[phase_key] == 1:
        _phases_printed += 1

    ope = getattr(world, "open_play_engine", None)
    defenders = getattr(world, "defending_team", []) or []
    bc = getattr(world, "ball_carrier", None)
    ball = getattr(world, "ball", None)
    anchor = getattr(world, "defensive_line_anchor", None)

    print("\n================ DEFENCE + FULLBACK AUDIT ================")
    print(f"frame={getattr(world, 'frame_count', None)}  phase={phase_key}  "
          f"sub_state={getattr(world, 'sub_state', None)}")
    print(f"in_kickoff={getattr(world, 'in_kickoff', None)}")
    print(f"defence_ran_this_frame={getattr(world, 'defence_ran_this_frame', None)}")
    print(f"in_pursuit_mode={getattr(world, 'in_pursuit_mode', None)}")
    print(f"ball_carrier={_format_player_id(bc) if bc is not None else None}")
    if ball is not None:
        print(f"ball: x={ball.x_m:.2f} y={ball.y_m:.2f}")
    else:
        print("ball: None")
    print(f"defensive_line_anchor={anchor}  ruck_x_m={getattr(world, 'ruck_x_m', None)}")
    print(f"published fullback_actor_id={getattr(world, 'fullback_actor_id', None)} "
          f"anchor=({getattr(world, 'fullback_anchor_x', None)}, "
          f"{getattr(world, 'fullback_anchor_y', None)})")
    if ope is not None:
        print(f"ope._phase={getattr(ope, '_phase', None)}  "
              f"_phase_timer={getattr(ope, '_phase_timer', 0.0):.3f}  "
              f"_suppress_pre_tackle={getattr(ope, '_suppress_pre_tackle', False)}")

    # What the kickoff fullback branch SHOULD have used.  The kickoff branch in
    # DefensiveBehaviourUnified.apply() reads world.defensive_line_anchor; at
    # kickoff that value is None (reset_for_kickoff), so KMC derives its own
    # local ball_x+10 that is never written to the world — and the fullback
    # override is unreachable because OPE/defence never runs during KICKOFF.
    if anchor is None:
        kick_anchor = (ball.x_m if ball is not None else 60.0) + 10.0
        print(f"[kickoff branch would use world.defensive_line_anchor "
              f"-> None; KMC local fallback = ball_x+10 = {kick_anchor:.2f}]")
    else:
        # KICKOFF FB OVERRIDE (2026-08-11): the anchor is the flat
        # formation line + side_sign offset, never the ball-derived
        # defensive_line_anchor.  side_sign comes from world.kickoff_side_sign.
        _side = getattr(world, "kickoff_side_sign", 1)
        kx, ky = compute_fullback_anchor(
            ball,
            anchor,
            in_kickoff=True,
            formation_line_x=getattr(world, "kickoff_engine_line_x", None),
            side_sign=_side,
        )
        print(f"would-be kickoff anchor (compute_fullback_anchor): ({kx:.2f}, {ky:.2f}) "
              f"side_sign={_side:+d} kickoff_line_x="
              f"{getattr(world, 'kickoff_engine_line_x', None)}")

    print("\n-- FULLBACK AUDIT STATE (Task J, 2026-08-12) --")
    _fb_aud = None
    for d in defenders:
        if getattr(d, "fullback_role_active", False):
            _fb_aud = d
            break
    if _fb_aud is not None:
        print(
            f"FB_STATE id={getattr(_fb_aud, 'player_id', None)} "
            f"jersey={getattr(_fb_aud, 'jersey_number', None)} "
            f"active={getattr(_fb_aud, 'fullback_role_active', False)} "
            f"arrived={getattr(_fb_aud, 'fullback_arrived', False)} "
            f"absorb_ok={getattr(_fb_aud, 'fullback_absorb_allowed', False)} "
            f"fx={getattr(_fb_aud, 'frames_within_absorb_x', 0)} "
            f"fy={getattr(_fb_aud, 'frames_within_absorb_y', 0)}"
        )
    else:
        print(
            f"FB_STATE id=None active=False arrived=False "
            f"absorb_ok=False fx=0 fy=0"
        )

    print("\n-- DEFENDERS (world.defending_team) --")
    for d in defenders:
        role = getattr(d, "defender_role", None)
        role_name = getattr(role, "name", None)
        ms = getattr(d, "movement_state", None)
        ms_name = getattr(ms, "name", None) or str(ms)
        anchor_x = anchor if anchor is not None else 60.0

        # Re-run the REAL selection gates (in _is_available's order) to say
        # exactly WHY each defender is rejected as the fullback actor.
        reject_reason = None
        if not _is_available(d, anchor_x, 35.0):
            if getattr(d, "frozen", False):
                reject_reason = "frozen"
            elif getattr(d, "tes_attached", False) or getattr(d, "in_contact", False):
                reject_reason = "attached/in_contact"
            elif getattr(d, "is_tackler", False):
                reject_reason = "is_tackler"
            elif getattr(d, "arrival_mode", False):
                reject_reason = "arrival_mode"
            elif role_name in ("MARKER_A", "MARKER_B", "DUMMY_HALF",
                               "TACKLER_1", "TACKLER_2", "TACKLER_3"):
                reject_reason = f"role={role_name}"
            else:
                dist = math.hypot(d.x - anchor_x, d.y - 35.0)
                reject_reason = f"dist_to_anchor={dist:.2f} > 25"

        # FULLBACK VISIBILITY (2026-08-11): the depth-role system excludes
        # the fullback actor (assign_depth_roles filters fullback_role_active),
        # so its depth_role is None by construction — printing the raw value
        # shows "None" and makes the last defender invisible in the trace.
        # Render "FB" in the depth-role cell instead, mirroring
        # debug_render_defensive_line().
        depth_cell = "FB" if getattr(d, "fullback_role_active", False) else getattr(d, "depth_role", None)
        print(
            f"id={_format_player_id(d)} x={d.x:6.2f} y={d.y:5.2f}  "
            f"role={str(role_name):12s} depth_role={str(depth_cell):4s} "
            f"depth_class={str(getattr(d, 'depth_class', None)):9s} "
            f"tackle_role={getattr(d, 'tackle_role', None)} "
            f"lane={getattr(d, 'lane_index', None)} move_state={ms_name:8s} "
            f"target_source={getattr(d, 'target_source', None)} "
            f"writer={getattr(d, 'movement_writer', None)} "
            f"target=({d.target_x:.2f},{d.target_y:.2f}) "
            f"frozen={getattr(d, 'frozen', False)} "
            f"freeze_t={getattr(d, 'freeze_timer', 0.0):.2f} "
            f"arrival={getattr(d, 'arrival_mode', False)} "
            f"tes_attached={getattr(d, 'tes_attached', False)} "
            f"is_tackler={getattr(d, 'is_tackler', False)} "
            f"in_contact={getattr(d, 'in_contact', False)} "
            f"tes_tackler_role={getattr(d, 'tes_tackler_role', None)} "
            f"fb_active={getattr(d, 'fullback_role_active', False)} "
            f"-> reject: {reject_reason}"
        )

    print("\n-- LINE SNAPSHOT (Y-sorted) --")
    for d in sorted(defenders, key=lambda p: p.y):
        role = getattr(d, "defender_role", None)
        # FULLBACK VISIBILITY (2026-08-11): render the depth-role cell as
        # "FB" on the actor so the last defender is visible in the snapshot.
        depth_cell = "FB" if getattr(d, "fullback_role_active", False) else getattr(d, "depth_role", None)
        print(
            f"  {_format_player_id(d)} y={d.y:6.2f} "
            f"role={getattr(role, 'name', None)} "
            f"depth={str(depth_cell):4s} "
            f"depth_class={getattr(d, 'depth_class', None)} "
            f"lane={getattr(d, 'lane_index', None)} "
            f"fb={getattr(d, 'fullback_role_active', False)}"
        )

    print("===========================================================\n")
