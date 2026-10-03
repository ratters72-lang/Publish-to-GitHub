"""Temp: compile + import + wiring smoke check for the retreat-obligation work.

Run from the repo root: python tmp/temp_compile_check.py
Deletes itself after use (see the caller's delete_file step).
"""

import importlib
import os
import sys
import traceback

# Running this file from ``tmp/`` puts ``tmp/`` on sys.path[0], not the repo
# root, so the project packages (``world``, ``game``, ``player``) would not
# resolve.  Insert the repo root (this file's parent directory) explicitly.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

TOUCHED = [
    "game/defence/defence_lvl_1/retreat_obligation.py",
    "game/defence/defence_lvl_1/intercept_tacklers.py",
    "game/defence/defence_lvl_1/pretackle_corridor.py",
    "game/defence/defence_lvl_1/pretackle_machine.py",
    "game/defence/defence_lvl_1/movement_mode_decision.py",
    "game/defence/defence_lvl_1/pre_tackle_authority.py",
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
    "game/managers/defence_manager.py",
    "world/open_play_engine.py",
]

failures = 0

# ---- 1. COMPILE each touched file -------------------------------------
for path in TOUCHED:
    try:
        with open(path, encoding="utf-8") as handle:
            compile(handle.read(), path, "exec")
        print("OK   compile %s" % path)
    except Exception:
        failures += 1
        print("FAIL compile %s" % path)
        traceback.print_exc()

# ---- 2. IMPORT the engine (pulls the whole defence stack) -------------
try:
    importlib.import_module("world.open_play_engine")
    print("OK   import world.open_play_engine")
except Exception:
    failures += 1
    print("FAIL import world.open_play_engine")
    traceback.print_exc()

# ---- 3. retreat_obligation API surface --------------------------------
EXPECTED_API = [
    "RETREAT_OBLIGATION_TOLERANCE_M",
    "RETREAT_OBLIGATION_STRICT",
    "OBLIGATION_ATTR",
    "OBLIGATION_FRAME_ATTR",
    "retreat_deficit_m",
    "is_exempt_role",
    "is_retreat_obligated",
    "has_retreat_obligation_authority",
    "stamp_retreat_obligation",
    "clear_retreat_obligation",
    "sync_retreat_obligation",
    "strip_tackle_authority",
    "audit_retreat_obligation_compliance",
    "format_obligation_report",
]
try:
    module = importlib.import_module(
        "game.defence.defence_lvl_1.retreat_obligation"
    )
except Exception:
    failures += 1
    print("FAIL import game.defence.defence_lvl_1.retreat_obligation")
    traceback.print_exc()
    module = None

if module is not None:
    missing = [name for name in EXPECTED_API if not hasattr(module, name)]
    if missing:
        failures += len(missing)
        print("FAIL retreat_obligation missing: %s" % ", ".join(missing))
    else:
        print("OK   retreat_obligation API surface (%d names)"
              % len(EXPECTED_API))

    # __all__ must agree with the real surface.
    declared = set(getattr(module, "__all__", ()))
    if not set(EXPECTED_API).issubset(declared):
        failures += 1
        print("FAIL __all__ does not export the full surface")

# ---- 4. WIRING: every file must reference the veto it carries ---------
WIRING = {
    "game/defence/defence_lvl_1/intercept_tacklers.py": [
        "is_retreat_obligated",
    ],
    "game/defence/defence_lvl_1/pretackle_corridor.py": [
        "is_retreat_obligated",
    ],
    "game/defence/defence_lvl_1/pretackle_machine.py": [
        "is_retreat_obligated",
    ],
    "game/defence/defence_lvl_1/movement_mode_decision.py": [
        "is_retreat_obligated",
    ],
    "game/defence/defence_lvl_1/pre_tackle_authority.py": [
        "is_retreat_obligated",
    ],
    "game/defence/defence_lvl_1/defensive_behaviour_unified.py": [
        "is_retreat_obligated",
    ],
    "game/managers/defence_manager.py": [
        "RETREAT_OBLIGATION_STRICT",
        "sync_retreat_obligation",
        "stamp_retreat_obligation",
        "strip_tackle_authority",
        "is_retreat_obligated",
        "audit_retreat_obligation_compliance",
    ],
    "world/open_play_engine.py": [
        "stamp_retreat_obligation",
        "clear_retreat_obligation",
        "is_retreat_obligated",
    ],
}
for path, names in WIRING.items():
    try:
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
    except Exception:
        failures += 1
        print("FAIL read %s" % path)
        continue
    for name in names:
        if name not in source:
            failures += 1
            print("FAIL %s does not reference %s" % (path, name))
print("OK   wiring references present in %d files" % len(WIRING))

# ---- 5. VETO COUNT: the per-frame grant sites we must have covered ----
# Each string below is a grant site identified in the repo-wide scan.
VETO_SITES = [
    ("game/defence/defence_lvl_1/defensive_behaviour_unified.py", 6),
    ("game/defence/defence_lvl_1/intercept_tacklers.py", 1),
    ("game/defence/defence_lvl_1/pretackle_corridor.py", 2),
    ("game/defence/defence_lvl_1/pretackle_machine.py", 1),
    ("game/defence/defence_lvl_1/movement_mode_decision.py", 1),
    ("game/defence/defence_lvl_1/pre_tackle_authority.py", 3),
    ("world/open_play_engine.py", 1),
]
total_vetoes = 0
for path, expected in VETO_SITES:
    with open(path, encoding="utf-8") as handle:
        count = handle.read().count("is_retreat_obligated(")
    total_vetoes += count
    flag = "OK  " if count >= expected else "FAIL"
    if count < expected:
        failures += 1
    print("%s %-58s %d vetoes (expected >= %d)"
          % (flag, path, count, expected))
print("OK   total is_retreat_obligated call sites = %d" % total_vetoes)

print("FAILURES=%d" % failures)
sys.exit(1 if failures else 0)
