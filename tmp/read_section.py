with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find _update_post_ruck_open_play
start = None
end = None
for i, l in enumerate(lines):
    if 'def _update_post_ruck_open_play' in l:
        start = i
    if start is not None and i > start and l.strip().startswith('def ') and not l.strip().startswith('def _update_post_ruck_open_play'):
        end = i
        break
    if start is not None and l.strip() == '':
        pass

# Print until we hit the next def at same indent level or end
found = False
for i, l in enumerate(lines):
    if 'def _update_post_ruck_open_play' in l:
        found = True
    if found:
        print(f"{i+1}:{l}", end='')
        # Check if next line starts a new method at same indent
        if i > start and l.strip().startswith('def ') and 'update_post_ruck' not in l:
            break
