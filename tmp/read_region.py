"""Print a numbered line range of a UTF-8 file (temp helper)."""
import sys


def main():
    path = sys.argv[1]
    start = int(sys.argv[2])
    end = int(sys.argv[3])
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    last = min(len(lines), end)
    for i in range(max(1, start), last + 1):
        print(f"{i}:{lines[i - 1].rstrip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
