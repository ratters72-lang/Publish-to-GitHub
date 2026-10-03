"""Verify the NRL drive rewire landed correctly and modules compile."""
import py_compile

for path in [
    "game/tes/tes_context.py",
    "game/tes/tes_drive_variation.py",
    "world/open_play_engine.py",
]:
    py_compile.compile(path, doraise=True)
    print(f"COMPILE_OK {path}")

# Show the rewired drive region (deterministic + variation)
with open("world/open_play_engine.py", encoding="utf-8") as f:
    lines = f.readlines()

GEOM = "compute_geometric_factor(ctx)"
GAUSS = "self.rng.gauss(0.0, BASE_SIGMA)"
BOOST = "dx_step *= DRIVE_DETERMINISTIC_BOOST"
CTX_ANGLE = "ctx.contact_angle = ctx.approach_angle_deg"
CTX_LEAN = "ctx.bc_lean_forward"
CTX_ALIGN = "ctx.tackler_alignment"

checks = {
    "geometry population (contact_angle)": CTX_ANGLE,
    "geometry population (bc_lean_forward)": CTX_LEAN,
    "geometry population (tackler_alignment)": CTX_ALIGN,
    "geometric_factor call": GEOM,
    "Gaussian variation (self.rng.gauss)": GAUSS,
    "deterministic boost (x1.8)": BOOST,
}
found = {name: False for name in checks}
for line in lines:
    for name, needle in checks.items():
        if needle in line:
            found[name] = True

for name, ok in found.items():
    print(f"{'PASS' if ok else 'FAIL'}  {name}")

# Order assertions: dx_step boost must appear before bc.x deterministic write
text = "".join(lines)
i_boost = text.find(BOOST)
i_det_write = text.find("bc.x = ctx.drive_start_x + dx_step * ctx.drive_elapsed_s")
i_gauss = text.find(GAUSS)
i_var_write = text.find("bc.x += variation_step")
i_var_accum = text.find("ctx.drive_variation_x += variation_step")

print(f"\norder boost({i_boost}) < det_write({i_det_write}): {0 <= i_boost < i_det_write}")
print(f"order gauss({i_gauss}) < var_write({i_var_write}): {0 <= i_gauss < i_var_write}")
print(f"order var_write({i_var_write}) < drive_variation_accum({i_var_accum}): "
      f"{0 <= i_var_write < i_var_accum}")
