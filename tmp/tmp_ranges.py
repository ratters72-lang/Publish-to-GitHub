"""Throwaway: print specific line ranges for tag-clearing sites."""
RANGES = [
    ("game/defence/defence_lvl_1/fullback_role.py", 1236, 1345),
    ("game/defence/defence_lvl_1/defensive_behaviour_unified.py", 1400, 1445),
    ("game/defence/defence_lvl_1/defensive_behaviour_unified.py", 3740, 3795),
    ("world/catch_system.py", 462, 490),
    ("world/catch_system.py", 558, 585),
    ("game/managers/defence_manager.py", 1090, 1170),
    ("game/kickoff_engine.py", 540, 560),
    ("game/kickoff_engine.py", 620, 640),
]


def main() -> None:
    for path, start, end in RANGES:
        print("=" * 78)
        print(f"{path}  [{start}..{end}]")
        print("=" * 78)
        try:
            lines = open(path, encoding="utf-8", errors="replace").readlines()
        except OSError as exc:
            print(f"  <unreadable: {exc}>")
            continue
        for n in range(start - 1, min(end, len(lines))):
            print(f"{n + 1:5}: {lines[n].rstrip()}")
        print()


if __name__ == "__main__":
    main()
