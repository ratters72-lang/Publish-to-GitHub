import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REGIONS = [
    ("game/defence/defence_lvl_1/defensive_split_spacing.py", 224, 260),
    ("game/defence/defence_lvl_1/defensive_split_spacing.py", 669, 800),
    ("game/defence/defence_lvl_1/defensive_split_spacing.py", 1169, 1190),
    ("game/defence/defence_lvl_1/defensive_split_spacing.py", 1684, 1900),
]


def main():
    for path, start, end in REGIONS:
        print("=" * 70)
        print(f"FILE {path}  [{start}-{end}]")
        print("=" * 70)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                if start <= i <= end:
                    print(f"{i}: {line.rstrip()}")


if __name__ == "__main__":
    main()
