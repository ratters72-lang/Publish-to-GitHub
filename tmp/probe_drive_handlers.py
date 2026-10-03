"""Extract TES drive/dominance integration sections from OpenPlayEngine."""

path = "world/open_play_engine.py"
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

starts = {}
for i, line in enumerate(lines):
    if line.startswith("    def "):
        name = line.strip().lstrip("def").strip().split("(")[0].strip()
        starts[name] = i

targets = [
    "_update_tackle_drive",
    "_update_tackle_dominance",
    "_update_tackle_contact",
    "_update_tackle_init",
    "_start_tackle",
    "_follow_attached_tacklers",
    "_update_attachments",
    "update_waveform",
]
for t in targets:
    idx = starts.get(t)
    if idx is None:
        print(f"### {t}: NOT FOUND")
        continue
    end = len(lines)
    for j in range(idx + 1, len(lines)):
        if lines[j].startswith("    ") is False and lines[j].strip() and not lines[j].startswith("#"):
            end = j
            break
    print(f"\n{'='*90}\n### {t}  (lines {idx+1}-{end})\n{'='*90}")
    print("".join(lines[idx:end]))
