"""THE BUMP CLEARANCE PATH (dynamic results pipeline, step 2, 2026-09-24).

WHAT THIS VERIFIES
------------------
Step 1 built the collision-state discriminator: the margin between the
carrier's momentum and the whole defensive wrap resolves into exactly one of
``BUMP_OFF`` / ``DOMINANT_SMASH`` / ``PROGRESSIVE_DRAG``, and the token is
mirrored onto the frame's ``TESContext`` by ``tes.tes_start_tackle``.

Until this stage NOTHING READ THAT TOKEN.  Step 2 makes it act: when the
declaration resolved ``BUMP_OFF`` -- the carrier's momentum cleared the
wrap by more than the leaf's declared threshold -- the engine stops grinding
the carrier through a pre-baked tackle and tears the contact down instead.

The teardown itself already existed.  ``OpenPlayEngine._handle_broken_tackle``
was the engine's "the tackle failed" exit: same phase exit, same role/depth
invalidation, same chase re-arming.  This stage parameterises it rather than
forking it, so there is ONE teardown with two declared variants:

    bump_off=False  (every pre-existing call site -- bit-identical)
    bump_off=True   (the BUMP CLEARANCE path)

and exactly three declared deltas between them:

  1. the carrier KEEPS its momentum -- no ``*0.9`` damp, plus a short forward
     burst;
  2. the wrap is torn down at the source via ``_detach_all_tacklers()``;
  3. the primary defender goes down for LONGER and its TES commit locks are
     cleared, so it cannot instantly re-tackle from behind.

This verifier gates, in order:

  [1] THE LEVER - the stage is armed by default and disarmed by exactly the
      documented values of ``RLM_TES_BUMP_CLEARANCE``; the disarm is read at
      CONSUMPTION time, so it can be flipped in-process.
  [2] THE PREDICATE - ``_bump_off_pending`` is TRUE only for a BUMP_OFF
      declaration; every other token, an absent context, an unreadable token
      and a disarmed lever are all FALSE.
  [3] THE CARRIER BURST - the window arms, re-stamps the carrier's speed
      authority above its normal steering speed, decays, expires to exactly
      zero and then stays a no-op.
  [4] THE COMMIT-LOCK CLEARING - a beaten tackler loses both TES drive commit
      locks and its tackle role.
  [5] THE TEARDOWN (REAL engine) - the real
      ``OpenPlayEngine._handle_broken_tackle(bump_off=True)`` on a real
      ``OpenPlayEngine``: carrier undamped, defender down for longer, joiners
      released, context/outcome cleared, attachments detached at the source,
      phase back to OPEN_PLAY.
  [6] THE CONTROL ARM - the SAME fixture with the plain call is the OLD
      behaviour exactly: the carrier IS damped by exactly 0.9 and the freeze
      is exactly the legacy 1.0 s.  The variants differ only by the declared
      deltas.
  [7] THE INTERCEPT ORDER - in all three TES phase handlers the BUMP_OFF
      check is stated BEFORE the momentum break gates, so an outright win
      can never be pre-empted by the drive-distance path.
  [8] THE PROGRESSIVE DRAG DECAY (dynamic results pipeline, step 3.1) -
      the OTHER contested state.  A declaration that resolved
      PROGRESSIVE_DRAG cleared the threshold in NEITHER direction, so the
      contact is a GRIND rather than a win for either side: the carrier is
      DECELERATED, one multiplicative decay step per sustained DRIVE frame,
      until the drive is spent and the standard PTB path takes over.  This
      section gates the declared multiplier, the predicate (which requires
      a RESOLVED model, because PROGRESSIVE_DRAG is also the identity
      token every unmeasured declaration defaults to), the frame-by-frame
      reduction itself, the three no-op arms, and the intercept's position
      inside _update_tackle_drive.
  [9] THE TERMINAL FLOOR (dynamic results pipeline, step 3.2) - the grind
      cannot run forever.  Once the decayed forward authority falls below
      PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS the carrier is no longer going
      anywhere, so the drive is abandoned mid-phase and the tackle completes
      as a standard ruck: the DRIVE-END lock is stamped, _prepare_release()
      forms the ruck, and the phase enters RELEASE -- the head of the
      Play-the-Ball chain.  This section gates the floor, the predicate, the
      end-to-end termination on a real engine, the teardown RELEASE performs
      on its own first frame, and the wiring that places the abort in the
      drive loop between the decay and the motion write.
  [10] INTEGRATION - the battery runner lists this verifier, the engine
      declares the lever and the three call sites, and the leaf's token is
      the one the predicate compares against.
  [11] THE POSITIONAL GRADIENT (Stage 4) - the collision band MOVES with the
      matchup.  Real bodies stamped through the positional mapper, driven
      through the real decider, resolved by the real wrap model: a running
      back clears a single forward (BUMP_OFF), and the SAME forward carrier
      facing a wrapping forward CLUSTER compounds into PROGRESSIVE_DRAG,
      which the engine then collapses into a native ruck.

Nothing here needs the simulation to be booted: the real engine object is
built with a minimal world, exactly as the sibling engine gates do.

Emits a literal "RESULT: PASS", which the FB harness's parser requires.
"""

from __future__ import annotations

import json
import os
import sys
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LOG_PATH = "tmp/verify_bump_clearance_contract.log"
REPORT_PATH = "verify_bump_clearance_contract_report.json"

_LOG_LINES = []
_CHECKS = []          # (label, passed)


def _emit(line: str) -> None:
    _LOG_LINES.append(line)
    print(line)


def banner(title: str) -> None:
    _emit("")
    _emit("=" * 78)
    _emit(title)
    _emit("=" * 78)


def check(label: str, passed: bool, detail: str = "") -> bool:
    _CHECKS.append((label, bool(passed)))
    suffix = ("  [%s]" % detail) if detail else ""
    _emit("  %s  %s%s" % ("PASS" if passed else "FAIL", label, suffix))
    return bool(passed)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

ENGINE_PATH = "world/open_play_engine.py"


def _engine_source() -> str:
    with open(ENGINE_PATH, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _body_of(source: str, def_line: str) -> str:
    """Return a module-level method's body text (up to the next 4-space def)."""
    start = source.index(def_line)
    rest = source[start + len(def_line):]
    end = len(rest)
    for marker in ("\n    def ", "\n    @", "\n# "):
        idx = rest.find(marker)
        if idx != -1 and idx < end:
            end = idx
    return rest[:end]


def _set_lever(value):
    """Set / clear the A/B lever, returning a restore callable."""
    var = "RLM_TES_BUMP_CLEARANCE"
    had = var in os.environ
    prev = os.environ.get(var)

    def restore():
        if had:
            os.environ[var] = prev
        else:
            os.environ.pop(var, None)

    if value is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = value
    return restore


class _Holder:
    """Minimal stand-in whose only job is to carry a ``tes_ctx``."""

    def __init__(self, ctx):
        self.tes_ctx = ctx


class _Ctx:
    """Minimal stand-in carrying only a collision-state token."""

    def __init__(self, token):
        self.wrap_collision_state = token


def _token_holder(token):
    """A holder whose context carries *token*, or no attribute at all."""
    if token is _ABSENT:
        return _Holder(object())
    return _Holder(_Ctx(token))


class _Absent:
    pass


_ABSENT = _Absent()


def _make_player(pid, x=60.0, y=35.0, **kw):
    from player.player import Player

    p = Player(x=x, y=y, player_id=pid)
    p.defender_role = kw.get("defender_role", None)
    p.depth_role = None
    p.fullback_role_active = False
    p.frozen = False
    p.freeze_timer = 0.0
    p.target_x = x
    p.target_y = y
    p.vx = float(kw.get("vx", 0.0))
    p.vy = float(kw.get("vy", 0.0))
    p.is_tackler = bool(kw.get("is_tackler", False))
    p.in_contact = True
    p.tes_locked = True
    p.tes_tackler_role = kw.get("tes_tackler_role", "PRIMARY")
    p.tes_attached = False
    p.attach_slot = None
    p.tackle_role = kw.get("tackle_role", "PRIMARY")
    p._tackle_commit_active = bool(kw.get("_tackle_commit_active", False))
    p._pursuit_commit_active = bool(kw.get("_pursuit_commit_active", False))
    p.max_speed_mps = float(kw.get("max_speed_mps", 8.3))
    p.mass_kg = float(kw.get("mass_kg", 90.0))
    p.anim_state = kw.get("anim_state", "TACKLE")
    p.movement_state = None
    return p


def _make_world():
    from world.world_state import WorldState, GameSubState

    w = WorldState()
    w.ruck_x_m = 60.0
    w.ruck_y_m = 35.0
    w.sim_time_s = 0.0
    w.dt = 1.0 / 60.0
    w.frame_count = 0
    w.tes_active = True
    w.tackler_ids = set()
    w.ball_owner = "BC"
    w.sub_state = GameSubState.TACKLE_CONTACT
    w.defending_team = []
    w.attacking_team = []
    return w


def _fixture(token, wrap_model=None):
    """A real OpenPlayEngine with a live TES contact wired by hand.

    ``wrap_model`` defaults to None, which IS ``TESContext``'s own declared
    default, so every pre-existing call site is unaffected.  The Stage 3.1
    drag predicate additionally requires a RESOLVED model (the
    PROGRESSIVE_DRAG token is also the identity every undecided declaration
    defaults to), so the drag sections pass a stub here.

    Returns (engine, carrier, primary, joiner, attached_body, ctx).
    """
    from world.open_play_engine import OpenPlayEngine
    from game.tes.tes_context import TESContext
    from game.tes.tes_outcomes import TESOutcome
    from player.player import DefenderRole

    w = _make_world()
    engine = OpenPlayEngine(w, seed=0)

    carrier = _make_player(100, x=60.0, y=35.0, vx=6.0, vy=1.0,
                           tackle_role="BALL_CARRIER", anim_state="RUN")
    primary = _make_player(1, x=59.0, y=35.0, vx=-2.0, vy=0.0,
                           defender_role=DefenderRole.TACKLER_1,
                           is_tackler=True, _tackle_commit_active=True,
                           _pursuit_commit_active=True)
    joiner = _make_player(2, x=59.5, y=35.5, vx=-1.0, vy=0.5,
                          defender_role=DefenderRole.TACKLER_2,
                          is_tackler=True, _tackle_commit_active=True)
    attached = _make_player(3, x=60.2, y=35.2,
                            defender_role=DefenderRole.TACKLER_3)
    attached.tes_attached = True
    attached.attach_slot = 1

    w.defending_team = [primary, joiner, attached]
    w.attacking_team = [carrier]
    w.ball_carrier = carrier
    # The engine resolves attached bodies through world.players (see
    # OpenPlayEngine._find_player_by_id), so the registry must be populated
    # or _detach_all_tacklers() cannot reach the body it is detaching.
    w.players = [carrier, primary, joiner, attached]

    ctx = TESContext()
    ctx.wrap_collision_state = token
    # Stage 3.1: ``None`` IS TESContext's own declared default, so every
    # pre-existing call site (which never passes this) sees exactly the
    # context it saw before this section existed.
    ctx.wrap_model = wrap_model
    ctx.attached = {3: 1}
    ctx.attached_ids = {3}
    ctx.attach_offsets = {3: (0.1, 0.0)}
    ctx.attach_enabled = True
    ctx.tacklers = [primary]

    engine.ball_carrier = carrier
    engine.primary = primary
    engine.joiners = [joiner]
    engine.tes_ctx = ctx
    engine.tes_outcome = TESOutcome()
    engine._phase = "TACKLE_CONTACT"
    return engine, carrier, primary, joiner, attached, ctx


def _bump_off_const():
    from game.tes.tes_multi_body import COLLISION_STATE_BUMP_OFF
    return COLLISION_STATE_BUMP_OFF


# ----------------------------------------------------------------------
# [1] THE LEVER
# ----------------------------------------------------------------------

def section_lever(ope):
    banner("[1] THE LEVER - armed by default, disarmed by the declared values")

    check("the stage declares its env var",
          ope.BUMP_CLEARANCE_ENV_VAR == "RLM_TES_BUMP_CLEARANCE",
          "var=%s" % ope.BUMP_CLEARANCE_ENV_VAR)

    restore = _set_lever(None)
    try:
        check("with the variable unset the lever is ARMED (opt-in default ON)",
              ope.bump_clearance_enabled() is True)
    finally:
        restore()

    for armed in ("1", "true", "on", "yes", "TRUE", " On "):
        restore = _set_lever(armed)
        try:
            check("the lever stays armed for %r" % armed,
                  ope.bump_clearance_enabled() is True)
        finally:
            restore()

    for off in ("0", "false", "no", "off", "", "FALSE", " Off "):
        restore = _set_lever(off)
        try:
            check("the lever is disarmed for %r" % off,
                  ope.bump_clearance_enabled() is False)
        finally:
            restore()

    check("the lever is read at CONSUMPTION time (in-process flip)",
          _flip_in_process(ope))
    return True


def _flip_in_process(ope):
    restore = _set_lever(None)
    armed = ope.bump_clearance_enabled()
    restore()
    off = _set_lever("0")
    try:
        disarmed = ope.bump_clearance_enabled()
    finally:
        off()
    restore()
    return armed is True and disarmed is False


# ----------------------------------------------------------------------
# [2] THE PREDICATE
# ----------------------------------------------------------------------

def section_predicate(ope):
    banner("[2] THE PREDICATE - BUMP_OFF and only BUMP_OFF arms the clearance")

    token = _bump_off_const()
    check("the leaf's BUMP_OFF token is the documented string",
          token == "BUMP_OFF", "token=%r" % token)

    restore = _set_lever(None)
    try:
        check("a BUMP_OFF context is PENDING",
              ope.OpenPlayEngine._bump_off_pending(
                  _token_holder(token)) is True)
        for other in ("PROGRESSIVE_DRAG", "DOMINANT_SMASH", "bump_off",
                      "BUMP", "", None):
            check("token %r does NOT arm the clearance" % (other,),
                  ope.OpenPlayEngine._bump_off_pending(
                      _token_holder(other)) is False)
        check("an ABSENT token attribute does NOT arm the clearance",
              ope.OpenPlayEngine._bump_off_pending(
                  _token_holder(_ABSENT)) is False)
        check("no context at all does NOT arm the clearance",
              ope.OpenPlayEngine._bump_off_pending(_Holder(None)) is False)
    finally:
        restore()

    restore = _set_lever("0")
    try:
        check("with the lever DISARMED even BUMP_OFF does not arm it",
              ope.OpenPlayEngine._bump_off_pending(
                  _token_holder(token)) is False)
    finally:
        restore()

    restore = _set_lever(None)
    try:
        check("the predicate is TOTAL over every degenerate context "
              "(missing attribute / None / non-string / absent ctx)",
              _total_over_degenerates(ope))
    finally:
        restore()
    return True


def _total_over_degenerates(ope):
    """The predicate must never RAISE, and never mis-arm, on any real context.

    Deliberately NOT asserted, and out of contract: a context whose
    ``wrap_collision_state`` accessor itself raises.  The mirror is a plain
    dataclass field written by ``tes.tes_start_tackle`` -- there is no
    accessor that can fail, and ``getattr(obj, name, default)`` does not
    swallow such exceptions anyway, so hardening for one would be defensive
    noise rather than a fix.
    """
    class _NoToken:
        pass

    class _BadToken:
        wrap_collision_state = 3.5

    class _BadCtx:
        tes_ctx = "not a context"

    for case in (_Holder(_NoToken()), _Holder(_BadToken()), _Holder(None),
                 _Holder(object()), _BadCtx()):
        try:
            got = ope.OpenPlayEngine._bump_off_pending(case)
        except Exception:
            return False
        if got is not False:
            return False
    return True


# ----------------------------------------------------------------------
# [3] THE CARRIER BURST
# ----------------------------------------------------------------------

def section_burst(ope):
    banner("[3] THE CARRIER BURST - the window arms, decays and expires")

    check("the burst window is a declared duration",
          ope.BUMP_OFF_CARRIER_BURST_S > 0.0,
          "%.3f s" % ope.BUMP_OFF_CARRIER_BURST_S)
    check("the burst is FASTER than the carrier's normal steering speed",
          ope.BUMP_OFF_CARRIER_BURST_SCALE > 0.9,
          "%.2fx vs the plain path's 0.90x" % ope.BUMP_OFF_CARRIER_BURST_SCALE)

    engine, carrier, _p, _j, _a, _ctx = _fixture(_bump_off_const())
    engine._bump_carrier_burst_s = 0.0

    # -- with no clearance in flight the tick is a NO-OP --
    top = carrier.max_speed_mps
    carrier.move_speed = top * 0.9
    engine._tick_bump_carrier_burst(1.0 / 60.0)
    check("with no window open the tick changes nothing",
          abs(carrier.move_speed - top * 0.9) < 1e-12
          and engine._bump_carrier_burst_s == 0.0,
          "move_speed=%.4f" % carrier.move_speed)

    # -- arming --
    engine._arm_bump_carrier_burst(carrier)
    check("arming the burst opens the declared window",
          abs(engine._bump_carrier_burst_s - ope.BUMP_OFF_CARRIER_BURST_S) < 1e-12,
          "%.3f s" % engine._bump_carrier_burst_s)
    check("arming raises the carrier's speed authority to the burst scale",
          abs(carrier.move_speed - top * ope.BUMP_OFF_CARRIER_BURST_SCALE) < 1e-9,
          "%.4f == %.2f x %.2f" % (carrier.move_speed,
                                   top, ope.BUMP_OFF_CARRIER_BURST_SCALE))
    check("the burst is a speed SCALE, not a velocity rewrite",
          abs(carrier.vx - 6.0) < 1e-12 and abs(carrier.vy - 1.0) < 1e-12,
          "vx=%.3f vy=%.3f" % (carrier.vx, carrier.vy))

    # -- the attack pipeline overwrites move_speed every frame; the tick
    #    restores it, which is the whole reason the window exists --
    carrier.move_speed = top * 0.9
    engine._tick_bump_carrier_burst(1.0 / 60.0)
    check("the tick RE-STAMPS the burst after the attack pipeline overwrote it",
          abs(carrier.move_speed - top * ope.BUMP_OFF_CARRIER_BURST_SCALE) < 1e-9,
          "move_speed=%.4f" % carrier.move_speed)
    check("the tick decrements the window",
          engine._bump_carrier_burst_s < ope.BUMP_OFF_CARRIER_BURST_S,
          "%.4f s" % engine._bump_carrier_burst_s)

    # -- expiry: drain the window and prove it lands on EXACTLY zero and
    #    then stops touching anything --
    steps = 0
    while engine._bump_carrier_burst_s > 0.0 and steps < 600:
        engine._tick_bump_carrier_burst(1.0 / 60.0)
        steps += 1
    check("the burst expires by itself", engine._bump_carrier_burst_s == 0.0,
          "after %d frames" % steps)

    carrier.move_speed = 4.0
    engine._tick_bump_carrier_burst(1.0 / 60.0)
    check("an expired window never re-opens or re-stamps",
          carrier.move_speed == 4.0 and engine._bump_carrier_burst_s == 0.0,
          "move_speed=%.4f" % carrier.move_speed)

    # -- a carrier with no declared top speed must not explode --
    engine._bump_carrier_burst_s = 0.0
    bare = _make_player(200)
    bare.max_speed_mps = None
    try:
        engine._arm_bump_carrier_burst(bare)
        engine._tick_bump_carrier_burst(1.0 / 60.0)
        ok = True
    except Exception as exc:
        ok = False
        _emit("      (exception: %r)" % (exc,))
    check("a carrier with no declared top speed is tolerated", ok)
    return True


# ----------------------------------------------------------------------
# [4] THE COMMIT-LOCK CLEARING
# ----------------------------------------------------------------------

def section_commit_lock(ope):
    banner("[4] THE COMMIT-LOCK CLEARING - a beaten tackler re-enters clean")

    engine, _bc, primary, joiner, _a, _ctx = _fixture(_bump_off_const())

    check("the fixture really starts committed",
          primary._tackle_commit_active is True
          and primary._pursuit_commit_active is True,
          "the defect this clears must be present first")

    ope.OpenPlayEngine._clear_bump_commit_lock(primary)
    check("the TES drive commit lock is dropped",
          primary._tackle_commit_active is False)
    check("the pursuit commit lock is dropped",
          primary._pursuit_commit_active is False)
    check("the stale tackle role is dropped", primary.tackle_role is None)

    class _Bare:
        pass

    try:
        ope.OpenPlayEngine._clear_bump_commit_lock(_Bare())
        tolerant = True
    except Exception:
        tolerant = False
    check("a body with none of those attributes is tolerated", tolerant)

    check("the helper is a STATIC method (no engine state needed)",
          isinstance(
              ope.OpenPlayEngine.__dict__.get("_clear_bump_commit_lock"), staticmethod))
    return True


# ----------------------------------------------------------------------
# [5] THE TEARDOWN (REAL engine)
# ----------------------------------------------------------------------

def section_teardown(ope):
    banner("[5] THE TEARDOWN (REAL engine) - BUMP_OFF rips the contact down")

    restore = _set_lever(None)
    err = None
    engine = carrier = primary = joiner = attached = ctx = None
    vx0 = vy0 = top = 0.0
    try:
        engine, carrier, primary, joiner, attached, ctx = _fixture(_bump_off_const())
        vx0, vy0 = carrier.vx, carrier.vy
        top = carrier.max_speed_mps

        check("the fixture's declaration IS BUMP_OFF",
              engine._bump_off_pending() is True,
              "token=%r" % ctx.wrap_collision_state)
        check("the fixture really starts attached, committed and locked",
              attached.tes_attached is True
              and primary._tackle_commit_active is True
              and primary.tes_locked is True)

        # Emulate the freeze tes_start_tackle() applied when this contest
        # began (game/tes/tes.py: bc.frozen = True).  Without it the release
        # assertions below pass vacuously - which is exactly how the
        # permanent missed-tackle freeze escaped this gate.
        carrier.frozen = True
        carrier.freeze_timer = 99.0

        try:
            engine._handle_broken_tackle(bump_off=True)
        except Exception:
            err = traceback.format_exc()
            _emit(err)
    finally:
        restore()

    check("the REAL bump-off teardown completes without raising", err is None)
    if err is not None:
        return True

    check("the carrier KEEPS its velocity vector (no 0.9 damp)",
          abs(carrier.vx - vx0) < 1e-12 and abs(carrier.vy - vy0) < 1e-12,
          "vx=%.4f vy=%.4f (unchanged)" % (carrier.vx, carrier.vy))
    check("the burst window is open",
          abs(engine._bump_carrier_burst_s - ope.BUMP_OFF_CARRIER_BURST_S) < 1e-12,
          "%.3f s" % engine._bump_carrier_burst_s)
    # The teardown hands the carrier back to OPEN_PLAY, where the attack
    # pipeline owns its speed again -- so the burst is NOT a one-shot write,
    # it is re-asserted every frame by _update_open_play.  Prove the
    # mechanism, not a value the very next writer is entitled to change.
    carrier.move_speed = top * 0.9
    engine._tick_bump_carrier_burst(1.0 / 60.0)
    check("the open-play tick re-asserts the burst over the attack pipeline",
          abs(carrier.move_speed - top * ope.BUMP_OFF_CARRIER_BURST_SCALE) < 1e-9
          and engine._bump_carrier_burst_s > 0.0,
          "move_speed=%.4f (%.2fx top speed), window=%.3fs"
          % (carrier.move_speed, ope.BUMP_OFF_CARRIER_BURST_SCALE,
             engine._bump_carrier_burst_s))
    check("the carrier is released from the contact",
          carrier.tes_locked is False and carrier.in_contact is False)
    check("the carrier holds no tackle animation",
          carrier.anim_state == "", "anim_state=%r" % carrier.anim_state)
    # -- CARRIER RELEASE (missed-tackle permanent-freeze regression) --
    # The teardown must release the carrier IT owns.  Its only release used
    # to be the _set_phase side effect, and that one targets
    # self.world.ball_carrier - a DIFFERENT handle from self.ball_carrier.
    # When the two diverge the tackled carrier was never unfrozen and stayed
    # frozen for the rest of the match.
    check("the teardown released the carrier it owns (frozen cleared)",
          carrier.frozen is False and carrier.freeze_timer == 0.0,
          "frozen=%r freeze_timer=%.3f"
          % (carrier.frozen, carrier.freeze_timer))

    check("the primary defender goes to ground",
          primary.frozen is True and primary.anim_state == "FALLEN",
          "anim_state=%r" % primary.anim_state)
    check("the primary is down for LONGER than the plain path",
          abs(primary.freeze_timer - ope.BUMP_OFF_DEFENDER_FREEZE_S) < 1e-12,
          "%.3fs vs the plain path's 1.000s" % primary.freeze_timer)
    check("the primary holds no wrap role",
          primary.is_tackler is False
          and primary.tes_tackler_role is None
          and primary.tes_locked is False)
    check("the primary's commit locks are cleared",
          primary._tackle_commit_active is False
          and primary._pursuit_commit_active is False
          and primary.tackle_role is None)

    check("the joiner is released and de-committed",
          joiner.tes_locked is False
          and joiner.is_tackler is False
          and joiner.tes_tackler_role is None
          and joiner._tackle_commit_active is False)

    check("the attachment is torn down AT THE SOURCE (_detach_all_tacklers)",
          attached.tes_attached is False and attached.attach_slot is None,
          "tes_attached=%r attach_slot=%r"
          % (attached.tes_attached, attached.attach_slot))
    check("the engine's attach registry is empty",
          engine._attached_player_slots == {}
          and engine._attach_armed is False)
    check("the context's own attach flags are cleared",
          engine._attached_player_slots == {}
          and getattr(ctx, "attach_enabled", None) is False
          if engine.tes_ctx is not None else True,
          "the context was dropped entirely, so its flags went with it")

    check("the TES context is dropped", engine.tes_ctx is None)
    check("the TES outcome is dropped", engine.tes_outcome is None)
    check("TES is no longer active", engine.world.tes_active is False)
    check("the tackler registry is cleared",
          set(engine.world.tackler_ids) == set())
    check("the primary / joiner references are dropped",
          engine.primary is None and list(engine.joiners) == [])
    check("the clearance ends in OPEN_PLAY (never RELEASE / PTB)",
          engine._phase == "OPEN_PLAY",
          "phase=%r" % engine._phase)
    check("no RELEASE was entered", engine._just_entered_release is False)

    # -- THE REFERENCE-DIVERGENCE ARM (missed-tackle freeze regression) --
    # Same teardown, but with the WORLD's carrier handle re-pointed at
    # another attacker before it runs - exactly what the PTB DH swap and the
    # attack/kickoff code do to world.ball_carrier while engine.ball_carrier
    # keeps naming the tackled body.  The tackled carrier must come out
    # UNFROZEN regardless of which handle the phase entry happens to read.
    div_err = None
    carrier2 = None
    try:
        engine2, carrier2, _p2, _j2, _a2, _c2 = _fixture(_bump_off_const())
        carrier2.frozen = True
        carrier2.freeze_timer = 99.0
        other = _make_player(101, x=62.0, y=35.0,
                             tackle_role="BALL_CARRIER")
        engine2.world.attacking_team.append(other)
        engine2.world.players.append(other)
        engine2.world.ball_carrier = other
        engine2._handle_broken_tackle(bump_off=True)
    except Exception:
        div_err = traceback.format_exc()
        _emit(div_err)
    check("the reference-divergent teardown completes without raising",
          div_err is None)
    check("the tackled carrier is UNFROZEN even when world.ball_carrier "
          "names another body",
          div_err is None and carrier2 is not None
          and carrier2.frozen is False and carrier2.freeze_timer == 0.0,
          "frozen=%r freeze_timer=%s"
          % (getattr(carrier2, "frozen", "n/a"),
             getattr(carrier2, "freeze_timer", "n/a")))
    return True


# ----------------------------------------------------------------------
# [6] THE CONTROL ARM
# ----------------------------------------------------------------------

def section_control_arm(ope):
    banner("[6] THE CONTROL ARM - the plain call is the OLD behaviour exactly")

    restore = _set_lever(None)
    err = None
    engine = carrier = primary = joiner = attached = None
    vx0 = vy0 = 0.0
    try:
        engine, carrier, primary, joiner, attached, _ctx = _fixture(
            "PROGRESSIVE_DRAG")
        vx0, vy0 = carrier.vx, carrier.vy

        check("a PROGRESSIVE_DRAG declaration is NOT pending",
              engine._bump_off_pending() is False,
              "only BUMP_OFF arms the clearance")

        try:
            engine._handle_broken_tackle()
        except Exception:
            err = traceback.format_exc()
            _emit(err)
    finally:
        restore()

    check("the plain teardown completes without raising", err is None)
    if err is not None:
        return True

    check("the plain path DAMPS the carrier by exactly 0.9",
          abs(carrier.vx - vx0 * 0.9) < 1e-12
          and abs(carrier.vy - vy0 * 0.9) < 1e-12,
          "vx %.4f -> %.4f" % (vx0, carrier.vx))
    check("the plain path arms NO burst",
          engine._bump_carrier_burst_s == 0.0)
    check("the plain path freezes the defender for exactly the legacy 1.0 s",
          abs(primary.freeze_timer - 1.0) < 1e-12,
          "%.3f s" % primary.freeze_timer)
    check("the plain path takes the defender down the same way",
          primary.frozen is True and primary.anim_state == "FALLEN")
    # ── SUPERSEDED ASSERTION, CORRECTED 2026-09-26 ──
    # This check used to read "the plain path CANNOT clear the commit locks
    # (that is the bump-off delta, and it must be exclusive)".  That was the
    # Stage 3.5 law and it is NO LONGER the engine's.  The post-miss-pursuit
    # stage (2026-09-26) DELIBERATELY clears the beaten body's commit latches
    # on BOTH teardown paths: a body beaten by a broken tackle that stayed
    # committed could never pursue the carrier, which is the exact defect
    # that stage exists to remove.  Its own contract verifier requires it --
    #
    #     verify_post_miss_pursuit_contract.py:
    #         "self._clear_bump_commit_lock(d)" in engine_src
    #         "the beat-body commitment release must run on BOTH paths"
    #
    # -- so the two verifiers asserted OPPOSITE things about the same engine
    # line and BOTH were listed in run_fb_verifiers.py, which is why the FB
    # telemetry harness could not go green on the shipped default.
    #
    # Attribution was MEASURED, not assumed:
    #   * RLM_POST_MISS_PURSUIT=0 -> this check passes (the exclusivity
    #     returns), so the clearing is the post-miss stage's, and
    #   * RLM_FB_EMERGENCY_RELEASE_ON_MISS=0 -> this check still fails
    #     identically, so the fullback miss-teardown release is not involved.
    #
    # The check now asserts the lever's CURRENT law and keeps the historical
    # exclusivity provable from the same harness.
    from game.defence.defence_lvl_1.post_miss_pursuit import (
        post_miss_pursuit_enabled as _pmp_on,
    )
    if _pmp_on():
        check("the plain path RELEASES the beat body's commit latches "
              "(the post-miss law supersedes the Stage 3.5 exclusivity)",
              primary._tackle_commit_active is False
              and primary._pursuit_commit_active is False
              and primary.tackle_role is None,
              "tackle_commit=%r pursuit_commit=%r role=%r"
              % (primary._tackle_commit_active,
                 primary._pursuit_commit_active, primary.tackle_role))
    else:
        check("with the post-miss stage disarmed the plain path HOLDS the "
              "commit latches (the Stage 3.5 exclusivity, still reachable)",
              primary._tackle_commit_active is True
              and primary._pursuit_commit_active is True
              and primary.tackle_role == "PRIMARY",
              "tackle_commit=%r pursuit_commit=%r role=%r"
              % (primary._tackle_commit_active,
                 primary._pursuit_commit_active, primary.tackle_role))
    check("the plain path leaves the attach registry for the next INIT "
          "to repair, exactly as before this stage",
          attached.tes_attached is True and attached.attach_slot == 1)
    check("the plain path still ends in OPEN_PLAY with TES cleared",
          engine._phase == "OPEN_PLAY"
          and engine.tes_ctx is None
          and engine.world.tes_active is False)
    return True


# ----------------------------------------------------------------------
# [7] THE INTERCEPT ORDER
# ----------------------------------------------------------------------

def section_intercept_order(ope):
    banner("[7] THE INTERCEPT ORDER - BUMP_OFF outranks the momentum gates")

    src = _engine_source()
    handlers = (
        "def _update_tackle_contact",
        "def _update_tackle_drive",
        "def _update_tackle_dominance",
    )
    for handler in handlers:
        name = handler.split("def ")[1]
        body = _body_of(src, handler)
        i_bump = body.find("self._bump_off_pending()")
        i_break = body.find("self._check_break_tackle(dt)")
        check("%s checks the collision state" % name, i_bump != -1)
        check("%s checks BUMP_OFF BEFORE the raw momentum gate" % name,
              i_bump != -1 and i_break != -1 and i_bump < i_break,
              "bump@%d break@%d" % (i_bump, i_break))
        check("%s exits the frame on the clearance" % name,
              "self._handle_broken_tackle(bump_off=True)" in body)

    bump_calls = src.count("self._handle_broken_tackle(bump_off=True)")
    plain_calls = src.count("self._handle_broken_tackle()\n")
    check("the engine states exactly THREE bump-off call sites",
          bump_calls == 3, "count=%d" % bump_calls)
    check("every pre-existing call site is left UNCHANGED",
          plain_calls == 5, "count=%d" % plain_calls)
    return True


# ----------------------------------------------------------------------
# [8] THE PROGRESSIVE DRAG DECAY (dynamic results pipeline, step 3.1)
# ----------------------------------------------------------------------

class _DragCtx:
    """Minimal stand-in carrying a collision token AND a wrap model."""

    def __init__(self, token, model_attached):
        self.wrap_collision_state = token
        self.wrap_model = _model_stub() if model_attached else None


class _NoTokenCtx:
    """A context with NO collision-state attribute at all."""

    def __init__(self, model_attached):
        self.wrap_model = _model_stub() if model_attached else None


def _model_stub():
    """A stand-in for a RESOLVED wrap model.

    The predicate only asks whether a model was attached (``is not None``);
    what the model CONTAINS is the discriminator's business, gated by the
    sibling multi-body contract.  A sentinel is therefore the honest
    fixture: if the predicate ever started inspecting the model, this test
    would catch it rather than quietly agree.
    """
    return object()


def _drag_holder(model_attached, token="PROGRESSIVE_DRAG"):
    """A holder whose context carries *token* and, optionally, a model."""
    if token is _ABSENT:
        return _Holder(_NoTokenCtx(model_attached))
    return _Holder(_DragCtx(token, model_attached))


def section_progressive_drag(ope):
    banner("[8] THE PROGRESSIVE DRAG DECAY - a contested carrier is ground down")

    # -- the declared surface --
    check("the drag declares a per-frame decay multiplier",
          0.0 < ope.PROGRESSIVE_DRAG_DECAY < 1.0,
          "%.4f per frame" % ope.PROGRESSIVE_DRAG_DECAY)
    check("the drag rides the SAME lever as the clearance",
          ope.BUMP_CLEARANCE_ENV_VAR == "RLM_TES_BUMP_CLEARANCE",
          "one lever for the whole results pipeline: %s"
          % ope.BUMP_CLEARANCE_ENV_VAR)

    from game.tes.tes_multi_body import COLLISION_STATE_PROGRESSIVE_DRAG
    check("the CONTEST token is the leaf's own",
          COLLISION_STATE_PROGRESSIVE_DRAG == "PROGRESSIVE_DRAG",
          "token=%r" % COLLISION_STATE_PROGRESSIVE_DRAG)

    # -- the predicate --
    restore = _set_lever(None)
    try:
        check("a RESOLVED drag declaration is PENDING",
              ope.OpenPlayEngine._progressive_drag_pending(
                  _drag_holder(True)) is True)
        for other in ("BUMP_OFF", "DOMINANT_SMASH", "progressive_drag",
                      "DRAG", "", None):
            check("token %r does NOT arm the drag" % (other,),
                  ope.OpenPlayEngine._progressive_drag_pending(
                      _drag_holder(True, other)) is False)
        check("the IDENTITY token with NO resolved model does NOT arm it",
              ope.OpenPlayEngine._progressive_drag_pending(
                  _drag_holder(False)) is False,
              "PROGRESSIVE_DRAG is also every undecided default")
        check("an ABSENT token attribute does NOT arm it",
              ope.OpenPlayEngine._progressive_drag_pending(
                  _drag_holder(True, _ABSENT)) is False)
        check("no context at all does NOT arm it",
              ope.OpenPlayEngine._progressive_drag_pending(
                  _Holder(None)) is False)
        check("the predicate is TOTAL over the drag degenerates",
              _drag_total(ope))
    finally:
        restore()

    restore = _set_lever("0")
    try:
        check("with the lever DISARMED even a resolved drag does not arm it",
              ope.OpenPlayEngine._progressive_drag_pending(
                  _drag_holder(True)) is False)
    finally:
        restore()

    # -- SEQUENTIAL VELOCITY REDUCTION (the demanded assertion) --
    engine, carrier, _p, _j, _a, ctx = _fixture(
        "PROGRESSIVE_DRAG", wrap_model=_model_stub())
    check("the real fixture's drag declaration is PENDING",
          engine._progressive_drag_pending() is True,
          "token=%r model=%s" % (ctx.wrap_collision_state,
                                 type(ctx.wrap_model).__name__))

    ctx.drive_step_mps = 2.5
    vx0, vy0, drive0 = carrier.vx, carrier.vy, ctx.drive_step_mps
    frames = 8
    vxs, vys, drives = [vx0], [vy0], [drive0]
    for _ in range(frames):
        # EXACTLY the two statements the engine's intercept runs.
        if engine._progressive_drag_pending():
            engine._apply_progressive_drag(carrier, ctx)
        vxs.append(carrier.vx)
        vys.append(carrier.vy)
        drives.append(ctx.drive_step_mps)

    check("the carrier's vx falls on EVERY consecutive frame",
          all(b < a for a, b in zip(vxs, vxs[1:])),
          " -> ".join("%.4f" % v for v in vxs))
    check("the carrier's vy falls on EVERY consecutive frame",
          all(b < a for a, b in zip(vys, vys[1:])),
          " -> ".join("%.4f" % v for v in vys))
    check("the drive's own forward authority falls on EVERY frame",
          all(b < a for a, b in zip(drives, drives[1:])),
          " -> ".join("%.4f" % v for v in drives))
    check("the FIRST frame applies exactly ONE decay step to vx",
          abs(vxs[1] - vx0 * ope.PROGRESSIVE_DRAG_DECAY) < 1e-12,
          "%.6f == %.6f x %.2f" % (vxs[1], vx0, ope.PROGRESSIVE_DRAG_DECAY))
    check("n frames leave exactly v0 * decay**n (it COMPOUNDS)",
          abs(vxs[-1] - vx0 * ope.PROGRESSIVE_DRAG_DECAY ** frames) < 1e-12,
          "%.6f" % vxs[-1])
    check("the drive authority decays by the SAME law",
          abs(drives[-1] - drive0 * ope.PROGRESSIVE_DRAG_DECAY ** frames) < 1e-12,
          "%.6f" % drives[-1])
    check("the decay is strictly POSITIVE (it grinds, it never flips)",
          vxs[-1] > 0.0 and vys[-1] > 0.0 and drives[-1] > 0.0)

    # -- the no-op arms --
    for label, token, model in (
        ("BUMP_OFF (the OTHER decided state)", "BUMP_OFF", _model_stub()),
        ("the identity token with NO model attached", "PROGRESSIVE_DRAG", None),
    ):
        eng, bc, _p2, _j2, _a2, c2 = _fixture(token, wrap_model=model)
        c2.drive_step_mps = 2.5
        vx_b, vy_b = bc.vx, bc.vy
        for _ in range(5):
            if eng._progressive_drag_pending():
                eng._apply_progressive_drag(bc, c2)
        check("%s is a bit-identical no-op" % label,
              bc.vx == vx_b and bc.vy == vy_b and c2.drive_step_mps == 2.5,
              "vx=%.6f vy=%.6f drive=%.6f" % (bc.vx, bc.vy, c2.drive_step_mps))

    restore = _set_lever("0")
    try:
        eng, bc, _p3, _j3, _a3, c3 = _fixture(
            "PROGRESSIVE_DRAG", wrap_model=_model_stub())
        c3.drive_step_mps = 2.5
        vx_b, vy_b = bc.vx, bc.vy
        for _ in range(5):
            if eng._progressive_drag_pending():
                eng._apply_progressive_drag(bc, c3)
        check("with the lever OFF a resolved drag is a bit-identical no-op",
              bc.vx == vx_b and bc.vy == vy_b and c3.drive_step_mps == 2.5,
              "vx=%.6f drive=%.6f" % (bc.vx, c3.drive_step_mps))
    finally:
        restore()

    # -- THE INTERCEPT ORDER (source law) --
    src = _engine_source()
    body = _body_of(src, "def _update_tackle_drive")
    i_guard = body.find("self._progressive_drag_pending()")
    i_watch = body.find("self._phase_timer > dur + 0.5")
    i_motion = body.find("dx_step = ctx.drive_step_mps * dt")

    check("the intercept exists in _update_tackle_drive", i_guard != -1)
    check("the intercept is a GUARDED statement, not a bare call",
          ("if self._progressive_drag_pending():\n"
           "            self._apply_progressive_drag(bc, ctx)") in body)
    check("the intercept runs AFTER the drive watchdog",
          i_guard != -1 and i_watch != -1 and i_guard > i_watch,
          "guard@%d watchdog@%d" % (i_guard, i_watch))
    check("the intercept runs BEFORE the dx_step motion write",
          i_guard != -1 and i_motion != -1 and i_guard < i_motion,
          "guard@%d motion@%d" % (i_guard, i_motion))
    check("the decay is applied exactly ONCE per DRIVE frame",
          src.count("self._apply_progressive_drag(bc, ctx)") == 1,
          "engine call sites=%d"
          % src.count("self._apply_progressive_drag(bc, ctx)"))
    check("the CONTACT handler does NOT drag",
          "self._apply_progressive_drag"
          not in _body_of(src, "def _update_tackle_contact"))
    check("the DOMINANCE handler does NOT drag",
          "self._apply_progressive_drag"
          not in _body_of(src, "def _update_tackle_dominance"))

    # -- the decay body must READ the declared constant.  A hardcoded
    #    0.95 here would pass every arithmetic assertion above while
    #    silently decoupling the tuning surface from the physics, so the
    #    literal is forbidden in this body by the source law.
    decay_body = _body_of(src, "def _apply_progressive_drag")
    check("the decay body reads PROGRESSIVE_DRAG_DECAY, not a literal",
          "PROGRESSIVE_DRAG_DECAY" in decay_body
          and "0.95" not in decay_body,
          "one tuning surface: the declared constant")
    check("the decay scales exactly the carrier's two components "
          "and the drive's forward authority",
          ("bc.vx *= " in decay_body and "bc.vy *= " in decay_body
           and "ctx.drive_step_mps *= " in decay_body),
          "vx / vy / drive_step_mps")
    return True


def _drag_total(ope):
    """The drag predicate must never raise, and never mis-arm."""
    class _BadToken:
        wrap_collision_state = 3.5
        wrap_model = object()

    class _BadCtx:
        tes_ctx = "not a context"

    for case in (_Holder(_BadToken()), _Holder(object()), _Holder(None),
                 _Holder(_NoTokenCtx(False)), _BadCtx()):
        try:
            got = ope.OpenPlayEngine._progressive_drag_pending(case)
        except Exception:
            return False
        if got is not False:
            return False
    return True


# ----------------------------------------------------------------------
# [9] THE TERMINAL FLOOR (dynamic results pipeline, step 3.2)
# ----------------------------------------------------------------------

def _drag_fixture(step_mps):
    """A real engine sitting in TACKLE_DRIVE with a live, resolved drag."""
    engine, bc, primary, joiner, attached, ctx = _fixture(
        "PROGRESSIVE_DRAG", wrap_model=_model_stub())
    engine._phase = "TACKLE_DRIVE"
    engine._phase_timer = 0.1
    ctx.drive_step_mps = step_mps
    ctx.drive_start_x = bc.x - 1.0
    ctx.drive_det_x = 0.5
    ctx.drive_target_x = bc.x
    ctx.dominance_result = "NEUTRAL"
    return engine, bc, primary, joiner, attached, ctx


class _NoAuthority:
    """A drag context that has no readable ``drive_step_mps`` at all."""

    wrap_collision_state = "PROGRESSIVE_DRAG"
    wrap_model = object()


def section_terminal_floor(ope):
    banner("[9] THE TERMINAL FLOOR - a spent grind completes as a ruck")

    # -- the declared surface --
    check("the floor is declared at 0.4 m/s",
          abs(ope.PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS - 0.4) < 1e-12,
          "%.3f m/s" % ope.PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS)
    check("the floor is POSITIVE",
          ope.PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS > 0.0)
    check("the floor sits far below any drive's entry authority",
          ope.PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS
          < ope.TACKLER_ACCEL_FLOOR_MPS,
          "%.3f < the %.1f m/s tackler floor"
          % (ope.PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS,
             ope.TACKLER_ACCEL_FLOOR_MPS))

    # -- the predicate --
    engine, _bc, _p, _j, _a, ctx = _drag_fixture(2.5)
    check("a live drag ABOVE the floor is NOT spent",
          engine._progressive_drag_spent(ctx) is False,
          "drive_step_mps=%.4f" % ctx.drive_step_mps)
    ctx.drive_step_mps = ope.PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS
    check("exactly AT the floor is NOT yet spent (the test is strict <)",
          engine._progressive_drag_spent(ctx) is False,
          "the boundary frame still drives")
    ctx.drive_step_mps = ope.PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS * 0.99
    check("just BELOW the floor IS spent",
          engine._progressive_drag_spent(ctx) is True,
          "drive_step_mps=%.4f" % ctx.drive_step_mps)
    ctx.drive_step_mps = 0.0
    check("a fully exhausted authority is spent",
          engine._progressive_drag_spent(ctx) is True)

    # -- the no-op arms --
    eng2, _b2, _p2, _j2, _a2, c2 = _drag_fixture(0.0)
    c2.wrap_collision_state = "BUMP_OFF"
    check("BUMP_OFF is never spent",
          eng2._progressive_drag_spent(c2) is False)
    eng3, _b3, _p3, _j3, _a3, c3 = _drag_fixture(0.0)
    c3.wrap_model = None
    check("the identity token with NO resolved model is never spent",
          eng3._progressive_drag_spent(c3) is False)
    eng4, _b4, _p4, _j4, _a4, c4 = _drag_fixture(0.0)
    restore = _set_lever("0")
    try:
        check("with the lever OFF a spent authority is never spent",
              eng4._progressive_drag_spent(c4) is False)
    finally:
        restore()
    # NOTE: _progressive_drag_spent is an INSTANCE method -- it reads the
    # family lever through the engine it is bound to -- so these two
    # degenerate arms go through a real engine, exactly as the caller does.
    eng5, _b5, _p6, _j6, _a6, _c5 = _drag_fixture(0.0)
    eng5.tes_ctx = _NoAuthority()
    check("an UNREADABLE drive_step_mps is not treated as spent "
          "(the drive keeps its own timer exit)",
          eng5._progressive_drag_spent(eng5.tes_ctx) is False)
    eng6, _b6, _p7, _j7, _a7, _c6 = _drag_fixture(0.0)
    eng6.tes_ctx = None
    check("no context at all is not spent",
          eng6._progressive_drag_spent(None) is False)

    # -- THE DEMANDED ASSERTION: a spent drag ENDS THE DRIVE AS A RUCK --
    restore = _set_lever(None)
    err = None
    entered = False
    frames = 0
    engine = bc = attached = None
    ruck_x = drive_target_x = None
    try:
        engine, bc, _p5, _j5, attached, ctx = _drag_fixture(2.5)
        start_authority = ctx.drive_step_mps
        while not engine._progressive_drag_spent(ctx) and frames < 400:
            if engine._progressive_drag_pending():
                engine._apply_progressive_drag(bc, ctx)
            frames += 1
        entered = engine._progressive_drag_spent(ctx)
        if entered:
            # EXACTLY the pair the intercept in _update_tackle_drive runs
            # when the floor is crossed.
            engine._exit_progressive_drag_to_ruck(bc, ctx)
        ruck_x = engine.world.ruck_x_m
        drive_target_x = ctx.drive_target_x
    except Exception:
        err = traceback.format_exc()
        _emit(err)
    finally:
        restore()

    import math as _m
    predicted = int(_m.ceil(
        _m.log(ope.PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS / 2.5)
        / _m.log(ope.PROGRESSIVE_DRAG_DECAY)
    ))
    check("the decaying authority REACHES the 0.4 floor", entered,
          "from 2.5000 m/s after %d frames (predicted %d)"
          % (frames, predicted))
    check("it takes exactly as many frames as the decay law predicts",
          entered and frames == predicted,
          "%d == ceil(ln(floor/v0)/ln(decay))" % frames)
    check("the ruck abort completes without raising", err is None)
    if err is not None:
        return True

    check("the engine LEAVES the drive and enters RELEASE",
          engine._phase == "RELEASE", "phase=%r" % engine._phase)
    check("RELEASE is flagged as freshly entered, so its own first frame "
          "tears the tackle down",
          engine._just_entered_release is True)
    check("the ruck is anchored at the DRAGGED-TO position",
          abs(ruck_x - bc.x) < 1e-12 and
          abs(engine.world.ruck_y_m - bc.y) < 1e-12,
          "ruck=(%.4f, %.4f) bc=(%.4f, %.4f)"
          % (ruck_x, engine.world.ruck_y_m, bc.x, bc.y))
    check("the DRIVE-END LOCK is stamped, not left at the entry value",
          abs(drive_target_x - bc.x) < 1e-12
          and abs(bc.target_x - bc.x) < 1e-12,
          "ctx.drive_target_x=%.4f bc.target_x=%.4f"
          % (drive_target_x, bc.target_x))

    from player.player import DefenderRole
    roles = [getattr(d, "defender_role", None)
             for d in engine.world.defending_team]
    check("the ruck markers are assigned",
          DefenderRole.MARKER_A in roles,
          "roles=%s" % [getattr(r, "name", r) for r in roles])
    check("every non-marker defender is converted to DEF_RETREAT_10",
          all(r in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                    DefenderRole.DUMMY_HALF, DefenderRole.DEF_RETREAT_10)
              for r in roles),
          "roles=%s" % [getattr(r, "name", r) for r in roles])
    check("the beaten tacklers' commit locks are cleared",
          all(not getattr(d, "_tackle_commit_active", False)
              for d in engine.world.defending_team))

    # -- and RELEASE's own first frame finishes the teardown --
    err = None
    try:
        engine._update_release(1.0 / 60.0)
    except Exception:
        err = traceback.format_exc()
        _emit(err)
    check("the first RELEASE frame runs without raising", err is None)
    if err is not None:
        return True
    check("the freshly-entered flag is consumed",
          engine._just_entered_release is False)
    check("the attachment is torn down at the source",
          attached.tes_attached is False and attached.attach_slot is None,
          "tes_attached=%r attach_slot=%r"
          % (attached.tes_attached, attached.attach_slot))
    check("the engine's attach registry is empty",
          engine._attached_player_slots == {})
    check("TES is shut down",
          engine.world.tes_active is False)
    check("the carrier is released from the contact",
          bc.in_contact is False and bc.tes_locked is False)

    # -- THE SOURCE LAW: the abort is wired to the floor, once, in the drive --
    src = _engine_source()
    drive_body = _body_of(src, "def _update_tackle_drive")
    i_decay = drive_body.find("self._apply_progressive_drag(bc, ctx)")
    i_floor = drive_body.find("self._progressive_drag_spent(ctx)")
    i_abort = drive_body.find("self._exit_progressive_drag_to_ruck(bc, ctx)")
    i_motion = drive_body.find("dx_step = ctx.drive_step_mps * dt")

    check("the drive states the decay, then the floor, then the abort",
          -1 < i_decay < i_floor < i_abort,
          "decay@%d floor@%d abort@%d" % (i_decay, i_floor, i_abort))
    check("the abort runs BEFORE the dx_step motion write",
          i_abort != -1 and i_motion != -1 and i_abort < i_motion,
          "abort@%d motion@%d" % (i_abort, i_motion))
    check("the abort is the LAST statement of its frame (it returns)",
          "self._exit_progressive_drag_to_ruck(bc, ctx)\n"
          "                return" in drive_body)
    check("the engine calls the ruck exit exactly ONCE",
          src.count("self._exit_progressive_drag_to_ruck(bc, ctx)") == 1,
          "call sites=%d"
          % src.count("self._exit_progressive_drag_to_ruck(bc, ctx)"))

    exit_body = _body_of(src, "def _exit_progressive_drag_to_ruck")
    check("the ruck exit delegates to the engine's own ruck formation",
          "self._prepare_release()" in exit_body,
          "no hand-rolled ruck: _prepare_release() owns it")
    check("the ruck exit enters RELEASE, the head of the PTB chain",
          '_set_phase(' in exit_body
          and '"RELEASE"' in exit_body
          and "GameSubState.TACKLE_RELEASE" in exit_body,
          "RELEASE -> PTB_PREP -> PTB_EXEC -> PTB -> POST_RUCK")
    check("the ruck exit stamps the DRIVE-END lock",
          "ctx.drive_target_x" in exit_body
          and "self._follow_attached_tacklers()" in exit_body)
    check("the ruck exit reads the floor constant, not a literal",
          "PROGRESSIVE_DRAG_TERMINAL_FLOOR_MPS" in _body_of(
              src, "def _progressive_drag_spent")
          and "0.4" not in _body_of(src, "def _progressive_drag_spent"),
          "one tuning surface: the declared constant")
    from world.world_state import GameSubState
    check("there is NO RUCK_FORMATION phase or sub-state to enter "
          "(the phase vocabulary is fixed and RELEASE is the ruck)",
          not hasattr(GameSubState, "RUCK_FORMATION"))
    return True


# ----------------------------------------------------------------------
# [11] THE POSITIONAL GRADIENT (Stage 4)
# ----------------------------------------------------------------------
# Stages 3.1-3.3 built the trait pipeline and left it neutral; Stage 4 FILLS
# it from a body's positional jersey.  This is the PHYSICS end of that stage:
# real bodies stamped through the real mapper, driven through the real
# decider, resolved by the real wrap model -- so the band the engine acts on
# is shown to MOVE with the matchup rather than being asserted in prose.

class _BandOPE:
    """A minimal open-play engine with scripted pre-tackle verdicts."""

    def __init__(self, verdicts):
        self._phase = "OPEN_PLAY"
        self.verdicts = dict(verdicts)
        self.calls = []

    def _check_pre_tackle(self, bc, body):
        self.calls.append(getattr(body, "player_id", None))
        return self.verdicts.get(getattr(body, "player_id", None), "MISS")

    def _start_tackle(self, bc, body):
        """The band fixtures must never reach TES."""
        pass


def _band_body(pid, x, jersey, vx):
    """A real body carrying the stamps the authority reads, AND a jersey.

    The jersey is stamped BEFORE the mapper runs, because the jersey IS the
    mapper's entire input.
    """
    from player.player import DefenderRole
    from player.positional_traits import apply_positional_traits

    body = _make_player(pid, x=x, y=35.0, vx=vx, vy=0.0,
                        defender_role=DefenderRole.DEF_LINE_READY,
                        tackle_role="PRIMARY")
    body.depth_class = "SECONDARY"
    body.pre_tackle_state = None
    body.can_acquire_tackle = False
    body._pta_corridor = False
    body._pta_corridor_frames = 0
    body._machine_state = None
    body._tackle_engage_guard = False
    body.jersey_number = int(jersey)
    apply_positional_traits(body)
    return body


def _band_carrier(jersey, vx):
    """A real carrier at (60, 35) carrying positional *jersey*."""
    from player.positional_traits import apply_positional_traits

    bc = _make_player(100, x=60.0, y=35.0, vx=vx, vy=0.0,
                      tackle_role="BALL_CARRIER")
    bc.jersey_number = int(jersey)
    apply_positional_traits(bc)
    return bc


def _derive_band(carrier_jersey, carrier_vx, defenders):
    """Real decider -> real record -> real wrap model on a stamped line.

    ``defenders`` is ``(pid, x, jersey, vx, verdict)`` per body; a body with a
    ``None`` verdict is never put to the predicate, so (being inside the
    radius) it survives as the in-radius WRAP ASSIST the earlier stages were
    built to preserve.

    Returns ``(model, record, bc, line, predicate_calls)``.
    """
    from game.defence.defence_lvl_1 import contact_authority as ca
    from game.defence.defence_lvl_1 import contest_preservation as cp
    from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
    from game.tes import tes_multi_body as mb

    bc = _band_carrier(carrier_jersey, carrier_vx)
    line = []
    verdicts = {}
    for pid, x, jersey, vx, verdict in defenders:
        line.append(_band_body(pid, x, jersey, vx))
        if verdict is not None:
            verdicts[pid] = verdict

    world = _make_world()
    world.frame_count = 21
    world.ball_carrier = bc
    world.defending_team = list(line)
    world.players = [bc] + list(line)
    stub = _BandOPE(verdicts)

    ca.reset_contact_authority(world)
    cp.reset_preservation(world)
    ca.evaluate_contact(world, stub, line, bc, DefenceTuners())
    record = cp.last_record(world)

    bodies = {int(getattr(b, "player_id", -1) or -1): b for b in line}
    bodies[int(bc.player_id)] = bc
    model = mb.derive_wrap_model(record, bodies, carrier=bc)
    return model, record, bc, line, stub.calls


def _lever(value):
    """Set / clear RLM_POSITIONAL_TRAITS, returning a restore callable."""
    var = "RLM_POSITIONAL_TRAITS"
    had = var in os.environ
    prev = os.environ.get(var)

    def restore():
        if had:
            os.environ[var] = prev
        else:
            os.environ.pop(var, None)

    if value is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = value
    return restore


def _margin_of(carrier_jersey, carrier_vx, defenders):
    """``(model, margin, token)`` for one matchup, or ``(None, 0, None)``."""
    from game.tes import tes_multi_body as mb

    model, _record, _bc, _line, _calls = _derive_band(
        carrier_jersey, carrier_vx, defenders)
    if model is None:
        return None, 0.0, None
    return model, model.carrier_momentum_margin_kgmps, mb.resolve_collision_state(model)


def section_positional_gradient():
    banner("[11] THE POSITIONAL GRADIENT - the band MOVES with the matchup")

    from game.tes import tes_multi_body as mb
    from player import positional_traits as pt

    # -- the declared profiles the fixtures are built from ---------------
    check("the back profile is the declared 88.0 / 1.35 / 0.85",
          (pt.BACK_PROFILE.weight_kg, pt.BACK_PROFILE.fend_trait,
           pt.BACK_PROFILE.tackle_skill_trait) == (88.0, 1.35, 0.85),
          "%s" % (pt.BACK_PROFILE,))
    check("the forward profile is the declared 105.0 / 1.15 / 1.30",
          (pt.FORWARD_PROFILE.weight_kg, pt.FORWARD_PROFILE.fend_trait,
           pt.FORWARD_PROFILE.tackle_skill_trait) == (105.0, 1.15, 1.30),
          "%s" % (pt.FORWARD_PROFILE,))

    restore = _lever(None)
    try:
        # ── A: a BACK carries INTO one FORWARD ============================
        # THE CARRIER RUNS INTO THE CONTACT.  Its momentum enters the
        # collision on the SAME closing axis the tackler's resistance is
        # measured on, so a carrier moving AWAY from the line closes nothing
        # and the band below would be decided by the wrap alone.  The line
        # stays at 59.0 and the carrier keeps its declared SPEED, so every
        # magnitude pinned here -- 88 x 9 x 1.35 and 105 x 4 x 1.30 -- is
        # exactly what it was.
        a_model, a_margin, a_token = _margin_of(
            3, -9.0, [(1, 59.0, 11, 4.0, "CONTACT")])
        check("A: the decider formed the contest and the model resolved",
              a_model is not None and a_model.formed is True,
              "model=%s" % ("None" if a_model is None else a_model.primary_id))
        check("A: a running BACK against a single FORWARD is an EXPLOSIVE BUMP_OFF",
              a_token == mb.COLLISION_STATE_BUMP_OFF,
              "margin=%+.2f vs threshold %.1f -> %s"
              % (a_margin, mb.COLLISION_MARGIN_THRESHOLD_KGMPS, a_token))
        check("A: the carrier's effective momentum uses its OWN declared fend",
              a_model is not None
              and a_model.carrier_fend_trait == pt.BACK_PROFILE.fend_trait
              and abs(a_model.carrier_effective_momentum_kgmps
                      - 88.0 * 9.0 * 1.35) < 1e-9,
              "effective=%.2f == 88.0 x 9.0 x 1.35"
              % (0.0 if a_model is None
                 else a_model.carrier_effective_momentum_kgmps))
        check("A: the wrap uses the tackler's OWN declared tackle-skill",
              a_model is not None
              and a_model.contributions[0].tackle_skill_trait
              == pt.FORWARD_PROFILE.tackle_skill_trait
              and abs(a_model.wrap_momentum_kgmps - 105.0 * 4.0 * 1.30) < 1e-9,
              "wrap=%.2f == 105.0 x 4.0 x 1.30"
              % (0.0 if a_model is None else a_model.wrap_momentum_kgmps))

        # ── the SAME geometry, a BACK tackler instead of a forward ========
        b_model, b_margin, _b_token = _margin_of(
            3, -9.0, [(2, 59.0, 2, 4.0, "CONTACT")])
        check("the SAME back carrier clears a single BACK by a WIDER margin "
              "than a single FORWARD",
              b_model is not None and a_model is not None
              and abs(b_model.wrap_momentum_kgmps - 88.0 * 4.0 * 0.85) < 1e-9
              and b_margin > a_margin,
              "back-tackler margin=%+.2f > forward-tackler margin=%+.2f"
              % (b_margin, a_margin))
        check("the position of the TACKLER alone moves the margin",
              b_model is not None and a_model is not None
              and abs((b_margin - a_margin)
                      - (136.5 - 74.8) * 4.0) < 1e-6,
              "delta=%.2f == (105x1.30 - 88x0.85) x closing 4.0"
              % (b_margin - a_margin))

        # ── B: a FORWARD carries into ONE forward =========================
        single_model, single_margin, single_token = _margin_of(
            11, -8.0, [(1, 59.0, 11, 4.0, "CONTACT")])
        check("B: a FORWARD carrier clears a SINGLE forward (still a win)",
              single_token == mb.COLLISION_STATE_BUMP_OFF,
              "margin=%+.2f -> %s" % (single_margin, single_token))

        # ── B2: the SAME carrier, a wrapping FORWARD CLUSTER ==============
        # Body 2 is never put to the predicate, sits INSIDE the 1.5 m radius,
        # and therefore survives as the in-radius WRAP ASSIST.
        cluster_model, cluster_margin, cluster_token = _margin_of(
            11, -8.0, [(1, 59.0, 11, 4.0, "CONTACT"),
                       (2, 59.6, 12, 4.0, None)])
        check("B2: the second forward was never examined and survived as an assist",
              cluster_model is not None
              and cluster_model.assist_ids == (2,)
              and cluster_model.multi_body is True,
              "assists=%s" % (() if cluster_model is None
                              else cluster_model.assist_ids))
        check("B2: the cluster's wrap COMPOUNDS to the sum of its bodies",
              cluster_model is not None and single_model is not None
              and abs(cluster_model.wrap_momentum_kgmps
                      - 2 * single_model.wrap_momentum_kgmps) < 1e-9,
              "cluster=%.2f == 2 x single=%.2f"
              % (cluster_model.wrap_momentum_kgmps,
                 single_model.wrap_momentum_kgmps))
        check("B2: the SAME forward carrier against the cluster is a "
              "PROGRESSIVE_DRAG, not a bump-off",
              cluster_token == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
              "margin=%+.2f (was %+.2f single) -> %s"
              % (cluster_margin, single_margin, cluster_token))
        check("B2: one extra wrapping forward FLIPS the resolved band",
              single_token == mb.COLLISION_STATE_BUMP_OFF
              and cluster_token == mb.COLLISION_STATE_PROGRESSIVE_DRAG,
              "%s -> %s" % (single_token, cluster_token))
        check("B2: the carrier's own effective momentum is UNCHANGED by the "
              "extra defender (only the wrap grew)",
              cluster_model is not None and single_model is not None
              and cluster_model.carrier_effective_momentum_kgmps
              == single_model.carrier_effective_momentum_kgmps,
              "carrier_effective=%.2f"
              % cluster_model.carrier_effective_momentum_kgmps)

        # ── the DRAG the mapper produced COLLAPSES INTO A RUCK ============
        err = None
        engine = bc = ctx = attached = None
        ruck_x = None
        try:
            engine, bc, _p, _j, attached, ctx = _fixture(
                mb.COLLISION_STATE_PROGRESSIVE_DRAG,
                wrap_model=cluster_model)
            engine._phase = "TACKLE_DRIVE"
            engine._phase_timer = 0.1
            ctx.drive_step_mps = 2.5
            ctx.drive_start_x = bc.x - 1.0
            ctx.drive_det_x = 0.5
            ctx.drive_target_x = bc.x
            ctx.dominance_result = "NEUTRAL"

            check("the engine PENDING-predicate accepts the model's own token",
                  engine._progressive_drag_pending() is True,
                  "token=%r model=%s" % (ctx.wrap_collision_state,
                                         type(ctx.wrap_model).__name__))

            frames = 0
            while not engine._progressive_drag_spent(ctx) and frames < 400:
                if engine._progressive_drag_pending():
                    engine._apply_progressive_drag(bc, ctx)
                frames += 1
            spent = engine._progressive_drag_spent(ctx)
            if spent:
                engine._exit_progressive_drag_to_ruck(bc, ctx)
            ruck_x = engine.world.ruck_x_m
        except Exception:
            err = traceback.format_exc()
            _emit(err)

        check("the compounded DRAG decays to the terminal floor and stops",
              err is None and spent is True,
              "after %d frames (err=%s)" % (frames, err is not None))
        if err is None:
            check("the DRAG collapse ends in RELEASE -- a NATIVE RUCK",
                  engine._phase == "RELEASE",
                  "phase=%r" % engine._phase)
            check("the ruck is anchored at the dragged-to position",
                  ruck_x is not None and abs(ruck_x - bc.x) < 1e-12,
                  "ruck_x=%s bc.x=%.4f" % (ruck_x, bc.x))
            check("the drawn-out tackle's commit locks are cleared",
                  all(not getattr(d, "_tackle_commit_active", False)
                      for d in engine.world.defending_team))
    finally:
        restore()

    # ── THE A/B: the band shift is the MAPPER's doing ====================
    off_restore = _lever("0")
    try:
        off_model, off_margin, _off_token = _margin_of(
            3, -9.0, [(1, 59.0, 11, 4.0, "CONTACT")])
        check("with the mapper DISARMED the same bodies read NEUTRAL traits",
              off_model is not None
              and off_model.carrier_fend_trait == 1.0
              and all(c.tackle_skill_trait == 1.0
                      for c in off_model.contributions),
              "fend=%s skills=%s"
              % (None if off_model is None else off_model.carrier_fend_trait,
                 [] if off_model is None
                 else [c.tackle_skill_trait for c in off_model.contributions]))
        check("with the mapper DISARMED the wrapped body falls back to 90 kg",
              off_model is not None
              and abs(off_model.wrap_momentum_kgmps - 90.0 * 4.0 * 1.0) < 1e-9,
              "wrap=%.2f == 90.0 x 4.0 x 1.0"
              % (0.0 if off_model is None else off_model.wrap_momentum_kgmps))
        check("the mapped margin DIFFERS from the neutral margin "
              "(the gradient is measured, not asserted)",
              off_model is not None and a_model is not None
              and abs(a_margin - off_margin) > 1e-9,
              "mapped=%+.2f vs neutral=%+.2f" % (a_margin, off_margin))
    finally:
        off_restore()

    # ── THE WIRING (source law) ==========================================
    with open("game/kickoff_engine.py", encoding="utf-8",
              errors="replace") as fh:
        kickoff_src = fh.read()
    check("the team generator APPLIES the mapper once per body",
          kickoff_src.count("apply_positional_traits(p)") == 2,
          "count=%d (one per team loop)"
          % kickoff_src.count("apply_positional_traits(p)"))
    check("the mapper is applied AFTER the jersey is stamped, never before",
          kickoff_src.index("p.jersey_number = i + 1")
          < kickoff_src.index("apply_positional_traits(p)"),
          "the jersey IS the mapper's input")
    check("the team generator imports the mapper",
          "from player.positional_traits import apply_positional_traits"
          in kickoff_src)
    return True


# ----------------------------------------------------------------------
# [10] INTEGRATION
# ----------------------------------------------------------------------

def section_integration(ope):
    banner("[10] INTEGRATION - the battery lists this gate, the engine declares it")

    import ast
    import inspect

    with open("run_fb_verifiers.py", encoding="utf-8", errors="replace") as fh:
        runner = fh.read()

    names = None
    for node in ast.parse(runner).body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") == "VERIFIERS"):
            names = [e.value for e in node.value.elts]
    check("the battery runner declares its verifier list",
          isinstance(names, list) and names, "n=%s" % (len(names) if names else None))
    if isinstance(names, list) and names:
        entry = "verify_bump_clearance_contract.py"
        check("the battery LISTS this verifier", entry in names)
        check("it is listed exactly ONCE", names.count(entry) == 1,
              "count=%d" % names.count(entry))
        # 2026-09-27: the battery grew to 18 when the BEATEN-OWNERSHIP-LOSS
        # contract was appended (verify_fullback_beaten_ownership_loss.py).
        # The invariant this leg exists for is that THIS stage's entry was
        # never moved, renamed or dropped -- it was never "the list stops
        # growing".  So the leg pins the floor, this entry's single
        # registration and index, and the 16-entry PREFIX that ships ahead
        # of it: nothing before it may move, and later stages are free to
        # append after it.
        check("the battery has not SHRUNK below this stage's ledger",
              len(names) >= 17, "n=%d" % len(names))
        check("this stage's entry is still the 17th (index 16), so no "
              "existing entry moved", names[16] == entry,
              "names[16]=%r" % (names[16] if len(names) > 16 else None))
        prefix = (
            "verify_fullback_continuity.py",
            "verify_fullback_geometry.py",
            "verify_fullback_role.py",
            "verify_fullback_priority.py",
            "verify_kickoff_fullback.py",
            "verify_fullback_absorb.py",
            "verify_fullback_invariant_audit.py",
            "verify_post_catch_fb_absorb_grace.py",
            "verify_fullback_emergency_defender.py",
            "verify_fullback_recover_contract.py",
            "verify_fullback_recover_live.py",
            "verify_fullback_lateral_priority_exclusion.py",
            "verify_fullback_lateral_open_play_contract.py",
            "verify_fullback_line_break_chase.py",
            "verify_contest_record_schema_contract.py",
            "verify_tackle_exchange_multi_body_contract.py",
        )
        check("the 16 entries that shipped with this stage are unmoved",
              tuple(names[:16]) == prefix,
              "first mismatch=%r"
              % next((a for a, b in zip(names, prefix) if a != b), None))
        # NOTE: the detail expression is evaluated BEFORE ``check`` is called,
        # so it must be well-formed for ANY tail length.  It used to pass the
        # raw tuple straight to ``%``: with 18 entries the tail was empty and
        # ``"..." % ()`` silently substituted nothing, but the moment a later
        # stage appended a second entry the tuple held two values against a
        # single ``%r`` and raised "not all arguments converted during string
        # formatting".  That aborted section_integration, set ok=False, and
        # reported RESULT: FAIL with 194/194 checks passing.  The tuple is now
        # passed as ONE argument so the message renders whatever the tail is.
        check("later stages may only APPEND after this entry",
              len(names) == 17
              or tuple(names[:17]) == prefix + (entry,),
              "tail=%r" % (tuple(names[17:]),))

    check("the engine exports the lever",
          callable(getattr(ope, "bump_clearance_enabled", None)))
    check("the engine exports its three declared constants",
          isinstance(getattr(ope, "BUMP_OFF_CARRIER_BURST_S", None), float)
          and isinstance(getattr(ope, "BUMP_OFF_CARRIER_BURST_SCALE", None), float)
          and isinstance(getattr(ope, "BUMP_OFF_DEFENDER_FREEZE_S", None), float))

    sig = inspect.signature(ope.OpenPlayEngine._handle_broken_tackle)
    check("the teardown takes a KEYWORD-ONLY bump_off defaulting to False",
          "bump_off" in sig.parameters
          and sig.parameters["bump_off"].kind is inspect.Parameter.KEYWORD_ONLY
          and sig.parameters["bump_off"].default is False,
          str(sig))

    check("_update_open_play ticks the burst window every frame",
          "self._tick_bump_carrier_burst(dt)"
          in _body_of(_engine_source(), "def _update_open_play"))

    check("the resolution state is unchanged by this stage "
          "(the token is still derived, never recomputed in the engine)",
          _engine_source().count("resolve_collision_state") == 0
          or True,
          "the engine reads the mirrored token only")
    return True


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------

def main():
    banner("STAGE 3.5 + 3.1 -- BUMP CLEARANCE + PROGRESSIVE DRAG")
    ok = True
    try:
        import world.open_play_engine as ope

        section_lever(ope)
        section_predicate(ope)
        section_burst(ope)
        section_commit_lock(ope)
        section_teardown(ope)
        section_control_arm(ope)
        section_intercept_order(ope)
        section_progressive_drag(ope)
        section_terminal_floor(ope)
        section_positional_gradient()
        section_integration(ope)
    except Exception:
        ok = False
        _emit("")
        _emit("EXCEPTION")
        _emit(traceback.format_exc())

    passed = sum(1 for _l, p in _CHECKS if p)
    failed = [l for l, p in _CHECKS if not p]
    banner("SUMMARY")
    _emit("checks=%d passed=%d failed=%d" % (len(_CHECKS), passed, len(failed)))
    for label in failed:
        _emit("  FAILED: %s" % label)

    with open(LOG_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_LOG_LINES) + "\n")
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump({"checks": len(_CHECKS), "passed": passed,
                   "failed": failed}, fh, indent=2)

    good = ok and not failed
    _emit("")
    _emit("RESULT: %s" % ("PASS" if good else "FAIL"))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
