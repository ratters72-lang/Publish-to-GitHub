import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    path = sys.argv[1]
    start = int(sys.argv[2])
    end = int(sys.argv[3])
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            if start <= i <= end:
                print(f"{i}: {line.rstrip()}")


if __name__ == "__main__":
    main()
