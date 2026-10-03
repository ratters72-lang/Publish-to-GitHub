with open('world/rules/movement_contracts.py', 'r', encoding='utf-8') as f:
    data = f.read()

# Find the belt-and-suspenders section and add DEF_RETREAT_10 freeze override for PTB phases
old = """    # -- POST-PTB MARKER LOCK: freeze markers during lock window --"""

new = """        # -- PTB FREEZE INVARIANT: DEF_RETREAT_10 MUST be frozen during PTB phases --
        # Even though MOVEMENT_RULES says DEF_RETREAT_10 must_be_frozen for
        # PTB_PREP/PTB_EXEC/PTB, external code paths (phase handlers, PTBManager,
        # DH controller, release transition) can unfreeze them.  This belt-and-
        # suspenders override guarantees the invariant regardless of what other
        # code does.
        if phase_str in ("PTB_PREP", "PTB_EXEC", "PTB"):
            for p in (getattr(world, "defending_team", []) or []):
                if getattr(p, "defender_role", None) == DefenderRole.DEF_RETREAT_10:
                    p.frozen = True
                    p.freeze_timer = 0.0
                    p.vx = 0.0
                    p.vy = 0.0
                    p.target_x = p.x
                    p.target_y = p.y

        # -- POST-PTB MARKER LOCK: freeze markers during lock window --"""

if old in data:
    data = data.replace(old, new, 1)
    print("Patch applied: DEF_RETREAT_10 freeze override for PTB phases")
else:
    print("FAILED: pattern not found")

with open('world/rules/movement_contracts.py', 'w', encoding='utf-8') as f:
    f.write(data)

print("File written")
