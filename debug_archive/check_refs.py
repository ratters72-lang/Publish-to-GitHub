with open('world/open_play_engine.py','r', encoding='utf-8') as f:
    c = f.read()
print('_compute_defensive_anchor references:', c.count('_compute_defensive_anchor'))
print('defensive_anchor_x references:', c.count('defensive_anchor_x'))
# Check that the old anchor compute call is gone (should be 0)
# The method definition was removed, but check call sites
import re
calls = re.findall(r'_compute_defensive_anchor\(\)', c)
print(f'Calls to _compute_defensive_anchor(): {len(calls)}')
