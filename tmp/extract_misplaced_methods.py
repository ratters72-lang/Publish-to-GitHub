"""Extract methods that were placed inside _check_break_tackle and place them correctly."""

with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the structure around the problem area
idx_check_break = content.find('def _check_break_tackle')
idx_check_pre = content.find('def _check_pre_tackle')
idx_start_tackle = content.find('def _start_tackle')
idx_prep_bc = content.find('def _prepare_bc_dh_for_tackle')

print(f"_check_break_tackle starts at: {idx_check_break}")
print(f"_check_pre_tackle at: {idx_check_pre}")
print(f"_start_tackle at: {idx_start_tackle}")
print(f"_prepare_bc_dh_for_tackle at: {idx_prep_bc}")

# The check_break_tackle body goes from its def until the next def at same level
# Find the proper end of _check_break_tackle
body_start = content.index(':\n', idx_check_break) + 2
# Find the next def at column 0 (next method)
next_def = content.find('\ndef ', idx_check_break + 5)
print(f"Next def after _check_break_tackle: {next_def}")

# Show what's between _check_break_tackle's body end and the misplaced methods
sample = content[idx_check_break:idx_check_pre]
# Find the last meaningful lines before _check_pre_tackle
lines_before = sample.split('\n')
print(f"\nLast 5 lines before _check_pre_tackle (inside _check_break_tackle):")
for line in lines_before[-6:]:
    print(f"  {repr(line)}")
