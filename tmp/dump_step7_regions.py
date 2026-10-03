"""Dump the step-7 apply loop + the owner method of _apply_side_slots."""

import io
import sys

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


def defs(path, lo, hi):
    print("=" * 78)
    print(f"DEFS in {path} between {lo} and {hi}")
    print("=" * 78)
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    for i in range(max(1, lo), min(len(lines), hi) + 1):
        ln = lines[i - 1]
        if ln.lstrip().startswith("def "):
            print(f"{i:6d}| {ln.rstrip()}")
    print()


def main():
    show(DBU, 2244, 2340, "step 7 apply loop")
    show(DBU, 860, 1000, "apply() dispatch")
    defs(DBU, 3433, 4400, "methods after _apply_open_play_unified")


if __name__ == "__main__":
    main()
