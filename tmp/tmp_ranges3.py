"""Throwaway: print _arm_tackler_role and the OPE reset block in full."""
RANGES = [
    ("world/open_play_engine.py", 3470, 3545),
    ("world/open_play_engine.py", 1040, 1070),
]


def main() -> None:
    for path, start, end in RANGES:
        print("=" * 78)
        print(f"{path}  [{start}..{end}]")
        print("=" * 78)
        lines = open(path, encoding="utf-8", errors="replace").readlines()
        for n in range(start - 1, min(end, len(lines))):
            print(f"{n + 1:5}: {lines[n].rstrip()}")
        print()


if __name__ == "__main__":
    main()
