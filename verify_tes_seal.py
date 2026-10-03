"""verify_tes_seal.py -- TES DRIVE MOVEMENT-AUTHORITY verifier (2026-09-10).

The TES chamber (TACKLE_INIT / TACKLE_CONTACT / TACKLE_DRIVE / TACKLE_DOMINANCE)
no longer seals the defensive line.  The superseded contract (DEPTH-LOCK v2 /
DRIVE-DEPTH TRACK) hard-froze or depth-pinned DEF_LINE_READY / DEF_CHASE and
DEF_RETREAT_10 for the whole chamber; this verifier asserts the replacement:

  - During CONTACT / DRIVE / DOMINANCE the ONLY frozen roles are the attacking
    support runners and unclassified bodies (ATT_SUPPORT / NONE).  Every
    defensive role keeps movement authority:
        * DEF_LINE_READY  -- spacing / depth-role pipeline keeps running,
        * DEF_CHASE       -- chase-to-carrier keeps running,
        * DEF_RETREAT_10  -- retreat-to-10 rollout keeps running.
    None of them is depth-locked, so both axes (X depth relative to the driven
    carrier, Y spacing lane) are live geometry every frame.
  - TACKLE_INIT keeps the one-frame hard-freeze latch for the line roles
    (DEF_LINE_READY / DEF_CHASE) and the depth lock for DEF_RETREAT_10, so the
    tackle-start frame is still authored by TES.
  - TACKLER_1/2/3, ATT_CARRIER and DUMMY_HALF retain full movement authority
    across the whole chamber.
  - Leaving TES (RELEASE) re-freezes the line (it holds its shape for the PTB)
    and leaves DEF_RETREAT_10 free.

This verifier asserts the contract WITHOUT needing a live engine.

Run:  python verify_tes_seal.py        (exit 0 on success)
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from world.world_state import WorldState
from world.rules.movement_contracts import (
    MOVEMENT_RULES,
    Role,
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


def _rules(phase):
    return MOVEMENT_RULES[phase]


def _in_set(role, phase_set):
    return role in phase_set


DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
INIT_PHASE = "TACKLE_INIT"
MOVING_ROLES = (Role.DEF_LINE_READY, Role.DEF_CHASE, Role.DEF_RETREAT_10)
AUTHORISED_ROLES = (Role.ATT_CARRIER, Role.TACKLER_1, Role.TACKLER_2,
                    Role.TACKLER_3, Role.DUMMY_HALF)
DRIVE_FROZEN_ROLES = (Role.ATT_SUPPORT, Role.NONE)


def _mk_def(pid, x, y, role=None):
    d = Player(x=x, y=y, player_id=pid)
    if role is not None:
        d.defender_role = role
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


def _mk_ope(track=True):
    tun = DefenceTuners()
    tun.tes_drive_depth_track_enabled = track
    return SimpleNamespace(def_tuners=tun)


def main():
    print("=" * 64)
    print("TES DRIVE MOVEMENT-AUTHORITY VERIFIER (2026-09-10)")
    print("=" * 64)

    # -- PART 1: MOVEMENT_RULES -- line + retreat move in the drive ------
    print("\n[1] MOVEMENT_RULES: DEF_LINE_READY / DEF_CHASE / DEF_RETREAT_10 move in drive")
    for phase in DRIVE_PHASES:
        r = _rules(phase)
        for role in MOVING_ROLES:
            check(f"1.{role.name} NOT in must_be_frozen for {phase}",
                  not _in_set(role, r["must_be_frozen"]),
                  "still must_be_frozen")
            check(f"1.{role.name} NOT in must_lock_depth for {phase}",
                  not _in_set(role, r["must_lock_depth"]),
                  "still must_lock_depth")
            check(f"1.{role.name} in allowed_to_move for {phase}",
                  _in_set(role, r["allowed_to_move"]),
                  "missing from allowed_to_move")
        for role in AUTHORISED_ROLES:
            check(f"1.{role.name} allowed_to_move for {phase}",
                  _in_set(role, r["allowed_to_move"]))
        for role in DRIVE_FROZEN_ROLES:
            check(f"1.{role.name} in must_be_frozen for {phase}",
                  _in_set(role, r["must_be_frozen"]),
                  "not frozen in the drive")

    r_init = _rules(INIT_PHASE)
    for role in (Role.DEF_LINE_READY, Role.DEF_CHASE):
        check(f"1.{role.name} in must_be_frozen for TACKLE_INIT",
              _in_set(role, r_init["must_be_frozen"]),
              "not must_be_frozen in INIT")
        check(f"1.{role.name} NOT in allowed_to_move for TACKLE_INIT",
              not _in_set(role, r_init["allowed_to_move"]))
    check("1.DEF_RETREAT_10 in must_lock_depth for TACKLE_INIT",
          _in_set(Role.DEF_RETREAT_10, r_init["must_lock_depth"]))
    check("1.DEF_RETREAT_10 NOT in must_be_frozen for TACKLE_INIT",
          not _in_set(Role.DEF_RETREAT_10, r_init["must_be_frozen"]))

    # -- PART 2: apply_phase_invariants -- drive leaves the line free ----
    print("\n[2] apply_phase_invariants: no freeze and no depth lock in the drive")

    line_body = _mk_def(1, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
    line_body.frozen = False
    w2 = _mk_world_members([line_body])
    w2.open_play_engine = _mk_ope(True)
    apply_phase_invariants(w2, "TACKLE_DRIVE")
    check("2.1 DEF_LINE_READY frozen=False on DRIVE entry",
          line_body.frozen is False, "frozen=%s" % line_body.frozen)
    check("2.2 DEF_LINE_READY depth_locked=False on DRIVE entry",
          getattr(line_body, "depth_locked", False) is False,
          "depth_locked=%s" % getattr(line_body, "depth_locked", False))
    check("2.3 DEF_LINE_READY depth_lock_y is None (Y lane live)",
          getattr(line_body, "depth_lock_y", "SENTINEL") is None,
          "depth_lock_y=%s" % getattr(line_body, "depth_lock_y", None))
    check("2.4 DEF_LINE_READY depth_lock_x is None (X depth live)",
          getattr(line_body, "depth_lock_x", "SENTINEL") is None,
          "depth_lock_x=%s" % getattr(line_body, "depth_lock_x", None))

    chase_body = _mk_def(2, 42.0, 31.0, DefenderRole.DEF_CHASE)
    chase_body.frozen = False
    w2b = _mk_world_members([chase_body])
    w2b.open_play_engine = _mk_ope(True)
    apply_phase_invariants(w2b, "TACKLE_CONTACT")
    check("2.5 DEF_CHASE frozen=False on CONTACT entry",
          chase_body.frozen is False, "frozen=%s" % chase_body.frozen)
    check("2.6 DEF_CHASE depth_locked=False on CONTACT entry",
          getattr(chase_body, "depth_locked", False) is False,
          "depth_locked=%s" % getattr(chase_body, "depth_locked", False))

    retreat = _mk_def(3, 46.0, 33.0, DefenderRole.DEF_RETREAT_10)
    retreat.frozen = False
    w2c = _mk_world_members([retreat])
    w2c.open_play_engine = _mk_ope(True)
    apply_phase_invariants(w2c, "TACKLE_DRIVE")
    check("2.7 DEF_RETREAT_10 frozen=False on DRIVE entry",
          retreat.frozen is False, "frozen=%s" % retreat.frozen)
    check("2.8 DEF_RETREAT_10 depth_locked=False on DRIVE entry",
          getattr(retreat, "depth_locked", False) is False,
          "depth_locked=%s" % getattr(retreat, "depth_locked", False))

    apply_phase_invariants(w2, "TACKLE_DRIVE")
    check("2.9 second DRIVE apply keeps DEF_LINE_READY unfrozen",
          line_body.frozen is False, "frozen=%s" % line_body.frozen)
    check("2.10 second DRIVE apply keeps DEF_LINE_READY unlocked",
          getattr(line_body, "depth_locked", False) is False,
          "depth_locked=%s" % getattr(line_body, "depth_locked", False))

    thaw_body = _mk_def(9, 45.0, 32.0, DefenderRole.DEF_LINE_READY)
    thaw_body.frozen = True
    w2h = _mk_world_members([thaw_body])
    w2h.open_play_engine = _mk_ope(True)
    apply_phase_invariants(w2h, "TACKLE_DOMINANCE")
    check("2.11 DRIVE thaws a stale-frozen DEF_LINE_READY",
          thaw_body.frozen is False, "frozen=%s" % thaw_body.frozen)

    line_init = _mk_def(4, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
    line_init.frozen = False
    w2d = _mk_world_members([line_init])
    w2d.open_play_engine = _mk_ope(True)
    apply_phase_invariants(w2d, "TACKLE_INIT")
    check("2.12 DEF_LINE_READY frozen=True on TACKLE_INIT entry",
          line_init.frozen is True, "frozen=%s" % line_init.frozen)
    check("2.13 DEF_LINE_READY NOT depth-locked on TACKLE_INIT entry",
          getattr(line_init, "depth_locked", False) is False,
          "depth_locked=%s" % getattr(line_init, "depth_locked", False))

    apply_phase_invariants(w2, "RELEASE")
    check("2.14 DEF_LINE_READY frozen in RELEASE (line holds)",
          line_body.frozen is True, "frozen=%s" % line_body.frozen)
    apply_phase_invariants(w2c, "RELEASE")
    check("2.15 DEF_RETREAT_10 unfrozen on RELEASE (wall reforms)",
          retreat.frozen is False, "frozen=%s" % retreat.frozen)

    # -- PART 3: the legacy canary no longer re-freezes the drive --------
    print("\n[3] legacy depth-track canary does NOT restore the drive freeze")
    line_off = _mk_def(5, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
    line_off.frozen = False
    w3 = _mk_world_members([line_off])
    w3.open_play_engine = _mk_ope(False)
    check("3.1 drive_depth_track_enabled False when canary OFF",
          drive_depth_track_enabled(w3) is False)
    apply_phase_invariants(w3, "TACKLE_DRIVE")
    check("3.2 canary OFF -> DEF_LINE_READY still frozen=False on DRIVE",
          line_off.frozen is False, "frozen=%s" % line_off.frozen)
    check("3.3 canary OFF -> DEF_LINE_READY still unlocked on DRIVE",
          getattr(line_off, "depth_locked", False) is False,
          "depth_locked=%s" % getattr(line_off, "depth_locked", False))

    retreat_off = _mk_def(6, 46.0, 33.0, DefenderRole.DEF_RETREAT_10)
    retreat_off.frozen = False
    w3b = _mk_world_members([retreat_off])
    w3b.open_play_engine = _mk_ope(False)
    apply_phase_invariants(w3b, "TACKLE_DRIVE")
    check("3.4 canary OFF -> DEF_RETREAT_10 unfrozen on DRIVE",
          retreat_off.frozen is False, "frozen=%s" % retreat_off.frozen)

    w3c = _mk_world_members([])
    w3c.open_play_engine = _mk_ope(True)
    check("3.5 drive_depth_track_enabled True when canary ON",
          drive_depth_track_enabled(w3c) is True)
    w3d = _mk_world_members([])
    check("3.6 drive_depth_track_enabled True with no ope (default)",
          drive_depth_track_enabled(w3d) is True)
    w3e = _mk_world_members([])
    w3e.open_play_engine = SimpleNamespace(def_tuners=DefenceTuners())
    check("3.7 fresh DefenceTuners defaults drive_depth_track_enabled True",
          drive_depth_track_enabled(w3e) is True)

    line_default = _mk_def(7, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
    line_default.frozen = False
    w3f = _mk_world_members([line_default])
    w3f.open_play_engine = SimpleNamespace(def_tuners=DefenceTuners())
    apply_phase_invariants(w3f, "TACKLE_DRIVE")
    check("3.8 default tuners: DEF_LINE_READY free on DRIVE (no freeze/lock)",
          line_default.frozen is False
          and getattr(line_default, "depth_locked", False) is False,
          "frozen=%s depth_locked=%s" % (
              line_default.frozen,
              getattr(line_default, "depth_locked", None)))

    line_default2 = _mk_def(8, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
    line_default2.frozen = False
    w3g = _mk_world_members([line_default2])
    w3g.open_play_engine = SimpleNamespace(def_tuners=DefenceTuners())
    apply_phase_invariants(w3g, "TACKLE_DOMINANCE")
    check("3.9 default tuners: DEF_LINE_READY free on DOMINANCE",
          line_default2.frozen is False
          and getattr(line_default2, "depth_locked", False) is False,
          "frozen=%s depth_locked=%s" % (
              line_default2.frozen,
              getattr(line_default2, "depth_locked", None)))

    # -- PART 4: has_movement_authority ---------------------------------
    print("\n[4] has_movement_authority grants the drive to every defensive role")
    retreat_auth = _mk_def(20, 46.0, 33.0, DefenderRole.DEF_RETREAT_10)
    retreat_auth.frozen = False
    w4 = _mk_world_members([retreat_auth])
    check("4.1 DEF_RETREAT_10 HAS authority in DRIVE",
          has_movement_authority(w4, "TACKLE_DRIVE", retreat_auth))
    line_auth = _mk_def(24, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
    line_auth.frozen = False
    w4.defending_team.append(line_auth)
    check("4.2 DEF_LINE_READY HAS authority in DRIVE",
          has_movement_authority(w4, "TACKLE_DRIVE", line_auth))
    chase_auth = _mk_def(27, 42.0, 30.0, DefenderRole.DEF_CHASE)
    chase_auth.frozen = False
    w4.defending_team.append(chase_auth)
    check("4.3 DEF_CHASE HAS authority in DRIVE",
          has_movement_authority(w4, "TACKLE_DRIVE", chase_auth))
    frozen_line = _mk_def(26, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
    frozen_line.frozen = True
    w4.defending_team.append(frozen_line)
    check("4.4 explicitly frozen DEF_LINE_READY has NO authority in DRIVE",
          not has_movement_authority(w4, "TACKLE_DRIVE", frozen_line))
    tack4 = _mk_def(21, 52.0, 34.0, DefenderRole.TACKLER_1)
    tack4.frozen = False
    w4.defending_team.append(tack4)
    check("4.5 TACKLER_1 authority retained in DRIVE",
          has_movement_authority(w4, "TACKLE_DRIVE", tack4))
    reg_tack = _mk_def(25, 52.0, 34.0, DefenderRole.DEF_LINE_READY)
    reg_tack.frozen = False
    w4.tackler_ids = {21, 25}
    w4.defending_team.append(reg_tack)
    check("4.6 registered tackler authority retained in DRIVE",
          has_movement_authority(w4, "TACKLE_DRIVE", reg_tack))
    fb4 = _mk_def(22, 10.0, 30.0, DefenderRole.DEF_LINE_READY)
    fb4.frozen = False
    fb4.fullback_role_active = True
    w4.defending_team.append(fb4)
    check("4.7 fullback authority retained in DRIVE",
          has_movement_authority(w4, "TACKLE_DRIVE", fb4))
    bc_att = _mk_def(23, 60.0, 35.0)
    bc_att.attack_role = "ball_carrier"
    w4.defending_team.append(bc_att)
    check("4.8 ATT_CARRIER authority retained in DRIVE (allowed_to_move)",
          has_movement_authority(w4, "TACKLE_DRIVE", bc_att))

    # -- PART 5: pre-tackle authority only touches bodies at the pile ---
    print("\n[5] pre-tackle authority manages only bodies inside the trigger radius")
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
    near_line = _mk_def(30, 58.5, 34.8, DefenderRole.DEF_LINE_READY)
    near_line.frozen = False
    far_line = _mk_def(31, 42.0, 30.0, DefenderRole.DEF_LINE_READY)
    far_line.frozen = False
    w5 = _mk_world_members([near_line, far_line], bc=bc)
    w5.open_play_engine = _mk_ope(True)
    w5.frame_spacing_targets = {}
    w5.ruck_x_m = 55.0
    w5.ruck_y_m = 35.0
    apply_phase_invariants(w5, "TACKLE_DRIVE")
    check("5.1 near DEF_LINE_READY is pre-tackle ELIGIBLE",
          is_pre_tackle_eligible(near_line))
    managed = apply_pre_tackle(
        w5, [near_line], 1.0 / 60.0, tun, frozen_mode=True,
    )
    check("5.2 frozen_mode MANAGES the eligible pile body",
          len(managed) >= 1, "n=%d" % len(managed))
    check("5.3 the 18 m body is NOT in the managed set",
          far_line not in managed)
    check("5.4 the 18 m body stays UNFROZEN during the drive",
          far_line.frozen is False, "frozen=%s" % far_line.frozen)
    check("5.5 the 18 m body keeps its depth role (no depth pin)",
          getattr(far_line, "depth_locked", False) is False,
          "depth_locked=%s" % getattr(far_line, "depth_locked", False))

    frozen_line_body = _mk_def(32, 58.5, 34.8, DefenderRole.DEF_LINE_READY)
    frozen_line_body.frozen = True
    w5b = _mk_world_members([frozen_line_body], bc=bc)
    w5b.open_play_engine = _mk_ope(False)
    w5b.frame_spacing_targets = {}
    w5b.ruck_x_m = 55.0
    w5b.ruck_y_m = 35.0
    check("5.6 explicitly frozen DEF_LINE_READY is pre-tackle INELIGIBLE",
          not is_pre_tackle_eligible(frozen_line_body))
    managed_frozen = apply_pre_tackle(
        w5b, [frozen_line_body], 1.0 / 60.0, tun, frozen_mode=True,
    )
    check("5.7 frozen_mode does NOT manage an explicitly frozen body",
          len(managed_frozen) == 0, "n=%d" % len(managed_frozen))

    # -- PART 6: Player.update runs the driver when not depth-locked -----
    print("\n[6] Player.update integrates BOTH axes when depth_locked=False")
    runner = Player(x=46.0, y=20.0, player_id=40)
    runner.frozen = False
    runner.depth_locked = False
    runner.depth_lock_x = None
    runner.depth_lock_y = None
    runner.vx = 0.0
    runner.vy = 0.0
    runner.target_x = 50.0
    runner.target_y = 24.0
    runner.move_speed = 4.0
    runner.max_defensive_speed_mps = 6.0
    runner.movement_state = MovementState.DRIFT
    runner._mmdl_moved = False
    runner._sd_vx = 0.0
    runner._sd_vy = 0.0
    runner._geometry_smooth_time_s = 0.12
    runner.update(1.0 / 60.0)
    check("6.1 X advanced toward target_x (depth geometry live)",
          runner.x > 46.0, "x=%s" % runner.x)
    check("6.2 Y advanced toward target_y (lateral geometry live)",
          runner.y > 20.0, "y=%s" % runner.y)
    check("6.3 target_x preserved (no depth pin overwrite)",
          abs(runner.target_x - 50.0) < 1e-6, "target_x=%s" % runner.target_x)
    check("6.4 target_y preserved (no laser-lock overwrite)",
          abs(runner.target_y - 24.0) < 1e-6, "target_y=%s" % runner.target_y)

    # -- PART 7: the drive geometry authority still publishes BC-relative X --
    print("\n[7] stamp_drive_depth_x publishes the live BC-relative depth target")
    from game.defence.defence_lvl_1.defensive_depth_roles import (
        stamp_drive_depth_x,
    )

    w7 = _mk_world_members([], bc=bc)
    w7.open_play_engine = _mk_ope(True)
    w7.frame_spacing_targets = {}
    w7.ruck_x_m = 55.0
    w7.ruck_y_m = 35.0
    w7.tes_drive_depth_ctx = None
    w7.sub_state = "TACKLE_DRIVE"
    w7._ope_phase_label = "TACKLE_DRIVE"

    winger = _mk_def(50, 58.0, 32.0, DefenderRole.DEF_LINE_READY)
    winger.depth_role = "WINGER"
    winger.frozen = False
    winger.max_defensive_speed_mps = 8.3
    winger.move_speed = 4.0
    w7.defending_team = [winger]

    stamp_drive_depth_x(w7, [winger])
    check("7.1 role_depth_target_x stamped = bc.x + WIDTH_OFFSETS[WINGER]",
          abs(float(getattr(winger, "role_depth_target_x", -1.0)) - 70.0) < 1e-6,
          "role_depth_target_x=%s" % getattr(winger, "role_depth_target_x", None))
    check("7.2 the published target is BC-relative, not the ruck anchor",
          abs(float(getattr(winger, "role_depth_target_x", -1.0)) - 65.0) > 1e-6,
          "role_depth_target_x=%s" % getattr(winger, "role_depth_target_x", None))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED -- DEF_LINE_READY / DEF_CHASE / "
          "DEF_RETREAT_10 keep movement authority through TACKLE_CONTACT, "
          "TACKLE_DRIVE and TACKLE_DOMINANCE; only ATT_SUPPORT / NONE stay "
          "frozen, and only TACKLE_INIT still latches the line")
    sys.exit(0)


if __name__ == "__main__":
    main()
