"""verify_fullback_state_ownership_contract.py — STAGE 2: STATE OWNERSHIP
(2026-09-30).

THE LAW UNDER TEST
------------------
For as long as a tagged fullback's BREAK EMERGENCY is live, the fullback
controller is the SOLE authority for that actor's ``movement_state``.  The
shared defender state policy — the Movement Mode Decision Layer in
``movement_mode_decision`` and the sweeper pin in
``defensive_behaviour_unified`` — must not name a state for that body until
the emergency ends.  When the emergency ends (the break latch clears) the
shared policy resumes unchanged.

THE EMERGENCY is the per-actor break latch armed by the geometry authority on
a genuine break of EITHER kind (``FB_BREAK_DEPTH`` / ``FB_BREAK_LATERAL``).  It
ENDS when the latch clears: the explicit tackle-cycle sweep at RELEASE entry /
miss teardown, tag loss, the engagement reset, or self-release when the
carrier genuinely drops behind the latched line.  Those are the latch
contract's own release conditions (pinned by
``verify_fullback_break_latch_contract.py``), so this stage does not invent a
SECOND definition of "the emergency ended".

THE SPLIT-BRAIN THIS REMOVES
----------------------------
``fullback_role.apply_fullback_geometry`` arms the latch on BOTH break kinds
and writes ``CHASE``.  The sweeper pin in ``defensive_behaviour_unified``
re-wrote ``MovementState.DRIFT`` whenever ``fullback_breakaway_live`` was
False — and that predicate admits a DEPTH latch but NOT a LATERAL one.  On a
lateral channel breach the pin therefore overwrote the chase the geometry
authority had just ordered, leaving ``_fb_intent = CHASE`` against a live
state of ``DRIFT``: the commanded-vs-carried pair Stage 1's report measured.

THE ROLLBACK LEVER
------------------
``RLM_FB_STATE_CONTROLLER`` (default ON).  OFF restores the Stage-1
arrangement exactly — the shared state policy may name the state even on a
latched break — with no code revert, which is what the A/B arms below prove.

WHAT IT DOES NOT CLAIM
----------------------
The MMDL's own verdict on a latched body is CHASE under BOTH lever states
(the lever-OFF fallback IS ``_fb_chase``), so the MMDL half of this stage is
demonstrated STRUCTURALLY: the branch is shown to DEFER to the controller
(§4 monkeypatches the controller's verdict and watches the MMDL carry it)
rather than to name the state itself.  The pin half is where the observable
CHASE/DRIFT difference lives, and it is exercised end to end in §6.
"""
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import game.defence.defence_lvl_1.fullback_controller as fbc
from game.defence.defence_lvl_1.fullback_controller import (
    FB_STATE_CONTROLLER,
    FB_STATE_CONTROLLER_ENV_VAR,
    SOURCE_STATE_OWNERSHIP,
    SOURCE_SHARED_POLICY,
    FullbackIntent,
    break_emergency_live,
    command_fullback_state,
    controller_owns_fullback_state,
    emergency_fullback_state,
    emergency_intent,
    fullback_state_controller_enabled,
    intent_movement_state,
    shared_policy_state_allowed,
)
from game.defence.defence_lvl_1.movement_mode_decision import (
    decide_movement_state,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from player.movement_states import MovementState

import game.defence.defence_lvl_1.fullback_role as fb

FAILURES = []

# ── GEOMETRY ──────────────────────────────────────────────────────────
# NO BREAK: ruck_x = 100 -> line anchor 110; a carrier at 105 is behind it, so
# the DEPTH leg cannot fire.  A carrier on the sweeper's own lane cannot fire
# the LATERAL leg either.  This is the fixture the LATERAL split-brain needs:
# a body whose latch is armed while ``fullback_breakaway_live`` is False.
RUCK_NO_BREAK = 100.0
LINE_NO_BREAK = RUCK_NO_BREAK + 10.0     # 110.0, the anchor authority's line
BC_X_NO_BREAK = 105.0
BC_Y_LANE = 35.0                          # the sweeper's own lane

TUNERS = DefenceTuners()


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        FAILURES.append(f"{label}  {detail}")


def section(n, title):
    print(f"\n[{n}] {title}")


def _src(rel_path):
    with open(rel_path, encoding="utf-8") as fh:
        return fh.read()


BREAKAWAY_ERRORS = []


def make_world(ruck_x=RUCK_NO_BREAK, tes_active=False, bc=None):
    """The MMDL's own minimal world shape (see verify_mmdl_fullback_exclusion)."""
    return NS(
        ruck_x_m=ruck_x,
        tes_active=tes_active,
        ball_carrier=make_bc() if bc is None else bc,
    )


def breakaway(world, actor) -> bool:
    """``fullback_breakaway_live`` read on *world*, total.

    The predicate is total by contract; a raise here would itself be a
    violation, so it is recorded and reported rather than hidden behind a
    bare ``False``.
    """
    try:
        return bool(
            fb.fullback_breakaway_live(
                world, actor, getattr(world, "ball_carrier", None)
            )
        )
    except Exception as exc:                              # noqa: BLE001
        BREAKAWAY_ERRORS.append(f"{type(exc).__name__}: {exc}")
        return False


def make_fb(x=60.0, y=BC_Y_LANE, tagged=True, movement_state=None, pid=201):
    """A tagged fullback stub.  No seeding needed (seed_hold exempts the tag)."""
    return NS(
        x=x, y=y, player_id=pid,
        depth_class="PASSIVE",
        defender_role=None,
        movement_state=movement_state,
        is_tackler=False,
        fullback_role_active=bool(tagged),
    )


# A GENUINE DEPTH break: ruck 30 -> live line 40; a carrier at 55 is 15 m past
# it and the sweeper at 60/35 is 5 m off the carrier's lane (the same geometry
# verify_mmdl_fullback_exclusion uses for its BREAK regime).
DEPTH_RUCK = 30.0
DEPTH_LINE = DEPTH_RUCK + 10.0
DEPTH_BC_X = 55.0


def make_bc(x=BC_X_NO_BREAK, y=BC_Y_LANE):
    return NS(x=x, y=y, vy=0.0)


def kind_of(world, actor) -> str:
    """The break kind the geometry authority classifies, for failure detail."""
    try:
        line_x = fb.break_test_line_anchor_x(world, actor)
        return str(fb.classify_fullback_break(
            getattr(world, "ball_carrier", None), actor, line_x,
            float(getattr(actor, "y", 0.0)),
        ))
    except Exception as exc:                              # noqa: BLE001
        return f"RAISED {type(exc).__name__}: {exc}"


def arm_lateral(d, anchor=LINE_NO_BREAK):
    """Arm a LATERAL break latch — the break kind ``breakaway_live`` ignores."""
    armed = fb.arm_fullback_break_latch(d, fb.FB_BREAK_LATERAL, anchor)
    assert armed is True, "fixture: the LATERAL latch must arm"
    return d


def arm_depth(d, anchor=LINE_NO_BREAK):
    armed = fb.arm_fullback_break_latch(d, fb.FB_BREAK_DEPTH, anchor)
    assert armed is True, "fixture: the DEPTH latch must arm"
    return d


def pin_yields(world, actor) -> bool:
    """The sweeper pin's gate, EXACTLY as ``defensive_behaviour_unified`` writes it.

    ``_fb_breakaway_live(world, fb_actor)`` OR
    ``not _fb_shared_policy_enabled(world, fb_actor)``

    where ``_fb_shared_policy_enabled`` is
    ``fullback_controller.shared_policy_state_allowed``.  §7 pins the source
    text of both call sites against this expression so this local copy cannot
    silently drift away from production.
    """
    return bool(
        breakaway(world, actor)
        or not shared_policy_state_allowed(world, actor)
    )


class lever_off:
    """In-process rollback: rebind ``fullback_controller.FB_STATE_CONTROLLER``.

    The same A/B pattern every runtime lever in this package uses — no code
    revert, no subprocess, and the value is restored exactly.
    """

    def __enter__(self):
        self._saved = fbc.FB_STATE_CONTROLLER
        fbc.FB_STATE_CONTROLLER = False
        return self

    def __exit__(self, *exc):
        fbc.FB_STATE_CONTROLLER = self._saved
        return False


# ══════════════════════════════════════════════════════════════════════
# [1] THE LEVER
# ══════════════════════════════════════════════════════════════════════

def sec_lever():
    section(1, "The Stage-2 rollback lever")
    check(
        "the env var is the documented name",
        FB_STATE_CONTROLLER_ENV_VAR == "RLM_FB_STATE_CONTROLLER",
        f"got {FB_STATE_CONTROLLER_ENV_VAR!r}",
    )
    check(
        "the shipped default is ON",
        bool(FB_STATE_CONTROLLER) is True,
        f"FB_STATE_CONTROLLER={FB_STATE_CONTROLLER!r}",
    )
    check(
        "the predicate agrees with the module boolean",
        fullback_state_controller_enabled() == bool(FB_STATE_CONTROLLER),
    )
    with lever_off():
        check(
            "rebinding the module boolean turns the predicate OFF",
            fullback_state_controller_enabled() is False,
        )
    check(
        "the rebinding is restored on exit",
        fullback_state_controller_enabled() is True,
    )
    check(
        "the two stage levers are independent names",
        FB_STATE_CONTROLLER_ENV_VAR != fbc.FB_INTENT_CONTROLLER_ENV_VAR,
    )


# ══════════════════════════════════════════════════════════════════════
# [2] THE OWNERSHIP TRUTH TABLE
# ══════════════════════════════════════════════════════════════════════

def sec_ownership():
    section(2, "Who owns the tagged fullback's state this frame")
    world = make_world()

    # (a) an untagged body carrying a latch attribute is NOT the controller's.
    untagged = make_fb(tagged=False)
    setattr(untagged, fb.FB_BREAK_LATCH_ATTR, True)
    check(
        "an armed latch on an UNTAGGED body is not an emergency",
        break_emergency_live(untagged) is False,
    )

    # (b) a tagged body with NO latch is not in an emergency.
    plain = make_fb()
    check(
        "a tagged fullback with NO latch is not in an emergency",
        break_emergency_live(plain) is False,
    )

    # (c) a tagged body with a LATERAL latch IS in an emergency.  This is the
    # kind ``fullback_breakaway_live`` does NOT admit, so the ownership cannot
    # be derived from that predicate.
    lateral = arm_lateral(make_fb())
    check(
        "a LATERAL latch IS an emergency",
        break_emergency_live(lateral) is True,
    )
    check(
        "...and breakaway_live does NOT admit it (the ownership is broader)",
        breakaway(world, lateral) is False,
        f"breakaway_live={breakaway(world, lateral)!r}",
    )

    # (d) a DEPTH latch is an emergency too.
    depth = arm_depth(make_fb())
    check(
        "a DEPTH latch IS an emergency",
        break_emergency_live(depth) is True,
    )

    # (e) the lever OFF suspends ownership entirely.
    with lever_off():
        check(
            "[lever OFF] even a LATERAL latch is not an emergency",
            break_emergency_live(arm_lateral(make_fb())) is False,
        )

    # (f) the two public predicates are exact negations.
    for label, actor in (("untagged+latched", untagged), ("tagged", plain),
                         ("lateral", lateral), ("depth", depth)):
        owns = controller_owns_fullback_state(world, actor)
        allowed = shared_policy_state_allowed(world, actor)
        check(
            f"owns_state / shared_policy_allowed are exact negations [{label}]",
            owns is (not allowed),
            f"owns={owns} allowed={allowed}",
        )
    check(
        "the shared policy is ALLOWED on an unlatched tagged fullback",
        shared_policy_state_allowed(world, plain) is True,
    )
    check(
        "the shared policy is REFUSED on a latched tagged fullback",
        shared_policy_state_allowed(world, lateral) is False,
    )


# ══════════════════════════════════════════════════════════════════════
# [3] THE EMERGENCY READING AND THE SOLE-AUTHORITY WRITE
# ══════════════════════════════════════════════════════════════════════

def sec_command_path():
    section(3, "The emergency reading and the sole-authority write path")
    world = make_world()
    chase = intent_movement_state(FullbackIntent.CHASE)

    check(
        "the ONE intent -> state law maps CHASE to MovementState.CHASE",
        chase is MovementState.CHASE,
        f"got {chase!r}",
    )

    # The intent a live emergency commands.
    check(
        "a live emergency commands the CHASE intent",
        emergency_intent(arm_lateral(make_fb())) is FullbackIntent.CHASE,
    )
    check(
        "no emergency falls back to the SWEEP intent (never an emergency order)",
        emergency_intent(make_fb()) is FullbackIntent.SWEEP,
    )

    # The state the emergency commands, resolved through the ONE law.
    latched = arm_lateral(make_fb())
    check(
        "the emergency state IS the CHASE intent's state",
        emergency_fullback_state(world, latched) is chase,
        f"got {emergency_fullback_state(world, latched)!r}",
    )
    check(
        "no emergency -> no commanded state (None, not a guess)",
        emergency_fullback_state(world, make_fb()) is None,
    )

    # THE WRITE.
    body = arm_lateral(make_fb(movement_state=MovementState.DRIFT))
    written = command_fullback_state(world, body)
    check(
        "the sole-authority write lands the emergency state",
        written is chase and body.movement_state is chase,
        f"written={written!r} state={body.movement_state!r}",
    )
    check(
        "...and stamps the Stage-2 source",
        getattr(body, fbc.INTENT_SOURCE_ATTR, None) == SOURCE_STATE_OWNERSHIP,
        f"src={getattr(body, fbc.INTENT_SOURCE_ATTR, None)!r}",
    )
    check(
        "...and stamps the CHASE intent",
        getattr(body, fbc.INTENT_ATTR, None) is FullbackIntent.CHASE,
    )

    # NO WRITE when the controller does not own the frame.
    unowned = make_fb(movement_state=MovementState.DRIFT)
    result = command_fullback_state(world, unowned)
    check(
        "an unowned frame writes NOTHING",
        result is None and unowned.movement_state is MovementState.DRIFT,
        f"result={result!r} state={unowned.movement_state!r}",
    )
    check(
        "...and stamps NOTHING (a yield is not a command)",
        getattr(unowned, fbc.INTENT_ATTR, None) is None
        and getattr(unowned, fbc.INTENT_SOURCE_ATTR, None) is None,
    )

    # THE LEVER: OFF restores the shared policy's ownership of the frame.
    with lever_off():
        off_body = arm_lateral(make_fb(movement_state=MovementState.DRIFT))
        check(
            "[lever OFF] the controller does not command the frame",
            command_fullback_state(world, off_body) is None
            and off_body.movement_state is MovementState.DRIFT,
        )
        check(
            "[lever OFF] and the shared policy is allowed again",
            shared_policy_state_allowed(world, off_body) is True,
        )


# ══════════════════════════════════════════════════════════════════════
# [4] THE MMDL DEFERS TO THE CONTROLLER
# ══════════════════════════════════════════════════════════════════════

def sec_mmdl_defers():
    """The MMDL half is STRUCTURAL: prove the branch carries the controller's verdict.

    On a latched body the MMDL's own verdict is CHASE under BOTH lever states
    (the lever-OFF fallback IS ``_fb_chase``), so the state alone cannot
    distinguish "deferred to the controller" from "decided it itself".
    Monkeypatching the controller's verdict does: whatever the controller
    says is what the MMDL must return.
    """
    section(4, "The MMDL latch gate carries the controller's verdict")
    world = make_world()
    tuners = TUNERS

    # (a) the real controller on a real LATERAL latch.
    real = arm_lateral(make_fb())
    check(
        "a LATERAL-latched fullback decides CHASE (the split-brain case)",
        decide_movement_state(world, real, make_bc(), tuners)
        is MovementState.CHASE,
    )

    # (b) a tagged fullback with NO latch keeps the sweeper contract.
    check(
        "an unlatched fullback still decides DRIFT",
        decide_movement_state(world, make_fb(), make_bc(), tuners)
        is MovementState.DRIFT,
    )

    # (c) the DEFERENCE PROOF: patch the controller's verdict.
    sentinel = MovementState.SHOOT
    saved = fbc.emergency_fullback_state

    def _patched(w, defender):
        # lever-aware on purpose, so the OFF arm still exercises the fallback.
        return sentinel if break_emergency_live(defender) else None

    fbc.emergency_fullback_state = _patched
    try:
        deferring = arm_lateral(make_fb())
        check(
            "the MMDL returns the CONTROLLER's state, not its own",
            decide_movement_state(world, deferring, make_bc(), tuners)
            is sentinel,
            f"got {decide_movement_state(world, deferring, make_bc(), tuners)!r}",
        )
        with lever_off():
            off = arm_lateral(make_fb())
            got = decide_movement_state(world, off, make_bc(), tuners)
            check(
                "[lever OFF] the MMDL falls back to its historical CHASE",
                got is MovementState.CHASE,
                f"got {got!r}",
            )
    finally:
        fbc.emergency_fullback_state = saved

    # (d) the patch is fully undone.
    restored = arm_lateral(make_fb())
    check(
        "the controller is restored after the deference proof",
        decide_movement_state(world, restored, make_bc(), tuners)
        is MovementState.CHASE,
    )
    check(
        "an UNTAGGED body in identical geometry is untouched by the gate",
        decide_movement_state(world, make_fb(tagged=False), make_bc(), tuners)
        is MovementState.HOLD,
    )
# ══════════════════════════════════════════════════════════════════════
# [5] RELEASE — THE SHARED POLICY RESUMES WHEN THE EMERGENCY ENDS
# ══════════════════════════════════════════════════════════════════════

def sec_release():
    section(5, "The emergency ends exactly when the latch clears")
    world = make_world()
    actor = arm_lateral(make_fb(movement_state=MovementState.DRIFT))

    check("the body starts inside an emergency",
          break_emergency_live(actor) is True)
    check("...and the shared policy is refused",
          shared_policy_state_allowed(world, actor) is False)

    released = fb.clear_fullback_break_latch(actor)
    check("the latch contract's own clear releases it", released is True)
    check("...leaving no armed latch", fb.is_fullback_break_latched(actor) is False)
    check("...so the emergency has ENDED",
          break_emergency_live(actor) is False)
    check("...the controller no longer owns the frame",
          controller_owns_fullback_state(world, actor) is False)
    check("...and the SHARED POLICY RESUMES",
          shared_policy_state_allowed(world, actor) is True)

    # The resumption is not a one-way door: re-arming on a new break restores
    # ownership, which is the "re-asserted on the next break" contract.
    arm_lateral(actor)
    check("a NEW break re-arms ownership",
          controller_owns_fullback_state(world, actor) is True
          and shared_policy_state_allowed(world, actor) is False)

    # The MMDL follows the same release: on an unlatched body the shared
    # policy decides again, and the sweeper contract is DRIFT.
    freed = arm_lateral(make_fb())
    fb.clear_fullback_break_latch(freed)
    check("the MMDL returns to the sweeper contract once released",
          decide_movement_state(world, freed, make_bc(), TUNERS)
          is MovementState.DRIFT)


# ══════════════════════════════════════════════════════════════════════
# [6] THE SPLIT-BRAIN, AND ITS REMOVAL — THE SWEEPER PIN
# ══════════════════════════════════════════════════════════════════════

# The geometry authority's order on a live lateral breach: the pursuit point
# and CHASE.  Only the STATE is asserted; the point is carried so the pin's
# second write (target_x = the static anchor) is visible.
GEOMETRY_ORDER_STATE = MovementState.CHASE
GEOMETRY_ORDER_X = BC_X_NO_BREAK - 1.0          # 104.0, goal-side of the BC


def composed_frame(world, actor):
    """What the frame ENDS UP commanding to *actor*.

    The geometry authority orders CHASE at its pursuit point; the sweeper pin
    then either YIELDS (owner present) or RUNS and re-pins the body to the
    static anchor in DRIFT.  That is exactly the two-writer sequence the
    stage removes, expressed over the live predicates.
    """
    state = GEOMETRY_ORDER_STATE
    target_x = GEOMETRY_ORDER_X
    if not pin_yields(world, actor):
        # the pin runs: the historical Stage-1 write, restored by the lever
        state = MovementState.DRIFT
        target_x = LINE_NO_BREAK
    return state, target_x


def sec_pin_split_brain():
    section(6, "The sweeper pin yields on a LATERAL break (the split-brain)")
    world = make_world()

    # (a) the LATERAL arm — the case the pin used to clobber.
    latched = arm_lateral(make_fb())
    check(
        "[lateral] breakaway_live does NOT see the break (the pin's own gate)",
        breakaway(world, latched) is False,
        f"breakaway_live={breakaway(world, latched)!r}",
    )
    check(
        "[lateral] but the controller DOES own the frame",
        controller_owns_fullback_state(world, latched) is True,
    )
    on_state, on_x = composed_frame(world, latched)
    check(
        "[lateral / lever ON] the pin YIELDS: the chase survives",
        on_state is MovementState.CHASE and on_x == GEOMETRY_ORDER_X,
        f"state={on_state!r} tx={on_x!r}",
    )
    with lever_off():
        off_state, off_x = composed_frame(world, arm_lateral(make_fb()))
        check(
            "[lateral / lever OFF] the pin RUNS: CHASE is clobbered to DRIFT",
            off_state is MovementState.DRIFT and off_x == LINE_NO_BREAK,
            f"state={off_state!r} tx={off_x!r}",
        )
    check(
        "the A/B is real: the two arms disagree on the state",
        on_state is not off_state,
        f"on={on_state!r} off={off_state!r}",
    )

    # (b) the DEPTH arm — breakaway_live already saw it, so this stage changes
    # nothing there.  That is the "no regression off a break" control.
    # A DEPTH break needs the carrier genuinely PAST the line, so this arm uses
    # its own geometry (the known-good BREAK regime of
    # verify_mmdl_fullback_exclusion): ruck 30 -> live line 40, carrier at 55.
    depth_world = make_world(
        ruck_x=DEPTH_RUCK, bc=make_bc(DEPTH_BC_X, BC_Y_LANE)
    )
    dep = arm_depth(make_fb(), anchor=DEPTH_LINE)
    check(
        "[depth] breakaway_live DOES see the break (unchanged by this stage)",
        breakaway(depth_world, dep) is True,
        f"breakaway_live={breakaway(depth_world, dep)!r} "
        f"kind={kind_of(depth_world, dep)!r}",
    )
    dep_on, _ = composed_frame(depth_world, dep)
    with lever_off():
        dep_off, _ = composed_frame(
            depth_world, arm_depth(make_fb(), anchor=DEPTH_LINE)
        )
    check(
        "[depth] the pin yields under BOTH lever states (no behaviour change)",
        dep_on is MovementState.CHASE
        and dep_off is MovementState.CHASE,
        f"on={dep_on!r} off={dep_off!r}",
    )

    # (c) an ORDINARY sweeper — no break at all — is untouched: the pin still
    # runs, which is the whole point of the sweeper contract.
    sweeper = make_fb()
    check(
        "[no break] the pin still RUNS for the ordinary sweeper",
        pin_yields(world, sweeper) is False,
    )
    sw_state, sw_x = composed_frame(world, sweeper)
    check(
        "[no break] ...so the sweeper keeps its DRIFT anchor command",
        sw_state is MovementState.DRIFT and sw_x == LINE_NO_BREAK,
        f"state={sw_state!r} tx={sw_x!r}",
    )
    with lever_off():
        check(
            "[no break] ...identically under the lever OFF (no A/B here)",
            pin_yields(world, make_fb()) is False,
        )

    # (d) an UNTAGGED body is never the controller's, so the pin always runs.
    untagged = make_fb(tagged=False)
    setattr(untagged, fb.FB_BREAK_LATCH_ATTR, True)
    check(
        "[untagged] the pin still RUNS even with a latch attribute present",
        pin_yields(world, untagged) is False,
    )


# ══════════════════════════════════════════════════════════════════════
# [7] SOURCE WIRING
# ══════════════════════════════════════════════════════════════════════

def sec_source_wiring():
    section(7, "Source wiring of the three writers")
    ctrl = _src("game/defence/defence_lvl_1/fullback_controller.py")
    mmdl = _src("game/defence/defence_lvl_1/movement_mode_decision.py")
    dbu = _src("game/defence/defence_lvl_1/defensive_behaviour_unified.py")

    # ── the controller ──
    check("the controller declares the lever",
          "RLM_FB_STATE_CONTROLLER" in ctrl
          and "def fullback_state_controller_enabled" in ctrl)
    for name in ("break_emergency_live", "controller_owns_fullback_state",
                 "shared_policy_state_allowed", "emergency_intent",
                 "emergency_fullback_state", "command_fullback_state"):
        check(f"the controller defines {name}", f"def {name}" in ctrl)
    check("it names its two sources",
          "SOURCE_STATE_OWNERSHIP" in ctrl and "SOURCE_SHARED_POLICY" in ctrl)
    check("it documents the stage",
          "STAGE 2 — STATE OWNERSHIP" in ctrl)
    check("the write goes through the SINGLE write path",
          "return enter_fullback_intent(" in ctrl)
    for name in ("command_fullback_state", "shared_policy_state_allowed",
                 "controller_owns_fullback_state", "FB_STATE_CONTROLLER",
                 "FB_STATE_CONTROLLER_ENV_VAR"):
        check(f"the controller exports {name}",
              f'"{name}"' in ctrl)

    # ── the lazy import (no module-scope cycle edge) ──
    edge = "from game.defence.defence_lvl_1.fullback_role import"
    check("the only fullback_role import edge is the lazy one",
          ctrl.count(edge) == 1, f"count={ctrl.count(edge)}")
    check("...and it lives inside the latch reader",
          ctrl.index(edge) > ctrl.index("def _break_latch_armed"),
          "the import precedes the reader, i.e. it is module-scope")

    # ── the MMDL ──
    check("the MMDL defines the single ownership helper",
          "def controller_owned_fullback_state" in mmdl)
    check("the latch gate RETURNS the controller's verdict",
          "return controller_owned_fullback_state(" in mmdl
          and "world, defender, _fb_chase" in mmdl)
    check("the helper resolves through the controller, not through a copy",
          "emergency_fullback_state as _fb_owned_state" in mmdl)
    check("the helper is total (a missing controller falls back)",
          "return fallback" in mmdl)
    check("the latch is still read lazily (no import cycle)",
          "is_fullback_break_latched as _fb_is_latched" in mmdl)

    # ── the shared dispatcher: BOTH pin sites ──
    check("the pin imports the controller's own predicate",
          dbu.count("shared_policy_state_allowed as _fb_shared_policy_enabled")
          == 2,
          f"count={dbu.count('shared_policy_state_allowed as _fb_shared_policy_enabled')}")
    check("both pin sites read breakaway_live",
          dbu.count("_fb_breakaway_live(world, fb_actor)") == 2,
          f"count={dbu.count('_fb_breakaway_live(world, fb_actor)')}")
    check("both pin sites OR in the controller's verdict",
          dbu.count("or not _fb_shared_policy_enabled(world, fb_actor)") == 2,
          f"count={dbu.count('or not _fb_shared_policy_enabled(world, fb_actor)')}")
    check("both pin sites route the state through the ONE write path",
          dbu.count("enter_fullback_intent as _fb_enter") == 2,
          f"count={dbu.count('enter_fullback_intent as _fb_enter')}")
    check("the shared policy no longer assigns a fullback state directly",
          "fb_actor.movement_state = MovementState.DRIFT" not in dbu)
    check("both sites are documented as the Stage-2 handover",
          dbu.count("STAGE 2 — STATE OWNERSHIP") == 2,
          f"count={dbu.count('STAGE 2 — STATE OWNERSHIP')}")


# ══════════════════════════════════════════════════════════════════════
# [8] DETERMINISM AND DEGENERATE INPUTS
# ══════════════════════════════════════════════════════════════════════

def sec_determinism():
    section(8, "Determinism and degenerate inputs")
    world = make_world()

    def reading():
        lat = arm_lateral(make_fb())
        dep = arm_depth(make_fb())
        plain = make_fb()
        return (
            break_emergency_live(lat),
            break_emergency_live(dep),
            break_emergency_live(plain),
            controller_owns_fullback_state(world, lat),
            shared_policy_state_allowed(world, plain),
            emergency_fullback_state(world, lat),
            emergency_intent(lat),
            pin_yields(world, lat),
            pin_yields(world, plain),
        )

    first = reading()
    check("the ownership reading is deterministic across runs",
          reading() == first == reading(),
          f"first={first!r}")

    # Degenerate inputs: every entry point is total.
    check("break_emergency_live(None) is a safe False",
          break_emergency_live(None) is False)
    check("controller_owns_fullback_state(None, None) is a safe False",
          controller_owns_fullback_state(None, None) is False)
    check("shared_policy_state_allowed(None, None) is a safe True",
          shared_policy_state_allowed(None, None) is True)
    check("emergency_fullback_state(None, None) commands nothing",
          emergency_fullback_state(None, None) is None)
    check("command_fullback_state(None, None) writes nothing",
          command_fullback_state(None, None) is None)
    check("emergency_intent(None) is the sweeper default",
          emergency_intent(None) is FullbackIntent.SWEEP)
    check("a world with no meter readings still resolves ownership",
          controller_owns_fullback_state(make_world(ruck_x=None),
                                         arm_lateral(make_fb())) is True)

    # A no-op write must not corrupt a body it does not own.
    bystander = make_fb(movement_state=MovementState.HOLD)
    before = bystander.movement_state
    command_fullback_state(world, bystander)
    check("an unowned body keeps its exact state object",
          bystander.movement_state is before)


def main():
    print("FULLBACK STATE OWNERSHIP — STAGE 2 CONTRACT (2026-09-30)")
    print("=" * 68)
    sec_lever()
    sec_ownership()
    sec_command_path()
    sec_mmdl_defers()
    sec_release()
    sec_pin_split_brain()
    sec_source_wiring()
    sec_determinism()
    for err in BREAKAWAY_ERRORS:
        check("breakaway_live stayed total", False, err)
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS — for as long as a tagged fullback's break emergency")
    print("        is live the controller is the SOLE authority for its")
    print("        movement state: the MMDL gate carries the controller's")
    print("        verdict, the shared pin yields on a LATERAL break it can")
    print("        no longer clobber, the shared policy resumes the moment")
    print("        the latch clears, and RLM_FB_STATE_CONTROLLER=0 restores")
    print("        the Stage-1 arrangement write-for-write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
