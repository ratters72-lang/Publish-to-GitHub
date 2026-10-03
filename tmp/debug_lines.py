with open('world/open_play_engine.py','r',encoding='utf-8') as f:
    lines=f.readlines()

# Show context around line 794 (index 793)
print("=== Around line 794 (apply_vector bc) ===")
for i in range(785, min(805, len(lines))):
    print(f"{i+1:4d}: {lines[i]}", end='')

print("\n=== Around line 1989 (_d.update release) ===")
for i in range(1979, min(2000, len(lines))):
    print(f"{i+1:4d}: {lines[i]}", end='')

print("\n=== Around line 2253 (apply_vector dh) ===")
for i in range(2243, min(2270, len(lines))):
    print(f"{i+1:4d}: {lines[i]}", end='')
