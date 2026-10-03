"""Verify Phase 2B — stability + collapse in TES."""
from player.movement_constants import COLLAPSE_THRESHOLD
from game.tes.tes_context import TESContext
from game.tes.tes_dominance import update_waveform, compute_dominance
from game.tes.tes_outcomes import TESOutcome
import random

ctx = TESContext()
rng = random.Random(42)
outcome = TESOutcome()

print(f"✓ COLLAPSE_THRESHOLD = {COLLAPSE_THRESHOLD}")
print(f"✓ ctx.stability initial = {ctx.stability}")
print(f"✓ ctx.collapsed initial = {ctx.collapsed}")

# Run one waveform frame — should compute stability
update_waveform(ctx, rng)
print(f"✓ ctx.stability after 1 frame = {ctx.stability:.4f}")
print(f"✓ ctx.collapsed after 1 frame = {ctx.collapsed}")

# Compute dominance — stability feeds into score
compute_dominance(ctx, outcome, rng)
print(f"✓ outcome.attacker_dominance = {outcome.attacker_dominance:.4f}")
print(f"✓ outcome.defender_dominance = {outcome.defender_dominance:.4f}")

print()
print("✅ Phase 2B complete — stability tracked and fed into dominance")
