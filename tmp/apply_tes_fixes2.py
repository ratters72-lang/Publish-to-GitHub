#!/usr/bin/env python3
"""Apply TES fixes to open_play_engine.py - robust version using line-based matching."""

def main():
    path = 'world/open_play_engine.py'
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Find exact line numbers for each insertion point
    trigger_start = None
    trigger_end = None
    drive_init_start = None
    drive_init_end = None
    drive_lerp = None
    contact_block_start = None
    contact_block_end = None
    insertion_point = None
    
    for i, line in enumerate(lines):
        # Find tackle trigger block
        if '# Tackle trigger (preserved from old code)' in line:
            trigger_start = i
        if trigger_start is not None and trigger_end is None:
            if 'def _update_defenders_tes_phases' in line:
                trigger_end = i - 1  # blank line before next def
        
        # Find drive init block
        if "First frame of drive: compute all metrics" in line:
            drive_init_start = i
        if drive_init_start is not None and drive_init_end is None:
            if "self._joiner_x0[j.player_id]" in line:
                drive_init_end = i + 1  # next line after
        
        # Find drive LERP block
        if '# ---- LERP positions ----' in line:
            drive_lerp = i
        
        # Find contact block
        if '# Refresh context snapshots' in line:
            contact_block_start = i - 1  # include the 'if' line
        if contact_block_start is not None and contact_block_end is None:
            if 'self._maybe_add_joiners()' in line:
                contact_block_end = i + 1
        
        # Find insertion point for new methods (before _check_break_tackle)
        if 'def _check_break_tackle' in line:
            insertion_point = i - 1  # blank line before
    
    if trigger_start is None:
        print("ERROR: trigger_start not found!")
        return
    if drive_init_start is None:
        print("ERROR: drive_init_start not found!")
        return
    if drive_lerp is None:
        print("ERROR: drive_lerp not found!")
        return
    if contact_block_start is None:
        print("ERROR: contact_block_start not found!")
        return
    if insertion_point is None:
        print("ERROR: insertion_point not found!")
        return
    
    print(f"trigger_start={trigger_start}, trigger_end={trigger_end}")
    print(f"drive_init_start={drive_init_start}, drive_init_end={drive_init_end}")
    print(f"drive_lerp={drive_lerp}")
    print(f"contact_block_start={contact_block_start}, contact_block_end={contact_block_end}")
    print(f"insertion_point={insertion_point}")
    
    # ===== FIX 1: Pre-tackle trigger =====
    old_trigger_lines = lines[trigger_start:trigger_end]
    new_trigger = [
        '        # ═══════════════════════════════════════════════════════════════════\n',
        '        # Tackle trigger with pre-tackle check (MISS / ARM_GRAB / CONTACT)\n',
        '        # ═══════════════════════════════════════════════════════════════════\n',
        '        for p in defenders:\n',
        '            if p is bc:\n',
        '                continue\n',
        '            dist = math.hypot(bc.x - p.x, bc.y - p.y)\n',
        '            if dist < TACKLE_RADIUS_M:\n',
        '                pre_result = self._check_pre_tackle(bc, p)\n',
        '                if pre_result == "MISS":\n',
        '                    print(f"[PRE_TACKLE] MISS - defender {p.player_id} whiffs")\n',
        '                    continue\n',
        '                elif pre_result == "ARM_GRAB":\n',
        '                    print(f"[PRE_TACKLE] ARM_GRAB - defender {p.player_id} grabs, BC slowed")\n',
        '                    bc.vx *= 0.85\n',
        '                    bc.vy *= 0.85\n',
        '                    continue\n',
        '                else:\n',
        '                    print(f"[TACKLE] defender {p.player_id} hit BC at dist={dist:.2f}")\n',
        '                    self._start_tackle(p, bc)\n',
        '                    return\n',
    ]
    lines[trigger_start:trigger_end] = new_trigger
    shift = len(new_trigger) - len(old_trigger_lines)
    
    # Adjust all line references
    def adjust(n, s):
        return n + s if n is not None else None
    
    drive_init_start += shift
    drive_init_end += shift
    drive_lerp += shift
    contact_block_start += shift
    contact_block_end += shift
    insertion_point += shift
    
    print(f"After FIX1: drive_init_start={drive_init_start}, drive_lerp={drive_lerp}, contact_block_start={contact_block_start}, insertion_point={insertion_point}")

    # ===== FIX 2: 2D Drive init =====
    old_drive_lines = lines[drive_init_start:drive_init_end]
    new_drive_init = [
        '        # First frame of drive: compute all metrics, set up LERP targets\n',
        '        if self._phase_timer == 0.0:\n',
        '            compute_dominance(ctx, self.tes_outcome, self.rng)\n',
        '            compute_drive_distance(ctx, self.tes_outcome, self.rng)\n',
        '            compute_tackle_duration(ctx, self.tes_outcome, self.rng)\n',
        '            compute_ptb_time(ctx, self.tes_outcome, self.rng)\n',
        '\n',
        '            # --- 2D DRIVE: decompose drive distance into X/Y components ---\n',
        '            # Use approach_angle_deg (already computed at TES entry) to give\n',
        '            # drive a lateral component based on the defender\'s approach vector.\n',
        '            drive_magnitude = abs(self.tes_outcome.drive_distance_m)\n',
        '            approach_rad = math.radians(ctx.approach_angle_deg)\n',
        '            dir_x = math.cos(approach_rad)\n',
        '            dir_y = math.sin(approach_rad)\n',
        '\n',
        '            # Apply team attack direction for X only (Y is absolute, not flipped)\n',
        '            ctx.drive_target_x = ctx.drive_start_x + drive_magnitude * dir_x * ctx.team_attack_dir\n',
        '            ctx.drive_target_y = ctx.bc_y0 + drive_magnitude * dir_y\n',
        '\n',
        '            # Initialise elapsed --- first frame is zero so BC stays at bc_x0\n',
        '            ctx.drive_elapsed_s = 0.0\n',
        '\n',
        '            # Snapshot joiner starting positions (addressed by player_id)\n',
        '            # so each joiner can LERP individually during the drive.\n',
        '            self._joiner_x0: dict[int, float] = {}\n',
        '            for j in self.joiners:\n',
        '                self._joiner_x0[j.player_id] = j.x\n',
    ]
    lines[drive_init_start:drive_init_end] = new_drive_init
    shift = len(new_drive_init) - len(old_drive_lines)
    drive_lerp += shift
    contact_block_start += shift
    contact_block_end += shift
    insertion_point += shift
    print(f"After FIX2: drive_lerp={drive_lerp}, contact_block_start={contact_block_start}, insertion_point={insertion_point}")

    # ===== FIX 2b: Update drive LERP =====
    old_lerp_lines = lines[drive_lerp:drive_lerp+5]  # 4-5 lines
    new_lerp = [
        '        # ---- 2D LERP positions (X + Y) ----\n',
        '        bc.x = ctx.bc_x0 + (ctx.drive_target_x - ctx.bc_x0) * t\n',
        '        bc.y = ctx.bc_y0 + (ctx.drive_target_y - ctx.bc_y0) * t\n',
        '        d.x = ctx.def_x0 + ((ctx.drive_target_x - 0.5) - ctx.def_x0) * t\n',
        '        # Defender Y also follows approach vector\n',
        '        d.y = ctx.def_y0 + (ctx.bc_y0 + drive_magnitude * dir_y - ctx.def_y0) * t\n',
        '        for j in self.joiners:\n',
        '            j_x0 = self._joiner_x0.get(j.player_id, j.x)\n',
        '            j.x = j_x0 + ((ctx.drive_target_x - 0.3) - j_x0) * t\n',
    ]
    lines[drive_lerp:drive_lerp+len(old_lerp_lines)] = new_lerp
    shift = len(new_lerp) - len(old_lerp_lines)
    contact_block_start += shift
    contact_block_end += shift
    insertion_point += shift
    print(f"After FIX2b: contact_block_start={contact_block_start}, insertion_point={insertion_point}")

    # ===== FIX 3: Broken tackle check in contact =====
    old_contact_lines = lines[contact_block_start:contact_block_end]
    new_contact = [
        '        if self.tes_ctx is not None:\n',
        '            # Refresh context snapshots\n',
        '            self.tes_ctx.attacker = _make_ctx(bc)\n',
        '            self.tes_ctx.defender = _make_ctx(d)\n',
        '            # Update waveform (TES runs here)\n',
        '            update_waveform(self.tes_ctx, self.rng)\n',
        '            # Spec 7.1: Check for break-tackle (BC overpowering defenders)\n',
        '            if self._check_break_tackle(dt):\n',
        '                return\n',
        '            # Check for broken tackle using scoring system\n',
        '            if not hasattr(self, \'_broken_checked_this_contact\'):\n',
        '                compute_dominance(self.tes_ctx, self.tes_outcome, self.rng)\n',
        '                self._broken_checked_this_contact = True\n',
        '            if self._check_broken_tackle_scoring(self.tes_ctx, self.tes_outcome):\n',
        '                self._handle_broken_tackle()\n',
        '                return\n',
        '            # Check for additional tacklers joining\n',
        '            self._maybe_add_joiners()\n',
    ]
    lines[contact_block_start:contact_block_end] = new_contact
    shift = len(new_contact) - len(old_contact_lines)
    insertion_point += shift
    print(f"After FIX3: insertion_point={insertion_point}")

    # ===== FIX 3b: Insert new methods =====
    # Check if already inserted
    already_done = any('def _check_pre_tackle' in line for line in lines)
    if not already_done:
        new_methods = [
            '\n',
            '    def _check_pre_tackle(self, bc, defender) -> str:\n',
            '        """Pre-tackle check: returns \'MISS\', \'ARM_GRAB\', or \'CONTACT\'.\n',
            '\n',
            '        Evaluates defender approach angle and relative speed to determine\n',
            '        whether the defender makes solid contact, an arm-grab, or whiffs entirely.\n',
            '        """\n',
            '        dx = bc.x - defender.x\n',
            '        dy = bc.y - defender.y\n',
            '        dist = math.hypot(dx, dy)\n',
            '        if dist < 0.01:\n',
            '            return "CONTACT"  # too close to miss\n',
            '\n',
            '        # Defender approach direction\n',
            '        def_spd = math.hypot(defender.vx, defender.vy)\n',
            '        if def_spd > 0.1:\n',
            '            def_dx = defender.vx / def_spd\n',
            '            def_dy = defender.vy / def_spd\n',
            '        else:\n',
            '            def_dx = math.cos(defender.facing)\n',
            '            def_dy = math.sin(defender.facing)\n',
            '\n',
            '        # Direction from defender to BC\n',
            '        to_bc_dx = dx / dist\n',
            '        to_bc_dy = dy / dist\n',
            '\n',
            '        # Dot product: 1.0 = head-on, 0.0 = side-on, -1.0 = chasing away\n',
            '        dot = def_dx * to_bc_dx + def_dy * to_bc_dy\n',
            '\n',
            '        # Relative speed: defender must have speed advantage for solid contact\n',
            '        bc_spd = math.hypot(bc.vx, bc.vy)\n',
            '        rel_speed = def_spd - bc_spd * 0.5\n',
            '\n',
            '        # Composite tackle score (0..1)\n',
            '        angle_score = (dot * 0.5 + 0.5)  # 0.0 (worst) to 1.0 (head-on)\n',
            '        speed_score = max(0.0, min(1.0, rel_speed / 10.0))\n',
            '        tackle_score = angle_score * 0.6 + speed_score * 0.4\n',
            '\n',
            '        MISS_THRESHOLD = 0.3\n',
            '        ARM_GRAB_THRESHOLD = 0.6\n',
            '\n',
            '        if tackle_score < MISS_THRESHOLD:\n',
            '            return "MISS"\n',
            '        elif tackle_score < ARM_GRAB_THRESHOLD:\n',
            '            return "ARM_GRAB"\n',
            '        else:\n',
            '            return "CONTACT"\n',
            '\n',
            '    def _check_broken_tackle_scoring(self, ctx, outcome) -> bool:\n',
            '        """Check if BC overpowers the defender based on computed dominance.\n',
            '\n',
            '        Returns True for a broken tackle: BC momentum >> defender momentum\n',
            '        AND the outcome is WEAK (attacker winning the dominance battle).\n',
            '        """\n',
            '        from game.tes.tes_state import TackleResult\n',
            '        bc_mom = ctx.attacker.mass * ctx.attacker.speed\n',
            '        def_mom = ctx.engagement.total_momentum()\n',
            '        if def_mom <= 0:\n',
            '            def_mom = bc_mom * 0.3  # treat as very weak resistance\n',
            '\n',
            '        # Broken if BC momentum > 1.5x defender momentum AND outcome is WEAK\n',
            '        if bc_mom > def_mom * 1.5 and outcome.tackle_result == TackleResult.WEAK:\n',
            '            return True\n',
            '        return False\n',
            '\n',
            '    def _handle_broken_tackle(self):\n',
            '        """Broken tackle: defender falls, BC continues in OPEN_PLAY.\n',
            '\n',
            '        No RELEASE, no PTB, no ruck formed.  The BC gets a slight speed\n',
            '        penalty but remains in open play.  The defender(s) go to ground.\n',
            '        """\n',
            '        bc = self.ball_carrier\n',
            '        d = self.primary\n',
            '\n',
            '        # BC: slight speed penalty, continues in open play\n',
            '        if bc:\n',
            '            bc.vx *= 0.9\n',
            '            bc.vy *= 0.9\n',
            '            bc.tes_locked = False\n',
            '            bc.in_contact = False\n',
            '            bc.anim_state = ""\n',
            '\n',
            '        # Primary defender: falls (frozen for 1 second on ground)\n',
            '        if d:\n',
            '            d.frozen = True\n',
            '            d.freeze_timer = 1.0  # stays on ground for 1s\n',
            '            d.is_tackler = False\n',
            '            d.in_contact = False\n',
            '            d.tes_locked = False\n',
            '            d.anim_state = "FALLEN"\n',
            '\n',
            '        # Joiners released\n',
            '        for j in self.joiners:\n',
            '            j.tes_locked = False\n',
            '            j.in_contact = False\n',
            '            j.is_tackler = False\n',
            '\n',
            '        # Reset TES state\n',
            '        self.tes_ctx = None\n',
            '        self.tes_outcome = None\n',
            '        self.world.tes_active = False\n',
            '        self.primary = None\n',
            '        self.joiners.clear()\n',
            '        # Reset broken-check flag for next tackle cycle\n',
            '        self._broken_checked_this_contact = False\n',
            '        # Clear dominance-computed flag if present\n',
            '        if hasattr(self, \'_dominance_computed_for_broken\'):\n',
            '            del self._dominance_computed_for_broken\n',
            '\n',
            '        # Return to OPEN_PLAY - no RELEASE, no PTB, no ruck\n',
            '        self._set_phase("OPEN_PLAY", GameSubState.OPEN_PLAY, "BROKEN_TACKLE")\n',
            '        print(f"[BROKEN_TACKLE] BC #{bc.player_id if bc else \'?\'} breaks tackle - back to OPEN_PLAY")\n',
        ]
        lines[insertion_point:insertion_point] = new_methods
        print(f"FIX 3b applied: inserted {len(new_methods)} new lines at line {insertion_point}")
    else:
        print("FIX 3b: methods already present, skipping")

    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print("All fixes applied successfully!")

if __name__ == '__main__':
    main()
