import io
def dump(path, start, end):
    fh = io.open(path, encoding='utf-8', errors='replace')
    lines = fh.readlines()
    fh.close()
    print(f"### {path} {start}..{end}")
    for i in range(start-1, min(end, len(lines))):
        print(f"{i+1}: {lines[i].rstrip()}")
    print()

dump('game/defence/defence_lvl_1/defensive_split_spacing.py', 770, 805)
dump('world/open_play_engine.py', 4415, 4445)
