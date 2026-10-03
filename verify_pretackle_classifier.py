"""Verify the MovementState-driven pretackle classifier.

Asserts the core behaviour of ``pretackle_classifier.classify_pretackle``
(and its helpers) which replaced the binary ``try_pre_tackle`` distance
fast-paths:

  A. Flat-foot forced step-in (full-speed CHASE + ``pre_tackle_stance``)
     is NOT a real planted posture — it must run the evasion stack and can
     fail on angle, never auto-engaging on distance alone.
  B. Genuine planted (zero-velocity square-up) still engages.
  C. Slot-OUT body is rejected even at close range with PRIMARY + stance
     (slot-OUT is measured on the defender's LIVE lane Y — the reference
     ``is_slot_tackle_eligible`` uses).  A body that is additionally
     RADIAL-IN and FACING the carrier is a live tackle attempt and engages
     through the radial+facing engage guard instead.
  D. ``is_engage_role`` rejects THIRD (joiner-only).
  E. Wrong-foot baseline advances on every call (not stale-only-on-engage).
  F. ``select_primary_candidate`` prefers slot-eligible over a nearer
     slot-OUT engage-role candidate, AND admits a radial+facing guarded
     body that carries no role yet — the admission that lets the tackle
     pipeline actually fire for it.

Run:  python verify_pretackle_classifier.py
"""

import sys
import math
import random

from player.player import DefenderRole
from player.movement_states import MovementState

from game.defence.defence_lvl_1.pretackle_classifier import (
    classify_pretackle as classify,
    is_real_planted_posture,
    is_closing_posture,
    is_engage_role,
    select_primary_candidate,
)


PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


class Dummy:
    """Minimal stub with the fields the classifier reads."""

    def __init__(self, *, x=60.0, y=35.0, vx=0.0, vy=0.0,
                 facing=0.0, tackle_role=None, defender_role=None,
                 movement_state=None, frozen=False, freeze_timer=0.0,
                 target_x=None, target_y=None, mass=90.0, tackle_skill=0.5,
                 _tackle_commit_active=False, prev_facing=None,
                 collision_priority=None, player_id=0, **kwargs):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.facing = facing
        self.tackle_role = tackle_role
        self.defender_role = defender_role
        self.movement_state = movement_state
        self.frozen = frozen
        self.freeze_timer = freeze_timer
        self.target_x = target_x if target_x is not None else x
        self.target_y = target_y if target_y is not None else y
        self.mass = mass
        self.tackle_skill = tackle_skill
        self._tackle_commit_active = _tackle_commit_active
        if prev_facing is not None:
            self.prev_facing = prev_facing
        self.collision_priority = collision_priority
        self.player_id = player_id
        for k, v in kwargs.items():
            setattr(self, k, v)


class W:
    def __init__(self):
        self.ball_owner = "BC"
        self.open_play_engine = None
        # Compliance line = ruck_x + 10 = 50m; tests place the defender at
        # x >= 58 (onside) so the retreat-line gate never interferes.
        self.ruck_x_m = 40.0
        # ``is_tackle_phase`` reads world.sub_state; None is not a TES phase.
        self.sub_state = None


class FixedRng:
    """Deterministic rng: random() always returns *val* (default 0.99 so the
    5% base-random and any slip/miss rolls never fire)."""
    def __init__(self, val=0.99):
        self.val = val

    def random(self):
        return self.val


def make_tuners():
    class T:
        def __init__(self):
            self.rng = FixedRng(0.99)
            self.retreat_compliance_distance_m = 10.0
    return T()


# ────────────────────────────────────────────────────────────────────────
def test_a_flatfoot_chase_not_planted_no_distance_auto():
    print("\n[A] flat-foot forced step-in is NOT planted; no distance-only auto-engage")

    w = W()
    t = make_tuners()

    # (1) is_real_planted_posture must be False for a full-speed CHASE.
    ff = Dummy(x=58.0, y=35.0, vx=8.0, vy=0.0, facing=0.0,
               tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
               movement_state=MovementState.CHASE,
               pre_tackle_stance=True, footplanting=True, target_y=35.0)
    check("A1 full-speed CHASE is NOT real-planted",
          is_real_planted_posture(ff) is False)
    check("A2 full-speed CHASE IS closing",
          is_closing_posture(ff) is True)

    # (2) A genuine HOLD square-up (zero velocity + _mmdl_moved) IS planted.
    gp = Dummy(x=58.0, y=35.0, vx=0.0, vy=0.0, facing=0.0,
               tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
               movement_state=MovementState.HOLD, target_y=35.0)
    gp._mmdl_moved = True
    check("A3 zero-velocity HOLD IS real-planted",
          is_real_planted_posture(gp) is True)

    # (3) A flat-foot CHASE at <2m with a BAD angle (facing away) must NOT
    #     engage — the evasion stack's angle check rejects it.  This is the
    #     key regression: the old Gate 4 would auto-engage on distance.
    bad = Dummy(x=58.5, y=35.0, vx=8.0, vy=0.0, facing=math.pi,  # facing away (-X)
                tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
                movement_state=MovementState.CHASE, target_y=35.0)
    bad.pre_tackle_stance = True
    bad.footplanting = True
    bc_bad = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0)
    check("A4 flat-foot CHASE facing away does NOT auto-engage",
          classify(w, bad, bc_bad, t) is False)

    # (4) A flat-foot CHASE with a good head-on profile and reasonable speed
    #     can still engage through the stack (it is NOT blanket-rejected for
    #     being CHASE).  Chosen speeds avoid the outrun/overshoot margins.
    good = Dummy(x=58.5, y=35.0, vx=6.5, vy=0.0, facing=0.0,
                 tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
                 movement_state=MovementState.CHASE, target_y=35.0,
                 mass=90.0, tackle_skill=0.8)
    good.pre_tackle_stance = True
    good.footplanting = True
    bc_good = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0)
    check("A5 flat-foot CHASE head-on CAN still engage (not blanket-rejected)",
          classify(w, good, bc_good, t) is True)


# ────────────────────────────────────────────────────────────────────────
def test_b_genuine_planted_engages():
    print("\n[B] genuine planted square-up engages")

    w = W()
    t = make_tuners()
    p = Dummy(x=58.5, y=35.0, vx=0.0, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.HOLD, target_y=35.0,
              mass=90.0, tackle_skill=0.8)
    p._mmdl_moved = True
    p.pre_tackle_stance = True   # genuine stance (zero velocity)
    p.footplanting = True
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0)
    r = classify(w, p, bc, t)
    check("B1 zero-velocity planted engages", r is True)


# ────────────────────────────────────────────────────────────────────────
def test_c_slot_out_rejected():
    print("\n[C] slot-OUT rejected on the LIVE lane + radial+facing guard")

    w = W()
    t = make_tuners()
    # Slot-OUT is measured on the defender's LIVE lane ``y`` — the reference
    # ``is_slot_tackle_eligible`` uses — NOT on ``target_y``.  This defender
    # sits 3.0 m off the carrier's lane AND 3.35 m away, so the radial+facing
    # engage guard cannot fire either: the pure slot-OUT case.
    d = Dummy(x=58.5, y=32.0, vx=0.0, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.HOLD, target_x=58.5, target_y=32.0,
              mass=90.0, tackle_skill=0.8)
    d._mmdl_moved = True
    d.pre_tackle_stance = True
    d.footplanting = True
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0)
    check("C0 defender is genuinely slot-OUT on its live lane",
          abs(float(bc.y) - float(d.y)) >= 0.3)
    check("C1 slot-OUT (live lane off the carrier) rejected",
          classify(w, d, bc, t) is False)

    # ── RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10) ──
    # The guard is the HARD fix for "no tackles happen ever": a GENUINE LINE
    # body that is radial-in AND facing the carrier is a live tackle attempt
    # and must engage even with its lane Y slot-OUT.  The body closes on the
    # carrier (velocity along its facing) so the closing-speed conjunct is
    # satisfied and the guard bypass is what decides the outcome.
    FACING_AT_BC = math.atan2(36.0 - 35.0, 60.0 - 61.0)   # atan2(1, -1)
    bc_g = Dummy(x=60.0, y=36.0, vx=5.0, vy=0.0, facing=0.0)
    g = Dummy(x=61.0, y=35.0, vx=-4.6, vy=4.6, facing=FACING_AT_BC,
              defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.CHASE, target_y=35.0)
    check("C2 guarded body is slot-OUT (lane Y off the carrier)",
          abs(float(bc_g.y) - float(g.y)) >= 0.3)
    check("C3 radial+facing slot-OUT body engages via the guard",
          classify(w, g, bc_g, t) is True)

    # A radial-in body NOT facing the carrier is still rejected.
    away = Dummy(x=61.0, y=35.0, vx=-4.6, vy=4.6, facing=0.0,
                 defender_role=DefenderRole.DEF_LINE_READY,
                 movement_state=MovementState.CHASE, target_y=35.0)
    check("C4 radial-in but NOT facing still rejected",
          classify(w, away, bc_g, t) is False)


# ────────────────────────────────────────────────────────────────────────
def test_d_engage_role_excludes_third():
    print("\n[D] is_engage_role excludes THIRD (joiner-only)")

    check("D1 PRIMARY is engage-role", is_engage_role(Dummy(tackle_role="PRIMARY")) is True)
    check("D2 SECONDARY is engage-role", is_engage_role(Dummy(tackle_role="SECONDARY")) is True)
    check("D3 THIRD is NOT engage-role", is_engage_role(Dummy(tackle_role="THIRD")) is False)
    check("D4 PASSIVE is NOT engage-role", is_engage_role(Dummy(tackle_role="PASSIVE")) is False)


# ────────────────────────────────────────────────────────────────────────
def test_e_wrong_foot_baseline_advances_every_call():
    print("\n[E] wrong-foot baseline advances on every call")

    w = W()
    t = make_tuners()
    # Face the BC head-on; prev_facing == facing -> no wrong-foot, engages.
    p = Dummy(x=58.5, y=35.0, vx=6.5, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.CHASE, target_y=35.0,
              mass=90.0, tackle_skill=0.8)
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0,
               prev_facing=0.0)
    r = classify(w, p, bc, t)
    check("E1 engages when no facing delta", r is True)
    check("E2 baseline advanced to current facing",
          getattr(bc, "prev_facing", None) == 0.0)

    # BC facing changed dramatically (180 deg) from its previous baseline:
    # the wrong-foot branch evaluates without throwing and the baseline
    # still advances on the call.
    bc2 = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=math.pi,
                prev_facing=0.0)
    p2 = Dummy(x=58.5, y=35.0, vx=6.5, vy=0.0, facing=0.0,
               tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
               movement_state=MovementState.CHASE, target_y=35.0,
               mass=90.0, tackle_skill=0.99)
    _r2 = classify(w, p2, bc2, t)
    check("E3 baseline still advances after a wrong-foot evaluation",
          getattr(bc2, "prev_facing", None) == math.pi)


# ────────────────────────────────────────────────────────────────────────
def test_f_select_primary_prefers_slot_eligible():
    print("\n[F] select_primary_candidate: slot preference + guard admission")

    w = W()
    bc = Dummy(x=60.0, y=35.0)
    # The NEARER candidate is genuinely slot-OUT on its LIVE lane (0.5 m off
    # the carrier) and 1.68 m away, so the radial+facing guard cannot fire
    # either (1.68 > 1.5).  The slightly further candidate is slot-IN (2.0 m).
    # The helper must prefer the slot-eligible one.
    near_slot_out = Dummy(x=58.4, y=34.5, tackle_role="PRIMARY",
                          target_x=58.4, target_y=34.5)
    far_slot_in = Dummy(x=58.0, y=35.0, tackle_role="PRIMARY",
                        target_x=58.0, target_y=35.0)
    check("F0 nearer candidate is genuinely slot-OUT + guard-ineligible",
          abs(35.0 - 34.5) >= 0.3
          and math.hypot(58.4 - 60.0, 34.5 - 35.0) >= 1.5)
    chosen = select_primary_candidate(w, [near_slot_out, far_slot_in], bc)
    check("F1 chosen is the slot-eligible one", chosen is far_slot_in)
    check("F2 slot-OUT not chosen", chosen is not near_slot_out)

    # If nothing is slot-eligible, still returns a real candidate.
    only_out = Dummy(x=58.0, y=33.0, tackle_role="PRIMARY",
                     target_x=58.0, target_y=33.0)
    chosen2 = select_primary_candidate(w, [only_out], bc)
    check("F3 falls back to nearest engage-role when none slot-eligible",
          chosen2 is only_out)

    # ── RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10) — CANDIDACY ──
    # A DEF_LINE_READY body with NO role yet that is radial-in AND facing the
    # carrier is ADMITTED as a candidate even though it is slot-OUT.  The live
    # call sites hand exactly ONE candidate to ``try_pre_tackle`` per frame,
    # so without this admission the tackle pipeline could never fire for it —
    # the final chokepoint of "no tackles ever".
    FACING_AT_BC = math.atan2(35.0 - 36.0, 60.0 - 61.0)   # atan2(-1, -1)
    guarded = Dummy(x=61.0, y=36.0, facing=FACING_AT_BC,
                    defender_role=DefenderRole.DEF_LINE_READY,
                    movement_state=MovementState.CHASE)
    check("F4 guarded body carries no engage role yet",
          is_engage_role(guarded) is False)
    check("F5 guarded slot-OUT body IS admitted as a candidate",
          select_primary_candidate(w, [guarded], bc) is guarded)

    # The guard does NOT widen candidacy for far / non-facing / THIRD bodies.
    far_body = Dummy(x=70.0, y=36.0, facing=FACING_AT_BC,
                     defender_role=DefenderRole.DEF_LINE_READY,
                     movement_state=MovementState.CHASE)
    check("F6 far radial-OUT line body is NOT admitted",
          select_primary_candidate(w, [far_body], bc) is None)
    away_body = Dummy(x=61.0, y=36.0, facing=0.0,
                      defender_role=DefenderRole.DEF_LINE_READY,
                      movement_state=MovementState.CHASE)
    check("F7 non-facing line body is NOT admitted",
          select_primary_candidate(w, [away_body], bc) is None)
    third_body = Dummy(x=61.0, y=36.0, facing=FACING_AT_BC,
                       tackle_role="THIRD",
                       movement_state=MovementState.CHASE)
    check("F8 THIRD stays joiner-only (never admitted)",
          select_primary_candidate(w, [third_body], bc) is None)


def main():
    test_a_flatfoot_chase_not_planted_no_distance_auto()
    test_b_genuine_planted_engages()
    test_c_slot_out_rejected()
    test_d_engage_role_excludes_third()
    test_e_wrong_foot_baseline_advances_every_call()
    test_f_select_primary_prefers_slot_eligible()
    print(f"\n{'='*60}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
