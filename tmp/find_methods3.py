with open('world/open_play_engine.py', 'r') as f:
    lines = f.readlines()
for i, l in enumerate(lines):
    l = l.strip()
    if 'def _update_post_ruck_open_play' in l:
        print(f'_update_post_ruck_open_play: line {i+1}')
    elif 'def _update_ptb_prep' in l:
        print(f'_update_ptb_prep: line {i+1}')
    elif 'def _update_ptb_exec' in l:
        print(f'_update_ptb_exec: line {i+1}')
    elif "def _update_ptb(" in l:
        print(f'_update_ptb: line {i+1}')
    elif "def _update_release(" in l:
        print(f'_update_release: line {i+1}')
    elif 'P7: Continuous retreat' in l:
        print(f'P7: line {i+1}')
    elif 'attack_controller.apply_vector' in l:
        print(f'apply_vector: line {i+1}')
