with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()
lines = content.split('\n')
print("Total lines:", len(lines))
for i, l in enumerate(lines):
    l_s = l.strip()
    if 'def _update_post_ruck_open_play' in l_s:
        print(f'_update_post_ruck_open_play: line {i+1}')
    elif 'def _update_ptb_prep' in l_s:
        print(f'_update_ptb_prep: line {i+1}')
    elif 'def _update_ptb_exec' in l_s:
        print(f'_update_ptb_exec: line {i+1}')
    elif "def _update_ptb(" in l_s:
        print(f'_update_ptb: line {i+1}')
    elif "def _update_release(" in l_s:
        print(f'_update_release: line {i+1}')
    elif 'P7: Continuous retreat' in l_s:
        print(f'P7: line {i+1}')
    elif 'attack_controller.apply_vector' in l_s:
        print(f'apply_vector: line {i+1}')
