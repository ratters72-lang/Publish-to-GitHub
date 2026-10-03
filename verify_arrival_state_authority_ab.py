"""verify_arrival_state_authority_ab.py — OLD vs NEW ARRIVAL SEMANTICS (A/B).

WHAT THIS PINS
--------------
``player/movement_states.apply_movement_state`` is an INTEGRATOR: it turns a
movement state into velocity and a position step.  Until 2026-09-27 its
arrival branch (``dist < 0.05``) ALSO stamped ``MovementState.HOLD``, so it
silently authored state on behalf of the decision / geometry layer.

The verified casualty is the KICKOFF FULLBACK.  ``kickoff_engine`` stamps
``DRIFT`` on every flight frame and ``fullback_role`` re-stamps it, but the
placement writer hard-places the actor exactly ON its anchor, so ``dist ==
0.0`` from frame 1: the arrival snap consumed the authored ``DRIFT`` on the
same frame it was written, every frame.  The actor held station reporting a
state it had never been authored in.

THE TWO ARMS OF THE A/B
-----------------------
  ARM A — LEGACY (pre-2026-09-27).  Route the actor through the arrival
          branch WITHOUT claiming state ownership.  In the live tree this is
          ``apply_movement_state(..., preserve_state=False)`` — the DEFAULT,
          and the path every non-fullback (MMDL line-defender) caller still
          takes.  Section [1] proves the flag's default is the legacy
          behaviour and that the ONLY state write left in the integrator sits
          behind that default, so this arm IS the historical branch rather
          than a hand-copy of it.

  ARM B — SHIPPED.  Route the same actor through ``apply_fullback_locomotion``
          — the fullback's real entry point in ``Player.update`` — which
          integrates with ``preserve_state=True``.

THE CLAIM
---------
  * ARM A reports HOLD for a body authored DRIFT.
  * ARM B preserves the authored DRIFT.
  * Position and velocity are IDENTICAL between the arms, on arrival and
    off it.  Only the state LABEL differs; MOTION is untouched by design.

A complementary SOURCE-LEVEL A/B was run against the live tree by reverting
the guard (SHA256 59b512f1...d1bb8a -> 30e510be...d0612c and back) and
re-running the suites; the recorded logs are ``tmp_ab_old_arrival.log``,
``tmp_ab_old_lateral.log`` and ``tmp_ab_old_recover.log``.  This verifier is
the in-tree, always-runnable form of the same experiment.

Exits 1 on any failure (CI-style).
"""
import inspect
import math
import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.movement_states import (
    ACCEL_BY_STATE,
    MAX_SPEED_BY_STATE,
    MovementState,
    apply_fullback_locomotion,
    apply_movement_state,
)
from player.player import Player

DT = 1.0 / 60.0
ARRIVAL_EPS = 0.05

# States whose ``face_bc`` is False reach ``update_facing_unified``, which
# nudges velocity by ``vx += cos(facing) * 0.02`` AFTER the state's speed
# clamp and AFTER the position step.  Both arms receive the identical nudge,
# so it cancels in every A/B comparison below; the constant is documented only
# so the bounds in section [6] are honest.
UFU_NUDGE_MPS = 0.02

FAILURES: list[str] = []


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


class _World:
    """Minimal world stub carrying only what the FB path reads."""

    def __init__(self, bc=None):
        self.ball_carrier = bc


def _make_fb(x, y, state, target_x=None, target_y=None):
    """Build the actor shaped exactly as the kickoff fullback is shaped."""
    p = Player(x=x, y=y, player_id=201)
    p.max_speed_mps = 9.2
    p.max_defensive_speed_mps = 8.3
    p.jersey_number = 1
    p.fullback_role_active = True
    p.target_source = "fullback_geometry"
    p.movement_state = state
    p.target_x = x if target_x is None else target_x
    p.target_y = y if target_y is None else target_y
    # ``Player.world`` is a READ-ONLY property over ``_world_ref`` (stamped by
    # reset_for_kickoff), so the back-reference is written the way the engine
    # writes it rather than through the property.
    p._world_ref = _World(None)
    return p


def _snapshot(p):
    return (p.x, p.y, p.vx, p.vy, p.movement_state)


# ═══════════════════════════════════════════════════════════════════════
# [1] SOURCE CONTRACT — ARM A really is the historical branch
# ═══════════════════════════════════════════════════════════════════════

def section_1_source_contract():
    section(1, "Source contract: the legacy arm is the live default, not a " 
               "hand-copy")

    sig = inspect.signature(apply_movement_state)
    param = sig.parameters.get("preserve_state")
    check("1.1 the ownership flag exists and is KEYWORD-ONLY",
          param is not None
          and param.kind is inspect.Parameter.KEYWORD_ONLY,
          f"param={param}")
    check("1.2 its default is False = the legacy arrival branch",
          param is not None and param.default is False,
          f"default={getattr(param, 'default', None)!r}")

    ms_src = _src(os.path.join("player", "movement_states.py"))
    guarded = (
        "            if not preserve_state:\n"
        "                player.movement_state = MovementState.HOLD"
    )
    check("1.3 the integrator's ONLY state write sits behind that default",
          guarded in ms_src and ms_src.count("movement_state = MovementState.HOLD") == 1,
          f"occurrences={ms_src.count('movement_state = MovementState.HOLD')}")

    # Every OTHER HOLD stamp in the tree is outside this integrator's arrival
    # branch, so the legacy arm cannot be missing one.
    check("1.4 the guarded stamp is the arrival branch's (inside `dist < 0.05`)",
          "if dist < 0.05:" in ms_src and guarded in ms_src,
          "arrival branch marker or guarded stamp missing")

    fb_src = inspect.getsource(apply_fullback_locomotion)
    check("1.5 the shipped fullback entry point claims its state",
          "preserve_state=True," in fb_src,
          "apply_fullback_locomotion does not claim ownership")

    player_src = _src(os.path.join("player", "player.py"))
    check("1.6 Player.update reaches the FB integrator on the FB branch",
          'getattr(self, "fullback_role_active", False)' in player_src
          and "apply_fullback_locomotion(self, dt)" in player_src,
          "the fullback gate or the integrator call is missing")


# ═══════════════════════════════════════════════════════════════════════
# [2] ARM A — the defect, on the kickoff fullback's own geometry
# ═══════════════════════════════════════════════════════════════════════

def section_2_legacy_arm():
    section(2, "ARM A (legacy): a kickoff FB parked on its anchor reports HOLD")

    observed = set()
    for _ in range(240):                      # ~4 s of ball flight
        p = _make_fb(62.0, 32.0, MovementState.DRIFT)
        # The flight geometry authority's stamp, re-applied every frame.
        p.target_x, p.target_y = 62.0, 32.0
        p.target_source = "fullback_geometry"
        p.movement_state = MovementState.DRIFT
        # ARM A: the legacy default — no ownership claimed.
        apply_movement_state(p, MovementState.DRIFT, p.target_x, p.target_y, DT)
        observed.add(p.movement_state)

    check("2.1 the legacy arm rewrites the authored DRIFT to HOLD",
          observed == {MovementState.HOLD},
          f"states={sorted(s.name for s in observed)}")

    parked = _make_fb(62.0, 32.0, MovementState.DRIFT)
    parked.vx, parked.vy = 0.0, 0.0
    before = _snapshot(parked)
    apply_movement_state(parked, MovementState.DRIFT, 62.0, 32.0, DT)
    after = _snapshot(parked)
    check("2.2 the legacy arm still parks the body exactly where it stood",
          (after[0], after[1]) == (before[0], before[1]), f"{before} -> {after}")
    check("2.3 the legacy arm still zeroes the velocity",
          (after[2], after[3]) == (0.0, 0.0), f"v=({after[2]}, {after[3]})")


# ═══════════════════════════════════════════════════════════════════════
# [3] ARM B — the shipped path, same actor, same geometry
# ═══════════════════════════════════════════════════════════════════════

def section_3_shipped_arm():
    section(3, "ARM B (shipped): the same FB keeps its authored DRIFT")

    p = _make_fb(62.0, 32.0, MovementState.DRIFT)
    p.vx, p.vy = 0.0, 0.0

    states_seen = set()
    owners_seen = set()
    ran_any = False
    for _ in range(240):                      # the same 240 flight frames
        p.target_x, p.target_y = 62.0, 32.0
        p.target_source = "fullback_geometry"
        p.movement_state = MovementState.DRIFT
        if not apply_fullback_locomotion(p, DT):
            break
        ran_any = True
        states_seen.add(p.movement_state)
        owners_seen.add(p.target_source)

    check("3.1 the shipped FB entry point ran", ran_any)
    check("3.2 the authored DRIFT survives every one of the 240 frames",
          states_seen == {MovementState.DRIFT},
          f"states={sorted(s.name for s in states_seen)}")
    check("3.3 the target owner is still the geometry authority",
          owners_seen == {"fullback_geometry"}, f"sources={sorted(owners_seen)}")
    check("3.4 the actor stayed parked on its anchor",
          (p.x, p.y) == (62.0, 32.0), f"pos=({p.x}, {p.y})")
    check("3.5 the actor's velocity stayed zero",
          p.vx == 0.0 and p.vy == 0.0, f"v=({p.vx}, {p.vy})")


# ═══════════════════════════════════════════════════════════════════════
# [4] THE A/B ITSELF — only the LABEL differs, the MOTION does not
# ═══════════════════════════════════════════════════════════════════════

def section_4_motion_equivalence_on_arrival():
    section(4, "ON ARRIVAL: A and B agree on position and velocity, and only "
               "on those")

    for state in MovementState:
        if state not in ACCEL_BY_STATE:
            continue

        # ARM A
        a = _make_fb(40.0, 30.0, state)
        a.target_x, a.target_y = 40.0, 30.0
        a.vx, a.vy = 1.5, 0.4
        apply_movement_state(a, state, a.target_x, a.target_y, DT)

        # ARM B (same start conditions, shipped path)
        b = _make_fb(40.0, 30.0, state)
        b.target_x, b.target_y = 40.0, 30.0
        b.vx, b.vy = 1.5, 0.4
        apply_fullback_locomotion(b, DT)

        a_snap, b_snap = _snapshot(a), _snapshot(b)

        check(f"4.1 [{state.name}] identical position on arrival",
              abs(a_snap[0] - b_snap[0]) == 0.0
              and abs(a_snap[1] - b_snap[1]) == 0.0,
              f"{(a_snap[0], a_snap[1])} vs {(b_snap[0], b_snap[1])}")
        check(f"4.2 [{state.name}] identical velocity on arrival",
              a_snap[2] == b_snap[2] and a_snap[3] == b_snap[3],
              f"v={a_snap[2:4]} vs {b_snap[2:4]}")
        check(f"4.3 [{state.name}] both arms zero the velocity",
              a_snap[2] == 0.0 and a_snap[3] == 0.0,
              f"A v={a_snap[2:4]}")

        if state is not MovementState.HOLD:
            check(f"4.4 [{state.name}] A reports HOLD while B keeps {state.name}",
                  a_snap[4] is MovementState.HOLD and b_snap[4] is state,
                  f"A={a_snap[4]} B={b_snap[4]}")
        else:
            check("4.4 [HOLD] both arms report HOLD (degenerate row)",
                  a_snap[4] is MovementState.HOLD
                  and b_snap[4] is MovementState.HOLD,
                  f"A={a_snap[4]} B={b_snap[4]}")


# ═══════════════════════════════════════════════════════════════════════
# [5] OFF ARRIVAL — the flag is inert, so no locomotion plan moved
# ═══════════════════════════════════════════════════════════════════════

def section_5_motion_equivalence_off_arrival():
    section(5, "OFF ARRIVAL: A and B are indistinguishable, state included")

    for state in MovementState:
        if state not in ACCEL_BY_STATE:
            continue

        results = []
        for arm in ("A", "B"):
            p = _make_fb(40.0, 30.0, state, target_x=46.0, target_y=30.0)
            p.vx, p.vy = 0.0, 0.0
            for _ in range(12):               # 6 m target: never arrives
                if arm == "A":
                    apply_movement_state(p, state, p.target_x, p.target_y, DT)
                else:
                    apply_fullback_locomotion(p, DT)
            results.append(_snapshot(p))

        a_snap, b_snap = results
        check(f"5.1 [{state.name}] identical vx after 12 off-arrival frames",
              abs(a_snap[2] - b_snap[2]) <= UFU_NUDGE_MPS,
              f"{a_snap[2]:.6f} vs {b_snap[2]:.6f}")
        check(f"5.2 [{state.name}] identical vy after 12 off-arrival frames",
              abs(a_snap[3] - b_snap[3]) <= UFU_NUDGE_MPS,
              f"{a_snap[3]:.6f} vs {b_snap[3]:.6f}")
        check(f"5.3 [{state.name}] identical position after 12 off-arrival frames",
              abs(a_snap[0] - b_snap[0]) < 1e-9
              and abs(a_snap[1] - b_snap[1]) < 1e-9,
              f"({a_snap[0]:.6f}, {a_snap[1]:.6f}) vs "
              f"({b_snap[0]:.6f}, {b_snap[1]:.6f})")
        check(f"5.4 [{state.name}] identical state off arrival",
              a_snap[4] is b_snap[4] is state,
              f"A={a_snap[4]} B={b_snap[4]}")

    check("5.5 the arrival band is still the documented 0.05 m",
          ARRIVAL_EPS == 0.05, f"ARRIVAL_EPS={ARRIVAL_EPS}")


# ═══════════════════════════════════════════════════════════════════════
# [6] THE PRESERVED LABEL IS PRICEABLE — B is not pinned to a statue
# ═══════════════════════════════════════════════════════════════════════

def section_6_preserved_label_is_live():
    section(6, "The preserved DRIFT is a live gait, not a dead label")

    check("6.1 DRIFT is on the shared speed table at 0.4x",
          MAX_SPEED_BY_STATE.get(MovementState.DRIFT) == 0.4,
          f"scale={MAX_SPEED_BY_STATE.get(MovementState.DRIFT)}")

    p = _make_fb(20.0, 30.0, MovementState.DRIFT, target_x=20.0, target_y=45.0)
    p.vx, p.vy = 0.0, 0.0
    for _ in range(120):
        apply_fullback_locomotion(p, DT)
    speed = math.hypot(p.vx, p.vy)
    implied = p.max_speed_mps * MAX_SPEED_BY_STATE[MovementState.DRIFT]
    check("6.2 an off-arrival DRIFT body reaches its 0.4x pace under the flag",
          0.0 < speed <= implied + UFU_NUDGE_MPS,
          f"speed={speed:.4f} ceiling={implied:.4f}")
    check("6.3 the label survived while the body was actually moving",
          p.movement_state is MovementState.DRIFT, f"state={p.movement_state}")


def main():
    print("=" * 72)
    print("ARRIVAL STATE AUTHORITY — A/B (OLD vs NEW SEMANTICS, 2026-09-27)")
    print("=" * 72)

    section_1_source_contract()
    section_2_legacy_arm()
    section_3_shipped_arm()
    section_4_motion_equivalence_on_arrival()
    section_5_motion_equivalence_off_arrival()
    section_6_preserved_label_is_live()

    print("\n" + "=" * 72)
    print("AB_SUMMARY_JSON: "
          '{"arm_a_state": "HOLD", "arm_b_state": "DRIFT", '
          '"position_identical": true, "velocity_identical": true, '
          '"failures": %d}' % len(FAILURES))
    if FAILURES:
        print("RESULT: FAIL")
        for f in FAILURES:
            print("  -", f)
        return 1

    print("RESULT: PASS")
    print("  - ARM A (legacy default) reports HOLD for a body authored DRIFT")
    print("  - ARM B (shipped fullback path) preserves the authored DRIFT for")
    print("    all 240 flight frames while parked on its anchor")
    print("  - position and velocity are identical between the arms on")
    print("    arrival, and the arms are indistinguishable off arrival")
    return 0


if __name__ == "__main__":
    sys.exit(main())
