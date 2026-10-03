"""Temp: dump the TES slot census + the two entry verifiers' clause code."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = [
    ("game/defence/defence_lvl_1/tes_drive_structure.py",
     ("def _census_side_ladder", "def establish_slot_ownership",
      "def slot_ladder_for", "def owned_slot_y", "def sync_tes_drive_chamber")),
    ("verify_tes_entry_lane_geometry.py",
     ("def _match_error", "def _chamber_entries")),
    ("verify_tes_entry_side_authority.py",
     ("def _collect",)),
]


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


for rel, anchors in TARGETS:
    path = os.path.join(ROOT, rel)
    lines = read_text(path)
    print(f"########## {rel} ({len(lines)} lines) ##########")
    for anchor in anchors:
        start = None
        for i, ln in enumerate(lines):
            if ln.startswith(anchor):
                start = i
                break
        if start is None:
            print(f"---- {anchor}: NOT FOUND")
            continue
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j].startswith("def ") or lines[j].startswith("class "):
                end = j
                break
        print(f"---- {anchor} (lines {start + 1}-{end})")
        for i in range(start, min(end, start + 120)):
            print(f"{i + 1}: "
                  f"{lines[i].rstrip().encode('ascii', 'replace').decode('ascii')}")
        print()
