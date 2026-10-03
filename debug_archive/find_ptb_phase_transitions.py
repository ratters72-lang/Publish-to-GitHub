with open('world/open_play_engine.py', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Find all _set_phase calls to understand what GameSubState each PTB phase uses
import re

# Find all _set_phase calls
for m in re.finditer(r'_set_phase\([^)]+\)', content):
    print(m.group())

print('---')

# Find the PTB handler definitions and their _set_phase calls
for phase_name in ['ptb_prep', 'ptb_exec', 'ptb_ready', 'ptb(?!_)']:
    pattern = re.compile(r'def _update_' + phase_name + r'\b')
    for m in pattern.finditer(content):
        start = m.start()
        # Find the block - look for _set_phase calls
        block = content[start:start+800]
        print(f'=== {m.group()} ===')
        for line in block.split('\n'):
            if '_set_phase' in line or 'GameSubState' in line:
                print(f'  {line.strip()}')
        print()
