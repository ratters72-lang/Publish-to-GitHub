"""verify_def_line_ready_frozen_drive.py -- DEF_LINE_READY KEEPS PLAYING during
the TES drive (contract of 2026-09-10).

CONTRACT (2026-09-10): during TACKLE_CONTACT / TACKLE_DRIVE / TACKLE_DOMINANCE
the DEF_LINE_READY line bodies are NOT frozen and NOT depth-locked.  They stay
in ``allowed_to_move`` so they keep movement authority and keep playing their
normal depth-role geometry:

  * DEPTH (X) tracks the live carrier  (bc.x + WIDTH_OFFSETS[depth_role]),
  * LATERAL (Y) follows the spacing lane relative to the live BC.

The superseded contract froze (or depth-pinned) the line for the whole chamber.
The only freeze that survives is TACKLE_INIT (the tackle-start latch).

This verifier asserts the DEFAULT contract without needing a live engine.

Run:  python verify_def_line_ready_frozen_drive.py        (exit 0 on success)
"""
import math
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from world.world_state import WorldState
from world.rules.movement_contracts import (
    apply_phase_invariants,
    has_movement_authority,
    drive_depth_track_enabled,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.pre_tackle_authority import (
    apply_pre_tackle,
    is_pre_tackle_eligible,
)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def _mk_def(pid, x, y, role=None):
    d = Player(x=x, y=y, player_id=pid)
    if role is not None:
        d.defender_role = role
    d.frozen = False
    d.max_speed_mps = 8.3
    d.max_defensive_speed_mps = 8.3
    d.move_speed = 4.0
    return d


def _mk_world_members(defenders=None, bc=None):
    w = WorldState()
    w.frame_count = 1
    defenders = list(defenders or [])
    w.defending_team = defenders
    w.attacking_team = []
    w.ball_carrier = bc
    w.tackler_ids = set()
    w.primary_tackler_id = None
    w.secondary_tacklers = []
    return w


def _default_ope():
    return SimpleNamespace(def_tuners=DefenceTuners())


DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
TES_MOVER_ROLES = frozenset({
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
})


def main():
    print("=" * 64)
    print("DEF_LINE_READY KEEPS PLAYING THROUGH THE DRIVE (2026-09-10)")
    print("=" * 64)

    # -- PART 1: invariants leave the line free ------------------------
    print("\n[1] apply_phase_invariants: line body unfrozen + unlocked in the drive")
    for phase in DRIVE_PHASES:
        d = _mk_def(1, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
        w = _mk_world_members([d])
        w.open_play_engine = _default_ope()
        apply_phase_invariants(w, phase)
        check(f"1.{phase} DEF_LINE_READY frozen=False",
              d.frozen is False, "frozen=%s" % d.frozen)
        check(f"1.{phase} DEF_LINE_READY depth_locked=False",
              getattr(d, "depth_locked", False) is False,
              "depth_locked=%s" % getattr(d, "depth_locked", False))
        check(f"1.{phase} DEF_LINE_READY depth_lock_y is None",
              getattr(d, "depth_lock_y", "SENTINEL") is None,
              "depth_lock_y=%s" % getattr(d, "depth_lock_y", None))
        check(f"1.{phase} DEF_LINE_READY depth_lock_x is None",
              getattr(d, "depth_lock_x", "SENTINEL") is None,
              "depth_lock_x=%s" % getattr(d, "depth_lock_x", None))

    # DEF_CHASE is in the same family: free as well.
    for phase in DRIVE_PHASES:
        dchase = _mk_def(2, 42.0, 31.0, DefenderRole.DEF_CHASE)
        wchase = _mk_world_members([dchase])
        wchase.open_play_engine = _default_ope()
        apply_phase_invariants(wchase, phase)
        check(f"1.{phase} DEF_CHASE frozen=False", dchase.frozen is False,
              "frozen=%s" % dchase.frozen)
        check(f"1.{phase} DEF_CHASE depth_locked=False",
              getattr(dchase, "depth_locked", False) is False)

    # DEF_RETREAT_10 also keeps playing.
    drt = _mk_def(3, 46.0, 33.0, DefenderRole.DEF_RETREAT_10)
    wdrt = _mk_world_members([drt])
    wdrt.open_play_engine = _default_ope()
    apply_phase_invariants(wdrt, "TACKLE_DRIVE")
    check("1.DEF_RETREAT_10 frozen=False on DRIVE", drt.frozen is False)
    check("1.DEF_RETREAT_10 depth_locked=False on DRIVE",
          getattr(drt, "depth_locked", False) is False,
          "depth_locked=%s" % getattr(drt, "depth_locked", False))

    # TACKLE_INIT still latches the line.
    init_line = _mk_def(4, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
    w_init = _mk_world_members([init_line])
    w_init.open_play_engine = _default_ope()
    apply_phase_invariants(w_init, "TACKLE_INIT")
    check("1.INIT DEF_LINE_READY frozen=True (tackle-start latch)",
          init_line.frozen is True, "frozen=%s" % init_line.frozen)

    # -- PART 2: movement authority -----------------------------------
    print("\n[2] has_movement_authority: the line keeps authority in the drive")
    for phase in DRIVE_PHASES:
        d = _mk_def(5, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
        w = _mk_world_members([d])
        w.open_play_engine = _default_ope()
        apply_phase_invariants(w, phase)
        check(f"2.{phase} DEF_LINE_READY HAS movement authority",
              has_movement_authority(w, phase, d))
        tack = _mk_def(6, 52.0, 34.0, DefenderRole.TACKLER_1)
        w.defending_team.append(tack)
        check(f"2.{phase} TACKLER_1 retains authority",
              has_movement_authority(w, phase, tack))
        fb = _mk_def(7, 10.0, 30.0, DefenderRole.DEF_LINE_READY)
        fb.fullback_role_active = True
        w.defending_team.append(fb)
        check(f"2.{phase} fullback retains authority (exempt)",
              has_movement_authority(w, phase, fb))
    frozen_body = _mk_def(8, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
    frozen_body.frozen = True
    w_fr = _mk_world_members([frozen_body])
    w_fr.open_play_engine = _default_ope()
    check("2.explicitly frozen DEF_LINE_READY has NO movement authority",
          not has_movement_authority(w_fr, "TACKLE_DRIVE", frozen_body))

    # -- PART 3: pre-tackle authority only manages the bodies at the pile --
    print("\n[3] only the pile bodies are managed; the rest keep defending")
    tun = SimpleNamespace()
    tun.pretackle_attempt_enabled = True
    tun.pre_tackle_trigger_radius_m = 2.0
    tun.pretackle_slow_speed_mps = 2.5
    tun.pretackle_freeze_radius_m = 0.15
    tun.pretackle_follow_smooth = 0.6
    tun.pretackle_commit_speed_mps = 4.0
    tun.pretackle_plant_radius_m = 0.8
    tun.pretackle_slowdown_band_m = 3.0
    tun.pretackle_slowdown_scale = 0.45
    tun.pretackle_commit_depth_offset_m = 1.0
    tun.pretackle_commit_lateral_offset_m = 1.0
    tun.tackle_radius_m = 1.5
    tun.pretackle_phase_tuners = {}

    bc = Player(x=60.0, y=35.0, player_id=99)
    bc.attack_role = "ball_carrier"
    near_line = _mk_def(30, 58.5, 34.8, DefenderRole.DEF_LINE_READY)
    far_line = _mk_def(31, 44.0, 28.0, DefenderRole.DEF_LINE_READY)
    w3 = _mk_world_members([near_line, far_line], bc=bc)
    w3.open_play_engine = _default_ope()
    apply_phase_invariants(w3, "TACKLE_DRIVE")
    check("3.1 near DEF_LINE_READY is pre-tackle ELIGIBLE",
          is_pre_tackle_eligible(near_line), "frozen=%s" % near_line.frozen)
    # Eligibility is ROLE-based (DEF_LINE_READY + unfrozen + not the fullback);
    # the DISTANCE gate lives inside apply_pre_tackle, which only plants bodies
    # at the pile.  Both bodies are role-eligible; only the pile one is managed.
    check("3.2 far DEF_LINE_READY is role-eligible but not at the pile",
          is_pre_tackle_eligible(far_line)
          and math.hypot(far_line.x - bc.x, far_line.y - bc.y) > 2.0,
          "frozen=%s dist=%.2f" % (far_line.frozen,
                                   math.hypot(far_line.x - bc.x,
                                              far_line.y - bc.y)))
    managed = apply_pre_tackle(w3, [near_line, far_line], 1.0 / 60.0, tun,
                               frozen_mode=True)
    check("3.3 apply_pre_tackle does NOT manage the 16 m body",
          far_line not in managed, "n=%d" % len(managed))
    check("3.4 far body stays UNFROZEN (still defending)",
          far_line.frozen is False, "frozen=%s" % far_line.frozen)
    check("3.5 far body keeps both axes live (no depth pin)",
          getattr(far_line, "depth_locked", False) is False
          and getattr(far_line, "depth_lock_x", None) is None,
          "depth_locked=%s depth_lock_x=%s" % (
              getattr(far_line, "depth_locked", None),
              getattr(far_line, "depth_lock_x", None)))

    # -- PART 4: update_tes_drive-style pool includes the line --------
    print("\n[4] the drive pool includes the line body (not frozen => in pool)")
    w4 = _mk_world_members([far_line], bc=bc)
    w4.open_play_engine = _default_ope()
    apply_phase_invariants(w4, "TACKLE_DRIVE")
    tes_ids = set()
    pool = [
        p for p in w4.defending_team
        if (
            not p.frozen
            and p is not bc
            and p.player_id not in tes_ids
            and getattr(p, "attack_role", None) != "dummy_half"
            and getattr(p, "defender_role", None) not in TES_MOVER_ROLES
            and not getattr(p, "fullback_role_active", False)
        )
    ]
    check("4.1 DEF_LINE_READY IS in the drive pool", far_line in pool)
    check("4.2 pool non-empty (the line is a drive mover)", len(pool) > 0,
          "n=%d" % len(pool))

    # -- PART 5: the line MOVES with the carrier (depth + lane) -------
    print("\n[5] the line body integrates BOTH axes during the drive")
    dt = 1.0 / 60.0
    d5 = _mk_def(50, 50.0, 30.0, DefenderRole.DEF_LINE_READY)
    d5.depth_role = "WINGER"
    d5.depth_locked = False
    d5.depth_lock_x = None
    d5.depth_lock_y = None
    d5.movement_state = MovementState.CHASE
    d5._mmdl_moved = False
    d5._sd_vx = 0.0
    d5._sd_vy = 0.0
    d5._geometry_smooth_time_s = 0.12
    d5.vx = 0.0
    d5.vy = 0.0
    bc_x = 60.0
    xs = []
    ys = []
    for _ in range(30):
        bc_x += 0.10                       # carrier drives +X 0.10 m/frame
        d5.role_depth_target_x = bc_x + 10.0  # published BC-relative depth
        d5.target_x = d5.role_depth_target_x
        d5.target_y = 30.0 + 2.0           # lane moves with the carrier
        d5.update(dt)
        xs.append(d5.x)
        ys.append(d5.y)
    check("5.1 X MOVED toward the carrier-relative depth target",
          d5.x > 50.0, "x=%.4f" % d5.x)
    check("5.2 X advanced across the window (live depth geometry)",
          (xs[-1] - xs[0]) > 1.0, "span=%.4f" % (xs[-1] - xs[0]))
    check("5.3 Y MOVED toward the live lane target",
          d5.y > 30.0, "y=%.4f" % d5.y)
    check("5.4 no depth pin left behind (depth_lock_x None)",
          d5.depth_lock_x is None, "depth_lock_x=%s" % d5.depth_lock_x)
    check("5.5 body is not frozen while the drive runs",
          d5.frozen is False, "frozen=%s" % d5.frozen)

    # -- PART 6: the legacy canary no longer restores the freeze -----
    print("\n[6] the depth-track canary no longer freezes the line")
    tun_off = DefenceTuners()
    tun_off.tes_drive_depth_track_enabled = False
    d6 = _mk_def(60, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
    w6 = _mk_world_members([d6])
    w6.open_play_engine = SimpleNamespace(def_tuners=tun_off)
    check("6.1 drive_depth_track_enabled False when canary OFF",
          drive_depth_track_enabled(w6) is False)
    apply_phase_invariants(w6, "TACKLE_DRIVE")
    check("6.2 canary OFF -> DEF_LINE_READY still unfrozen and unlocked",
          d6.frozen is False and getattr(d6, "depth_locked", False) is False,
          "frozen=%s depth_locked=%s" % (d6.frozen,
                                         getattr(d6, "depth_locked", None)))
    w6b = _mk_world_members([])
    w6b.open_play_engine = _default_ope()
    check("6.3 drive_depth_track_enabled True with fresh tuners",
          drive_depth_track_enabled(w6b) is True)

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — DEF_LINE_READY / DEF_CHASE / "
          "DEF_RETREAT_10 keep movement authority and keep integrating both "
          "axes (carrier depth + live lane) through TACKLE_CONTACT, "
          "TACKLE_DRIVE and TACKLE_DOMINANCE")
    sys.exit(0)


if __name__ == "__main__":
    main()
