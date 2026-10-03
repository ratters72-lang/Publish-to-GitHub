"""dump_tc.py - dump _apply_tackler_commit body + call sites."""
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

s = open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
         encoding="utf-8", errors="replace").read()

# Body: from the def to the next top-level def / class marker.
i = s.find("def _apply_tackler_commit(")
print("DEF at", i)
end = s.find("\n\n\n", i)
print(s[i:end])

# Call sites.
print("\n\n===== CALL SITES =====")
for j in (1282, 2311, 3206):
    print(f"--- around {j} ---")
    print(s[j - 300:j + 200])
