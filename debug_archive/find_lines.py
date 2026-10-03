with open('world/open_play_engine.py','r', encoding='utf-8') as f:
    lines = f.readlines()

targets = ['_compute_defensive_anchor', '_update_post_ptb_reset', '_update_release',
           '_prepare_release', '_update_ptb_ready', '_update_ptb_prep',
           '_update_ptb_exec', '_update_ptb', '_support_lane_y']
print(f"Total lines: {len(lines)}")
for i, line in enumerate(lines):
    for t in targets:
        if f'def {t}' in line:
            print(f'{t}: line {i+1}')
