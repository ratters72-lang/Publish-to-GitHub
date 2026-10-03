"""verify_arrival_preserves_state.py — ARRIVAL AUTHORITY BOUNDARY (2026-09-27).

THE VIOLATION THIS PINS
-----------------------
``player/movement_states.apply_movement_state`` is an INTEGRATOR: it converts
a movement state into velocity and a position step.  It is not a state
authority.  Until 2026-09-27 its arrival branch (``dist < 0.05``) also stamped
``MovementState.HOLD``, silently overwriting whatever the decision / geometry
layer had authored that frame.

The verified casualty was the KICKOFF FULLBACK.  ``kickoff_engine`` stamps
``DRIFT`` on every flight frame and ``fullback_role`` re-stamps it, but
``placement_writer`` hard-places the actor exactly ON its anchor, so
``dist == 0.0`` from frame 1: the arrival snap consumed the authored ``DRIFT``
on the same frame it was written, every frame.  The actor held station
reporting a state it had never been authored in.

THE LAW NOW
-----------
  * the arrival branch stops VELOCITY and nothing else;
  * the HOLD stamp survives only for callers that did not claim ownership of
    the state — the MMDL line-defender path, whose pre-tackle posture read
    (``pretackle_machine.read_posture``) classifies "STATIONARY" on HOLD;
  * ``apply_fullback_locomotion`` — the fullback's entry point — integrates
    with ``preserve_state=True``, so the actor's authority-owned DRIFT /
    CHASE / RECOVER survives arrival and the function is a genuine pure
    consumer.

MOTION IS UNCHANGED BY DESIGN: the velocity stop happens in both modes.  Only
the state LABEL is at stake, which is exactly why section [6] exists — it
proves the flag is inert outside the arrival branch.

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
from player.collapse_controls import COLLAPSE_COMMIT_RADIUS_M, collapse_commit

DT = 1.0 / 60.0
ARRIVAL_EPS = 0.05

# States whose ``face_bc`` is False reach ``update_facing_unified``, which
# nudges velocity by ``vx += cos(facing) * 0.02``.  That nudge lands AFTER the
# state's speed clamp and AFTER the position step, so section [6] allows for
# exactly that much and no more.
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


class _Ball:
    def __init__(self, x, y, vy=0.0):
        self.x = x
        self.y = y
        self.vy = vy


def _make_player(x=40.0, y=30.0):
    p = Player(x=x, y=y, player_id=900)
    p.max_speed_mps = 9.2
    p.max_defensive_speed_mps = 8.3
    p.jersey_number = 1
    # ``Player.world`` is a READ-ONLY property over ``_world_ref`` (stamped by
    # reset_for_kickoff), so the back-reference is written the way the engine
    # writes it rather than through the property.
    p._world_ref = _World(None)
    return p


def _park(player, state, vx=1.5, vy=0.4):
    """Put *player* exactly ON its target with a live velocity, in *state*."""
    player.target_x = player.x
    player.target_y = player.y
    player.vx = vx
    player.vy = vy
    player.movement_state = state


# ═══════════════════════════════════════════════════════════════════════
# [1] CONTRACT IN SOURCE — the flag exists, is keyword-only, is guarded,
#     and exactly ONE engine call site claims ownership.
# ═══════════════════════════════════════════════════════════════════════

def section_1_contract_in_source():
    section(1, "Source contract: keyword-only flag, guarded write, one owner")

    ms_src = _src(os.path.join("player", "movement_states.py"))

    sig = inspect.signature(apply_movement_state)
    param = sig.parameters.get("preserve_state")
    check("1.1 apply_movement_state exposes preserve_state",
          param is not None,
          f"params={sorted(sig.parameters)}")
    if param is not None:
        check("1.2 preserve_state is KEYWORD-ONLY",
              param.kind is inspect.Parameter.KEYWORD_ONLY,
              f"kind={param.kind}")
        check("1.3 preserve_state defaults to False (MMDL path unchanged)",
              param.default is False,
              f"default={param.default!r}")

    check("1.4 arrival branch guards the HOLD stamp with `if not preserve_state:`",
          "if not preserve_state:" in ms_src,
          "guard missing — an unconditional HOLD write is the violation")

    guarded = (
        "            if not preserve_state:\n"
        "                player.movement_state = MovementState.HOLD"
    )
    check("1.5 the ONLY HOLD stamp left in the integrator sits behind the guard",
          guarded in ms_src,
          "expected the guarded HOLD write verbatim")

    # Every ENGINE call site that claims ownership, repo-wide.
    #
    # INSTRUMENTATION IS EXCLUDED (2026-09-29): a checker that ASSERTS a
    # property necessarily QUOTES the token it searches for, and the claim
    # under test is about the engine, not about the check.  Until this date
    # only THIS file excluded itself, so the very first second checker to
    # quote the token -- verify_arrival_state_authority_ab.py, then
    # verify_fb_chase_intercept_tracking.py -- turned this check red for a
    # reason that had nothing to do with engine ownership.  The rule is now
    # the general one: only engine modules (player/, game/, world/, ball/,
    # run.py) can claim ownership of a movement state.
    self_path = os.path.abspath(__file__)
    INSTRUMENTATION_PREFIXES = ("verify_", "probe_", "tmp_", "tmp", "debug_")
    owners = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "__pycache__", ".venv", "node_modules"}]
        for name in files:
            if not name.endswith(".py"):
                continue
            if name.startswith(INSTRUMENTATION_PREFIXES):
                continue
            path = os.path.join(root, name)
            if os.path.abspath(path) == self_path:
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    body = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            if "preserve_state=True," in body:
                owners.append(path.replace("\\", "/").lstrip("./"))

    expected_owner = "player/movement_states.py"
    check("1.6 exactly ONE engine file claims state ownership",
          owners == [expected_owner],
          f"owners={sorted(owners)} expected=[{expected_owner!r}]")

    fb_src = inspect.getsource(apply_fullback_locomotion)
    check("1.7 apply_fullback_locomotion is that owner",
          "preserve_state=True," in fb_src,
          "the fullback path does not claim its authored state")

    player_src = _src(os.path.join("player", "player.py"))
    check("1.8 Player.update reaches the FB integrator ONLY on the FB branch",
          'getattr(self, "fullback_role_active", False)' in player_src
          and "apply_fullback_locomotion(self, dt)" in player_src,
          "the fullback gate or the integrator call is missing from Player.update")


# ═══════════════════════════════════════════════════════════════════════
# [2] LEGACY MODE — preserve_state=False keeps the round's MMDL behaviour
# ═══════════════════════════════════════════════════════════════════════

def section_2_legacy_mode():
    section(2, "Default mode (False): arrival still zeroes velocity AND snaps HOLD")

    for state in MovementState:
        if state not in ACCEL_BY_STATE:
            continue
        p = _make_player()
        _park(p, state)
        before_pos = (p.x, p.y)

        apply_movement_state(p, state, p.target_x, p.target_y, DT)

        check(f"2.1 [{state.name}] velocity stopped",
              p.vx == 0.0 and p.vy == 0.0,
              f"v=({p.vx}, {p.vy})")
        check(f"2.2 [{state.name}] legacy HOLD snap preserved",
              p.movement_state is MovementState.HOLD,
              f"state={p.movement_state}")
        check(f"2.3 [{state.name}] position untouched",
              (p.x, p.y) == before_pos,
              f"pos={before_pos} -> {(p.x, p.y)}")


# ═══════════════════════════════════════════════════════════════════════
# [3] AUTHORITY MODE — preserve_state=True stops velocity, keeps state
# ═══════════════════════════════════════════════════════════════════════

def section_3_authority_mode():
    section(3, "preserve_state=True: arrival stops velocity ONLY")

    for state in MovementState:
        if state not in ACCEL_BY_STATE:
            continue
        p = _make_player()
        _park(p, state)
        before_pos = (p.x, p.y)

        apply_movement_state(
            p, state, p.target_x, p.target_y, DT, preserve_state=True,
        )

        check(f"3.1 [{state.name}] velocity stopped",
              p.vx == 0.0 and p.vy == 0.0,
              f"v=({p.vx}, {p.vy})")
        check(f"3.2 [{state.name}] authored state SURVIVES arrival",
              p.movement_state is state,
              f"state={p.movement_state} expected={state}")
        check(f"3.3 [{state.name}] position untouched",
              (p.x, p.y) == before_pos,
              f"pos={before_pos} -> {(p.x, p.y)}")
# ═══════════════════════════════════════════════════════════════════════
# [4] THE VERIFIED CASE — the kickoff fullback parked on its anchor
# ═══════════════════════════════════════════════════════════════════════

def section_4_kickoff_fullback():
    section(4, "Verified case: kickoff FB parked ON its anchor keeps DRIFT")

    p = _make_player(x=62.0, y=32.0)
    p.fullback_role_active = True
    p.target_source = "fullback_geometry"
    p.movement_state = MovementState.DRIFT
    p.vx = 0.0
    p.vy = 0.0

    states_seen = set()
    owners_seen = set()
    ran_any = False
    for _ in range(240):          # ~4 s of ball flight
        # The flight geometry authority's stamp, re-applied every frame.
        p.target_x = 62.0
        p.target_y = 32.0
        p.target_source = "fullback_geometry"
        p.movement_state = MovementState.DRIFT
        if not apply_fullback_locomotion(p, DT):
            break
        ran_any = True
        states_seen.add(p.movement_state)
        owners_seen.add(p.target_source)

    check("4.1 apply_fullback_locomotion ran",
          ran_any,
          "the shared model refused the actor")
    check("4.2 the FB's state is DRIFT on every flight frame — never HOLD",
          states_seen == {MovementState.DRIFT},
          f"states={sorted(s.name for s in states_seen)}")
    check("4.3 the FB's target_source is still the geometry authority",
          owners_seen == {"fullback_geometry"},
          f"sources={sorted(owners_seen)}")
    check("4.4 the FB stayed parked on its anchor",
          (p.x, p.y) == (62.0, 32.0),
          f"pos=({p.x}, {p.y})")
    check("4.5 the FB's velocity stayed zero",
          p.vx == 0.0 and p.vy == 0.0,
          f"v=({p.vx}, {p.vy})")

    # Contrast: the SAME body through the default path is the old defect.
    q = _make_player(x=62.0, y=32.0)
    q.target_x = 62.0
    q.target_y = 32.0
    q.movement_state = MovementState.DRIFT
    apply_movement_state(q, MovementState.DRIFT, 62.0, 32.0, DT)
    check("4.6 the defect reproduces on the default path (HOLD, not DRIFT)",
          q.movement_state is MovementState.HOLD,
          f"state={q.movement_state}")


# ═══════════════════════════════════════════════════════════════════════
# [5] THE COLLAPSE GUARD STILL WINS IN BOTH MODES
# ═══════════════════════════════════════════════════════════════════════

def section_5_collapse_guard():
    section(5, "Inside the collapse-commit zone the velocity stop is suppressed")

    check("5.1 the commit radius is a live threshold (< 0.8 m)",
          COLLAPSE_COMMIT_RADIUS_M == 0.8 and collapse_commit(0.0) is True,
          f"radius={COLLAPSE_COMMIT_RADIUS_M} commit(0.0)={collapse_commit(0.0)}")

    for preserve in (False, True):
        for state in (MovementState.DRIFT, MovementState.CHASE):
            p = _make_player()
            _park(p, state, vx=1.5, vy=0.4)
            bc = _Ball(p.x, p.y)          # dist_to_bc == 0.0 -> commit zone

            apply_movement_state(
                p, state, p.target_x, p.target_y, DT,
                bc_x=bc.x, bc_y=bc.y,
                preserve_state=preserve,
            )

            label = "preserve=True" if preserve else "default"
            check(f"5.2 [{label}/{state.name}] velocity NOT zeroed in commit zone",
                  p.vx == 1.5 and p.vy == 0.4,
                  f"v=({p.vx}, {p.vy})")
            check(f"5.3 [{label}/{state.name}] state not rewritten by the guard",
                  p.movement_state is state,
                  f"state={p.movement_state}")
# ═══════════════════════════════════════════════════════════════════════
# [6] THE FLAG IS ARRIVAL-ONLY — live motion must be identical
# ═══════════════════════════════════════════════════════════════════════

def section_6_flag_is_arrival_only():
    section(6, "Off-arrival, preserve_state changes NOTHING (motion is intact)")

    if ARRIVAL_EPS != 0.05:
        check("6.0 the arrival band is still 0.05 m", False,
              f"ARRIVAL_EPS={ARRIVAL_EPS}")

    for state in MovementState:
        if state not in ACCEL_BY_STATE:
            continue

        results = []
        for preserve in (False, True):
            p = _make_player(x=40.0, y=30.0)
            p.max_speed_mps = 9.2
            # Target 6 m away: well outside the arrival band.
            p.target_x = 46.0
            p.target_y = 30.0
            p.vx = 0.0
            p.vy = 0.0
            p.movement_state = state

            for _ in range(12):
                apply_movement_state(
                    p, state, p.target_x, p.target_y, DT,
                    preserve_state=preserve,
                )
            results.append((p.vx, p.vy, p.x, p.y, p.movement_state))

        (a_vx, a_vy, a_x, a_y, a_ms), (b_vx, b_vy, b_x, b_y, b_ms) = results
        check(f"6.1 [{state.name}] identical vx after 12 off-arrival frames",
              abs(a_vx - b_vx) <= UFU_NUDGE_MPS,
              f"{a_vx:.6f} vs {b_vx:.6f}")
        check(f"6.2 [{state.name}] identical vy after 12 off-arrival frames",
              abs(a_vy - b_vy) <= UFU_NUDGE_MPS,
              f"{a_vy:.6f} vs {b_vy:.6f}")
        check(f"6.3 [{state.name}] identical position after 12 frames",
              abs(a_x - b_x) < 1e-9 and abs(a_y - b_y) < 1e-9,
              f"({a_x:.6f}, {a_y:.6f}) vs ({b_x:.6f}, {b_y:.6f})")
        check(f"6.4 [{state.name}] state identical in both modes",
              a_ms is b_ms is state,
              f"{a_ms} vs {b_ms}")

    # And the state itself is never rewritten off-arrival in either mode.
    p = _make_player(x=40.0, y=30.0)
    p.target_x = 46.0
    p.target_y = 30.0
    p.movement_state = MovementState.RETREAT
    apply_movement_state(p, MovementState.RETREAT, 46.0, 30.0, DT)
    check("6.5 off-arrival the integrator never rewrites state (even by default)",
          p.movement_state is MovementState.RETREAT,
          f"state={p.movement_state}")


# ═══════════════════════════════════════════════════════════════════════
# [7] THE PACE TABLE IS UNTOUCHED — DRIFT is still priceable
# ═══════════════════════════════════════════════════════════════════════

def section_7_pace_table():
    section(7, "The state is priceable: DRIFT keeps its shared-table pace")

    check("7.1 DRIFT is on the shared speed table",
          MAX_SPEED_BY_STATE.get(MovementState.DRIFT) == 0.4,
          f"scale={MAX_SPEED_BY_STATE.get(MovementState.DRIFT)}")
    check("7.2 HOLD is still the zero-speed row",
          MAX_SPEED_BY_STATE.get(MovementState.HOLD) == 0.0,
          f"scale={MAX_SPEED_BY_STATE.get(MovementState.HOLD)}")

    p = _make_player()
    implied = p.max_speed_mps * MAX_SPEED_BY_STATE[MovementState.DRIFT]
    check("7.3 the parked FB's authored DRIFT implies a live, non-zero pace",
          0.0 < implied <= p.max_defensive_speed_mps,
          f"implied={implied:.3f}")

    # Sanity: an OFF-arrival DRIFT body actually builds that pace, so the
    # preserved label is not a label on a statue.
    q = _make_player(x=20.0, y=30.0)
    q.target_x = 20.0
    q.target_y = 45.0
    q.movement_state = MovementState.DRIFT
    q.vx = 0.0
    q.vy = 0.0
    for _ in range(120):
        apply_movement_state(
            q, MovementState.DRIFT, q.target_x, q.target_y, DT,
            preserve_state=True,
        )
    speed = math.hypot(q.vx, q.vy)
    check("7.4 a moving DRIFT body reaches its 0.4x pace under the flag",
          0.0 < speed <= implied + UFU_NUDGE_MPS,
          f"speed={speed:.4f} ceiling={implied:.4f}")


def main():
    print("=" * 72)
    print("ARRIVAL AUTHORITY BOUNDARY VERIFIER (2026-09-27)")
    print("=" * 72)

    section_1_contract_in_source()
    section_2_legacy_mode()
    section_3_authority_mode()
    section_4_kickoff_fullback()
    section_5_collapse_guard()
    section_6_flag_is_arrival_only()
    section_7_pace_table()

    print("\n" + "=" * 72)
    if FAILURES:
        print("RESULT: FAIL")
        for f in FAILURES:
            print("  -", f)
        return 1

    print("RESULT: PASS")
    print("  - arrival stops velocity and nothing else")
    print("  - preserve_state=True keeps the authored state (every state on")
    print("    the shared table); False keeps the legacy MMDL HOLD snap")
    print("  - apply_fullback_locomotion is the single declared owner")
    print("  - the kickoff FB holds DRIFT on its anchor for the whole flight")
    print("  - the collapse-commit guard still suppresses the stop")
    print("  - off-arrival motion and state are identical in both modes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
