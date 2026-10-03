"""
Trace what get_defence_target() Y values are produced for each defender index.
Then trace the pursuit mode non-P/S Y calculation: abs(lane_idx) + 3
"""

# Simulate get_defence_target() as it exists
def get_defence_target(defender_index):
    """Copy from world/world_state.py"""
    anchor_x = 70.0  # typical open play anchor
    ruck_x = 50.0
    ruck_y = 35.0
    lane_width = 2.0
    base_y = 30.0  # field-anchored base Y

    if defender_index == 0:
        return (ruck_x, ruck_y - 0.5)
    if defender_index == 1:
        return (ruck_x, ruck_y - 1.0)
    if defender_index == 2:
        return (ruck_x, ruck_y)
    lane_index = defender_index - 3
    target_y = base_y + lane_index * lane_width
    return (anchor_x, target_y)


print("=" * 60)
print("CHECK 1: get_defence_target() Y for each def_idx")
print("=" * 60)
for def_idx in range(0, 7):
    tx, ty = get_defence_target(def_idx)
    print(f"  def_idx={def_idx} -> target_y={ty:.2f}m")

print()
print("=" * 60)
print("CHECK 2: What pursuit mode non-P/S defenders get")
print("   ty = get_defence_target(world, abs(lane_idx) + 3)")
print("=" * 60)
for lane_idx in range(-3, 4):
    def_idx = abs(lane_idx) + 3
    tx, ty = get_defence_target(def_idx)
    print(f"  lane_idx={lane_idx:+d} -> def_idx={def_idx} -> target_y={ty:.2f}m")

print()
print("=" * 60)
print("CHECK 3: What structured mode eligible loop gives")
print("   line_idx = idx + 3, tx,ty = get_defence_target(world, line_idx)")
print("=" * 60)
for idx in range(0, 7):
    line_idx = idx + 3
    tx, ty = get_defence_target(line_idx)
    print(f"  eligible idx={idx} -> line_idx={line_idx} -> target_y={ty:.2f}m")

print()
print("=" * 60)
print("CRITICAL FINDING")
print("=" * 60)
print("base_y=30.0, lane_width=2.0")
print("Range: 30.0 to 30.0 + 6*2.0 = 42.0 = only 12m wide")
print("Field is 70m wide. Should spread from ~5m to ~65m (margin 3m each side).")
print("THE DEFENCE IS COLLAPSED TO 12m WIDE instead of 60m wide.")
print()
print("Fix: lane_width should be ~10m for 6 gaps between 7 defenders")
print("     base_y should be 5.0-8.0m (margin)")
