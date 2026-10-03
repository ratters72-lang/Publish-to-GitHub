"""Quick integrity check."""
import sys

try:
    with open("world/open_play_engine.py", encoding="utf-8") as f:
        text = f.read()
    compile(text, "world/open_play_engine.py", "exec")
    print("SYNTAX OK")
    
    # Count key method occurrences
    for name in ["update", "_apply_defence_sdm", "_set_phase", "_prepare_release", "_update_tackle_drive", "_update_tackle_dominance", "_update_release", "_assign_markers_for_new_tackle", "_clear_tes_artefacts"]:
        count = text.count(f"def {name}")
        print(f"  def {name}: {count}x")
        
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")
    sys.exit(1)
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
