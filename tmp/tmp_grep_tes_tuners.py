import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

pat = re.compile(r"tes_")


def main(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            if pat.search(line):
                print(f"{i}: {line.rstrip()}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print("=== " + p + " ===")
        main(p)
