"""Verify that ``try_pre_tackle`` never MINTS a CLEAN_ENGAGE without feeding TES.

THE DEFECT THIS PINS (2026-10-01)
---------------------------------
``pretackle_machine.apply_outcome`` maps the three ENGAGE states onto
``CLEAN_ENGAGE`` — documented as "the ONLY outcome that feeds TES".  The sole
production caller, ``defensive_behaviour_lvl1.try_pre_tackle``, was:

    state = run_machine(...)
    if should_engage(..., state=state): return True
    apply_outcome(..., state=state)          # <-- even for an ENGAGE state
    return False

``should_engage`` runs the FULL engage window (contact geometry *including
distance* ``<= machine_engage_distance_m``, posture, planted, wrong-foot),
whereas ``run_machine`` has NO distance test.  So a defender that classified
``CLOSING_HEAD_ON`` (an engage state) while still further than the engage
distance reached ``apply_outcome`` with an ENGAGE state, which returned
``CLEAN_ENGAGE`` — a clean engage label on a frame in which NO TACKLE STARTED.

That is the "the fullback receives CLEAN_ENGAGE but never converts into a
tackle" artefact: every probe reading ``apply_outcome`` saw a clean engage that
never fed TES.  Measured live (probe_fb_clean_engage_conversion, seed 4): two
frames classified CLOSING_HEAD_ON at 2.45 m and 2.04 m, both minted
CLEAN_ENGAGE with no tackle.

THE CONTRACT NOW
----------------
``try_pre_tackle`` applies ``apply_outcome`` ONLY for NON-engage states.  An
engage state whose window did not open is simply not labelled (``apply_outcome``
is a documented NO-OP for CLEAN_ENGAGE, so physics is unchanged).  Genuine
non-engage outcomes (BOUNCE_BACK / FALL_OVER / STUNNED_UPRIGHT / WRONG_FOOT)
resolve exactly as before.

Run:  python verify_pretackle_no_clean_engage_without_tes.py
Exit 0 on success, 1 on failure.
"""

import math
import sys

from player.player import DefenderRole
from player.movement_states import MovementState

from game.defence.defence_lvl_1 import pretackle_machine as pm
from game.defence.defence_lvl_1.pretackle_machine import (
    PretackleMachineState as S,
    PretackleOutcome as O,
    ENGAGE_STATES,
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
        self.ruck_x_m = 40.0
        self.sub_state = None
        self.tackle_slot_threshold_m = 0.3


class FixedRng:
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


class OutcomeSpy:
    """Wrap ``pretackle_machine.apply_outcome`` and record every call."""

    def __init__(self):
        self.calls = []
        self._real = pm.apply_outcome

    def __enter__(self):
        def hook(world, defender, bc, tuners, state=None):
            self.calls.append(state)
            return self._real(world, defender, bc, tuners, state=state)
        pm.apply_outcome = hook
        return self

    def __exit__(self, *exc):
        pm.apply_outcome = self._real
        return False


# ────────────────────────────────────────────────────────────────────────
def test_a_engage_state_shut_by_distance_is_not_labelled():
    """The artefact: CLOSING_HEAD_ON but already beyond engage distance."""
    print("\n[A] engage state beyond engage distance mints NO CLEAN_ENGAGE")
    w = W()
    t = make_tuners()
    # 2.5 m from the carrier (engage distance is 2.0 m), head-on, slot-in,
    # CHASE -> classifies CLOSING_HEAD_ON, but the window is SHUT by distance.
    d = Dummy(x=57.5, y=35.0, vx=6.5, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.CHASE, target_y=35.0)
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)

    dist = math.hypot(bc.x - d.x, bc.y - d.y)
    check("A0 separation exceeds the engage distance",
          dist > t.machine_engage_distance_m, f"dist={dist}")

    st = run_machine(w, d, bc, t)
    check("A1 classified an ENGAGE state (CLOSING_HEAD_ON)",
          st is S.CLOSING_HEAD_ON and st in ENGAGE_STATES, f"got {st}")
    check("A2 should_engage False (distance conjunct shut the window)",
          should_engage(w, d, bc, t, state=st) is False)

    with OutcomeSpy() as spy:
        result = try_pre_tackle(w, d, bc, t)
    check("A3 try_pre_tackle False (no TES)", result is False)
    check("A4 apply_outcome NOT called for an engage state "
          "(no CLEAN_ENGAGE minted)", len(spy.calls) == 0,
          f"calls={spy.calls}")


# ────────────────────────────────────────────────────────────────────────
def test_b_genuine_engage_feeds_tes_without_outcome():
    """A genuine engage returns True and never reaches apply_outcome."""
    print("\n[B] genuine engage feeds TES, apply_outcome untouched")
    w = W()
    t = make_tuners()
    d = Dummy(x=58.5, y=35.0, vx=6.5, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.CHASE, target_y=35.0)
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)

    st = run_machine(w, d, bc, t)
    check("B1 classified CLOSING_HEAD_ON", st is S.CLOSING_HEAD_ON, f"got {st}")
    check("B2 should_engage True", should_engage(w, d, bc, t, state=st) is True)

    with OutcomeSpy() as spy:
        result = try_pre_tackle(w, d, bc, t)
    check("B3 try_pre_tackle True (TES fed)", result is True)
    check("B4 apply_outcome not called on the engage path",
          len(spy.calls) == 0, f"calls={spy.calls}")


# ────────────────────────────────────────────────────────────────────────
def test_c_non_engage_state_still_applies_outcome():
    """A NON-engage state still resolves through apply_outcome, unchanged."""
    print("\n[C] non-engage state still applies its physical outcome")
    w = W()
    t = make_tuners()
    # Genuinely slot-OUT stationary (3.0 m off lane, 3.35 m away: the
    # radial+facing guard cannot fire) -> SQUARE_UP_OFF_SLOT -> WRONG_FOOT.
    d = Dummy(x=58.5, y=32.0, vx=0.0, vy=0.0, facing=0.0,
              tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
              movement_state=MovementState.HOLD, target_x=58.5, target_y=32.0,
              mass=90.0, tackle_skill=0.8, _mmdl_moved=True)
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)

    st = run_machine(w, d, bc, t)
    check("C1 classified a NON-engage state (SQUARE_UP_OFF_SLOT)",
          st is S.SQUARE_UP_OFF_SLOT and st not in ENGAGE_STATES,
          f"got {st}")
    check("C2 should_engage False", should_engage(w, d, bc, t, state=st) is False)

    before_y = d.y
    with OutcomeSpy() as spy:
        result = try_pre_tackle(w, d, bc, t)
    check("C3 try_pre_tackle False", result is False)
    check("C4 apply_outcome called exactly once for the non-engage state",
          len(spy.calls) == 1, f"calls={spy.calls}")
    check("C5 WRONG_FOOT physics still applied (lateral recoil)",
          abs(d.y - before_y) > 1e-9, f"before={before_y} after={d.y}")


# ────────────────────────────────────────────────────────────────────────
def test_d_apply_outcome_contract_unchanged():
    """``apply_outcome`` itself still maps an engage state to CLEAN_ENGAGE."""
    print("\n[D] apply_outcome contract unchanged (J10/J11)")
    w = W()
    t = make_tuners()
    ce = Dummy(x=58.5, y=35.0, vx=0.0, vy=0.0, facing=0.0,
               tackle_role="PRIMARY", defender_role=DefenderRole.DEF_LINE_READY,
               movement_state=MovementState.HOLD, target_y=35.0, _mmdl_moved=True)
    bc = Dummy(x=60.0, y=35.0, vx=5.0, vy=0.0, facing=0.0, prev_facing=0.0)
    st = run_machine(w, ce, bc, t)
    x_before = ce.x
    out = pm.apply_outcome(w, ce, bc, t, state=st)
    check("D1 CLEAN_ENGAGE returned when called directly",
          out is O.CLEAN_ENGAGE)
    check("D2 no-op (position unchanged)", ce.x == x_before)


def main():
    test_a_engage_state_shut_by_distance_is_not_labelled()
    test_b_genuine_engage_feeds_tes_without_outcome()
    test_c_non_engage_state_still_applies_outcome()
    test_d_apply_outcome_contract_unchanged()
    print(f"\n{'='*60}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
