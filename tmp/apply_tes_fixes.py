#!/usr/bin/env python3
"""Apply TES fixes to open_play_engine.py: pre-tackle, 2D drive, broken tackle."""

import re

def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

def main():
    path = 'world/open_play_engine.py'
    content = read_file(path)

    # === FIX 1: Pre-tackle branch in _update_defence() ===
    old_trigger = '''        # ═══════════════════════════════════════════════════════════════════
        # Tackle trigger (preserved from old code)
        # ═══════════════════════════════════════════════════════════════════
        for p in defenders:
            if p is bc:
                continue
            dist = math.hypot(bc.x - p.x, bc.y - p.y)
            if dist < TACKLE_RADIUS_M:
                print(f"[TACKLE] defender {p.player_id} hit BC at dist={dist:.2f}")
                self._start_tackle(p, bc)
                return'''

    new_trigger = '''        # ═══════════════════════════════════════════════════════════════════
        # Tackle trigger with pre-tackle check (MISS / ARM_GRAB / CONTACT)
        # ═══════════════════════════════════════════════════════════════════
        for p in defenders:
            if p is bc:
                continue
            dist = math.hypot(bc.x - p.x, bc.y - p.y)
            if dist < TACKLE_RADIUS_M:
                pre_result = self._check_pre_tackle(bc, p)
                if pre_result == "MISS":
                    print(f"[PRE_TACKLE] MISS - defender {p.player_id} whiffs")
                    continue
                elif pre_result == "ARM_GRAB":
                    print(f"[PRE_TACKLE] ARM_GRAB - defender {p.player_id} grabs, BC slowed")
                    bc.vx *= 0.85
                    bc.vy *= 0.85
                    continue
                else:
                    print(f"[TACKLE] defender {p.player_id} hit BC at dist={dist:.2f}")
                    self._start_tackle(p, bc)
                    return'''

    if old_trigger not in content:
        print("ERROR: old_trigger not found!")
        # Try to find it with different encoding
        idx = content.find('# Tackle trigger (preserved from old code)')
        if idx >= 0:
            print(f"Found marker at {idx}")
            print(repr(content[idx:idx+500]))
        return
    content = content.replace(old_trigger, new_trigger, 1)
    print("FIX 1 applied: pre-tackle trigger")

    # === FIX 2: 2D Drive in _update_tackle_drive() ===
    # Replace first-frame block
    old_drive_init = '''        # First frame of drive: compute all metrics, set up LERP targets
        if self._phase_timer == 0.0:
            compute_dominance(ctx, self.tes_outcome, self.rng)
            compute_drive_distance(ctx, self.tes_outcome, self.rng)
            compute_tackle_duration(ctx, self.tes_outcome, self.rng)
            compute_ptb_time(ctx, self.tes_outcome, self.rng)

            # Compute the absolute target X for BC drive
            ctx.drive_target_x = ctx.drive_start_x + self.tes_outcome.drive_distance_m
            # Initialise elapsed â€” first frame is zero so BC stays at bc_x0
            ctx.drive_elapsed_s = 0.0

            # Snapshot joiner starting positions (addressed by player_id)
            # so each joiner can LERP individually during the drive.
            self._joiner_x0: dict[int, float] = {}
            for j in self.joiners:
                self._joiner_x0[j.player_id] = j.x'''

    new_drive_init = '''        # First frame of drive: compute all metrics, set up LERP targets
        if self._phase_timer == 0.0:
            compute_dominance(ctx, self.tes_outcome, self.rng)
            compute_drive_distance(ctx, self.tes_outcome, self.rng)
            compute_tackle_duration(ctx, self.tes_outcome, self.rng)
            compute_ptb_time(ctx, self.tes_outcome, self.rng)

            # --- 2D DRIVE: decompose drive distance into X/Y components ---
            # Use approach_angle_deg (already computed at TES entry) to give
            # drive a lateral component based on the defender's approach vector.
            drive_magnitude = abs(self.tes_outcome.drive_distance_m)
            approach_rad = math.radians(ctx.approach_angle_deg)
            dir_x = math.cos(approach_rad)
            dir_y = math.sin(approach_rad)

            # Apply team attack direction for X only (Y is absolute, not flipped)
            ctx.drive_target_x = ctx.drive_start_x + drive_magnitude * dir_x * ctx.team_attack_dir
            ctx.drive_target_y = ctx.bc_y0 + drive_magnitude * dir_y

            # Initialise elapsed â€” first frame is zero so BC stays at bc_x0
            ctx.drive_elapsed_s = 0.0

            # Snapshot joiner starting positions (addressed by player_id)
            # so each joiner can LERP individually during the drive.
            self._joiner_x0: dict[int, float] = {}
            for j in self.joiners:
                self._joiner_x0[j.player_id] = j.x'''

    if old_drive_init not in content:
        print("ERROR: old_drive_init not found!")
        idx = content.find('First frame of drive: compute all metrics')
        if idx >= 0:
            print(f"Found marker at {idx}")
            print(repr(content[idx:idx+600]))
        return
    content = content.replace(old_drive_init, new_drive_init, 1)
    print("FIX 2 applied: 2D drive init")

    # === Fix 2b: update LERP to also interpolate Y ===
    old_drive_lerp = '''        # ---- LERP positions ----
        bc.x = ctx.bc_x0 + (ctx.drive_target_x - ctx.bc_x0) * t
        d.x = ctx.def_x0 + ((ctx.drive_target_x - 0.5) - ctx.def_x0) * t
        for j in self.joiners:
            j_x0 = self._joiner_x0.get(j.player_id, j.x)
            j.x = j_x0 + ((ctx.drive_target_x - 0.3) - j_x0) * t'''

    new_drive_lerp = '''        # ---- 2D LERP positions (X + Y) ----
        bc.x = ctx.bc_x0 + (ctx.drive_target_x - ctx.bc_x0) * t
        bc.y = ctx.bc_y0 + (ctx.drive_target_y - ctx.bc_y0) * t
        d.x = ctx.def_x0 + ((ctx.drive_target_x - 0.5) - ctx.def_x0) * t
        # Defender Y also follows approach vector
        d.y = ctx.def_y0 + (ctx.bc_y0 + drive_magnitude * dir_y - ctx.def_y0) * t
        for j in self.joiners:
            j_x0 = self._joiner_x0.get(j.player_id, j.x)
            j.x = j_x0 + ((ctx.drive_target_x - 0.3) - j_x0) * t'''

    if old_drive_lerp not in content:
        print("ERROR: old_drive_lerp not found!")
        idx = content.find('# ---- LERP positions ----')
        if idx >= 0:
            print(f"Found marker at {idx}")
            print(repr(content[idx:idx+500]))
        return
    content = content.replace(old_drive_lerp, new_drive_lerp, 1)
    print("FIX 2b applied: 2D drive LERP")

    # === FIX 3: Broken tackle check + handler ===
    # Add broken tackle check in _update_tackle_contact before transition to DRIVE
    old_contact_check = '''        if self.tes_ctx is not None:
            # Refresh context snapshots
            self.tes_ctx.attacker = _make_ctx(bc)
            self.tes_ctx.defender = _make_ctx(d)
            # Update waveform (TES runs here)
            update_waveform(self.tes_ctx, self.rng)
            # Spec 7.1: Check for break-tackle (BC overpowering defenders)
            if self._check_break_tackle(dt):
                return
            # Check for additional tacklers joining
            self._maybe_add_joiners()'''

    new_contact_check = '''        if self.tes_ctx is not None:
            # Refresh context snapshots
            self.tes_ctx.attacker = _make_ctx(bc)
            self.tes_ctx.defender = _make_ctx(d)
            # Update waveform (TES runs here)
            update_waveform(self.tes_ctx, self.rng)
            # Spec 7.1: Check for break-tackle (BC overpowering defenders)
            if self._check_break_tackle(dt):
                return
            # Check for broken tackle using scoring system
            if not hasattr(self, '_broken_checked_this_contact'):
                compute_dominance(self.tes_ctx, self.tes_outcome, self.rng)
                self._broken_checked_this_contact = True
            if self._check_broken_tackle_scoring(self.tes_ctx, self.tes_outcome):
                self._handle_broken_tackle()
                return
            # Check for additional tacklers joining
            self._maybe_add_joiners()'''

    if old_contact_check not in content:
        print("ERROR: old_contact_check not found!")
        idx = content.find('# Refresh context snapshots')
        if idx >= 0:
            print(f"Found marker at {idx}")
            print(repr(content[idx:idx+600]))
        return
    content = content.replace(old_contact_check, new_contact_check, 1)
    print("FIX 3 applied: broken tackle check in contact")

    # === Add new methods before _check_break_tackle ===
    # Find the _check_break_tackle method and add our new methods before it
    old_start_comment = '''    # -- Tackle initiation ----------------------------------------------'''

    new_methods_block = '''    # -- Tackle initiation ----------------------------------------------

    def _check_pre_tackle(self, bc, defender) -> str:
        """Pre-tackle check: returns 'MISS', 'ARM_GRAB', or 'CONTACT'.

        Evaluates defender approach angle and relative speed to determine
        whether the defender makes solid contact, an arm-grab, or whiffs entirely.
        """
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
        """Check if BC overpowers the defender based on computed dominance.

        Returns True for a broken tackle: BC momentum >> defender momentum
        AND the outcome is WEAK (attacker winning the dominance battle).
        """
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
        """Broken tackle: defender falls, BC continues in OPEN_PLAY.

        No RELEASE, no PTB, no ruck formed.  The BC gets a slight speed
        penalty but remains in open play.  The defender(s) go to ground.
        """
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
        print(f"[BROKEN_TACKLE] BC #{bc.player_id if bc else '?'} breaks tackle - back to OPEN_PLAY")'''

    # We already replaced, let's find the right insertion point
    idx_start_comment = content.find('# -- Tackle initiation ----------------------------------------------')
    idx_check_break = content.find('def _check_break_tackle')
    if idx_start_comment < 0 or idx_check_break < 0:
        print("ERROR: could not find insertion point!")
        return

    # Insert new methods after the comment line, before _check_break_tackle
    # First check if we already applied this
    if '_check_pre_tackle' in content:
        print("FIX 3b methods already present - skipping insertion")
    else:
        # Insert after the disabled approach code, just before _check_break_tackle
        insert_before = '# [STEP-02A-01] _enter_tackle_approach disabled'
        insert_idx = content.find(insert_before)
        if insert_idx < 0:
            print("ERROR: could not find insertion point marker!")
            return
        # Find the end of the disabled block (the blank line after ####)
        after_disabled = content.find('\n\n    def _check_break_tackle', insert_idx)
        if after_disabled < 0:
            print("ERROR: could not find _check_break_tackle for insertion!")
            return

        # Insert our new methods between the end of the disabled block and _check_break_tackle
        gap = content[after_disabled:]
        content = content[:after_disabled] + '\n\n' + new_methods_block + '\n' + gap
        print("FIX 3b applied: new methods inserted")

    write_file(path, content)
    print("All fixes applied successfully!")

if __name__ == '__main__':
    main()
