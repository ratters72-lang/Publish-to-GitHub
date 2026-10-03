import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REGIONS = [
    ("game/defence/defence_lvl_1/defensive_split_spacing.py", 1900, 2060),
    ("game/defence/defence_lvl_1/defensive_pipeline.py", 262, 320),
    ("game/defence/defence_lvl_1/defensive_pipeline.py", 320, 440),
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
