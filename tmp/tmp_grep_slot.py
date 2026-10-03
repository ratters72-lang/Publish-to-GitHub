import re
import sys

pat = re.compile(r"^(def|class|LEFT|RIGHT|_lane)\b")


def main(path):
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if pat.match(line):
                print(f"{i}: {line.rstrip()}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print("=== " + p + " ===")
        main(p)
