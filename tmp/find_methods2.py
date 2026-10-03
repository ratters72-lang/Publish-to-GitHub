with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find all method definitions
import re
methods = re.findall(r'^\s+def (\w+)', content, re.MULTILINE)
print("All methods in OpenPlayEngine:")
for m in methods:
    print(f"  {m}")
