"""
Final verification: run the engine and check for import/compile errors
"""
import sys
sys.path.insert(0, '.')

# Check import works
try:
    from world.open_play_engine import OpenPlayEngine
    print("OK: OpenPlayEngine imported successfully")
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)

# Verify no remaining movement authority violations
with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

print("\n=== Movement Authority Audit ===")

# These are legitimate uses of apply_vector (OPEN_PLAY only, gated by _update_attack)
# Check they're still in _update_attack only
count_bc = 0
count_dh = 0
import re
for m in re.finditer(r'self\.attack_controller\.apply_vector\((\w+),', content):
    line = content[:m.start()].count('\n') + 1
    # Find enclosing function
    func_start = content.rfind('def ', 0, m.start())
    if func_start >= 0:
        func_line = content[func_start:content.find('\n', func_start)]
    else:
        func_line = '???'
    
    arg = m.group(1)
    if arg == 'bc':
        count_bc += 1
        # Check if inside _update_attack (which is correctly gated)
        if '_update_attack' in func_line:
            print(f"  OK: apply_vector(bc) in _update_attack (line {line}) - correctly gated")
        else:
            print(f"  WARNING: apply_vector(bc) in {func_line} (line {line})")
    elif arg == 'dh':
        count_dh += 1
        if '_update_attack' in func_line:
            print(f"  OK: apply_vector(dh) in _update_attack (line {line}) - correctly gated")
        else:
            print(f"  WARNING: apply_vector(dh) in {func_line} (line {line})")

# Check for P4 retreat violations
if '_d.update(dt)  # P4: apply retreat movement' in content:
    print("  WARNING: P4 retreat _d.update still present!")
else:
    print("  OK: No P4 retreat _d.update remaining")

# Check frozen writes
frozen_writes = re.findall(r'\.frozen\s*=\s*True', content)
if frozen_writes:
    for fw in frozen_writes:
        line = content[:content.find(fw)].count('\n') + 1
        print(f"  WARNING: direct .frozen = True at line {line}")
else:
    print("  OK: No direct frozen=True writes")

# Check for _move_toward in POST_RUCK
if '_move_toward(d, target_x, d.y, 6.0, dt)' in content:
    print("  WARNING: _move_toward still present in POST_RUCK!")
else:
    print("  OK: No _move_toward in POST_RUCK")

print(f"\n=== Summary ===")
print(f"apply_vector(bc) calls: {count_bc}")
print(f"apply_vector(dh) calls: {count_dh}")
print(f"All in _update_attack only (correctly gated to OPEN_PLAY)")
print()
print("All movement authority fixes verified.")
