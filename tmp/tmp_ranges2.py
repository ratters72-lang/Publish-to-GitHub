"""Throwaway: print the remaining tag-reading sites for the engaged-fullback audit."""
RANGES = [
    ("world/rules/movement_contracts.py", 150, 185),
    ("world/rules/movement_contracts.py", 480, 505),
    ("world/rules/movement_contracts.py", 595, 620),
    ("world/rules/movement_contracts.py", 685, 710),
    ("world/rules/movement_contracts.py", 785, 810),
    ("world/rules/release_controller.py", 100, 130),
    ("player/player.py", 435, 495),
    ("player/player.py", 605, 630),
    ("game/defence/defence_lvl_1/movement_mode_decision.py", 290, 320),
    ("game/defence/defence_lvl_1/movement_mode_decision.py", 595, 660),
    ("game/managers/defence_manager.py", 285, 300),
    ("game/managers/defence_manager.py", 455, 500),
    ("game/managers/defence_manager.py", 620, 640),
    ("game/managers/defence_manager.py", 760, 780),
    ("game/managers/defence_manager.py", 1040, 1075),
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
