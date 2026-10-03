"""verify_rollout_slot_arbitration.py — rollout verifier for the slot-arbitration
feature: soft-lock clamp, override hysteresis, tackle gating, ownership conflict.

Confirms the 2026-09-07 rollout ship-text claims by runtime probe:

  1. SOFT-LOCK CLAMP: a low-priority writer cannot move a locked slot's
     ``target_y`` beyond ``slot_locked_y +/- SOFT_LOCK_RADIUS_M``; a proposal
     inside the band is written as-is.
  2. OVERRIDE HYSTERESIS: a large override by a high-priority writer is
     clamped until it is sustained for ``OVERRIDE_SUSTAIN_FRAMES`` frames,
     then committed.
  3. TACKLE GATING: a BC inside the radial radius but OUTSIDE the defender's
     slot-Y band (|bc.y - target_y| >= TACKLE_SLOT_THRESHOLD_M) must NOT
     trigger a tackle — the defender is not promoted to COMMIT, and
     ``decide_movement_state`` does not return CHASE from the switch.  A
     slot-IN body (within the band) IS promoted/committed.
  4. OWNERSHIP CONFLICT: ``claim_slot_exclusive`` returns False when two
     writers claim the same slot on the same frame, and
     ``slot_ownership_conflicts`` surfaces it.  A different frame is clean.

Run:  python verify_rollout_slot_arbitration.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from game.defence.defence_lvl_1.slot_arbitration import (
    SOFT_LOCK_RADIUS_M,
    PRIORITY_PIN,
    PRIORITY_RETREAT,
    lock_slot,
    update_slot_target,
    reset_slot_claims,
    claim_slot_exclusive,
    slot_ownership_conflicts,
    is_slot_tackle_eligible,
    tackle_slot_threshold_m,
    override_sustain_frames,
)
from game.defence.defence_lvl_1.pre_tackle_authority import (
    apply_pre_tackle,
    PreTackleState,
    is_committed,
)
from game.defence.defence_lvl_1.movement_mode_decision import (
    decide_movement_state,
)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_world():
    w = SimpleNamespace()
    w.frame_count = 0
    w.tes_active = False
    w.sub_state = SimpleNamespace(name="OPEN_PLAY")
    w.ruck_x_m = 60.0
    w.ruck_y_m = 35.0
    w.frame_convergence_targets = {}
    return w


def make_tuners():
    t = SimpleNamespace()
    t.pretackle_attempt_enabled = True
    t.pre_tackle_trigger_radius_m = 2.0
    t.tackle_radius_m = 1.5
    t.pursuit_commit_radius_m = 12.0
    t.pretackle_slow_speed_mps = 2.5
    t.pretackle_freeze_radius_m = 0.15
    t.pretackle_follow_smooth = 0.6
    t.pretackle_commit_speed_mps = 4.0
    t.pretackle_plant_radius_m = 0.8
    t.pretackle_slowdown_band_m = 3.0
    t.pretackle_slowdown_scale = 0.45
    t.pretackle_commit_depth_offset_m = 1.0
    t.pretackle_commit_lateral_offset_m = 1.0
    t.pretackle_phase_tuners = {}
    return t


def make_slot(pid, target_y):
    s = Player(x=50.0, y=target_y, player_id=pid)
    s.target_y = target_y
    s.target_x = 50.0
    s._target_writer = None
    return s


def make_def_line_body(pid, x, y, target_y):
    d = Player(x=x, y=y, player_id=pid)
    d.target_x = x
    d.target_y = target_y
    d.defender_role = DefenderRole.DEF_LINE_READY
    d.frozen = False
    d.freeze_timer = 0.0
    d.fullback_role_active = False
    d.tackle_state_active = False
    d.depth_class = "PRIMARY"
    d.vx = 0.0
    d.vy = 0.0
    d.pre_tackle_state = PreTackleState.SLOW
    d.movement_state = MovementState.DRIFT
    return d


def state_name(body):
    s = getattr(body, "pre_tackle_state", None)
    return getattr(s, "name", None) or (s.value if isinstance(s, PreTackleState) else s)


def main():
    print("=" * 64)
    print("ROLLOUT SLOT-ARBITRATION + TACKLE-GATING VERIFIER (2026-09-07)")
    print("=" * 64)

    world = make_world()

    # ══════════════════════════════════════════════════════════════
    # [1] SOFT-LOCK CLAMP
    # ══════════════════════════════════════════════════════════════
    print("\n[1] SOFT-LOCK CLAMP")
    r = SOFT_LOCK_RADIUS_M  # 0.2
    slot = make_slot(1, 30.0)
    lock_slot(world, slot, 30.0, "ruck_pin", PRIORITY_PIN)
    w1 = update_slot_target(world, slot, 30.1, "retreat", PRIORITY_RETREAT)
    check("1.1 proposal inside band written as-is",
          abs(w1 - 30.1) < 1e-6, "got %.4f" % w1)
    w2 = update_slot_target(world, slot, 35.0, "retreat", PRIORITY_RETREAT)
    check("1.2 low-prio write clamped to locked + SOFT_LOCK_RADIUS",
          abs(w2 - (30.0 + r)) < 1e-6, "got %.4f (expected %.4f)" % (w2, 30.0 + r))
    check("1.3 slot.target_y reflects the clamp",
          abs(slot.target_y - (30.0 + r)) < 1e-6, "target_y=%.4f" % slot.target_y)
    w3 = update_slot_target(world, slot, 20.0, "retreat", PRIORITY_RETREAT)
    check("1.4 low-prio write below clamped to locked - SOFT_LOCK_RADIUS",
          abs(w3 - (30.0 - r)) < 1e-6, "got %.4f (expected %.4f)" % (w3, 30.0 - r))

    # ══════════════════════════════════════════════════════════════
    # [2] OVERRIDE HYSTERESIS
    # ══════════════════════════════════════════════════════════════
    print("\n[2] OVERRIDE HYSTERESIS")
    sustain = override_sustain_frames(world)  # default 3
    h = make_slot(2, 30.0)
    lock_slot(world, h, 30.0, "ruck_pin", PRIORITY_PIN)
    f1 = update_slot_target(world, h, 35.0, "override_writer", 200)
    check("2.1 override on frame 1 clamped (pending)",
          abs(f1 - (30.0 + r)) < 1e-6, "got %.4f" % f1)
    f2 = update_slot_target(world, h, 35.0, "override_writer", 200)
    check("2.2 override on frame 2 still clamped",
          abs(f2 - (30.0 + r)) < 1e-6, "got %.4f" % f2)
    f3 = update_slot_target(world, h, 35.0, "override_writer", 200)
    check("2.3 override committed once sustained",
          abs(f3 - 35.0) < 1e-6, "got %.4f (expected 35.0)" % f3)

    # ══════════════════════════════════════════════════════════════
    # [3] TACKLE GATING
    # ══════════════════════════════════════════════════════════════
    print("\n[3] TACKLE GATING (slot-Y threshold)")
    threshold = tackle_slot_threshold_m(world)  # 0.3
    # 3a: BC radially close (dist 0.64 < 1.5) but slot-OUT (|35.5 - 35.0| = 0.5 >= 0.3).
    bc_out = Player(x=60.0, y=35.5, player_id=99)
    bc_out.target_y = 35.5
    d_out = make_def_line_body(3, x=60.5, y=35.1, target_y=35.0)
    w_out = make_world()
    w_out.ball_carrier = bc_out
    check("3.1 slot-OUT is NOT slot-tackle-eligible",
          not is_slot_tackle_eligible(bc_out, d_out, w_out),
          "|bc.y-target.y|=%.3f >= %.3f" % (abs(35.5 - 35.0), threshold))
    st_out = decide_movement_state(w_out, d_out, bc_out, make_tuners())
    check("3.2 radial-in but slot-OUT -> NOT forced CHASE by the switch",
          st_out is not MovementState.CHASE,
          "state=%s" % getattr(st_out, "name", st_out))
    managed_out = apply_pre_tackle(w_out, [d_out], 1.0 / 60.0,
                                   make_tuners(), frozen_mode=False)
    check("3.3 slot-OUT body NOT committed by apply_pre_tackle",
          not is_committed(d_out), "state=%s" % state_name(d_out))
    check("3.4 slot-OUT body still managed (SLOW/HOLD)",
          d_out in managed_out and getattr(d_out, "pre_tackle_state", None) in (
              PreTackleState.SLOW, PreTackleState.HOLD),
          "managed=%s state=%s" % (d_out in managed_out, state_name(d_out)))

    # 3b: BC radially close (dist 0.5 < 1.5) AND slot-IN (|35.1 - 35.1| = 0 < 0.3).
    bc_in = Player(x=60.0, y=35.1, player_id=98)
    bc_in.target_y = 35.1
    d_in = make_def_line_body(4, x=60.5, y=35.1, target_y=35.1)
    w_in = make_world()
    w_in.ball_carrier = bc_in
    check("3.5 slot-IN IS slot-tackle-eligible",
          is_slot_tackle_eligible(bc_in, d_in, w_in),
          "|bc.y-target.y|=%.3f < %.3f" % (abs(35.1 - 35.1), threshold))
    managed_in = apply_pre_tackle(w_in, [d_in], 1.0 / 60.0,
                                  make_tuners(), frozen_mode=False)
    check("3.6 slot-IN body committed by apply_pre_tackle",
          is_committed(d_in), "state=%s" % state_name(d_in))

    # ══════════════════════════════════════════════════════════════
    # [4] OWNERSHIP CONFLICT
    # ══════════════════════════════════════════════════════════════
    print("\n[4] OWNERSHIP CONFLICT")
    w4 = make_world()
    reset_slot_claims(w4)
    check("4.1 first claim on frame 7 succeeds",
          claim_slot_exclusive(w4, 7, "L1", "writerA") is True)
    check("4.2 second writer on same frame/slot rejected",
          claim_slot_exclusive(w4, 7, "L1", "writerB") is False)
    check("4.3 conflict surfaced by slot_ownership_conflicts",
          "L1" in slot_ownership_conflicts(w4),
          "conflicts=%s" % slot_ownership_conflicts(w4))
    reset_slot_claims(w4)
    assert claim_slot_exclusive(w4, 7, "L1", "writerA") is True
    check("4.4 different-frame claim does not conflict",
          claim_slot_exclusive(w4, 8, "L1", "writerB") is True
          and slot_ownership_conflicts(w4) == [],
          "conflicts=%s" % slot_ownership_conflicts(w4))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
