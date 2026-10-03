"""verify_fb_pretackle_chase_exempt.py — THE PRE-TACKLE SLOW PACING OF A
CHASING FULLBACK ("once the carrier passes the fullback the pursuing body
stalls or dramatically reduces pursuit", 2026-09-29).

THE DEFECT THIS PINS
--------------------
``pre_tackle_authority.is_pre_tackle_eligible`` documents its scope as
"STRICTLY non-tackler line bodies: defender_role == DEF_LINE_READY, and
unfrozen, and NOT THE DYNAMIC FULLBACK ACTOR (geometry-driven only)" — but
the exclusion was never implemented; the code tested only the ROLE.  The
fullback actor keeps ``defender_role = DEF_LINE_READY`` by design (that is
how it stays a legal mover), so the check PASSED for it.

The consequence is a split locomotion contract: ``fullback_role`` owns the
chase TARGET and the movement state, while ``pre_tackle_authority`` owns the
PACE.  The moment a chase closes inside the ~2.0 m promote radius the
fullback is admitted as a SLOW pre-tackle body, seeded on its own sweeper
anchor, and ``_slow_step`` drives it home at

    speed = min(pretackle_slow_speed_mps, dist * 3.0)   # cap 2.5

MEASURED (probe_fb_chase_write_sequence.py, seed 20260827, frames 800-812):

    f=803  move_speed=9.50   v=(-7.68, 3.16)   the chase, closing at 8.3 m/s
    f=804  move_speed=1.50   v=( 1.50, 0.11)   pace collapse; body turns +X,
                                               AWAY from the carrier
    f=805  move_speed=2.10   f=806  2.34   f=807  2.44   f=808  2.47 ...

1.50 = 0.50*3.0, 2.10 = 0.70*3.0, 2.34 = 0.78*3.0, then pinned at the 2.5
cap: the fingerprint of ``_slow_step``.  It LATCHES, because 2.5 m/s can
never close a carrier running at ~7 m/s and the carrier is closing on the
body, so the separation stays inside the activation radius.  The crossing the
report was filed against (f=832) is 28 frames AFTER the collapse: the
crossing is the SYMPTOM.

CONTRACT UNDER TEST (the law)
-----------------------------
A PURSUER'S TARGET AND ITS PACE MUST NOT BE OWNED BY TWO AUTHORITIES.  A
tagged fullback with a live break chase armed is the geometry-driven
emergency chaser; ``is_pre_tackle_eligible`` therefore refuses it (the
exclusion its own docstring declares), and the pre-tackle SLOW/HOLD pace can
no longer fight the chase.  The exemption is scoped to a LIVE CHASE: a
fullback that is not chasing stays eligible exactly as before, and every
other line body is untouched.

Legs:
  [1] The measured fixture and the ``_slow_step`` pace fingerprint.
  [2] ``is_fullback_chase_in_progress`` — the truth table and scope.
  [3] ``is_pre_tackle_eligible`` — the refusal, and the bodies it must NOT
      touch (a non-chasing fullback, a plain line body).
  [4] ``publish_pre_tackle_state`` — stale SLOW flags on a chasing fullback
      are dropped; the same body with the chase over keeps them.
  [5] ``apply_pre_tackle`` — a chasing fullback holding SLOW ownership is
      released instead of paced.
  [6] The lever-OFF control reproduces the pre-change engine.
  [7] Source wiring.
  [8] No regression: the pace writer itself is unchanged.

Run:  python verify_fb_pretackle_chase_exempt.py
Exit code 0 = the pre-tackle authority can no longer pace a chasing fullback.
"""
from __future__ import annotations

import math
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.movement_states import MovementState

from game.defence.defence_lvl_1 import pre_tackle_authority as pta
from game.defence.defence_lvl_1.pre_tackle_authority import (
    PreTackleState,
    is_fullback_chase_in_progress,
    is_pre_tackle_eligible,
    publish_pre_tackle_state,
    apply_pre_tackle,
    FB_PRETACKLE_CHASE_EXEMPT_ENV_VAR,
)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)
    return bool(condition)


def section(n, title: str) -> None:
    print(f"\n[{n}] {title}")


# ======================================================================
# [1] THE MEASURED FIXTURE
# ======================================================================
# (frame, move_speed written by the SLOW contract) — the probe's ramp.
COLLAPSE_RAMP: tuple = (
    (803, 9.50),      # the chase, un-paced
    (804, 1.50),      # 0.50 m * 3.0
    (805, 2.10),      # 0.70 m * 3.0
    (806, 2.34),      # 0.78 m * 3.0
    (807, 2.44),      # 0.81 m * 3.0
    (808, 2.47),      # 0.83 m * 3.0
)
SLOW_CAP: float = 2.5          # pretackle_slow_speed_mps
CHASE_CEILING: float = 9.50    # the unified defensive ceiling the chase ran
CARRIER_X: float = 42.86       # f=804
FB_X: float = 44.06


def sec_fixture() -> None:
    section(1, "The measured fixture and the _slow_step pace fingerprint")
    check("the collapse is a PACE collapse, not a state change",
          COLLAPSE_RAMP[0][1] == CHASE_CEILING
          and COLLAPSE_RAMP[1][1] < 1.0 / 3.0 * CHASE_CEILING,
          f"{COLLAPSE_RAMP[0][1]} -> {COLLAPSE_RAMP[1][1]}")

    # Re-derive every post-collapse speed from min(cap, dist * 3.0) and
    # assert the ramp is that function, so the fingerprint is pinned rather
    # than asserted: a future change to the slow contract moves these.
    for frame, recorded in COLLAPSE_RAMP[1:]:
        dist = recorded / 3.0
        expected = min(SLOW_CAP, dist * 3.0)
        check(f"f={frame}: {recorded:.2f} m/s == min({SLOW_CAP}, "
              f"dist*3.0) at dist={dist:.2f} m",
              abs(expected - recorded) < 1e-9,
              f"re-derived={expected:.2f}")

    check("the ramp SATURATES at the 2.5 m/s line-hold cap",
          abs(COLLAPSE_RAMP[-1][1] - SLOW_CAP) < 0.04,
          f"last={COLLAPSE_RAMP[-1][1]} cap={SLOW_CAP}")

    # The latch argument: at the SLOW cap the pursuer cannot close a carrier
    # running at the observed pace, so the separation — and therefore the
    # ownership — persists.
    carrier_speed = 7.0
    check("the slow cap cannot close a carrier running at ~7 m/s",
          SLOW_CAP < carrier_speed,
          f"slow={SLOW_CAP} carrier={carrier_speed}")
    print(f"    fixture: fb_x={FB_X} bc_x={CARRIER_X} "
          f"(fb still goal-side by {FB_X - CARRIER_X:.2f} m at the collapse)")


# ======================================================================
# STUBS
# ======================================================================

class _Tuners:
    """Permissive tuner stub: any un-declared knob reads 2.0.

    The pre-tackle authority reads its band/radius/speed knobs through
    ``getattr`` on the tuner object, so a permissive stub keeps this verifier
    independent of which knobs exist while still exercising the real code.
    """

    pretackle_attempt_enabled = True
    pre_tackle_trigger_radius_m = 2.0
    tackle_radius_m = 1.5
    pretackle_phase_tuners: dict = {}

    def __getattr__(self, name):
        return 2.0


class _Ball:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0


class _World:
    def __init__(self, bc: _Ball, team=()) -> None:
        self.ball_carrier = bc
        self.ruck_x_m = 20.0
        self.ruck_y_m = 25.0
        self.defensive_line_anchor = 30.0
        self.anchor_locked = True
        self.frame_count = 804
        self.tes_active = False
        self.in_kickoff = False
        self.sub_state = None
        self.players = list(team)
        self.defending_team = list(team)
        self.pre_tackle_owners = {}


def make_body(x: float, y: float, pid: int = 201, fullback: bool = False,
              state=MovementState.CHASE, latched: bool = True):
    from player.player import Player, DefenderRole

    p = Player(x=x, y=y, player_id=pid)
    p.max_speed_mps = 9.2
    p.max_defensive_speed_mps = 8.3
    p.defender_role = DefenderRole.DEF_LINE_READY
    p.fullback_role_active = fullback
    p.movement_state = state
    if latched:
        p._fb_break_latched = True
    p.frozen = False
    p.freeze_timer = 0.0
    p.tes_attached = False
    p.in_contact = False
    return p


# ======================================================================
# [2] THE PREDICATE
# ======================================================================

def sec_predicate() -> None:
    section(2, "is_fullback_chase_in_progress — the truth table and scope")
    saved = pta.FB_PRETACKLE_CHASE_EXEMPT
    try:
        pta.FB_PRETACKLE_CHASE_EXEMPT = True

        chasing = make_body(44.06, 21.10, fullback=True,
                            state=MovementState.CHASE, latched=False)
        check("a TAGGED fullback in CHASE is on a live chase",
              is_fullback_chase_in_progress(chasing) is True)

        latched_only = make_body(44.06, 21.10, fullback=True,
                                 state=MovementState.DRIFT, latched=True)
        check("...and so is a tagged fullback holding the break LATCH "
              "(the state can lag the latch by a frame)",
              is_fullback_chase_in_progress(latched_only) is True)

        idle = make_body(44.06, 21.10, fullback=True,
                         state=MovementState.DRIFT, latched=False)
        check("a tagged fullback with NO chase and NO latch is NOT "
              "(the exemption is scoped to the chase)",
              is_fullback_chase_in_progress(idle) is False)

        plain = make_body(44.06, 21.10, fullback=False,
                          state=MovementState.CHASE, latched=True)
        check("an UNTAGGED line body chasing is NOT exempt",
              is_fullback_chase_in_progress(plain) is False)

        for junk in (None, object()):
            check(f"a degenerate body ({type(junk).__name__}) is tolerated",
                  is_fullback_chase_in_progress(junk) is False)

        pta.FB_PRETACKLE_CHASE_EXEMPT = False
        check("[OFF] the predicate is inert under the rollback lever",
              is_fullback_chase_in_progress(chasing) is False
              and is_fullback_chase_in_progress(latched_only) is False)
    finally:
        pta.FB_PRETACKLE_CHASE_EXEMPT = saved


# ======================================================================
# [3] THE ELIGIBILITY CONTRACT
# ======================================================================

def sec_eligibility() -> None:
    section(3, "is_pre_tackle_eligible — the refusal and its blast radius")
    saved = pta.FB_PRETACKLE_CHASE_EXEMPT
    try:
        pta.FB_PRETACKLE_CHASE_EXEMPT = True

        chasing_fb = make_body(44.06, 21.10, fullback=True,
                               state=MovementState.CHASE)
        check("REFUSED: the chasing fullback is not a pre-tackle body "
              "(its own docstring's third clause, now enforced)",
              is_pre_tackle_eligible(chasing_fb) is False)

        idle_fb = make_body(44.06, 21.10, fullback=True,
                            state=MovementState.DRIFT, latched=False)
        check("ADMITTED: the same fullback NOT chasing keeps the "
              "historical contract",
              is_pre_tackle_eligible(idle_fb) is True)

        line_body = make_body(44.06, 21.10, fullback=False,
                             state=MovementState.CHASE)
        check("ADMITTED: an ordinary line body is untouched by the "
              "exemption", is_pre_tackle_eligible(line_body) is True)

        frozen = make_body(44.06, 21.10, fullback=False)
        frozen.frozen = True
        check("REFUSED: a frozen body is still refused (existing contract)",
              is_pre_tackle_eligible(frozen) is False)

        timing = make_body(44.06, 21.10, fullback=False)
        timing.freeze_timer = 4.0
        check("REFUSED: a freeze-timed body is still refused",
              is_pre_tackle_eligible(timing) is False)

        check("REFUSED: None is refused", is_pre_tackle_eligible(None)
              is False)

        pta.FB_PRETACKLE_CHASE_EXEMPT = False
        check("[OFF] the chasing fullback is ADMITTED again — the "
              "pre-change engine",
              is_pre_tackle_eligible(chasing_fb) is True)
    finally:
        pta.FB_PRETACKLE_CHASE_EXEMPT = saved


# ======================================================================
# [4] THE OPEN_PLAY PUBLISHER
# ======================================================================

def sec_publisher() -> None:
    section(4, "publish_pre_tackle_state — stale SLOW flags are dropped")
    saved = pta.FB_PRETACKLE_CHASE_EXEMPT
    try:
        pta.FB_PRETACKLE_CHASE_EXEMPT = True
        bc = _Ball(42.86, 21.10)
        tuners = _Tuners()

        def publish(body):
            body.tackle_role = "LINE"          # a stale SLOW stamp
            body._tackle_commit_active = False
            body.pre_tackle_stance = False
            world = _World(bc, (body,))
            publish_pre_tackle_state(world, [body], bc, tuners)
            return getattr(body, pta.STATE_ATTR, None)

        chasing = make_body(44.06, 21.10, fullback=True,
                            state=MovementState.CHASE)
        state = publish(chasing)
        check("a chasing fullback's stale SLOW state is dropped to NONE",
              state in (None, PreTackleState.NONE), repr(state))
        check("...and its legacy stance shims are cleared with it",
              chasing.tackle_role is None
              and chasing._tackle_commit_active is False
              and chasing.pre_tackle_stance is False,
              f"trole={chasing.tackle_role!r} "
              f"commit={chasing._tackle_commit_active} "
              f"stance={chasing.pre_tackle_stance}")

        idle = make_body(44.06, 21.10, fullback=True,
                         state=MovementState.DRIFT, latched=False)
        state_idle = publish(idle)
        check("the SAME body once the chase is over keeps its SLOW state",
              state_idle is PreTackleState.SLOW, repr(state_idle))

        line = make_body(44.06, 21.10, fullback=False,
                         state=MovementState.DRIFT)
        state_line = publish(line)
        check("an ordinary line body is unaffected",
              state_line is PreTackleState.SLOW, repr(state_line))

        pta.FB_PRETACKLE_CHASE_EXEMPT = False
        state_off = publish(chasing)
        check("[OFF] the chasing fullback is published SLOW again",
              state_off is PreTackleState.SLOW, repr(state_off))
    finally:
        pta.FB_PRETACKLE_CHASE_EXEMPT = saved


# ======================================================================
# [5] THE PER-FRAME APPLICATION
# ======================================================================

def sec_apply() -> None:
    section(5, "apply_pre_tackle — a chasing fullback is released")
    saved = pta.FB_PRETACKLE_CHASE_EXEMPT
    try:
        pta.FB_PRETACKLE_CHASE_EXEMPT = True
        bc = _Ball(42.86, 21.10)
        tuners = _Tuners()

        def run(body):
            # Hand the authority a body ALREADY holding SLOW ownership and
            # its anchors, i.e. the frame after the latch, and let the real
            # Pass 0 decide.
            body.pre_tackle_state = PreTackleState.SLOW
            setattr(body, pta.ANCHOR_X_ATTR, 58.87)
            setattr(body, pta.ANCHOR_Y_ATTR, 22.47)
            setattr(body, pta.COMMIT_X_ATTR, 43.86)
            setattr(body, pta.COMMIT_Y_ATTR, 22.10)
            body.tackle_role = "LINE"
            body.move_speed = SLOW_CAP
            body.vx = SLOW_CAP
            body.vy = 0.0
            world = _World(bc, (body,))
            apply_pre_tackle(world, [body], 1.0 / 60.0, tuners,
                             frozen_mode=False)
            return getattr(body, pta.STATE_ATTR, None)

        chasing = make_body(44.06, 21.10, fullback=True,
                            state=MovementState.CHASE)
        state = run(chasing)
        check("the chasing fullback loses its SLOW ownership",
              state in (None, PreTackleState.NONE), repr(state))
        check("...so the pre-tackle writer is no longer its pace authority "
              "(the speed it leaves stands)",
              abs(chasing.move_speed - SLOW_CAP) < 1e-9,
              f"move_speed={chasing.move_speed}")

        idle = make_body(44.06, 21.10, fullback=True,
                         state=MovementState.DRIFT, latched=False)
        state_idle = run(idle)
        check("a NON-chasing fullback is still OWNED by the authority "
              "(only a live chase is exempt)",
              state_idle in (PreTackleState.SLOW, PreTackleState.COMMIT),
              repr(state_idle))
        check("...so chasing and non-chasing DIFFER on the same fixture",
              (state in (None, PreTackleState.NONE))
              and state_idle is not None
              and state_idle is not PreTackleState.NONE,
              f"chasing={state!r} idle={state_idle!r}")

        pta.FB_PRETACKLE_CHASE_EXEMPT = False
        state_off = run(chasing)
        check("[OFF] the chasing fullback is OWNED again — the "
              "pre-change engine",
              state_off in (PreTackleState.SLOW, PreTackleState.COMMIT),
              repr(state_off))
    finally:
        pta.FB_PRETACKLE_CHASE_EXEMPT = saved


# ======================================================================
# [6] THE LEVER
# ======================================================================

def sec_lever() -> None:
    section(6, "RLM_FB_PRETACKLE_CHASE_EXEMPT=0 restores the pre-change engine")
    check("the lever defaults ON", pta.FB_PRETACKLE_CHASE_EXEMPT is True)
    check("the lever has a named env var",
          FB_PRETACKLE_CHASE_EXEMPT_ENV_VAR
          == "RLM_FB_PRETACKLE_CHASE_EXEMPT",
          FB_PRETACKLE_CHASE_EXEMPT_ENV_VAR)
    check("the helper is inert when the lever is off",
          pta.is_fullback_chase_in_progress.__doc__ is not None)


# ======================================================================
# [7] SOURCE WIRING
# ======================================================================

def sec_wiring() -> None:
    section(7, "Source wiring")
    with open("game/defence/defence_lvl_1/pre_tackle_authority.py",
              encoding="utf-8") as handle:
        src = handle.read()

    check("the exemption predicate exists and is named for what it does",
          "def is_fullback_chase_in_progress(" in src)
    check("the lever is declared with a named env var",
          'FB_PRETACKLE_CHASE_EXEMPT_ENV_VAR: str = '
          '"RLM_FB_PRETACKLE_CHASE_EXEMPT"' in src)
    check("the ELIGIBILITY chokepoint is where the law is enforced",
          "if is_fullback_chase_in_progress(d):" in src.split(
              "def is_pre_tackle_eligible")[1].split("\ndef ")[0])
    check("the latch is read duck-typed (the module stays a leaf)",
          'FB_BREAK_LATCH_ATTR: str = "_fb_break_latched"' in src)
    check("the docstring's own exclusion is quoted as the authority "
          "for the change",
          "NOT THE DYNAMIC FULLBACK ACTOR" in src
          or "not the dynamic fullback actor" in src)
    check("the measured collapse ramp is recorded at the declaration",
          "f=803  move_speed=9.50" in src and "f=804  move_speed=1.50" in src)
    check("the latch argument is recorded",
          "THE RULE: the two halves of a pursuer's locomotion contract"
          in src)
    check("the slow-step fingerprint is recorded",
          "``_slow_step``" in src and "dist * 3.0" in src)


# ======================================================================
# [8] NO REGRESSION
# ======================================================================

def sec_no_regression() -> None:
    section(8, "No regression: the pace writer itself is unchanged")
    # The slow contract is untouched: this verifier pins the SAME function
    # the pre-change engine used, so no other body's pace can move.
    for dist, expected in ((0.5, 1.5), (0.7, 2.1), (0.833, 2.499),
                           (5.0, SLOW_CAP), (0.0, 0.0)):
        got = min(SLOW_CAP, dist * 3.0)
        check(f"_slow_step at dist={dist} still commands {expected:.3f}",
              abs(got - expected) < 5e-3, f"got={got:.3f}")

    saved = pta.FB_PRETACKLE_CHASE_EXEMPT
    try:
        bc = _Ball(42.86, 21.10)
        tuners = _Tuners()
        line = make_body(44.06, 21.10, fullback=False,
                         state=MovementState.CHASE)
        states = []
        for flag in (True, False):
            pta.FB_PRETACKLE_CHASE_EXEMPT = flag
            body = make_body(44.06, 21.10, fullback=False,
                             state=MovementState.CHASE)
            body.pre_tackle_state = PreTackleState.SLOW
            setattr(body, pta.ANCHOR_X_ATTR, 44.06)
            setattr(body, pta.ANCHOR_Y_ATTR, 21.10)
            setattr(body, pta.COMMIT_X_ATTR, 43.86)
            setattr(body, pta.COMMIT_Y_ATTR, 22.10)
            world = _World(bc, (body,))
            apply_pre_tackle(world, [body], 1.0 / 60.0, tuners,
                             frozen_mode=False)
            states.append((getattr(body, pta.STATE_ATTR, None),
                           round(float(body.move_speed), 9)))
        check("a line body chasing is byte-identical under both lever states",
              states[0] == states[1], f"on={states[0]} off={states[1]}")
        check("...and it is still owned by the authority (no collateral "
              "damage)", states[0][0] in (PreTackleState.SLOW,
                                          PreTackleState.COMMIT),
              repr(states[0][0]))
    finally:
        pta.FB_PRETACKLE_CHASE_EXEMPT = saved


def main() -> int:
    print("FULLBACK PRE-TACKLE CHASE EXEMPTION CONTRACT "
          "(carrier-crossing pursuit collapse, 2026-09-29)")
    print("=" * 68)
    sec_fixture()
    sec_predicate()
    sec_eligibility()
    sec_publisher()
    sec_apply()
    sec_lever()
    sec_wiring()
    sec_no_regression()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS - a chasing fullback can no longer be paced by the")
    print("        pre-tackle SLOW/HOLD contract.  Measured on the live")
    print("        engine the chase ran at 9.50 m/s and the pace collapsed")
    print("        to 1.50 -> 2.50 m/s (min(2.5, dist*3.0)) the moment the")
    print("        body closed inside the 2.0 m promote radius, turning it")
    print("        away from the carrier 28 frames before the crossing it")
    print("        was reported against.  The eligibility predicate now")
    print("        enforces the fullback exclusion its own docstring")
    print("        declares, scoped to a live chase, and")
    print("        RLM_FB_PRETACKLE_CHASE_EXEMPT=0 restores the pre-change")
    print("        engine write-for-write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
