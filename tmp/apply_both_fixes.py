with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    data = f.read()

# Fix 1: Add retreat enforcement after ruck_x = self.world.ruck_x_m
original1 = "        self._phase_timer += dt\n        ruck_x = self.world.ruck_x_m"
replacement1 = """        self._phase_timer += dt
        ruck_x = self.world.ruck_x_m

        # -- RETREAT ENFORCEMENT: all DEF_RETREAT_10 must retreat to ruck_x + 10 --
        retreat_target_x = ruck_x + 10.0
        for d in self.world.defending_team:
            if d.defender_role == DefenderRole.DEF_RETREAT_10:
                d.target_x = retreat_target_x
                d.target_y = d.y
                d.move_speed = d.max_speed_mps * 0.6
                d.update(dt)
"""

if original1 in data:
    data = data.replace(original1, replacement1, 1)
    print("Fix 1 applied: retreat enforcement")
else:
    print("Fix 1 FAILED: pattern not found")

# Fix 2: Add else clause to marker freeze loop to re-enable collision on non-markers
original2 = """                else:
                    # Freeze expired: restore everything
                    d.marker_freeze_timer = 0.0
                    d.apply_physics_forces = True
                    d.body.collision_enabled = True
                    d.defender_role = DefenderRole.DEF_LINE_READY"""

replacement2 = """                else:
                    # Freeze expired: restore everything
                    d.marker_freeze_timer = 0.0
                    d.apply_physics_forces = True
                    d.body.collision_enabled = True
                    d.defender_role = DefenderRole.DEF_LINE_READY
            else:
                # INVARIANT: non-marker defenders must NEVER have collision disabled.
                # The DH depends on contact friction/push-off from all defenders.
                # Stale collision_enabled=False from previous phases leaks into
                # POST_RUCK, making the DH floaty and slow.
                d.apply_physics_forces = True
                d.body.collision_enabled = True"""

if original2 in data:
    data = data.replace(original2, replacement2, 1)
    print("Fix 2 applied: non-marker collision enforcement")
else:
    print("Fix 2 FAILED: pattern not found")

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.write(data)

print("File written")
