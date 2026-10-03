import os
import subprocess
import sys

STAGE_LEVERS = (
    "RLM_FB_LOCOMOTION_CONTROLLER",
    "RLM_FB_RECOVERY_CONTROLLER",
    "RLM_FB_STATE_CONTROLLER",
    "RLM_FB_INTENT_CONTROLLER",
)
OTHER_LEVERS = (
    "RLM_FB_BREAK_LATCH",
    "RLM_FB_CHASE_INTERCEPT_TRACK",
    "RLM_FB_LINE_OCCUPIED",
    "RLM_FB_CHASE_INTERCEPT",
    "RLM_FB_PRETACKLE_CHASE_EXEMPT",
)

ARMS = (
    ("stage4_off", {"RLM_FB_LOCOMOTION_CONTROLLER": "0"}),
    ("stage3_off", {"RLM_FB_RECOVERY_CONTROLLER": "0"}),
    ("stage2_off", {"RLM_FB_STATE_CONTROLLER": "0"}),
    ("all_stage_off", {k: "0" for k in STAGE_LEVERS}),
    ("all_stage_off_all_off",
     {k: "0" for k in STAGE_LEVERS + OTHER_LEVERS}),
    ("shipped_default", {}),
)

lines = []
for label, overrides in ARMS:
    env = dict(os.environ)
    for key, value in overrides.items():
        env[key] = value
    proc = subprocess.run(
        [sys.executable, "verify_kickoff_fullback.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        check=False,
    )
    text = proc.stdout.decode("utf-8", errors="replace")
    hits = [l.strip() for l in text.splitlines()
            if "ASSERT" in l or "RESULT:" in l]
    lines.append("%-26s rc=%d" % (label, proc.returncode))
    for hit in hits:
        lines.append("      " + hit)

with open("tmp/s4_kick_ab.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
print("\n".join(lines))
