"""Verify the 12-state pretackle machine.

Asserts the core behaviour of ``pretackle_machine`` which replaced the
binary ``try_pre_tackle`` engage decision with a single MovementState-driven
posture classification:

  A. Exactly 12 machine states + exactly 5 outcomes; every state maps to one
     outcome; every outcome is reachable; the TES-engage set is exactly 3.
  B. Genuine planted (HOLD + v=0 + _mmdl_moved) square-up -> SQUARE_UP_ENGAGE
     -> CLEAN_ENGAGE -> ``should_engage`` True.
  C. Close-range CLOSING (CHASE/SHOOT, no stale stance flag) head-on + slot-in
     -> CLOSING_HEAD_ON -> CLEAN_ENGAGE -> ``should_engage`` True.
  D. Flat-foot forced step-in (full-speed CHASE + ``pre_tackle_stance`` +
     NOT genuinely planted) -> FLATFOOT_FORCED -> FALL_OVER -> never feeds TES
     on distance alone (Finding 2).
  E. Slot-OUT body (lateral lane mismatch) is rejected even close + PRIMARY
     + planted (Finding 1) — measured on the defender's LIVE lane Y, the
     reference ``is_slot_tackle_eligible`` actually uses.  A body that is
     additionally RADIAL-IN and FACING the carrier is a live tackle attempt:
     the radial+facing ENGAGE GUARD forces the engage instead (E6-E9), while
     a radial-in but non-facing body is still rejected (E10).
  F. Wrong-footed BC (facing delta > threshold) -> STUNNED_UPRIGHT / wrong-foot
     -> never feeds TES (Finding 4).
  G. Wrong-foot baseline advances on EVERY call (per-frame derivative).
  H. Posture classes: LATERAL (SLIDE/DRIFT) -> LATERAL_SLIDE -> WRONG_FOOT;
     RETREAT -> RETREATING -> BOUNCE_BACK.
  I. Pre-condition failures (frozen / marker / wrong role / no BC / suppress)
     resolve to RETREATING (non-engage) so the machine is total.
  J. ``apply_outcome`` produces the physical effect (BounceBack pushes,
     FallOver stuns, WrongFoot recoils) and CLEAN_ENGAGE is a no-op.
  K. The machine module tree imports cleanly and ``try_pre_tackle`` delegates
     to it.

Run:  python verify_pretackle_machine.py
Exit 0 on success, 1 on failure.
"""

import sys
import math

from player.player import DefenderRole
from player.movement_states import MovementState

from game.defence.defence_lvl_1.pretackle_machine import (
    PretackleMachineState as S,
    PretackleOutcome as O,
    OUTCOME_BY_STATE,
    ENGAGE_STATES,
    run_machine,
    should_engage,
    apply_outcome,
    is_real_planted_posture,
    is_closing_posture,
    approach_angle_from_facing,
    wrong_foot_delta,
    read_posture,
    outcome_for_state,
    is_engage_state,
)
from game.defence.defence_lvl_1.pretackle_classifier import (
    is_engage_role,
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
    """Minimal stub carrying the fields the machine reads."""

    def __init__(self, *, x=60.0, y=35.0, vx=0.0, vy=0.0,
                 facing=0.0, tackle_role=None, defender_role=None,
                 movement_state=None, frozen=False, freeze_timer=0.0,
                 target_x=None, target_y=None, mass=90.0, tackle_skill=0.5,
                 _tackle_commit_active=False, prev_facing=None,
                 collision_priority=None, player_id=0,
                 pre_tackle_state=None, pre_tackle_stance=False,
                 footplanting=False, _mmdl_moved=False, **kwargs):
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
        self.pre_tackle_state = pre_tackle_state
        self.pre_tackle_stance = pre_tackle_stance
        self.footplanting = footplanting
        self._mmdl_moved = _mmdl_moved
        for k, v in kwargs.items():
            setattr(self, k, v)


class W:
    def __init__(self):
        self.ball_owner = "BC"
        self.open_play_engine = None
        # Compliance line = ruck_x + 10 = 50m; tests place defenders onside.
        self.ruck_x_m = 40.0
        # Not a TES phase.
        self.sub_state = None
        # Slot threshold (m) read by is_slot_tackle_eligible.
        self.tackle_slot_threshold_m = 0.3


class FixedRng:
    """Deterministic rng: random() always returns *val* (default 0.99 so
    the 5% base-random and any slip/miss rolls never fire)."""
    def __init__(self, val=0.99):
        self.val = val

    def random(self):
        return self.val


def make_tuners():
    class T:
        def __init__(self):
            self.rng = FixedRng(0.99)
            self.retreat_compliance_distance_m = 10.0
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
    return T()


# ────────────────────────────────────────────────────────────────────────
def test_a_enum_geometry():
    print("\n[A] enum geometry + mapping")
    states = list(S)
    check("A1 exactly 12 machine states", len(states) == 12,
          f"got {len(states)}")
    outcomes = list(O)
    check("A2 exactly 5 outcomes", len(outcomes) == 5,
          f"got {len(outcomes)}")
    check("A3 every state maps to a valid outcome",
          all(outcome_for_state(s) in outcomes for s in states))
    check("A4 engage set is exactly 3 states",
          len(ENGAGE_STATES) == 3, f"got {len(ENGAGE_STATES)}")
    check("A5 engage states are the clean-engage states",
          ENGAGE_STATES == {S.SQUARE_UP_ENGAGE, S.CLOSING_HEAD_ON,
                            S.COMMIT_PLANTED})
    check("A6 all 5 outcomes reachable",
          set(OUTCOME_BY_STATE.values()) == set(outcomes))
    check("A7 engage set only maps to CLEAN_ENGAGE",
          all(OUTCOME_BY_STATE[s] is O.CLEAN_ENGAGE for s in ENGAGE_STATES))
    check("A8 every non-engage state maps to a non-CLEAN outcome",
          all(OUTCOME_BY_STATE[s] is not O.CLEAN_ENGAGE
              for s in states if s not in ENGAGE_STATES))
    check("A9 is_engage_state agrees with the set",
          all(is_engage_state(s) == (s in ENGAGE_STATES) for s in states))


# ────────────────────────────────────────────────────────────────────────
def test_b_genuine_planted_engages():
    print("\n[B] genuine planted square-up engages")
    w = W()
    t = make_tuners()
    p = Dummy(x=58.5, y=35.0, vx=0.0, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.HOLD, target_y=35.0,
              mass=90.0, tackle_skill=0.8, _mmdl_moved=True)
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    check("B1 genuinely planted predicate", is_real_planted_posture(p) is True)
    st = run_machine(w, p, bc, t)
    check("B2 classified SQUARE_UP_ENGAGE", st is S.SQUARE_UP_ENGAGE,
          f"got {st}")
    check("B3 outcome CLEAN_ENGAGE",
          outcome_for_state(st) is O.CLEAN_ENGAGE)
    check("B4 should_engage True", should_engage(w, p, bc, t, state=st) is True)


# ────────────────────────────────────────────────────────────────────────
def test_c_closing_head_on_engages():
    print("\n[C] closing head-on engages")
    w = W()
    t = make_tuners()
    # CHASE with NO stale pre_tackle_stance flag, head-on, slot-in.
    d = Dummy(x=58.5, y=35.0, vx=6.5, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.CHASE, target_y=35.0,
              mass=90.0, tackle_skill=0.8)
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    check("C1 is_closing_posture", is_closing_posture(d) is True)
    st = run_machine(w, d, bc, t)
    check("C2 classified CLOSING_HEAD_ON", st is S.CLOSING_HEAD_ON,
          f"got {st}")
    check("C3 should_engage True", should_engage(w, d, bc, t, state=st) is True)


# ────────────────────────────────────────────────────────────────────────
def test_d_flatfoot_forced_never_auto_engages():
    print("\n[D] flat-foot forced step-in is NOT a planted fast-path")
    w = W()
    t = make_tuners()
    # Full-speed CHASE + pre_tackle_stance flag + NOT genuinely planted.
    ff = Dummy(x=58.5, y=35.0, vx=8.0, vy=0.0, facing=0.0,
               tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
               movement_state=MovementState.CHASE, target_y=35.0,
               pre_tackle_stance=True, footplanting=True)
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    check("D1 full-speed CHASE is NOT real-planted",
          is_real_planted_posture(ff) is False)
    st = run_machine(w, ff, bc, t)
    check("D2 classified FLATFOOT_FORCED", st is S.FLATFOOT_FORCED,
          f"got {st}")
    check("D3 outcome FALL_OVER", outcome_for_state(st) is O.FALL_OVER)
    check("D4 does NOT feed TES (no distance auto-engage)",
          should_engage(w, ff, bc, t, state=st) is False)

    # Bad angle flat-foot (facing away) also never engages.
    bad = Dummy(x=58.5, y=35.0, vx=8.0, vy=0.0, facing=math.pi,
                tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
                movement_state=MovementState.CHASE, target_y=35.0,
                pre_tackle_stance=True, footplanting=True)
    st_bad = run_machine(w, bad, bc, t)
    check("D5 bad-angle flat-foot classified FLATFOOT_FORCED",
          st_bad is S.FLATFOOT_FORCED, f"got {st_bad}")
    check("D6 bad-angle flat-foot never feeds TES",
          should_engage(w, bad, bc, t, state=st_bad) is False)


# ────────────────────────────────────────────────────────────────────────
def test_e_slot_out_rejected():
    print("\n[E] slot-OUT rejected on the LIVE lane + radial+facing guard")
    w = W()
    t = make_tuners()
    # Slot-OUT is measured on the defender's LIVE lane ``y`` — the reference
    # ``is_slot_tackle_eligible`` uses — NOT on ``target_y``.  This defender
    # sits 3.0 m off the carrier's lane AND 3.35 m away, so the radial+facing
    # engage GUARD cannot fire either: this is the pure slot-OUT case.
    d = Dummy(x=58.5, y=32.0, vx=0.0, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.HOLD, target_x=58.5, target_y=32.0,
              mass=90.0, tackle_skill=0.8, _mmdl_moved=True)
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    check("E0 defender is genuinely slot-OUT on its live lane",
          abs(float(bc.y) - float(d.y)) >= 0.3)
    st = run_machine(w, d, bc, t)
    check("E1 slot-OUT stationary classified SQUARE_UP_OFF_SLOT",
          st is S.SQUARE_UP_OFF_SLOT, f"got {st}")
    check("E2 outcome WRONG_FOOT", outcome_for_state(st) is O.WRONG_FOOT)
    check("E3 should_engage False", should_engage(w, d, bc, t, state=st) is False)

    # Same for a CLOSING slot-OUT body.
    c = Dummy(x=58.5, y=32.0, vx=6.5, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.CHASE, target_y=32.0)
    st_c = run_machine(w, c, bc, t)
    check("E4 slot-OUT closing classified CLOSING_OFF_SLOT",
          st_c is S.CLOSING_OFF_SLOT, f"got {st_c}")
    check("E5 slot-OUT closing never feeds TES",
          should_engage(w, c, bc, t, state=st_c) is False)

    # ── RADIAL+FACING TACKLE ENGAGE GUARD (2026-09-10) ──
    # A body that IS radial-in AND facing the carrier has a live tackle
    # attempt and MUST engage even though its lane Y is slot-OUT: exactly the
    # "no tackles happen ever" configuration the guard exists for.
    FACING_AT_BC = math.atan2(36.0 - 35.0, 60.0 - 61.0)   # atan2(1, -1)
    bc_g = Dummy(x=60.0, y=36.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    g = Dummy(x=61.0, y=35.0, vx=6.5, vy=0.0, facing=FACING_AT_BC,
              defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.CHASE, target_y=35.0)
    check("E6 guarded body is slot-OUT (lane Y off the carrier)",
          abs(float(bc_g.y) - float(g.y)) >= 0.3)
    check("E6b guarded body carries no role yet (DEF_LINE_READY)",
          getattr(g, "tackle_role", None) is None)
    st_g = run_machine(w, g, bc_g, t)
    check("E7 radial+facing slot-OUT body STILL engages (CLOSING_HEAD_ON)",
          st_g is S.CLOSING_HEAD_ON, f"got {st_g}")
    check("E8 guard-engaged body feeds TES",
          should_engage(w, g, bc_g, t, state=st_g) is True)
    check("E9 guard stamps the TES engage role on the body",
          getattr(g, "tackle_role", None) == "PRIMARY",
          f"got {getattr(g, 'tackle_role', None)!r}")

    # A radial-in body NOT facing the carrier is NOT forced — that is the
    # "radial proximity is not a tackle" case the slot gate protected.
    g2 = Dummy(x=61.0, y=35.0, vx=6.5, vy=0.0, facing=0.0,
               defender_role=DefenderRole.DEF_LINE_READY,
               movement_state=MovementState.CHASE, target_y=35.0)
    st_g2 = run_machine(w, g2, bc_g, t)
    check("E10 radial-in but NOT facing is not forced",
          st_g2 is S.RETREATING, f"got {st_g2}")


# ────────────────────────────────────────────────────────────────────────
def test_f_wrong_footed_rejected():
    print("\n[F] wrong-footed BC never feeds TES")
    w = W()
    t = make_tuners()
    # BC facing delta > threshold (0 -> pi).
    d = Dummy(x=58.5, y=35.0, vx=6.5, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.CHASE, target_y=35.0)
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=math.pi, prev_facing=0.0)
    st = run_machine(w, d, bc, t)
    check("F1 wrong-footed closing classified CLOSING_WRONG_FOOTED",
          st is S.CLOSING_WRONG_FOOTED, f"got {st}")
    check("F2 outcome STUNNED_UPRIGHT", outcome_for_state(st) is O.STUNNED_UPRIGHT)
    check("F3 should_engage False", should_engage(w, d, bc, t, state=st) is False)

    # Stationary wrong-footed.
    p = Dummy(x=58.5, y=35.0, vx=0.0, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.HOLD, target_y=35.0,
              _mmdl_moved=True)
    bc2 = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=math.pi, prev_facing=0.0)
    st_p = run_machine(w, p, bc2, t)
    check("F4 wrong-footed stationary classified SQUARE_UP_WRONG_FOOTED",
          st_p is S.SQUARE_UP_WRONG_FOOTED, f"got {st_p}")
    check("F5 should_engage False", should_engage(w, p, bc2, t, state=st_p) is False)


# ────────────────────────────────────────────────────────────────────────
def test_g_wrong_foot_baseline_advances_every_call():
    print("\n[G] wrong-foot baseline advances on every call (per-frame)")
    w = W()
    t = make_tuners()
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    _delta1 = wrong_foot_delta(bc, t)
    check("G1 first-delta 0 when prev==current", _delta1 == 0.0)
    check("G2 baseline advanced to current", getattr(bc, "prev_facing", None) == 0.0)

    # BC flips facing; the delta is the per-frame derivative.
    bc.facing = math.pi
    _delta2 = wrong_foot_delta(bc, t)
    check("G3 large delta measured", abs(_delta2 - math.pi) < 1e-9,
          f"got {_delta2}")
    check("G4 baseline advanced to new facing", getattr(bc, "prev_facing", None) == math.pi)

    # Next call with no facing change -> delta ~0 (per-frame, not stale).
    _delta3 = wrong_foot_delta(bc, t)
    check("G5 subsequent delta ~0 (per-frame derivative)", abs(_delta3) < 1e-9,
          f"got {_delta3}")


# ────────────────────────────────────────────────────────────────────────
def test_h_lateral_and_retreat_classify():
    print("\n[H] LATERAL / RETREATING posture classes")
    w = W()
    t = make_tuners()
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)

    lat = Dummy(x=59.0, y=35.0, vx=0.0, vy=2.0, facing=0.0,
                tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
                movement_state=MovementState.SLIDE, target_y=35.0)
    st_lat = run_machine(w, lat, bc, t)
    check("H1 SLIDE classified LATERAL_SLIDE", st_lat is S.LATERAL_SLIDE,
          f"got {st_lat}")
    check("H2 outcome WRONG_FOOT", outcome_for_state(st_lat) is O.WRONG_FOOT)
    check("H3 should_engage False", should_engage(w, lat, bc, t, state=st_lat) is False)

    ret = Dummy(x=59.0, y=35.0, vx=-2.0, vy=0.0, facing=0.0,
                tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
                movement_state=MovementState.RETREAT, target_y=35.0)
    st_ret = run_machine(w, ret, bc, t)
    check("H4 RETREAT classified RETREATING", st_ret is S.RETREATING,
          f"got {st_ret}")
    check("H5 outcome BOUNCE_BACK", outcome_for_state(st_ret) is O.BOUNCE_BACK)
    check("H6 should_engage False", should_engage(w, ret, bc, t, state=st_ret) is False)


# ────────────────────────────────────────────────────────────────────────
def test_i_pre_condition_failures_total():
    print("\n[I] pre-condition failures resolve to RETREATING (machine is total)")
    w = W()
    t = make_tuners()
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    good = Dummy(x=58.5, y=35.0, vx=6.5, vy=0.0, facing=0.0,
                 tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
                 movement_state=MovementState.CHASE, target_y=35.0)

    # no BC
    st = run_machine(w, good, None, t)
    check("I1 no-BC -> RETREATING", st is S.RETREATING)
    # frozen
    fz = Dummy(x=58.5, y=35.0, vx=6.5, vy=0.0, facing=0.0,
               tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
               movement_state=MovementState.CHASE, target_y=35.0, frozen=True)
    st_fz = run_machine(w, fz, bc, t)
    check("I2 frozen -> RETREATING", st_fz is S.RETREATING)
    # marker
    mk = Dummy(x=58.5, y=35.0, vx=6.5, vy=0.0, facing=0.0,
               tackle_role="PRIMARY", defender_role=DefenderRole.MARKER_A,
               movement_state=MovementState.CHASE, target_y=35.0)
    st_mk = run_machine(w, mk, bc, t)
    check("I3 marker -> RETREATING", st_mk is S.RETREATING)
    # THIRD role (joiner-only)
    th = Dummy(x=58.5, y=35.0, vx=6.5, vy=0.0, facing=0.0,
               tackle_role="THIRD", defender_role=DefenderRole.DEF_LINE_READY,
               movement_state=MovementState.CHASE, target_y=35.0)
    st_th = run_machine(w, th, bc, t)
    check("I4 THIRD -> RETREATING", st_th is S.RETREATING)
    check("I5 is_engage_role rejects THIRD", is_engage_role(th) is False)


# ────────────────────────────────────────────────────────────────────────
def test_j_apply_outcome_physics():
    print("\n[J] apply_outcome physical effects")
    w = W()
    t = make_tuners()

    # BOUNCE_BACK (CLOSING_SIDE_ON).  Slot-IN is measured on the LIVE lane:
    # the carrier sits 0.1 m off the defender's Y.  The SIDE-ON band comes
    # from the defender's FACING being ~46 deg off the d->BC ray (a lateral
    # gap would be slot-OUT instead).  The 1.503 m separation is just outside
    # the 1.5 m guard radius, so the legacy classification path is exercised
    # here rather than the forced engage.
    bc = Dummy(x=60.0, y=35.1, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    side_d = Dummy(x=58.5, y=35.0, vx=6.5, vy=0.0, facing=math.radians(50.0),
                   tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
                   movement_state=MovementState.CHASE, target_y=35.0)
    st_side = run_machine(w, side_d, bc, t)
    check("J1 side-on closing classified CLOSING_SIDE_ON",
          st_side is S.CLOSING_SIDE_ON, f"got {st_side}")
    before_x = side_d.x
    out = apply_outcome(w, side_d, bc, t, state=st_side)
    check("J2 BOUNCE_BACK outcome returned", out is O.BOUNCE_BACK)
    check("J3 BOUNCE_BACK moved defender backward (x decreased)",
          side_d.x < before_x, f"before={before_x} after={side_d.x}")
    check("J4 BOUNCE_BACK zeroed velocity", side_d.vx == 0.0 and side_d.vy == 0.0)

    # FALL_OVER: FLATFOOT_FORCED -> stun + zero velocity.
    ff = Dummy(x=58.5, y=35.0, vx=8.0, vy=0.0, facing=0.0,
               tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
               movement_state=MovementState.CHASE, target_y=35.0,
               pre_tackle_stance=True, footplanting=True)
    ff_bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    st_ff = run_machine(w, ff, ff_bc, t)
    out_ff = apply_outcome(w, ff, ff_bc, t, state=st_ff)
    check("J5 FALL_OVER outcome returned", out_ff is O.FALL_OVER)
    check("J6 FALL_OVER zeroed velocity", ff.vx == 0.0 and ff.vy == 0.0)
    check("J7 FALL_OVER set a stun freeze_timer", ff.freeze_timer > 0.0)

    # WRONG_FOOT: lateral recoil.
    # WRONG_FOOT: a genuinely slot-OUT stationary body (live lane 3.0 m off
    # the carrier, 3.35 m away so the guard cannot fire) -> SQUARE_UP_OFF_SLOT.
    wf_d = Dummy(x=58.5, y=32.0, vx=0.0, vy=0.0, facing=0.0,
                 tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
                 movement_state=MovementState.HOLD, target_y=32.0, _mmdl_moved=True)
    wf_bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    st_wf = run_machine(w, wf_d, wf_bc, t)
    before_y = wf_d.y
    out_wf = apply_outcome(w, wf_d, wf_bc, t, state=st_wf)
    check("J8 WRONG_FOOT outcome returned", out_wf is O.WRONG_FOOT)
    check("J9 WRONG_FOOT recoiled laterally", abs(wf_d.y - before_y) > 1e-9,
          f"before={before_y} after={wf_d.y}")

    # CLEAN_ENGAGE: no-op.
    ce = Dummy(x=58.5, y=35.0, vx=0.0, vy=0.0, facing=0.0,
               tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
               movement_state=MovementState.HOLD, target_y=35.0, _mmdl_moved=True)
    ce_bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    st_ce = run_machine(w, ce, ce_bc, t)
    x_before = ce.x
    out_ce = apply_outcome(w, ce, ce_bc, t, state=st_ce)
    check("J10 CLEAN_ENGAGE outcome returned", out_ce is O.CLEAN_ENGAGE)
    check("J11 CLEAN_ENGAGE is a no-op (position unchanged)",
          ce.x == x_before and ce.vx == 0.0 and ce.vy == 0.0)


# ────────────────────────────────────────────────────────────────────────
def test_k_import_and_delegation():
    print("\n[K] module tree imports + try_pre_tackle delegates")
    import importlib
    mods = [
        "game.defence.defence_lvl_1.pretackle_machine",
        "game.defence.defence_lvl_1.pretackle_classifier",
        "game.defence.defence_lvl_1.defensive_behaviour_lvl1",
        "game.defence.defence_lvl_1.pre_tackle_authority",
    ]
    ok = True
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            ok = False
            print(f"      [import FAIL] {m}: {e}")
    check("K1 all modules import", ok)

    import re
    with open("game/defence/defence_lvl_1/defensive_behaviour_lvl1.py",
              encoding="utf-8") as f:
        src = f.read()
    check("K2 try_pre_tackle delegates to run_machine/should_engage",
          "from game.defence.defence_lvl_1.pretackle_machine import" in src
          and "run_machine" in src and "should_engage" in src
          and "apply_outcome" in src)
    tun_src = ""
    try:
        with open("game/defence/defence_lvl_1/defensive_tuners.py",
                  encoding="utf-8") as f:
            tun_src = f.read()
    except Exception:
        pass
    check("K3 defence tuners carry the machine tuner block",
          "pretackle_machine_enabled" in tun_src
          and "machine_engage_distance_m" in tun_src)


def main():
    test_a_enum_geometry()
    test_b_genuine_planted_engages()
    test_c_closing_head_on_engages()
    test_d_flatfoot_forced_never_auto_engages()
    test_e_slot_out_rejected()
    test_f_wrong_footed_rejected()
    test_g_wrong_foot_baseline_advances_every_call()
    test_h_lateral_and_retreat_classify()
    test_i_pre_condition_failures_total()
    test_j_apply_outcome_physics()
    test_k_import_and_delegation()
    print(f"\n{'='*60}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
