"""Verify world.open_play_engine and its SDEA dep can be imported cleanly."""
print("Attempting import of world.open_play_engine...")
# We can't fully import it due to dependencies, but we can verify 
# the specific _apply_defence_sdm method source is syntactically valid
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    source = f.read()
compile(source, 'world/open_play_engine.py', 'exec')
print("Compilation OK — no syntax errors.")

# Verify the patch was applied: the tes_locked check should be removed
count_tes_locked_gate = source.count('not getattr(d, "tes_locked", False)')
count_sdea_comment = source.count('SDEA: TES physics must NEVER block retreat_10 movement')
count_only_frozen = source.count('if not d.frozen:\n                    d.update(dt)')

print(f"tes_locked gate occurrences: {count_tes_locked_gate}")
print(f"SDEA comment occurrences: {count_sdea_comment}")
print(f"frozen-only gate occurrences: {count_only_frozen}")

# Find the retreat_10 block and verify
idx = source.find('if decision["action"] == "retreat_10"')
if idx >= 0:
    block = source[idx:idx+800]
    print("\n--- PATCHED BLOCK ---")
    print(block[:500])
    print("...")
    print("--- END PATCHED BLOCK ---")
    assert 'tes_locked' not in block.split("if not d.frozen")[1].split("\n")[0], \
        "FAIL: tes_locked still present in the d.update(dt) gate!"
    print("\nPASS: tes_locked check successfully removed from retreat_10 gate.")Context: Defensive movement architecture (for AI / engine spec)
Current behaviour (problem):  
Defenders use a hard rule:

target_x
=
ball_carrier.x
−
role_depth
and then move at full speed toward that target. There is no predictor / arrival behaviour. This causes:

Overrun and oscillation around the target.

Flat defensive line (depth differences between roles are not visible).

Retreaters, fold defenders, inside/edge defenders, markers all effectively behave the same.

SDEA flags like STUCK_NOT_FROZEN and movement invariants breaking.

Required architectural rule:  
Roles must drive movement. Movement must express roles.  
Each defensive role (e.g. DEF_RETREAT_10, FOLD_DEFENDER, INSIDE_DEFENDER, EDGE_DEFENDER, MARKER_1, MARKER_2, TACKLER_1, TACKLER_2) must map to:

A target position (depth/width relative to ruck/ball).

A movement profile (speed, arrival behaviour, freeze).

Predictor / arrival behaviour (core instruction)
Instead of always moving at max speed to the target, defenders must slow down as they approach their role-defined position.

Arrival controller (1D example along x-axis):

python
error = d.target_x - d.x  # distance to target along x

stop_radius = 0.1   # within this, we consider the player "at target"
slow_radius = 1.0   # within this, we start slowing down

if abs(error) < stop_radius:
    d.vx = 0.0
    d.x = d.target_x
    d.reached_target = True
elif abs(error) < slow_radius:
    # scale speed proportionally to remaining distance
    d.vx = d.max_speed * (error / slow_radius)
else:
    # far away: move at full speed toward target
    d.vx = d.max_speed * (1 if error > 0 else -1)
This must be applied per frame before physics integration (update(dt)).

Role → movement mapping (simplified spec)
For each defender d:

python
def apply_role_movement(d, phase, ruck_x, ruck_y):
    role = d.role

    if role == Roles.TACKLER_1:
        # Tackler 1 must be frozen during PTB_PREP/PTB_EXEC
        if phase in (Phases.PTB_PREP, Phases.PTB_EXEC):
            d.frozen = True
            d.vx = d.vy = 0.0
        return

    if role == Roles.DEF_RETREAT_10:
        d.target_x = ruck_x + 10.0
        d.target_y = d.lane_y
        d.max_speed = d.retreat_speed
        return

    if role == Roles.FOLD_DEFENDER:
        d.target_x = ruck_x + d.fold_depth   # e.g. 12–14m
        d.target_y = d.fold_lane
        d.max_speed = d.fold_speed
        return

    if role == Roles.INSIDE_DEFENDER:
        d.target_x = ruck_x + d.inside_depth # e.g. 8–10m
        d.target_y = d.inside_lane
        d.max_speed = d.inside_speed
        return

    if role == Roles.EDGE_DEFENDER:
        d.target_x = ruck_x + d.edge_depth   # e.g. 14–16m
        d.target_y = d.edge_lane             # near sideline
        d.max_speed = d.edge_speed
        return

    if role in (Roles.MARKER_1, Roles.MARKER_2):
        d.target_x = ruck_x
        d.target_y = ruck_y + d.marker_offset
        d.max_speed = d.marker_speed
        return
Then the movement pipeline per frame should be:

python
for d in defenders:
    apply_role_movement(d, phase, ruck_x, ruck_y)

    if not d.frozen:
        apply_arrival_predictor(d)  # the error/slowdown logic above
        d.update(dt)                # physics integration
Key invariants:

Roles are the source of movement targets. No other system (pursuit, generic AI) may overwrite target_x, target_y, vx, vy, or frozen after this step in TES/RELEASE/PTB.

TACKLER_1 must be frozen=True in PTB_PREP and PTB_EXEC.

Depth differences per role must be visible in actual positions (line should not be perfectly flat).

Predictor/arrival behaviour is mandatory to prevent overshoot and to allow defenders to “settle” into their role positions.
else:
    print("FAIL: Could not find retreat_10 block!")
