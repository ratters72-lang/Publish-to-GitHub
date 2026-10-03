"""Check if _maybe_add_joiners DEFINITION exists (not just calls)."""
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Search for the method definition
if 'def _maybe_add_joiners' in content:
    print("FOUND def _maybe_add_joiners")
    idx = content.index('def _maybe_add_joiners')
    start = max(0, idx - 100)
    end = min(len(content), idx + 400)
    print(content[start:end])
else:
    print("MISSING: def _maybe_add_joiners not found in file!")
    
# Also check for any def *_add_joiner* variants
import re
matches = re.findall(r'def\s+.*add.*join.*\s*\(', content, re.IGNORECASE)
if matches:
    print(f"Found alternate joiner definitions: {matches}")
else:
    print("No alternate joiner definitions found")
