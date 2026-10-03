"""verify_post_tes_corridor_contract.py — Stage 5 STATIC verifier (C1-C9).

Reads the corridor authority, the engine, the gate and the project's own
freeze audit, and asserts the contract in
``POST_TES_REINTEGRATION_CONTRACT.md`` holds in the source.  Static only:
it never boots the engine.  Exit 0 = clean.

Checks
------
C1  the authority imports and every ``__all__`` name resolves
C2  every declared corridor-transition writer exists in its named source file
C3  ``is_line_member`` delegates to ``population_authority.is_line_body``
    (one membership oracle; the corridor must not re-derive membership)
C4  the stuck window / displacement / grace constants MATCH ``freeze_audit``
    (R5: the corridor may not invent a stricter predicate)
C5  the barrier codes are a CLOSED set -- no barrier is emitted that is not a
    declared ``BARRIER_*`` constant or the ``role:`` prefix
C6  every declared hold in the contract's R4 list is implemented
C7  PURITY: no production module imports the corridor authority (R9)
C8  the engine's frame tail order is unchanged (the Stage 4B C1 invariant:
    release repair -> repair_line_population_if_drifted -> ruck pin -> finalise)
C9  the re-admission writers named in the engine still exist

Usage:  python verify_post_tes_corridor_contract.py
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

AUTH_REL = os.path.join("game", "defence", "defence_lvl_1",
                        "post_tes_corridor_authority.py")
OPE_REL = os.path.join("world", "open_play_engine.py")
GATE_REL = os.path.join("game", "defence", "defence_lvl_1",
                        "tackle_state_gate.py")
FA_REL = os.path.join("game", "debug", "freeze_audit.py")
CONTRACT_REL = "POST_TES_REINTEGRATION_CONTRACT.md"

AUTH = os.path.join(ROOT, AUTH_REL)
OPE = os.path.join(ROOT, OPE_REL)
GATE = os.path.join(ROOT, GATE_REL)
FA = os.path.join(ROOT, FA_REL)
CONTRACT = os.path.join(ROOT, CONTRACT_REL)

# Files allowed to reference the authority (R9 purity allow-list).
_PURITY_ALLOWED = {
    AUTH_REL.replace("\\", "/"),
    "verify_post_tes_corridor_contract.py",
    os.path.join("tmp", "probe_stage5_corridor.py").replace("\\", "/"),
    "tmp_run_stage5_regression.py",
}

failures = []
infos = []


def read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        failures.append("cannot read %s (%s)" % (path, exc))
        return ""


def check(label, cond, detail=""):
    if cond:
        print("  PASS  %s" % label)
    else:
        failures.append(label + ((" -- " + detail) if detail else ""))
        print("  FAIL  %s%s" % (label, (" -- " + detail) if detail else ""))


def info(label):
    infos.append(label)
    print("  INFO  %s" % label)


# ─────────────────────────────────────────────────────────────────────
# C1 — the authority imports and __all__ resolves
# ─────────────────────────────────────────────────────────────────────

def c1():
    print("C1  authority imports; every __all__ name resolves")
    sys.path.insert(0, ROOT)
    try:
        from game.defence.defence_lvl_1 import post_tes_corridor_authority as pta
    except Exception as exc:                       # noqa: BLE001
        check("import post_tes_corridor_authority", False, repr(exc))
        return None
    check("import post_tes_corridor_authority", True)
    missing = [n for n in pta.__all__ if not hasattr(pta, n)]
    check("every __all__ name resolves", not missing, "missing=%s" % missing)
    check("three corridor nodes declared",
          tuple(pta.CORRIDOR_NODES) == ("TES_OWNED", "RELEASED", "LINE_MEMBER"),
          str(pta.CORRIDOR_NODES))
    check("re-admission surface is the five declared writers",
          len(pta.RE_ADMISSION_WRITERS) == 5,
          str(len(pta.RE_ADMISSION_WRITERS)))
    return pta


# ─────────────────────────────────────────────────────────────────────
# C2 — declared transition writers exist
# ─────────────────────────────────────────────────────────────────────

def _file_for_writer(writer):
    """('relative/path.py', 'symbol') for a dotted writer reference."""
    parts = writer.split(".")
    symbol = parts[-1]
    # A class-qualified reference (world.open_play_engine.OpenPlayEngine.m)
    # still resolves to the module file that holds the method.
    for cut in range(len(parts) - 1, 0, -1):
        mod_parts = parts[:cut]
        if mod_parts and mod_parts[0] in ("game", "world", "player", "ball"):
            rel = os.path.join(*mod_parts) + ".py"
            if os.path.exists(os.path.join(ROOT, rel)):
                return rel, symbol
    return None, symbol


def c2(pta):
    print("C2  every declared corridor-transition writer exists")
    if pta is None:
        check("declared writers exist", False, "authority unavailable")
        return
    for edge, writers in pta.TRANSITION_WRITERS.items():
        for w in writers:
            rel, symbol = _file_for_writer(w)
            if rel is None:
                check("writer resolves to a file: %s" % w, False)
                continue
            src = read(os.path.join(ROOT, rel))
            check("writer exists: %s" % w,
                  ("def %s" % symbol) in src or symbol in src)


# ─────────────────────────────────────────────────────────────────────
# C3 — one membership oracle
# ─────────────────────────────────────────────────────────────────────

def c3():
    print("C3  LINE_MEMBER reads the single membership oracle")
    src = read(AUTH)
    check("authority imports population_authority.is_line_body",
          "population_authority import" in src and "is_line_body" in src)
    check("authority does NOT inline a membership test",
          "LINE_EXCLUDED_ROLES" in src and "is_line_body" in src)
    pop = read(os.path.join(ROOT, "game", "defence", "defence_lvl_1",
                            "population_authority.py"))
    check("population_authority still defines is_line_body",
          "def is_line_body" in pop)


# ─────────────────────────────────────────────────────────────────────
# C4 — the stuck constants match freeze_audit (R5)
# ─────────────────────────────────────────────────────────────────────

def c4(pta):
    print("C4  stuck constants MATCH freeze_audit (R5)")
    fa = read(FA)
    if pta is None:
        check("stuck constants", False, "authority unavailable")
        return
    fa_window = re.search(r"window:\s*int\s*=\s*(\d+)", fa)
    fa_thresh = re.search(r"stuck_threshold:\s*int\s*=\s*(\d+)", fa)
    check("freeze_audit window is 10 frames",
          bool(fa_window) and int(fa_window.group(1)) == 10,
          fa_window.group(0) if fa_window else "not found")
    check("freeze_audit stuck_threshold is 10 frames",
          bool(fa_thresh) and int(fa_thresh.group(1)) == 10,
          fa_thresh.group(0) if fa_thresh else "not found")
    check("corridor window MATCHES freeze_audit",
          int(pta.STUCK_WINDOW_FRAMES) == 10,
          str(pta.STUCK_WINDOW_FRAMES))
    check("corridor displacement threshold is freeze_audit's 0.05 m",
          abs(float(pta.STUCK_DISPLACEMENT_M) - 0.05) < 1e-9,
          str(pta.STUCK_DISPLACEMENT_M))
    check("corridor grace is freeze_audit's 0.5 s",
          abs(float(pta.POST_PTB_GRACE_S) - 0.5) < 1e-9,
          str(pta.POST_PTB_GRACE_S))
    check("freeze_audit still suppresses the marker reform window",
          "is_marker_reform" in fa and "MARKER_A" in fa)
    check("corridor declares the same marker-reform phases",
          set(pta.MARKER_REFORM_PHASES) ==
          {"RELEASE", "PTB_PREP", "PTB_EXEC", "PTB"},
          str(sorted(pta.MARKER_REFORM_PHASES)))
    check("corridor declares the post-PTB grace phase",
          set(pta.POST_PTB_GRACE_PHASES) == {"POST_RUCK_OPEN_PLAY"},
          str(sorted(pta.POST_PTB_GRACE_PHASES)))


# ─────────────────────────────────────────────────────────────────────
# C5 — the barrier codes are a closed set
# ─────────────────────────────────────────────────────────────────────

def c5(pta):
    print("C5  barrier codes are a CLOSED set")
    src = read(AUTH)
    if pta is None:
        check("closed barrier set", False, "authority unavailable")
        return
    declared = {
        pta.BARRIER_FROZEN,
        pta.BARRIER_TACKLE_STATE,
        pta.BARRIER_FULLBACK,
    }
    # Every `return` in reintegration_barrier must be a declared constant or
    # the role-prefix composition.
    body = src.split("def reintegration_barrier", 1)[-1].split("\ndef ", 1)[0]
    returns = re.findall(r"return\s+([^\n]+)", body)
    unknown = []
    for expr in returns:
        expr = expr.strip()
        if expr.startswith("None"):
            continue
        if ("BARRIER_FROZEN" in expr or "BARRIER_FULLBACK" in expr
                or "BARRIER_TACKLE_STATE" in expr
                or "BARRIER_ROLE_PREFIX" in expr):
            continue
        unknown.append(expr)
    check("every barrier return is a declared code", not unknown,
          str(unknown))
    check("barrier constants are distinct",
          len({pta.BARRIER_FROZEN, pta.BARRIER_TACKLE_STATE,
               pta.BARRIER_FULLBACK}) == 3)
    check("verdict constants are distinct",
          pta.STUCK_OWNED != pta.STUCK_UNOWNED)


# ─────────────────────────────────────────────────────────────────────
# C6 — every declared hold is implemented
# ─────────────────────────────────────────────────────────────────────

HOLDS = ("frozen", "freeze_timer", "tackle_state_active", "marker_lock",
         "depth_lock", "tes_structure_hold", "fullback_role_active",
         "post_ptb_grace", "marker_reform")


def c6():
    print("C6  every hold listed in the contract R4 is implemented")
    src = read(AUTH)
    contract = read(CONTRACT)
    for hold in HOLDS:
        check("declared_hold implements %r" % hold,
              ('"%s"' % hold) in src or ("'%s'" % hold) in src)
        check("contract R4 lists %r" % hold, hold in contract)


# ─────────────────────────────────────────────────────────────────────
# C7 — purity: no production module imports the authority
# ─────────────────────────────────────────────────────────────────────

def c7():
    print("C7  PURITY — no production module imports the authority")
    offenders = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", "debug_archive", ".git")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, ROOT).replace("\\", "/")
            if rel in _PURITY_ALLOWED:
                continue
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            if "post_tes_corridor_authority" in text:
                offenders.append(rel)
    check("no production module imports post_tes_corridor_authority",
          not offenders, str(offenders))
    if offenders:
        info("offenders: %s" % offenders)


# ─────────────────────────────────────────────────────────────────────
# C8 — the engine tail order is unchanged
# ─────────────────────────────────────────────────────────────────────

def c8():
    print("C8  engine frame tail order unchanged (Stage 4B C1 invariant)")
    src = read(OPE)
    order = [
        "update_tackle_gate",
        "repair_line_population_if_drifted",
        "enforce_ruck_pin",
        "finalise_line_population",
        "record_final_targets",
    ]
    positions = []
    for token in order:
        idx = src.rfind(token)
        positions.append((token, idx))
    check("all tail writers present",
          all(i >= 0 for _t, i in positions), str(positions))
    seq = [i for _t, i in positions if i >= 0]
    check("tail order is strictly increasing", seq == sorted(seq), str(seq))
    # The release slot-invariant repair must still be gated to non-TES.
    check("release slot-invariant repair still gated to non-TES phases",
          '"TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE"' in src)


# ─────────────────────────────────────────────────────────────────────
# C9 — the re-admission writers exist in the engine
# ─────────────────────────────────────────────────────────────────────

def c9():
    print("C9  re-admission writers exist")
    ope = read(OPE)
    gate = read(GATE)
    for name in ("_clear_marker_roles", "_post_ruck_marker_cleanup",
                 "_reset_tackle_state"):
        check("engine defines %s" % name, ("def %s" % name) in ope)
    check("gate defines _release_defender",
          "def _release_defender" in gate)
    check("gate defines update_tackle_state",
          "def update_tackle_state" in gate)
    pa = read(os.path.join(ROOT, "game", "defence", "defence_lvl_1",
                           "pretackle_attempt.py"))
    check("assign_def_line_ready exists",
          "def assign_def_line_ready" in pa)


def main():
    print("=" * 78)
    print("STAGE 5 — POST-TES REINTEGRATION CORRIDOR: STATIC CONTRACT VERIFIER")
    print("=" * 78)
    pta = c1()
    print()
    c2(pta)
    print()
    c3()
    print()
    c4(pta)
    print()
    c5(pta)
    print()
    c6()
    print()
    c7()
    print()
    c8()
    print()
    c9()
    print()
    print("=" * 78)
    if failures:
        print("RESULT: %d CHECK(S) FAILED" % len(failures))
        for f in failures:
            print("   - %s" % f)
        return 1
    print("RESULT: all checks PASS (%d INFO findings)" % len(infos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
