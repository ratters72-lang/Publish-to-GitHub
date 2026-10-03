"""
verify_retreat_backpedal_contract.py — THE BACKPEDAL CONTRACT.

CONTRACT UNDER TEST (2026-09-18)
--------------------------------
Retreat has TWO facing regimes separated by ONE threshold,
``retreat_backpedal_max_debt_m``:

    BACKPEDAL     debt inside the threshold
                  the body holds the eyes on the play (ball carrier /
                  ruck) and closes the remaining X deficit by REVERSE
                  MOVEMENT ALONG X — the small-debt compliance shuffle.

    TURN AND RUN  debt clearly beyond the threshold (threshold + the
                  facing hysteresis) the body may turn its back on the
                  play and sprint to the line.

Sections:

  1. THRESHOLD IS FIRST-CLASS AND SINGLE-SOURCED
     the module default and the live tuner both sit inside the contract's
     1-2 m band, the deprecated alias cannot drift from the canonical
     value, and the resolution order is canonical -> alias -> default.

  2. THE BACKPEDAL BAND IS X-DOMINANT AND DISCHARGE-NEUTRAL
     lateral_priority 0.0 (pure reverse travel along X), facing THREAT,
     speed_scale at or above the legacy 0.85 flat command — so the X
     closure that discharges the obligation cannot be slowed — and the
     band edge IS the backpedal threshold.

  3. THE FACING GATE IS A PERMISSION, NOT AN EMERGENCE
     below the threshold the eyes are on the play NO MATTER what the band
     table says; travel facing is only granted at threshold + hysteresis
     and only when the band permits it.  A band that declares THREAT can
     never yield TRAVEL.

  4. THE GATE HAS ITS OWN HYSTERESIS AND A LATCH
     a body hovering inside the hold zone cannot alternate
     backpedal/turn-and-run frame to frame, and a monotone debt sweep
     (the live case: the debt only shrinks inside a run) produces exactly
     ONE facing-policy change.

  5. THE PROFILE + LIFETIME
     the issued profile carries the backpedal verdict; the latch is
     dropped with the gait; the profile stays frame-scoped.

  6. WIRING (source-level, order-sensitive)
     one stamping authority, Player consumes the verdict and pins the
     velocity for the held backpedal, the latch is cleared at kickoff.

Emits verify_retreat_backpedal_report.json; exits 0 when all checks pass.
"""

import ast
import json
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1 import retreat_locomotion as rl
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

LOCO_SRC = "game/defence/defence_lvl_1/retreat_locomotion.py"
TUNERS_SRC = "game/defence/defence_lvl_1/defensive_tuners.py"
MANAGER_SRC = "game/managers/retreat_manager.py"
PLAYER_SRC = "player/player.py"

# The contract's allowed band for the threshold, in metres of X debt.
THRESHOLD_MIN_M = 1.0
THRESHOLD_MAX_M = 2.0

# The legacy flat retreat command.  The backpedal band may not command LESS
# than this, or the X closure that discharges the obligation is slowed —
# the measured 4x discharge regression.
LEGACY_FLAT_SPEED_SCALE = 0.85

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def read_src(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _module_scope_imports(src):
    """Return the modules imported at MODULE scope (top level only).

    Import safety is about module scope: a top-level project import is what
    can create an import cycle between the locomotion module and the
    defensive managers.  A FUNCTION-LOCAL project import (the anchor read)
    is exactly the pattern the contract requires, so it must NOT be counted
    here — and `_function_local_imports` asserts it is still there.
    """
    tree = ast.parse(src)
    modules = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
    return modules


def _function_local_imports(src):
    """Return the modules imported INSIDE function bodies (cycle-safe)."""
    tree = ast.parse(src)
    module_scope = {id(n) for n in tree.body}
    modules = set()
    for node in ast.walk(tree):
        if id(node) in module_scope:
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
    return modules


class _World:
    """Minimal world stub: the gate only needs the frame counter."""

    def __init__(self, frame=0):
        self.frame_count = frame


class _Tuners:
    """Bare namespace tuner source (per-attribute getattr reads)."""

    def __init__(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)


def retreat_body(debt_m, target_x=40.0, pid=1):
    """A defender with exactly *debt_m* of X deficit to its target."""
    d = Player(x=target_x - debt_m, y=35.0, player_id=pid)
    d.defender_role = DefenderRole.DEF_RETREAT_10
    d.depth_class = "PASSIVE"
    d.max_speed_mps = 8.3
    d.target_x = target_x
    return d


# ─────────────────────────────────────────────────────────────────────
# 1. THRESHOLD IS FIRST-CLASS AND SINGLE-SOURCED
# ─────────────────────────────────────────────────────────────────────
def run_section1():
    print("\n[1] The backpedal threshold is first-class and single-sourced")

    default_threshold = rl.DEFAULT_BACKPEDAL_MAX_DEBT_M
    check("the module default sits inside the contract band "
          f"[{THRESHOLD_MIN_M}, {THRESHOLD_MAX_M}] m",
          THRESHOLD_MIN_M <= default_threshold <= THRESHOLD_MAX_M,
          f"default={default_threshold}")

    tuners = DefenceTuners()
    live = float(getattr(tuners, "retreat_backpedal_max_debt_m"))
    check("the live tuner sits inside the contract band",
          THRESHOLD_MIN_M <= live <= THRESHOLD_MAX_M, f"tuner={live}")
    check("the live tuner IS the module default (no second number)",
          abs(live - default_threshold) < 1e-9)

    alias = float(getattr(tuners, "retreat_shuffle_max_debt_m"))
    check("the deprecated alias cannot drift from the canonical value",
          abs(alias - live) < 1e-9, f"alias={alias}")

    check("backpedal_threshold_m reads the live tuner",
          abs(rl.backpedal_threshold_m(tuners) - live) < 1e-9)

    # Resolution order: canonical -> alias -> module default.
    canonical_wins = _Tuners(
        retreat_backpedal_max_debt_m=1.25,
        retreat_shuffle_max_debt_m=9.0,
    )
    check("the canonical name WINS over the alias when both are present",
          abs(rl.backpedal_threshold_m(canonical_wins) - 1.25) < 1e-9,
          f"resolved={rl.backpedal_threshold_m(canonical_wins)}")

    alias_only = _Tuners(retreat_shuffle_max_debt_m=1.75)
    check("the alias is honoured only when the canonical name is absent",
          abs(rl.backpedal_threshold_m(alias_only) - 1.75) < 1e-9,
          f"resolved={rl.backpedal_threshold_m(alias_only)}")

    check("an empty namespace falls back to the module default",
          abs(rl.backpedal_threshold_m(_Tuners()) - default_threshold) < 1e-9)
    check("a None namespace falls back to the module default",
          abs(rl.backpedal_threshold_m(None) - default_threshold) < 1e-9)

    # is_backpedal is inclusive at the threshold and exclusive above it.
    check("is_backpedal is True strictly inside the threshold",
          rl.is_backpedal(live - 0.01, tuners))
    check("is_backpedal is True AT the threshold (inclusive)",
          rl.is_backpedal(live, tuners))
    check("is_backpedal is False just beyond the threshold",
          not rl.is_backpedal(live + 0.01, tuners))

    # A malformed tuner value must not arm the gate with nonsense.
    check("a malformed canonical value does not raise",
          rl.backpedal_threshold_m(_Tuners(
              retreat_backpedal_max_debt_m="not-a-number",
              retreat_shuffle_max_debt_m=1.5,
          )) == 1.5)


# ─────────────────────────────────────────────────────────────────────
# 2. THE BACKPEDAL BAND IS X-DOMINANT AND DISCHARGE-NEUTRAL
# ─────────────────────────────────────────────────────────────────────
def run_section2():
    print("\n[2] The backpedal band is X-dominant and discharge-neutral")

    spec = rl.DEFAULT_MODE_SPECS["BACKPEDAL_SHUFFLE"]
    check("the backpedal band's lateral priority is 0.0 "
          "(pure reverse travel along X)",
          abs(float(spec["lateral_priority"])) < 1e-9,
          f"lateral_priority={spec['lateral_priority']}")
    check("the backpedal band holds the eyes on the play",
          spec["facing_policy"] == rl.FACE_THREAT)
    check("the backpedal band commands at least the legacy flat speed "
          "(so X closure cannot be slowed)",
          float(spec["speed_scale"]) >= LEGACY_FLAT_SPEED_SCALE - 1e-9,
          f"speed_scale={spec['speed_scale']}")

    # The tuner profile dict is the live source and must agree.
    tuners = DefenceTuners()
    live_spec = rl.resolve_mode_spec(rl.RetreatLocomotionMode.BACKPEDAL_SHUFFLE,
                                     tuners)
    check("the LIVE backpedal profile is X-dominant",
          abs(float(live_spec["lateral_priority"])) < 1e-9,
          f"lateral_priority={live_spec['lateral_priority']}")
    check("the LIVE backpedal profile holds the eyes on the play",
          live_spec["facing_policy"] == rl.FACE_THREAT)
    check("the LIVE backpedal profile is discharge-neutral",
          float(live_spec["speed_scale"]) >= LEGACY_FLAT_SPEED_SCALE - 1e-9)

    # The backpedal band edge IS the threshold — no second edge.
    threshold = rl.backpedal_threshold_m(tuners)
    glide_max = float(tuners.retreat_glide_max_debt_m)
    mode_below = rl.threshold_mode(threshold - 0.01, threshold, glide_max)
    mode_at = rl.threshold_mode(threshold, threshold, glide_max)
    mode_above = rl.threshold_mode(threshold + 0.01, threshold, glide_max)
    check("the band edge IS the backpedal threshold (below -> backpedal)",
          mode_below is rl.RetreatLocomotionMode.BACKPEDAL_SHUFFLE)
    check("at the threshold the body is out of the backpedal band",
          mode_at is rl.RetreatLocomotionMode.RETREAT_GLIDE)
    check("just above the threshold the band is the turn-and-run band",
          mode_above is rl.RetreatLocomotionMode.RETREAT_GLIDE)

    # The higher bands may declare travel facing (permission), and the
    # large-debt band must be the full run.
    run_spec = rl.resolve_mode_spec(rl.RetreatLocomotionMode.RETREAT_RUN, tuners)
    check("the deep band declares travel facing and ceiling-ish speed",
          run_spec["facing_policy"] == rl.FACE_TRAVEL
          and float(run_spec["speed_scale"]) >= 0.95)


# ─────────────────────────────────────────────────────────────────────
# 3. THE FACING GATE IS A PERMISSION, NOT AN EMERGENCE
# ─────────────────────────────────────────────────────────────────────
def run_section3():
    print("\n[3] The facing gate is a permission, not an emergence")

    tuners = DefenceTuners()
    threshold = rl.backpedal_threshold_m(tuners)
    hysteresis = float(tuners.retreat_facing_travel_hysteresis_m)
    check("the facing hysteresis is positive (the hold zone exists)",
          hysteresis > 0.0, f"hysteresis={hysteresis}")

    # Below the threshold: THREAT no matter what the band declares.
    d = retreat_body(0.5, pid=10)
    for band_policy in (rl.FACE_THREAT, rl.FACE_TRAVEL):
        policy = rl.resolve_facing_policy(
            d, 0.5, band_policy,
            backpedal_max=threshold, hysteresis=hysteresis,
        )
        check(f"debt < threshold forces THREAT (band said {band_policy})",
              policy == rl.FACE_THREAT, f"policy={policy}")

    # At the threshold but inside the hold zone: the PREVIOUS verdict holds.
    d_hold_threat = retreat_body(threshold + hysteresis * 0.5, pid=11)
    d_hold_threat.retreat_facing_policy = rl.FACE_THREAT
    policy = rl.resolve_facing_policy(
        d_hold_threat, threshold + hysteresis * 0.5, rl.FACE_TRAVEL,
        backpedal_max=threshold, hysteresis=hysteresis,
    )
    check("inside the hold zone a THREAT verdict is kept",
          policy == rl.FACE_THREAT, f"policy={policy}")

    d_hold_travel = retreat_body(threshold + hysteresis * 0.5, pid=12)
    d_hold_travel.retreat_facing_policy = rl.FACE_TRAVEL
    policy = rl.resolve_facing_policy(
        d_hold_travel, threshold + hysteresis * 0.5, rl.FACE_TRAVEL,
        backpedal_max=threshold, hysteresis=hysteresis,
    )
    check("inside the hold zone a TRAVEL verdict is kept",
          policy == rl.FACE_TRAVEL, f"policy={policy}")

    # Beyond the hold zone: travel is granted — but only if the band allows.
    d_far = retreat_body(threshold + hysteresis + 1.0, pid=13)
    policy = rl.resolve_facing_policy(
        d_far, threshold + hysteresis + 1.0, rl.FACE_TRAVEL,
        backpedal_max=threshold, hysteresis=hysteresis,
    )
    check("beyond the threshold + hysteresis, travel facing is permitted",
          policy == rl.FACE_TRAVEL, f"policy={policy}")

    d_far_threat_band = retreat_body(threshold + hysteresis + 1.0, pid=14)
    d_far_threat_band.retreat_facing_policy = rl.FACE_TRAVEL
    policy = rl.resolve_facing_policy(
        d_far_threat_band, threshold + hysteresis + 1.0, rl.FACE_THREAT,
        backpedal_max=threshold, hysteresis=hysteresis,
    )
    check("a band that declares THREAT can never yield TRAVEL",
          policy == rl.FACE_THREAT, f"policy={policy}")

    # The verdict is LATCHED on the body (the hold zone needs memory).
    d_latch = retreat_body(threshold + hysteresis * 0.5, pid=15)
    rl.resolve_facing_policy(
        d_latch, threshold + hysteresis * 0.5, rl.FACE_TRAVEL,
        backpedal_max=threshold, hysteresis=hysteresis,
    )
    check("the gate latches its verdict on the body",
          getattr(d_latch, "retreat_facing_policy", None) == rl.FACE_THREAT)
    check("a body with no prior verdict in the hold zone defaults to THREAT "
          "(never turn away from the play on a guess)",
          getattr(d_latch, "retreat_facing_policy") == rl.FACE_THREAT)

    # End-to-end through the stamp: a sub-threshold obligation is a backpedal.
    world = _World(frame=100)
    d_stamp = retreat_body(live := 0.4, target_x=40.0, pid=16)
    profile = rl.apply_retreat_locomotion(world, d_stamp, tuners,
                                          base_speed=8.3, target_x=40.0)
    check("the stamp issues a profile for a sub-threshold obligation",
          profile is not None)
    check("the issued sub-threshold profile faces the threat",
          profile.faces_threat, f"policy={profile.facing_policy}")
    check("the issued sub-threshold profile is flagged backpedal_active",
          bool(profile.backpedal_active))
    check("the issued sub-threshold profile is X-dominant",
          abs(profile.lateral_priority) < 1e-9)
    check("the body carries the latched verdict after the stamp",
          d_stamp.retreat_facing_policy == rl.FACE_THREAT)


# ─────────────────────────────────────────────────────────────────────
# 4. HYSTERESIS: NO CHATTER, ONE FLIP PER RUN
# ─────────────────────────────────────────────────────────────────────
def run_section4():
    print("\n[4] The gate has its own hysteresis and cannot chatter")

    tuners = DefenceTuners()
    threshold = rl.backpedal_threshold_m(tuners)
    hysteresis = float(tuners.retreat_facing_travel_hysteresis_m)

    # ── 4a. OSCILLATION INSIDE THE HOLD ZONE: ZERO FLIPS ──
    d = retreat_body(threshold, pid=20)
    d.retreat_facing_policy = rl.FACE_TRAVEL
    flips = 0
    prev = d.retreat_facing_policy
    # Sweep between threshold and threshold + hysteresis - epsilon: the
    # classic "hovering at the boundary" case that used to flap.
    for i in range(200):
        debt = threshold + (hysteresis * 0.9 if i % 2 else hysteresis * 0.1)
        policy = rl.resolve_facing_policy(
            d, debt, rl.FACE_TRAVEL,
            backpedal_max=threshold, hysteresis=hysteresis,
        )
        if policy != prev:
            flips += 1
            prev = policy
    check("hovering inside the hold zone produces ZERO facing flips",
          flips == 0, f"flips={flips}")

    # ── 4b. MONOTONE DECREASING DEBT (the live trajectory): ONE FLIP ──
    d2 = retreat_body(6.0, pid=21)
    flips2 = 0
    prev2 = None
    debt = 6.0
    while debt >= 0.0:
        policy = rl.resolve_facing_policy(
            d2, debt, rl.FACE_TRAVEL,
            backpedal_max=threshold, hysteresis=hysteresis,
        )
        if prev2 is not None and policy != prev2:
            flips2 += 1
        prev2 = policy
        debt -= 0.02
    check("a monotone debt run changes the facing verdict EXACTLY once "
          "(sprint back, then turn to face the play)",
          flips2 == 1, f"flips={flips2}")

    # ── 4c. THE FIRST FLIP IS AT OR BELOW THE THRESHOLD ──
    d3 = retreat_body(6.0, pid=22)
    first_threat_debt = None
    debt = 6.0
    while debt >= 0.0:
        policy = rl.resolve_facing_policy(
            d3, debt, rl.FACE_TRAVEL,
            backpedal_max=threshold, hysteresis=hysteresis,
        )
        if policy == rl.FACE_THREAT and first_threat_debt is None:
            first_threat_debt = debt
        debt -= 0.02
    check("the body returns to the backpedal no later than the threshold",
          first_threat_debt is not None
          and first_threat_debt <= threshold + 1e-9,
          f"first_threat_debt={first_threat_debt}")

    # ── 4d. GROWING DEBT TURNS TO RUN NO EARLIER THAN THRESHOLD + HYST ──
    d4 = retreat_body(0.1, pid=23)
    first_travel_debt = None
    debt = 0.1
    while debt <= 8.0:
        policy = rl.resolve_facing_policy(
            d4, debt, rl.FACE_TRAVEL,
            backpedal_max=threshold, hysteresis=hysteresis,
        )
        if policy == rl.FACE_TRAVEL and first_travel_debt is None:
            first_travel_debt = debt
        debt += 0.02
    check("the turn-and-run never engages below threshold + hysteresis",
          first_travel_debt is not None
          and first_travel_debt >= threshold + hysteresis - 1e-9,
          f"first_travel_debt={first_travel_debt}")


# ─────────────────────────────────────────────────────────────────────
# 5. THE PROFILE + LIFETIME
# ─────────────────────────────────────────────────────────────────────
def run_section5():
    print("\n[5] The profile carries the verdict and the latch has a lifetime")

    fields = set(rl.RetreatLocomotion.__dataclass_fields__.keys())
    check("the profile exposes backpedal_active",
          "backpedal_active" in fields, f"fields={sorted(fields)}")
    check("the profile still exposes the facing policy + rate",
          {"facing_policy", "facing_rate_deg_s"}.issubset(fields))

    tuners = DefenceTuners()
    world = _World(frame=200)
    d = retreat_body(0.3, target_x=40.0, pid=30)
    rl.apply_retreat_locomotion(world, d, tuners, base_speed=8.3, target_x=40.0)
    check("the latch is set by the stamp",
          d.retreat_facing_policy == rl.FACE_THREAT)

    rl.clear_retreat_locomotion(d)
    check("clearing the gait clears the backpedal latch "
          "(a new obligation must re-earn the turn-and-run)",
          getattr(d, "retreat_facing_policy", "missing") is None)
    check("clearing the gait clears the frame-scoped profile",
          d.retreat_locomotion is None
          and d.retreat_locomotion_mode is None
          and d.retreat_debt_m is None)

    # The runtime lever restores the legacy navigation AND clears the latch.
    import os
    os.environ["RLM_RETREAT_LOCOMOTION"] = "0"
    d2 = retreat_body(0.3, target_x=40.0, pid=31)
    d2.retreat_facing_policy = rl.FACE_TRAVEL
    profile = rl.apply_retreat_locomotion(world, d2, tuners,
                                          base_speed=8.3, target_x=40.0)
    os.environ.pop("RLM_RETREAT_LOCOMOTION", None)
    check("with the lever off no profile is issued", profile is None)
    check("with the lever off the stale latch is cleared",
          getattr(d2, "retreat_facing_policy", "missing") is None)

    # The report renders the verdict (audit visibility).
    report = rl.format_locomotion_report([d_stamped()])
    check("the locomotion report names the backpedal verdict",
          "face=" in report and "threat" in report, f"report={report}")


def d_stamped():
    """A body carrying a live sub-threshold profile (report rendering)."""
    tuners = DefenceTuners()
    d = retreat_body(0.3, target_x=40.0, pid=32)
    rl.apply_retreat_locomotion(_World(frame=300), d, tuners,
                                base_speed=8.3, target_x=40.0)
    return d


# ─────────────────────────────────────────────────────────────────────
# 6. WIRING
# ─────────────────────────────────────────────────────────────────────
def run_section6():
    print("\n[6] Wiring: one stamping authority, Player consumes the verdict")

    loco = read_src(LOCO_SRC)
    mgr = read_src(MANAGER_SRC)
    player = read_src(PLAYER_SRC)

    module_imports = _module_scope_imports(loco)
    check("retreat_locomotion carries NO module-scope project imports "
          "(import safety for every defensive manager, in any load order)",
          all(not m.startswith(("game.", "world.", "player."))
              for m in module_imports),
          f"module-scope imports={sorted(module_imports)}")
    local_imports = _function_local_imports(loco)
    check("the anchor read stays FUNCTION-LOCAL (single-sourced, cycle-free)",
          "game.defence.defence_lvl_1.anchor_authority" in local_imports,
          f"function-local imports={sorted(local_imports)}")

    check("the retreat manager is the sole stamping authority",
          mgr.count("apply_retreat_locomotion(") == 1
          and mgr.count("clear_retreat_locomotion(") >= 2)

    check("Player consumes faces_threat for the held backpedal",
          "faces_threat" in player)
    check("Player consumes backpedal_active to pin the velocity "
          "(the held facing must not bleed a reverse vx into the closure)",
          "backpedal_active" in player)
    check("Player restores the pre-UFU velocity for the backpedal",
          "_vx_before_ufu" in player and "_vy_before_ufu" in player)
    check("Player carries and clears the facing latch",
          "self.retreat_facing_policy = None" in player)
    check("Player clears the profile at the end of every frame "
          "(frame-scoped gait)",
          "self.retreat_locomotion = None" in player)

    tuners_src = read_src(TUNERS_SRC)
    check("the tuners expose the canonical threshold and the alias",
          "retreat_backpedal_max_debt_m" in tuners_src
          and "retreat_shuffle_max_debt_m" in tuners_src)
    check("the alias is ASSIGNED from the canonical value "
          "(it cannot drift)",
          "self.retreat_shuffle_max_debt_m: float = (\n"
          "            self.retreat_backpedal_max_debt_m\n        )"
          in tuners_src)
    check("the tuners expose the facing hysteresis",
          "retreat_facing_travel_hysteresis_m" in tuners_src)


def main():
    print("=" * 72)
    print("RETREAT BACKPEDAL CONTRACT VERIFICATION")
    print("(small debt -> eyes on the play + reverse travel along X;")
    print(" turn-and-run only past the backpedal threshold)")
    print("=" * 72)

    run_section1()
    run_section2()
    run_section3()
    run_section4()
    run_section5()
    run_section6()

    report = {
        "retreat_backpedal_contract": {
            "all_checks_passed": not failures,
            "failures": failures,
            "section_count": 6,
            "threshold_m": rl.DEFAULT_BACKPEDAL_MAX_DEBT_M,
            "facing_hysteresis_m": rl.DEFAULT_FACING_TRAVEL_HYSTERESIS_M,
            "model": ("debt <= retreat_backpedal_max_debt_m -> backpedal "
                      "(facing the threat, reverse travel along X); "
                      "turn-and-run permitted at "
                      "threshold + retreat_facing_travel_hysteresis_m"),
        }
    }
    report_path = "verify_retreat_backpedal_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport written to {report_path}")

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
