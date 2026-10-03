"""tmp runner — A/B the four broad-sweep failures against the glide gauge.

If a failure is CAUSED by the bounded-glide change, flipping the gauge off
(``RLM_GLIDE_SMOOTH=0`` -> byte-identical legacy path) must make it PASS.
If it fails identically with the gauge on and off, it is independent of the
glide change and predates it.
"""
from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

TARGETS = [
    "verify_tackle_engage_guard.py",
    "verify_retreat_width_consistency.py",
    "verify_defensive_pipeline.py",
    "probe_tes_lane_true.py",
]

TIMEOUT_S = 1200


def run(name: str, smooth: bool) -> tuple[int, str]:
    child_env = dict(os.environ)
    child_env["PYTHONIOENCODING"] = "utf-8"
    if smooth:
        child_env.pop("RLM_GLIDE_SMOOTH", None)
    else:
        child_env["RLM_GLIDE_SMOOTH"] = "0"
    try:
        proc = subprocess.run(
            [sys.executable, name],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            encoding="utf-8", errors="replace", env=child_env,
        )
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    out = (proc.stdout or "").strip().splitlines()
    err = (proc.stderr or "").strip().splitlines()
    lines = out or err
    return proc.returncode, " | ".join(lines[-3:]) if lines else ""


def main() -> int:
    rows = []
    for name in TARGETS:
        rc_on, tail_on = run(name, smooth=True)
        rc_off, tail_off = run(name, smooth=False)
        rows.append((name, rc_on, rc_off, tail_on, tail_off))

    print("=" * 100)
    print(f"{'target':<42} {'gauge ON':>9} {'gauge OFF':>10}   verdict")
    print("=" * 100)
    caused = []
    for name, rc_on, rc_off, tail_on, tail_off in rows:
        if rc_on == 0 and rc_off != 0:
            verdict = "CAUSED BY THE CHANGE (passes only with the gauge on)"
            caused.append(name)
        elif rc_on != 0 and rc_off == 0:
            verdict = "pre-existing: fails EVEN with the gate off"
        elif rc_on != 0 and rc_off != 0:
            verdict = "PRE-EXISTING (fails identically both ways)"
        else:
            verdict = "clean both ways"
        print(f"{name:<42} {rc_on:>9} {rc_off:>10}   {verdict}")
    print("=" * 100)
    for name, rc_on, rc_off, tail_on, tail_off in rows:
        print(f"\n--- {name} ---")
        print(f"  gauge ON  (rc={rc_on}): {tail_on}")
        print(f"  gauge OFF (rc={rc_off}): {tail_off}")
    print("\n" + "=" * 100)
    print(f"CAUSED BY THE CHANGE: {caused or 'none'}")
    return 1 if caused else 0


if __name__ == "__main__":
    sys.exit(main())
