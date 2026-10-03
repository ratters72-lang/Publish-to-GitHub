"""Final repair: read file, fix line 1120 dangling print, and extract any misplaced methods."""
import ast

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the syntax error at line 1120+
# The issue is that _check_break_tackle's body was split when we extracted methods
# The dangling line "f\"def_resistance={def_resistance:.1f} x 1.4 -- BREAK!\")" 
# at wrong indentation is the issue

# Find and fix the malformed _check_break_tackle
# Strategy: find the full _check_break_tackle, remove any stray lines after the
# methods were extracted, and replace with the full version

import re

# Find the check_break_tackle method boundaries
idx_break_start = content.find('    def _check_break_tackle')
# Find the next def at same level
next_def = content.find('\n    def ', idx_break_start + 5)
if next_def > 0:
    raw_method = content[idx_break_start:next_def]
else:
    raw_method = content[idx_break_start:]

# Check if the method body is complete - look for 'return False' at end
if 'return False' not in raw_method.split('\n')[-3]:
    print("_check_break_tackle is malformed - replacing")
    
    # The original full method text (from exploration data)
    replacement = """    def _check_break_tackle(self, dt: float) -> bool:
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
        return False
"""
    
    content = content[:idx_break_start] + replacement + content[next_def:]
    print("Replaced malformed _check_break_tackle")

# Now try to parse
try:
    ast.parse(content)
    print("SYNTAX OK!")
    with open('world/open_play_engine.py', 'w', encoding='utf-8') as f:
        f.write(content)
except SyntaxError as e:
    print(f"Still has syntax error at line {e.lineno}: {e.msg}")
    lines = content.split('\n')
    for i in range(max(0, e.lineno-5), min(len(lines), e.lineno+5)):
        indicator = " >>>" if i+1 == e.lineno else "    "
        print(f"{indicator} {i+1}: {repr(lines[i][:100])}")
