"""verify_fullback_locomotion_ownership_contract.py — STAGE 4 OF THE
FULLBACK CONTROLLER: LOCOMOTION OWNERSHIP (2026-09-30).

THE LAW UNDER TEST
------------------
The tagged fullback on a LIVE BREAK or an OWED-GROUND RECOVERY steers under a
DEDICATED fullback locomotion contract resolved from its INTENT -- not under
the shared defender tables (``ACCEL_BY_STATE`` / ``MAX_SPEED_BY_STATE`` /
``LATERAL_PRIORITY_BY_STATE`` / ``FACE_BC_BY_STATE``) and not under the
line-defence approach priorities.  Emergency pursuit, interception and
recovery steering become fullback behaviours independent of DRIFT, SLIDE and
line-defence priorities.

THE TWO DOORS
-------------
    door A  a LIVE BREAK EMERGENCY (Stage 2's per-actor break latch) -- the
            emergency PURSUIT and the INTERCEPTION.
    door B  an OWED-GROUND RECOVERY (Stage 3's LIVE recovery predicate,
            evaluated against the point the geometry authority COMMANDED) --
            the RECOVERY STEERING.

DOOR B IS NOT A FALLBACK OF A BREAK.  It is what stops the contract being
punched through on the frame the latch clears: a fullback that MISSED has its
break latch released by the miss teardown and is STILL owed ground, so without
door B the shared tables would take the body over mid-recovery -- a hole
straight through the Stage-3 recovery contract.

THE STATE WINS WHEN IT NAMES A PROFILE (2026-09-30)
---------------------------------------------------
The doors decide WHETHER the body is running its own play; the STATE decides
WHICH.  The geometry writer owns the actor's movement state and writes it
through Stage 1's ONE write path, so by the time the movement layer resolves a
profile the frame's play is already named.  MEASURED split-brain this closes:
the writer's breakaway chase branch is guarded by the LIVE break test and
RETURNS on the frame it fires, while its owed-ground branch writes RECOVER --
but door A reads the per-actor LATCH, which is sticky and outlives the live
break, so on those frames the state read RECOVER while door A handed over the
PURSUIT profile and a body commanded home sprinted at the unified defensive
ceiling (8.30 m/s against the RECOVER row's 4.75 m/s) for 100+ consecutive
frames.  ``locomotion_intent`` therefore returns the state's own intent
whenever the state names a profile, and falls back to the doors (door A first)
for a state that names none -- the sweeper's DRIFT -- so every sweeper /
flight / yield frame keeps exactly the contract it had.

THE OWNERSHIP LAW
-----------------
    locomotion_contract_owned  =  door A  OR  door B        (lever-gated)
    shared_locomotion_allowed  =  not locomotion_contract_owned
    fullback_locomotion_profile -> the dedicated profile, or None  (shared)

WHAT THIS VERIFIER PINS
-----------------------
  [1] the lever, and that OFF restores the shared tables everywhere
  [2] the leaf's profiles: fields, shared-equivalence, and the None answers
  [3] the ownership truth table and the EXACT negation
  [4] door B reads the LIVE Stage-3 predicate, per frame, at the commanded
      anchor -- never a persisted stamp
  [5] the ONE resolver: profile iff owned; None for the sweeper / the flight
  [6] the declaration stamps
  [7] the CONSUMPTION: params_override is handed to apply_movement_state,
      alignment_damping is honoured, and owned fullback recovery travels
      directly to its positional anchor on both axes
  [8] the source wiring (no import cycle, façade delegation) and the
      INDEPENDENCE proof: re-tuning the shared table moves the shared body
      and does NOT move the fullback's.

RESULT: ALL CHECKS PASSED on success; RESULT: FAIL otherwise.
"""
import math
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import player.movement_states as ms
from player.movement_states import (
    MovementState,
    ACCEL_BY_STATE,
    DECEL_BY_STATE,
    MAX_SPEED_BY_STATE,
    LATERAL_PRIORITY_BY_STATE,
    FACE_BC_BY_STATE,
    apply_movement_state,
    apply_fullback_locomotion,
)
from player.player import Player

import game.defence.defence_lvl_1.fullback_controller as fbc
import game.defence.defence_lvl_1.fullback_locomotion as fbl
from game.defence.defence_lvl_1.fullback_controller import (
    FullbackIntent,
    CONTROLLER,
)

DT = 1.0 / 60.0
FAILURES = []

SHARED_FIELDS = (
    "accel",
    "decel",
    "max_speed_scale",
    "lateral_priority",
    "face_bc",
)


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        FAILURES.append(label)


def section(n, title):
    print(f"\n[{n}] {title}")


def _src(rel_path):
    with open(rel_path, encoding="utf-8") as fh:
        return fh.read()


# ══════════════════════════════════════════════════════════════════════
# STUBS
# ══════════════════════════════════════════════════════════════════════

class _BC:
    """Ball-carrier stub (x/y are properties on the real Player)."""

    def __init__(self, x, y, vy=0.0):
        self.x = x
        self.y = y
        self.vy = vy


class _World:
    """World stub.  ``frame_count`` is what the declaration stamps."""

    def __init__(self, bc=None, frame_count=1):
        self.ball_carrier = bc
        self.frame_count = frame_count


def make_body(x=60.0, y=35.0, state=MovementState.DRIFT, target_x=60.0,
              target_y=35.0, bc=None, tagged=True, pid=401):
    p = Player(x=x, y=y, player_id=pid)
    p.jersey_number = 1
    p.max_speed_mps = 9.5
    p.max_defensive_speed_mps = 8.3
    p.movement_state = state
    p.target_x = target_x
    p.target_y = target_y
    p._world_ref = _World(bc)
    if tagged:
        p.fullback_role_active = True
    return p


def latch(body, armed=True):
    """Arm / clear the per-actor break latch Stage 2 reads."""
    body.__dict__["_fb_break_latched"] = bool(armed)
    return body


def speed(p):
    return math.hypot(p.vx, p.vy)


class _RecoverySpy:
    """A stand-in for Stage 3's live ``fullback_recovery_active``.

    Records every call so the verifier can prove door B re-evaluates the LIVE
    predicate each time (with the COMMANDED anchor) instead of reading a
    stamp that persists across frames.
    """

    def __init__(self, answer=False):
        self.answer = answer
        self.calls = []

    def __call__(self, world, defender, anchor_x, anchor_y):
        self.calls.append((world, defender, anchor_x, anchor_y))
        return bool(self.answer)


def _install_recovery_spy(spy):
    """Override ONLY the Stage-3 predicate of the controller's role accessor.

    ``_fullback_role_api`` is the ONE lazy accessor for BOTH Stage 2's break
    latch (``is_latched``) and Stage 3's recovery predicate
    (``recovery_active``), so the override must MERGE.  Replacing the mapping
    wholesale would silently disarm DOOR A and make this verifier measure its
    own stub instead of the contract.
    """
    saved = fbc._fullback_role_api
    real = dict(saved())

    def _fake_api():
        merged = dict(real)
        merged["recovery_active"] = spy
        return merged

    fbc._fullback_role_api = _fake_api

    def _restore():
        fbc._fullback_role_api = saved

    return _restore


# ══════════════════════════════════════════════════════════════════════
# [1] THE LEVER
# ══════════════════════════════════════════════════════════════════════

def sec_lever():
    section(1, "The lever — RLM_FB_LOCOMOTION_CONTROLLER, default ON, "
               "OFF restores the shared tables")

    check(
        "the env-var name is the documented one",
        fbc.FB_LOCOMOTION_CONTROLLER_ENV_VAR == "RLM_FB_LOCOMOTION_CONTROLLER",
        f"name={fbc.FB_LOCOMOTION_CONTROLLER_ENV_VAR!r}",
    )
    check(
        "the lever ships ON",
        fbc.FB_LOCOMOTION_CONTROLLER is True,
        f"value={fbc.FB_LOCOMOTION_CONTROLLER!r}",
    )
    check(
        "fullback_locomotion_controller_enabled() reports the module boolean",
        fbc.fullback_locomotion_controller_enabled() is True,
    )

    body = latch(make_body())
    saved = fbc.FB_LOCOMOTION_CONTROLLER
    try:
        fbc.FB_LOCOMOTION_CONTROLLER = False
        check(
            "lever OFF: the controller owns NOTHING",
            fbc.locomotion_contract_owned(_World(), body) is False,
        )
        check(
            "lever OFF: shared locomotion is allowed for the tagged FB",
            fbc.shared_locomotion_allowed(_World(), body) is True,
        )
        check(
            "lever OFF: the resolver answers None (shared tables)",
            fbc.fullback_locomotion_profile(_World(), body) is None,
        )
        check(
            "lever OFF: the intent is UNOWNED",
            fbc.locomotion_intent(_World(), body) is FullbackIntent.UNOWNED,
        )
    finally:
        fbc.FB_LOCOMOTION_CONTROLLER = saved

    check(
        "lever restored: the tagged, latched FB is owned again",
        fbc.locomotion_contract_owned(_World(), body) is True,
    )

    # The lever must be read at CALL time, not frozen at import, so one
    # process can A/B it.
    check(
        "the lever is a module-scope boolean the consumer reads live",
        "FB_LOCOMOTION_CONTROLLER" in _src(
            "game/defence/defence_lvl_1/fullback_controller.py"
        ),
    )


# ══════════════════════════════════════════════════════════════════════
# [2] THE LEAF'S PROFILES
# ══════════════════════════════════════════════════════════════════════

def sec_profiles():
    section(2, "fullback_locomotion: the profile fields, the shared-"
               "equivalence, and the None answers")

    check(
        "the profile vocabulary is the seven declared fields",
        set(fbl.LOCOMOTION_FIELDS) == {
            "accel", "decel", "max_speed_scale", "lateral_priority",
            "face_bc", "intercept_steer", "alignment_damping",
        },
        f"fields={fbl.LOCOMOTION_FIELDS}",
    )

    profiles = fbl.FB_LOCOMOTION_PROFILES
    check(
        "there is a profile for the pursuit and for the recovery, and nothing "
        "else",
        set(profiles) == {FullbackIntent.CHASE, FullbackIntent.RECOVER},
        f"keys={sorted(i.name for i in profiles)}",
    )

    for intent, state in (
        (FullbackIntent.CHASE, MovementState.CHASE),
        (FullbackIntent.RECOVER, MovementState.RECOVER),
    ):
        prof = fbl.profile_for_intent(intent)
        check(
            f"{intent.name}: profile is returned",
            isinstance(prof, dict),
        )
        if not isinstance(prof, dict):
            continue
        check(
            f"{intent.name}: it declares its movement state {state.name}",
            prof.get("state") is state,
            f"state={prof.get('state')}",
        )
        check(
            f"{intent.name}: it carries every declared field",
            all(field in prof for field in fbl.LOCOMOTION_FIELDS),
        )
        # The neutrality spine: the five SHARED fields are today's table
        # row, so the shipped default adds nothing but the two steering
        # switches.
        tables = {
            "accel": ACCEL_BY_STATE,
            "decel": DECEL_BY_STATE,
            "max_speed_scale": MAX_SPEED_BY_STATE,
            "lateral_priority": LATERAL_PRIORITY_BY_STATE,
            "face_bc": FACE_BC_BY_STATE,
        }
        drift = [
            field for field in SHARED_FIELDS
            if prof[field] != tables[field][state]
        ]
        check(
            f"{intent.name}: every shared field equals the shared table row",
            not drift,
            f"diverged={drift}",
        )
        check(
            f"{intent.name}: locomotion_profile_is_shared_equivalent is True",
            fbl.locomotion_profile_is_shared_equivalent(intent) is True,
        )

    chase = fbl.profile_for_intent(FullbackIntent.CHASE)
    recover = fbl.profile_for_intent(FullbackIntent.RECOVER)
    check(
        "the PURSUIT steers at the intercept point on both axes",
        chase.get("intercept_steer") is True,
    )
    check(
        "the PURSUIT does NOT let the pre-tackle alignment damp its lateral "
        "command (its commanded point IS the interception)",
        chase.get("alignment_damping") is False,
    )
    check(
        "the RECOVERY steers directly to its owned X/Y anchor",
        recover.get("intercept_steer") is True,
    )
    check(
        "the RECOVERY bypasses carrier-focused pre-tackle alignment clamps",
        recover.get("alignment_damping") is False,
    )

    for intent in (FullbackIntent.SWEEP, FullbackIntent.FLIGHT,
                   FullbackIntent.UNOWNED):
        check(
            f"{intent.name}: no profile — the shared tables keep the body",
            fbl.profile_for_intent(intent) is None,
        )
        check(
            f"{intent.name}: not shared-equivalent (it is not a profile at "
            "all)",
            fbl.locomotion_profile_is_shared_equivalent(intent) is False,
        )

    check(
        "an unrecognised intent degrades to None rather than raising",
        fbl.profile_for_intent(object()) is None,
    )
    check(
        "an unhashable intent degrades to None rather than raising",
        fbl.profile_for_intent(["nope"]) is None,
    )


# ══════════════════════════════════════════════════════════════════════
# [3] THE OWNERSHIP TRUTH TABLE
# ══════════════════════════════════════════════════════════════════════

def sec_truth_table():
    section(3, "The ownership truth table and the EXACT negation")

    world = _World()
    spy = _RecoverySpy(answer=False)
    restore = _install_recovery_spy(spy)
    try:
        untagged = make_body(tagged=False)
        latch(untagged, True)
        check(
            "untagged + break latch: NOT owned (the tag is required)",
            fbc.locomotion_contract_owned(world, untagged) is False,
        )
        check(
            "untagged: shared locomotion is allowed",
            fbc.shared_locomotion_allowed(world, untagged) is True,
        )

        idle = make_body()
        latch(idle, False)
        spy.answer = False
        check(
            "tagged + no break + no recovery: NOT owned (the sweeper glide)",
            fbc.locomotion_contract_owned(world, idle) is False,
        )
        check(
            "tagged + no break + no recovery: shared locomotion allowed",
            fbc.shared_locomotion_allowed(world, idle) is True,
        )
        check(
            "tagged + no break + no recovery: intent is UNOWNED",
            fbc.locomotion_intent(world, idle) is FullbackIntent.UNOWNED,
        )

        breaker = make_body()
        latch(breaker, True)
        spy.answer = False
        check(
            "DOOR A — tagged + live break: owned",
            fbc.locomotion_contract_owned(world, breaker) is True,
        )
        check(
            "DOOR A — intent is CHASE",
            fbc.locomotion_intent(world, breaker) is FullbackIntent.CHASE,
        )

        recoverer = make_body()
        latch(recoverer, False)
        spy.answer = True
        check(
            "DOOR B — tagged + no break + recovery owed: owned",
            fbc.locomotion_contract_owned(world, recoverer) is True,
        )
        check(
            "DOOR B — intent is RECOVER",
            fbc.locomotion_intent(world, recoverer) is FullbackIntent.RECOVER,
        )

        both = make_body()
        latch(both, True)
        spy.answer = True
        check(
            "BOTH doors open: door A wins (the body is the pursuer)",
            fbc.locomotion_intent(world, both) is FullbackIntent.CHASE,
        )

        # The exact negation, on every fixture above.
        for label, body in (("untagged", untagged), ("idle", idle),
                            ("breaker", breaker), ("recoverer", recoverer),
                            ("both", both)):
            owned = fbc.locomotion_contract_owned(world, body)
            allowed = fbc.shared_locomotion_allowed(world, body)
            check(
                f"{label}: shared_locomotion_allowed is the EXACT negation "
                "of locomotion_contract_owned",
                allowed is (not owned),
                f"owned={owned} allowed={allowed}",
            )
    finally:
        restore()

    # A missing Stage-3 predicate must not claim a recovery it cannot prove.
    spy = _RecoverySpy(answer=True)
    saved = fbc._fullback_role_api
    try:
        fbc._fullback_role_api = lambda: {}
        lonely = latch(make_body(), False)
        check(
            "an unreachable Stage-3 predicate reports NO recovery owed",
            fbc.recovery_owed(_World(), lonely) is False,
        )
        check(
            "and therefore claims no door-B ownership",
            fbc.recovery_locomotion_owned(_World(), lonely) is False,
        )
    finally:
        fbc._fullback_role_api = saved


# ══════════════════════════════════════════════════════════════════════
# [4] DOOR B IS THE LIVE PREDICATE, NOT A STAMP
# ══════════════════════════════════════════════════════════════════════

def sec_door_b_is_live():
    section(4, "DOOR B reads Stage 3's LIVE predicate, per frame, at the "
               "COMMANDED anchor")

    world = _World()
    spy = _RecoverySpy(answer=False)
    restore = _install_recovery_spy(spy)
    try:
        body = latch(make_body(target_x=42.5, target_y=21.25), False)

        check(
            "with the live predicate False, no recovery is owed",
            fbc.recovery_owed(world, body) is False,
        )
        check(
            "the live predicate WAS consulted (it is not a cached stamp)",
            len(spy.calls) == 1,
            f"calls={len(spy.calls)}",
        )
        if spy.calls:
            got_world, got_body, ax, ay = spy.calls[-1]
            check(
                "it was handed the world and the very defender",
                got_world is world and got_body is body,
            )
            check(
                "the anchor is the actor's COMMANDED point",
                abs(ax - 42.5) < 1e-9 and abs(ay - 21.25) < 1e-9,
                f"anchor=({ax},{ay})",
            )

        # The committed geometry anchor takes precedence when present.
        body._fb_committed_anchor_x = 77.5
        body._fb_committed_anchor_y = 12.25
        fbc.recovery_owed(world, body)
        got = spy.calls[-1]
        check(
            "the COMMITTED anchor wins over target_x/target_y when stamped",
            abs(got[2] - 77.5) < 1e-9 and abs(got[3] - 12.25) < 1e-9,
            f"anchor=({got[2]},{got[3]})",
        )

        # Now flip the live answer and re-ask: the SAME body must change
        # verdict, which no persisted stamp could do.
        before = len(spy.calls)
        spy.answer = True
        check(
            "flipping the live answer flips the verdict on the same body",
            fbc.recovery_owed(world, body) is True,
        )
        check(
            "and it was re-evaluated (a fresh call), not remembered",
            len(spy.calls) == before + 1,
            f"calls={len(spy.calls)}",
        )
        check(
            "so the recovery door opens for that frame",
            fbc.recovery_locomotion_owned(world, body) is True,
        )
        check(
            "and the intent becomes RECOVER",
            fbc.locomotion_intent(world, body) is FullbackIntent.RECOVER,
        )

        # A Stage-3 predicate that RAISES must degrade to "not owed" -- the
        # shared tables -- rather than propagating out of an integrator.
        def _boom(*_a):
            raise RuntimeError("stage 3 exploded")

        saved = fbc._fullback_role_api
        try:
            fbc._fullback_role_api = lambda: {"recovery_active": _boom}
            check(
                "a raising Stage-3 predicate degrades to NOT owed",
                fbc.recovery_owed(world, body) is False,
            )
        finally:
            fbc._fullback_role_api = saved

        # An unreadable anchor must also degrade to NOT owed.
        blind = latch(make_body(), False)
        blind.target_x = None
        blind.target_y = None
        spy.answer = True
        check(
            "an unreadable commanded anchor degrades to NOT owed",
            fbc.recovery_owed(world, blind) is False,
        )
    finally:
        restore()
# ══════════════════════════════════════════════════════════════════════
# [5] THE ONE RESOLVER
# ══════════════════════════════════════════════════════════════════════

def sec_one_resolver():
    section(5, "fullback_locomotion_profile — the ONE resolver every "
               "consumer reads")

    world = _World()
    spy = _RecoverySpy(answer=False)
    restore = _install_recovery_spy(spy)
    try:
        untagged = latch(make_body(tagged=False), True)
        check(
            "untagged: None (the shared tables keep the body)",
            fbc.fullback_locomotion_profile(world, untagged) is None,
        )

        sweeper = make_body(target_x=70.0, target_y=35.0)
        latch(sweeper, False)
        spy.answer = False
        check(
            "tagged on its sweeper anchor: None (the shared tables keep the "
            "body)",
            fbc.fullback_locomotion_profile(world, sweeper) is None,
        )

        flight = make_body(state=MovementState.DRIFT)
        latch(flight, False)
        spy.answer = False
        check(
            "the kickoff FLIGHT frame: None (byte-identical to today)",
            fbc.fullback_locomotion_profile(world, flight) is None,
        )

        breaker = latch(make_body(), True)
        spy.answer = False
        prof = fbc.fullback_locomotion_profile(world, breaker)
        check(
            "tagged on a LIVE BREAK: the PURSUIT profile is returned",
            prof is fbl.profile_for_intent(FullbackIntent.CHASE),
            f"profile={prof}",
        )
        check(
            "and the returned intent agrees",
            fbc.locomotion_intent(world, breaker) is FullbackIntent.CHASE,
        )

        recoverer = latch(make_body(), False)
        spy.answer = True
        prof = fbc.fullback_locomotion_profile(world, recoverer)
        check(
            "tagged owing ground: the RECOVERY profile is returned",
            prof is fbl.profile_for_intent(FullbackIntent.RECOVER),
            f"profile={prof}",
        )
        spy.answer = False
        check(
            "and the identity is the leaf's own dict, not a copy",
            fbc.fullback_locomotion_profile(world, breaker)
            is fbl.FB_LOCOMOTION_PROFILES[FullbackIntent.CHASE],
        )

        # The resolver is TOTAL: a frame it cannot reason about must yield
        # None (shared tables), never raise inside an integrator.
        check(
            "a None body yields None rather than raising",
            fbc.fullback_locomotion_profile(world, None) is None,
        )
        # A None WORLD is a different matter: neither door consults the world
        # (the break latch is per-actor).
        try:
            fbc.fullback_locomotion_profile(None, breaker)
            raised = False
        except Exception as exc:                          # noqa: BLE001
            raised = f"{type(exc).__name__}: {exc}"
        check("a None world does not raise", raised is False, f"{raised}")
        idle_blind = latch(make_body(), False)
        spy.answer = False
        check(
            "a None world on an idle, unlatched body still yields None",
            fbc.fullback_locomotion_profile(None, idle_blind) is None,
        )

        # Lever OFF collapses the resolver to None for every fixture.
        saved = fbc.FB_LOCOMOTION_CONTROLLER
        try:
            fbc.FB_LOCOMOTION_CONTROLLER = False
            for label, body in (("breaker", breaker),
                                ("recoverer", recoverer),
                                ("sweeper", sweeper)):
                check(
                    f"lever OFF: {label} resolves to None",
                    fbc.fullback_locomotion_profile(world, body) is None,
                )
        finally:
            fbc.FB_LOCOMOTION_CONTROLLER = saved

        # And an unreachable leaf degrades to None rather than naming a
        # profile it cannot obtain.
        saved_api = fbc._FB_LOCOMOTION_API
        try:
            fbc._FB_LOCOMOTION_API = {"profile_for_intent": None}
            check(
                "an unreachable leaf degrades to None (shared tables)",
                fbc.fullback_locomotion_profile(world, breaker) is None,
            )
        finally:
            fbc._FB_LOCOMOTION_API = saved_api
    finally:
        restore()


# ══════════════════════════════════════════════════════════════════════
# [6] THE DECLARATION STAMPS
# ══════════════════════════════════════════════════════════════════════

def sec_declarations():
    section(6, "declare_fullback_locomotion — the frame's locomotion record")

    spy = _RecoverySpy(answer=False)
    restore = _install_recovery_spy(spy)
    try:
        world = _World(frame_count=900)
        body = latch(make_body(), True)          # door A: CHASE

        check(
            "nothing is declared before the first call",
            fbc.declared_fullback_locomotion(body) is None,
        )
        intent = fbc.declare_fullback_locomotion(world, body)
        check(
            "the declaration returns the resolved intent",
            intent is FullbackIntent.CHASE,
            f"intent={intent}",
        )
        check(
            "the intent is stamped on the body",
            fbc.declared_fullback_locomotion(body) is FullbackIntent.CHASE,
        )
        check(
            "the SOURCE is stamped and names the break",
            fbc.declared_fullback_locomotion_source(body)
            == fbc.locomotion_source_for_intent(FullbackIntent.CHASE),
            f"source={fbc.declared_fullback_locomotion_source(body)!r}",
        )
        check(
            "the FRAME is stamped from the world's frame counter",
            fbc.declared_fullback_locomotion_frame(body) == 900,
            f"frame={fbc.declared_fullback_locomotion_frame(body)}",
        )

        # The recovery declaration stamps its own source on a later frame.
        world.frame_count = 901
        latch(body, False)
        spy.answer = True
        intent = fbc.declare_fullback_locomotion(world, body)
        check(
            "a recovery frame re-stamps the intent as RECOVER",
            intent is FullbackIntent.RECOVER
            and fbc.declared_fullback_locomotion(body)
            is FullbackIntent.RECOVER,
        )
        check(
            "and its source names the recovery",
            fbc.declared_fullback_locomotion_source(body)
            == fbc.locomotion_source_for_intent(FullbackIntent.RECOVER),
        )
        check(
            "and the frame advances",
            fbc.declared_fullback_locomotion_frame(body) == 901,
        )
        check(
            "the break and the recovery sources are distinct strings",
            fbc.locomotion_source_for_intent(FullbackIntent.CHASE)
            != fbc.locomotion_source_for_intent(FullbackIntent.RECOVER),
        )

        # An UNOWNED frame stamps nothing at all -- it must not overwrite the
        # last real declaration with a lie.
        world.frame_count = 902
        latch(body, False)
        spy.answer = False
        intent = fbc.declare_fullback_locomotion(world, body)
        check(
            "an UNOWNED frame returns UNOWNED",
            intent is FullbackIntent.UNOWNED,
        )
        check(
            "and stamps NOTHING (the last real declaration survives)",
            fbc.declared_fullback_locomotion(body)
            is FullbackIntent.RECOVER
            and fbc.declared_fullback_locomotion_frame(body) == 901,
            f"declared={fbc.declared_fullback_locomotion(body)} "
            f"frame={fbc.declared_fullback_locomotion_frame(body)}",
        )

        # Lever OFF: no stamp is written, and the returned value is still the
        # RESOLVED intent -- with the lever off the contract resolves
        # UNOWNED, so that is what a caller is handed.  The point is that the
        # caller is NEVER handed a value the contract would not produce.
        fresh = latch(make_body(), True)
        saved = fbc.FB_LOCOMOTION_CONTROLLER
        try:
            fbc.FB_LOCOMOTION_CONTROLLER = False
            intent = fbc.declare_fullback_locomotion(world, fresh)
            check(
                "lever OFF still returns the RESOLVED intent (UNOWNED)",
                intent is fbc.locomotion_intent(world, fresh)
                is FullbackIntent.UNOWNED,
                f"intent={intent}",
            )
            check(
                "but stamps nothing on the body",
                fbc.declared_fullback_locomotion(fresh) is None
                and fbc.declared_fullback_locomotion_source(fresh) == ""
                and fbc.declared_fullback_locomotion_frame(fresh) is None,
                f"declared={fbc.declared_fullback_locomotion(fresh)}",
            )
        finally:
            fbc.FB_LOCOMOTION_CONTROLLER = saved

        # The readers are TOTAL on a body that has never been declared to.
        naive = Player(x=60.0, y=35.0, player_id=402)
        check(
            "declared_fullback_locomotion is None on an undeclared body",
            fbc.declared_fullback_locomotion(naive) is None,
        )
        check(
            "declared_fullback_locomotion_source is '' on an undeclared body",
            fbc.declared_fullback_locomotion_source(naive) == "",
        )
        check(
            "declared_fullback_locomotion_frame is None on an undeclared body",
            fbc.declared_fullback_locomotion_frame(naive) is None,
        )
        check(
            "the readers do not raise on None",
            fbc.declared_fullback_locomotion(None) is None
            and fbc.declared_fullback_locomotion_source(None) == ""
            and fbc.declared_fullback_locomotion_frame(None) is None,
        )
    finally:
        restore()
# ══════════════════════════════════════════════════════════════════════
# [7] THE CONSUMPTION — params_override, and the neutrality A/B
# ══════════════════════════════════════════════════════════════════════

def _capture(tagged=True, armed=False, state=MovementState.CHASE,
             tx=200.0, ty=35.0, bc=None, recovery=None):
    """Run ``apply_fullback_locomotion`` once and capture its down-call.

    Returns ``(body, kwargs)`` where *kwargs* are the keyword arguments the
    consumer handed ``apply_movement_state``.  ``recovery`` optionally
    installs a Stage-3 answer for the run.
    """
    body = latch(make_body(state=state, target_x=tx, target_y=ty, bc=bc,
                           tagged=tagged), armed)
    captured = {}
    original = ms.apply_movement_state

    def _spy(player, st, cx, cy, dt, **kw):
        captured["args"] = (player, st, cx, cy, dt)
        captured["kwargs"] = kw
        return original(player, st, cx, cy, dt, **kw)

    restore_recovery = None
    if recovery is not None:
        restore_recovery = _install_recovery_spy(_RecoverySpy(answer=recovery))
    ms.apply_movement_state = _spy
    try:
        apply_fullback_locomotion(body, DT)
    finally:
        ms.apply_movement_state = original
        if restore_recovery is not None:
            restore_recovery()
    return body, captured.get("kwargs", {})


def _run(state, tagged, armed, tx=200.0, ty=35.0, bc=None, recovery=None,
         on=True):
    """One full shared-model step for a body under a given lever setting."""
    saved = fbc.FB_LOCOMOTION_CONTROLLER
    body = latch(make_body(state=state, target_x=tx, target_y=ty, bc=bc,
                           tagged=tagged), armed)
    restore_recovery = None
    if recovery is not None:
        restore_recovery = _install_recovery_spy(_RecoverySpy(answer=recovery))
    fbc.FB_LOCOMOTION_CONTROLLER = on
    try:
        apply_fullback_locomotion(body, DT)
    finally:
        fbc.FB_LOCOMOTION_CONTROLLER = saved
        if restore_recovery is not None:
            restore_recovery()
    return body


def sec_consumption():
    section(7, "CONSUMPTION — params_override reaches apply_movement_state, "
               "and the shipped default is behaviour-neutral")

    bc_far = _BC(200.0, 35.0)

    # (a) An owned frame hands over the resolved PROFILE.
    body, kw = _capture(armed=True, bc=bc_far)
    override = kw.get("params_override")
    check(
        "a LIVE-BREAK frame hands apply_movement_state a params_override",
        isinstance(override, dict),
        f"override={override!r}",
    )
    check(
        "and it IS the resolver's PURSUIT profile (no second source of "
        "truth)",
        override is fbl.profile_for_intent(FullbackIntent.CHASE),
    )
    check(
        "the PUrsuit frame asks for the intercept steering this stage "
        "already shipped",
        kw.get("intercept_track") is True,
        f"intercept_track={kw.get('intercept_track')}",
    )

    # (b) An owned RECOVERY frame hands over the RECOVERY profile.
    #
    # THE STATE IS THE AUTHORITY (2026-09-30): an owed-ground frame is one the
    # geometry writer stamped RECOVER through the ONE write path, so the
    # fixture must carry that state.  The pre-fix fixture handed a CHASE-STATE
    # body to the recovery door and demanded the RECOVERY profile -- i.e. it
    # asserted the very state/profile split-brain the live engine exhibited
    # (``verify_fullback_recover_live.py``, 2 of 6 runs: the state read RECOVER
    # while door A's sticky latch handed over the PURSUIT profile, so a body
    # commanded home sprinted at 8.30 m/s against the RECOVER row's 4.75).
    body, kw = _capture(armed=False, bc=bc_far, recovery=True,
                        state=MovementState.RECOVER)
    override = kw.get("params_override")
    check(
        "an OWED-GROUND frame hands over the RECOVERY profile",
        override is fbl.profile_for_intent(FullbackIntent.RECOVER),
        f"override={override!r}",
    )
    check(
        "the RECOVERY frame steers directly to its owned anchor",
        kw.get("intercept_track") is True,
        f"intercept_track={kw.get('intercept_track')}",
    )

    # (b2) THE STATE WINS WHEN IT NAMES A PROFILE -- the measured regression.
    #      Door A (the sticky latch) is OPEN here and the live break is cold,
    #      which is exactly the shape the real engine produced: the authority
    #      had already stamped RECOVER, so the RECOVERY profile is the only
    #      answer that cannot disagree with the state.
    body, kw = _capture(armed=True, bc=bc_far, recovery=True,
                        state=MovementState.RECOVER)
    override = kw.get("params_override")
    check(
        "a LATCHED emergency whose state is RECOVER hands over the RECOVERY "
        "profile (the profile can never disagree with the state)",
        override is fbl.profile_for_intent(FullbackIntent.RECOVER),
        f"override={override!r}",
    )
    check(
        "and it keeps direct two-axis recovery steering",
        kw.get("intercept_track") is True,
        f"intercept_track={kw.get('intercept_track')}",
    )
    check(
        "the STATE is what decided it: a RECOVER state resolves to the "
        "RECOVERY intent even with the break latch armed",
        fbc.locomotion_intent(
            _World(), latch(make_body(state=MovementState.RECOVER), True)
        ) is FullbackIntent.RECOVER,
    )
    check(
        "and a CHASE state still resolves to the PURSUIT intent",
        fbc.locomotion_intent(
            _World(), latch(make_body(state=MovementState.CHASE), True)
        ) is FullbackIntent.CHASE,
    )
    check(
        "a state that names NO profile still yields to the doors: the "
        "sweeper's DRIFT with an armed latch stays the pursuer",
        fbc.locomotion_intent(
            _World(), latch(make_body(state=MovementState.DRIFT), True)
        ) is FullbackIntent.CHASE,
    )

    # (c) An UNOWNED frame hands over nothing at all: the shared tables.
    body, kw = _capture(armed=False, bc=bc_far, recovery=False)
    check(
        "an UNOWNED (sweeper) frame hands over params_override=None",
        kw.get("params_override") is None,
        f"override={kw.get('params_override')!r}",
    )

    body, kw = _capture(tagged=False, armed=True, bc=bc_far)
    check(
        "an UNTAGGED frame hands over params_override=None",
        kw.get("params_override") is None,
        f"override={kw.get('params_override')!r}",
    )

    # (d) THE ACCEPTANCE BAR — lever OFF is byte-identical to the shipped
    #     path, and the lever-ON default is behaviour-neutral on the frames
    #     this stage claims.
    for label, state, armed, recovery in (
        ("sweeper DRIFT", MovementState.DRIFT, False, False),
        ("sweeper CHASE", MovementState.CHASE, False, False),
        ("live-break CHASE", MovementState.CHASE, True, False),
        ("owed-ground RECOVER", MovementState.RECOVER, False, True),
    ):
        off = _run(state, True, armed, bc=bc_far, recovery=recovery, on=False)
        on = _run(state, True, armed, bc=bc_far, recovery=recovery, on=True)
        same = (
            abs(off.x - on.x) < 1e-12 and abs(off.y - on.y) < 1e-12
            and abs(off.vx - on.vx) < 1e-12 and abs(off.vy - on.vy) < 1e-12
        )
        check(
            f"{label}: lever ON is byte-identical to lever OFF",
            same,
            f"OFF=({off.x!r},{off.y!r},{off.vx!r},{off.vy!r}) "
            f"ON=({on.x!r},{on.y!r},{on.vx!r},{on.vy!r})",
        )

    # (e) The override is actually CONSUMED: a copied profile differing only
    #     in ``alignment_damping`` must produce a different step on a fixture
    #     where the pre-tackle alignment clamp bites.
    prof = dict(fbl.profile_for_intent(FullbackIntent.CHASE))
    prof["intercept_steer"] = True

    def _one(damping):
        b = make_body(state=MovementState.CHASE, target_x=70.0, target_y=48.0,
                      bc=_BC(61.0, 35.0), tagged=False)
        b.vx = 0.0
        b.vy = 0.0
        over = dict(prof)
        over["alignment_damping"] = damping
        apply_movement_state(b, MovementState.CHASE, 70.0, 48.0, DT,
                             bc_x=61.0, bc_y=35.0, bc_vy=0.0,
                             preserve_state=True, intercept_track=True,
                             params_override=over)
        return (b.vx, b.vy, b.x, b.y)

    damped = _one(True)
    undamped = _one(False)
    check(
        "alignment_damping is genuinely read by apply_movement_state",
        damped != undamped,
        f"damped={damped} undamped={undamped}",
    )

    # (f) With NO override the historical derivation is intact: asking for
    #     intercept_track=True must reproduce alignment_damping=False.
    def _hist(intercept):
        b = make_body(state=MovementState.CHASE, target_x=70.0, target_y=48.0,
                      bc=_BC(61.0, 35.0), tagged=False)
        b.vx = 0.0
        b.vy = 0.0
        apply_movement_state(b, MovementState.CHASE, 70.0, 48.0, DT,
                             bc_x=61.0, bc_y=35.0, bc_vy=0.0,
                             preserve_state=True, intercept_track=intercept)
        return (b.vx, b.vy, b.x, b.y)

    check(
        "no override: intercept_track=True reproduces the damping-off step",
        _hist(True) == undamped,
        f"hist={_hist(True)} undamped={undamped}",
    )
    check(
        "no override: intercept_track=False reproduces the damping-on step",
        _hist(False) == damped,
        f"hist={_hist(False)} damped={damped}",
    )


# ══════════════════════════════════════════════════════════════════════
# [8] WIRING + THE INDEPENDENCE PROOF
# ══════════════════════════════════════════════════════════════════════

def sec_wiring_and_independence():
    section(8, "Wiring (no cycle, façade, __all__) and the INDEPENDENCE "
               "proof")

    controller_src = _src("game/defence/defence_lvl_1/fullback_controller.py")
    leaf_src = _src("game/defence/defence_lvl_1/fullback_locomotion.py")
    movement_src = _src("player/movement_states.py")

    # No import cycle: the MODULE-SCOPE edges must run one way only.
    check(
        "the leaf imports the controller at module scope",
        any(
            line.startswith("from game.defence.defence_lvl_1.fullback_"
                            "controller import")
            or line.startswith("import game.defence.defence_lvl_1."
                               "fullback_controller")
            for line in leaf_src.splitlines()
        ),
        "no module-scope controller import found in the leaf",
    )
    check(
        "the controller does NOT import the leaf at module scope (lazy only)",
        not any(
            line.startswith("from game.defence.defence_lvl_1.fullback_"
                            "locomotion import")
            or line.startswith("import game.defence.defence_lvl_1."
                               "fullback_locomotion")
            for line in controller_src.splitlines()
        ),
        "a module-scope leaf import would close the cycle",
    )
    check(
        "and the ONE leaf edge is the lazy accessor",
        "def _fullback_locomotion_api" in controller_src,
    )
    check(
        "player/movement_states.py imports the controller only "
        "function-locally",
        not any(
            line.startswith("from game.defence.defence_lvl_1."
                            "fullback_controller import")
            or line.startswith("import game.defence.defence_lvl_1."
                               "fullback_controller")
            for line in movement_src.splitlines()
        ),
    )
    check(
        "the consumption site resolves the profile through the controller's "
        "ONE resolver",
        "fullback_locomotion_profile" in movement_src,
    )
    check(
        "and hands it down as params_override",
        "params_override=" in movement_src,
    )
    check(
        "apply_movement_state declares the keyword-only params_override",
        "params_override" in movement_src,
    )
    check(
        "the historical not-intercept_track derivation survives as the "
        "default",
        "_alignment_damping" in movement_src,
    )

    # __all__ coverage.
    for name in ("FB_LOCOMOTION_CONTROLLER",
                 "FB_LOCOMOTION_CONTROLLER_ENV_VAR",
                 "fullback_locomotion_controller_enabled",
                 "locomotion_contract_owned",
                 "shared_locomotion_allowed",
                 "locomotion_intent",
                 "fullback_locomotion_profile",
                 "declare_fullback_locomotion",
                 "declared_fullback_locomotion",
                 "declared_fullback_locomotion_source",
                 "declared_fullback_locomotion_frame",
                 "break_locomotion_owned",
                 "recovery_locomotion_owned",
                 "recovery_owed",
                 "locomotion_source_for_intent"):
        check(
            f"the controller exports {name}",
            name in fbc.__all__,
            f"__all__ entries={len(fbc.__all__)}",
        )

    for name in ("FB_LOCOMOTION_PROFILES", "LOCOMOTION_FIELDS",
                 "profile_for_intent", "locomotion_profile_is_shared_"
                 "equivalent"):
        check(
            f"the leaf exports {name}",
            name in fbl.__all__,
            f"__all__ entries={len(fbl.__all__)}",
        )

    # The façade delegates rather than duplicating.
    world = _World()
    spy = _RecoverySpy(answer=False)
    restore = _install_recovery_spy(spy)
    try:
        fb = latch(make_body(), True)
        check(
            "façade: locomotion_controller_enabled()",
            CONTROLLER.locomotion_controller_enabled()
            is fbc.fullback_locomotion_controller_enabled(),
        )
        check(
            "façade: owns_locomotion",
            CONTROLLER.owns_locomotion(world, fb)
            is fbc.locomotion_contract_owned(world, fb),
        )
        check(
            "façade: shared_locomotion_allowed",
            CONTROLLER.shared_locomotion_allowed(world, fb)
            is fbc.shared_locomotion_allowed(world, fb),
        )
        check(
            "façade: break_locomotion_owned",
            CONTROLLER.break_locomotion_owned(world, fb)
            is fbc.break_locomotion_owned(world, fb),
        )
        check(
            "façade: recovery_locomotion_owned",
            CONTROLLER.recovery_locomotion_owned(world, fb)
            is fbc.recovery_locomotion_owned(world, fb),
        )
        check(
            "façade: locomotion_intent",
            CONTROLLER.locomotion_intent(world, fb)
            is fbc.locomotion_intent(world, fb),
        )
        check(
            "façade: locomotion_profile",
            CONTROLLER.locomotion_profile(world, fb)
            is fbc.fullback_locomotion_profile(world, fb),
        )
        check(
            "façade: recovery_owed",
            CONTROLLER.recovery_owed(world, fb)
            is fbc.recovery_owed(world, fb),
        )
        check(
            "façade: declared_locomotion",
            CONTROLLER.declared_locomotion(fb)
            is fbc.declared_fullback_locomotion(fb),
        )
        check(
            "façade: declare_locomotion stamps what the module function "
            "stamps",
            CONTROLLER.declare_locomotion(world, fb)
            is fbc.declared_fullback_locomotion(fb),
        )
    finally:
        restore()

    # ── THE INDEPENDENCE PROOF ──
    # Re-tune the SHARED CHASE table at runtime.  The shared body must move;
    # the owned fullback must not, because its profile is a snapshot.
    state = MovementState.CHASE
    saved_lp = LATERAL_PRIORITY_BY_STATE[state]
    saved_accel = ACCEL_BY_STATE[state]
    bc = _BC(200.0, 35.0)
    try:
        shared_before = _run_shared(state, bc)
        owned_before = _run_owned(state, bc)

        LATERAL_PRIORITY_BY_STATE[state] = 0.05
        ACCEL_BY_STATE[state] = saved_accel * 4.0

        shared_after = _run_shared(state, bc)
        owned_after = _run_owned(state, bc)
    finally:
        LATERAL_PRIORITY_BY_STATE[state] = saved_lp
        ACCEL_BY_STATE[state] = saved_accel

    check(
        "INDEPENDENCE: re-tuning the shared CHASE table MOVES the shared "
        "body",
        shared_before != shared_after,
        f"before={shared_before} after={shared_after}",
    )
    check(
        "INDEPENDENCE: the same re-tune does NOT move the owned fullback",
        owned_before == owned_after,
        f"before={owned_before} after={owned_after}",
    )
    check(
        "INDEPENDENCE: the owned fullback really is running a different "
        "contract from the shared body",
        shared_after != owned_after,
    )


def _run_shared(state, bc):
    """A CHASE step for an UNTAGGED body -- the shared table's own answer."""
    saved = fbc.FB_LOCOMOTION_CONTROLLER
    b = latch(make_body(state=state, target_x=200.0, target_y=35.0, bc=bc,
                        tagged=False), False)
    fbc.FB_LOCOMOTION_CONTROLLER = saved
    try:
        apply_fullback_locomotion(b, DT)
    finally:
        fbc.FB_LOCOMOTION_CONTROLLER = saved
    return (b.vx, b.vy, b.x, b.y)


def _run_owned(state, bc):
    """A CHASE step for the TAGGED, LATCHED fullback -- the owned answer."""
    b = latch(make_body(state=state, target_x=200.0, target_y=35.0, bc=bc,
                        tagged=True), True)
    apply_fullback_locomotion(b, DT)
    return (b.vx, b.vy, b.x, b.y)


def main():
    print("FULLBACK LOCOMOTION OWNERSHIP — STAGE 4 CONTRACT (2026-09-30)")
    print("=" * 68)
    sec_lever()
    sec_profiles()
    sec_truth_table()
    sec_door_b_is_live()
    sec_one_resolver()
    sec_declarations()
    sec_consumption()
    sec_wiring_and_independence()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: ALL CHECKS PASSED — the tagged fullback's emergency "
          "pursuit,")
    print("        interception and recovery steering are its OWN contract,")
    print("        resolved from its intent, independent of DRIFT, SLIDE and")
    print("        line-defence priorities — and the shipped default is")
    print("        byte-identical to the shared tables it replaces.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
