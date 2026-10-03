import io

def read_lines(path):
    with io.open(path, 'r', encoding='utf-8-sig') as f:
        return f.readlines()

dbu = read_lines('game/defence/defence_lvl_1/defensive_behaviour_unified.py')
ope = read_lines('world/open_play_engine.py')

regions = [
    ('DBU imports-tail/constants/_apply_tackler_commit', dbu, 95, 345),
    ('OPE tackle-init mask', ope, 3128, 3160),
    ('OPE contact mask', ope, 3524, 3550),
    ('OPE drive mask', ope, 3598, 3622),
    ('OPE dominance mask', ope, 4016, 4050),
    ('OPE release mask', ope, 4505, 4535),
]

mask_core = (
    '        world_ctx = getattr(self.world, "ctx", None)\n'
    '        if isinstance(world_ctx, dict):\n'
    '            world_ctx["frozen_ids"] = [p.player_id for p in self.world.players]\n'
)

with io.open('tmp/fx_edit_regions.txt', 'w', encoding='utf-8') as out:
    for label, lines, a, b in regions:
        out.write(f"\n########## {label} [{a}-{b}] ##########\n")
        for i in range(a - 1, min(b, len(lines))):
            out.write(f"{i+1}: {lines[i]}")
    # Exact occurrence count of the mask core across OPE
    src = ''.join(ope)
    n = src.count(mask_core)
    out.write(f"\n########## MASK CORE OCCURRENCES IN OPE: {n} ##########\n")

print("done")
