"""Temp: print an inclusive line range of a file (1-based).

Usage:  python tmp/tmp_region.py <relpath> <start> <end>
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rel = sys.argv[1]
start = int(sys.argv[2])
end = int(sys.argv[3])


def read_text(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


path = os.path.join(ROOT, rel)
lines = read_text(path).splitlines()
print(f"== {rel} lines {start}-{end} of {len(lines)} ==")
for i in range(start - 1, min(end, len(lines))):
    print(f"{i + 1}: "
          f"{lines[i].rstrip().encode('ascii', 'replace').decode('ascii')}")
