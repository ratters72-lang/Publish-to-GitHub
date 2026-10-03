"""run_tes_lane_regression.py — lane-true retreat regression sweep.

Runs every verifier that exercises the two authorities this task touched:

  * ``game/defence/defence_lvl_1/tackle_state_gate.py``  — the TACKLE-STATE
    peel + release sweep (now exempts held structure bodies), and
  * ``game/defence/defence_lvl_1/pre_tackle_authority.py`` — the unified
    pre-tackle depth lock (now exempts held structure bodies).

Each verifier is run as a subprocess with its own log; the exit code and the
verifier's RESULT line are appended to a summary file as soon as the run
finishes, so progress survives an interrupted sweep.

    python run_tes_lane_regression.py            # run every verifier
    python run_tes_lane_regression.py 0 10       # run a slice [0:10]

Exit code 0 when every verifier in the slice returned 0.
"""
import io
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

TIMEOUT_S = 600
SUMMARY = "tmp_tes_lane_regression_summary.txt"

VERIFIERS = (
    "verify_tes_lane_true_retreat.py",
    "verify_tes_drive_structure_hold.py",
    "verify_tes_unified_drive_pipeline.py",
    "verify_tes_line_width_hold.py",
    "verify_tes_lateral_authority_phase_gate.py",
    "verify_tes_seal.py",
    "verify_tes_diagnostics.py",
    "verify_stage6_tackle_radius_gating.py",
    "verify_stage7_role_gated_authority.py",
    "verify_pretackle_machine.py",
    "verify_pretackle_classifier.py",
    "verify_def_line_ready_frozen_drive.py",
    "verify_tackle_drive_out_of_band.py",
    "verify_post_tes_settle.py",
    "verify_release_clears_tackler_commit.py",
    "verify_marker_drive_lock.py",
    "verify_ruck_pin_glide.py",
    "verify_ruck_pin_role_aware.py",
    "verify_seed_hold.py",
    "verify_defensive_state_reset.py",
    "verify_no_tackle_no_defence.py",
    "verify_line_break_static_guard.py",
    "verify_secondary_width_grace.py",
    "verify_count_authority_lane_spacing.py",
    "verify_depth_roles_side_width_consistency.py",
    "verify_retreat_width_consistency.py",
    "verify_width_tuners.py",
    "verify_open_play_tackle_radius_chase.py",
    "verify_slot_out_late_chase_histogram.py",
    "verify_anticipation_reconnected.py",
)


def _result_line(text):
    """Return the verifier's RESULT line (or the last non-empty line)."""
    for line in reversed(text.splitlines()):
        if "RESULT" in line:
            return line.strip()
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return "(no output)"


def run_one(name):
    """Run one verifier; return (name, exit_code, result_line, seconds)."""
    import time
    started = time.time()
    log_path = "tmp_" + name.replace(".py", "") + ".log"
    try:
        proc = subprocess.run(
            [sys.executable, name],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            encoding="utf-8", errors="replace",
        )
        code = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code = -9
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        output += f"\n[TIMEOUT] exceeded {TIMEOUT_S}s"
    except Exception as exc:  # pragma: no cover - harness safety net
        code = -1
        output = f"[HARNESS ERROR] {type(exc).__name__}: {exc}"
    with io.open(log_path, "w", encoding="utf-8") as fh:
        fh.write(output)
    return name, code, _result_line(output), time.time() - started


def main(argv):
    lo = int(argv[0]) if len(argv) > 0 else 0
    hi = int(argv[1]) if len(argv) > 1 else len(VERIFIERS)
    names = VERIFIERS[lo:hi]
    if not names:
        print(f"no verifier in slice [{lo}:{hi}] of {len(VERIFIERS)}")
        return 2
    print(f"lane-true regression sweep: {len(names)} verifier(s) "
          f"[{lo}:{hi}] of {len(VERIFIERS)}")
    failures = []
    for name in names:
        vname, code, result, secs = run_one(name)
        status = "PASS" if code == 0 else f"FAIL({code})"
        line = f"{status:<9} {secs:6.1f}s  {vname:<45} {result}"
        print(line, flush=True)
        with io.open(SUMMARY, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if code != 0:
            failures.append((vname, code, result))
    print("-" * 78)
    if not failures:
        print(f"SWEEP OK — all {len(names)} verifier(s) returned 0.")
        return 0
    print(f"SWEEP FAILURES ({len(failures)}):")
    for vname, code, result in failures:
        print(f"  - {vname} (exit {code}): {result}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
