"""Runtime import + attribute-resolution check for the OPE TES edits."""

import sys
sys.path.insert(0, ".")

import world.open_play_engine as ope

# Verify the three new names resolve at module scope
assert hasattr(ope, "compute_drive_variation_step"), "compute_drive_variation_step missing"
assert hasattr(ope, "compute_dominance_push_result"), "compute_dominance_push_result missing"
assert hasattr(ope, "investigate_tes_forward_momentum"), "investigate_tes_forward_momentum missing"
print("IMPORT_OK world.open_play_engine")

# Verify the engine source references the new names correctly (no leftovers)
src = open("world/open_play_engine.py", encoding="utf-8").read()
assert "compute_drive_variation_step(self.rng, ctx)" in src
assert "compute_dominance_push_result(self.rng, ctx)" in src
assert "ctx.frame_log" in src
assert "RECOVERY_BACKWARD_DISTANCE_M: float = 0.15" in src
print("INTEGRATION_OK all OPE edits present")

# Sanity: the drive-gate constant is a real float multiplier
assert 0.0 < ope.RECOVERY_BACKWARD_DISTANCE_M < 0.5
print(f"RECOVERY_BACKWARD_DISTANCE_M = {ope.RECOVERY_BACKWARD_DISTANCE_M}")
print("ALL PASSED")
