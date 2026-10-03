"""Rewrite the two NRL drive blocks at the engine's TRUE 8-space base indent.

Verified via inspect_ws.py: method body = 8, `if` header = 8, nested body = 12.
"""
path = "world/open_play_engine.py"
with open(path, encoding="utf-8") as f:
    text = f.read()

A1 = "# Advance the drive timer"
A2 = "# ---- TES DRIVE VARIATION (extra metres / wrestle) ----"
A3 = "# ---- NRL DRIVE CURVE (spec Step 3) ----"
A4 = "# [8] TEAM ATTACK DIR AUDIT"

i1 = text.find(A1)
i2 = text.find(A2)
i3 = text.find(A3)
i4 = text.find(A4)
assert 0 < i1 < i2 < i3 < i4, (i1, i2, i3, i4)

ls1 = text.rfind("\n", 0, i1) + 1     # start of A1 line
le1 = text.rfind("\n", 0, i2) + 1     # start of A2 line
ls3 = text.rfind("\n", 0, i3) + 1     # start of A3 line
le3 = text.rfind("\n", 0, i4) + 1     # start of A4 line

# DET_BLOCK: 8-space base, 12-space nested bodies.
DET_BLOCK = (
    "        # Advance the drive timer\n"
    "        ctx.drive_elapsed_s += dt\n"
    "        dur = max(0.1, self.tes_outcome.tackle_duration_s)\n"
    "        t = max(0.0, min(1.0, ctx.drive_elapsed_s / dur))\n"
    "\n"
    "        # ---- PHASE 2.4: drive-strength-based BC movement ----\n"
    "        # BC x advances per frame by drive_step_mps * dt along team_attack_dir.\n"
    "        # If the defending team DOMINATES, the BC is driven BACKWARD\n"
    "        # (opposite team_attack_dir) — spec 2.4 \"BC.x must decrease if\n"
    "        # defending team dominates\".\n"
    "        #\n"
    "        # NRL DRIVE (2026-08-05) — Step 1: deterministic base movement with\n"
    "        # attacker-win boost (Step 5: x1.8 for WEAK/NEUTRAL) and geometric\n"
    "        # correction (Step 6).\n"
    "        if ctx.dominance_result == \"DOMINANT\":\n"
    "            dx_step = -ctx.drive_step_mps * dt\n"
    "        else:\n"
    "            dx_step = ctx.drive_step_mps * dt\n"
    "            # Attacker-win deterministic boost (spec Step 5).\n"
    "            dx_step *= DRIVE_DETERMINISTIC_BOOST\n"
    "        # Geometric correction factor (spec Step 6): reduces effective drive\n"
    "        # when contact geometry is poor.  Always in [0, 1], never modifies\n"
    "        # TES state, clamp logic, or tackler-follow logic.\n"
    "        geometric_factor = compute_geometric_factor(ctx)\n"
    "        dx_step *= geometric_factor\n"
    "        # Place target = start + signed accumulated step, capped at target.\n"
    "        # NOTE: the deterministic write is shared with the DOMINANT back-drive;\n"
    "        # the boost/geometry applied above only shifts the live forward step.\n"
    "        bc.x = ctx.drive_start_x + dx_step * ctx.drive_elapsed_s\n"
    "        # Y drive is a straight lerp (lateral component, no strength scaling)\n"
    "        bc.y = ctx.bc_y0 + (ctx.drive_target_y - ctx.bc_y0) * t\n"
    "\n"
)

# VAR_BLOCK: inside `if ctx.drive_elapsed_s is not None:` (8) => 12 base, 16 nested.
VAR_BLOCK = (
    "            # ---- NRL DRIVE CURVE (spec Step 3) ----\n"
    "            # Momentum-weighted Gaussian variation per frame, attacker-win\n"
    "            # bias (WEAK/NEUTRAL) vs defender-win suppression (DOMINANT),\n"
    "            # hard-clamped to +/- MAX_VARIATION, then scaled by the\n"
    "            # geometric correction factor (spec Step 7).\n"
    "            variation_step = self.rng.gauss(0.0, BASE_SIGMA)\n"
    "            variation_step *= (1.0 + ctx.drive_momentum * MOMENTUM_SCALE)\n"
    "            if ctx.dominance_result in (\"WEAK\", \"NEUTRAL\"):\n"
    "                variation_step *= ATTACKER_BIAS\n"
    "            else:\n"
    "                variation_step *= DEFENDER_BIAS\n"
    "            variation_step = max(-MAX_VARIATION, min(MAX_VARIATION, variation_step))\n"
    "            variation_step *= geometric_factor\n"
    "            bc.x += variation_step\n"
    "            ctx.drive_variation_x += variation_step\n"
    "\n"
)

# A2 header line currently at 10 spaces -> normalize to 8.
a2_line_start = le1
a2_line_end = text.find("\n", a2_line_start)
a2_content = text[a2_line_start:a2_line_end].lstrip()
text = text[:a2_line_start] + "        " + a2_content + text[a2_line_end:]

text = text[:ls1] + DET_BLOCK + text[le1:ls3] + VAR_BLOCK + text[le3:]

with open(path, "w", encoding="utf-8") as f:
    f.write(text)

print("rewrite complete (8-space base)")
