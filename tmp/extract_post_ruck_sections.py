"""Extract key sections from open_play_engine.py for freeze analysis."""

with open("world/open_play_engine.py", "r") as f:
    lines = f.readlines()

# Track which sections we're in
current_method = None

for i, line in enumerate(lines):
    stripped = line.rstrip()
    if "def _" in stripped:
        # Extract method name
        parts = stripped.split("def ")[1].split("(")[0]
        current_method = parts

    # Only print lines from methods we care about
    target_methods = [
        "_update_ptb",
        "_update_post_ruck_open_play",
        "_post_ruck_marker_cleanup",
        "_assign_defensive_line_targets",
        "_reset_tackle_state",
        "_prepare_release",
        "_trigger_marker_peel",
    ]

    if current_method in target_methods:
        # Print any lines containing freeze/velocity/BC/ball_carrier related terms
        keywords = [
            "frozen", "freeze", "freeze_timer", "marker_freeze",
            "vx =", "vy =", "vx=0", "vy=0", "vx = 0", "vy = 0",
            "ball_carrier", ".bc ", "bc.", "b_c.",
            "set_phase", "reset",
            "apply_vector", "attack_controller",
        ]
        if any(k in line.lower() for k in keywords):
            print(f"{current_method} L{i+1}: {stripped}")
