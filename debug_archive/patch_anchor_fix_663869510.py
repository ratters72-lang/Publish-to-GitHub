"""Apply all anchor-fix changes to world/open_play_engine.py."""

import re

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

changes = 0

# Change 1: Replace anchor computation block in update()
old_anchor_block = """        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑08 — Compute defensive line anchor every frame
        # ═══════════════════════════════════════════════════════════════
        if self.world.sub_state in (
            GameSubState.OPEN_PLAY,
            GameSubState.POST_PTB_RESET,
            GameSubState.PTB_READY,
            GameSubState.PTB,
        ):
            self.world.defensive_line_anchor = self._compute_defensive_anchor()"""

new_anchor_block = """        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑08 — Compute defensive line anchor every frame
        # ═══════════════════════════════════════════════════════════════
        # NEW STORED-ANCHOR LOGIC (AI_ENGINE_FIX_INSTRUCTIONS):
        #   POST_PTB_RESET: anchor frozen (set once at entry, never changed)
        #   OPEN_PLAY + pursuit: anchor += step
        #   OPEN_PLAY + not pursuit: anchor frozen (structured defence)
        #   PTB_READY/PTB: anchor frozen (pre-PTB, no movement)
        if self.world.sub_state == GameSubState.OPEN_PLAY:
            if self.world.in_pursuit_mode:
                self.defensive_anchor_x += ANCHOR_STEP_M
            # else: structured defence — anchor frozen
            self.world.defensive_line_anchor = self.defensive_anchor_x
        elif self.world.sub_state in (
            GameSubState.POST_PTB_RESET,
            GameSubState.PTB_READY,
            GameSubState.PTB,
        ):
            # Anchor is FROZEN during these phases — synced to world but never updated
            self.world.defensive_line_anchor = self.defensive_anchor_x"""

if old_anchor_block in content:
    content = content.replace(old_anchor_block, new_anchor_block, 1)
    changes += 1
    print(f"Change 1: Anchor computation block replaced.")
else:
    print("ERROR: Could not find old anchor block")
    # Debug: find what's around that area
    idx = content.find('STEP‑03A‑08 — Compute defensive line anchor')
    if idx >= 0:
        print(f"Found marker at {idx}")
        print(repr(content[idx:idx+400]))
    exit(1)

# Change 2: Set frozen anchor in _enter_post_ptb_reset()
old_enter = """        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑08 — Set anchor and rebuild defensive line on PTB reset
        # ═══════════════════════════════════════════════════════════════
        self.world.defensive_line_anchor = self._compute_defensive_anchor()
        self._rebuild_defensive_line()
        self._assign_markers()
        self._assign_dh_defender()"""

new_enter = """        # ═══════════════════════════════════════════════════════════════
        # STEP‑03A‑08 — Set FROZEN anchor on PTB reset
        # ═══════════════════════════════════════════════════════════════
        # AI_ENGINE_FIX: anchor = ruck_x + 10.0, frozen until pursuit reactivates.
        # Do NOT use fr.x or bc.x — anchor must be independent of player positions.
        self.defensive_anchor_x = (self.world.ruck_x_m or 60.0) + 10.0
        self.world.defensive_line_anchor = self.defensive_anchor_x
        self._rebuild_defensive_line()
        self._assign_markers()
        self._assign_dh_defender()"""

if old_enter in content:
    content = content.replace(old_enter, new_enter, 1)
    changes += 1
    print(f"Change 2: _enter_post_ptb_reset anchor set to ruck_x + 10.")
else:
    print("ERROR: Could not find old _enter_post_ptb_reset anchor block")
    exit(1)

# Change 3: Fix _assign_markers() — use ruck_x_m instead of fr.x/bc.x
old_markers = """        fr = self.world.first_receiver
        ruck_x = fr.x if fr is not None else bc.x
        ruck_y = bc.y

        primary = self._nearest_defender_to(ruck_x, ruck_y)"""

new_markers = """        # AI_ENGINE_FIX: Use ruck_x_m (NOT fr.x or bc.x) for marker positioning.
        # Markers must stand at ruck_x, behind tackled player on Y-axis.
        ruck_x = self.world.ruck_x_m
        ruck_y = self.world.ruck_y_m

        primary = self._nearest_defender_to(ruck_x, ruck_y)"""

if old_markers in content:
    content = content.replace(old_markers, new_markers, 1)
    changes += 1
    print(f"Change 3: _assign_markers() uses ruck_x_m.")
else:
    print("ERROR: Could not find old _assign_markers block")
    # Print context
    idx = content.find('def _assign_markers')
    if idx >= 0:
        print(repr(content[idx:idx+400]))
    exit(1)

# Change 4: Fix _assign_dh_defender() — use ruck_x_m instead of fr.x/bc.x
# There are TWO _assign_dh_defender methods. Fix the first one (the OPE method, not the helper).
old_dh_assign = """    def _assign_dh_defender(self) -> None:
        \"\"\"Assign a DUMMY_HALF defender based on FR (fallback to BC) ruck position.

        Nearest defender to ruck_x + 1.0 gets DefenderRole.DUMMY_HALF.
        \"\"\"
        bc = self.world.ball_carrier
        if bc is None:
            return

        fr = self.world.first_receiver
        ruck_x = fr.x if fr is not None else bc.x
        ruck_y = bc.y

        dh_def = self._nearest_defender_to(ruck_x + 1.0, ruck_y)"""

new_dh_assign = """    def _assign_dh_defender(self) -> None:
        \"\"\"Assign a DUMMY_HALF defender based on ruck_x + 1.0.

        AI_ENGINE_FIX: Use ruck_x_m (NOT fr.x or bc.x) for DH defender positioning.
        Nearest defender to ruck_x + 1.0 gets DefenderRole.DUMMY_HALF.
        \"\"\"
        bc = self.world.ball_carrier
        if bc is None:
            return

        ruck_x = self.world.ruck_x_m
        ruck_y = self.world.ruck_y_m

        dh_def = self._nearest_defender_to(ruck_x + 1.0, ruck_y)"""

if old_dh_assign in content:
    content = content.replace(old_dh_assign, new_dh_assign, 1)
    changes += 1
    print(f"Change 4: _assign_dh_defender() uses ruck_x_m.")
else:
    print("ERROR: Could not find old _assign_dh_defender block")
    idx = content.find('def _assign_dh_defender')
    if idx >= 0:
        print(repr(content[idx:idx+500]))
    exit(1)

# Change 5: Fix LAW 5 — only clamp anchor during OPEN_PLAY, not POST_PTB_RESET
old_law5 = """        # LAW 5 — Anchor and ruck_x clamped to BC (only when NOT in pursuit mode)
        if not self.world.in_pursuit_mode and bc is not None:
            if self.world.defensive_line_anchor is not None:
                self.world.defensive_line_anchor = min(
                    self.world.defensive_line_anchor, bc.x + 5.0
                )
            # ruck_x must trail the BC by at least 1.0m
            if self.world.ruck_x_m is not None:
                self.world.ruck_x_m = min(self.world.ruck_x_m, bc.x - 1.0)"""

new_law5 = """        # LAW 5 — Anchor clamped to BC (only during OPEN_PLAY, NOT POST_PTB_RESET)
        # AI_ENGINE_FIX: LAW 5 must NOT modify anchor during POST_PTB_RESET
        # because anchor is frozen at ruck_x + 10.0 during POST_PTB_RESET.
        if not self.world.in_pursuit_mode and bc is not None and self._phase == "OPEN_PLAY":
            if self.world.defensive_line_anchor is not None:
                self.world.defensive_line_anchor = min(
                    self.world.defensive_line_anchor, bc.x + 5.0
                )
        # ruck_x must trail the BC by at least 1.0m (applies always)
        if not self.world.in_pursuit_mode and bc is not None:
            if self.world.ruck_x_m is not None:
                self.world.ruck_x_m = min(self.world.ruck_x_m, bc.x - 1.0)"""

if old_law5 in content:
    content = content.replace(old_law5, new_law5, 1)
    changes += 1
    print(f"Change 5: LAW 5 only clamps anchor during OPEN_PLAY.")
else:
    print("ERROR: Could not find old LAW 5 block")
    idx = content.find('LAW 5')
    if idx >= 0:
        print(repr(content[idx:idx+400]))
    exit(1)

# Change 6: Remove _compute_defensive_anchor() method
old_compute = """    def _compute_defensive_anchor(self) -> float:
        \"\"\"Compute the defensive line anchor X position.

        Aligns to FIRST RECEIVER.x + 10.0 if a first receiver exists,
        otherwise falls back to BC.x + 10.0.
        Returns 0.0 if neither exists (safety fallback).
        \"\"\"
        bc = self.world.ball_carrier
        if bc is None:
            return 0.0

        fr = self.world.first_receiver

        if fr is not None:
            return fr.x + 10.0
        else:
            return bc.x + 10.0

    def _nearest_defender_to"""

new_compute = """    def _nearest_defender_to"""

if old_compute in content:
    content = content.replace(old_compute, new_compute, 1)
    changes += 1
    print(f"Change 6: Removed _compute_defensive_anchor() method.")
else:
    print("ERROR: Could not find _compute_defensive_anchor method")
    idx = content.find('_compute_defensive_anchor')
    if idx >= 0:
        print(repr(content[idx:idx+600]))
    exit(1)

# Change 7: Fix _update_dh_defender_tracking — use ruck_x instead of dh.x
old_dh_track = """        if closest is not None:
            # Shadow the DH: 1m in front, same Y
            closest.target_x = dh.x - 1.0
            closest.target_y = dh.y"""

new_dh_track = """        if closest is not None:
            # AI_ENGINE_FIX: DH defender targets ruck_x (not dh.x which could drift).
            # Mark the DH from ruck position, not from the DH's potentially drifted X.
            closest.target_x = self.world.ruck_x_m
            closest.target_y = self.world.ruck_y_m"""

# Need to be careful — there are TWO similar blocks. The first is in the
# _update_dh_defender_tracking method's helper section. Let's replace the
# specific one inside _update_dh_defender_tracking
# The call at line ~1791 is the main tracking method.
# Let's find the exact block:
idx = content.find('    def _update_dh_defender_tracking')
if idx >= 0:
    # Find the "Shadow the DH" block after it
    inner_idx = content.find('Shadow the DH: 1m in front, same Y', idx)
    if inner_idx >= 0:
        # Find the exact lines
        block_start = content.rfind('        if closest is not None:', inner_idx - 200, inner_idx)
        if block_start >= 0:
            block_end = content.find('\n\n', block_start)
            if block_end < 0:
                block_end = block_start + 200
            old_track = content[block_start:block_end]
            new_track = """        if closest is not None:
            # AI_ENGINE_FIX: DH defender targets ruck_x (not dh.x which could drift).
            # Mark the DH from ruck position, not from the DH's potentially drifted X.
            closest.target_x = self.world.ruck_x_m
            closest.target_y = self.world.ruck_y_m"""
            content = content.replace(old_track, new_track, 1)
            changes += 1
            print(f"Change 7: _update_dh_defender_tracking uses ruck_x_m.")
        else:
            print("ERROR: Could not find 'if closest is not None:' before 'Shadow the DH'")
            exit(1)
    else:
        print("ERROR: Could not find 'Shadow the DH' in _update_dh_defender_tracking")
        exit(1)
else:
    print("ERROR: Could not find _update_dh_defender_tracking method")
    exit(1)

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\nAll {changes} changes applied successfully!")
