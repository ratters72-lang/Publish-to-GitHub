"""Temp: print the key marker lines plus the tail of a run log.

Usage:  python tmp/tmp_key.py <logfile> [tail_n]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKERS = (
    "RESULT", "EXIT", "VIOLATION", "[E]", "latch error", "shared-lane",
    "recovery pull", "VIOLATION(S)", "- E:", "Traceback", "Error",
)


def read_text(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


rel = sys.argv[1]
tail_n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
path = os.path.join(ROOT, rel)
lines = read_text(path).splitlines()
print(f"== {rel} ({len(lines)} lines) ==")
seen = set()
for i, ln in enumerate(lines):
    if any(m in ln for m in MARKERS):
        if i in seen:
            continue
        seen.add(i)
        print(f"{i + 1}: {ln.rstrip().encode('ascii', 'replace').decode('ascii')}")
print("-- tail --")
for ln in lines[-tail_n:]:
    print(ln.rstrip().encode("ascii", "replace").decode("ascii"))
