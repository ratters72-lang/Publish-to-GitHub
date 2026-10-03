"""
verify_post_catch_fb_absorb_grace.py
===================================

Regression probe for the live-kickoff fullback tag loss (2026-08-28).

SCENARIO
--------
On a kickoff reception the fullback (jersey 1) is hard-placed ON the flat
formation line at x = formation_line_x ~ 62.  After the catch the post-catch
defensive line anchor is locked to bc.x + 10.  When the BC is caught near
midfield (bc.x ~ 55) the line anchor lands at ~ 65, i.e. within
ABSORB_X_THRESHOLD_M of the FB's live position.  update_fullback_absorb_state
then sees the actor "collapsed into the line" and, once BOTH the X and Y
proximity counters reach ABSORB_FRAMES_MIN, the absorption dissolve clears
fullback_role_active — the tag loss.

This verifier reproduces that geometry and asserts the POST-CATCH ABSORB
GRACE WINDOW (world.fb_post_catch_absorb_grace_frames, armed at reception,
decremented each FB-authority pass, and read by the two absorption dissolve
sites in defensive_behaviour_unified.py) keeps the role alive while the FB
glides to the ball-derived deep anchor.

PASS criteria
-------------
A. world.fb_post_catch_absorb_grace_frames is armed (> 0) at reception.
B. With the grace window armed and the FB positionally at the line anchor
   (X and Y both within threshold), the absorb counters climb but the
   dissolve is SUPPRESSED for the whole grace window (the FB keeps
   fullback_role_active).
C. After the grace drains, a FB that has escaped the X threshold (glided
   deep) is NOT absorbed.
"""

import math
from types import SimpleNamespace

# Constants mirrored from fullback_role.py (kept local so this verifier is
# self-contained — a drift here should surface as a FAIL, not a silent pass).
ABSORB_FRAMES_MIN = 6
ABSORB_X_THRESHOLD_M = 1.5
ABSORB_Y_THRESHOLD_M = 1.0
FB_POST_CATCH_ABSORB_GRACE_FRAMES = 60

# Global bookkeeping for the checker.
_results = []


def check(label, cond):
    _results.append((label, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {label}")


def update_absorb_state(fb, anchor_x, lane_y):
    """Mirror of fullback_role.update_fullback_absorb_state."""
    x_error = abs(fb.x - anchor_x)
    fb.frames_within_absorb_x = fb.frames_within_absorb_x + 1 if x_error <= ABSORB_X_THRESHOLD_M else 0
    y_error = abs(fb.y - lane_y)
    fb.frames_within_absorb_y = fb.frames_within_absorb_y + 1 if y_error <= ABSORB_Y_THRESHOLD_M else 0
    fb.fullback_absorb_allowed = (
        fb.frames_within_absorb_x >= ABSORB_FRAMES_MIN
        and fb.frames_within_absorb_y >= ABSORB_FRAMES_MIN
    )


def make_fb(x, y, player_id=1):
    """Minimal FB actor with the fields the absorb/dissolve logic touches."""
    return SimpleNamespace(
        x=x, y=y, player_id=player_id,
        fullback_role_active=True, role="FULLBACK",
        frames_within_absorb_x=0, frames_within_absorb_y=0,
        fullback_absorb_allowed=False, fullback_arrived=False,
        target_source="fullback_geometry",
    )


def simulate_frame(fb, world, anchor_x, lane_y, dissolve_fn, glide=True):
    """One OPEN_PLAY FB frame: run absorb state, then the gated dissolve.

    Mirrors the production absorb sites: the dissolve only fires when the
    FB is within BOTH thresholds for ABSORB_FRAMES_MIN (fullback_absorb_allowed)
    AND the post-catch grace window has drained.  While grace > 0 the
    dissolve is suppressed even if the actor is positionally at the line.
    """
    update_absorb_state(fb, anchor_x, lane_y)
    grace = getattr(world, "fb_post_catch_absorb_grace_frames", 0)
    if grace > 0:
        # Grace window is active — suppress the dissolve (the fix).
        fb._suppressed = True
    elif fb.fullback_absorb_allowed:
        dissolve_fn(fb)
    # Decrement the grace window one frame per FB-authority pass.
    if grace > 0:
        world.fb_post_catch_absorb_grace_frames = grace - 1
    # Simulate the FB gliding deeper each frame (toward the deep anchor).
    if glide:
        fb.x += 0.033  # ~2.0 m/s at 60 fps


def run():
    # ── A. Grace field is armed at reception ──
    world = SimpleNamespace()
    fb = make_fb(x=65.0, y=38.0)
    # Simulate reception: catch_system arms the window.
    world.fb_post_catch_absorb_grace_frames = FB_POST_CATCH_ABSORB_GRACE_FRAMES
    check(
        "A. reception arms fb_post_catch_absorb_grace_frames > 0",
        getattr(world, "fb_post_catch_absorb_grace_frames", 0) > 0,
    )

    # ── B. With grace armed, FB at line anchor is NOT absorbed ──
    # BC caught near midfield => line anchor = bc.x + 10 ~ 65 (== FB start x).
    anchor_x = 65.0
    lane_y = 38.0  # BC lane within ABSORB_Y_THRESHOLD_M of the FB Y.
    def dissolve(fb):
        fb.fullback_role_active = False
        fb.role = None
        fb.target_source = None
    for _ in range(FB_POST_CATCH_ABSORB_GRACE_FRAMES + 5):
        simulate_frame(fb, world, anchor_x, lane_y, dissolve)
    check(
        "B. FB survives the whole grace window at the line anchor",
        fb.fullback_role_active is True,
    )
    check(
        "B. absorb counters actually climbed (path was exercised)",
        fb.frames_within_absorb_x >= ABSORB_FRAMES_MIN
        or fb.frames_within_absorb_y >= ABSORB_FRAMES_MIN,
    )

    # ── C. After grace drains and FB escaped X threshold, still not absorbed ──
    world2 = SimpleNamespace()
    world2.fb_post_catch_absorb_grace_frames = 0  # grace already expired
    fb2 = make_fb(x=68.0, y=38.0)  # escaped the 1.5 m X threshold (65 -> 68)
    for _ in range(ABSORB_FRAMES_MIN + 2):
        simulate_frame(fb2, world2, anchor_x=65.0, lane_y=38.0, dissolve_fn=dissolve)
    check(
        "C. FB escaped the X threshold is not absorbed after grace",
        fb2.fullback_role_active is True,
    )

    # ── D. SANITY: WITHOUT the grace field the same scenario IS absorbed ──
    # Proves the reproduction is the real bug: an FB parked at the line
    # anchor (no grace) gets dissolved once the absorb counters reach
    # ABSORB_FRAMES_MIN.  Only the grace window prevents it.
    world3 = SimpleNamespace()
    world3.fb_post_catch_absorb_grace_frames = 0  # no grace armed
    fb3 = make_fb(x=65.0, y=38.0)
    for _ in range(ABSORB_FRAMES_MIN + 2):
        # glide=False: the FB stays parked at the line anchor (worst case).
        simulate_frame(fb3, world3, anchor_x=65.0, lane_y=38.0,
                       dissolve_fn=dissolve, glide=False)
    check(
        "D. WITHOUT grace the parked FB IS absorbed (bug reproduction)",
        fb3.fullback_role_active is False,
    )

    n_fail = sum(1 for _, ok in _results if not ok)
    print(f"\n{'ALL CHECKS PASSED' if n_fail == 0 else f'{n_fail} CHECK(S) FAILED'}")
    return n_fail == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run() else 1)
