"""verify_fullback_intent_ownership_contract.py — STAGE 1: INTENT OWNERSHIP.

GATES: the dynamic fullback's INTENT has exactly ONE owner.

What Stage 1 claims, and what this file proves:

  [1] THE VOCABULARY.  There is exactly one intent set (UNOWNED / SWEEP /
      CHASE / RECOVER / FLIGHT) and the intent -> MovementState mapping is
      total over it.

  [2] ONE WRITE PATH.  ``fullback_controller.enter_fullback_intent`` is the
      only thing that writes a tagged fullback's ``movement_state``, and what
      it writes is ``INTENT_MOVEMENT_STATE[intent]`` — one of the SAME
      ``MovementState`` singletons every producer named literally before this
      stage existed.  The stage is therefore behaviour-neutral BY
      CONSTRUCTION, and this section pins that construction.

  [3] UNOWNED WRITES NOTHING.  The five early yields the geometry writer has
      always performed are named honestly ("not this controller's body") and
      a yield must not invent a movement state or claim ownership.

  [4] THE ROLLBACK LEVER.  ``RLM_FB_INTENT_CONTROLLER=0`` suppresses the
      telemetry and changes NOTHING about the state written — a true no-op
      fallback, proved by an in-process A/B on the written value.

  [5] THE TRANSITION LAW.  Every handover the live engine can take is a
      declared edge, and a recorded handover is always legal.

  [6] THE OWNERSHIP BOUNDARY (source wiring).  No module other than the
      controller writes a tagged fullback's movement state:
        * ``fullback_role.py``      — routed calls, 0 bare writes;
        * ``kickoff_engine.py``     — routed, 0 bare writes;
        * ``movement_mode_decision.py`` — the fullback branch resolves its
          verdict through the controller and names no fullback state itself.
      The controller must NOT import ``fullback_role`` at module scope, or
      the delegation would close an import cycle.

  [7] LIVE.  Boots the real engine on a fixed seed and, on every frame the
      defence ran, asserts that a tagged fullback is never unstamped, that
      its intent is always in the closed vocabulary, and that every observed
      handover is a declared edge.  It then REPORTS (never asserts) the
      end-of-frame agreement rate between the stamped intent and the actor's
      live state — the measurable size of the CHASE/DRIFT split-brain that
      Stage 2 (State Ownership) exists to remove.

Exits 1 on any failed contract check (CI-style).
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import random
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

# HEADLESS DISPLAY: the live section boots the real engine, and
# ``world.field.Field.__init__`` loads its grass texture with ``convert()``,
# which requires an initialised display surface.  Same bootstrap as
# verify_fb_determinism.py.
import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

from types import SimpleNamespace as NS

from player.movement_states import MovementState

import game.defence.defence_lvl_1.fullback_controller as fbc
from game.defence.defence_lvl_1.fullback_controller import (
    CONTROLLER,
    FullbackIntent,
    INTENT_MOVEMENT_STATE,
    INTENTS,
    enter_fullback_intent,
    intent_for_state,
    intent_movement_state,
    intent_name,
    intent_state_agrees,
    intent_transition_was_legal,
    is_legal_intent_transition,
    live_fullback_intent,
    stamped_fullback_intent,
    stamped_fullback_intent_source,
)

CONTROLLER_PATH = "game/defence/defence_lvl_1/fullback_controller.py"
ROLE_PATH = "game/defence/defence_lvl_1/fullback_role.py"
KICKOFF_PATH = "game/kickoff_engine.py"
MMDL_PATH = "game/defence/defence_lvl_1/movement_mode_decision.py"

SEED = 12345
MAX_FRAMES = 3600
DT = 1.0 / 60.0

FAILURES = []
CHECKS = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" \u2014 {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def section(number: int, title: str) -> None:
    print(f"\n[{number}] {title}")


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _fb_stub(state=None, tagged=True):
    """A minimal tagged fullback body — enough for the write path."""
    body = NS(
        fullback_role_active=tagged,
        movement_state=state,
        facing_override=False,
    )
    body.clear_facing_override = lambda: setattr(
        body, "facing_override", False
    )
    return body


def _world(frame=1, in_kickoff=False):
    return NS(frame_count=frame, in_kickoff=in_kickoff)


# ======================================================================
# [1] THE VOCABULARY
# ======================================================================

def sec_vocabulary() -> None:
    section(1, "The vocabulary — one intent set, one total mapping")
    check("the intent set is exactly the five declared names",
          [i.name for i in INTENTS] ==
          ["UNOWNED", "SWEEP", "CHASE", "RECOVER", "FLIGHT"],
          str([i.name for i in INTENTS]))
    check("the mapping is total over the intent set",
          all(i in INTENT_MOVEMENT_STATE for i in INTENTS))
    check("SWEEP resolves to the sweeper state (DRIFT)",
          intent_movement_state(FullbackIntent.SWEEP) is MovementState.DRIFT)
    check("CHASE resolves to the pursuit state (CHASE)",
          intent_movement_state(FullbackIntent.CHASE) is MovementState.CHASE)
    check("RECOVER resolves to the owed-ground state (RECOVER)",
          intent_movement_state(FullbackIntent.RECOVER) is MovementState.RECOVER)
    check("FLIGHT resolves to the airborne state (DRIFT)",
          intent_movement_state(FullbackIntent.FLIGHT) is MovementState.DRIFT)
    check("UNOWNED resolves to NO state (a yield is not a command)",
          intent_movement_state(FullbackIntent.UNOWNED) is None)
    check("an unrecognised intent resolves to NO state",
          intent_movement_state("SWEEP") is None)

    check("intent_for_state recovers SWEEP from DRIFT",
          intent_for_state(MovementState.DRIFT) is FullbackIntent.SWEEP)
    check("intent_for_state recovers CHASE from CHASE",
          intent_for_state(MovementState.CHASE) is FullbackIntent.CHASE)
    check("intent_for_state recovers RECOVER from RECOVER",
          intent_for_state(MovementState.RECOVER) is FullbackIntent.RECOVER)
    check("a state this controller never authors is UNOWNED",
          all(intent_for_state(s) is FullbackIntent.UNOWNED
              for s in (MovementState.HOLD, MovementState.RETREAT,
                        MovementState.SLIDE, MovementState.SHOOT)))
    check("a body with no state at all is UNOWNED",
          intent_for_state(None) is FullbackIntent.UNOWNED)

    check("intent_name is the stable enum name",
          intent_name(FullbackIntent.CHASE) == "CHASE"
          and intent_name(None) == "None")
    check("the swap SWEEP <-> CHASE is a round trip",
          intent_for_state(intent_movement_state(FullbackIntent.CHASE))
          is FullbackIntent.CHASE)


# ======================================================================
# [2] ONE WRITE PATH — behaviour neutrality by construction
# ======================================================================

def sec_single_write_path() -> None:
    section(2, "One write path — the state written is the mapped singleton")
    for intent, expected in (
        (FullbackIntent.SWEEP, MovementState.DRIFT),
        (FullbackIntent.CHASE, MovementState.CHASE),
        (FullbackIntent.RECOVER, MovementState.RECOVER),
        (FullbackIntent.FLIGHT, MovementState.DRIFT),
    ):
        body = _fb_stub()
        written = enter_fullback_intent(
            _world(), body, intent, source="contract"
        )
        check(f"enter_fullback_intent({intent.name}) writes {expected.name}",
              written is expected and body.movement_state is expected,
              f"wrote {getattr(written, 'name', written)}")
        check(f"...and it is the SAME singleton the literal write used "
              f"({intent.name})",
              body.movement_state is intent_movement_state(intent))

    _repeat = _fb_stub()
    _first = enter_fullback_intent(_world(), _repeat, FullbackIntent.CHASE)
    _second = enter_fullback_intent(_world(), _repeat, FullbackIntent.CHASE)
    check("the identical state object is written for a repeated entry",
          _first is _second is MovementState.CHASE
          and _repeat.movement_state is MovementState.CHASE)

    check("no body -> no write, no crash",
          enter_fullback_intent(_world(), None, FullbackIntent.CHASE) is None)

    # The telemetry is the ONLY new runtime footprint.
    body = _fb_stub()
    enter_fullback_intent(_world(frame=7), body, FullbackIntent.CHASE,
                          source=fbc.SOURCE_BREAK_DEPTH)
    check("the commanded intent is stamped",
          stamped_fullback_intent(body) is FullbackIntent.CHASE)
    check("the reason is stamped",
          stamped_fullback_intent_source(body) == fbc.SOURCE_BREAK_DEPTH)
    check("the stamping frame is recorded",
          fbc.stamped_fullback_intent_frame(body) == 7)
    check("the contract reader agrees with the live state",
          intent_state_agrees(body))

    # A DRIFT facing lock must not survive the handoff into movement-facing
    # CHASE, while unrelated intent transitions leave existing locks intact.
    body = _fb_stub(state=MovementState.DRIFT)
    body.facing_override = True
    enter_fullback_intent(_world(), body, FullbackIntent.CHASE)
    check("entering CHASE clears a stale facing override",
          body.movement_state is MovementState.CHASE
          and body.facing_override is False)

    body = _fb_stub(state=MovementState.DRIFT)
    body.facing_override = True
    enter_fullback_intent(_world(), body, FullbackIntent.RECOVER)
    check("non-CHASE transitions preserve facing overrides",
          body.movement_state is MovementState.RECOVER
          and body.facing_override is True)

    # A body the controller has never touched is not a contract violation.
    check("an uncommanded body passes the agreement reader",
          intent_state_agrees(_fb_stub()))


# ======================================================================
# [3] UNOWNED WRITES NOTHING
# ======================================================================

def sec_unowned_writes_nothing() -> None:
    section(3, "UNOWNED — a yield is not a command")
    body = _fb_stub(state=MovementState.CHASE)
    result = enter_fullback_intent(_world(), body, FullbackIntent.UNOWNED,
                                   source="yield")
    check("UNOWNED writes no movement state",
          body.movement_state is MovementState.CHASE)
    check("UNOWNED returns no state", result is None)
    check("UNOWNED claims no ownership (nothing stamped)",
          stamped_fullback_intent(body) is None)
    check("...and no reason either",
          stamped_fullback_intent_source(body) == "")

    # The five yields the geometry writer performs must not clear an
    # existing command: a body holding SWEEP keeps holding it.
    body = _fb_stub()
    enter_fullback_intent(_world(), body, FullbackIntent.SWEEP,
                          source=fbc.SOURCE_ANCHOR)
    enter_fullback_intent(_world(), body, FullbackIntent.UNOWNED, source="yield")
    check("a yield does not erase the last command",
          stamped_fullback_intent(body) is FullbackIntent.SWEEP)
    check("...and the body keeps its commanded state",
          body.movement_state is MovementState.DRIFT)


# ======================================================================
# [4] THE ROLLBACK LEVER
# ======================================================================

def sec_rollback_lever() -> None:
    section(4, "The rollback lever — bookkeeping only, never behaviour")
    check("the lever is declared with a named env var",
          fbc.FB_INTENT_CONTROLLER_ENV_VAR == "RLM_FB_INTENT_CONTROLLER")
    check("the lever ships ON", fbc.fullback_intent_controller_enabled() is True)

    saved = fbc.FB_INTENT_CONTROLLER
    try:
        on_body = _fb_stub()
        on_written = enter_fullback_intent(
            _world(), on_body, FullbackIntent.CHASE, source="ab"
        )
        fbc.FB_INTENT_CONTROLLER = False
        off_body = _fb_stub()
        off_written = enter_fullback_intent(
            _world(), off_body, FullbackIntent.CHASE, source="ab"
        )
        check("lever ON and OFF write the SAME state object",
              on_written is off_written is MovementState.CHASE)
        check("lever ON stamps the intent",
              stamped_fullback_intent(on_body) is FullbackIntent.CHASE)
        check("lever OFF stamps nothing (pre-stage footprint restored)",
              stamped_fullback_intent(off_body) is None
              and stamped_fullback_intent_source(off_body) == "")
    finally:
        fbc.FB_INTENT_CONTROLLER = saved

    check("the facade exposes the same lever",
          CONTROLLER.enabled() is True)
    check("the facade resolves intents through the same law",
          CONTROLLER.state_for(FullbackIntent.SWEEP) is MovementState.DRIFT)


# ======================================================================
# [5] THE TRANSITION LAW
# ======================================================================

def sec_transition_law() -> None:
    section(5, "The transition law — every handover the engine takes")
    check("staying in an intent is always legal",
          all(is_legal_intent_transition(i, i) for i in INTENTS))
    check("ANY intent may fall back to UNOWNED (a defender can take the body)",
          all(is_legal_intent_transition(i, FullbackIntent.UNOWNED)
              for i in INTENTS))
    check("anything may be entered from UNOWNED (the re-assert contract)",
          all(is_legal_intent_transition(FullbackIntent.UNOWNED, i)
              for i in INTENTS if i is not FullbackIntent.UNOWNED))
    check("the sweeper may break into a chase",
          is_legal_intent_transition(FullbackIntent.SWEEP,
                                     FullbackIntent.CHASE))
    check("a chase may decay back to the sweeper",
          is_legal_intent_transition(FullbackIntent.CHASE,
                                     FullbackIntent.SWEEP))
    check("a chase may hand over to owed-ground recovery",
          is_legal_intent_transition(FullbackIntent.CHASE,
                                     FullbackIntent.RECOVER))
    check("the sweeper may take the kickoff flight contract",
          is_legal_intent_transition(FullbackIntent.SWEEP,
                                     FullbackIntent.FLIGHT))
    check("the flight contract releases into open play",
          is_legal_intent_transition(FullbackIntent.FLIGHT,
                                     FullbackIntent.SWEEP))

    body = _fb_stub()
    enter_fullback_intent(_world(), body, FullbackIntent.SWEEP, source="a")
    enter_fullback_intent(_world(), body, FullbackIntent.CHASE, source="b")
    check("a recorded handover is legal",
          intent_transition_was_legal(body)
          and getattr(body, fbc.INTENT_PREV_ATTR) is FullbackIntent.SWEEP)
    check("a body with no recorded handover passes",
          intent_transition_was_legal(_fb_stub()))


# ======================================================================
# [6] THE OWNERSHIP BOUNDARY (source wiring)
# ======================================================================

def sec_source_wiring() -> None:
    section(6, "The ownership boundary — no other module writes the state")
    role_src = _read(ROLE_PATH)
    kickoff_src = _read(KICKOFF_PATH)
    mmdl_src = _read(MMDL_PATH)
    ctrl_src = _read(CONTROLLER_PATH)

    routed = role_src.count("enter_fullback_intent(")
    bare = role_src.count("movement_state =")
    check("fullback_role routes every producer through the controller",
          routed >= 7, f"routed={routed}")
    check("fullback_role contains NO bare movement-state write",
          bare == 0, f"bare writes={bare}")
    check("fullback_role imports the controller lazily",
          "from game.defence.defence_lvl_1.fullback_controller import" in role_src)
    check("the geometry writer's signature is unchanged for its callers",
          "def apply_fullback_geometry(world, defender, kickoff_flight" in role_src)

    check("kickoff_engine routes the in-flight contract through the controller",
          "enter_fullback_intent(" in kickoff_src
          and "SOURCE_FLIGHT" in kickoff_src)
    check("kickoff_engine contains NO bare fullback movement-state write",
          "fb_actor.movement_state =" not in kickoff_src)

    start = mmdl_src.find('if getattr(defender, "fullback_role_active", False):')
    end = mmdl_src.find('if getattr(defender, "tackle_state_active", False):')
    check("the MMDL fullback branch exists where expected",
          start != -1 and end != -1 and end > start,
          f"start={start} end={end}")
    branch = mmdl_src[start:end] if (start != -1 and end > start) else ""
    check("the MMDL fullback branch resolves through the controller",
          "intent_movement_state(" in branch
          and "FullbackIntent.SWEEP" in branch
          and "FullbackIntent.CHASE" in branch)
    check("the MMDL fullback branch names no movement state literally",
          "return MovementState.CHASE" not in branch
          and "return MovementState.DRIFT" not in branch
          and "return MovementState.RECOVER" not in branch)
    retreat_ban = mmdl_src.find(
        'and getattr(next_state, "name", None) == "RETREAT"'
    )
    check("the MMDL retreat ban resolves through the controller",
          retreat_ban != -1
          and "intent_movement_state(" in mmdl_src[retreat_ban:retreat_ban + 500])

    check("the controller declares the intent vocabulary",
          "class FullbackIntent(Enum):" in ctrl_src)
    check("the controller declares the single write path",
          "def enter_fullback_intent(" in ctrl_src)
    check("the controller declares the intent -> state law",
          "def intent_movement_state(" in ctrl_src)
    cycle = [
        line for line in ctrl_src.splitlines()
        if (line.startswith("import ") or line.startswith("from "))
        and "fullback_role" in line
    ]
    check("the controller does NOT import fullback_role (no import cycle)",
          not cycle, str(cycle))


# ======================================================================
# [7] LIVE — the real engine
# ======================================================================

def run_simulation():
    """Boot the real engine on the fixed seed; return the FB intent trace."""
    import world.open_play_engine as ope_mod
    ope_mod.DEBUG_SDEA_PHYSICS = False
    ope_mod.DEBUG_OPE_TRACE = False

    from world.field import Field
    from world.world_state import WorldState, GamePhase
    from world.open_play_engine import OpenPlayEngine
    from kick_off.kickoff_formations import (
        apply_receiving_kickoff_formation,
        apply_kicking_team_formation,
    )
    from game.kickoff_engine import KickoffEngine
    from ball.ball import Ball
    from game.physics.collision_system import CollisionSystem
    from world.catch_system import check_for_catch

    random.seed(SEED)

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    world.ruck_established = True

    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play

    kickoff_engine = KickoffEngine(world, ball, field)

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    rows = []
    for frame in range(1, MAX_FRAMES + 1):
        world.frame_count = frame
        ball.update(DT)

        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(DT)
            check_for_catch(world, ball, DT)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                world.open_play.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

        if getattr(world, "defence_ran_this_frame", False):
            for body in world.defending_team:
                if not getattr(body, "fullback_role_active", False):
                    continue
                rows.append((
                    frame,
                    stamped_fullback_intent(body),
                    live_fullback_intent(world, body),
                    getattr(body, "movement_state", None),
                    intent_state_agrees(body),
                    intent_transition_was_legal(body),
                ))

        if frame > 800 and open_play._phase == "OPEN_PLAY" \
                and world.ball_carrier is not None:
            break

    return rows


def sec_live() -> None:
    section(7, "Live — a tagged fullback is always a stamped, legal owner")
    print("        (booting the real engine on seed "
          f"{SEED}; this takes a moment)")
    rows = run_simulation()

    check("the live rollout produced fullback frames", len(rows) > 0,
          f"{len(rows)} tagged-fullback frames")
    if not rows:
        return

    check("a tagged fullback NEVER runs unstamped",
          all(r[1] is not None for r in rows),
          f"{sum(1 for r in rows if r[1] is None)} unstamped frames")
    check("the stamped intent is always in the closed vocabulary",
          all(r[1] in INTENTS for r in rows))
    check("the live intent reading is always in the closed vocabulary",
          all(r[2] in INTENTS for r in rows))
    check("every commanded state is a real MovementState",
          all(r[3] is None or isinstance(r[3], MovementState) for r in rows))
    illegal = [r for r in rows if not r[5]]
    check("every observed intent handover is a declared edge",
          not illegal, f"{len(illegal)} illegal handovers")

    histogram = {}
    for _, intent, _, _, _, _ in rows:
        histogram[intent_name(intent)] = histogram.get(intent_name(intent), 0) + 1
    print("\n        [info] commanded intent histogram over the tagged frames:")
    for name in sorted(histogram):
        print(f"               {name:<8} {histogram[name]:>6}")

    live_histogram = {}
    for _, _, live, _, _, _ in rows:
        live_histogram[intent_name(live)] = live_histogram.get(
            intent_name(live), 0) + 1
    print("        [info] LIVE intent histogram (from tag + movement_state):")
    for name in sorted(live_histogram):
        print(f"               {name:<8} {live_histogram[name]:>6}")

    # ── THE SPLIT-BRAIN MEASUREMENT (reported, never asserted) ──
    # End-of-frame the actor's state may legitimately differ from the last
    # command: the geometry authority yields to a tackle-side system, or the
    # MMDL policy branch overwrites the state after the writer ran.  THAT
    # second case is exactly the CHASE/DRIFT split-brain the overall stage
    # plan describes and Stage 2 (State Ownership) removes.  Stage 1 does
    # NOT fix it, so this section measures it instead of asserting it away.
    disagrees = sum(1 for r in rows if not r[4])
    rate = 100.0 * (len(rows) - disagrees) / len(rows)
    pairs = {}
    for _, intent, _, state, agrees, _ in rows:
        if agrees:
            continue
        key = (intent_name(intent),
               getattr(state, "name", None) or str(state))
        pairs[key] = pairs.get(key, 0) + 1
    print(f"\n        [info] end-of-frame intent/state agreement "
          f"{rate:.2f}% ({len(rows) - disagrees}/{len(rows)})")
    for (cmnd_name, state_name), count in sorted(
        pairs.items(), key=lambda kv: -kv[1]
    )[:6]:
        print(f"               commanded {cmnd_name:<8} "
              f"carried {state_name:<8} x{count}")
    check("the agreement rate is not degenerate (the trace is alive)",
          rate > 50.0, f"{rate:.2f}%")


# ======================================================================

def main() -> int:
    print("FULLBACK INTENT OWNERSHIP CONTRACT (Stage 1, 2026-09-30)")
    print("=" * 68)
    sec_vocabulary()
    sec_single_write_path()
    sec_unowned_writes_nothing()
    sec_rollback_lever()
    sec_transition_law()
    sec_source_wiring()
    sec_live()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print(f"RESULT: PASS \u2014 ALL {CHECKS} CHECKS PASSED")
    print("        One vocabulary, one mapping, one write path: the dynamic")
    print("        fullback's intent has a single owner, and the state it")
    print("        writes is the same singleton every producer named before.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
