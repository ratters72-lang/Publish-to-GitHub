"""f4.py - repairs catch_system.py after the Stage 3B splice."""
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

NL = chr(10)
P = "world/catch_system.py"
s = open(P, encoding="utf-8", errors="replace").read()

# Fix A: rebuild the KICKOFF_CATCH print statement (its opener was
# consumed by the previous splice; the argument lines are orphaned).
orphan_start = s.find('"[KICKOFF_CATCH] pursuit="')
if orphan_start == -1:
    print("orphan print not found - nothing to repair")
    sys.exit(0)
anchor = s.find('" bc.x=", player.x,', orphan_start)
close_paren = s.find(")", anchor)
line_end = s.find(NL, close_paren)
ls = s.rfind(NL, 0, orphan_start) + 1

print_block_lines = [
    "            print(",
    '                "[KICKOFF_CATCH] pursuit=", world.in_pursuit_mode,',
    '                " first_not_made=", world.first_tackle_not_made,',
    '                " ruck_x=", world.ruck_x_m,',
    '                " anchor=", world.defensive_line_anchor,',
    '                " bc.x=", player.x,',
    "            )",
]
pb = NL.join(print_block_lines)
s = s[:ls] + pb + s[line_end:]

# Fix B: dedent the first STAGE 3B comment line to 12 spaces.
marker = "# STAGE 3B: NO band formation"
k = s.find(marker)
if k != -1:
    ls2 = s.rfind(NL, 0, k) + 1
    indent = k - ls2
    if indent > 12:
        s = s[:ls2] + (" " * 12) + s[k:]

open(P, "w", encoding="utf-8").write(s)
print("catch_system repaired OK")
