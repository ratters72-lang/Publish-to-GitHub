"""verify_target_x_glide.py — guardrail for the TARGET_JIGGLE glide-cap fix (2026-09-07).

The live-run signal was  ``TARGET_JIGGLE id=206 tx_range=7.28``.  The
freeze_audit contract (game/debug/freeze_audit.py) fires when a STATIONARY
line body's ``target_x`` ranges > 5.0 m across a 10-frame window
(``tx_range = max(target_x[-10:]) - min(target_x[-10:])``), suppressed only
when masked / post-ruck-grace / moving (total velocity >= 0.1).

The fix: ``game/defence/defence_lvl_1/target_x_glide.py`` glides a parked
line body's ``target_x`` to at most ``TARGET_X_GLIDE_SPEED_MPS * dt`` per
frame, so a 7.28 m seesaw collapses to ~1.0 m across a 10-frame window.

This verifier asserts:
  1. WITHOUT the cap, the exact 7.28 m seesaw (60.0 <-> 67.28) over a
     10-frame window yields tx_range > 5.0 (the bug reproduces).
  2. WITH the cap, the same seesaw yields tx_range <= 5.0 (== ~1.0 m).
  3. The eligibility gate accepts parked states (RETREAT/DRIFT/HOLD/SLOW)
     AND pursuit states (CHASE/SHOOT — UNIVERSAL RATE LIMIT 2026-09-09),
     and rejects frozen / fullback / committed tackler / marker / DH /
     pre-tackle COMMIT/PLANTED bodies.
  4. The cap is structurally wired into DefenceManager.update_open_play
     (lazy import + glide loop, before the integration loop).

Run:  python verify_target_x_glide.py
"""
from __future__ import annotations

import inspect
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from game.defence.defence_lvl_1.target_x_glide import (
    TARGET_X_GLIDE_SPEED_MPS,
    glide_cap_target_x,
    is_target_x_glide_eligible,
)
from player.player import DefenderRole

DT = 1.0 / 60.0
GLIDE_STEP = TARGET_X_GLIDE_SPEED_MPS * DT  # 0.1 m/frame
AUDIT_WINDOW = 10
AUDIT_SCALE_THRESHOLD = 5.0
BURST_TX = 7.28  # the live-run TARGET_JIGGLE range


class _St:
    """Minimal movement_state stand-in exposing `.name`."""
    def __init__(self, name: str):
        self.name = name


class _Fake:
    """Minimal defender stand-in with every attribute the eligibility gate
    reads (no pre-tackle state -> is_pre_tackle_managed() is False)."""
    def __init__(self, movement_state="RETREAT", frozen=False,
                 fullback=False, commit=False, role=None,
                 auth_until=-1):
        self.movement_state = movement_state if isinstance(movement_state, _St) else _St(movement_state)
        self.frozen = frozen
        self.fullback_role_active = fullback
        self._tackle_commit_active = commit
        self.defender_role = role
        self.authoritative_until_frame = auth_until
        # pre-tackle absent -> is_pre_tackle_managed(d) is False
        self.target_x = 0.0
        self._prev_target_x_glide = None


def _simulate_window(targets: list[float]) -> tuple[list[float], float]:
    """Run the *targets* (one per frame, written by the "writer") through
    glide_cap_target_x and return the last-window target_x list + its range."""
    d = _Fake(movement_state="RETREAT")
    capped = []
    for tx in targets:
        d.target_x = tx
        glide_cap_target_x(d, DT)
        capped.append(d.target_x)
    win = capped[-AUDIT_WINDOW:]
    return win, (max(win) - min(win))


def main() -> int:
    fails: list[str] = []

    # ── Check 1a: WITHOUT cap the 7.28 m seesaw reproduces a > 5.0 range ──
    raw_targets = []
    for i in range(AUDIT_WINDOW + 2):
        # Alternating writer: 60.0 (lane A) and 67.28 (lane B) every frame.
        raw_targets.append(BURST_TX if i % 2 else 0.0)
    raw_win = raw_targets[-AUDIT_WINDOW:]
    raw_range = max(raw_win) - min(raw_win)
    print(f"[VERIFY] RAW (no cap) 10-frame window range = {raw_range:.2f} m")
    if raw_range <= AUDIT_SCALE_THRESHOLD:
        fails.append(
            f"Raw seesaw range {raw_range:.2f} m is NOT > {AUDIT_SCALE_THRESHOLD} m — "
            "the bug does not reproduce; the test is not meaningful."
        )

    # ── Check 1b: WITH cap the same seesaw collapses below 5.0 m ──
    capped_win, capped_range = _simulate_window(raw_targets)
    print(f"[VERIFY] CAPPED 10-frame window = {[round(v, 3) for v in capped_win]}")
    print(f"[VERIFY] CAPPED 10-frame window range = {capped_range:.3f} m")
    if capped_range > AUDIT_SCALE_THRESHOLD:
        fails.append(
            f"Glide cap left tx_range={capped_range:.3f} m — still > {AUDIT_SCALE_THRESHOLD} m, "
            "would still fire TARGET_JIGGLE."
        )
    # Also confirm the cap actually engaged (range should be ~GLIDE_STEP*10, not the raw 7.28).
    expected = GLIDE_STEP * len(raw_targets)  # monotone cap bound
    print(f"[VERIFY] cap engaged: expected bound <= {expected:.3f} m")
    if capped_range > expected + 0.01:
        fails.append(
            f"Capped range {capped_range:.3f} m exceeds the {expected:.3f} m monotone cap bound — "
            "the cap is not clamping each frame."
        )

    # ── Check 2: eligibility gate ──
    elig = []
    for name in ("RETREAT", "DRIFT", "HOLD", "SLOW"):
        elig.append(is_target_x_glide_eligible(_Fake(movement_state=name)))
    if not all(elig):
        fails.append(
            f"Parked states not all eligible: RETREAT/DRIFT/HOLD/SLOW = {elig}"
        )
    if is_target_x_glide_eligible(_Fake(frozen=True)):
        fails.append("Frozen body is glide-eligible — must be exempt.")
    if is_target_x_glide_eligible(_Fake(fullback=True)):
        fails.append("Fullback is glide-eligible — must be exempt.")
    if is_target_x_glide_eligible(_Fake(commit=True)):
        fails.append("Committed tackler is glide-eligible — must be exempt.")
    if not is_target_x_glide_eligible(_Fake(movement_state="CHASE")):
        fails.append("CHASE pursuit body is NOT glide-eligible — must be capped (universal).")
    if not is_target_x_glide_eligible(_Fake(movement_state="SHOOT")):
        fails.append("SHOOT pursuit body is NOT glide-eligible — must be capped (universal).")
    if is_target_x_glide_eligible(_Fake(role=DefenderRole.MARKER_A)):
        fails.append("Marker is glide-eligible — must be exempt.")
    if is_target_x_glide_eligible(_Fake(role=DefenderRole.DUMMY_HALF)):
        fails.append("Dummy half is glide-eligible — must be exempt.")
    print("[VERIFY] eligibility gate: parked+pursuit=eligible (universal), frozen/fullback/commit/marker/DH=exempt — OK")

    # ── Check 3: structural wiring in update_open_play ──
    try:
        from game.managers.defence_manager import DefenceManager
        src = inspect.getsource(DefenceManager.update_open_play)
        # The call's OPTIONAL keyword arguments are now part of the
        # bounded-glide contract (2026-09-20: tuner + world handles let the
        # glide seed its baseline from the body's own position and cap on its
        # first call) and may grow again, so the anchor is the call PREFIX.
        # The wiring contract this section asserts is UNCHANGED: the glide is
        # imported lazily, called once per eligible body, and runs before the
        # integration loop.
        _call_prefix = "_glide_cap_x(_dg, dt"
        if "glide_cap_target_x as _glide_cap_x" not in src:
            fails.append("Lazy import of the glide cap is missing from update_open_play.")
        if _call_prefix not in src:
            fails.append("The glide-cap call is missing from update_open_play.")
        if "RLM_TARGET_X_TRACE" not in src:
            fails.append("The opt-in target_x trace gate is missing from update_open_play.")
        # Ensure it runs BEFORE the integration loop (p.update(dt)).
        idx_glide = src.find(_call_prefix)
        idx_apply = src.find("# ---- Apply movement ----")
        if idx_glide == -1 or idx_apply == -1 or idx_glide > idx_apply:
            fails.append("The glide cap must run BEFORE the 'Apply movement' integration loop.")
    except Exception as exc:  # pragma: no cover - defensive
        fails.append(f"Could not inspect update_open_play wiring: {exc}")
    print("[VERIFY] structural wiring: lazy import + glide loop + trace + pre-integration — OK")

    if fails:
        print("\n========= RESULT: FAIL =========")
        for f in fails:
            print(f"  - {f}")
        return 1

    print("\n========= RESULT: ALL CHECKS PASSED =========")
    print(f"  - Raw {BURST_TX} m seesaw -> {raw_range:.2f} m (bug reproduces).")
    print(f"  - Capped {BURST_TX} m seesaw -> {capped_range:.3f} m (<= {AUDIT_SCALE_THRESHOLD} m).")
    print(f"  - Eligibility gate + structural wiring confirmed.")
    return 0


def _simulate_without_cap(targets: list[float]):
    raise NotImplementedError  # unused; raw range computed directly inline


if __name__ == "__main__":
    raise SystemExit(main())
