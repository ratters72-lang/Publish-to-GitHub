"""Dump specific line regions of width consumers (read-only)."""

import io
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

REGIONS = [
    ("game/defence/defence_lvl_1/defensive_role_spacing.py", 40, 200),
    ("world/world_state.py", 130, 200),
    ("world/world_state.py", 370, 450),
    ("game/defence/defence_lvl_1/defensive_behaviour_unified.py", 1495, 1535),
    ("game/defence/defence_lvl_1/defensive_behaviour_unified.py", 2205, 2245),
    ("game/defence/defence_lvl_1/defensive_behaviour_unified.py", 2340, 2380),
    ("game/defence/defence_lvl_1/defensive_behaviour_unified.py", 2560, 2600),
    ("game/defence/defence_lvl_1/defensive_behaviour_unified.py", 2760, 2800),
    ("game/defence/defence_lvl_1/defensive_behaviour_unified.py", 3195, 3235),
    ("game/defence/defence_lvl_1/defensive_behaviour_unified.py", 4205, 4245),
    ("game/managers/defence_manager.py", 395, 420),
    ("world/open_play_engine.py", 2495, 2525),
    ("world/open_play_engine.py", 2752, 2782),
]


def main():
    for path, lo, hi in REGIONS:
        print("=" * 78)
        print(f"{path}  [{lo}-{hi}]")
        print("=" * 78)
        try:
            with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError as exc:
            print(f"  <cannot open: {exc}>")
            continue
        for i in range(max(1, lo), min(len(lines), hi) + 1):
            print(f"{i:6d}| {lines[i - 1].rstrip()}")
        print()


if __name__ == "__main__":
    main()
