"""Run every root-level verify_*.py / probe_*.py / run_*verifiers.py script
and report a per-script PASS/FAIL summary.

Each script is executed as a subprocess with the project root on sys.path.
Exit code 0 == PASS.
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKIP = {
    "run_rollout_verifiers.py",     # already a runner; executed explicitly
    "run_fb_verifiers.py",
    "verify_rollout_slot_arbitration.py",  # keep, it is a verifier
}


def collect():
    pats = ["verify_*.py", "probe_*.py"]
    seen = []
    for pat in pats:
        for path in sorted(glob.glob(os.path.join(ROOT, pat))):
            name = os.path.basename(path)
            if name in SKIP:
                continue
            seen.append(name)
    return seen


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    failures = []
    passed = []
    for script in collect():
        try:
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", script],
                cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=900,
            )
            code = proc.returncode
            tail = (proc.stdout or b"").decode("utf-8", "replace").strip().splitlines()
            tail = tail[-1] if tail else ""
        except subprocess.TimeoutExpired:
            code = 124
            tail = "TIMEOUT"
        status = "PASS" if code == 0 else "FAIL"
        (passed if code == 0 else failures).append(script)
        print(f"[{status}] exit={code:<4} {script}   {tail[:110]}")
    print("\n" + "=" * 72)
    print(f"PASSED: {len(passed)}   FAILED: {len(failures)}")
    if failures:
        print("FAILURE(S):")
        for f in failures:
            print("  -", f)
        return 1
    print("RESULT: ALL SWEEP SCRIPTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
