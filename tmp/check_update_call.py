with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    data = f.read()

# Find the _update_post_ruck_open_play method
import re
# Find all methods to get the full method content
idx = data.find('def _update_post_ruck_open_play')
if idx < 0:
    print("Method not found!")
    exit()

# Get from method start to next def at same indent level
# Find next '    def ' after this method
remainder = data[idx:]
next_method = re.search(r'\n    def (?!_update_post_ruck_open_play)', remainder)
if next_method:
    method_text = remainder[:next_method.start()]
else:
    method_text = remainder

# Print the last 500 chars to see the tail
print(method_text[-500:])
print()
print("---")
print("Contains 'd.update(dt)':", 'd.update(dt)' in method_text)
print("Contains '.update(dt)':", '.update(dt)' in method_text)
