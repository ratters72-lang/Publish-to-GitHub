"""Dump adjust_for_role + method owners after _apply_open_play_unified."""

import io
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

RS = "game/defence/defence_lvl_1/defensive_role_spacing.py"
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


def method_owners(path, lo, hi):
    print("=" * 78)
    print(f"METHODS in {path} between {lo} and {hi}")
    print("=" * 78)
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, ln in enumerate(fh, 1):
            if lo <= i <= hi and (
                ln.startswith("    def ") or ln.startswith("def ")
            ):
                print(f"{i:6d}| {ln.rstrip()}")
    print()


def main():
    show(RS, 198, 330, "adjust_for_role")
    method_owners(DBU, 3400, 4400)


if __name__ == "__main__":
    main()
