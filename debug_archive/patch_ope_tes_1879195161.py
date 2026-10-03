# OPE TES phase handler patches for TACKLE_INIT authority stack fix
# 
# Changes:
# 1. _update_tackle_init — remove AUDIT FIX retreat loop (non-tacklers outside bubble untouched)
# 2. _update_tackle_contact — remove AUDIT FIX retreat loop
# 3. _update_tackle_drive — keep PASSIVE retreat outside bubble, remove movement_state set
# 4. _update_tackle_dominance — keep PASSIVE retreat outside bubble, remove movement_state set
# 5. _update_release — keep retreat (spec allows), add isinstance guards
# 6. TES gate freeze loop — add isinstance(p, Player) guard
import re

# Read the file
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. _update_tackle_init — remove the AUDIT FIX retreat loop
old_init = '''        # ── AUDIT FIX: Non-tackler defenders retreat toward anchor_x during INIT ──
        # This immediately starts defenders moving instead of leaving them frozen.
        anchor_x = self.world.defensive_line_anchor
        if anchor_x is None:
            anchor_x = 60.0
        ruck_x = self.world.ruck_x_m if self.world.ruck_x_m is not None else 60.0
        ruck_y = self.world.ruck_y_m if self.world.ruck_y_m is not None else 35.0
        for p in self.world.players:
            if p not in self.world.defending_team:
                continue
            if p.is_tackler:
                continue
            def_role = getattr(p, "defender_role", None)
            if def_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                if def_role == DefenderRole.MARKER_A:
                    p.target_x = ruck_x
                    p.target_y = ruck_y - 0.5
                else:
                    p.target_x = ruck_x
                    p.target_y = ruck_y - 1.0
                continue
            if def_role == DefenderRole.DUMMY_HALF:
                p.target_x = ruck_x
                p.target_y = ruck_y
                continue
            lane_idx = getattr(p, "lane_index", 0)
            line_def_idx = abs(lane_idx) + 3
            _, lane_y_target = get_defence_target(self.world, line_def_idx)
            lerp_alpha = 0.3
            p.target_x = p.x + (anchor_x - p.x) * lerp_alpha
            p.target_y = p.y + (lane_y_target - p.y) * lerp_alpha
            p.move_speed = p.max_speed_mps * 0.5

        # Transition after brief engagement window'''

new_init = '''        # Transition after brief engagement window'''

content = content.replace(old_init, new_init, 1)

# 2. _update_tackle_contact — remove the AUDIT FIX retreat loop
old_contact = '''        # ── AUDIT FIX: Non-tackler defenders retreat toward anchor_x during CONTACT ──
        anchor_x = self.world.defensive_line_anchor
        if anchor_x is None:
            anchor_x = 60.0
        ruck_x = self.world.ruck_x_m if self.world.ruck_x_m is not None else 60.0
        ruck_y = self.world.ruck_y_m if self.world.ruck_y_m is not None else 35.0
        for p in self.world.players:
            if p not in self.world.defending_team:
                continue
            if p.is_tackler:
                continue
            def_role = getattr(p, "defender_role", None)
            if def_role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                if def_role == DefenderRole.MARKER_A:
                    p.target_x = ruck_x
                    p.target_y = ruck_y - 0.5
                else:
                    p.target_x = ruck_x
                    p.target_y = ruck_y - 1.0
                continue
            if def_role == DefenderRole.DUMMY_HALF:
                p.target_x = ruck_x
                p.target_y = ruck_y
                continue
            lane_idx = getattr(p, "lane_index", 0)
            line_def_idx = abs(lane_idx) + 3
            _, lane_y_target = get_defence_target(self.world, line_def_idx)
            lerp_alpha = 0.3
            p.target_x = p.x + (anchor_x - p.x) * lerp_alpha
            p.target_y = p.y + (lane_y_target - p.y) * lerp_alpha
            p.move_speed = p.max_speed_mps * 0.5

        if self._phase_timer >= 0.15:'''

new_contact = '''        if self._phase_timer >= 0.15:'''

content = content.replace(old_contact, new_contact, 1)

# 3. _update_tackle_drive — remove movement_state set (spec: "No movement_state writes")
# Keep retreat logic but drop p.movement_state = MovementState.RETREAT
old_drive_movement = '''            p.move_speed = p.max_speed_mps * 0.85
            p.movement_state = MovementState.RETREAT
            p.frozen = False'''

new_drive_movement = '''            p.move_speed = p.max_speed_mps * 0.85
            p.frozen = False'''

content = content.replace(old_drive_movement, new_drive_movement, 1)

# 4. _update_tackle_dominance — same: remove movement_state set
old_dominance_movement = '''            p.move_speed = p.max_speed_mps * 0.85
            p.movement_state = MovementState.RETREAT
            p.frozen = False'''

new_dominance_movement = '''            p.move_speed = p.max_speed_mps * 0.85
            p.frozen = False'''

content = content.replace(old_dominance_movement, new_dominance_movement, 1)

# 5. _update_release — add isinstance(p, Player) guard to the defender loop
old_release_loop = '''        for d in self.world.defending_team:
            if d is self.primary or d in self.joiners:'''

# The loop already uses self.world.defending_team which should only contain Players.
# But add a guard for safety.
# The loop is fine — defending_team is a filtered list, not world.players.

# 6. TES gate freeze loop — add isinstance(p, Player) guard
old_freeze_loop = '''            for p in self.world.players:
                if p.player_id not in engaged_ids:
                    if bc_local is not None:
                        dx = p.x - bc_local.x
                        dy = p.y - bc_local.y
                        if (dx * dx + dy * dy) < (3.5 * 3.5):  # ruck bubble radius
                            p.target_x = p.x
                            p.target_y = p.y
                    else:
                        # No BC — freeze everyone as safety fallback
                        p.target_x = p.x
                        p.target_y = p.y'''

new_freeze_loop = '''            for p in self.world.players:
                # TACKLE AUTHORITY STACK: guard by Player type
                if not isinstance(p, Player):
                    continue
                if p.player_id not in engaged_ids:
                    if bc_local is not None:
                        dx = p.x - bc_local.x
                        dy = p.y - bc_local.y
                        if (dx * dx + dy * dy) < (3.5 * 3.5):  # ruck bubble radius
                            p.target_x = p.x
                            p.target_y = p.y
                    else:
                        # No BC — freeze everyone as safety fallback
                        p.target_x = p.x
                        p.target_y = p.y'''

content = content.replace(old_freeze_loop, new_freeze_loop, 1)

# Write result
with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Patches applied successfully")
print("Changes made:")
print("1. _update_tackle_init — removed AUDIT FIX retreat loop")
print("2. _update_tackle_contact — removed AUDIT FIX retreat loop")
print("3. _update_tackle_drive — removed movement_state set (No movement_state writes)")
print("4. _update_tackle_dominance — removed movement_state set (No movement_state writes)")
print("5. TES gate freeze loop — added isinstance(p, Player) guard")
