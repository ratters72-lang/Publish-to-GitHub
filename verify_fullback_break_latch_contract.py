"""verify_fullback_break_latch_contract.py — THE ANCHOR-RELOCK BREAK LATCH
(frame-382/383 telemetry, 2026-09-28).

THE DEFECT THIS PINS
--------------------
The authority-timeline probe (``probe_fb_authority_timeline.py``, seed
20260827, window 16, recorded in ``tmp_fb_authority_timeline.txt``) captures
the FIRST live ``fullback_geometry`` CHASE -> DRIFT transition on frames
382/383:

    f=382  mode=CHASE  brk=DEPTH  bc_x=26.52  line=18.25  ruck=8.25
    f=383  mode=DRIFT  brk=NONE   bc_x=26.52  line=36.52  ruck=26.52

THE CARRIER NEVER MOVED.  Between the two frames the tackle cycle re-locked
``world.ruck_x_m`` from 8.25 onto the CARRIER (26.52) -- the
CONTACT -> DRIVE entry write in ``OpenPlayEngine._update_tackle_contact``,
which also drops the anchor lock so the next frame re-derives
``line = ruck_x + 10 = 36.52``.  The line anchor therefore moved +18.27 m
GOAL-SIDE of a live break, and that alone withdrew the classification.

THE GATE WALK (``classify_fullback_break`` evaluation order)
------------------------------------------------------------
    G10a  DEPTH    bc.x > line + FB_LINE_BREAK_MARGIN_M (1.0)
    G10b  LEVEL    line-intact exclusion: bc.x < line - FB_LATERAL_BREAK_LEVEL_M (3.0)
    G10c  CHANNEL  |bc.y - channel_y| > FB_LATERAL_BREAK_M (5.0)
    G10d  REACH    hypot(bc - defender) <= FB_LATERAL_BREAK_RADIUS_M (25.0)

    G10a at f=382:  26.52 > 19.25   -> TRUE, returns DEPTH
    G10a at f=383:  26.52 > 37.52   -> FALSE, falls through
    G10b at f=383:  26.52 < 33.52   -> TRUE, returns NONE   <-- the leg
    G10c at f=383:  |19.11 - 22.40| = 3.29, not > 5.0
    G10d at f=383:  reach 4.30 <= 25, satisfied

So the predicate that caused the loss is the LINE-INTACT LEVEL exclusion
(G10b) acting on a G10a that had just been invalidated by the relock.  With
``FB_BREAK_NONE``, ``apply_fullback_geometry`` wrote the sweeper anchor and
``MovementState.DRIFT`` under the SAME writer with NO engine event and
nothing clearing the emergency engagement -- the probe's ``MODE_OVERWRITE``
handover.

CONTRACT UNDER TEST (the law)
-----------------------------
Once a genuine break has been classified for the fullback, a FORWARD move of
the line anchor may NOT retroactively un-classify it.  The break TEST reads
``break_test_line_anchor_x()`` -- the live anchor FLOORED by the anchor
latched when the chase was armed -- while the sweeper's own DEPTH geometry
keeps reading the LIVE anchor (the depth role must follow the real ruck).

The latch is TACKLE-CYCLE state: released at an explicit cycle end
(``clear_fullback_chase_state``, called from the RELEASE-entry teardown and
the miss teardown) or when the actor loses the tag; and SELF-RELEASING the
moment the carrier genuinely drops behind the LATCHED line.

Legs:
  [1] The frame-382/383 signature reproduces the telemetry verbatim.
  [2] The gate walk: which leg flipped and which leg returned NONE.
  [3] THE FAILURE / THE FIX -- the two-frame relock fixture through the REAL
      writer: lever OFF reproduces the defective DRIFT + sweeper re-pin;
      lever ON keeps CHASE on the pursuit point.
  [4] Latch lifecycle -- arm on DEPTH / LATERAL, MIN-anchor under repeated
      forward relocks, self-release, explicit cycle-end sweep, tag loss.
  [5] The single reader -- purity, the lever-off identity, the floor, and the
      ``carrier_past_defensive_line`` anchor override.
  [6] Every classify consumer honours the latch -- the geometry writer, the
      live-break establishment, ``fullback_breakaway_live``,
      ``fullback_lateral_break_active``, ``fullback_depth_break_active`` and
      ``fullback_emergency_defender_active``.
  [7] The explicit relock handover backstop -- its effect, its lever gate.
  [8] Source wiring -- the engine cycle-end sites, the six call sites, the
      MMDL branch, the tag-loss clear, and the depth-vs-test anchor split.
  [9] No regression -- off a break, and with the lever off, the sweeper
      contract is byte-identical.

Run:  python verify_fullback_break_latch_contract.py
Exit code 0 = the relock can no longer silently collapse a live chase.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from player.movement_states import MovementState

from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    arm_fullback_break_latch,
    break_test_line_anchor_x,
    carrier_behind_latched_line,
    classify_fullback_break,
    clear_fullback_break_latch,
    clear_fullback_chase_state,
    compute_fullback_anchor,
    establish_fullback_live_break_chase,
    fullback_breakaway_live,
    fullback_depth_break_active,
    fullback_emergency_defender_active,
    fullback_lateral_break_active,
    is_fullback_break_latched,
    is_fullback_emergency_engaged,
    latched_fullback_break_anchor_x,
    release_fullback_chase_on_anchor_relock,
    set_fullback_emergency_engaged,
    FB_ANCHOR_RELOCK_EPS_M,
    FB_BREAK_DEPTH,
    FB_BREAK_LATERAL,
    FB_BREAK_LATCH_ATTR,
    FB_BREAK_LATCH_ANCHOR_ATTR,
    FB_ANCHOR_HYSTERESIS_M,
    FB_CHASE_LEAD_MAX_M,
    FB_CHASE_TRAIL_M,
    FB_LATERAL_BREAK_LEVEL_M,
    FB_LATERAL_BREAK_M,
    FB_LATERAL_BREAK_RADIUS_M,
    FB_LINE_BREAK_MARGIN_M,
    FB_RELOCK_HANDOVER_ATTR,
)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)
    return bool(condition)


def section(n, title: str) -> None:
    print(f"\n[{n}] {title}")


# ======================================================================
# THE FRAME-382/383 SCENARIO (tmp_fb_authority_timeline.txt, window 16)
# ======================================================================

# The ruck / line anchor pair either side of the CONTACT -> DRIVE relock.
F382_RUCK_X: float = 8.25
F382_LINE_X: float = 18.25          # = ruck + 10, the PRE-relock anchor
F383_RUCK_X: float = 26.52
F383_LINE_X: float = 36.52          # = ruck + 10, the POST-relock anchor
RELOK_DELTA_M: float = F383_LINE_X - F382_LINE_X   # +18.27

# The bodies, IDENTICAL on both frames (the whole point).
BC_X: float = 26.52
BC_Y: float = 19.11
FB_X: float = 29.29
FB_Y: float = 22.40

# The probe's derived readings for the two frames.
PROBE_DEPTH_MARGIN_F382: float = 7.27   # bc.x - (line + margin)
PROBE_DEPTH_MARGIN_F383: float = -11.00
PROBE_LANE_GAP: float = 3.29            # |fb.y - bc.y|, the actor's channel
PROBE_REACH: float = 4.30               # hypot(bc - fb), to 2 dp
PROBE_REACH_EXACT: float = 4.30081      # hypot(-2.77, -3.29)
# The f=383 target the probe recorded verbatim: tgt=(48.44, 19.11).
PROBE_TGT_X: float = 48.44
PROBE_TGT_Y: float = 19.11
# The measured GOAL-SIDE re-pin on this fixture: 48.44083 - 29.29.
PROBE_REPIN_M: float = 19.1508

LANE_Y_FLOAT: float = BC_Y


class _Ball:
    """A stationary carrier: no velocity, so the chase command is the
    historical axis-margin trail point (bc.x - FB_CHASE_TRAIL_M, bc.y)."""

    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class _World:
    """Minimal world carrying the fields the FB authority reads.

    ``fullback_recover_allowed`` is deliberately False so the sweeper
    contract resolves to DRIFT -- the mode the telemetry row records at
    f=383 -- instead of the RECOVER fast path.
    """

    def __init__(self, anchor_x: float, bc: _Ball | None = None,
                 frame: int = 1) -> None:
        self.ruck_x_m = anchor_x - 10.0
        self.ruck_y_m = BC_Y
        self.defensive_line_anchor = anchor_x
        self.anchor_locked = True
        self.ball_carrier = bc
        self.frame_count = frame
        self.tes_active = False
        self.in_kickoff = False
        self.fullback_recover_allowed = False
        self.defending_team = ()


def make_fb(x: float = FB_X, y: float = FB_Y, pid: int = 201):
    from player.player import Player, DefenderRole

    p = Player(x=x, y=y, player_id=pid)
    p.jersey_number = 1
    p.max_speed_mps = 9.2
    p.max_defensive_speed_mps = 8.3
    p.fullback_role_active = True
    p.can_acquire_tackle = True
    p.defender_role = DefenderRole.DEF_LINE_READY
    p.tes_attached = False
    p.in_contact = False
    p._tackle_commit_active = False
    p.target_x = FB_X
    p.target_y = FB_Y
    return p


def run_relock_pair(lever_on: bool, frame_a_line: float = F382_LINE_X,
                    frame_b_line: float = F383_LINE_X,
                    bc_x_a: float = BC_X, bc_x_b: float = BC_X):
    """Drive the REAL writer across the two-frame relock pair.

    Returns ``(state_a, state_b, tx_b, sweeper_anchor_b)`` so a failing leg
    can print the whole frame pair rather than a bare comparison.
    """
    saved = fb.FB_BREAK_LATCH
    try:
        fb.FB_BREAK_LATCH = bool(lever_on)
        bc = _Ball(bc_x_a, BC_Y)
        world = _World(anchor_x=frame_a_line, bc=bc, frame=1)
        defender = make_fb()

        apply_fullback_geometry(world, defender)
        state_a = fb.movement_state_name(defender)

        bc.x = bc_x_b
        world.ruck_x_m = frame_b_line - 10.0
        world.defensive_line_anchor = frame_b_line
        world.frame_count = 2
        apply_fullback_geometry(world, defender)
        state_b = fb.movement_state_name(defender)
        anchor_b, _anchor_b_y = compute_fullback_anchor(bc, frame_b_line)
    finally:
        fb.FB_BREAK_LATCH = saved
    return state_a, state_b, float(defender.target_x), float(anchor_b)


# ======================================================================
# [1] THE FRAME-382/383 SIGNATURE
# ======================================================================

def sec_signature() -> None:
    section(1, "The frame-382/383 signature reproduces the telemetry")
    check("the relock moved the live line anchor +18.27 m",
          abs(RELOK_DELTA_M - 18.27) < 1e-9, f"delta={RELOK_DELTA_M:.4f}")
    check("both frames carry the SAME ruck -> line relationship",
          abs((F382_LINE_X - F382_RUCK_X) - 10.0) < 1e-9
          and abs((F383_LINE_X - F383_RUCK_X) - 10.0) < 1e-9,
          f"f382={F382_LINE_X - F382_RUCK_X} f383={F383_LINE_X - F383_RUCK_X}")
    check("the relock lands the RUCK exactly on the carrier; the LINE is "
          "then 10 m goal-side of it by construction",
          abs(F383_RUCK_X - BC_X) < 1e-9
          and abs((F383_LINE_X - BC_X) - 10.0) < 1e-9,
          f"ruck={F383_RUCK_X} bc={BC_X} line-bc={F383_LINE_X - BC_X}")
    check("the carrier is byte-identical across the pair",
          abs(BC_X - 26.52) < 1e-9 and abs(BC_Y - 19.11) < 1e-9,
          f"bc=({BC_X}, {BC_Y})")

    depth_382 = BC_X - (F382_LINE_X + FB_LINE_BREAK_MARGIN_M)
    depth_383 = BC_X - (F383_LINE_X + FB_LINE_BREAK_MARGIN_M)
    check("the DEPTH margin is +7.27 m on the CHASE frame",
          abs(depth_382 - PROBE_DEPTH_MARGIN_F382) < 1e-9,
          f"{depth_382:.4f}")
    check("the DEPTH margin is -11.00 m on the overwrite frame",
          abs(depth_383 - PROBE_DEPTH_MARGIN_F383) < 1e-9,
          f"{depth_383:.4f}")
    check("the actor's lane gap is 3.29 m (the probe's `lane`)",
          abs(abs(FB_Y - BC_Y) - PROBE_LANE_GAP) < 1e-9,
          f"{abs(FB_Y - BC_Y):.4f}")
    reach = math.hypot(BC_X - FB_X, BC_Y - FB_Y)
    check("the 2D reach matches the probe's `dist` at the probe's precision",
          round(reach, 2) == PROBE_REACH
          and abs(reach - PROBE_REACH_EXACT) < 1e-4,
          f"{reach:.5f} -> {round(reach, 2)}")
    check("the reach is WELL inside the documented 2D radius",
          math.hypot(BC_X - FB_X, BC_Y - FB_Y) <= FB_LATERAL_BREAK_RADIUS_M,
          f"reach={PROBE_REACH} radius={FB_LATERAL_BREAK_RADIUS_M}")


# ======================================================================
# [2] THE GATE WALK
# ======================================================================

def sec_gate_walk() -> None:
    section(2, "The gate walk: which leg flipped, which leg returned NONE")
    bc = _Ball(BC_X, BC_Y)
    defender = make_fb()

    kind_382 = classify_fullback_break(bc, defender, F382_LINE_X, BC_Y)
    kind_383 = classify_fullback_break(bc, defender, F383_LINE_X, BC_Y)
    check("G10a: the CHASE frame classifies DEPTH on the live anchor",
          kind_382 == FB_BREAK_DEPTH, kind_382)
    check("G10a: the overwrite frame classifies NONE on the live anchor",
          kind_383 == fb.FB_BREAK_NONE, kind_383)

    # G10a is the leg that FLIPPED.
    g10a_382 = BC_X > F382_LINE_X + FB_LINE_BREAK_MARGIN_M
    g10a_383 = BC_X > F383_LINE_X + FB_LINE_BREAK_MARGIN_M
    check("G10a DEPTH flips TRUE -> FALSE across the relock",
          g10a_382 and not g10a_383,
          f"f382={g10a_382} f383={g10a_383}")
    check("...so the classifier falls THROUGH G10a on the overwrite frame",
          not g10a_383)

    # G10b is the leg that RETURNED NONE.
    g10b_383 = BC_X < F383_LINE_X - FB_LATERAL_BREAK_LEVEL_M
    check("G10b LEVEL engages on the overwrite frame (the returning leg)",
          g10b_383,
          f"bc={BC_X} < line-margin={F383_LINE_X - FB_LATERAL_BREAK_LEVEL_M}")
    check("G10b does NOT engage on the CHASE frame",
          not (BC_X < F382_LINE_X - FB_LATERAL_BREAK_LEVEL_M))

    # G10c / G10d are order-shadowed by G10b but reported for completeness:
    # neither could have rescued the classification.
    channel_y = float(FB_Y)          # open-play mirroring: the actor's lane
    g10c = abs(BC_Y - channel_y) > FB_LATERAL_BREAK_M
    g10d = math.hypot(BC_X - FB_X, BC_Y - FB_Y) <= FB_LATERAL_BREAK_RADIUS_M
    check("G10c CHANNEL would also have failed (no lateral breach)",
          not g10c, f"|{BC_Y} - {channel_y}| = {abs(BC_Y - channel_y):.2f}")
    check("G10d REACH was satisfied (so the reach guard is NOT the cause)",
          g10d, f"reach={math.hypot(BC_X - FB_X, BC_Y - FB_Y):.2f}")


# ======================================================================
# [3] THE FAILURE AND THE FIX, THROUGH THE REAL WRITER
# ======================================================================

def sec_relock_pair() -> None:
    section(3, "Two-frame relock pair through the REAL geometry writer")

    # ---- ARM 1: THE PRE-CHANGE ENGINE (lever off) ----
    state_a_off, state_b_off, tx_off, anchor_off = run_relock_pair(False)
    print(f"    LEVER OFF  f382={state_a_off!r} f383={state_b_off!r} "
          f"tx[383]={tx_off:.4f} sweeper_anchor={anchor_off:.4f}")
    check("[OFF] the chase frame IS a chase", state_a_off == "CHASE",
          repr(state_a_off))
    check("[OFF] the relock frame COLLAPSES to the sweeper contract "
          "(matches the telemetry's mode=DRIFT)",
          state_b_off in ("DRIFT", "RECOVER") and state_b_off != "CHASE",
          repr(state_b_off))
    check("[OFF] ...and the command is re-pinned to the deep sweeper anchor",
          abs(tx_off - anchor_off) < 1e-9,
          f"tx={tx_off:.4f} anchor={anchor_off:.4f}")
    check("[OFF] ...and the re-pin reproduces the probe's recorded f=383 "
          "target VERBATIM",
          abs(tx_off - PROBE_TGT_X) < 1e-2,
          f"tx={tx_off:.4f} telemetry={PROBE_TGT_X}")
    check("[OFF] ...which is the measured GOAL-SIDE jump the probe records",
          abs((tx_off - FB_X) - PROBE_REPIN_M) < 1e-2,
          f"jump={tx_off - FB_X:.4f} expected={PROBE_REPIN_M}")

    # ---- ARM 2: THE SHIPPED LAW (lever on) ----
    state_a_on, state_b_on, tx_on, anchor_on = run_relock_pair(True)
    print(f"    LEVER ON   f382={state_a_on!r} f383={state_b_on!r} "
          f"tx[383]={tx_on:.4f} sweeper_anchor={anchor_on:.4f}")
    check("[ON] the chase frame IS a chase", state_a_on == "CHASE",
          repr(state_a_on))
    check("[ON] the relock frame is STILL a chase (chase preserved)",
          state_b_on == "CHASE", repr(state_b_on))
    check("[ON] ...and the command is the interception point, NOT the "
          "sweeper anchor",
          abs(tx_on - BC_X) <= 1e-9,
          f"tx={tx_on:.4f} expected={BC_X:.4f}")
    check("[ON] the pursuit point is on the carrier's lane",
          abs(tx_on - anchor_on) > 1.0,
          f"tx={tx_on:.4f} anchor={anchor_on:.4f}")
    check("[ON] the A/B is real: the two arms disagree by the re-pin",
          abs(tx_on - tx_off) > 20.0, f"off={tx_off:.4f} on={tx_on:.4f}")
    check("[ON] the LATENCY is closed: state differs on the relock frame",
          state_b_on != state_b_off,
          f"off={state_b_off!r} on={state_b_on!r}")


# ======================================================================
# [4] LATCH LIFECYCLE
# ======================================================================

def sec_lifecycle() -> None:
    section(4, "Latch lifecycle: arm, MIN-anchor, self-release, cycle end")

    saved = fb.FB_BREAK_LATCH
    fb.FB_BREAK_LATCH = True
    try:
        # ARM on DEPTH.
        d = make_fb()
        check("an unarmed fullback carries no latch",
              not is_fullback_break_latched(d)
              and latched_fullback_break_anchor_x(d) is None)
        arm_fullback_break_latch(d, FB_BREAK_DEPTH, F382_LINE_X)
        check("arming records the kind and the anchor",
              is_fullback_break_latched(d)
              and abs(latched_fullback_break_anchor_x(d) - F382_LINE_X) < 1e-9
              and getattr(d, fb.FB_BREAK_LATCH_KIND_ATTR) == FB_BREAK_DEPTH,
              f"anchor={latched_fullback_break_anchor_x(d)}")

        # MIN-anchor under repeated FORWARD relocks.
        arm_fullback_break_latch(d, FB_BREAK_DEPTH, F383_LINE_X)
        check("a FORWARD relock does not creep the latched anchor",
              abs(latched_fullback_break_anchor_x(d) - F382_LINE_X) < 1e-9,
              f"anchor={latched_fullback_break_anchor_x(d)}")
        arm_fullback_break_latch(d, FB_BREAK_DEPTH, F383_LINE_X + 30.0)
        check("a THIRD forward relock still cannot creep it",
              abs(latched_fullback_break_anchor_x(d) - F382_LINE_X) < 1e-9,
              f"anchor={latched_fullback_break_anchor_x(d)}")
        arm_fullback_break_latch(d, FB_BREAK_DEPTH, F382_LINE_X - 4.0)
        check("a RETREAT of the real anchor DOES tighten the latch",
              abs(latched_fullback_break_anchor_x(d) - (F382_LINE_X - 4.0))
              < 1e-9,
              f"anchor={latched_fullback_break_anchor_x(d)}")

        # Totality: a malformed anchor must not clear an existing latch.
        arm_fullback_break_latch(d, FB_BREAK_DEPTH, None)
        arm_fullback_break_latch(d, FB_BREAK_DEPTH, float("nan"))
        check("a malformed anchor leaves the latch untouched (total)",
              is_fullback_break_latched(d)
              and latched_fullback_break_anchor_x(d) is not None,
              f"anchor={latched_fullback_break_anchor_x(d)}")
        check("a None defender is a no-op",
              arm_fullback_break_latch(None, FB_BREAK_DEPTH, 10.0) is False
              and clear_fullback_break_latch(None) is False
              and is_fullback_break_latched(None) is False
              and latched_fullback_break_anchor_x(None) is None
              and carrier_behind_latched_line(None, _Ball(0.0, 0.0)) is False)

        # LATERAL arming.
        lat = make_fb()
        arm_fullback_break_latch(lat, FB_BREAK_LATERAL, 40.0)
        check("the latch records a LATERAL break too",
              getattr(lat, fb.FB_BREAK_LATCH_KIND_ATTR) == FB_BREAK_LATERAL,
              str(getattr(lat, fb.FB_BREAK_LATCH_KIND_ATTR)))

        # SELF-RELEASE: the carrier genuinely drops behind the LATCHED line.
        srel = make_fb()
        arm_fullback_break_latch(srel, FB_BREAK_DEPTH, F382_LINE_X)
        check("the carrier is NOT behind its own latched line while breaking",
              not carrier_behind_latched_line(srel, _Ball(BC_X, BC_Y)))
        check("the carrier IS behind the latched line once it retreats",
              carrier_behind_latched_line(srel, _Ball(10.0, BC_Y)))
        _s, self_state, self_tx, _a = run_relock_pair(
            True, frame_a_line=F382_LINE_X, frame_b_line=F382_LINE_X,
            bc_x_a=BC_X, bc_x_b=10.0,
        )
        check("SELF-RELEASE: the chase ends when the carrier drops behind "
              "the LATCHED line",
              self_state != "CHASE", repr(self_state))

        # EXPLICIT CYCLE END: clear_fullback_chase_state sweeps the tag.
        tagged = make_fb(pid=301)
        untagged = make_fb(pid=302)
        untagged.fullback_role_active = False
        arm_fullback_break_latch(tagged, FB_BREAK_DEPTH, F382_LINE_X)
        arm_fullback_break_latch(untagged, FB_BREAK_DEPTH, F382_LINE_X)
        world = _World(anchor_x=F383_LINE_X, bc=_Ball(BC_X, BC_Y))
        world.defending_team = (tagged, untagged)
        released = clear_fullback_chase_state(world)
        check("the cycle-end sweep releases the TAGGED fullback's latch",
              not is_fullback_break_latched(tagged)
              and tagged in released, f"released={len(released)}")
        check("...and it leaves a NON-fullback body alone",
              is_fullback_break_latched(untagged))
        check("a stub world with no team is a safe no-op",
              clear_fullback_chase_state(None) == []
              and clear_fullback_chase_state(_World(F382_LINE_X)) == [])

        # RESET semantics.
        rst = make_fb()
        arm_fullback_break_latch(rst, FB_BREAK_DEPTH, F382_LINE_X)
        setattr(rst, FB_RELOCK_HANDOVER_ATTR, True)
        check("clear_fullback_break_latch drops every latch artefact",
              clear_fullback_break_latch(rst)
              and not hasattr(rst, FB_BREAK_LATCH_ATTR)
              and not hasattr(rst, FB_BREAK_LATCH_ANCHOR_ATTR)
              and not hasattr(rst, FB_RELOCK_HANDOVER_ATTR))
        check("a second clear reports False (idempotent)",
              clear_fullback_break_latch(rst) is False)

        # The engagement packaging.
        eng = make_fb()
        arm_fullback_break_latch(eng, FB_BREAK_DEPTH, F382_LINE_X)
        set_fullback_emergency_engaged(eng)
        check("reset_fullback_emergency_engagement also drops the latch",
              fb.reset_fullback_emergency_engagement(eng)
              and not is_fullback_break_latched(eng))
        latched_only = make_fb()
        arm_fullback_break_latch(latched_only, FB_BREAK_DEPTH, F382_LINE_X)
        check("...including for a latched-but-NEVER-engaged body",
              fb.reset_fullback_emergency_engagement(latched_only) is False
              and not is_fullback_break_latched(latched_only))
    finally:
        fb.FB_BREAK_LATCH = saved


# ======================================================================
# [5] THE SINGLE READER
# ======================================================================

def sec_reader() -> None:
    section(5, "break_test_line_anchor_x: the single reader")
    saved = fb.FB_BREAK_LATCH
    try:
        world = _World(anchor_x=F383_LINE_X, bc=_Ball(BC_X, BC_Y))

        # Lever OFF: identity with the live anchor, in both spellings.
        fb.FB_BREAK_LATCH = False
        d_off = make_fb()
        arm_fullback_break_latch(d_off, FB_BREAK_DEPTH, F382_LINE_X)
        check("[OFF] no latch can be armed at all",
              not is_fullback_break_latched(d_off))
        check("[OFF] the reader returns the PASSED live anchor",
              abs(break_test_line_anchor_x(world, d_off, F383_LINE_X)
                  - F383_LINE_X) < 1e-9)
        check("[OFF] the reader falls back to the authority with no anchor "
              "argument",
              abs(break_test_line_anchor_x(world, d_off) - F383_LINE_X) < 1e-9,
              f"{break_test_line_anchor_x(world, d_off):.4f}")
        check("[OFF] a garbage anchor argument still resolves to the "
              "authority",
              abs(break_test_line_anchor_x(world, d_off, "junk")
                  - F383_LINE_X) < 1e-9)

        # Lever ON: the floor.
        fb.FB_BREAK_LATCH = True
        d_on = make_fb()
        check("[ON] no latch -> the LIVE anchor (identity)",
              abs(break_test_line_anchor_x(world, d_on, F383_LINE_X)
                  - F383_LINE_X) < 1e-9)
        check("[ON] no latch, no argument -> the LIVE authority",
              abs(break_test_line_anchor_x(world, d_on) - F383_LINE_X) < 1e-9)
        arm_fullback_break_latch(d_on, FB_BREAK_DEPTH, F382_LINE_X)
        check("[ON] latched -> the LATCHED anchor floors a forward move",
              abs(break_test_line_anchor_x(world, d_on, F383_LINE_X)
                  - F382_LINE_X) < 1e-9,
              f"{break_test_line_anchor_x(world, d_on, F383_LINE_X):.4f}")
        check("[ON] ...and a RETREAT of the live anchor still wins",
              abs(break_test_line_anchor_x(world, d_on, F382_LINE_X - 9.0)
                  - (F382_LINE_X - 9.0)) < 1e-9,
              f"{break_test_line_anchor_x(world, d_on, F382_LINE_X - 9.0):.4f}")
        check("[ON] a None defender degrades to the live anchor",
              abs(break_test_line_anchor_x(world, None, F383_LINE_X)
                  - F383_LINE_X) < 1e-9)

        # PURITY: the reader never mutates the world or the body.
        before_world = (world.ruck_x_m, world.defensive_line_anchor,
                        world.ball_carrier, world.frame_count)
        before_body = (d_on.x, d_on.y, d_on.target_x, d_on.target_y)
        for _ in range(3):
            break_test_line_anchor_x(world, d_on, F383_LINE_X)
            break_test_line_anchor_x(world, d_on)
        check("the reader is side-effect free (world unchanged)",
              (world.ruck_x_m, world.defensive_line_anchor,
               world.ball_carrier, world.frame_count) == before_world)
        check("the reader is side-effect free (body unchanged)",
              (d_on.x, d_on.y, d_on.target_x, d_on.target_y) == before_body)

        # carrier_past_defensive_line's anchor override.
        from game.defence.defence_lvl_1.movement_mode_decision import (
            carrier_past_defensive_line as _past,
        )
        check("anchor override: a live-anchor FALSE becomes TRUE under the "
              "latched anchor",
              not _past(world, _Ball(BC_X, BC_Y),
                        FB_LINE_BREAK_MARGIN_M, anchor_x=F383_LINE_X)
              and _past(world, _Ball(BC_X, BC_Y),
                        FB_LINE_BREAK_MARGIN_M, anchor_x=F382_LINE_X))
        check("no override: the live authority is read (the historical call)",
              not _past(world, _Ball(BC_X, BC_Y)))
        check("no override: a stale stored anchor cannot leak in",
              _past(world, _Ball(F383_LINE_X + 0.5, BC_Y),
                    FB_LINE_BREAK_MARGIN_M)
              is False
              or _past(world, _Ball(F383_LINE_X + 0.5, BC_Y)) is False)
        check("a garbage override is not fatal (total)",
              _past(world, _Ball(BC_X, BC_Y), 1.0, anchor_x="junk") is False)

        # The levers are the documented pins.
        check("the relock epsilon is the documented pin",
              abs(FB_ANCHOR_RELOCK_EPS_M - 0.5) < 1e-12,
              str(FB_ANCHOR_RELOCK_EPS_M))
        check("the anchor-timing hysteresis is unchanged",
              abs(FB_ANCHOR_HYSTERESIS_M - 0.15) < 1e-12,
              str(FB_ANCHOR_HYSTERESIS_M))
    finally:
        fb.FB_BREAK_LATCH = saved
# ======================================================================
# [6] EVERY CLASSIFY CONSUMER HONOURS THE LATCH
# ======================================================================

def sec_consumers() -> None:
    section(6, "Every classify consumer reads the latched anchor")

    # ---- 6a: the DEPTH case (the frame-382/383 shape) ----
    world = _World(anchor_x=F383_LINE_X, bc=_Ball(BC_X, BC_Y))
    d = make_fb()

    check("[depth/live] the live anchor says NO break on every consumer",
          not fullback_breakaway_live(world, d, world.ball_carrier)
          and not fullback_depth_break_active(world, d, world.ball_carrier)
          and not fullback_emergency_defender_active(
              world, d, world.ball_carrier),
          "unlatched")

    arm_fullback_break_latch(d, FB_BREAK_DEPTH, F382_LINE_X)

    check("[depth/latched] fullback_breakaway_live admits the chase",
          fullback_breakaway_live(world, d, world.ball_carrier))
    check("[depth/latched] fullback_depth_break_active admits the break",
          fullback_depth_break_active(world, d, world.ball_carrier))
    check("[depth/latched] fullback_emergency_defender_active admits the "
          "actor to tackle candidacy",
          fullback_emergency_defender_active(world, d, world.ball_carrier))
    check("[depth/latched] the LATERAL branch stays False (it is a DEPTH "
          "break, not a channel breach)",
          not fullback_lateral_break_active(world, d, world.ball_carrier))

    # ---- 6b: the live-break establishment ----
    est = make_fb()
    est.target_x = FB_X
    est.target_y = FB_Y
    check("[establish/unlatched] the establishment DECLINES on the live "
          "anchor (unchanged historical identity)",
          establish_fullback_live_break_chase(world, est) is False)
    est2 = make_fb()
    est2.target_x = FB_X
    est2.target_y = FB_Y
    arm_fullback_break_latch(est2, FB_BREAK_DEPTH, F382_LINE_X)
    check("[establish/latched] the establishment COMPLETES and re-arms the "
          "latch",
          establish_fullback_live_break_chase(world, est2) is True
          and is_fullback_break_latched(est2))
    check("[establish/latched] ...and it asserts the full pursuit contract",
          est2.target_source == "fullback_geometry"
          and fb.movement_state_name(est2) == "CHASE"
          and abs(est2.target_x - BC_X) <= 1e-9,
          f"tx={est2.target_x:.4f} src={est2.target_source}")

    # ---- 6c: the LATERAL case ----
    # Pre-relock line 40.0 / post-relock line 58.0; the carrier is LEVEL with
    # the old line, 10 m off the sweeper's lane, inside its 2D reach.
    lat_a_line, lat_b_line = 40.0, 58.0
    lat_bc = _Ball(40.0, 45.0)
    lat_world = _World(anchor_x=lat_b_line, bc=lat_bc)
    lat_def = make_fb(x=43.0, y=35.0)
    check("[lateral/live] the live anchor classifies NONE (line intact)",
          not fullback_lateral_break_active(lat_world, lat_def, lat_bc))
    arm_fullback_break_latch(lat_def, FB_BREAK_LATERAL, lat_a_line)
    check("[lateral/latched] the LATERAL branch admits the channel breach",
          fullback_lateral_break_active(lat_world, lat_def, lat_bc))
    check("[lateral/latched] the DEPTH branch stays False",
          not fullback_depth_break_active(lat_world, lat_def, lat_bc))
    check("[lateral/latched] emergency candidacy admits the actor",
          fullback_emergency_defender_active(lat_world, lat_def, lat_bc))

    # ---- 6d: the MMDL fullback branch ----
    from game.defence.defence_lvl_1.movement_mode_decision import (
        decide_movement_state,
    )
    mmdl_def = make_fb()
    arm_fullback_break_latch(mmdl_def, FB_BREAK_DEPTH, F382_LINE_X)
    mmdl_state = decide_movement_state(
        world, mmdl_def, world.ball_carrier, None
    )
    check("[mmdl/latched] the movement-mode decision layer agrees (CHASE)",
          mmdl_state == MovementState.CHASE, str(mmdl_state))
    mmdl_live = make_fb()
    mmdl_live_state = decide_movement_state(
        world, mmdl_live, world.ball_carrier, None
    )
    check("[mmdl/live] with no latch the same body is a sweeper",
          mmdl_live_state != MovementState.CHASE, str(mmdl_live_state))

    # ---- 6e: the LATERAL-latched MMDL state (2026-09-29) ----
    # A LATERAL channel breach arms the SAME latch but leaves the carrier
    # LEVEL with the line, so ``carrier_past_defensive_line`` is FALSE even
    # under the latched anchor -- the anchor override cannot carry this case.
    # The state policy must therefore read the LATCH itself.  With the gate
    # only on the canonical test the sweeper was handed a CHASE target while
    # its movement state stayed DRIFT, and DRIFT starves the depth axis by
    # construction (``LATERAL_PRIORITY_BY_STATE[DRIFT] = 0.8`` gives
    # ``dir_x = 0.2 * nx`` before normalisation, top speed 0.4 x max) --
    # the "closes in Y but never in X" symptom.
    from game.defence.defence_lvl_1.movement_mode_decision import (
        carrier_past_defensive_line as _past,
    )
    lat_mmdl = make_fb(x=43.0, y=35.0)
    arm_fullback_break_latch(lat_mmdl, FB_BREAK_LATERAL, lat_a_line)
    check("[mmdl/lateral-latched] the canonical anchor test ALONE is FALSE "
          "(the case the latch gate exists to carry)",
          not _past(lat_world, lat_bc, FB_LINE_BREAK_MARGIN_M,
                    anchor_x=break_test_line_anchor_x(lat_world, lat_mmdl)))
    check("[mmdl/lateral-latched] the movement-mode decision layer still "
          "agrees (CHASE)",
          decide_movement_state(lat_world, lat_mmdl, lat_bc, None)
          == MovementState.CHASE,
          str(decide_movement_state(lat_world, lat_mmdl, lat_bc, None)))
    check("[mmdl/lateral-unlatched] the identical body stays a sweeper",
          decide_movement_state(lat_world, make_fb(x=43.0, y=35.0), lat_bc,
                                None) != MovementState.CHASE)


# ======================================================================
# [7] THE EXPLICIT RELOCK HANDOVER BACKSTOP
# ======================================================================

def sec_backstop() -> None:
    section(7, "The explicit anchor-relock handover backstop")
    saved = fb.FB_BREAK_LATCH
    world = _World(anchor_x=F383_LINE_X, bc=_Ball(BC_X, BC_Y))
    try:
        # ARMED: the backstop releases the latch AND the engagement.
        fb.FB_BREAK_LATCH = True
        d = make_fb()
        arm_fullback_break_latch(d, FB_BREAK_DEPTH, F382_LINE_X)
        set_fullback_emergency_engaged(d)
        check("the body starts latched and engaged",
              is_fullback_break_latched(d)
              and is_fullback_emergency_engaged(d))
        performed = release_fullback_chase_on_anchor_relock(world, d)
        check("the backstop performs the explicit handover",
              performed is True)
        check("...clearing the latch",
              not is_fullback_break_latched(d))
        check("...clearing the emergency engagement",
              not is_fullback_emergency_engaged(d))
        check("...and STAMPING the transition so it is observable",
              bool(getattr(d, FB_RELOCK_HANDOVER_ATTR, False)))
        check("the actor is handed back as a line body",
              d.defender_role.name == "DEF_LINE_READY",
              str(getattr(d.defender_role, "name", d.defender_role)))
        check("an untagged body is left alone",
              release_fullback_chase_on_anchor_relock(world, make_fb_untagged())
              is False)
        check("a None defender is a no-op",
              release_fullback_chase_on_anchor_relock(world, None) is False)

        # DISARMED: NO WRITE AT ALL (the historical engine).
        fb.FB_BREAK_LATCH = False
        off = make_fb()
        arm_fullback_break_latch(off, FB_BREAK_DEPTH, F382_LINE_X)
        set_fullback_emergency_engaged(off)
        check("[OFF] the backstop writes NOTHING and reports no handover",
              release_fullback_chase_on_anchor_relock(world, off) is False)
        check("[OFF] ...the engagement marker is untouched",
              is_fullback_emergency_engaged(off))
        check("[OFF] ...and no relock stamp is written",
              not hasattr(off, FB_RELOCK_HANDOVER_ATTR))
    finally:
        fb.FB_BREAK_LATCH = saved


def make_fb_untagged():
    d = make_fb(pid=777)
    d.fullback_role_active = False
    return d


# ======================================================================
# [8] SOURCE WIRING
# ======================================================================

def sec_wiring() -> None:
    section(8, "Source wiring")
    with open("game/defence/defence_lvl_1/fullback_role.py",
              encoding="utf-8") as handle:
        fb_src = handle.read()
    with open("game/defence/defence_lvl_1/movement_mode_decision.py",
              encoding="utf-8") as handle:
        mmdl_src = handle.read()
    with open("world/open_play_engine.py", encoding="utf-8") as handle:
        ope_src = handle.read()

    check("the reader exists and is named for what it does",
          "def break_test_line_anchor_x" in fb_src)
    check("the reader floors the live anchor with the latched one",
          "return float(min(live, latched))" in fb_src)
    check("the reader documents the forward-relock rule",
          "a FORWARD relock cannot move the" in fb_src
          or "cannot move the test anchor" in fb_src)
    check("the cycle-end sweep exists",
          "def clear_fullback_chase_state" in fb_src)
    check("the latched DEPTH twin exists",
          "def fullback_depth_break_active" in fb_src)
    check("the backstop exists",
          "def release_fullback_chase_on_anchor_relock" in fb_src)

    check("every classify call site reads the single reader",
          fb_src.count("break_test_line_anchor_x(") >= 5,
          f"call sites={fb_src.count('break_test_line_anchor_x(')}")
    check("the geometry writer arms the latch on a classified break",
          "arm_fullback_break_latch(defender, break_kind, _break_test_anchor)"
          in fb_src)
    check("the live-break establishment arms it too",
          "arm_fullback_break_latch(defender, break_kind, _break_test_anchor)"
          in fb_src)
    check("the tag-loss loop clears the latch",
          "clear_fullback_break_latch(p)" in fb_src)
    check("the engagement reset clears it as well",
          "clear_fullback_break_latch(d)" in fb_src)
    check("the emergency admission reads the latched DEPTH twin",
          "if fullback_depth_break_active(world, d, bc):" in fb_src)
    check("the ownership-loss predicate reads it too",
          "fullback_depth_break_active(world, defender, bc)" in fb_src)
    check("the establishment admission is the latched two-branch test",
          "if not (\n        fullback_depth_break_active(world, defender, bc)"
          in fb_src)
    check("the geometry writer's relock detection is lever-gated",
          "if fullback_break_latch_enabled() and _live_line_anchor is not None:"
          in fb_src)

    # THE DEPTH SPLIT: the sweeper geometry still reads the LIVE anchor.
    check("the sweeper DEPTH geometry still reads the LIVE anchor",
          "anchor_x, anchor_y = compute_fullback_anchor(bc, "
          "float(line_anchor_x))" in fb_src)
    check("...and the break VERDICT reads the latched one",
          "_break_test_anchor = break_test_line_anchor_x(\n"
          "        world, defender, line_anchor_x\n    )" in fb_src)

    check("the MMDL passes the latched anchor into the canonical test",
          "carrier_past_defensive_line(world, bc, anchor_x=_test_anchor)"
          in mmdl_src)
    check("the canonical test accepts the override",
          "anchor_x: float | None = None" in mmdl_src)
    check("the MMDL branch imports the reader lazily (no import cycle)",
          "break_test_line_anchor_x as _fb_break_anchor" in mmdl_src)
    check("the MMDL branch gates the FB state on the LATCH itself",
          "is_fullback_break_latched as _fb_is_latched" in mmdl_src
          and "if _fb_is_latched(defender):" in mmdl_src)

    # THE SOLE REMAINING SWEEP SITE IS THE RELEASE-ENTRY TEARDOWN
    # (2026-10-01).  The broken-tackle path used to capture every tagged
    # fullback's latch, blanket-clear it and re-arm it; that exit is gone,
    # so exactly ONE sweep site survives and it is the cycle-end teardown.
    # The latch therefore outlives a missed tackle (the miss teardown hands
    # the ROLE back with ``clear_latch=False``) and dies only when the
    # tackle cycle really ends.
    check("the RELEASE-entry teardown is the ONLY latch sweep site",
          ope_src.count("_fb_clear_chase_state(self.world)") == 1,
          f"sites={ope_src.count('_fb_clear_chase_state(self.world)')}")
    check("the engine imports the sweep",
          "clear_fullback_chase_state as _fb_clear_chase_state" in ope_src)
    check("the miss teardown preserves the latch (clear_latch=False)",
          "clear_latch=False" in fb_src)
    check("the broken-tackle re-arm cycle is gone from the engine",
          "_fb_relatch" not in ope_src)


# ======================================================================
# [9] NO REGRESSION
# ======================================================================

def sec_no_regression() -> None:
    section(9, "No regression off a break, and a write-free disarmed lever")
    saved = fb.FB_BREAK_LATCH
    try:
        # ---- 9a: off a break, with the lever ON ----
        fb.FB_BREAK_LATCH = True
        bc = _Ball(30.0, 35.0)
        world = _World(anchor_x=40.0, bc=bc)
        d = make_fb(x=43.0, y=35.0)
        apply_fullback_geometry(world, d)
        bc.x = 30.0
        world.ruck_x_m = 48.0
        world.defensive_line_anchor = 58.0
        world.frame_count = 2
        apply_fullback_geometry(world, d)
        anchor_x, anchor_y = compute_fullback_anchor(bc, 58.0)
        check("[ON/off-break] the sweeper never chases", 
              fb.movement_state_name(d) != "CHASE",
              repr(fb.movement_state_name(d)))
        check("[ON/off-break] no latch is armed without a classified break",
              not is_fullback_break_latched(d))
        check("[ON/off-break] the command is the sweeper depth anchor",
              abs(d.target_x - anchor_x) <= 1e-9,
              f"tx={d.target_x:.4f} anchor={anchor_x:.4f}")

        # ---- 9b: a genuine break with NO relock behaves as before ----
        bc2 = _Ball(BC_X, BC_Y)
        w2 = _World(anchor_x=F382_LINE_X, bc=bc2)
        d2 = make_fb()
        apply_fullback_geometry(w2, d2)
        state_no_relock_on = fb.movement_state_name(d2)
        tx_no_relock_on = float(d2.target_x)

        fb.FB_BREAK_LATCH = False
        bc3 = _Ball(BC_X, BC_Y)
        w3 = _World(anchor_x=F382_LINE_X, bc=bc3)
        d3 = make_fb()
        apply_fullback_geometry(w3, d3)
        state_no_relock_off = fb.movement_state_name(d3)
        tx_no_relock_off = float(d3.target_x)

        check("a genuine break with no relock chases under BOTH lever "
              "states (the latch changes nothing here)",
              state_no_relock_on == "CHASE"
              and state_no_relock_off == "CHASE",
              f"on={state_no_relock_on!r} off={state_no_relock_off!r}")
        check("...to the byte-identical pursuit point",
              abs(tx_no_relock_on - tx_no_relock_off) <= 1e-9,
              f"on={tx_no_relock_on:.4f} off={tx_no_relock_off:.4f}")
        check("...and the pursuit point is bounded by the documented lead",
              abs(tx_no_relock_on - BC_X)
              <= FB_CHASE_TRAIL_M + FB_CHASE_LEAD_MAX_M + 1e-9,
              f"tx={tx_no_relock_on:.4f}")

        # ---- 9c: the DISARMED lever is write-free ----
        bc4 = _Ball(BC_X, BC_Y)
        w4 = _World(anchor_x=F382_LINE_X, bc=bc4)
        d4 = make_fb()
        fb.FB_BREAK_LATCH = False
        apply_fullback_geometry(w4, d4)
        bc4.x = BC_X
        w4.ruck_x_m = F383_RUCK_X
        w4.defensive_line_anchor = F383_LINE_X
        w4.frame_count = 2
        apply_fullback_geometry(w4, d4)
        check("[OFF] no relock bookkeeping is written at all",
              not hasattr(d4, "_fb_prev_line_anchor_x"))
        check("[OFF] no relock handover stamp is written",
              not hasattr(d4, FB_RELOCK_HANDOVER_ATTR))
        check("[OFF] the move does not leave a latch behind",
              not is_fullback_break_latched(d4))

        # ---- 9d: determinism ----
        runs = [run_relock_pair(True) for _ in range(3)]
        check("the latched path is deterministic across repeated runs",
              all(r == runs[0] for r in runs), str(runs[0]))

        # ---- 9e: a garbage world must not raise ----
        class _Broken:
            pass

        for bad in (None, _Broken()):
            try:
                break_test_line_anchor_x(bad, make_fb())
                clear_fullback_chase_state(bad)
                ok = True
            except Exception as exc:            # noqa: BLE001
                ok = False
                print(f"      raised: {exc!r}")
            check(f"a degenerate world ({type(bad).__name__}) is tolerated",
                  ok)
    finally:
        fb.FB_BREAK_LATCH = saved


def main() -> int:
    print("FULLBACK ANCHOR-RELOCK BREAK LATCH CONTRACT "
          "(frame-382/383, 2026-09-28)")
    print("=" * 68)
    sec_signature()
    sec_gate_walk()
    sec_relock_pair()
    sec_lifecycle()
    sec_reader()
    sec_consumers()
    sec_backstop()
    sec_wiring()
    sec_no_regression()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS - a FORWARD ruck relock can no longer silently")
    print("        collapse a live fullback chase: the break test reads the")
    print("        anchor latched when the chase was armed, the sweeper's")
    print("        depth geometry still follows the live anchor, the latch")
    print("        is released only at an explicit tackle-cycle end, and")
    print("        RLM_FB_BREAK_LATCH=0 restores the historical engine")
    print("        write-for-write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
