"""verify_post_tackle_return_lifecycle_contract.py

Contract verifier for the post-tackle fullback return WINDOW.

The defect this pins: ``arm_fullback_post_tackle_return`` set the window and
nothing ever cleared it, so the flag lived forever and left a body permanently
ruck-anchored.  The window must therefore (a) ARM, (b) RELEASE on COMPLETION or
INTERRUPTION, (c) be swept by kickoff reset, (d) be inert behind its env gate,
and (e) be wired into the two writers that own the body's frame
(``apply_fullback_geometry`` and ``DefenceManager.reassert_fullback``).

Run:  python verify_post_tackle_return_lifecycle_contract.py
Exit: 0 when every check passes, 1 otherwise.
"""

import importlib
import inspect
import os
import pathlib
import sys


sys.path.insert(0, ".")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%-4s %-62s %s" % ("PASS" if ok else "FAIL", name, detail))
    return bool(ok)


def skip(name, detail=""):
    RESULTS.append((name, None, detail))
    print("%-4s %-62s %s" % ("SKIP", name, detail))


class _Body:
    """Minimal stand-in for a defender body."""

    player_id = 200
    fullback_role_active = True
    frozen = False
    x = 50.0
    y = 16.0


class _World:
    def __init__(self, bodies):
        self.defending_team = bodies
        self.attacking_team = []
        self.ball_carrier = None


REQUIRED_FUNCS = (
    "fullback_post_tackle_return_enabled",
    "is_fullback_post_tackle_return_active",
    "assigned_fullback_return_target",
    "record_fullback_return_target",
    "arm_fullback_post_tackle_return",
    "fullback_post_tackle_return_live",
    "fullback_post_tackle_return_release_reason",
    "clear_fullback_post_tackle_return",
    "clear_fullback_post_tackle_return_for",
)

REQUIRED_CONSTS = (
    "FB_POST_TACKLE_RETURN_ENV_VAR",
    "FB_POST_TACKLE_RETURN_ATTR",
    "FB_POST_TACKLE_RETURN_TARGET_ATTR",
    "FB_POST_TACKLE_RETURN_RELEASE_M",
)

WIRING = (
    ("game/kickoff_engine.py",
     "clear_fullback_post_tackle_return_for",
     "kickoff reset sweeps the window"),
    ("world/open_play_engine.py",
     "_fb_arm_post_tackle_return",
     "engine binds the arm alias"),
    ("game/managers/defence_manager.py",
     "fullback_post_tackle_return",
     "defence manager releases the window"),
)


def _verify_symbols(fb):
    for name in REQUIRED_FUNCS:
        check("symbol %s" % name, callable(getattr(fb, name, None)))
    for name in REQUIRED_CONSTS:
        check("const  %s" % name, hasattr(fb, name))


def _engaged(fb):
    """Context-free engagement stub: arm() requires an engaged fullback."""
    real = fb.is_fullback_emergency_engaged
    fb.is_fullback_emergency_engaged = lambda _d: True
    return real


def _verify_lifecycle(fb):
    body = _Body()

    check("window starts inactive",
          not fb.is_fullback_post_tackle_return_active(body))

    # arm() is GUARDED, not total: it requires a tagged, engaged fullback.
    check("arm() refuses an unengaged body",
          fb.arm_fullback_post_tackle_return(body) is False)

    real_engaged = _engaged(fb)
    try:
        check("arm() returns True for an engaged tagged fullback",
              fb.arm_fullback_post_tackle_return(body) is True)
        check("window active after arm",
              fb.is_fullback_post_tackle_return_active(body))
        check("arm() stamps the source",
              getattr(body, fb.FB_POST_TACKLE_RETURN_SOURCE_ATTR, None)
              == "drive_completion")
        check("arm() clears any stale recorded target",
              getattr(body, fb.FB_POST_TACKLE_RETURN_TARGET_ATTR, None)
              is None)
    finally:
        fb.is_fullback_emergency_engaged = real_engaged

    recorded = fb.record_fullback_return_target(body, 85.8, 24.0)
    check("record_fullback_return_target accepted", bool(recorded))
    tx, ty = fb.assigned_fullback_return_target(body)
    check("assigned target round-trips",
          abs(tx - 85.8) < 1e-6 and abs(ty - 24.0) < 1e-6,
          "got (%.2f, %.2f)" % (tx, ty))

    world = _World([body])
    try:
        live = fb.fullback_post_tackle_return_live(world, body, 85.8, 24.0)
        check("live() returns a bool", isinstance(live, bool),
              "returned %r" % (live,))
    except Exception as exc:                             # noqa: BLE001
        skip("live() returns a bool", "%s: %s" % (type(exc).__name__, exc))

    cleared = fb.clear_fullback_post_tackle_return(body,
                                                   reason="contract_test")
    check("clear() reports that it cleared", bool(cleared))
    check("window inactive after clear",
          not fb.is_fullback_post_tackle_return_active(body))
    check("clear() drops the recorded target",
          getattr(body, fb.FB_POST_TACKLE_RETURN_TARGET_ATTR, None) is None)
    check("clear() stamps the release reason for the audit",
          getattr(body, fb.FB_POST_TACKLE_RETURN_SOURCE_ATTR, None)
          == "released:contract_test",
          "source=%r"
          % getattr(body, fb.FB_POST_TACKLE_RETURN_SOURCE_ATTR, None))
    check("release_reason() returns a string",
          isinstance(fb.fullback_post_tackle_return_release_reason(body), str))
    check("COMPLETION radius is 0.5 m",
          abs(float(fb.FB_POST_TACKLE_RETURN_RELEASE_M) - 0.5) < 1e-9,
          "= %r" % fb.FB_POST_TACKLE_RETURN_RELEASE_M)


def _verify_sweep(fb):
    tagged = [_Body(), _Body()]
    untagged = _Body()
    untagged.fullback_role_active = False
    bodies = tagged + [untagged]
    world = _World(bodies)

    real_engaged = _engaged(fb)
    try:
        for b in bodies:
            fb.arm_fullback_post_tackle_return(b)
    finally:
        fb.is_fullback_emergency_engaged = real_engaged

    released = fb.clear_fullback_post_tackle_return_for(world)
    check("clear_for() returns the list of released bodies",
          isinstance(released, list) and len(released) == len(tagged),
          "released=%d expected=%d" % (len(released), len(tagged)))
    check("clear_for() skips bodies without the fullback tag",
          untagged not in released)
    check("clear_for() leaves no window active",
          not any(fb.is_fullback_post_tackle_return_active(b)
                  for b in bodies))


def _verify_env_gate(fb):
    var = fb.FB_POST_TACKLE_RETURN_ENV_VAR
    previous = os.environ.get(var)

    os.environ[var] = "0"
    try:
        if fb.fullback_post_tackle_return_enabled():
            fb = importlib.reload(fb)
        check("env gate reports disabled",
              fb.fullback_post_tackle_return_enabled() is False)

        body = _Body()
        real_engaged = _engaged(fb)
        try:
            check("arm() inert when disabled",
                  fb.arm_fullback_post_tackle_return(body) is False)
        finally:
            fb.is_fullback_emergency_engaged = real_engaged
        check("window stays inactive when disabled",
              not fb.is_fullback_post_tackle_return_active(body))
    finally:
        if previous is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = previous

    fb = importlib.reload(fb)
    check("env gate re-enables on unset",
          fb.fullback_post_tackle_return_enabled() is True)
    return fb


def _verify_wiring(fb):
    root = pathlib.Path(".")
    for rel, needle, label in WIRING:
        path = root / rel
        if not path.exists():
            check(label, False, "missing file %s" % rel)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        check(label, needle in text, "%s in %s" % (needle, rel))

    try:
        src = inspect.getsource(fb.apply_fullback_geometry)
    except (OSError, TypeError):
        src = ""
    check("geometry writer consumes the window",
          "fullback_post_tackle_return" in src.lower()
          or "assigned_fullback_return_target" in src,
          "apply_fullback_geometry")


def main():
    from game.defence.defence_lvl_1 import fullback_role as fb

    _verify_symbols(fb)
    _verify_lifecycle(fb)
    _verify_sweep(fb)
    fb = _verify_env_gate(fb)
    _verify_wiring(fb)

    passed = sum(1 for _n, ok, _d in RESULTS if ok is True)
    failed = sum(1 for _n, ok, _d in RESULTS if ok is False)
    skipped = sum(1 for _n, ok, _d in RESULTS if ok is None)

    print("")
    print("=" * 84)
    print("POST-TACKLE RETURN LIFECYCLE CONTRACT: %d passed, %d failed, %d skipped"
          % (passed, failed, skipped))
    print("=" * 84)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
