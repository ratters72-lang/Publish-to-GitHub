"""Determine which width-consuming code paths are LIVE in the dispatched
defensive pipeline (read-only)."""

import io
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

DBU = "game/defence/defence_lvl_1/defensive_behaviour_unified.py"


def show(path, lo, hi, label=""):
    print("=" * 78)
    print(f"{path}  [{lo}-{hi}]  {label}")
    print("=" * 78)
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    for i in range(max(1, lo), min(len(lines), hi) + 1):
        print(f"{i:6d}| {lines[i - 1].rstrip()}")
    print()


def grep(path, patterns, label=""):
    print("=" * 78)
    print(f"GREP {path}  {label}")
    print("=" * 78)
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, ln in enumerate(fh, 1):
            for p in patterns:
                if p in ln:
                    print(f"{i:6d}| {ln.rstrip()}")
                    break
    print()


def main():
    # 1. Dispatch: which apply path is used?
    grep(DBU, [
        "def apply(", "def _apply_", "use_structured_open_play",
        "self._apply_structured_open_play", "self._apply_open_play_unified",
        "def_role_spacing", "line_width", "half_w", "lane_y",
    ], "dispatch + width locals")

    # 2. Is `line_width` used after each assignment?
    for lo, hi, tag in ((1507, 1600, "pursuit"),
                        (2348, 2440, "line-break collapse"),
                        (2570, 2680, "close")):
        show(DBU, lo, hi, tag)

    # 3. _apply_side_slots containing method
    grep(DBU, ["def _apply_even_side_slots", "def _even_", "_apply_side_slots("],
         "side-slot owner")


if __name__ == "__main__":
    main()
