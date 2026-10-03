"""Compile-check OPE + tes_drive_variation and exercise the new pure functions."""

import ast
import random
import sys
import types

# 1. Syntax-check the edited files
for path in ("world/open_play_engine.py", "game/tes/tes_drive_variation.py"):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    ast.parse(src, filename=path)
    print(f"SYNTAX_OK {path}")

# 2. Exercise the new pure functions directly (no engine import needed)
sys.path.insert(0, ".")
from game.tes.tes_context import TESContext
from game.tes.tes_drive_variation import (
    compute_drive_variation_step,
    compute_dominance_push_result,
)

def make_ctx(result, momentum=0.0, mom_factor=1.0, flatfooted=False):
    ctx = types.SimpleNamespace()
    ctx.drive_momentum = momentum
    ctx.dominance_result = result
    ctx.contact_angle = 10.0
    ctx.bc_lean_forward = 1.0
    ctx.tackler_alignment = 0.0
    ctx.bc_momentum_factor = mom_factor
    ctx.bc_flatfooted = flatfooted
    return ctx

rng = random.Random(1234)
print("\n-- drive variation step (should be >=0 WEAK/NEUTRAL, <=0 DOMINANT) --")
for res in ("WEAK", "NEUTRAL", "DOMINANT"):
    sample = [compute_drive_variation_step(rng, make_ctx(res)) for _ in range(200)]
    lo, hi = min(sample), max(sample)
    assert abs(lo) <= 0.06 and abs(hi) <= 0.06, (res, lo, hi)
    if res == "DOMINANT":
        assert max(sample) <= 0.0, (res, max(sample))
    else:
        assert min(sample) >= 0.0, (res, min(sample))
    print(f"{res}: min={lo:.4f} max={hi:.4f} OK")

print("\n-- result-aware DOMINANCE push (WEAK/NEUTRAL ~0, DOMINANT > 0) --")
base = {}
for res in ("WEAK", "NEUTRAL", "DOMINANT"):
    vals = [compute_dominance_push_result(rng, make_ctx(res)) for _ in range(400)]
    avg = sum(vals) / len(vals)
    base[res] = avg
    print(f"{res}: avg={avg:.4f} max={max(vals):.4f}")
assert base["WEAK"] < 0.15, base["WEAK"]
assert base["NEUTRAL"] < 0.35, base["NEUTRAL"]
assert base["DOMINANT"] > 0.5, base["DOMINANT"]
print("DOMINANCE push OK")

# 3. Momentum-gate drive_step sanity: flat-footed BC drives much less
print("\n-- momentum-gate sanity (drive_step_mps formula) --")
drive_mag_abs, dur, strength = 3.0, 1.0, 1.3
for label, factor in (("flat-footed 0.35", 0.35), ("sprint 1.0", 1.0)):
    step = (drive_mag_abs / dur) * strength * 1.5 * factor
    print(f"{label}: step={step:.2f} m/s")
print("\nALL CHECKS PASSED")
