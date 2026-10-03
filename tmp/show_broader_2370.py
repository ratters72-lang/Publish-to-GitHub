with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Show from line 2360 to 2400
for i in range(2360, min(len(lines), 2410)):
    print(f"{i+1}: {repr(lines[i][:120])}")

# Also show what method definition precedes this
print("\n--- Searching back for method def ---")
for i in range(2355, 2320, -1):
    if 'def ' in lines[i]:
        print(f"  Method at line {i+1}: {lines[i].strip()}")
        break
