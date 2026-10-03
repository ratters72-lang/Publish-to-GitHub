"""Verify the RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10).

"Mate no tackles happen ever" — the tackle pipeline never fired.  The engage
decision was gated on a SLOT-Y threshold in several independent places, all
reading ``slot_arbitration.is_slot_tackle_eligible``
(``abs(bc.y - defender.y) < tackle_slot_threshold_m``, default 0.3 m).  When
the carrier ran a lane whose Y sat off a defender's live Y every gate vetoed,
so a defender that was radial-in, closing and FACING the carrier was routed to
DRIFT/RETREAT and never started a tackle.

The guard bypasses the slot gate for exactly that geometry:

    genuine line defender AND radial-in AND facing-at-carrier
        -> bypass slot -> force engage (TES role stamped)

Asserts:
  A. The predicate: radial-in + facing fires; not-facing / radial-out do not.
  B. Scope: markers / dummy-half / fullback / TES tacklers / frozen /
     freeze-timer / THIRD (joiner-only) bodies are never forced.
  C. Pre-conditions: no ball ownership and a LIVE TES suppress the guard.
  D. Stamps: ``target_source``, the guard latch, the TES engage role, and the
     CHASE closing posture (``force_tackle_engage``); THIRD never promoted.
  E. Machine: ``run_machine`` classifies the guarded body CLOSING_HEAD_ON and
     ``should_engage`` returns True even though the body is slot-OUT.
  F. Classifier: ``classify_pretackle`` engages a guarded slot-OUT body.
  G. LIVE path: ``select_primary_candidate`` ADMITS the guarded body (it
     carries no role yet) and ``try_pre_tackle`` returns True — the tackle
     pipeline actually fires.
  H. Non-forced bodies are still rejected (slot-OUT and guard-ineligible).
  I. Wiring: movement_mode_decision / pre_tackle_authority / pretackle_machine
     / pretackle_classifier all consume the ONE guard predicate.

Run:  python verify_tackle_engage_guard.py
Exit 0 on success, 1 on failure.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from types import SimpleNamespace

from player.player import DefenderRole
from player.movement_states import MovementState

from game.defence.defence_lvl_1.tackle_engage_guard import (
    TACKLE_ENGAGE_GUARD_SOURCE,
    TES_ENGAGE_ROLE,
    NON_ENGAGE_ROLES,
    TACKLE_RADIUS_M,
    TACKLE_ENGAGE_FACING_MAX_DEG,
    approach_angle_deg,
    distance_to_bc,
    is_facing_bc,
    is_radial_in,
    is_tackle_engage_guard,
    is_tackle_engage_guard_stamped,
    stamp_tackle_engage_guard,
    stamp_tes_tackler_role,
    force_tackle_engage,
    clear_tackle_engage_guard,
)
from game.defence.defence_lvl_1.slot_arbitration import (
    is_slot_tackle_eligible,
)
from game.defence.defence_lvl_1.pretackle_classifier import (
    classify_pretackle,
    is_engage_role,
    select_primary_candidate,
)
from game.defence.defence_lvl_1.pretackle_machine import (
    PretackleMachineState as S,
    run_machine,
    should_engage,
)
from game.defence.defence_lvl_1.defensive_behaviour_lvl1 import try_pre_tackle


PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, detail))


class Body:
    """Minimal defender/carrier stub carrying the fields the guard reads."""

    def __init__(self, pid, x, y, role=DefenderRole.DEF_LINE_READY,
                 state=MovementState.CHASE, facing=0.0, vx=0.0, vy=0.0,
                 tackle_role=None):
        self.player_id = pid
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.facing = facing
        self.frozen = False
        self.freeze_timer = 0.0
        self.fullback_role_active = False
        self.tackle_state_active = False
        self.defender_role = role
        self.movement_state = state
        self.tackle_role = tackle_role
        self.target_source = None
        self.target_x = x
        self.target_y = y
        self.mass = 90.0
        self.tackle_skill = 0.8
        self._mmdl_moved = False


class FixedRng:
    """Deterministic rng: ``random()`` always returns 0.99, so the 5% base
    random failure and any slip/miss rolls never fire."""

    def __init__(self, val=0.99):
        self.val = val

    def random(self):
        return self.val


class Tuners:
    def __init__(self):
        self.rng = FixedRng(0.99)
        self.tackle_radius_m = 1.5
        self.tackle_engage_facing_max_deg = 60.0
        self.pretackle_machine_enabled = True
        self.machine_engage_distance_m = 2.0
        self.machine_head_on_max_deg = 30.0
        self.machine_side_on_max_deg = 60.0
        self.machine_max_approach_angle_deg = 120.0
        self.machine_planted_velocity_eps = 0.01
        self.machine_wrong_foot_facing_delta = 0.3
        self.machine_wrong_foot_base_chance = 0.4
        self.machine_wrong_foot_skill_scale = 0.15
        self.machine_base_random_fail = 0.05
        self.machine_bounce_back_push_m = 0.25
        self.machine_fall_over_stun_s = 0.5
        self.machine_stunned_upright_stun_s = 0.2
        self.machine_wrong_foot_recoil_m = 0.15
        self.retreat_compliance_distance_m = 10.0


def make_world(**kw):
    base = dict(ball_owner="BC", tes_active=False, open_play_engine=None,
                tackle_slot_threshold_m=0.3, frame_count=17, ruck_x_m=40.0,
                sub_state=None)
    base.update(kw)
    return SimpleNamespace(**base)


# bc at (60, 36); guarded body at (61, 35): dist 1.414 (radial-in) and
# |bc.y - d.y| = 1.0 (slot-OUT on the live lane).
BC_X, BC_Y = 60.0, 36.0
G_X, G_Y = 61.0, 35.0
FACE_AT_BC = math.atan2(BC_Y - G_Y, BC_X - G_X)     # atan2(1, -1)


def guarded_body(pid=1, role=DefenderRole.DEF_LINE_READY, tackle_role=None):
    return Body(pid, G_X, G_Y, role=role, facing=FACE_AT_BC,
                tackle_role=tackle_role)


def make_bc(pid=99):
    return Body(pid, BC_X, BC_Y, state=MovementState.CHASE)


# ────────────────────────────────────────────────────────────────────────
def test_a_predicate():
    print("\n[A] predicate: radial-in AND facing-at-carrier")
    t = Tuners()
    w = make_world()
    bc = make_bc()

    d = guarded_body()
    check("A1 approach angle to the carrier ~0 deg",
          abs(approach_angle_deg(d, bc)) < 1e-9,
          "got %.3f" % approach_angle_deg(d, bc))
    check("A2 distance is inside the tackle radius",
          distance_to_bc(d, bc) < TACKLE_RADIUS_M,
          "dist=%.3f" % distance_to_bc(d, bc))
    check("A3 is_radial_in True", is_radial_in(d, bc, t) is True)
    check("A4 is_facing_bc True", is_facing_bc(d, bc, t) is True)
    check("A5 guard predicate fires", is_tackle_engage_guard(d, bc, w, t) is True)

    # not facing (carrier past the shoulder)
    away = Body(2, G_X, G_Y, facing=0.0)
    check("A6 radial-in but NOT facing -> False",
          is_tackle_engage_guard(away, bc, w, t) is False)

    # radial-out
    far = Body(3, 70.0, 35.0, facing=FACE_AT_BC)
    check("A7 facing but radial-OUT -> False",
          is_tackle_engage_guard(far, bc, w, t) is False)

    # radial-in exactly ON the radius is NOT inside it (strict <)
    edge = Body(4, 60.0, 35.0, facing=0.0)
    check("A8 distance == radius is not radial-in (strict <)",
          is_tackle_engage_guard(edge, bc, w, t) is False)

    # None bodies
    check("A9 missing carrier -> False",
          is_tackle_engage_guard(d, None, w, t) is False)
    check("A10 missing defender -> False",
          is_tackle_engage_guard(None, bc, w, t) is False)

    # Tunable overrides: a body 2.0 m away from the carrier is radial-OUT at
    # the 1.5 m default and radial-IN once the radius is widened to 3.0 m.
    mid = Body(6, 62.0, BC_Y, facing=math.pi)
    check("A11a 2.0 m body is radial-OUT at the default radius",
          is_tackle_engage_guard(mid, bc, w, t) is False)
    big = Tuners()
    big.tackle_radius_m = 3.0
    check("A11 radius override widens radial-in",
          is_tackle_engage_guard(mid, bc, w, big) is True)
    narrow = Tuners()
    narrow.tackle_engage_facing_max_deg = 5.0
    off = Body(5, G_X, G_Y, facing=FACE_AT_BC + math.radians(20.0))
    check("A12 facing-band override tightens the facing test",
          is_tackle_engage_guard(off, bc, w, narrow) is False)

    # master gate
    off_t = Tuners()
    off_t.tackle_engage_guard_enabled = False
    check("A13 master gate disables the guard",
          is_tackle_engage_guard(d, bc, w, off_t) is False)


# ────────────────────────────────────────────────────────────────────────
def test_b_scope():
    print("\n[B] scope: dedicated owners are never forced")
    t = Tuners()
    w = make_world()
    bc = make_bc()

    check("B1 marker (MARKER_A) never forced",
          is_tackle_engage_guard(guarded_body(role=DefenderRole.MARKER_A),
                                 bc, w, t) is False)
    check("B2 marker (MARKER_B) never forced",
          is_tackle_engage_guard(guarded_body(role=DefenderRole.MARKER_B),
                                 bc, w, t) is False)
    check("B3 dummy half never forced",
          is_tackle_engage_guard(guarded_body(role=DefenderRole.DUMMY_HALF),
                                 bc, w, t) is False)

    fb = guarded_body()
    fb.fullback_role_active = True
    check("B4 fullback never forced", is_tackle_engage_guard(fb, bc, w, t) is False)

    fr = guarded_body()
    fr.frozen = True
    check("B5 frozen body never forced", is_tackle_engage_guard(fr, bc, w, t) is False)

    ft = guarded_body()
    ft.freeze_timer = 0.4
    check("B6 freeze-timer body never forced",
          is_tackle_engage_guard(ft, bc, w, t) is False)

    tsa = guarded_body()
    tsa.tackle_state_active = True
    check("B7 TES tackle-state holder never forced",
          is_tackle_engage_guard(tsa, bc, w, t) is False)

    check("B8 THIRD is a non-engage role",
          "THIRD" in NON_ENGAGE_ROLES)
    check("B9 THIRD never promoted to an initiator by the guard",
          is_tackle_engage_guard(guarded_body(tackle_role="THIRD"),
                                 bc, w, t) is False)
    check("B10 a role-less line body IS eligible (the reported case)",
          is_tackle_engage_guard(guarded_body(tackle_role=None), bc, w, t) is True)
    check("B11 an existing PRIMARY is still eligible",
          is_tackle_engage_guard(guarded_body(tackle_role="PRIMARY"),
                                 bc, w, t) is True)


# ────────────────────────────────────────────────────────────────────────
def test_c_preconditions():
    print("\n[C] pre-conditions: ownership + live TES")
    t = Tuners()
    bc = make_bc()
    d = guarded_body()

    w_tes = make_world(tes_active=True)
    check("C1 live TES suppresses the guard",
          is_tackle_engage_guard(d, bc, w_tes, t) is False)

    w_no = make_world(ball_owner="9")
    check("C2 carrier not in possession suppresses the guard",
          is_tackle_engage_guard(d, bc, w_no, t) is False)

    w_none = make_world(ball_owner=None)
    check("C3 unknown ownership (None) still allows the guard",
          is_tackle_engage_guard(d, bc, w_none, t) is True)


# ────────────────────────────────────────────────────────────────────────
def test_d_stamps():
    print("\n[D] stamps: target_source / latch / TES role / posture")
    w = make_world()
    d = guarded_body()

    stamp_tackle_engage_guard(d, world=w)
    check("D1 target_source stamped", d.target_source == TACKLE_ENGAGE_GUARD_SOURCE,
          "got %r" % (d.target_source,))
    check("D2 guard latch set", is_tackle_engage_guard_stamped(d) is True)
    check("D3 guard frame recorded",
          getattr(d, "_tackle_engage_guard_frame", None) == 17,
          "got %r" % (getattr(d, "_tackle_engage_guard_frame", None),))

    clear_tackle_engage_guard(d)
    check("D4 clear drops the latch", is_tackle_engage_guard_stamped(d) is False)

    r = Body(2, G_X, G_Y)
    stamp_tes_tackler_role(r)
    check("D5 role-less body gets the TES engage role",
          r.tackle_role == TES_ENGAGE_ROLE, "got %r" % (r.tackle_role,))

    keep = Body(3, G_X, G_Y, tackle_role="SECONDARY")
    stamp_tes_tackler_role(keep)
    check("D6 an existing engage role is never downgraded",
          keep.tackle_role == "SECONDARY", "got %r" % (keep.tackle_role,))

    third = Body(4, G_X, G_Y, tackle_role="THIRD")
    check("D7 THIRD is not an engage role so the stamp would fire... "
          "but the guard never admits it", is_engage_role(third) is False)
    check("D8 ...so guard-eligibility is False for THIRD",
          is_tackle_engage_guard(third, make_bc(), w, Tuners()) is False)

    f = Body(5, G_X, G_Y)
    force_tackle_engage(f, world=w)
    check("D9 force pins the CLOSING posture (CHASE)",
          f.movement_state is MovementState.CHASE)
    check("D10 force stamps the TES role", f.tackle_role == TES_ENGAGE_ROLE)
    check("D11 force stamps target_source",
          f.target_source == TACKLE_ENGAGE_GUARD_SOURCE)
    check("D12 force sets the latch", is_tackle_engage_guard_stamped(f) is True)


# ────────────────────────────────────────────────────────────────────────
def test_e_machine():
    print("\n[E] machine: guarded slot-OUT body engages")
    t = Tuners()
    w = make_world()
    bc = make_bc()
    d = guarded_body()

    check("E0 body is slot-OUT on its live lane",
          is_slot_tackle_eligible(bc, d, w) is False)
    check("E0b body carries no role yet", d.tackle_role is None)

    st = run_machine(w, d, bc, t)
    check("E1 classified CLOSING_HEAD_ON", st is S.CLOSING_HEAD_ON, "got %s" % st)
    check("E2 should_engage True",
          should_engage(w, d, bc, t, state=st) is True)
    check("E3 run_machine stamped the TES engage role",
          d.tackle_role == TES_ENGAGE_ROLE, "got %r" % (d.tackle_role,))
    check("E4 target_source stamped by the machine path",
          d.target_source == TACKLE_ENGAGE_GUARD_SOURCE,
          "got %r" % (d.target_source,))

    # Non-facing radial-in body is still refused by the machine.
    away = Body(2, G_X, G_Y, facing=0.0)
    st_away = run_machine(w, away, bc, t)
    check("E5 non-facing body is NOT forced (RETREATING)",
          st_away is S.RETREATING, "got %s" % st_away)
    check("E6 non-facing body gets no role stamp", away.tackle_role is None,
          "got %r" % (away.tackle_role,))
    check("E7 non-facing body never feeds TES",
          should_engage(w, away, bc, t, state=st_away) is False)


# ────────────────────────────────────────────────────────────────────────
def test_f_classifier():
    print("\n[F] classifier: guarded slot-OUT body engages")
    t = Tuners()
    w = make_world()
    bc = make_bc()

    # Closing on the carrier so the closing-speed conjunct is satisfied and
    # the guard bypass is what decides the outcome.  The carrier MOVES too:
    # the classifier's defender-overshoot margin would otherwise reject a
    # body closing faster than a stationary carrier.
    bc_moving = Body(98, BC_X, BC_Y, state=MovementState.CHASE,
                     vx=-5.0, vy=5.0)
    g = Body(1, G_X, G_Y, facing=FACE_AT_BC, vx=-4.6, vy=4.6)
    check("F1 classifier engages the guarded body",
          classify_pretackle(w, g, bc_moving, t) is True)
    check("F2 classifier stamped the TES engage role",
          g.tackle_role == TES_ENGAGE_ROLE, "got %r" % (g.tackle_role,))

    away = Body(2, G_X, G_Y, facing=0.0, vx=-4.6, vy=4.6)
    check("F3 non-facing radial-in body still rejected",
          classify_pretackle(w, away, bc_moving, t) is False)


# ────────────────────────────────────────────────────────────────────────
def test_g_live_path():
    print("\n[G] LIVE path: candidate selection + try_pre_tackle")
    t = Tuners()
    w = make_world()
    bc = make_bc()

    d = guarded_body()
    check("G0 guarded body has no engage role yet",
          is_engage_role(d) is False)
    primary = select_primary_candidate(w, [d], bc)
    check("G1 guarded body IS admitted as the candidate",
          primary is d, "got %r" % (primary,))
    fired = try_pre_tackle(w, primary, bc, t)
    check("G2 try_pre_tackle FIRES (the tackle pipeline must fire)",
          fired is True)
    check("G3 TES engage role stamped", d.tackle_role == TES_ENGAGE_ROLE,
          "got %r" % (d.tackle_role,))
    check("G4 target_source stamped",
          d.target_source == TACKLE_ENGAGE_GUARD_SOURCE,
          "got %r" % (d.target_source,))

    # Admission stays narrow.
    far = Body(2, 70.0, 35.0, facing=FACE_AT_BC)
    check("G5 far radial-out line body is NOT a candidate",
          select_primary_candidate(w, [far], bc) is None)
    away = Body(3, G_X, G_Y, facing=0.0)
    check("G6 non-facing line body is NOT a candidate",
          select_primary_candidate(w, [away], bc) is None)
    mark = Body(4, G_X, G_Y, role=DefenderRole.MARKER_A, facing=FACE_AT_BC)
    check("G7 marker is NOT a candidate",
          select_primary_candidate(w, [mark], bc) is None)
    third = Body(5, G_X, G_Y, facing=FACE_AT_BC, tackle_role="THIRD")
    check("G8 THIRD stays joiner-only (never a candidate)",
          select_primary_candidate(w, [third], bc) is None)
    fz = Body(6, G_X, G_Y, facing=FACE_AT_BC)
    fz.frozen = True
    check("G9 frozen body is NOT a candidate",
          select_primary_candidate(w, [fz], bc) is None)
    w_tes = make_world(tes_active=True)
    check("G10 no forced candidacy during a live TES",
          select_primary_candidate(w_tes, [Body(7, G_X, G_Y, facing=FACE_AT_BC)],
                                   bc) is None)

    # A slot-eligible engage-role body is still preferred when it is closer.
    rival = Body(8, 60.0, 36.2, facing=math.pi, tackle_role="PRIMARY")
    guard_far = Body(9, 61.0, 35.0, facing=FACE_AT_BC)
    chosen = select_primary_candidate(w, [rival, guard_far], bc)
    check("G11 closer slot-eligible rival still wins when both qualify",
          chosen is rival, "got %r" % (chosen,))


# ────────────────────────────────────────────────────────────────────────
def test_h_non_forced_still_rejected():
    print("\n[H] slot-OUT and guard-INELIGIBLE bodies are still rejected")
    t = Tuners()
    w = make_world()
    bc = make_bc()

    # Slot-OUT on the live lane AND outside the tackle radius -> no guard.
    d = Body(1, 58.5, 32.0, tackle_role="PRIMARY",
             state=MovementState.CHASE)
    check("H0 genuinely slot-OUT and guard-ineligible",
          abs(BC_Y - d.y) >= 0.3 and distance_to_bc(d, bc) >= TACKLE_RADIUS_M)
    st = run_machine(w, d, bc, t)
    check("H1 machine refuses it (CLOSING_OFF_SLOT)",
          st is S.CLOSING_OFF_SLOT, "got %s" % st)
    check("H2 it never feeds TES",
          should_engage(w, d, bc, t, state=st) is False)
    check("H3 it gets no guard stamp", d.target_source is None,
          "got %r" % (d.target_source,))


# ────────────────────────────────────────────────────────────────────────
def test_i_wiring():
    print("\n[I] wiring: one predicate, every consumer")

    def src(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    base = os.path.join("game", "defence", "defence_lvl_1")
    consumers = {
        "movement_mode_decision.py": ("tackle_engage_guard",
                                      "is_tackle_engage_guard"),
        "pre_tackle_authority.py": ("tackle_engage_guard",
                                    "is_tackle_engage_guard"),
        "pretackle_machine.py": ("tackle_engage_guard",
                                 "is_tackle_engage_guard",
                                 "stamp_tes_tackler_role"),
        "pretackle_classifier.py": ("tackle_engage_guard",
                                    "is_tackle_engage_guard",
                                    "stamp_tes_tackler_role"),
    }
    for fname, needles in consumers.items():
        try:
            text = src(os.path.join(base, fname))
        except Exception as e:
            check("I %s readable" % fname, False, repr(e))
            continue
        missing = [n for n in needles if n not in text]
        check("I %s consumes the single guard predicate" % fname,
              not missing, "missing %s" % missing)

    # The guard module is the single definition.
    gsrc = src(os.path.join(base, "tackle_engage_guard.py"))
    check("I5 guard module defines the one predicate",
          "def is_tackle_engage_guard(" in gsrc)
    check("I6 guard module defines the TES role stamp",
          "def stamp_tes_tackler_role(" in gsrc)
    check("I7 guard module defines the target-source stamp",
          "def stamp_tackle_engage_guard(" in gsrc)
    check("I8 guard module declares the non-initiator roles",
          "NON_ENGAGE_ROLES" in gsrc and "THIRD" in gsrc)

    # try_pre_tackle is a delegate to the machine (the live engage gate).
    bsrc = src(os.path.join(base, "defensive_behaviour_lvl1.py"))
    check("I9 try_pre_tackle delegates to the machine",
          "run_machine" in bsrc and "should_engage" in bsrc)

    check("I10 default facing band is the documented 60 deg",
          TACKLE_ENGAGE_FACING_MAX_DEG == 60.0)
    check("I11 default radius mirrors the TES tackle radius (1.5 m)",
          TACKLE_RADIUS_M == 1.5)


def main():
    test_a_predicate()
    test_b_scope()
    test_c_preconditions()
    test_d_stamps()
    test_e_machine()
    test_f_classifier()
    test_g_live_path()
    test_h_non_forced_still_rejected()
    test_i_wiring()
    print("\n" + "=" * 60)
    print("RESULT: %d passed, %d failed" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
