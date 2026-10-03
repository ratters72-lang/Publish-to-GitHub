"""Comprehensive fix: reconstruct _check_break_tackle and place all methods correctly."""

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the misplaced _check_pre_tackle and other methods that were extracted from inside _check_break_tackle
idx_check_pre = content.find('    def _check_pre_tackle')
idx_check_broken = content.find('    def _check_broken_tackle_scoring')
idx_handle_broken = content.find('    def _handle_broken_tackle')
idx_prepare_bc = content.find('    def _prepare_bc_dh_for_tackle')

# Find the next method after _handle_broken_tackle
idx_next_after_handle = content.find('\n    def ', idx_handle_broken + 5)
# But this might find _prepare_bc_dh_for_tackle too early
# Let's find the exact span of misplaced methods
misplaced_span_start = idx_check_pre
# The next method after _handle_broken_tackle - find by looking for "# -- Tackle initiation"
# or "def _start_tackle"
for candidate in ['def _check_break_tackle', 'def _check_pre_tackle', 'def _prepare_bc_dh_for_tackle', '# -- Tackle']:
    idx = content.find(candidate, idx_handle_broken + 5)
    if idx > 0 and idx != idx_check_pre and idx != idx_check_broken and idx != idx_handle_broken:
        # Found the next def or marker after the misplaced block
        idx_next_after_handle = idx
        break

print(f"_check_pre_tackle at {idx_check_pre}")
print(f"_handle_broken_tackle at {idx_handle_broken}")
print(f"Next after handle at {idx_next_after_handle}")

# The misplaced methods are from idx_check_pre to idx_next_after_handle
misplaced_block = content[idx_check_pre:idx_next_after_handle]
print(f"\nMisplaced block ({len(misplaced_block)} chars):")
print(misplaced_block[:200])
print("...")
print(misplaced_block[-200:])

# Now we need to:
# 1. Remove the misplaced block from its current position
# 2. Locate where _check_break_tackle's body was truncated and fix it
# 3. Insert the new methods before _check_break_tackle

# First, find the original _check_break_tackle and see where it was truncated
idx_check_break = content.find('    def _check_break_tackle')
# Find the end of the truncated _check_break_tackle (before misplaced methods were extracted)
# The truncated version ends somewhere between _check_break_tackle and _check_pre_tackle
truncated_tail = content[idx_check_break:idx_check_pre]
last_lines = truncated_tail.split('\n')[-5:]
print(f"\nLast 5 lines of truncated _check_break_tackle:")
for l in last_lines:
    print(f"  {repr(l)}")

# The truncated _check_break_tackle lost its body after the print statement
# We need to reconstruct it - but first let's restore the original by removing 
# the misplaced block from where we put it and re-inserting at the right spot

# Remove misplaced block from where we put it (before _check_break_tackle)
content = content.replace(misplaced_block, '', 1)

# Now find the truncated _check_break_tackle again (shifted)
new_idx_break = content.find('    def _check_break_tackle')
# Find the next def after _check_break_tackle
next_def_after_break = content.find('\n    def ', new_idx_break + 5)
# But this might be _check_pre_tackle or _start_tackle etc
# Let's find what's next by checking
after_break = content[new_idx_break:new_idx_break + 3000]
# Find the end of check_break_tackle - either the misplaced block or another def
# Actually the misplaced block is gone now, so the end is wherever the next def starts
# Let's find the next def more carefully
for end_marker in ['    def _start_tackle', '    def _prepare_bc', '    # -- Tackle']:
    idx_marker = content.find(end_marker, new_idx_break + 5)
    if idx_marker > 0:
        next_def_after_break = idx_marker
        print(f"Found next def marker '{end_marker}' at {idx_marker}")
        break

# _check_break_tackle is incomplete - it ends with a dangling print statement
# We need to reconstruct its full body
# From the original read, the full method was ~30 lines ending with DEF_LINE_READY role assignments
# The complete _check_break_tackle method:

full_check_break_tackle = """    def _check_break_tackle(self, dt: float) -> bool:
        \"\"\"
        Spec 7.1: During TES_DRIVE/CONTACT/DOMINANCE, if BC drive_force or
        speed exceeds defender_resistance threshold:
          - Cancel TES, cancel markers, cancel PTB.
          - Switch to OPEN_PLAY immediately.
          - Former tacklers within chase_radius -> DEF_CHASE.
          - Former tacklers offside -> DEF_RETREAT_10.
        Returns True if break-tackle occurred (TES cancelled).
        \"\"\"
        bc = self.ball_carrier
        if bc is None or self.tes_ctx is None:
            return False
        bc_speed = math.hypot(bc.vx, bc.vy)
        bc_mass = getattr(bc, "mass_kg", 90.0)
        bc_momentum = bc_mass * bc_speed
        total_def_momentum = self.tes_ctx.engagement.total_momentum()
        def_resistance = total_def_momentum if total_def_momentum > 0 else bc_momentum * 0.5
        if bc_momentum > def_resistance * 1.4 or bc_speed > 7.0:
            print(f"[BREAK_TACKLE] BC #{bc.player_id} momentum={bc_momentum:.1f} > "
                  f"def_resistance={def_resistance:.1f} x 1.4 -- BREAK!")
            self.tes_ctx = None
            self.tes_outcome = None
            self.world.tes_active = False
            self.primary = None
            self.joiners = []
            ruck_x = getattr(self.world, "ruck_x_m", bc.x)
            chase_radius = 5.0
            for d in self.world.defending_team:
                if d.defender_role in (DefenderRole.TACKLER_1,
                                       DefenderRole.TACKLER_2,
                                       DefenderRole.TACKLER_3):
                    d.is_tackler = False
                    d.in_contact = False
                    d.tes_locked = False
                    dist_to_bc = math.hypot(d.x - bc.x, d.y - bc.y)
                    if dist_to_bc <= chase_radius:
                        d.defender_role = DefenderRole.DEF_CHASE
                    elif d.x < ruck_x + 10.0:
                        d.defender_role = DefenderRole.DEF_RETREAT_10
                    else:
                        d.defender_role = DefenderRole.DEF_LINE_READY
            for d in self.world.defending_team:
                if d.defender_role == DefenderRole.DEF_RETREAT_10:
                    if d.x >= ruck_x + 10.0:
                        d.defender_role = DefenderRole.DEF_LINE_READY
            self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "BREAK_TACKLE")
            return True
        return False"""

# Replace the truncated method with the full version
truncated_start = content.find('    def _check_break_tackle')
truncated_end = content.find('    def _start_tackle', truncated_start)
full_replacement = full_check_break_tackle + '\n\n    def _start_tackle'
content = content[:truncated_start] + full_replacement + content[truncated_end + len('    def _start_tackle'):]

print(f"\nReplaced truncated _check_break_tackle with full version")

# Now insert the new methods before _check_break_tackle
new_idx_break2 = content.find('    def _check_break_tackle')
# Insert new methods in the blank line before it
insertion_point = content.rfind('\n\n', 0, new_idx_break2) + 1

new_methods = """

    def _check_pre_tackle(self, bc, defender) -> str:
        \"\"\"Pre-tackle check: returns 'MISS', 'ARM_GRAB', or 'CONTACT'.

        Evaluates defender approach angle and relative speed to determine
        whether the defender makes solid contact, an arm-grab, or whiffs entirely.
        \"\"\"
        dx = bc.x - defender.x
        dy = bc.y - defender.y
        dist = math.hypot(dx, dy)
        if dist < 0.01:
            return "CONTACT"  # too close to miss

        # Defender approach direction
        def_spd = math.hypot(defender.vx, defender.vy)
        if def_spd > 0.1:
            def_dx = defender.vx / def_spd
            def_dy = defender.vy / def_spd
        else:
            def_dx = math.cos(defender.facing)
            def_dy = math.sin(defender.facing)

        # Direction from defender to BC
        to_bc_dx = dx / dist
        to_bc_dy = dy / dist

        # Dot product: 1.0 = head-on, 0.0 = side-on, -1.0 = chasing away
        dot = def_dx * to_bc_dx + def_dy * to_bc_dy

        # Relative speed: defender must have speed advantage for solid contact
        bc_spd = math.hypot(bc.vx, bc.vy)
        rel_speed = def_spd - bc_spd * 0.5

        # Composite tackle score (0..1)
        angle_score = (dot * 0.5 + 0.5)  # 0.0 (worst) to 1.0 (head-on)
        speed_score = max(0.0, min(1.0, rel_speed / 10.0))
        tackle_score = angle_score * 0.6 + speed_score * 0.4

        MISS_THRESHOLD = 0.3
        ARM_GRAB_THRESHOLD = 0.6

        if tackle_score < MISS_THRESHOLD:
            return "MISS"
        elif tackle_score < ARM_GRAB_THRESHOLD:
            return "ARM_GRAB"
        else:
            return "CONTACT"

    def _check_broken_tackle_scoring(self, ctx, outcome) -> bool:
        \"\"\"Check if BC overpowers the defender based on computed dominance.

        Returns True for a broken tackle: BC momentum >> defender momentum
        AND the outcome is WEAK (attacker winning the dominance battle).
        \"\"\"
        from game.tes.tes_state import TackleResult
        bc_mom = ctx.attacker.mass * ctx.attacker.speed
        def_mom = ctx.engagement.total_momentum()
        if def_mom <= 0:
            def_mom = bc_mom * 0.3  # treat as very weak resistance

        # Broken if BC momentum > 1.5x defender momentum AND outcome is WEAK
        if bc_mom > def_mom * 1.5 and outcome.tackle_result == TackleResult.WEAK:
            return True
        return False

    def _handle_broken_tackle(self):
        \"\"\"Broken tackle: defender falls, BC continues in OPEN_PLAY.

        No RELEASE, no PTB, no ruck formed.  The BC gets a slight speed
        penalty but remains in open play.  The defender(s) go to ground.
        \"\"\"
        bc = self.ball_carrier
        d = self.primary

        # BC: slight speed penalty, continues in open play
        if bc:
            bc.vx *= 0.9
            bc.vy *= 0.9
            bc.tes_locked = False
            bc.in_contact = False
            bc.anim_state = ""

        # Primary defender: falls (frozen for 1 second on ground)
        if d:
            d.frozen = True
            d.freeze_timer = 1.0  # stays on ground for 1s
            d.is_tackler = False
            d.in_contact = False
            d.tes_locked = False
            d.anim_state = "FALLEN"

        # Joiners released
        for j in self.joiners:
            j.tes_locked = False
            j.in_contact = False
            j.is_tackler = False

        # Reset TES state
        self.tes_ctx = None
        self.tes_outcome = None
        self.world.tes_active = False
        self.primary = None
        self.joiners.clear()
        # Reset broken-check flag for next tackle cycle
        self._broken_checked_this_contact = False
        # Clear dominance-computed flag if present
        if hasattr(self, '_dominance_computed_for_broken'):
            del self._dominance_computed_for_broken

        # Return to OPEN_PLAY - no RELEASE, no PTB, no ruck
        self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "BROKEN_TACKLE")
        print(f"[BROKEN_TACKLE] BC #{bc.player_id if bc else '?'} breaks tackle - back to OPEN_PLAY")\
"""

content = content[:insertion_point] + new_methods + content[insertion_point:]

with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Comprehensive fix applied!")
