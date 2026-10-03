"""Temp: map the clause sites of the three TES verifiers."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [
    "verify_tes_lane_true_retreat.py",
    "verify_tes_entry_lane_geometry.py",
    "verify_tes_entry_side_authority.py",
]
HITS = re.compile(
    r"^\s*(def |class )|F1|F2|F3|V1|V2|V3|V4|V5|\[E\]|clause|tolerance|"
    r"violations\.append|def _chk|MUST|threshold"
)


def read_text(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


for rel in FILES:
    path = os.path.join(ROOT, rel)
    if not os.path.isfile(path):
        print(f"== {rel}: MISSING ==")
        continue
    lines = read_text(path).splitlines()
    print(f"===== {rel} ({len(lines)} lines) =====")
    for i, ln in enumerate(lines):
        if HITS.search(ln):
            text = f"{i + 1}: {ln.rstrip()}"
            print(text.encode("ascii", "replace").decode("ascii"))
    print()
