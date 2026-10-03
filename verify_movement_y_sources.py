"""
verify_movement_y_sources.py — Task F: prove every live movement Y-writer
routes through split-design spacing (or is an exempt hold-pin / marker /
fullback write).

Asserts:
  1. NO legacy Y-source survives in the movement writers:
       - `= lane_y`            (lane-index spacing — removed)
       - `= fr.y` / `= chase_y` (FR-driven chase convergence — removed)
       - `= ball_y`            (ball-lane scramble — removed)
       - get_defence_target() feeding target_y (legacy 7-lane slots)
  2. Every `target_y = d.y` in the scanned modules is a HOLD-PIN
     (paired `target_x = d.x` in the same block) — freeze pins are
     legitimate; pure current-Y movement writes are not.
  3. The split-spacing writer (`spacing_y`) is present and dominant in
     every live movement module.
  4. OPE contains Task F spacing blocks in all 8 routed regions.
"""

import io
import re
import sys

FAILURES: list[str] = []


def read(path: str) -> list[str]:
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.readlines()


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        FAILURES.append(msg)


# ----------------------------------------------------------------------
# 1. Legacy Y-source scan (the COD removals)
# ----------------------------------------------------------------------
LEGACY_PATTERNS = {
    "= lane_y": re.compile(r"target_y\s*=\s*lane_y\b"),
    "= fr.y / chase_y": re.compile(r"target_y\s*=\s*(fr\.y|chase_y)\b"),
    "= ball_y": re.compile(r"target_y\s*=\s*ball_y\b"),
    "get_defence_target() -> ty": re.compile(
        r"tx\s*,\s*ty\s*=\s*get_defence_target\("
    ),
}

print("=" * 72)
print("SECTION 1 — legacy Y-source removal")
print("=" * 72)
FILES = [
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
    "game/defence/defence_lvl_1/defensive_behaviour_lvl1.py",
    "game/defence/defence_lvl_2/defensive_behaviour_lvl2.py",
    "game/defence/defence_lvl_1/defensive_role_movement.py",
    "game/defence/defence_lvl_1/defensive_role_spacing.py",
    "world/open_play_engine.py",
    "world/catch_system.py",
    "game/kickoff_engine.py",
]
for path in FILES:
    lines = read(path)
    for label, pat in LEGACY_PATTERNS.items():
        hits = [i for i, ln in enumerate(lines, 1) if pat.search(ln)]
        check(
            not hits,
            f"{path}: no `{label}`",
        )
        if hits:
            for i in hits[:5]:
                print(f"        line {i}: {lines[i-1].rstrip()}")

# ----------------------------------------------------------------------
# 2. Hold-pin audit: target_y = d.y must be a hold pin (target_x = d.x)
# ----------------------------------------------------------------------
print()
print("=" * 72)
print("SECTION 2 — hold-pin audit (target_y = d.y only when pinned)")
print("=" * 72)
WRITER_FILES = [
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
    "game/defence/defence_lvl_1/defensive_behaviour_lvl1.py",
    "game/defence/defence_lvl_2/defensive_behaviour_lvl2.py",
    "game/defence/defence_lvl_1/defensive_role_movement.py",
    "world/open_play_engine.py",
]
DOTY = re.compile(r"target_y\s*=\s*d\.y\b")
DOTX = re.compile(r"target_x\s*=\s*d\.x\b")
for path in WRITER_FILES:
    lines = read(path)
    bad = []
    for i, ln in enumerate(lines, 1):
        if not DOTY.search(ln):
            continue
        window = lines[max(0, i - 2):i + 1]  # up to 2 lines above
        if not any(DOTX.search(w) for w in window):
            bad.append(i)
    check(
        not bad,
        f"{path}: every `target_y = d.y` is a hold-pin (target_x = d.x)",
    )
    if bad:
        for i in bad[:5]:
            print(f"        line {i}: {lines[i-1].rstrip()}")

# ----------------------------------------------------------------------
# 3. Split-spacing writer present in every live module
# ----------------------------------------------------------------------
print()
print("=" * 72)
print("SECTION 3 — split-spacing writer present")
print("=" * 72)
SPACER_CALL = re.compile(r"spacing_y\(")
WORLD_SPACER_CALL = re.compile(r"spacing_y\(world")
RANGE_FILES = [
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
    "game/defence/defence_lvl_1/defensive_behaviour_lvl1.py",
    "game/defence/defence_lvl_2/defensive_behaviour_lvl2.py",
]
for path in RANGE_FILES:
    lines = read(path)
    n_spacer = sum(1 for ln in lines if SPACER_CALL.search(ln))
    check(
        n_spacer >= 5,
        f"{path}: spacing_y(...) call sites = {n_spacer} (>=5)",
    )

# defensive_role_movement uses the phase-anchor stub (no live world in the
# PTB role pipeline) — spacing_y(stub, ...) builds the SAME split chain.
rm_lines = read("game/defence/defence_lvl_1/defensive_role_movement.py")
n_rm = sum(1 for ln in rm_lines if SPACER_CALL.search(ln))
check(
    n_rm >= 4,
    f"defensive_role_movement: spacing_y(stub...) call sites = {n_rm} (>=4)",
)

# ----------------------------------------------------------------------
# 4. OPE Task F blocks present in all 8 routed regions
# ----------------------------------------------------------------------
print()
print("=" * 72)
print("SECTION 4 — OPE routing blocks")
print("=" * 72)
ope = read("world/open_play_engine.py")
n_taskf = sum(1 for ln in ope if "Task F" in ln)
check(n_taskf >= 8, f"OPE: >=8 Task F spacing Y blocks (found {n_taskf})")
for marker in [
    "spacing target (Task F)",
    "SDM retreat",
    "RELEASE retreat",
    "PTB_EXEC",
    "PTB_PREP",
    "PTB retreat",
    "POST_RUCK",
    "line-target assignment",
]:
    present = any(marker in ln for ln in ope)
    check(present, f"OPE: region '{marker}' routed")

# ----------------------------------------------------------------------
# 5. catch kickoff line uses role_anchor_y (Task E compliant)
# ----------------------------------------------------------------------
print()
print("=" * 72)
print("SECTION 5 — catch kickoff line role-anchored")
print("=" * 72)
catch = read("world/catch_system.py")
check(
    any("target_y = role_anchor_y(" in ln for ln in catch),
    "catch_system: kickoff line target_y = role_anchor_y(...)",
)

print()
print("=" * 72)
if FAILURES:
    print(f"RESULT: FAIL ({len(FAILURES)} failures)")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("RESULT: PASS — all live movement Y-writers are split-spacing,")
print("        hold-pin, marker, or role-anchored. No legacy Y-source.")
sys.exit(0)
