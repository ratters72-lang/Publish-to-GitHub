"""Throwaway: locate the fullback pre-tackle pool helpers and their signatures."""
import os
import re

TARGETS = (
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
    "game/defence/defence_lvl_1/pretackle_attempt.py",
    "game/defence/defence_lvl_1/pre_tackle_authority.py",
)
DEF = re.compile(r"^\s*def\s+(\w*fullback\w*|\w*tackle_candidate\w*)\s*\((.*)$")


def main() -> None:
    for path in TARGETS:
        print("=" * 78)
        print(path)
        print("=" * 78)
        lines = open(path, encoding="utf-8", errors="replace").readlines()
        for n, line in enumerate(lines, 1):
            m = DEF.match(line)
            if m:
                print(f"{n:5}: {m.group(1)}(")
                print(f"       {m.group(2).rstrip()}")
        print()


if __name__ == "__main__":
    main()
