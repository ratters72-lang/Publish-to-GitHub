"""Compile + marker check for the width-authority fix. READ-ONLY."""

import io
import py_compile
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

FILES = [
    "game/defence/defence_lvl_1/defensive_tuners.py",
    "game/defence/defence_lvl_1/defensive_split_spacing.py",
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
]

MARKERS = {
    "game/defence/defence_lvl_1/defensive_tuners.py": [
        "max_defender_gap_m",
    ],
    "game/defence/defence_lvl_1/defensive_split_spacing.py": [
        "def lane_gap_m",
        "side_width: float | None = None) -> list:",
        "gap = lane_gap_m(side_width, n_side, tuners)",
        "max_width_short",
        "_side_width_by",
        "side_width=_side_width_by.get(int(side))",
        "side_width=(_sw_r if int(side) == RIGHT else _sw_l)",
        "side_width=(_stub_rw if int(side) == RIGHT else _stub_lw)",
        "left_width, right_width = side_widths(world, tuners, ruck_y=ruck_y)",
    ],
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py": [],
}

BANNED = {
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py": [
        "line_width = governed_width_m(",
    ],
}

failures = []


def main():
    print("=" * 74)
    print("WIDTH-AUTHORITY FIX — COMPILE + MARKER CHECK")
    print("=" * 74)

    print("\n[1] Byte-compile the three changed modules")
    for path in FILES:
        try:
            py_compile.compile(path, doraise=True, cfile=None)
            print(f"  [PASS] {path}")
        except py_compile.PyCompileError as exc:
            print(f"  [FAIL] {path}: {exc}")
            failures.append(f"compile:{path}")

    print("\n[2] Required markers present")
    for path, markers in MARKERS.items():
        with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        for m in markers:
            ok = m in src
            print(f"  [{'PASS' if ok else 'FAIL'}] {path.rsplit('/', 1)[-1]}: {m}")
            if not ok:
                failures.append(f"missing:{path}:{m}")

    print("\n[3] Banned text absent")
    for path, banned in BANNED.items():
        with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        for b in banned:
            ok = b not in src
            print(f"  [{'PASS' if ok else 'FAIL'}] {path.rsplit('/', 1)[-1]}: absent '{b}'")
            if not ok:
                failures.append(f"banned:{path}:{b}")

    print("\n" + "=" * 74)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        for f in failures:
            print(f"   - {f}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
