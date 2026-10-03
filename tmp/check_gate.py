with open('world/open_play_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Verify the gate pattern exists
if 'if self._phase != "OPEN_PLAY":\n            return\n\n        print("_update_defenders_tes_phases FIRING phase=", self._phase)' in content:
    print("GATE IS CORRECT - defence path in TES phase handlers is now DEAD")
    print("The function returns immediately during TACKLE_INIT/CONTACT/DRIVE/DOMINANCE")
else:
    print("Checking alternate pattern...")
    # Check for slightly different spacing
    if 'if self._phase != "OPEN_PLAY":' in content and 'return' in content.split('if self._phase != "OPEN_PLAY":')[1].split('\n')[0]:
        print("Gate found with different spacing - still correct")
    else:
        print("GATE NOT FOUND - check the edit")
