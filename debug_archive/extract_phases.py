import re

with open('world/open_play_engine.py', 'r') as f:
    content = f.read()

# Find all phase handler methods by name
methods_to_find = [
    '_update_release', 
    '_update_ptb_ready', 
    '_update_ptb_prep', 
    '_update_ptb_exec', 
    '_update_ptb', 
    '_update_post_ptb_reset', 
    '_update_tackle_dominance',
    '_prepare_release',
    '_compute_defensive_anchor',
    '_enforce_attack_directions',
    '_enforce_defence_directions',
]

for m_name in methods_to_find:
    # Find the method definition
    pattern = re.compile(r'def ' + re.escape(m_name) + r'\b')
    match = pattern.search(content)
    if match:
        idx = match.start()
        # Find where the method ends (next def at same indent level or end of file)
        end_idx = len(content)
        # Look for next def at indent level 4
        rest = content[idx + len(f'def {m_name}'):]
        next_def = re.search(r'\n    def ', rest)
        if next_def:
            end_idx = idx + len(f'def {m_name}') + next_def.start() + 1
        
        print(f'=== {m_name} (lines {content[:idx].count(chr(10))+1}-{content[:end_idx].count(chr(10))}) ===')
        print(content[idx:end_idx].strip())
        print()
    else:
        # Try finding references instead
        ref_idx = content.find(m_name)
        if ref_idx != -1:
            print(f'=== {m_name} referenced at {ref_idx} ===')
            start = max(0, ref_idx - 50)
            end = min(len(content), ref_idx + len(m_name) + 50)
            print(content[start:end])
            print()
        else:
            print(f'=== {m_name} NOT FOUND ===')
            print()

# Also find def_behaviour.apply calls
print('=== def_behaviour.apply calls ===')
for i, line in enumerate(content.split('\n')):
    if 'def_behaviour' in line and 'apply' in line:
        print(f'  Line {i+1}: {line.strip()}')

# Find where def_behaviour.apply is called within phase handlers
print()
print('=== All def_behaviour references ===')
for i, line in enumerate(content.split('\n')):
    if 'def_behaviour' in line:
        print(f'  Line {i+1}: {line.strip()}')
