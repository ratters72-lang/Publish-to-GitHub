# Read only the tail of open_play_engine.py from line 1400 onwards (PTB + POST_RUCK sections)
with open("world/open_play_engine.py", "rb") as f:
    lines = f.readlines()

total_lines = len(lines)
print(f"Total lines: {total_lines}")

# Find key method starts
for i, line in enumerate(lines):
    text = line.decode("utf-8", errors="replace")
    if "def _" in text:
        parts = text.strip().split("def ")[1].split("(")[0]
        print(f"L{i+1}: def {parts}")

# Find all lines containing frozen, vx =, vy = in PTB and POST_RUCK sections
print("\n\n=== SECTIONS FROM _update_ptb to end ===")
in_section = False
for i, line in enumerate(lines):
    text = line.decode("utf-8", errors="replace")
    if "def _update_ptb" in text:
        in_section = True
    if "def _update_ptb" in text or ("def _check_marker_offside" in text and not in_section):
        pass  # will be caught above

    if in_section:
        keywords = ["frozen", "freeze", "vx", "vy", "ball_carrier", "bc.", "b_c.", "set_phase", "apply_vector", "attack_controller"]
        if any(k in text.lower() for k in keywords):
            print(f"L{i+1}: {text.rstrip()}")
