"""Run every top-level verify_*.py and report pass/fail. Operational only."""

import glob
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = os.path.abspath(".")
TIMEOUT_S = 300


def main():
    scripts = sorted(glob.glob(os.path.join(ROOT, "verify_*.py")))
    print("=" * 78)
    print(f"DEFENCE VERIFIER SWEEP — {len(scripts)} scripts")
    print("=" * 78)

    passed, failed, errored = [], [], []
    for path in scripts:
        name = os.path.basename(path)
        try:
            proc = subprocess.run(
                [sys.executable, name],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_S,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            errored.append((name, f"TIMEOUT after {TIMEOUT_S}s", ""))
            print(f"  [TIMEOUT] {name}")
            continue

        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            passed.append(name)
            print(f"  [PASS] {name}")
        elif proc.returncode == 1:
            tail = "\n".join(out.strip().splitlines()[-14:])
            failed.append((name, tail))
            print(f"  [FAIL] {name}")
        else:
            tail = "\n".join(out.strip().splitlines()[-14:])
            errored.append((name, f"exit={proc.returncode}", tail))
            print(f"  [ERR ] {name} (exit {proc.returncode})")

    print("\n" + "=" * 78)
    print(f"TOTALS: {len(passed)} pass / {len(failed)} fail / {len(errored)} error")
    print("=" * 78)

    if failed:
        print("\n---- FAILURES ----")
        for name, tail in failed:
            print(f"\n### {name}\n{tail}")
    if errored:
        print("\n---- ERRORS ----")
        for name, why, tail in errored:
            print(f"\n### {name} ({why})\n{tail}")

    sys.exit(1 if (failed or errored) else 0)


if __name__ == "__main__":
    main()
