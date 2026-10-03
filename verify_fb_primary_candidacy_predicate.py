"""verify_fb_primary_candidacy_predicate.py — pins the EXACT predicate that
rejects an in-range FB from primary candidacy, and the stamp overlap.

BACKGROUND
----------
``select_primary_candidate`` (pretackle_classifier) admits a body by:

    is_engage_role(d)  OR  is_tackle_engage_guard(d, bc, world, tuners)

It NEVER tests ``engage_distance_m`` (that distance is the classifier's
window, applied later in ``classify_pretackle`` step 4, not here).  The
engage guard, however, gates on ``tackle_radius_m`` (default 1.5 m).  So a
body between 1.5 m and ``ENGAGE_DISTANCE_M`` (2.0 m) is "in range" for the
classifier yet rejected by the guard.

This verifier is DETERMINISTIC (no rollout): it builds a body stub and drives
``select_primary_candidate`` / ``is_tackle_engage_guard`` directly, plus a
couple of source assertions that the arming stamps are the SAME fields the
guard and the FB gates read.

Run:  python verify_fb_primary_candidacy_predicate.py
"""

import inspect
import math

from game.defence.defence_lvl_1 import pretackle_classifier as pc
from game.defence.defence_lvl_1 import tackle_engage_guard as guard
from game.defence.defence_lvl_1 import movement_mode_decision as mmd

_PASS = 0
_FAIL = 0


def check(label, ok):
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print("  PASS  %s" % label)
    else:
        _FAIL += 1
        print("  FAIL  %s" % label)


class _World:
    def __init__(self):
        self.tes_active = False
        self.ball_owner = "BC"
        self.defence_tuners = None
        self.tuners = None


class _Body:
    """Minimal candidate/FB stub honouring every attribute the gates read."""

    def __init__(self, x, y, facing=0.0, *, tackle_role=None,
                 defender_role="DEF_LINE_READY", frozen=False,
                 freeze_timer=0.0, tackle_state_active=False,
                 fullback_role_active=False, can_acquire_tackle=False):
        self.x = x
        self.y = y
        self.facing = facing
        self.vx = 0.0
        self.vy = 0.0
        self.tackle_role = tackle_role
        self.defender_role = defender_role
        self.frozen = frozen
        self.freeze_timer = freeze_timer
        self.tackle_state_active = tackle_state_active
        self.fullback_role_active = fullback_role_active
        self.can_acquire_tackle = can_acquire_tackle


def _bc_at(x=10.0, y=0.0):
    b = _Body(x, y)
    b.x = x
    b.y = y
    return b


def _facing_toward(d, bc):
    return math.atan2(bc.y - d.y, bc.x - d.x)


def run():
    print("=" * 78)
    print("A. CANDIDACY GATE — is_engage_role OR is_tackle_engage_guard, NO "
          "distance test")
    print("=" * 78)

    w = _World()
    bc = _bc_at(10.0, 0.0)

    # --- The headline: inside ENGAGE_DISTANCE_M (2.0) but outside the guard
    #     radius (1.5) -> rejected.
    d = _Body(10.0 - 1.8, 0.0)                      # dist 1.8 m
    d.facing = _facing_toward(d, bc)
    chosen = pc.select_primary_candidate(w, [d], bc)
    check("A1 in-range (1.8 m < ENGAGE_DISTANCE_M=%.1f) but radial-OUT vs "
          "guard radius %.1f -> REJECTED"
          % (pc.ENGAGE_DISTANCE_M, guard.tackle_radius_m(None)),
          chosen is None)

    # --- Inside the guard radius AND facing -> admitted via the guard.
    d2 = _Body(10.0 - 1.2, 0.0)                     # dist 1.2 m
    d2.facing = _facing_toward(d2, bc)
    chosen2 = pc.select_primary_candidate(w, [d2], bc)
    check("A2 inside guard radius (1.2 m) + facing -> ADMITTED via guard",
          chosen2 is d2)
    check("A2b guard predicate agrees",
          guard.is_tackle_engage_guard(d2, bc, w, None) is True)

    # --- Inside the guard radius but NOT facing -> rejected.
    d3 = _Body(10.0 - 1.2, 0.0)
    d3.facing = math.pi                              # facing away (~180 deg)
    chosen3 = pc.select_primary_candidate(w, [d3], bc)
    check("A3 inside guard radius but facing ~180 deg -> REJECTED",
          chosen3 is None)

    # --- A tagged fullback flag is NOT read by candidacy: identical outcome.
    d4 = _Body(10.0 - 1.8, 0.0, fullback_role_active=True,
               can_acquire_tackle=True)
    d4.facing = _facing_toward(d4, bc)
    check("A4 fullback_role_active / can_acquire_tackle do NOT change the "
          "candidacy verdict (still REJECTED at 1.8 m)",
          pc.select_primary_candidate(w, [d4], bc) is None)

    # --- The ROLE disjunct admits at ANY distance.
    d5 = _Body(10.0 - 5.0, 0.0, tackle_role="PRIMARY")   # dist 5.0 m
    check("A5 tackle_role=PRIMARY admitted at 5.0 m (distance is ordering "
          "only)", pc.select_primary_candidate(w, [d5], bc) is d5)
    d6 = _Body(10.0 - 5.0, 0.0, tackle_role="SECONDARY")
    check("A6 tackle_role=SECONDARY admitted at 5.0 m",
          pc.select_primary_candidate(w, [d6], bc) is d6)

    # --- THIRD is a joiner role, never a candidate.
    d7 = _Body(10.0 - 1.2, 0.0, tackle_role="THIRD")
    d7.facing = _facing_toward(d7, bc)
    check("A7 tackle_role=THIRD -> REJECTED (joiner-only)",
          pc.select_primary_candidate(w, [d7], bc) is None)

    print("")
    print("=" * 78)
    print("B. GUARD SCOPE — the arming stamps are the guard's own exclusions")
    print("=" * 78)

    # defender_role in the exempt set -> guard false even at 1.2 m + facing.
    d8 = _Body(10.0 - 1.2, 0.0, defender_role="TACKLER_1")
    d8.facing = _facing_toward(d8, bc)
    check("B1 defender_role=TACKLER_1 -> REJECTED (excluded from genuine line "
          "defender)", pc.select_primary_candidate(w, [d8], bc) is None)

    d9 = _Body(10.0 - 1.2, 0.0, tackle_state_active=True)
    d9.facing = _facing_toward(d9, bc)
    check("B2 tackle_state_active=True -> REJECTED",
          pc.select_primary_candidate(w, [d9], bc) is None)

    d10 = _Body(10.0 - 1.2, 0.0, frozen=True)
    d10.facing = _facing_toward(d10, bc)
    check("B3 frozen=True -> REJECTED",
          pc.select_primary_candidate(w, [d10], bc) is None)

    print("")
    print("=" * 78)
    print("C. THE RADIUS MISMATCH (the mechanism)")
    print("=" * 78)
    check("C1 guard radius tackle_radius_m(%.0f) = %.2f"
          % (pc.ENGAGE_DISTANCE_M, guard.tackle_radius_m(None)),
          abs(guard.tackle_radius_m(None) - 1.5) < 1e-9)
    check("C2 ENGAGE_DISTANCE_M = %.2f" % pc.ENGAGE_DISTANCE_M,
          abs(pc.ENGAGE_DISTANCE_M - 2.0) < 1e-9)
    check("C3 guard radius (1.5) < classifier engage distance (2.0): a body "
          "in (1.5, 2.0) is classifier-in-range but guard-OUT",
          guard.tackle_radius_m(None) < pc.ENGAGE_DISTANCE_M)

    print("")
    print("=" * 78)
    print("D. SOURCE — no distance test in candidacy; guard scope is shared")
    print("=" * 78)
    spc = inspect.getsource(pc.select_primary_candidate)
    check("D1 select_primary_candidate does NOT reference ENGAGE_DISTANCE_M",
          "ENGAGE_DISTANCE_M" not in spc)
    check("D2 select_primary_candidate does NOT reference engage_distance",
          "engage_distance" not in spc)
    check("D3 admission is 'is_engage_role OR guarded'",
          "is_engage_role(d) or guarded" in spc)
    smmd = inspect.getsource(mmd._is_genuine_line_defender)
    check("D4 _is_genuine_line_defender excludes TACKLER_* and "
          "tackle_state_active (shared with the armer's stamps)",
          "tackle_state_active" in smmd and "TACKLER" in smmd)
    # _arm_tackler_role lives on OpenPlayEngine; read its source for the stamps.
    import world.open_play_engine as ope
    sarm = inspect.getsource(ope.OpenPlayEngine._arm_tackler_role)
    check("D5 _arm_tackler_role stamps tackle_state_active on the armed body",
          "stamp_tackle_state" in sarm or "tackle_state_active" in sarm)
    check("D6 _arm_tackler_role stamps defender_role on the armed body",
          "defender_role" in sarm)
    check("D7 _arm_tackler_role preserves the fullback tag and marks the "
          "engagement", "set_fullback_emergency_engaged" in sarm
          and "fullback_role_active" in sarm)

    print("")
    print("=" * 78)
    print("VERDICT: %d passed, %d failed" % (_PASS, _FAIL))
    print("=" * 78)
    if _FAIL == 0:
        print("  The rejecting predicate is the CANDIDACY ADMISSION, not any")
        print("  distance check: 'if not (is_engage_role(d) or guarded): "
              "continue'.")
        print("  A tagged FB inside ENGAGE_DISTANCE_M (2.0) is rejected because")
        print("  (i) it holds no engage role (tackle_role is None unless the")
        print("      lane selector stamps PRIMARY via the FB emergency gate),")
        print("  (ii) the engage guard's radial test uses tackle_radius_m")
        print("      (1.5 m) — stricter than the 2.0 m classifier window — and")
        print("      additionally requires facing (<= 60 deg).")
        print("  The arming stamps (defender_role=TACKLER_*, "
              "tackle_state_active=True,")
        print("  frozen) are the SAME fields the guard/lane gates read, so")
        print("  arming re-trips the very exclusions that gate candidacy.")
    return _FAIL


if __name__ == "__main__":
    raise SystemExit(0 if run() == 0 else 1)
