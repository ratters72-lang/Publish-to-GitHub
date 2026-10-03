import contextlib
import io
import os
import runpy
import sys
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

# The script lives in tmp/, so the repo root must be on sys.path explicitly.
sys.path.insert(0, os.getcwd())

# Force the guard OFF at the module level.  ``pretackle_machine`` imports
# ``is_tackle_engage_guard`` lazily inside the function body and that function
# calls the module-global ``tackle_engage_guard_enabled`` at call time, so
# this patch fully disables the guard.
import game.defence.defence_lvl_1.tackle_engage_guard as g
g.tackle_engage_guard_enabled = lambda tuners: False

VERIFIERS = [
    "verify_pretackle_machine.py",
    "verify_pretackle_classifier.py",
    "verify_tackle_begin_glide.py",
]

report = []
for v in VERIFIERS:
    buf = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf):
            runpy.run_path(v, run_name="__main__")
    except SystemExit as e:
        code = int(e.code or 0)
    except Exception:
        code = 99
        buf.write("\nEXCEPTION:\n" + traceback.format_exc())
    out = buf.getvalue()
    report.append("=== %s  exit=%s  (GUARD DISABLED) ===" % (v, code))
    for line in out.splitlines():
        s = line.strip()
        if ("[FAIL]" in line or "RESULT:" in line or "Traceback" in line
                or s.startswith("File ") or s.endswith("Error")):
            report.append(line)
    report.append("")

text = "\n".join(report)
with open("tmp/guard_attribution_out.txt", "w", encoding="utf-8") as f:
    f.write(text)
print(text)
