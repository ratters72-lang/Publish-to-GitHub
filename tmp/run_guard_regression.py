import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

# Child verifiers print non-ASCII glyphs (e.g. "→").  Under a redirected pipe
# Windows Python defaults to cp1252, which cannot encode them, so the child
# dies with UnicodeEncodeError and a bogus non-zero exit code.  Force UTF-8
# for the children so exit codes reflect assertion results, not console
# encoding.
_ENV = dict(os.environ)
_ENV["PYTHONIOENCODING"] = "utf-8"

VERIFIERS = [
    "verify_pretackle_machine.py",
    "verify_pretackle_classifier.py",
    "verify_tackle_engage_guard.py",
    "verify_open_play_tackle_radius_chase.py",
    "verify_no_tackle_no_defence.py",
    "verify_stage3_unified_authority.py",
    "verify_stage4_movement_state_rebuild.py",
    "verify_stage6_tackle_radius_gating.py",
    "verify_stage7_role_gated_authority.py",
    "verify_rollout_slot_arbitration.py",
    "verify_tes_seal.py",
    "verify_seed_hold.py",
    "verify_flatfoot_step_in.py",
    "verify_def_line_ready_frozen_drive.py",
    "verify_tackle_begin_glide.py",
    "verify_stage5_phase_gated_tuning.py",
    "verify_stage9_anticipation_layer.py",
    "verify_anticipation_reconnected.py",
    "verify_depth_roles_side_width_consistency.py",
    "verify_line_break_static_guard.py",
    "verify_marker_drive_lock.py",
]

lines = []
passed = []
failed = []
missing = []

for v in VERIFIERS:
    if not os.path.exists(v):
        missing.append(v)
        lines.append("MISSING  %s" % v)
        continue
    try:
        r = subprocess.run(
            [sys.executable, v],
            capture_output=True, text=True, timeout=900, errors="replace",
            env=_ENV,
        )
    except subprocess.TimeoutExpired:
        failed.append(v)
        lines.append("TIMEOUT  %s" % v)
        continue
    except Exception as e:
        failed.append(v)
        lines.append("ERROR    %s -> %r" % (v, e))
        continue

    out = r.stdout or ""
    fails = [
        ln.strip() for ln in out.splitlines()
        if "[FAIL]" in ln or ln.strip().startswith("FAIL:")
    ]
    if r.returncode == 0 and not fails:
        passed.append(v)
        lines.append("PASS     %s" % v)
    else:
        failed.append(v)
        lines.append("FAIL(%s) %s  -- %d failing assertion(s)"
                     % (r.returncode, v, len(fails)))
        for f in fails[:25]:
            lines.append("           " + f)
        if not fails:
            tail = (out or r.stderr or "")[-350:]
            lines.append("           tail: " + tail.replace("\n", " | "))

summary = (
    "\n=== GUARD REGRESSION SUMMARY ===\n"
    "PASS %d | FAIL %d | MISSING %d\n" % (len(passed), len(failed), len(missing))
)
if failed:
    summary += "FAILED: %s\n" % ", ".join(failed)
if missing:
    summary += "MISSING: %s\n" % ", ".join(missing)

text = "\n".join(lines) + "\n" + summary
with open("tmp/guard_regression_out.txt", "w", encoding="utf-8") as f:
    f.write(text)
print(text)
