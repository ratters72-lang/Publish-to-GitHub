"""Fix the deterministic DRIVE integration: accumulate metres per frame.

Two edits in _update_tackle_drive:
1. First-frame block: reset ctx.drive_det_x = 0.0 (near drive_elapsed_s init).
2. Deterministic write: replace
       bc.x = ctx.drive_start_x + dx_step * ctx.drive_elapsed_s
   with per-frame accumulation:
       ctx.drive_det_x += dx_step * DRIVE_DETERMINISTIC_BOOST  (handled below)
       bc.x = ctx.drive_start_x + ctx.drive_det_x

NOTE: the DOMINANT branch already applies the sign inside dx_step; the
attacker boost is applied in the else branch.  The final write becomes a
plain accumulator read.
"""
path = "world/open_play_engine.py"
with open(path, encoding="utf-8") as f:
    text = f.read()

# --- Edit 1: reset the accumulator in the first-frame block ---
OLD_RESET = "            # Initialise elapsed --- first frame is zero so BC stays at bc_x0\n            ctx.drive_elapsed_s = 0.0\n"
NEW_RESET = "            # Initialise elapsed --- first frame is zero so BC stays at bc_x0\n            ctx.drive_elapsed_s = 0.0\n            # Deterministic metre accumulator (NRL DRIVE, 2026-08-05).\n            ctx.drive_det_x = 0.0\n"
assert OLD_RESET in text, "reset anchor not found"
text = text.replace(OLD_RESET, NEW_RESET, 1)

# --- Edit 2: deterministic write becomes accumulator read ---
OLD_WRITE = (
    "        # Place target = start + signed accumulated step, capped at target.\n"
    "        # NOTE: the deterministic write is shared with the DOMINANT back-drive;\n"
    "        # the boost/geometry applied above only shifts the live forward step.\n"
    "        bc.x = ctx.drive_start_x + dx_step * ctx.drive_elapsed_s\n"
)
NEW_WRITE = (
    "        # Accumulate the signed per-frame step (metres) so the drive reaches\n"
    "        # full arc over the duration: dx_step already carries team_attack_dir,\n"
    "        # the DOMINANT back-drive sign, the attacker boost, and geometry.\n"
    "        ctx.drive_det_x += dx_step\n"
    "        bc.x = ctx.drive_start_x + ctx.drive_det_x\n"
)
assert OLD_WRITE in text, "write anchor not found"
text = text.replace(OLD_WRITE, NEW_WRITE, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(text)

print("det integration fix applied")
