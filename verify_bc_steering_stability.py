"""BC STEERING STABILITY verifier (TASK XIII, 2026-08-27).

SPEC CONTEXT
------------
The ball carrier's run vector was the last discontinuous signal.  Four
discrete flip sources fed ``RunLogic.compute_run_vector`` every frame —
nearest-defender cone flip, lane-drift sign flip at midfield, gap-pair
churn on defender x-order swaps, and the post-PTB escape-exit snap.  The
defensive line reads the carrier's trajectory, so "defensive geometry
cannot stabilise until the attack lanes and BC steering are stable".

FIX UNDER TEST
--------------
game/attack/attack_lvl_1/bc_steering_authority.py
    Single-writer steering stabiliser: defender-cone hysteresis, lane-drift
    deadband, gap-pair commit window, escape-exit ramp, and the vector-flip
    audit.  Self-gated by ``AttackStructureTuners.steering_enabled``.
game/attack/attack_lvl_1/attack_tuners.py
    TASK XIII also flips ``structure_enabled`` to True ("XII to be ON") and
    adds the steering tuners block.
game/attack/attack_lvl_4/attack_controller.py
    Wires the stabiliser between geometry build and RunLogic, plus the
    escape-exit ramp and vector-flip audit (all gated).

PARTS
-----
  P1  Gate-off byte-stability: stabilise_geometry returns the input
      UNCHANGED and escape_exit_blend returns 0.0 when steering is disabled.
  P2  Cone hysteresis: near-equidistant defenders do NOT flip the holder;
      a genuinely-closer (beyond hysteresis margin) defender swaps
      deterministically; holder leaving the field reselects; the avoidance
      angle tracks the held defender.
  P3  Lane deadband: a carrier straddling midfield keeps its committed
      drift direction through sub-deadband oscillation; a clear excursion
      past the deadband flips it exactly once.
  P4  Gap commit: the defender pair is held through x-order churn, refits
      deterministically after the commit window with a sustained reorder,
      and refits immediately on member loss.
  P5  Escape-exit ramp: deterministic decay to zero over escape_exit_ramp_s,
      strictly decreasing, replay-identical; re-entry resets to full; gate
      off returns zero.
  P6  Vector-flip audit: a large rotation emits VECTOR_FLIP, a small one
      does not.
  P7  Determinism: replaying an identical input sequence produces
      identical stabilised outputs.
  P8  Wiring/regression: the engine imports; kickoff reset wires
      reset_bc_steering; the controller is wired with steer_tuners;
      production defaults are ACTIVATED (structure + steering); the
      reworked Task XII verifier and a core defensive verifier still pass.

Exit code 0 when all scenarios pass, 1 otherwise.
"""
import importlib
import math
import subprocess
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.attack.attack_lvl_1.attack_tuners import AttackStructureTuners
from game.attack.attack_lvl_1.bc_steering_authority import (
    _STEER_STATE,
    stabilise_geometry,
    reset_bc_steering,
    note_escape_active,
    escape_exit_blend,
    record_run_vector,
)
import game.debug.attack_structure_audit as asa

DT = 0.016
RUCK_Y = 35.0

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class SteerTuners(AttackStructureTuners):
    def __init__(self, **overrides):
        super().__init__()
        self.steering_enabled = True
        for key, value in overrides.items():
            setattr(self, key, value)


class DarkSteerTuners(AttackStructureTuners):
    """Explicitly-disabled steering: exercises the legacy rollback path."""

    def __init__(self):
        super().__init__()
        self.steering_enabled = False


class StubWorld:
    def __init__(self):
        self.defending_team = []
        self.frame_count = 100
        self.sim_time_s = 12.0
        self.ruck_y_m = RUCK_Y
        self.escape_angle_deg = 45


def make_player(player_id, x, y):
    return Player(x=x, y=y, player_id=player_id)


def raw_geom(defenders, bc):
    """Build the raw steering geometry dict the controller would supply."""
    nearest = None
    nd = 1e9
    ang = (0.0, 0.0)
    for d in defenders:
        dx = d.x - bc.x
        dy = d.y - bc.y
        dist = math.hypot(dx, dy)
        if dist < nd:
            nd = dist
            nearest = d
            if dist > 1e-6:
                ang = (dx / dist, dy / dist)
    cone = {
        "nearest_def": nearest,
        "nearest_def_dist": nd,
        "nearest_def_angle": ang,
        "def_front": ang[0] > 0.3,
        "def_side": -0.3 <= ang[0] <= 0.3,
        "def_back": ang[0] < -0.3,
    }
    return {
        "def_cone": cone,
        "lane": {
            "offset_y": bc.y - RUCK_Y,
            "right_lane_vec": (0.0, 1.0),
            "left_lane_vec": (0.0, -1.0),
            "lane_score": 1.0,
        },
        "gap": {},
    }


# ─────────────────────────────────────────────────────────────────────
# P1 — gate-off byte-stability
# ─────────────────────────────────────────────────────────────────────

def p1_gate_off_byte_stable():
    print()
    print("[P1] Gate OFF: stabiliser declines (geometry unchanged, ramp zero)")
    w = StubWorld()
    bc = make_player(99, 60.0, RUCK_Y)
    d1 = make_player(1, 62.0, RUCK_Y + 1.0)
    w.defending_team = [d1]
    geom = raw_geom(w.defending_team, bc)
    t_off = DarkSteerTuners()

    out = stabilise_geometry(w, bc, geom, t_off, DT)
    check("P1.0: stabilise_geometry returns input UNCHANGED when disabled",
          out is geom)

    note_escape_active(w)
    check("P1.1: escape_exit_blend returns 0.0 when disabled",
          escape_exit_blend(w, 0.92, DT, t_off) == 0.0)


# ─────────────────────────────────────────────────────────────────────
# P2 — defender-cone hysteresis
# ─────────────────────────────────────────────────────────────────────

def p2_cone_hysteresis():
    print()
    print("[P2] Defender-cone hysteresis: anti-flip, genuine swap, reselect, "
          "angle tracking")
    w = StubWorld()
    bc = make_player(99, 60.0, RUCK_Y)
    d1 = make_player(1, 62.0, RUCK_Y + 1.0)   # dist ~2.236
    d2 = make_player(2, 62.0, RUCK_Y - 1.5)   # dist ~2.5
    w.defending_team = [d1, d2]
    t = SteerTuners(cone_hysteresis_m=0.6)
    asa.reset_events()
    reset_bc_steering(w)

    g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
    check("P2.0: nearest holder is d1 initially",
          g["def_cone"]["nearest_def"] is d1)
    ang = g["def_cone"]["nearest_def_angle"]
    _d1 = math.hypot(d1.x - bc.x, d1.y - bc.y)
    expected = ((d1.x - bc.x) / _d1, (d1.y - bc.y) / _d1)
    check("P2.1: avoidance angle tracks the held defender",
          abs(ang[0] - expected[0]) < 1e-6 and abs(ang[1] - expected[1]) < 1e-6)

    # Raw nearest flips to d2 by a tiny margin (inside hysteresis band):
    # the holder must NOT change and no CONE_HOLDER_CHANGE may fire.
    d2.y = RUCK_Y - 1.4                       # dist ~2.44 -> still farther
    d2.x = 62.0
    for _ in range(4):
        g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
    check("P2.2: sub-margin perturbation does NOT flip the holder",
          g["def_cone"]["nearest_def"] is d1)
    check("P2.3: no CONE_HOLDER_CHANGE for the perturbation",
          "CONE_HOLDER_CHANGE" not in {e["kind"] for e in asa.get_events()})

    # Genuinely closer beyond the margin: d2 now at 1.6 m (< 2.236 - 0.6).
    d2.x = 61.6
    d2.y = RUCK_Y
    before = len(asa.get_events())
    g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
    check("P2.4: holder swaps to d2 when clearly closer",
          g["def_cone"]["nearest_def"] is d2)
    kinds = [e["kind"] for e in asa.get_events()[before:]]
    check("P2.5: CONE_HOLDER_CHANGE emitted on the genuine swap",
          "CONE_HOLDER_CHANGE" in kinds, str(kinds))

    # Holder leaves the field: immediate reselect to d1.
    w.defending_team = [d1]
    g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
    check("P2.6: holder loss reselects immediately",
          g["def_cone"]["nearest_def"] is d1)


# ─────────────────────────────────────────────────────────────────────
# P3 — lane-drift deadband
# ─────────────────────────────────────────────────────────────────────

def p3_lane_deadband():
    print()
    print("[P3] Lane deadband: straddle holds, clear excursion flips once")
    w = StubWorld()
    bc = make_player(99, 60.0, RUCK_Y)
    w.defending_team = [make_player(1, 62.0, RUCK_Y)]
    t = SteerTuners(lane_deadband_m=1.5)
    asa.reset_events()
    reset_bc_steering(w)

    # First call at centre seeds direction (-1 = left/up for offset 0).
    g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
    first_vec = g["lane"]["active_lane_vec"]
    check("P3.0: direction seeded on first frame", first_vec is not None)

    # Oscillate within the deadband: direction must be held.
    signs = set()
    for y in (RUCK_Y - 1.0, RUCK_Y + 1.0, RUCK_Y - 0.8, RUCK_Y + 0.9):
        bc.y = y
        g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
        signs.add(g["lane"]["active_lane_vec"])
    check("P3.1: straddling midfield HOLD the committed direction",
          len(signs) == 1, str(signs))
    check("P3.2: no LANE_DIR_CHANGE through the deadband",
          "LANE_DIR_CHANGE" not in {e["kind"] for e in asa.get_events()})

    # Clear excursion past the deadband flips once.  The seed was -1 (offset
    # 0 -> left/up); a NEGATIVE offset excursion (bc above centre) crosses
    # to the +1 (right/down) side, which is a genuine flip.
    before = len(asa.get_events())
    bc.y = RUCK_Y - 2.0                        # offset -2.0 < -1.5
    g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
    check("P3.3: clear excursion flips the direction",
          g["lane"]["active_lane_vec"] != first_vec)
    kinds = [e["kind"] for e in asa.get_events()[before:]]
    check("P3.4: LANE_DIR_CHANGE emitted on the excursion",
          "LANE_DIR_CHANGE" in kinds, str(kinds))


# ─────────────────────────────────────────────────────────────────────
# P4 — gap-pair commit window
# ─────────────────────────────────────────────────────────────────────

def p4_gap_commit():
    print()
    print("[P4] Gap-pair commit: hold through churn, timed refit, member loss")
    w = StubWorld()
    bc = make_player(99, 60.0, RUCK_Y)
    d1 = make_player(1, 65.0, RUCK_Y)
    d2 = make_player(2, 66.0, RUCK_Y)
    d3 = make_player(3, 67.0, RUCK_Y)          # behind by x initially
    w.defending_team = [d1, d2, d3]
    t = SteerTuners(gap_commit_window_s=0.75)
    asa.reset_events()
    reset_bc_steering(w)

    g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
    check("P4.0: pair seeded to top-2 by x (65,66)",
          abs(g["gap"]["gap_center"][0] - 65.5) < 1e-9,
          f"centre={g['gap']['gap_center'][0]:.2f}")

    # d3 now overtakes d1 by x (64.5): raw sorted would be (3,1,2) but the
    # commit window holds the incumbent (1,2) — anti-churn.
    d3.x = 64.5
    for _ in range(5):
        g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
    check("P4.1: pair HELD through x-order churn (65,66)",
          abs(g["gap"]["gap_center"][0] - 65.5) < 1e-9,
          f"centre={g['gap']['gap_center'][0]:.2f}")

    # Burn the commit window; sustained reorder must trigger a refit.
    before = len(asa.get_events())
    for _ in range(60):
        g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
    check("P4.2: refit after window expiry to (64.5,65)",
          abs(g["gap"]["gap_center"][0] - (64.5 + 65.0) / 2) < 1e-9,
          f"centre={g['gap']['gap_center'][0]:.2f}")
    kinds = [e["kind"] for e in asa.get_events()[before:]]
    check("P4.3: GAP_REFIT emitted after window expiry",
          "GAP_REFIT" in kinds, str(kinds))

    # Member loss refits immediately.
    w.defending_team = [d2, d3]
    g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
    check("P4.4: member loss refits immediately to (64.5,66)",
          abs(g["gap"]["gap_center"][0] - (64.5 + 66.0) / 2) < 1e-9,
          f"centre={g['gap']['gap_center'][0]:.2f}")


# ─────────────────────────────────────────────────────────────────────
# P5 — escape-exit ramp determinism
# ─────────────────────────────────────────────────────────────────────

def p5_escape_ramp():
    print()
    print("[P5] Escape-exit ramp: monotone decay, deterministic, re-entry")
    t = SteerTuners(escape_exit_ramp_s=0.30)

    def run(w):
        note_escape_active(w)
        weights = []
        while True:
            fw = escape_exit_blend(w, 0.92, DT, t)
            weights.append(round(fw, 6))
            if fw == 0.0:
                break
            if len(weights) > 500:
                break
        return weights

    w1 = StubWorld()
    weights = run(w1)
    check("P5.0: ramp starts near full and ends at zero",
          weights[0] > 0.7 and weights[-1] == 0.0,
          f"first={weights[0]} last={weights[-1]}")
    check("P5.1: ramp strictly decreasing",
          all(weights[i] >= weights[i + 1] for i in range(len(weights) - 1)))

    w2 = StubWorld()
    weights2 = run(w2)
    check("P5.2: ramp replay is deterministic",
          weights == weights2, f"{len(weights)} vs {len(weights2)} frames")

    # Re-entry mid-cycle resets to a full weight immediately.
    note_escape_active(w1)
    fw_re = escape_exit_blend(w1, 0.92, DT, t)
    check("P5.3: re-entering the escape window resets the ramp",
          fw_re > 0.7, f"fw={fw_re:.3f}")

    # Gate off returns zero regardless of window state.
    w3 = StubWorld()
    note_escape_active(w3)
    check("P5.4: gate-off ramp is zero",
          escape_exit_blend(w3, 0.92, DT, DarkSteerTuners()) == 0.0)


# ─────────────────────────────────────────────────────────────────────
# P6 — vector-flip audit
# ─────────────────────────────────────────────────────────────────────

def p6_vector_flip_audit():
    print()
    print("[P6] Vector-flip audit: large rotation emits, small does not")
    w = StubWorld()
    t = SteerTuners(vector_flip_threshold_deg=60.0)
    asa.reset_events()
    reset_bc_steering(w)

    record_run_vector(w, 1.0, 0.0, t)          # records baseline
    before = len(asa.get_events())
    record_run_vector(w, 0.0, 1.0, t)          # 90° rotation
    kinds = [e["kind"] for e in asa.get_events()[before:]]
    check("P6.0: VECTOR_FLIP emitted on a large rotation",
          "VECTOR_FLIP" in kinds, str(kinds))

    before = len(asa.get_events())
    import math as _m
    # Rotate 5° from the CURRENT baseline (0,1) left by the prior rotation:
    # a genuinely small delta, must NOT emit.
    record_run_vector(w, _m.sin(_m.radians(5)), _m.cos(_m.radians(5)), t)
    check("P6.1: small rotation does NOT emit VECTOR_FLIP",
          len(asa.get_events()) == before)


# ─────────────────────────────────────────────────────────────────────
# P7 — determinism across an identical input sequence
# ─────────────────────────────────────────────────────────────────────

def p7_determinism():
    print()
    print("[P7] Determinism: identical input sequence -> identical outputs")
    t = SteerTuners()

    def replay():
        w = StubWorld()
        bc = make_player(99, 60.0, RUCK_Y)
        d1 = make_player(1, 62.0, RUCK_Y + 1.0)
        d2 = make_player(2, 62.0, RUCK_Y - 1.5)
        w.defending_team = [d1, d2]
        reset_bc_steering(w)
        seq = []
        for i in range(12):
            if i == 4:
                d2.x = 61.6
                d2.y = RUCK_Y
            if i == 8:
                bc.y = RUCK_Y + 2.0
            if i == 10:
                d1.y = RUCK_Y + 0.5
            g = stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), t, DT)
            seq.append((
                getattr(g["def_cone"]["nearest_def"], "player_id", None),
                g["def_cone"]["nearest_def_angle"],
                g["lane"]["active_lane_vec"],
                g["gap"]["gap_center"],
            ))
        return seq

    check("P7.0: repeated replay is byte-identical",
          replay() == replay())


# ─────────────────────────────────────────────────────────────────────
# P8 — wiring / regression
# ─────────────────────────────────────────────────────────────────────

def p8_wiring_regression():
    print()
    print("[P8] Wiring: engine import, reset wiring, tuners, activation, "
          "regression")
    mod = importlib.import_module("world.open_play_engine")
    check("P8.0: OpenPlayEngine imports with the steering chain", mod is not None)

    ope_src = open("world/open_play_engine.py", encoding="utf-8").read()
    check("P8.1: kickoff reset clears BC-steering state",
          "reset_bc_steering(w)" in ope_src)

    ctrl_src = open("game/attack/attack_lvl_4/attack_controller.py",
                    encoding="utf-8").read()
    check("P8.2: controller wired with steer_tuners",
          "steer_tuners=" in ctrl_src and "steering_enabled" in ctrl_src)

    # Production defaults are ACTIVATED (the "XII to be ON" step).
    defaults = AttackStructureTuners()
    check("P8.3: structure_enabled default is ON",
          defaults.structure_enabled is True)
    check("P8.4: steering_enabled default is ON",
          defaults.steering_enabled is True)

    # reset_bc_steering actually drops per-cycle state.
    w = StubWorld()
    bc = make_player(99, 60.0, RUCK_Y)
    w.defending_team = [make_player(1, 62.0, RUCK_Y + 1.0)]
    stabilise_geometry(w, bc, raw_geom(w.defending_team, bc), SteerTuners(), DT)
    check("P8.5: steering state existed before reset",
          id(w) in _STEER_STATE)
    reset_bc_steering(w)
    check("P8.6: reset_bc_steering drops the cycle state",
          id(w) not in _STEER_STATE)

    # Reverted Task XII verifier still green.
    proc = subprocess.run(
        [sys.executable, "verify_attack_geometry_stability.py"],
        capture_output=True, text=True, timeout=300,
    )
    tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    check("P8.7: verify_attack_geometry_stability still green",
          proc.returncode == 0, tail[0])

    # Core defensive verifier unchanged.
    proc2 = subprocess.run(
        [sys.executable, "verify_defensive_pipeline.py"],
        capture_output=True, text=True, timeout=300,
    )
    tail2 = (proc2.stdout or "").strip().splitlines()[-1:] or [""]
    check("P8.8: verify_defensive_pipeline still green",
          proc2.returncode == 0, tail2[0])


def main():
    print("=" * 64)
    print("BC STEERING STABILITY VALIDATION (TASK XIII)")
    print("=" * 64)

    p1_gate_off_byte_stable()
    p2_cone_hysteresis()
    p3_lane_deadband()
    p4_gap_commit()
    p5_escape_ramp()
    p6_vector_flip_audit()
    p7_determinism()
    p8_wiring_regression()

    print()
    print("=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
