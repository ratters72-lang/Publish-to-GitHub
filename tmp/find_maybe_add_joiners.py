"""Check if _maybe_add_joiners exists in open_play_engine.py."""
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

if '_maybe_add_joiners' in content:
    print("FOUND _maybe_add_joiners")
    # Show the context around it
    idx = content.index('_maybe_add_joiners')
    start = max(0, idx - 200)
    end = min(len(content), idx + 600)
    print(content[start:end])
else:
    print("MISSING _maybe_add_joiners - not found in file")

# Also check for _add_joiners variants
for variant in ['_add_joiners', '_may_add_joiners', 'maybe_add_joiner', 'add_joiners']:
    if variant in content:
        print(f"FOUND variant: {variant}")
