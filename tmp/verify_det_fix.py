"""Verify the deterministic accumulator patch + compile all modules."""
import py_compile

for path in [
    "game/tes/tes_context.py",
    "game/tes/tes_drive_variation.py",
    "world/open_play_engine.py",
]:
    py_compile.compile(path, doraise=True)
    print(f"COMPILE_OK {path}")

text = open("world/open_play_engine.py", encoding="utf-8").read()

checks = {
    "reset drive_det_x": "ctx.drive_det_x = 0.0",
    "accumulator increment": "ctx.drive_det_x += dx_step",
    "new deterministic write": "bc.x = ctx.drive_start_x + ctx.drive_det_x",
    "old dt*elapsed write gone": "bc.x = ctx.drive_start_x + dx_step * ctx.drive_elapsed_s",
}
for name, needle in checks.items():
    found = needle in text
    if name.startswith("old"):
        print(f"{'PASS' if not found else 'FAIL'}  {name}")
    else:
        print(f"{'PASS' if found else 'FAIL'}  {name}")

# Order: accumulator increment before BC write.
i_inc = text.find("ctx.drive_det_x += dx_step")
i_wr = text.find("bc.x = ctx.drive_start_x + ctx.drive_det_x")
print(f"order inc({i_inc}) < write({i_wr}): {0 < i_inc < i_wr}")
