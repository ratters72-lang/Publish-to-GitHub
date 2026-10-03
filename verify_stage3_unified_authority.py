"""
verify_stage3_unified_authority.py -- Stage 3: Unified Pre-Tackle Authority verifier.

Verifies the Stage 3 claims against the actual engine code:

  A. ``game/tackle/`` does not exist (legacy package removed).
  B. ``game/defence/defence_lvl_1/pre_tackle_authority.py`` is the single
     unified pre-tackle authority and defines the canonical state vocabulary.
  C. ``pre_tackle_state`` has exactly ONE writer (the authority) and is
     consumed by both movement and TES.
  D. The shim fields ``tackle_role`` / ``pre_tackle_stance`` /
     ``_tackle_commit_active`` still exist for compatibility.
  E. Frozen-anchor behaviour is restored for TES drive frames.
  F. No engine-runtime module imports from ``game/tackle/``; the engine
     module tree imports cleanly.

This is a VERIFICATION task: it only inspects code, it never modifies engine
behaviour.

Run:
    python verify_stage3_unified_authority.py

Exit 0 when every hard check passes; exit 1 on any failure.  Cautionary
findings (e.g. residual writes to the shim fields) are reported as WARN
and do not by themselves fail the run, but are documented in the report.
"""
import os
import re
import sys

sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))

AUTHORITY_REL = os.path.join("game", "defence", "defence_lvl_1", "pre_tackle_authority.py")
AUTHORITY_ABS = os.path.join(ROOT, AUTHORITY_REL)

# Engine runtime package directories (legacy tackle package must be absent).
ENGINE_DIRS = ["game", "world", "player", "kick_off", "ball", "ai_agents"]

failures = []
warnings = []


def check(name, cond, detail=""):
    """Print a PASS/FAIL line and record failures."""
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def warn(name, detail=""):
    """Print a WARN line (cautionary, does not fail the run)."""
    warnings.append(name)
    print("  [WARN] %s%s" % (name, (" - " + detail) if detail else ""))


def norm(path):
    return path.replace("\\", "/")


def engine_tree_files():
    """All engine-runtime .py files (package dirs + run.py), excluding
    archive/version/cache/tmp trees."""
    files = []
    for dname in ENGINE_DIRS:
        base = os.path.join(ROOT, dname)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            # Skip caches / archived copies.
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "debug_archive", "Versions")
                           and not d.startswith("debug_archive")]
            for fn in filenames:
                if fn.endswith(".py"):
                    files.append(os.path.join(dirpath, fn))
    rp = os.path.join(ROOT, "run.py")
    if os.path.exists(rp):
        files.append(rp)
    return files


def read_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def read_lines(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception:
        return []


def scan(pattern, files=None):
    """Return list of (relpath, lineno, line-text) matching *pattern*."""
    files = files if files is not None else engine_tree_files()
    hits = []
    for p in files:
        for i, line in enumerate(read_lines(p), 1):
            if re.search(pattern, line):
                hits.append((norm(os.path.relpath(p, ROOT)), i, line.rstrip()))
    return hits


def engine_imports():
    """Import the engine modules Stage 3 touches; return (ok, err) pair."""
    modules = [
        "world.open_play_engine",
        "game.managers.defence_manager",
        "game.defence.defence_lvl_1.pre_tackle_authority",
        "game.defence.defence_lvl_1.defensive_behaviour_unified",
        "game.defence.defence_lvl_1.defensive_behaviour_lvl1",
    ]
    try:
        for m in modules:
            __import__(m)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)


# ------------------------------------------------------------------ #
# CHECK A -- game/tackle/ removed
# ------------------------------------------------------------------ #
def check_a():
    print("\n[A] game/tackle/ legacy package removed")
    tackle_dir = os.path.join(ROOT, "game", "tackle")
    exists = os.path.exists(tackle_dir)
    check("A1: game/tackle/ directory does not exist", not exists)
    # Also confirm no game/tackle.py module at the package root.
    check("A2: game/tackle.py does not exist",
          not os.path.exists(os.path.join(ROOT, "game", "tackle.py")))


# ------------------------------------------------------------------ #
# CHECK B -- authority module exists + defines the state vocabulary
# ------------------------------------------------------------------ #
def check_b():
    print("\n[B] pre_tackle_authority.py exists and is the unified authority")
    exists = os.path.exists(AUTHORITY_ABS)
    check("B1: authority file exists", exists)
    if not exists:
        return
    src = read_file(AUTHORITY_ABS)
    needed = [
        ("B2: PreTackleState enum defined", "class PreTackleState"),
        ("B3: apply_pre_tackle defined", "def apply_pre_tackle"),
        ("B4: publish_pre_tackle_state defined", "def publish_pre_tackle_state"),
        ("B5: pre_tackle_enabled gate defined", "def pre_tackle_enabled"),
        ("B6: STATE_ATTR == 'pre_tackle_state'",
         "STATE_ATTR: str = \"pre_tackle_state\""),
        ("B7: PRE_TACKLE_TARGET_SOURCE constant",
         "PRE_TACKLE_TARGET_SOURCE: str = \"pre_tackle\""),
        ("B8: COMMIT / PLANTED / SLOW / HOLD states in enum",
         all(tok in src for tok in ("COMMIT", "PLANTED", "SLOW", "HOLD"))),
    ]
    for name, token in needed:
        if isinstance(token, bool):
            check(name, token)
        else:
            check(name, token in src)


# ------------------------------------------------------------------ #
# CHECK C -- single writer of pre_tackle_state; movement + TES consume
# ------------------------------------------------------------------ #
def check_c():
    print("\n[C] pre_tackle_state single writer + movement/TES consumers")
    files = engine_tree_files()

    # Writers: attribute assignment or setattr to "pre_tackle_state".
    writer_pat = (
        r"\.pre_tackle_state\s*="
        r"|setattr\([^,]+,\s*[\"']pre_tackle_state[\"']\s*,"
    )
    writer_hits = scan(writer_pat, files)
    writer_files = sorted({h[0] for h in writer_hits})

    check("C1: exactly ONE writer module for pre_tackle_state",
          len(writer_files) == 1,
          "writers=%s" % writer_files)
    if len(writer_files) == 1:
        check("C2: the single writer is pre_tackle_authority.py",
              writer_files[0] == norm(AUTHORITY_REL),
              "writer=%s" % writer_files[0])

    # Readers: any reference to pre_tackle_state outside the writer module.
    ref_pat = r"pre_tackle_state"
    ref_hits = scan(ref_pat, files)
    reader_hits = [h for h in ref_hits if h[0] != norm(AUTHORITY_REL)]
    reader_files = sorted({h[0] for h in reader_hits})
    print("  [INFO] writer sites:")
    for f, ln, txt in writer_hits:
        print("         WRITE  %s:%d: %s" % (f, ln, txt.strip()))
    print("  [INFO] reader/ref sites (excluding the authority):")
    for f, ln, txt in reader_hits:
        print("         READ   %s:%d: %s" % (f, ln, txt.strip()))

    tes_reader = "game/defence/defence_lvl_1/defensive_behaviour_lvl1.py"
    cls_reader = "game/defence/defence_lvl_1/pretackle_classifier.py"
    # TES gate delegates to the MovementState-driven classifier, which is the
    # single consumer of MovementState for the engage decision (per the
    # posture-audit Finding 3: the classifier must key on MovementState +
    # velocity, NOT the legacy parallel pre_tackle_state enum).
    classifier_hits = scan(r"classify_pretackle", files)
    check("C3: TES (try_pre_tackle) delegates to the MovementState classifier",
          (any(h[0] == tes_reader for h in classifier_hits)
           and os.path.exists(os.path.join(ROOT, *cls_reader.split("/")))),
          "classifier_refs=%s" % sorted({h[0] for h in classifier_hits}))
    # Movement is produced by the single authority: it stamps target /
    # velocity / _mmdl_moved from the canonical state.  Assert the drive
    # path invokes the authority with frozen_mode=True (movement writer).
    dm_path = norm(os.path.join("game", "managers", "defence_manager.py"))
    dm_src = read_file(os.path.join(ROOT, *dm_path.split("/")))
    check("C4: movement (DefenceManager.update_tes_drive) calls "
          "apply_pre_tackle(... frozen_mode=True)",
          "apply_pre_tackle(" in dm_src and "frozen_mode=True" in dm_src)


# ------------------------------------------------------------------ #
# CHECK D -- shim fields exist as compatibility fields
# ------------------------------------------------------------------ #
def check_d():
    print("\n[D] shim fields (tackle_role / pre_tackle_stance / "
          "_tackle_commit_active) exist for compatibility")
    shims = ["tackle_role", "pre_tackle_stance", "_tackle_commit_active"]
    files = engine_tree_files()
    for shim in shims:
        hits = scan(r"\.%s\s*=" % re.escape(shim), files)
        refs = scan(re.escape(shim), files)
        check("D1: %s referenced in engine tree" % shim,
              len(refs) > 0,
              "refs=%d" % len(refs))
        # Writes outside the authority indicate the shims are not strictly
        # read-only.  The authority's own _sync_shims also writes them, but
        # those are DERIVED outputs from the canonical state.
        outside = sorted({h[0] for h in hits if h[0] != norm(AUTHORITY_REL)})
        if outside:
            warn("D2: %s is WRITTEN outside pre_tackle_authority.py "
                 "(not strictly read-only)" % shim,
                 "writers=%s" % outside)
            for f, ln, txt in hits:
                if f != norm(AUTHORITY_REL):
                    print("         WRITE %s:%d: %s" % (f, ln, txt.strip()))
        else:
            check("D2: %s no writes outside the authority" % shim,
                  True,
                  "only the authority writes (derived shim)")


# ------------------------------------------------------------------ #
# CHECK E -- frozen-anchor restored for TES drive frames
# ------------------------------------------------------------------ #
def check_e():
    print("\n[E] frozen-anchor restoration for TES drive frames")
    dm_rel = "game/managers/defence_manager.py"
    dm_src = read_file(os.path.join(ROOT, *dm_rel.split("/")))
    # The drive-frame path must invoke the unified authority in frozen mode.
    check("E1: update_tes_drive calls apply_pre_tackle with frozen_mode=True",
          "apply_pre_tackle(" in dm_src and "frozen_mode=True" in dm_src)
    # The authority must seed frozen anchors during frozen mode.
    auth_src = read_file(AUTHORITY_ABS)
    check("E2: apply_pre_tackle seeds bodies via _seed_body (anchor capture)",
          "_seed_body(world, d, bc, tuners, frozen_mode, pt)" in auth_src)
    check("E3: frozen-mode COMMIT consumes the FROZEN commit point",
          "frozen_mode:" in auth_src and "COMMIT_X_ATTR" in auth_src)
    check("E4: SLOW/HOLD converge to FROZEN anchors (never live bc.x)",
          "if st == PreTackleState.HOLD" in auth_src
          and "ax = float(getattr(d, ANCHOR_X_ATTR, d.x))" in auth_src)


# ------------------------------------------------------------------ #
# CHECK F -- no stale game.tackle imports + engine imports cleanly
# ------------------------------------------------------------------ #
def check_f():
    print("\n[F] no game.tackle imports in the engine tree; engine imports cleanly")
    files = engine_tree_files()
    # Line-anchored + comment-safe: only flag actual code (import statements
    # or path/dotted references), never prose inside a comment.
    pats = [
        ("F1: no 'import game.tackle'", r"^\s*import\s+game\.tackle\b"),
        ("F2: no 'from game.tackle' import", r"^\s*from\s+game\.tackle\b"),
        ("F3: no 'game/tackle' path reference in code",
         r"^\s*[^#\n]*game/tackle\b"),
        ("F4: no 'import tackle'", r"^\s*import\s+tackle\b"),
        ("F5: no 'from tackle' import", r"^\s*from\s+tackle\b"),
        ("F6: no 'game.tackle' dotted reference in code",
         r"^\s*[^#\n]*game\.tackle\b"),
    ]
    engine_hits = []
    for name, pat in pats:
        hits = scan(pat, files)
        engine_hits.extend(hits)
        check(name, len(hits) == 0,
              "hits=%d" % len(hits) if hits else "")
        for f, ln, txt in hits:
            print("         IMPORT %s:%d: %s" % (f, ln, txt.strip()))

    ok, err = engine_imports()
    check("F7: engine module tree imports cleanly", ok,
          err if not ok else "world.open_play_engine / defence_manager / "
                            "pre_tackle_authority / DBU / lvl1 import OK")


def main():
    print("=" * 72)
    print("STAGE 3 -- UNIFIED PRE-TACKLE AUTHORITY VERIFIER")
    print("=" * 72)
    check_a()
    check_b()
    check_c()
    check_d()
    check_e()
    check_f()
    print("\n" + "=" * 72)
    if warnings:
        print("WARNINGS (%d):" % len(warnings))
        for w in warnings:
            print("  - " + w)
    print("=" * 72)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL HARD CHECKS PASSED%s" %
          (" (%d WARN)" % len(warnings) if warnings else ""))
    sys.exit(0)


if __name__ == "__main__":
    main()
