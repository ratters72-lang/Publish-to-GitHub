import re

content = open('game/defence/defence_lvl_1/defensive_behaviour_unified.py').read()

# Count inverted patterns in target_x assignments (should be 0)
target_minus = re.findall(r'(ball_x|bc_x)\s*-\s*role_offset', content)
chase_minus = re.findall(r'bc_x\s*-\s*\(.*engagement_offset', content)

# Count fixed patterns (should be 4)
target_plus = re.findall(r'(ball_x|bc_x)\s*\+\s*role_offset', content)
chase_plus = re.findall(r'bc_x\s*\+\s*\(.*engagement_offset', content)

# Also check for the passive retreat pattern
passive_old = re.findall(r'ball_x\s*-\s*role_offset\s*-\s*2\.0', content)
passive_new = re.findall(r'ball_x\s*\+\s*role_offset\s*\+\s*2\.0', content)

print(f'INVERTED (ball_x - role_offset):         {len(target_minus)} — {target_minus}')
print(f'INVERTED (bc_x - engagement):            {len(chase_minus)} — {chase_minus}')
print(f'INVERTED (ball_x - role_offset - 2):     {len(passive_old)}')
print()
print(f'CORRECT  (ball_x + role_offset):         {len(target_plus)} — {target_plus}')
print(f'CORRECT  (bc_x + engagement):            {len(chase_plus)} — {chase_plus}')
print(f'CORRECT  (ball_x + role_offset + 2):     {len(passive_new)}')
print()
print('PASS' if not (target_minus or chase_minus or passive_old) else 'FAIL - inverted patterns remain')
