"""
Apply remaining movement authority fixes (Fixes 3-7) to OPE.
"""
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# FIX 3: Remove attack_controller.apply_vector and _move_toward from _update_post_ruck_open_play
# Replace the BC movement section in _update_post_ruck_open_play
old_post_ruck = '''        # ═══════════════════════════════════════════════════════════════
        # BC continues to move under attack controller ═══════════════════
        # ═══════════════════════════════════════════════════════════════
        bc = self.world.ball_carrier
        if bc is not None:
            self.attack_controller.apply_vector(bc, self.world, dt)'''

new_post_ruck = '''        # ═══════════════════════════════════════════════════════════════
        # BC movement is owned by DefenceReactivityAgent during
        # POST_RUCK_OPEN_PLAY -- AttackController MUST NOT run.
        # ═══════════════════════════════════════════════════════════════
        bc = self.world.ball_carrier
        # BC is not moved by AttackController -- DRA handles this phase
        if bc is not None:
            pass'''

assert old_post_ruck in content, "FIX 3: old_post_ruck not found!"
content = content.replace(old_post_ruck, new_post_ruck)

# FIX 4: Remove _move_toward for defenders in _update_post_ruck_open_play
old_move_toward = '''            # ALL non-marker, non-DH defenders get a retreat target every frame.
            # This keeps them moving during the entire POST_RUCK phase
            # instead of standing still when not yet in DEF_RETREAT_10.
            if d.x < target_x:
                _move_toward(d, target_x, d.y, 6.0, dt)'''

new_move_toward = '''            # ALL non-marker, non-DH defenders get a retreat target set by
            # DefenceReactivityAgent. _move_toward is NOT called here --
            # DRA is sole movement owner during POST_RUCK_OPEN_PLAY.'''

assert old_move_toward in content, "FIX 4: _move_toward in post_ruck not found!"
content = content.replace(old_move_toward, new_move_toward)

# FIX 5: Remove P7 retreat blocks from _update_release
old_p7_release = '''        # P7: Continuous retreat for DEF_RETREAT_10 during RELEASE
        _rx = getattr(self.world, "ruck_x_m", 60.0)
        _rt = _rx + RETREAT_COMPLIANCE_DISTANCE_M
        for _d in self.world.defending_team:
            if _d.defender_role == DefenderRole.DEF_RETREAT_10:
                _d.target_x = _rt
                _d.target_y = _d.y
                _d.update(dt)  # P4: apply retreat movement'''

new_no_p7_release = '''        # RELEASE movement is owned by release_controller only.
        # P7 retreat blocks removed -- DEF_RETREAT_10 targets are
        # managed by _enforce_release_freeze() in release_controller.'''

assert old_p7_release in content, "FIX 5: P7 in release not found!"
content = content.replace(old_p7_release, new_no_p7_release)

# FIX 6: Remove P7 retreat blocks from _update_ptb_prep and _update_ptb_exec and _update_ptb
# These all have the same pattern
old_p7_ptb = '''        # P7: Continuous retreat for DEF_RETREAT_10 during PTB_PREP
        _rx = getattr(self.world, "ruck_x_m", 60.0)
        _rt = _rx + RETREAT_COMPLIANCE_DISTANCE_M
        for _d in self.world.defending_team:
            if _d.defender_role == DefenderRole.DEF_RETREAT_10:
                _d.target_x = _rt
                _d.target_y = _d.y
                _d.update(dt)  # P4: apply movement since _update_defence is skipped'''

new_no_p7_ptb = '''        # PTB_PREP movement is owned by PTBManager only.
        # P7 retreat blocks removed -- PTBManager is sole movement owner.'''

assert old_p7_ptb in content, "FIX 6: P7 in ptb_prep not found!"
content = content.replace(old_p7_ptb, new_no_p7_ptb)

# Fix for PTB_EXEC variant (different comment)
old_p7_ptb_exec = '''        # P7: Continuous retreat for DEF_RETREAT_10 during PTB_EXEC
        _rx = getattr(self.world, "ruck_x_m", 60.0)
        _rt = _rx + RETREAT_COMPLIANCE_DISTANCE_M
        for _d in self.world.defending_team:
            if _d.defender_role == DefenderRole.DEF_RETREAT_10:
                _d.target_x = _rt
                _d.target_y = _d.y
                _d.update(dt)  # P4: apply movement since _update_defence is skipped'''

assert old_p7_ptb_exec in content, "FIX 6b: P7 in ptb_exec not found!"
content = content.replace(old_p7_ptb_exec, new_no_p7_ptb)

# Fix for PTB variant
old_p7_ptb_plain = '''        # P7: Continuous retreat for DEF_RETREAT_10 during PTB
        _rx = getattr(self.world, "ruck_x_m", 60.0)
        _rt = _rx + RETREAT_COMPLIANCE_DISTANCE_M
        for _d in self.world.defending_team:
            if _d.defender_role == DefenderRole.DEF_RETREAT_10:
                _d.target_x = _rt'''

new_no_p7_ptb_plain = '''        # PTB movement is owned by PTBManager only.
        # P7 retreat blocks removed -- PTBManager is sole movement owner.
        _rx = getattr(self.world, "ruck_x_m", 60.0)
        _rt = _rx + RETREAT_COMPLIANCE_DISTANCE_M'''

assert old_p7_ptb_plain in content, "FIX 6c: P7 in ptb not found!"
content = content.replace(old_p7_ptb_plain, new_no_p7_ptb_plain)

# FIX 7: Remove attack_controller.apply_vector from _update_ptb
old_ptb_apply = '''            self.attack_controller.apply_vector(dh, self.world, dt)
            # CRITICAL: execute DH physics step to resolve target_x/y into movement.
            # Without this, apply_vector() sets targets but DH never moves.
            dh.update(dt)'''

new_ptb_no_apply = '''            # PTBManager is sole movement owner during PTB phase.
            # AttackController MUST NOT write DH/BC movement here.
            # DH movement resumes when phase transitions to OPEN_PLAY.
            dh.vx = 0.0
            dh.vy = 0.0'''

assert old_ptb_apply in content, "FIX 7: apply_vector in ptb not found!"
content = content.replace(old_ptb_apply, new_ptb_no_apply)

# Write back
with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("All fixes applied successfully!")
print("Verifying...")
# Quick verify: check that the old patterns are gone
for label, old in [
    ("post_ruck_apply", old_post_ruck),
    ("post_ruck_movetoward", old_move_toward),
    ("p7_release", old_p7_release),
    ("p7_ptb_prep", old_p7_ptb),
    ("p7_ptb_exec", old_p7_ptb_exec),
    ("p7_ptb", old_p7_ptb_plain),
    ("ptb_apply", old_ptb_apply),
]:
    if old in content:
        print(f"  WARNING: {label} still present!")
    else:
        print(f"  OK: {label} removed")
