"""For each failing verifier, list the project modules it imports so I can tell
whether the failure can possibly be caused by the 2026-09-10 drive-freeze change
(world.rules.movement_contracts / world.open_play_engine /
 game.managers.defence_manager / game.defence.defence_lvl_1.pre_tackle_authority)."""
import os
import re
import sys

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHANGED = (
    "movement_contracts",
    "open_play_engine",
    "defence_manager",
    "pre_tackle_authority",
)

FAILED = [
    "verify_attack_geometry_stability.py",
    "verify_banded_roles.py",
    "verify_bc_steering_stability.py",
    "verify_catch_line_speed.py",
    "verify_collapse_geometry.py",
    "verify_convergence_single_source.py",
    "verify_def_line_ready_audit.py",
    "verify_defensive_line.py",
    "verify_defensive_pipeline.py",
    "verify_defensive_spacing.py",
    "verify_depth_roles.py",
    "verify_edge_release_conversion.py",
    "verify_fb_determinism.py",
    "verify_mmdl_fullback_exclusion.py",
    "verify_movement_chain.py",
    "verify_movement_y_authority.py",
    "verify_movement_y_sources.py",
    "verify_one_a_per_side.py",
    "verify_retreat_fullback_contracts.py",
    "verify_role_width_order.py",
    "verify_ruck_role_system.py",
    "verify_stage3e_transition_ownership.py",
    "verify_tackle_commit.py",
    "verify_task6_chase.py",
    "verify_tes_diagnostics.py",
]

IMP = re.compile(r"^\s*(?:from|import)\s+([A-Za-z0-9_.]+)")


def imports_of(path):
    names = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = IMP.match(line)
            if m:
                names.append(m.group(1))
    return names


def touches_changed(path):
    hits = set()
    # direct imports
    for name in imports_of(path):
        for c in CHANGED:
            if c in name:
                hits.add(name)
    # indirect: engine boot (runs the real engine)
    text = open(path, encoding="utf-8", errors="replace").read()
    for probe in ("OpenPlayEngine(", "run_engine", "run_frames", "from world.open_play_engine",
                  "import world.open_play_engine", "create_world", "build_world"):
        if probe in text:
            hits.add("ENGINE-RUNTIME:" + probe)
    return hits


def main():
    for name in FAILED:
        p = os.path.join(ROOT, name)
        if not os.path.exists(p):
            print(f"{name}: MISSING")
            continue
        hits = sorted(touches_changed(p))
        if hits:
            print(f"{name}: DEPENDS-ON -> {hits}")
        else:
            print(f"{name}: no direct dependency on changed modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
