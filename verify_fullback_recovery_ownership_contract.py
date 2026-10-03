"""verify_fullback_recovery_ownership_contract.py -- STAGE 3: RECOVERY OWNERSHIP.

GATES: a tagged fullback owns ITS OWN recovery, and the four shared recovery
pathways must not name a target, a movement state or a pace for that body.

What Stage 3 claims, and what this file proves:

  [1] THE LEVER.  ``RLM_FB_RECOVERY_CONTROLLER`` (default ON), the in-process
      rebind the A/B uses, and the facade's own reader.

  [2] THE OWNERSHIP LAW.  ``recovery_contract_owned(world, d)`` is true only
      for a tagged fullback with the lever on, ``shared_recovery_allowed`` is
      its EXACT negation, and every line defender is allowed.

  [3] THE FOUR SHARED PATHWAYS DENY THE TAGGED FULLBACK, on a live break:
        * ``line_break_static_guard.is_static_guard_recovery_eligible``
          (the shared recovery predicate, which freeze-expiry delegates to),
        * ``line_break_static_guard.is_static_guard_eligible``,
        * ``line_break_static_guard.enforce_line_break_static_guard``
          (the static-pin guard -- the target must not move),
        * ``freeze_expiry_pursuit.is_expiry_pursuit_eligible`` /
          ``hand_back_expiry_pursuit``,
        * ``post_miss_pursuit.is_exempt_role`` / ``arm_post_miss_pursuit`` /
          ``owns_post_miss_pursuit`` / ``pursue_carrier``.
      Each is checked AGAINST AN UNTAGGED CONTROL so a broken fixture can
      never masquerade as a pass.

  [4] THE DEFERENCE IS REAL, NOT COINCIDENTAL.  Monkeypatching the
      controller's ``recovery_contract_owned`` to deny a PLAIN line defender
      must deny that defender on all four pathways with the lever ON, and must
      NOT with the lever OFF (they then read their own historical checks).
      This is the Stage-2 technique: the lever switches WHO defines the
      denial, not the size of the denied set.

  [5] THE FULLBACK'S OWN SINGLE RECOVERY CONTRACT.  ``recovery_intent``
      resolves CHASE on a live break, RECOVER on owed ground, SWEEP
      otherwise and UNOWNED for a body that is not the fullback;
      ``declare_fullback_recovery`` stamps the declaration and the readers
      read it back, and stamps nothing with the lever off.

  [6] MISSES / RECOVERIES / RE-ENGAGEMENT ARE FB-OWNED.  A beaten, frozen,
      freeze-timed tagged fullback is declined by all four shared pathways AND
      is still resolved CHASE by its own contract; and the miss-teardown
      release still fires with the SHARED-recovery lever disarmed -- the FB
      contract is not downstream of the shared pathway.

  [7] SOURCE WIRING.  All four modules read the contract; the controller
      imports none of them at module scope (no cycle); and ``fullback_role``'s
      Stage-1 write-path counts (routed producers, zero bare writes) are
      unchanged by this stage.

  [8] DEGENERATE INPUTS.  None world / None body never raise and never claim
      ownership.

Exits 1 on any failed contract check (CI-style).
"""
import os
import sys

sys.path.insert(0, ".")

from player.movement_states import MovementState

import game.defence.defence_lvl_1.fullback_controller as fbc
from game.defence.defence_lvl_1.fullback_controller import (
    CONTROLLER,
    FullbackIntent,
    declare_fullback_recovery,
    declared_fullback_recovery,
    declared_fullback_recovery_frame,
    declared_fullback_recovery_source,
    fullback_recovery_controller_enabled,
    recovery_contract_owned,
    recovery_intent,
    shared_recovery_allowed,
)
from game.defence.defence_lvl_1 import line_break_static_guard as lbsg
from game.defence.defence_lvl_1 import freeze_expiry_pursuit as fep
from game.defence.defence_lvl_1 import post_miss_pursuit as pmp
from game.defence.defence_lvl_1 import fullback_role as fbr
from game.defence.defence_lvl_1.fullback_role import compute_fullback_anchor

HERE = os.path.dirname(os.path.abspath(__file__))
CONTROLLER_PATH = "game/defence/defence_lvl_1/fullback_controller.py"
GUARD_PATH = "game/defence/defence_lvl_1/line_break_static_guard.py"
EXPIRY_PATH = "game/defence/defence_lvl_1/freeze_expiry_pursuit.py"
POST_MISS_PATH = "game/defence/defence_lvl_1/post_miss_pursuit.py"
ROLE_PATH = "game/defence/defence_lvl_1/fullback_role.py"

ANCHOR_X = 60.0
FAILURES = []
CHECKS = 0


def check(name, ok, detail=""):
    global CHECKS
    CHECKS += 1
    if ok:
        print("  PASS  %s%s" % (name, ("  %s" % detail) if detail else ""))
    else:
        print("  FAIL  %s  %s" % (name, detail))
        FAILURES.append(name)


def section(number, title):
    print("\n[%d] %s" % (number, title))


def src_of(rel_path):
    with open(os.path.join(HERE, rel_path), "r", encoding="utf-8") as fh:
        return fh.read()


class LineBody:
    """A clean, unowned line body -- the same stub recipe the freeze-expiry
    contract verifier proves is genuinely recovery-eligible."""

    def __init__(self, **kw):
        self.x = kw.get("x", 62.0)
        self.y = kw.get("y", 35.0)
        self.target_x = kw.get("target_x", 62.0)
        self.target_y = kw.get("target_y", 35.0)
        self.frozen = kw.get("frozen", False)
        self.freeze_timer = kw.get("freeze_timer", 0.0)
        self.movement_state = kw.get("movement_state", MovementState.DRIFT)
        self.move_speed = kw.get("move_speed", 6.0)
        self.max_speed_mps = kw.get("max_speed_mps", 9.2)
        self.max_defensive_speed_mps = 8.3
        self.timing_locked = False
        self.reaction_timer = 0.0
        self.retreat_delay = 0.0
        self.lateral_hesitation = 0.0
        self.step_stagger = 0.0
        self.step_stagger_active = False
        self.target_source = kw.get("target_source", "mmdl_drift")
        self.defender_role = kw.get("defender_role", "DEF_LINE_READY")
        self.tackle_role = None
        self._tackle_commit_active = False
        self._pursuit_commit_active = False
        self.pre_tackle_stance = False
        self.tackle_state_active = False
        self.tes_attached = False
        self.in_contact = False
        self.fullback_role_active = kw.get("fullback_role_active", False)


class Carrier:
    def __init__(self, x=70.0, y=40.0):
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.is_ball_carrier = True


class LineWorld:
    """Minimum world for ``defensive_line_anchor_m`` + the break predicate."""

    def __init__(self, players, bc, anchor_x=ANCHOR_X, tes_active=False,
                 recover_allowed=False):
        self.frame_count = 1000
        self.players = list(players)
        self.ball_carrier = bc
        self.defensive_line_anchor = anchor_x
        self.anchor_locked = True
        self.ruck_x_m = anchor_x - 10.0
        self.ruck_y_m = 35.0
        self.tes_active = tes_active
        self.fullback_recover_allowed = recover_allowed


def make_tagged_fb(**kw):
    body = LineBody(**kw)
    body.fullback_role_active = True
    return body


# ======================================================================
# [1] THE LEVER
# ======================================================================

def sec_lever():
    section(1, "The recovery lever -- name, default, in-process rebind")
    check("the lever env var is the documented name",
          fbc.FB_RECOVERY_CONTROLLER_ENV_VAR == "RLM_FB_RECOVERY_CONTROLLER",
          "got %r" % (fbc.FB_RECOVERY_CONTROLLER_ENV_VAR,))
    check("the lever is a module boolean", isinstance(fbc.FB_RECOVERY_CONTROLLER, bool))
    check("the lever ships ON", fullback_recovery_controller_enabled() is True)
    saved = fbc.FB_RECOVERY_CONTROLLER
    try:
        fbc.FB_RECOVERY_CONTROLLER = False
        check("rebinding the lever OFF disarms it in-process",
              fullback_recovery_controller_enabled() is False)
    finally:
        fbc.FB_RECOVERY_CONTROLLER = saved
    check("the lever is distinct from the Stage-1 intent lever",
          fbc.FB_RECOVERY_CONTROLLER_ENV_VAR != fbc.FB_INTENT_CONTROLLER_ENV_VAR)
    check("the lever is distinct from the Stage-2 state lever",
          fbc.FB_RECOVERY_CONTROLLER_ENV_VAR != fbc.FB_STATE_CONTROLLER_ENV_VAR)
    check("the facade exposes the recovery lever",
          CONTROLLER.recovery_controller_enabled() is True)


# ======================================================================
# [2] THE OWNERSHIP LAW
# ======================================================================

def sec_ownership_law():
    section(2, "The ownership law -- tag x lever, and the exact negation")
    world = LineWorld([], Carrier())
    tagged = make_tagged_fb()
    plain = LineBody()
    check("a tagged fullback is owned by the controller",
          recovery_contract_owned(world, tagged) is True)
    check("...and the shared pathways are therefore NOT allowed",
          shared_recovery_allowed(world, tagged) is False)
    check("a plain line defender is NOT owned",
          recovery_contract_owned(world, plain) is False)
    check("...and the shared pathways ARE allowed",
          shared_recovery_allowed(world, plain) is True)
    check("the two predicates are exact negations (tagged)",
          shared_recovery_allowed(world, tagged)
          is (not recovery_contract_owned(world, tagged)))
    check("the two predicates are exact negations (plain)",
          shared_recovery_allowed(world, plain)
          is (not recovery_contract_owned(world, plain)))
    saved = fbc.FB_RECOVERY_CONTROLLER
    try:
        fbc.FB_RECOVERY_CONTROLLER = False
        check("lever OFF: the tag alone does not own the recovery",
              recovery_contract_owned(world, tagged) is False)
        check("lever OFF: the shared pathways are allowed again (historical checks rule)",
              shared_recovery_allowed(world, tagged) is True)
    finally:
        fbc.FB_RECOVERY_CONTROLLER = saved
    check("a world is not required to own (the tag is per-actor)",
          recovery_contract_owned(None, tagged) is True)
    check("the facade agrees with the module predicate",
          CONTROLLER.owns_recovery(world, tagged) is True
          and CONTROLLER.owns_recovery(world, plain) is False
          and CONTROLLER.shared_recovery_allowed(world, tagged) is False)
# ======================================================================
# [3] THE FOUR SHARED PATHWAYS DENY THE TAGGED FULLBACK
# ======================================================================

def sec_shared_pathways_deny():
    section(3, "The four shared pathways deny the tagged fullback (live break)")
    import types as _types

    carrier = Carrier(x=70.0, y=40.0)
    world = LineWorld([], carrier)
    fwd = Carrier(x=70.0, y=40.0)
    world_fwd = LineWorld([], fwd)

    tagged = make_tagged_fb()
    control = LineBody()

    # -- the premise: the CONTROL is genuinely eligible on every pathway --
    check("PREMISE: the untagged control is shared-recovery eligible",
          lbsg.is_static_guard_recovery_eligible(control, world) is True)
    check("PREMISE: the untagged control is expiry-pursuit eligible",
          fep.is_expiry_pursuit_eligible(control, world) is True)
    check("PREMISE: the untagged control is NOT exempt from post-miss pursuit",
          pmp.is_exempt_role(control, world) is False)

    # -- 1/2. the shared recovery predicate AND the guard built on it --
    check("shared recovery declines the tagged fullback",
          lbsg.is_static_guard_recovery_eligible(tagged, world) is False)
    check("the static guard declines the tagged fullback",
          lbsg.is_static_guard_eligible(tagged, world) is False)

    # -- 3. the static guard's actual WRITE leaves the tagged body alone --
    tagged_pin = make_tagged_fb(x=62.0, y=35.0, target_x=62.0,
                                target_source="struct_freeze_timer")
    control_pin = LineBody(x=62.0, y=35.0, target_x=62.0,
                           target_source="struct_freeze_timer")
    tuners = _types.SimpleNamespace(max_defensive_speed_mps=8.3)
    rearmed = lbsg.enforce_line_break_static_guard(
        world_fwd, [tagged_pin, control_pin], bc=fwd, tuners=tuners,
    )
    check("the guard does NOT re-arm the tagged fullback",
          tagged_pin not in rearmed
          and tagged_pin.target_x == 62.0
          and tagged_pin.target_source == "struct_freeze_timer"
          and tagged_pin.movement_state is MovementState.DRIFT,
          "target=%r src=%r" % (tagged_pin.target_x, tagged_pin.target_source))
    check("...while the untagged control IS re-armed by the same call",
          control_pin in rearmed
          and control_pin.target_x == float(fwd.x)
          and control_pin.target_source == lbsg.LINE_BREAK_STATIC_GUARD_SOURCE,
          "target=%r src=%r" % (control_pin.target_x, control_pin.target_source))

    # -- 4. freeze-expiry --
    check("freeze-expiry declines the tagged fullback",
          fep.is_expiry_pursuit_eligible(tagged, world) is False)
    pinned = make_tagged_fb(x=62.0, y=35.0, target_x=62.0, frozen=True,
                            freeze_timer=0.5)
    check("the freeze-expiry hand-back declines the tagged fullback",
          fep.hand_back_expiry_pursuit(pinned, world) is False
          and pinned.target_x == 62.0)

    # -- 5. post-miss pursuit --
    check("post-miss pursuit exempts the tagged fullback",
          pmp.is_exempt_role(tagged, world) is True)
    check("post-miss ARMS no entitlement on the tagged fullback",
          pmp.arm_post_miss_pursuit(world, tagged) is False
          and pmp.post_miss_pursuit_until(tagged) is None)
    armed = make_tagged_fb(x=62.0, y=35.0, target_x=62.0)
    pmp.arm_post_miss_pursuit(world, armed)
    armed.__dict__[pmp.POST_MISS_PURSUIT_ARM_ATTR] = 99_999
    held = (armed.target_x, armed.target_y, armed.target_source)
    check("post-miss ownership declines the tagged fullback",
          pmp.owns_post_miss_pursuit(world, armed) is False)
    check("post-miss pursuit writes nothing to the tagged fullback",
          pmp.pursue_carrier(world, armed) is False
          and (armed.target_x, armed.target_y, armed.target_source) == held)


# ======================================================================
# [4] THE DEFERENCE IS REAL (monkeypatch the controller's predicate)
# ======================================================================

def sec_deference():
    section(4, "Deference -- the pathways read the controller, not a copy")
    import types as _types

    carrier = Carrier(x=70.0, y=40.0)
    world = LineWorld([], carrier)
    plain = LineBody(x=62.0, y=35.0, target_x=62.0,
                     target_source="struct_freeze_timer")
    plain2 = LineBody(x=62.0, y=35.0)

    original = fbc.recovery_contract_owned
    saved_lever = fbc.FB_RECOVERY_CONTROLLER
    try:
        fbc.recovery_contract_owned = lambda world, d: True
        check("LEVER ON: a PLAIN body is denied by the shared recovery predicate",
              lbsg.is_static_guard_recovery_eligible(plain2, world) is False)
        check("LEVER ON: a PLAIN body is denied by the static guard",
              lbsg.is_static_guard_eligible(plain2, world) is False)
        check("LEVER ON: a PLAIN body is denied by freeze-expiry",
              fep.is_expiry_pursuit_eligible(plain2, world) is False)
        check("LEVER ON: a PLAIN body is exempted from post-miss pursuit",
              pmp.is_exempt_role(plain2, world) is True)
        rearmed = lbsg.enforce_line_break_static_guard(
            world, [plain], bc=carrier,
            tuners=_types.SimpleNamespace(max_defensive_speed_mps=8.3),
        )
        check("LEVER ON: the guard writes nothing to a controller-owned body",
              plain not in rearmed and plain.target_x == 62.0)

        # -- restore the true predicate and disarm the lever: the four
        #    pathways fall back to their historical per-module checks --
        fbc.recovery_contract_owned = original
        fbc.FB_RECOVERY_CONTROLLER = False
        check("LEVER OFF: a plain body is eligible again (historical checks rule)",
              lbsg.is_static_guard_recovery_eligible(plain2, world) is True)
        check("LEVER OFF: freeze-expiry is eligible again",
              fep.is_expiry_pursuit_eligible(plain2, world) is True)
        check("LEVER OFF: post-miss pursuit is NOT exempt again",
              pmp.is_exempt_role(plain2, world) is False)
        check("LEVER OFF: the tagged fullback is still denied by the historical check",
              lbsg.is_static_guard_recovery_eligible(
                  make_tagged_fb(x=62.0, y=35.0), world) is False
              and fep.is_expiry_pursuit_eligible(
                  make_tagged_fb(x=62.0, y=35.0), world) is False
              and pmp.is_exempt_role(
                  make_tagged_fb(x=62.0, y=35.0), world) is True)
        # -- lever ON again: the CONTRACT gate denies a plain body once more --
        fbc.recovery_contract_owned = lambda world, d: True
        fbc.FB_RECOVERY_CONTROLLER = True
        check("LEVER ON: the contract gate denies a plain body again",
              lbsg.is_static_guard_recovery_eligible(plain2, world) is False
              and fep.is_expiry_pursuit_eligible(plain2, world) is False)
    finally:
        fbc.recovery_contract_owned = original
        fbc.FB_RECOVERY_CONTROLLER = saved_lever
    check("the controller predicate is restored",
          fbc.recovery_contract_owned is original)


# ======================================================================
# [5] THE FULLBACK'S OWN SINGLE RECOVERY CONTRACT
# ======================================================================

def sec_own_contract():
    section(5, "The fullback's own single recovery contract")
    tagged = make_tagged_fb()
    plain = LineBody()
    check("an untagged body has NO recovery intent (UNOWNED)",
          recovery_intent(None, plain) is FullbackIntent.UNOWNED)

    # CHASE -- a genuine break is live.
    break_carrier = Carrier(x=80.0, y=35.0)
    break_world = LineWorld([], break_carrier)
    chaser = make_tagged_fb(x=70.0, y=35.0)
    check("a live break resolves the FB recovery intent to CHASE",
          recovery_intent(break_world, chaser) is FullbackIntent.CHASE,
          "got %r" % (recovery_intent(break_world, chaser),))

    # RECOVER -- owed ground, no break.
    no_break_carrier = Carrier(x=62.0, y=45.0)
    owe_world = LineWorld([], no_break_carrier, anchor_x=70.0,
                          recover_allowed=True)
    ax, ay = compute_fullback_anchor(no_break_carrier, 70.0)
    owing = make_tagged_fb(x=ax - 20.0, y=ay)
    check("owed ground resolves the FB recovery intent to RECOVER",
          recovery_intent(owe_world, owing, ax, ay) is FullbackIntent.RECOVER,
          "got %r (debt %.1f m)"
          % (recovery_intent(owe_world, owing, ax, ay), ax - owing.x))

    # SWEEP -- no break, no owed ground.
    quiet_carrier = Carrier(x=ANCHOR_X - 5.0, y=35.0)
    quiet_world = LineWorld([], quiet_carrier)
    quiet = make_tagged_fb(x=62.0, y=35.0)
    check("no break and no debt resolves the FB recovery intent to SWEEP",
          recovery_intent(quiet_world, quiet) is FullbackIntent.SWEEP,
          "got %r" % (recovery_intent(quiet_world, quiet),))

    # the declaration stamps the decision and the readers read it back
    body = make_tagged_fb(x=70.0, y=35.0)
    declared = declare_fullback_recovery(break_world, body)
    check("declare_fullback_recovery returns the resolved intent",
          declared is FullbackIntent.CHASE)
    check("the declared recovery is readable back",
          declared_fullback_recovery(body) is FullbackIntent.CHASE)
    check("the declaration stamps its source",
          declared_fullback_recovery_source(body) == fbc.SOURCE_RECOVERY,
          "got %r" % (declared_fullback_recovery_source(body),))
    check("the declaration stamps the frame",
          declared_fullback_recovery_frame(body) == break_world.frame_count)
    check("the facade declares and reads the same contract",
          CONTROLLER.declare_recovery(break_world, body)
          is FullbackIntent.CHASE
          and CONTROLLER.declared_recovery(body) is FullbackIntent.CHASE)

    # lever OFF: the decision is still returned, but nothing is stamped
    silent = make_tagged_fb(x=70.0, y=35.0)
    saved = fbc.FB_RECOVERY_CONTROLLER
    try:
        fbc.FB_RECOVERY_CONTROLLER = False
        resolved = declare_fullback_recovery(break_world, silent)
        check("lever OFF: the resolution is unchanged",
              resolved is FullbackIntent.CHASE)
        check("lever OFF: nothing is stamped",
              declared_fullback_recovery(silent) is None
              and declared_fullback_recovery_source(silent) == "")
    finally:
        fbc.FB_RECOVERY_CONTROLLER = saved
# ======================================================================
# [6] MISSES / RECOVERIES / RE-ENGAGEMENT ARE FULLBACK-OWNED
# ======================================================================

def sec_miss_ownership():
    section(6, "Misses -- FB-owned, and not downstream of the shared lever")
    import types as _types

    carrier = Carrier(x=80.0, y=35.0)
    world = LineWorld([], carrier)
    tuners = _types.SimpleNamespace(max_defensive_speed_mps=8.3)

    # A beaten fullback after a miss: tag + frozen + live timer + pinned target.
    beaten = make_tagged_fb(x=62.0, y=35.0, target_x=62.0, frozen=True,
                            freeze_timer=0.5,
                            target_source="struct_freeze_timer")
    beaten.__dict__["_freeze_pin_active"] = True
    beaten.target_y = 35.0

    check("MISS: shared recovery declines the beaten fullback",
          lbsg.is_static_guard_recovery_eligible(beaten, world) is False)
    check("MISS: the static guard declines the beaten fullback",
          lbsg.is_static_guard_eligible(beaten, world) is False)
    check("MISS: freeze-expiry declines the beaten fullback",
          fep.is_expiry_pursuit_eligible(beaten, world) is False)
    check("MISS: post-miss pursuit exempts the beaten fullback",
          pmp.is_exempt_role(beaten, world) is True
          and pmp.arm_post_miss_pursuit(world, beaten) is False)
    rearmed = lbsg.enforce_line_break_static_guard(
        world, [beaten], bc=carrier, tuners=tuners,
    )
    check("MISS: the guard leaves the beaten fullback's target untouched",
          beaten not in rearmed and beaten.target_x == 62.0
          and beaten.frozen is True)

    # ...and the FB's OWN contract still resolves the re-engagement.
    check("MISS: the fullback's own contract still resolves CHASE",
          recovery_intent(world, beaten) is FullbackIntent.CHASE)
    check("MISS: the fullback's own recovery is declarable while beaten",
          declare_fullback_recovery(world, beaten) is FullbackIntent.CHASE
          and declared_fullback_recovery(beaten) is FullbackIntent.CHASE)

    # The miss-teardown release is the FB's OWN behaviour: the SHARED-recovery
    # lever being off must not disarm it.
    class TeamWorld(LineWorld):
        pass

    engaged = make_tagged_fb(x=62.0, y=35.0)
    fbr.set_fullback_emergency_engaged(engaged)
    engaged.defender_role = "TACKLER_1"
    team_world = TeamWorld([engaged], carrier)
    team_world.defending_team = [engaged]
    saved = fbc.FB_RECOVERY_CONTROLLER
    try:
        fbc.FB_RECOVERY_CONTROLLER = False
        released = fbr.release_fullback_emergency_on_miss(team_world)
        check("MISS: the miss teardown still releases with the shared lever OFF",
              released == [engaged]
              and fbr.is_fullback_emergency_engaged(engaged) is False,
              "released=%r" % (len(released),))
        check("MISS: the relief restores the line role, never the tag",
              engaged.defender_role.name == "DEF_LINE_READY"
              and engaged.fullback_role_active is True)
    finally:
        fbc.FB_RECOVERY_CONTROLLER = saved


# ======================================================================
# [7] SOURCE WIRING
# ======================================================================

def sec_source_wiring():
    section(7, "Source wiring -- one contract, four readers, no cycle")
    ctrl_src = src_of(CONTROLLER_PATH)
    guard_src = src_of(GUARD_PATH)
    expiry_src = src_of(EXPIRY_PATH)
    pmp_src = src_of(POST_MISS_PATH)
    role_src = src_of(ROLE_PATH)

    check("the controller declares the recovery ownership predicate",
          "def recovery_contract_owned(" in ctrl_src)
    check("the controller declares the positive shared-pathway gate",
          "def shared_recovery_allowed(" in ctrl_src)
    check("the controller declares the single recovery decision",
          "def recovery_intent(" in ctrl_src)
    check("the controller declares the FB-owned recovery declaration",
          "def declare_fullback_recovery(" in ctrl_src)

    check("the shared-recovery predicate reads the contract",
          "shared_recovery_allowed" in guard_src
          and "fullback_controller" in guard_src)
    check("the static guard's write reads the contract",
          guard_src.count("_fb_shared_recovery_allowed(world, d)") >= 2,
          "found %d" % guard_src.count("_fb_shared_recovery_allowed(world, d)"))
    check("freeze-expiry reads the contract",
          "shared_recovery_allowed" in expiry_src
          and "fullback_controller" in expiry_src)
    check("post-miss pursuit reads the contract",
          "recovery_contract_owned" in pmp_src
          and "fullback_controller" in pmp_src)
    check("the geometry writer declares the FB-owned recovery",
          "declare_fullback_recovery(" in role_src)

    # NO module-scope import edge, in EITHER direction.
    module_scope = [
        line for line in ctrl_src.splitlines()
        if (line.startswith("import ") or line.startswith("from "))
        and any(name in line for name in (
            "fullback_role", "line_break_static_guard",
            "freeze_expiry_pursuit", "post_miss_pursuit",
        ))
    ]
    check("the controller imports none of the four modules at module scope",
          not module_scope, str(module_scope))
    for name, src in (("static guard", guard_src),
                      ("freeze-expiry", expiry_src),
                      ("post-miss pursuit", pmp_src)):
        check("%s imports the controller FUNCTION-LOCALLY only" % name,
              "\nfrom game.defence.defence_lvl_1.fullback_controller" not in src)
    check("post-miss pursuit still has NO module-scope project import",
          pmp_src.count("\nfrom game.") == 0
          and pmp_src.count("\nimport game.") == 0)
    check("the exempt-role set is still declared in post-miss pursuit",
          "_EXEMPT_ROLE_NAMES = frozenset" in pmp_src)

    # Stage-1's write-path counts must survive this stage untouched.
    check("fullback_role still routes every producer through the controller",
          role_src.count("enter_fullback_intent(") >= 7,
          "routed=%d" % role_src.count("enter_fullback_intent("))
    check("fullback_role still contains NO bare movement-state write",
          role_src.count("movement_state =") == 0,
          "bare=%d" % role_src.count("movement_state ="))


# ======================================================================
# [8] DEGENERATE INPUTS
# ======================================================================

def sec_degenerate():
    section(8, "Degenerate inputs -- never raise, never claim")
    world = LineWorld([], Carrier())
    check("None body is never owned",
          recovery_contract_owned(world, None) is False
          and recovery_contract_owned(None, None) is False)
    check("None body is always allowed by the shared pathways",
          shared_recovery_allowed(None, None) is True
          and shared_recovery_allowed(world, None) is True)
    check("None body resolves to UNOWNED",
          recovery_intent(world, None) is FullbackIntent.UNOWNED
          and recovery_intent(None, None) is FullbackIntent.UNOWNED)
    check("declaring on a None body returns UNOWNED and does not raise",
          declare_fullback_recovery(None, None) is FullbackIntent.UNOWNED
          and declared_fullback_recovery(None) is None
          and declared_fullback_recovery_source(None) == ""
          and declared_fullback_recovery_frame(None) is None)
    check("an untagged body is never recovered by the shared predicates' owner",
          recovery_contract_owned(None, LineBody()) is False)
    check("post-miss exempt reader survives None",
          pmp.is_exempt_role(None) is True)
    check("shared recovery eligibility survives None",
          lbsg.is_static_guard_recovery_eligible(None, world) is False
          and lbsg.is_static_guard_eligible(None, world) is False)
    check("expiry eligibility survives None",
          fep.is_expiry_pursuit_eligible(None, world) is False)
    quiet = LineWorld([], Carrier(x=ANCHOR_X - 5.0, y=35.0))
    check("no live break and no armed recovery resolves SWEEP",
          recovery_intent(quiet, make_tagged_fb(x=62.0, y=35.0))
          is FullbackIntent.SWEEP)


# ======================================================================

def main():
    print("FULLBACK RECOVERY OWNERSHIP CONTRACT (Stage 3, 2026-09-30)")
    print("=" * 68)
    sec_lever()
    sec_ownership_law()
    sec_shared_pathways_deny()
    sec_deference()
    sec_own_contract()
    sec_miss_ownership()
    sec_source_wiring()
    sec_degenerate()
    print("\n" + "=" * 68)
    if FAILURES:
        print("RESULT: FAIL (%d check(s))" % len(FAILURES))
        for label in FAILURES:
            print("  - %s" % label)
        return 1
    print("RESULT: PASS -- ALL %d CHECKS PASSED" % CHECKS)
    print("        The tagged fullback owns its own recovery: the shared")
    print("        recovery, freeze-expiry, post-miss pursuit and static-guard")
    print("        pathways yield to ONE contract, and misses, recoveries and")
    print("        re-engagement are fullback behaviours.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
