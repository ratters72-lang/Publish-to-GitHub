"""Run the FB verifier battery one verifier per line, minimal output."""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(ROOT)

src = open(os.path.join(REPO, "run_fb_verifiers.py"), encoding="utf-8").read()
names = re.findall(r'"(verify_[a-z0-9_]+\.py)"', src)

env = dict(os.environ)
env["SDL_VIDEODRIVER"] = "dummy"
env["SDL_AUDIODRIVER"] = "dummy"
env["PYTHONIOENCODING"] = "utf-8"

bad = []
print(f"battery: {len(names)} verifiers")
for name in names:
    path = os.path.join(REPO, name)
    if not os.path.exists(path):
        print(f"  [SKIP] {name} (not found)")
        continue
    try:
        proc = subprocess.run(
            [sys.executable, name], cwd=REPO, env=env,
            capture_output=True, text=True, timeout=420,
        )
        code = proc.returncode
        tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        tail = tail[-1] if tail else ""
    except subprocess.TimeoutExpired:
        code, tail = "TIMEOUT", ""
    flag = "PASS" if code == 0 else "FAIL"
    if code != 0:
        bad.append(name)
    print(f"  [{flag}] {name} (exit={code}) {tail[:110]}")

print(f"RESULT: {len(names) - len(bad)}/{len(names)} pass")
for name in bad:
    print(f"  FAILED: {name}")
sys.exit(1 if bad else 0)
