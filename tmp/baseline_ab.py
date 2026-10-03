"""A/B baseline check — prove whether TASK VI edits caused the
verify_movement_reactivation ASSERT-A fail (frame 752, pid 212).

Bypass strategy: monkeypatch `carrier_past_defensive_line` to return False
inside the verifier process.  When False, ALL of the TASK VI chase wiring
decays to the pre-patch behaviour:
  - decide_movement_state: lock override + branch-4 override inactive
  - _is_carrier_past_anchor: inactive -> step-7a engaged chase + MMDL
    LINE-gate/blend bypass inactive
The Player clamp bypass (_is_chase_state) is still present but it only
MATTERS when a defender reaches CHASE; under the patch-inactive world,
any CHASE comes from pre-existing writers (tackler_commit / MMDL default)
which already existed before TASK VI.

If ASSERT-A still fires with these bypassed, the failure is NOT caused
by TASK VI.
"""
import sys

sys.path.insert(0, ".")

# Bypass BEFORE importing anything that binds carrier_past_defensive_line.
import game.defence.defence_lvl_1.movement_mode_decision as mmd
mmd.carrier_past_defensive_line = lambda world, bc, extra_m=1.0: False

# Re-import the dependent module fresh so its `from ... import
# carrier_past_defensive_line` picks up the patched function.
import importlib
fb = importlib.import_module(
    "game.defence.defence_lvl_1.defensive_behaviour_unified"
)
importlib.reload(fb)

# Now run the actual verifier module in-process, picking the entry point
# the same way Python does for `python verify_movement_reactivation.py`.
sys.argv = ["verify_movement_reactivation.py"]
verifier = importlib.import_module("verify_movement_reactivation")

_runner = getattr(verifier, "main", None)
if callable(_runner):
    _runner()
else:
    # No main(): re-execute the module body against __main__ so
    # `if __name__ == "__main__":` blocks fire exactly like a CLI run.
    import runpy
    runpy.run_module("verify_movement_reactivation", run_name="__main__")
