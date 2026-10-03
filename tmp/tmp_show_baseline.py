"""Temp: show key clause lines from each gate-off baseline (encoding-aware)."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [
    "tmp/base_lane_true.txt",
    "tmp/base_entry_lane.txt",
    "tmp/base_entry_side.txt",
]
MARKERS = (
    "RESULT", "EXIT=", "FAIL", "PASS", "[A]", "[B]", "[C]", "[D]", "[E]",
    "[F", "[V", "violation", "chamber 8", "worst", "V4", "F2", "flip",
    "Traceback", "Error", "check",
)


def read_text(path: str) -> str:
    """Decode a file written by either cmd (ascii) or PS (utf-16)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


for rel in FILES:
    path = os.path.join(ROOT, rel)
    if not os.path.isfile(path):
        print(f"== {rel}: MISSING ==")
        continue
    text = read_text(path)
    lines = [ln.rstrip() for ln in text.splitlines()]
    print(f"== {rel} ({len(lines)} lines) ==")
    shown = 0
    for ln in lines:
        if any(m in ln for m in MARKERS):
            print(ln.encode("ascii", "replace").decode("ascii"))
            shown += 1
    print(f"-- {shown} shown")
    print()
