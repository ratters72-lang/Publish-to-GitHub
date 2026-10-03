"""Unit-test the sweep parser against the real log shapes that fooled v1."""

from __future__ import annotations

import importlib.util
import sys

spec = importlib.util.spec_from_file_location("sweep", "tmp/run_defence_regression.py")
assert spec is not None and spec.loader is not None
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)

CASES = [
    (
        "contact_authority ALL 146 PASSED (prose contains the word FAILED)",
        "[PASS] check 1 holds\n"
        "[PASS] check 2 holds (0 checks FAILED so far)\n"
        "RESULT: ALL 146 CHECKS PASSED\n",
        "PASS",
    ),
    (
        "contest_transport ALL 77 PASSED",
        "RESULT: ALL 77 CHECKS PASSED\n",
        "PASS",
    ),
    (
        "emergency_initiation padded FAILURES/VERDICT",
        "[DEPRECATED] game/defence/ imported - no-op\n"
        "sections run : 7\n"
        "FAILURES     : 0\n"
        "VERDICT      : PASS\n",
        "PASS",
    ),
    (
        "can_tackle bare PASS: per-check lines",
        "PASS: tagged fullback is drafted as PRIMARY on a genuine break\n"
        "PASS: lane candidacy follows the single break gate\n"
        "PASS: the fullback tag survives tackle-role admission\n",
        "PASS",
    ),
    (
        "tes_drive_v1 PASS lines + RESULT: CONTRACT HOLDS",
        "PASS the engine does not import the module at module scope\n"
        "PASS the engine's OWN TES chain handlers are still defined\n"
        "RESULT: CONTRACT HOLDS\n",
        "PASS",
    ),
    (
        "open_play_stall real failure",
        "[PASS] no non-frozen OPEN_PLAY body holds an out-of-field target\n"
        "[FAIL] no non-frozen body holds a live target through a sustained stall\n"
        "RESULT: FAIL - 1 check(s): ['no non-frozen body holds a live target']\n",
        "FAIL",
    ),
    (
        "preservation contract zero-failure prose",
        "RESULT: the preservation contract holds - 89 checks, 0 failures\n",
        "PASS",
    ),
    (
        "exit boundary INFO-findings prose",
        "RESULT: all checks PASS (7 INFO findings)\n",
        "PASS",
    ),
    (
        "post_tes corridor prose",
        "RESULT: all checks PASS (0 INFO findings)\n",
        "PASS",
    ),
    (
        "tackle_formation zero-failure prose",
        "RESULT: the formation contract holds - 155 checks, 0 failures\n",
        "PASS",
    ),
    (
        "explicit per-check failure wins over later success prose",
        "FAIL: the latch never releases\n"
        "RESULT: ALL 12 CHECKS PASSED\n",
        "FAIL",
    ),
    (
        "non-zero FAILURES count",
        "FAILURES     : 3\nVERDICT      : FAIL\n",
        "FAIL",
    ),
    (
        "non-zero checks-failed count",
        "4 checks failed\n",
        "FAIL",
    ),
    (
        "silence is UNKNOWN, never a pass",
        "starting verifier\nnothing else printed\n",
        "UNKNOWN",
    ),
]


def main() -> int:
    bad = 0
    for name, log, expected in CASES:
        got = sweep._parse(log)
        ok = got == expected
        if not ok:
            bad += 1
        print(f"{'ok  ' if ok else 'BAD '} {name}: got={got} want={expected}")
    print(f"PARSER_CASES={len(CASES)} MISMATCHES={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
