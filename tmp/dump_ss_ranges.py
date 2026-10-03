"""Dump the CURRENT defensive_split_spacing.py regions to be edited."""

import io
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

PATH = "game/defence/defence_lvl_1/defensive_split_spacing.py"

RANGES = [
    (290, 336, "side_widths head (count-boss branch)"),
    (336, 412, "side_widths body tail"),
    (1090, 1125, "spacing_y lane calls"),
    (1495, 1560, "decide_side_counts feasibility"),
    (1605, 1660, "side_lanes def + gap"),
    (1660, 1705, "side_lanes body + derive_side_width_m"),
    (1868, 1892, "assign_split_sides band widths"),
    (2030, 2042, "audit winger"),
    (2108, 2145, "compute_targets side loop"),
]


def main():
    with io.open(PATH, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    print(f"TOTAL LINES: {len(lines)}")
    for lo, hi, label in RANGES:
        print("=" * 78)
        print(f"[{lo}-{hi}]  {label}")
        print("=" * 78)
        for i in range(max(1, lo), min(len(lines), hi) + 1):
            print(f"{i:6d}| {lines[i - 1].rstrip()}")
        print()


if __name__ == "__main__":
    main()
