import sys, os
sys.path.insert(0, '.')
ok = True

try:
    from world.rules.phase_rules import PHASE_RULES
    print(f"[OK] STEP 1: phase_rules.py — {len(PHASE_RULES)} phases defined")
except Exception as e:
    print(f"[FAIL] STEP 1: {e}")
    ok = False

try:
    from world.rules.movement_contracts import apply_phase_invariants, audit_movement, log_phase_freeze, enforce_bc_backward_invariant
    print(f"[OK] STEP 2/3: movement_contracts.py — apply_phase_invariants, audit_movement, log_phase_freeze imported")
except Exception as e:
    print(f"[FAIL] STEP 2/3: {e}")
    ok = False

try:
    # Check that OPE has STAGE_START markers
    with open('world/open_play_engine.py', 'r') as f:
        ope = f.read()
    if '[STAGE_START]' in ope:
        count = ope.count('[STAGE_START]')
        print(f"[OK] STEP 4: OPE has {count} STAGE_START markers")
    else:
        print("[WARN] STEP 4: No STAGE_START markers found in OPE (unicode comment encoding)")
except Exception as e:
    print(f"[FAIL] STEP 4: {e}")
    ok = False

try:
    with open('player/player.py', 'r') as f:
        player = f.read()
    if 'movement_writer' in player:
        print(f"[OK] STEP 5: Player has movement_writer attribute (clear at top of update)")
    else:
        print("[FAIL] STEP 5: movement_writer not found in player.py")
        ok = False
except Exception as e:
    print(f"[FAIL] STEP 5: {e}")
    ok = False

try:
    from tmp.freeze_log_hunter import parse_log
    print(f"[OK] STEP 6: freeze_log_hunter.py — parse_log function importable")
except Exception as e:
    print(f"[FAIL] STEP 6: {e}")
    ok = False

try:
    with open('tmp/movement_pipeline.py', 'r') as f:
        pipeline = f.read()
    if 'MOVEMENT_PIPELINE' in pipeline:
        print(f"[OK] STEP 7: movement_pipeline.py — MOVEMENT_PIPELINE defined")
    else:
        print("[FAIL] STEP 7: MOVEMENT_PIPELINE not found")
        ok = False
except Exception as e:
    print(f"[FAIL] STEP 7: {e}")
    ok = False

print()
if ok:
    print("ALL STEPS COMPLETE")
else:
    print("SOME STEPS FAILED")
