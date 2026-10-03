out = open("tmp/tuner_dump.txt", "w", encoding="utf-8")
src = open("game/defence/defence_lvl_1/defensive_tuners.py", encoding="utf-8").read().splitlines()
for i in range(315, min(370, len(src))):
    out.write(f"{i+1}: {src[i]}\n")
out.write("\n--- quiet window region ---\n")
for i in range(815, min(845, len(src))):
    out.write(f"{i+1}: {src[i]}\n")
out.close()
