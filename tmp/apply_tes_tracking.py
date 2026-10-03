"""
Surgical patch for TES Participant Tracking in OpenPlayEngine.

Changes:
  1. Add self.tes_attackers / self.tes_defenders sets in __init__
  2. Populate sets in _start_tackle() after primary+joiners established
  3. In _update_defenders_tes_phases(): skip non-BC attack role suppression when BC not in tes_attackers
  4. In TES phase methods: add participant-scope guard at start
  5. Clear sets in _prepare_release() and _clear_tes_artefacts()
  6. In _update_release() first-frame path
  7. In _reset_tackle_state()

We read the file, apply changes via replace_in_file, then verify syntax.
"""

# Read the current file to understand exact lines
import re

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()
    lines = content.split('\n')

print(f"Total lines: {len(lines)}")

# Find key locations
for i, line in enumerate(lines):
    stripped = line.strip()
    if 'self.tes_outcome: TESOutcome | None = None' in stripped:
        print(f"tes_outcome declaration: line {i+1}")
    if 'self.primary: Player | None = None' in stripped:
        print(f"primary declaration: line {i+1}")
    if 'self.joiners: list[Player] = []' in stripped:
        print(f"joiners declaration: line {i+1}")
    if 'def _start_tackle(self, bc: Player, d: Player) -> None:' in stripped:
        print(f"_start_tackle: line {i+1}")
    if 'self.primary = d' in stripped:
        print(f"self.primary = d: line {i+1}")
    if 'self.joiners.append(j)' in stripped:
        print(f"self.joiners.append: line {i+1}")
    if 'def _clear_tes_artefacts(self)' in stripped:
        print(f"_clear_tes_artefacts: line {i+1}")
    if 'def _prepare_release(self)' in stripped:
        print(f"_prepare_release: line {i+1}")
    if 'def _reset_tackle_state(self)' in stripped:
        print(f"_reset_tackle_state: line {i+1}")
    if 'def _update_release(self' in stripped:
        print(f"_update_release: line {i+1}")
    if 'def _update_defenders_tes_phases(self' in stripped:
        print(f"_update_defenders_tes_phases: line {i+1}")
    if 'def _update_tackle_contact(self' in stripped:
        print(f"_update_tackle_contact: line {i+1}")
    if 'def _update_tackle_drive(self' in stripped:
        print(f"_update_tackle_drive: line {i+1}")
    if 'def _update_tackle_dominance(self' in stripped:
        print(f"_update_tackle_dominance: line {i+1}")
    if 'def _update_tackle_init(self' in stripped:
        print(f"_update_tackle_init: line {i+1}")
    if 'def _handle_broken_tackle(self' in stripped:
        print(f"_handle_broken_tackle: line {i+1}")
    if 'def _check_break_tackle(self' in stripped:
        print(f"_check_break_tackle: line {i+1}")
