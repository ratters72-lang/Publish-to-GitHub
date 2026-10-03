import re

path = "game/defence/defence_lvl_1/defensive_behaviour_lvl1.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# Replace the import line pattern
old_import = "from game.defence.defence_lvl_1.defensive_split_spacing import spacing_y as _spacing_y"
new_import = "from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y"

count = content.count(old_import)
content = content.replace(old_import, new_import)

# Replace all calls
old_call = "_spacing_y(world, d, self.tuners)"
new_call = "_frame_spacing_y(world, d, self.tuners)"

count2 = content.count(old_call)
content = content.replace(old_call, new_call)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"Replaced {count} imports, {count2} calls")
