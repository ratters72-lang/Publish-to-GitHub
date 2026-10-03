"""Defence-wide regression sweep for the Stage-4 locomotion-ownership change.

Runs every defence/TES verifier in the repository root that is NOT already part
of ``run_fb_verifiers.py`` and prints one JSON summary line.

Verdict parsing is LINE-ANCHORED and whitespace-normalised, because the
repository's verifiers pad their labels inconsistently ("FAILURES     : 0",
"VERDICT      : PASS") and use several reporting families.  Matching bare words
anywhere in the log is wrong in both directions: "0 checks FAILED" is a success,
and a check whose NAME contains "failed" is not a failure.

A failure verdict on ANY line therefore wins over success prose, so a partial
failure can never report green.  UNKNOWN fails the sweep, so silence is never
mistaken for a pass.

The parser is covered by ``tmp/parser_test.py``.

NOTE: ``verify_tes_width_authority_contract.py`` does not exist in this tree
(the TES-width authority stage shipped only the OBLIGATION verifier, which is
in the list below).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

VERIFIERS = [
    # --- TES / depth / width / glide authority -------------------------
    "verify_tes_depth_authority_contract.py",
    "verify_tes_depth_obligation_contract.py",
    "verify_tes_width_obligation_contract.py",
    "verify_tes_glide_smoothing_contract.py",
    "verify_tes_drive_v1_contract.py",
    "verify_depth_boundary_invariant.py",
    "verify_post_tes_corridor_contract.py",
    "verify_exit_boundary_ownership_contract.py",
    # --- contest / contact / tackle surfaces ---------------------------
    "verify_contest_preservation_contract.py",
    "verify_contest_transport_contract.py",
    "verify_contact_authority_contract.py",
    "verify_tackle_formation_contract.py",
    # --- open play / release / pre-tackle / freeze ---------------------
    "verify_open_play_stall_authority.py",
    "verify_release_target_field_clamp.py",
    "verify_pretackle_corridor.py",
    "verify_tackle_commit.py",
    "verify_freeze_clock_contract.py",
    "verify_freeze_expiry_pursuit_contract.py",
    "verify_post_miss_pursuit_contract.py",
    # --- fullback-adjacent verifiers outside the FB CI battery ---------
    "verify_arrival_preserves_state.py",
    "verify_arrival_state_authority_ab.py",
    "verify_mmdl_fullback_exclusion.py",
    "verify_fullback_can_tackle.py",
    "verify_fb_determinism.py",
    "verify_fb_emergency_initiation.py",
    "verify_fullback_shared_locomotion.py",
    # --- retreat ------------------------------------------------------
    "verify_retreat_backpedal_contract.py",
    "verify_retreat_fullback_contracts.py",
]

_FAIL_LINE_RE = re.compile(r"^(FAIL:|RESULT: FAIL|VERDICT ?: FAIL)")
_FAILURES_RE = re.compile(r"^FAILURES ?: (\d+)")
_CHECKS_FAIL_RE = re.compile(r"^(\d+) CHECKS? (?:FAILED|FAIL)\b")

_PASS_LINE_RE = re.compile(
    r"^(PASS:"
    r"|RESULT: ALL"
    r"|RESULT: PASS"
    r"|RESULT: CONTRACT HOLDS"
    r"|VERDICT ?: PASS"
    r"|FAILURES ?: 0)"
)
_CHECKS_PASS_RE = re.compile(r"CHECKS? PASS(?:ED)?\b")
_ZERO_FAILURES_RE = re.compile(r"\b0 FAILURES\b")


def _parse(text: str) -> str:
    """Return PASS / FAIL / UNKNOWN for one verifier's log."""
    failed = False
    passed = False

    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw.strip().upper())
        if not line:
            continue

        if _FAIL_LINE_RE.match(line):
            failed = True
            continue

        match = _FAILURES_RE.match(line)
        if match and int(match.group(1)) > 0:
            failed = True
            continue

        match = _CHECKS_FAIL_RE.match(line)
        if match and int(match.group(1)) > 0:
            failed = True
            continue

        if _PASS_LINE_RE.match(line):
            passed = True
        elif _CHECKS_PASS_RE.search(line) or _ZERO_FAILURES_RE.search(line):
            passed = True

    if failed:
        return "FAIL"
    if passed:
        return "PASS"
    return "UNKNOWN"


def main() -> int:
    out: dict[str, dict[str, str]] = {}
    failed = False
    for name in VERIFIERS:
        if not os.path.exists(name):
            out[name] = {"result": "MISSING"}
            failed = True
            continue
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        ) as handle:
            log_path = handle.name
        try:
            with open(log_path, "w", encoding="utf-8") as log_file:
                subprocess.run(
                    [sys.executable, name],
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            with open(log_path, encoding="utf-8", errors="replace") as log_file:
                text = log_file.read()
            result = _parse(text)
            entry: dict[str, str] = {"result": result}
            if result != "PASS":
                interesting = [
                    line.strip()
                    for line in text.splitlines()
                    if "RESULT" in line.upper()
                    or line.strip().upper().startswith(("FAIL", "VERDICT", "PASS"))
                ]
                if interesting:
                    entry["tail"] = " | ".join(interesting[-3:])
            out[name] = entry
            if result != "PASS":
                failed = True
        finally:
            try:
                os.remove(log_path)
            except OSError:
                pass

    print("DEFENCE_REGRESSION_JSON: " + json.dumps(out, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
