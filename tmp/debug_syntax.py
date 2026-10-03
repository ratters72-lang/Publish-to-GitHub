with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the problematic area - show lines with proper python line numbers
# Look for the tackle initiation comment and surrounding context
for i, line in enumerate(lines):
    if '# -- Tackle initiation' in line or 'def _check_pre_tackle' in line or '_update_defenders_tes_phases' in line or '# [STEP-02A-01]' in line or 'def _check_break_tackle' in line:
        start = max(0, i-2)
        end = min(len(lines), i+3)
        for j in range(start, end):
            print(f"{j+1}: {lines[j].rstrip()}")
        print("---")
