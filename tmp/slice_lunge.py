import io

with io.open('world/open_play_engine.py', 'r', encoding='utf-8-sig') as f:
    lines = f.readlines()

with io.open('tmp/lunge_region.txt', 'w', encoding='utf-8') as out:
    # Find def _lunge_tacklers_to_bc and print 70 lines from there
    start = None
    for i, l in enumerate(lines):
        if 'def _lunge_tacklers_to_bc' in l:
            start = i
            break
    if start is None:
        out.write("NOT FOUND\n")
    else:
        for i in range(start - 2, min(start + 72, len(lines))):
            out.write(f"{i+1}: {lines[i]}")

print("done")
