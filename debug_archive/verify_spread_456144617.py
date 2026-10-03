"""
Verify the fix: proper full-field spread
"""
def get_defence_target_fixed(defender_index):
    lane_width = 10.0  # was 2.0
    base_y = 5.0        # was 30.0
    ruck_x, ruck_y = 50.0, 35.0

    if defender_index == 0:
        return (ruck_x, ruck_y - 0.5)
    if defender_index == 1:
        return (ruck_x, ruck_y - 1.0)
    if defender_index == 2:
        return (ruck_x, ruck_y)
    lane_index = defender_index - 3
    target_y = base_y + lane_index * lane_width
    return (ruck_x, target_y)

print("=== FIXED get_defence_target() spread (7 line defenders) ===")
for idx in range(3, 10):
    tx, ty = get_defence_target_fixed(idx)
    print(f"  def_idx={idx} -> lane={idx-3} -> ty={ty:.0f}m  (field offset from sideline: {ty:.0f}m)")

print()
print("=== With proper pursuit mapping: def_idx = lane_idx + 6 ===")
for lane_idx in range(-3, 4):
    def_idx = lane_idx + 6
    tx, ty = get_defence_target_fixed(def_idx)
    print(f"  lane_idx={lane_idx:+d} -> def_idx={def_idx} -> ty={ty:.0f}m")

print()
print("Range: 5m to 65m = 60m wide spread on 70m field ✓")
print("Left margin: 5m, right margin: 5m ✓")
print("Center at 35m = field center ✓")
