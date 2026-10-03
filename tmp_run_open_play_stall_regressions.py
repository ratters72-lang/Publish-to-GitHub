"""tmp_run_open_play_stall_regressions.py — regression sweep for the
OPEN_PLAY stationary-body fix (target field bound + zero-speed-HOLD band
exemption).

Runs every verifier whose contract touches a module the fix changed or that
observes defender targets:

    target_field_bound / pre_tackle_authority / defensive_behaviour_unified
    writer_priority_bands   (band arbitration + the HOLD exemption)
    player.player           (the target field bound in Player.update)
    game.managers.defence_manager  (the post-writer field bound)

Each verifier runs in its own subprocess exactly as a developer would run it
(launched from the repository root).  A verifier FAILS when its exit code is
non-zero OR its output carries a failure marker.  Full output is written to
tmp_stall_regression_output.txt so a failure can be read without re-running.

Run:  python tmp_run_open_play_stall_regressions.py
"""

import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

HERE = os.path.dirname(os.path.abspath(__file__))

OUTPUT_PATH = os.path.join(HERE, "tmp_stall_regression_output.txt")

# (label, script) — the sweep.  Order runs the tightest contracts first.
VERIFIERS = (
    ("pretackle corridor",        "verify_pretackle_corridor.py"),
    ("tackle commit",             "verify_tackle_commit.py"),
    ("contact authority",         "verify_contact_authority_contract.py"),
    ("release target clamp",      "verify_release_target_field_clamp.py"),
    ("target_x glide",            "verify_target_x_glide.py"),
    ("depth boundary invariant",  "verify_depth_boundary_invariant.py"),
    ("tes depth authority",       "verify_tes_depth_authority_contract.py"),
    ("tes depth obligation",      "verify_tes_depth_obligation_contract.py"),
    ("tes width authority",       "verify_tes_width_authority_contract.py"),
    ("tes width obligation",      "verify_tes_width_obligation_contract.py"),
    ("tes glide smoothing",       "verify_tes_glide_smoothing_contract.py"),
    ("fullback role",             "verify_fullback_role.py"),
    ("fullback geometry",         "verify_fullback_geometry.py"),
    ("fullback recover",          "verify_fullback_recover_contract.py"),
    ("fullback lateral excl.",    "verify_fullback_lateral_priority_exclusion.py"),
    ("fullback lateral open",     "verify_fullback_lateral_open_play_contract.py"),
    ("fullback continuity",       "verify_fullback_continuity.py"),
    ("fullback can tackle",       "verify_fullback_can_tackle.py"),
    ("fullback emergency",        "verify_fullback_emergency_defender.py"),
    ("mmdl fullback exclusion",   "verify_mmdl_fullback_exclusion.py"),
    ("retreat obligation",        "verify_retreat_obligation_contract.py"),
    ("retreat fullback",          "verify_retreat_fullback_contracts.py"),
    ("retreat backpedal",         "verify_retreat_backpedal_contract.py"),
    ("exit boundary ownership",   "verify_exit_boundary_ownership_contract.py"),
    ("post-tes corridor",         "verify_post_tes_corridor_contract.py"),
    ("tackle formation",          "verify_tackle_formation_contract.py"),
    ("tackle exchange multi",     "verify_tackle_exchange_multi_body_contract.py"),
    ("contest preservation",      "verify_contest_preservation_contract.py"),
    ("contest transport",         "verify_contest_transport_contract.py"),
    ("contest record schema",     "verify_contest_record_schema_contract.py"),
    ("anticipation reconnected",  "verify_anticipation_reconnected.py"),
    ("kickoff fullback",          "verify_kickoff_fullback.py"),
    ("tes drive v1",              "verify_tes_drive_v1_contract.py"),
    ("OPEN_PLAY stall authority", "verify_open_play_stall_authority.py"),
)

# Markers that mean "this verifier reported a failure" even if it exited 0.
# Kept deliberately NARROW: a bare "FAILURE" / "FAILED" substring also appears
# in ordinary verifier prose and in the fail-message text of a PASSING run, so
# scanning for it produced a false FAIL for verify_contact_authority_contract
# (exit 0, no RESULT: FAIL) on the first sweep.  Only an explicit verdict line
# or a crash counts.
FAILURE_MARKERS = (
    "RESULT: FAIL",
    "Traceback (most recent call last)",
    "AssertionError",
)


def run_one(script, log):
    """Run one verifier; always return (status, detail, tail_lines)."""
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        return "MISSING", "not found", []
    try:
        proc = subprocess.run(
            [sys.executable, script],
            cwd=HERE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "exceeded 900 s", []

    out = proc.stdout.decode("utf-8", errors="replace")
    log.append("")
    log.append("=" * 96)
    log.append("=== %s  (exit %d)" % (script, proc.returncode))
    log.append("=" * 96)
    log.append(out)

    marker = next((m for m in FAILURE_MARKERS if m in out), None)
    tail = [ln for ln in out.splitlines() if ln.strip()][-8:]
    if proc.returncode != 0 or marker is not None:
        return "FAIL", "exit=%d%s" % (
            proc.returncode,
            (" | %s" % marker) if marker else "",
        ), tail
    return "PASS", "exit=0", []


def main():
    log = ["Regression sweep — OPEN_PLAY stationary-body fix", ""]
    statuses = []
    print("Regression sweep — OPEN_PLAY stationary-body fix")
    print("-" * 78)

    for label, script in VERIFIERS:
        status, detail, tail = run_one(script, log)
        statuses.append((label, script, status, detail))
        print("  %-6s %-26s %s%s"
              % (status, label, script,
                 ("  [%s]" % detail) if detail and status != "PASS" else ""))
        if status == "FAIL":
            for ln in tail:
                print("          | %s" % ln[:160])

    print("-" * 78)
    n_fail = sum(1 for _l, _s, st, _d in statuses
                 if st in ("FAIL", "TIMEOUT", "MISSING"))
    n_pass = sum(1 for _l, _s, st, _d in statuses if st == "PASS")
    print("  PASS = %d    FAIL/TIMEOUT/MISSING = %d    total = %d"
          % (n_pass, n_fail, len(statuses)))
    print("  full output written to %s" % os.path.basename(OUTPUT_PATH))

    log.append("")
    log.append("SUMMARY: PASS=%d  FAIL=%d" % (n_pass, n_fail))
    for label, script, status, detail in statuses:
        log.append("  %-6s %-26s %s" % (status, label, script))

    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")

    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
