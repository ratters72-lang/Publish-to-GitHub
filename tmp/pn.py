"""pn.py - find run_defensive_pipeline / run_frame_preamble call sites."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

for f in ("world/open_play_engine.py", "game/kickoff_engine.py",
          "game/managers/defence_manager.py"):
    s = open(f, encoding="utf-8", errors="replace").read()
    print("=" * 60)
    print(f)
    print("=" * 60)
    for pat in ("run_defensive_pipeline", "run_frame_preamble",
                "clear_frame_geometry"):
        for m in re.finditer(pat, s):
            st = s.rfind(chr(10), 0, m.start()) + 1
            en = s.find(chr(10), m.end())
            ln = s[st:en].strip()
            if "import" in ln and pat != ln.strip():
                continue
            print(f"  {m.start():8d}: {ln[:120]}")
