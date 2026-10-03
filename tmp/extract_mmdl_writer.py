with open("game/defence/defence_lvl_1/defensive_behaviour_unified.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

out = []
for i in range(2195, min(2355, len(lines))):
    out.append(f"{i+1}: {lines[i].rstrip()}")

with open("tmp/mmdl_writer_block.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("done")
