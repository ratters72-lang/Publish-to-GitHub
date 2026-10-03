# Find lines of specific patterns for SEARCH blocks
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

targets = {
    'P7: Continuous retreat': [],
    'attack_controller.apply_vector': [],
    'def _update_post_ruck_open_play': [],
    'def _update_ptb(': [],
    'def _update_ptb_prep': [],
    'def _update_ptb_exec': [],
    'def _update_release(': [],
}

for i, l in enumerate(lines):
    for key in targets:
        if key in l:
            targets[key].append(i + 1)

for key, vals in targets.items():
    print(f'{key}: {vals}')

# Show context around _update_post_ruck_open_play
print('\n\n--- Lines around _update_post_ruck_open_play ---')
for key in targets:
    if 'post_ruck' in key and targets[key]:
        start = max(0, targets[key][0] - 1)
        for j in range(start, min(len(lines), start + 80)):
            print(f'{j+1}: {lines[j]}', end='')
