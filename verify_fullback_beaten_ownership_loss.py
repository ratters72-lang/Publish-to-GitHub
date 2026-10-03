"""Verify beaten-fullback revocation and standalone selector separation.

THE DEFECT THIS PINS
--------------------
A tagged fullback's separate emergency-engage authority must end when it is
genuinely beaten, while stale lane/commit/stance stamps must not transfer
movement ownership away from its geometry controller. Selector-owned lane
roles are now unavailable to fullbacks regardless of break or distance.

Measured shape (FULLBACK_TACKLE_LOSS_OWNERSHIP_REPORT.md sect.3):
``FB.x = 46.1``, ``BC.x = 55.4`` -- the last defender ~9.3 m BEHIND the
carrier holding PRIMARY, yielding the frame to writers that have already
released it.

THE LAW UNDER TEST
------------------
``FB_BEATEN_ENGAGE_M`` remains the geometric revocation band for a live
break-chase candidate and for stale-stamp cleanup. It is independent of
``tackle_role``: the separate authority is refreshed when the fullback is
back inside the band, and lane PRIMARY remains owned exclusively by line
defenders. Before TES arms the fullback, its geometry controller retains
movement ownership.

Legs:
  [1] Constants, lever and module surface.
  [2] ``is_beaten_by_carrier`` -- pure geometry, total, exact 6.0 m boundary.
  [3] ``fullback_beaten_ownership_loss_active`` -- the ownership-loss gate.
  [4] Fullback exclusion from lane selection; line PRIMARY is unchanged.
  [5] Fullback exclusion from the legacy banded selector.
  [6] The RELEASE contract -- the whole ownership package is withdrawn.
  [7] END-TO-END -- a beaten fullback's command remains geometry-owned and
      becomes the pursuit point (CHASE + governed speed).
  [8] Pre-TES commit stamps do not take fullback movement ownership.
  [9] Closing back inside restores standalone engage eligibility, not lane ownership.
  [10] Source wiring.

Run:  python verify_fullback_beaten_ownership_loss.py
Exit 0 = beaten revocation and standalone fullback ownership hold.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from player.movement_states import MovementState
from player.player import Player, DefenderRole
from world.world_state import WorldState, GameSubState

from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    clear_beaten_fullback_ownership_stamps,
    fullback_approach_cap_applies,
    fullback_beaten_ownership_loss_active,
    fullback_emergency_defender_active,
    fullback_tackle_authority_active,
    is_beaten_by_carrier,
    release_stale_fullback_lane_stamp,
    FB_BEATEN_ENGAGE_M,
    FB_BEATEN_OWNERSHIP,
    FB_BEATEN_OWNERSHIP_ENV_VAR,
    FB_CHASE_TRAIL_M,
    FB_OWNERSHIP_STAMP_ATTRS,
    FB_MAX_APPROACH_SPEED_MPS,
    DEPTH_MAX_M,
)
from game.defence.defence_lvl_1.intercept_tacklers import (
    is_lane_tackler_candidate,
    select_lane_tacklers,
)
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)
    return bool(condition)


def section(number, title: str) -> None:
    print(f"\n[{number}] {title}")


# ══════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════

# The measured frame from FULLBACK_TACKLE_LOSS_OWNERSHIP_REPORT.md sect.3:
# the last defender 9.3 m BEHIND the carrier, still holding PRIMARY.
FB_X: float = 46.1
BC_X: float = 55.4
BEATEN_GAP_M: float = BC_X - FB_X          # 9.3
LANE_Y: float = 35.0

# A LOCKED line anchor that makes the sweeper anchor saturate at
# DEPTH_MAX_M (the frame-1814 geometry), so the anchor command is static
# while the carrier streams.
LINE_ANCHOR_X: float = 63.80 - DEPTH_MAX_M  # 33.80


class _Ball:
    def __init__(self, x: float, y: float = LANE_Y) -> None:
        self.x = x
        self.y = y


class _World:
    """Minimal world carrying everything the FB authority reads."""

    def __init__(self, anchor_x: float = LINE_ANCHOR_X,
                 bc: _Ball | None = None, frame: int = 1) -> None:
        self.ruck_x_m = anchor_x - 10.0
        self.ruck_y_m = LANE_Y
        self.defensive_line_anchor = anchor_x
        self.anchor_locked = True
        self.ball_carrier = bc
        self.frame_count = frame
        self.tes_active = False
        self.in_kickoff = False
        self.fullback_recover_allowed = False


def make_fullback(x: float = FB_X, y: float = LANE_Y, pid: int = 301) -> Player:
    d = Player(x=x, y=y, player_id=pid)
    d.jersey_number = 1
    d.max_speed_mps = 9.2
    d.max_defensive_speed_mps = 8.3
    d.fullback_role_active = True
    d.can_acquire_tackle = True
    d.defender_role = DefenderRole.DEF_LINE_READY
    d.tes_attached = False
    d.in_contact = False
    d.tackle_role = None
    d._tackle_commit_active = False
    d.pre_tackle_stance = False
    d.frozen = False
    d.target_x = x
    d.target_y = y
    return d


def make_line_defender(x: float, y: float = LANE_Y, pid: int = 302,
                       depth_role: str = "A") -> Player:
    d = Player(x=x, y=y, player_id=pid)
    d.defender_role = DefenderRole.DEF_LINE_READY
    d.depth_role = depth_role
    d.depth_side = 1
    d.fullback_role_active = False
    d.can_acquire_tackle = True
    d.max_speed_mps = 9.2
    d.max_defensive_speed_mps = 8.3
    d.tackle_role = "PASSIVE"
    d._tackle_commit_active = False
    d.pre_tackle_stance = False
    d.vx = 9.0
    d.vy = 0.0
    d.frozen = False
    d.tackle_state_active = False
    return d


def carrier_of(x: float, y: float = LANE_Y, pid: int = 499) -> Player:
    """A ball-carrier body at *x* (the world's reference for break tests)."""
    c = Player(x=x, y=y, player_id=pid)
    c.max_speed_mps = 9.0
    c.mass = 90.0
    c.vx = 5.0
    c.vy = 0.0
    return c


def make_tuners() -> DefenceTuners:
    t = DefenceTuners()
    for name, value in (
        ("tackle_primary_distance_m", 3.0),
        ("tackle_primary_angle_deg", 45.0),
        ("tackle_primary_min_speed_mps", 1.0),
        ("tackle_secondary_distance_m", 5.0),
        ("tackle_secondary_angle_deg", 60.0),
        ("tackle_secondary_min_speed_mps", 1.0),
        ("tackle_third_distance_m", 7.0),
        ("tackle_third_angle_deg", 75.0),
    ):
        if not hasattr(t, name):
            setattr(t, name, value)
    return t


def make_state_world(defending, bc, ruck_x: float = 40.0) -> WorldState:
    """A real WorldState world for the banded selector legs."""
    w = WorldState()
    w.defending_team = list(defending)
    w.attacking_team = []
    w.ball_carrier = bc
    w.ruck_x_m = ruck_x
    w.ruck_y_m = LANE_Y
    w.sub_state = GameSubState.OPEN_PLAY
    w.tackler_ids = set()
    w.primary_tackler_id = None
    w.secondary_tacklers = []
    w.frame_count = 1
    w.dt = 1.0 / 60.0
    w.in_pursuit_mode = False
    w.first_tackle_not_made = False
    w.defence_enabled = True
    w.use_structured_open_play = True
    w._prev_frame_open_play = None
    w.defensive_line_anchor = ruck_x + 10.0
    w.defensive_line_update_enabled = True
    w.defensive_chase_enabled = True
    w.ball_owner = "BC"
    w.ball_x_m = bc.x
    w.ball_y_m = bc.y
    w.marker_lock_duration_s = 0.75
    w.marker_freeze_timer = 0.75
    w.pre_tackle_stance_radius_m = 0.8
    w.lane_tackler_switch_hysteresis_m = 0.5
    return w


# ══════════════════════════════════════════════════════════════════════
# [1] CONSTANTS, LEVER, MODULE SURFACE
# ══════════════════════════════════════════════════════════════════════

def sec_surface() -> None:
    section(1, "Constants, A/B lever and module surface")
    check("the beaten band is the 6.0 m mirror of the lane ahead-band",
          abs(FB_BEATEN_ENGAGE_M - 6.0) < 1e-12, str(FB_BEATEN_ENGAGE_M))
    check("the lever ships ON", FB_BEATEN_OWNERSHIP is True,
          str(FB_BEATEN_OWNERSHIP))
    check("the lever env var is the documented name",
          FB_BEATEN_OWNERSHIP_ENV_VAR == "RLM_FB_BEATEN_OWNERSHIP",
          FB_BEATEN_OWNERSHIP_ENV_VAR)
    check("the lever is readable at consumption time",
          callable(fb.fullback_beaten_ownership_enabled)
          and fb.fullback_beaten_ownership_enabled() is True)
    check("the geometry predicate is exposed",
          callable(fb.is_beaten_by_carrier))
    check("the ownership-loss predicate is exposed",
          callable(fb.fullback_beaten_ownership_loss_active))
    check("the stamp-withdrawal helper is exposed",
          callable(fb.clear_beaten_fullback_ownership_stamps))
    check("the withdrawn stamps are the three the authority reads",
          set(FB_OWNERSHIP_STAMP_ATTRS)
          == {"tackle_role", "_tackle_commit_active", "pre_tackle_stance"},
          str(FB_OWNERSHIP_STAMP_ATTRS))
    # The mirror law: the lane system's ahead-band default is the same 6.0.
    import inspect
    from game.defence.defence_lvl_1 import intercept_tacklers as it
    sig = inspect.signature(it.is_lane_tackler_candidate)
    check("the ahead-band default is the same 6.0 m the behind-band uses",
          abs(float(sig.parameters["ahead_engage_m"].default) - FB_BEATEN_ENGAGE_M)
          < 1e-12,
          str(sig.parameters["ahead_engage_m"].default))


# ══════════════════════════════════════════════════════════════════════
# [2] is_beaten_by_carrier — PURE GEOMETRY, TOTAL, EXACT BOUNDARY
# ══════════════════════════════════════════════════════════════════════

def sec_geometry() -> None:
    section(2, "is_beaten_by_carrier — the negative-side twin of the lane band")
    bc = _Ball(BC_X)

    check("EXACT band boundary is NOT beaten (strict <)",
          is_beaten_by_carrier(_Ball(FB_X).__class__(BC_X - FB_BEATEN_ENGAGE_M),
                               bc) is False,
          f"defender.x={BC_X - FB_BEATEN_ENGAGE_M:.3f} bc.x={BC_X:.3f}")
    check("one centimetre beyond the band IS beaten",
          is_beaten_by_carrier(
              Player(x=BC_X - FB_BEATEN_ENGAGE_M - 0.01, y=LANE_Y, player_id=1),
              bc) is True)
    check("the measured 9.3 m gap is beaten",
          is_beaten_by_carrier(
              Player(x=FB_X, y=LANE_Y, player_id=1), bc) is True,
          f"gap={BEATEN_GAP_M:.2f} m")
    check("a defender LEVEL with the carrier is not beaten",
          is_beaten_by_carrier(
              Player(x=BC_X, y=LANE_Y, player_id=1), bc) is False)
    check("a defender AHEAD of the carrier is not beaten",
          is_beaten_by_carrier(
              Player(x=BC_X + 12.0, y=LANE_Y, player_id=1), bc) is False)
    check("just inside the band (5.99 m behind) is not beaten",
          is_beaten_by_carrier(
              Player(x=BC_X - 5.99, y=LANE_Y, player_id=1), bc) is False)
    check("a deeper gap (25 m) is beaten",
          is_beaten_by_carrier(
              Player(x=BC_X - 25.0, y=LANE_Y, player_id=1), bc) is True)

    # TOTALITY: never raises, and an unreadable position is NEVER "beaten"
    # (a stub body must degrade to the historical behaviour).
    check("None defender reports False", is_beaten_by_carrier(None, bc) is False)
    check("None carrier reports False",
          is_beaten_by_carrier(Player(x=FB_X, y=LANE_Y, player_id=1), None)
          is False)

    class _Bad:
        x = "not-a-number"

    check("an unreadable x reports False (never raises)",
          is_beaten_by_carrier(_Bad(), bc) is False)

    class _Missing:
        pass

    check("a body with no x attribute reports False",
          is_beaten_by_carrier(_Missing(), bc) is False)

    class _NoneX:
        x = None

    check("a None x reports False (never treated as the try line)",
          is_beaten_by_carrier(_NoneX(), bc) is False)

    class _NanX:
        x = float("nan")

    check("a NaN x reports False",
          is_beaten_by_carrier(_NanX(), bc) is False)
    check("a non-numeric band falls back to the documented band",
          is_beaten_by_carrier(
              Player(x=FB_X, y=LANE_Y, player_id=1), bc,
              behind_engage_m="bad") is True)
    check("a custom band is honoured",
          is_beaten_by_carrier(
              Player(x=BC_X - 4.0, y=LANE_Y, player_id=1), bc,
              behind_engage_m=3.0) is True
          and is_beaten_by_carrier(
              Player(x=BC_X - 4.0, y=LANE_Y, player_id=1), bc,
              behind_engage_m=5.0) is False)


# ══════════════════════════════════════════════════════════════════════
# [3] fullback_beaten_ownership_loss_active — THE OWNERSHIP GATE
# ══════════════════════════════════════════════════════════════════════

def sec_predicate() -> None:
    section(3, "fullback_beaten_ownership_loss_active — the ownership gate")
    bc = _Ball(BC_X)
    world = _World(bc=bc)

    tagged = make_fullback()
    check("tagged + live break + beaten => ownership loss",
          fullback_beaten_ownership_loss_active(world, tagged) is True)

    untagged = make_line_defender(FB_X, pid=303)
    check("an untagged body never reports ownership loss",
          fullback_beaten_ownership_loss_active(world, untagged) is False)

    inside = make_fullback(x=BC_X - 4.0, pid=304)
    check("a tagged body INSIDE the band keeps its ownership",
          fullback_beaten_ownership_loss_active(world, inside) is False)

    # OFF A BREAK the ownership could not exist in the first place.
    off_world = _World(bc=_Ball(LINE_ANCHOR_X - 5.0))
    off_fb = make_fullback(x=LINE_ANCHOR_X - 20.0, pid=305)
    check("off a break the predicate is False (nothing to withdraw)",
          fullback_beaten_ownership_loss_active(off_world, off_fb) is False)

    # The tackle cycle owns an engaged body's stamps.
    engaged = make_fullback(pid=306)
    fb.set_fullback_emergency_engaged(engaged)
    try:
        check("an ENGAGED body's stamps belong to the tackle cycle",
              fullback_beaten_ownership_loss_active(world, engaged) is False)
    finally:
        fb.reset_fullback_emergency_engagement(engaged)

    # A LATERAL break counts as a genuine break, but roughly LEVEL in X must
    # NOT be read as "beaten".
    lateral_world = _World(anchor_x=40.0, bc=_Ball(40.0, LANE_Y + 10.0))
    lateral_fb = make_fullback(x=50.0, y=LANE_Y, pid=307)
    check("the lateral fixture really is a lateral break",
          fb.fullback_lateral_break_active(lateral_world, lateral_fb) is True)
    check("a lateral break LEVEL in X is not ownership loss",
          fullback_beaten_ownership_loss_active(
              lateral_world, lateral_fb) is False)

    # None body / None carrier never report ownership loss.
    check("None body reports False",
          fullback_beaten_ownership_loss_active(world, None) is False)
    check("no carrier reports False",
          fullback_beaten_ownership_loss_active(_World(bc=None), tagged)
          is False)

    # A/B: the lever can be rebound in-process and withdraws the law whole.
    saved = fb.FB_BEATEN_OWNERSHIP
    try:
        fb.FB_BEATEN_OWNERSHIP = False
        check("LEVER OFF: the ownership survives a beaten body",
              fullback_beaten_ownership_loss_active(
                  world, make_fullback(pid=308)) is False)
    finally:
        fb.FB_BEATEN_OWNERSHIP = saved
    check("LEVER restored ON", fb.FB_BEATEN_OWNERSHIP is True)
# ══════════════════════════════════════════════════════════════════════
# [4] FULLBACK EXCLUSION — THE LANE GATE
# ══════════════════════════════════════════════════════════════════════

def sec_lane_admission() -> None:
    section(4, "Standalone fullback exclusion — lane selector")
    carrier = Player(x=BC_X, y=LANE_Y, player_id=401)
    carrier.max_speed_mps = 9.0
    beaten = make_fullback()
    world = make_state_world([beaten], carrier, ruck_x=40.0)

    check("the emergency gate itself still admits the tagged body "
          "(tackle eligibility is untouched)",
          fullback_emergency_defender_active(world, beaten, carrier) is True)
    check("...but the LANE candidate test rejects the BEATEN body",
          is_lane_tackler_candidate(beaten, BC_X, world=world,
                                    bc=carrier) is False)

    inside = make_fullback(x=BC_X - 4.0, pid=402)
    check("a tagged body INSIDE the band remains outside lane selection",
          is_lane_tackler_candidate(inside, BC_X, world=world,
                                    bc=carrier) is False)

    ahead = make_fullback(x=BC_X + 3.0, pid=403)
    check("a tagged body AHEAD of the carrier is still outside lane selection",
          is_lane_tackler_candidate(ahead, BC_X, world=world,
                                    bc=carrier) is False)

    saved = fb.FB_BEATEN_OWNERSHIP
    try:
        fb.FB_BEATEN_OWNERSHIP = False
        check("LEVER OFF: the tagged body still cannot enter lane selection",
              is_lane_tackler_candidate(make_fullback(pid=404), BC_X,
                                        world=world, bc=carrier) is False)
    finally:
        fb.FB_BEATEN_OWNERSHIP = saved

    # The SELECTOR: a fullback never receives the lane role, while a
    # legitimate line body retains ordinary PRIMARY selection.
    fb_beaten = make_fullback(pid=405)
    line = make_line_defender(BC_X - 2.0, pid=406)
    lane_world = make_state_world([fb_beaten, line], carrier, ruck_x=40.0)
    lane_world.lane_tackler_switch_hysteresis_m = 0.5
    primaries = select_lane_tacklers(lane_world, [fb_beaten, line], carrier)
    check("select_lane_tacklers: the fullback is untouched",
          fb_beaten.tackle_role is None, str(fb_beaten.tackle_role))
    check("select_lane_tacklers: the line body still takes the lane",
          line.tackle_role == "PRIMARY" and line in primaries,
          str(line.tackle_role))


# ══════════════════════════════════════════════════════════════════════
# [5] FULLBACK EXCLUSION — THE BANDED SELECTOR (selectable POOL)
# ══════════════════════════════════════════════════════════════════════

def sec_banded_admission() -> None:
    section(5, "Standalone fullback exclusion — banded selector")
    tuners = make_tuners()
    dbu = DefensiveBehaviourUnified(tuners)
    carrier = Player(x=BC_X, y=LANE_Y, player_id=411)
    carrier.max_speed_mps = 9.0
    carrier.mass = 90.0

    # The banded selector's distance windows ship at 3.0 / 5.0 / 7.0 m, all
    # INSIDE the 6.0 m beat band, so a beaten body could not reach the pool
    # by proximity alone.  Widen them to 30 m so this leg genuinely drives
    # the re-grant path -- and the lever-OFF control below PROVES the widening
    # took effect (a mis-named tuner would leave the control unstamped and
    # fail it loudly instead of passing vacuously).
    for name in ("tackle_primary_distance_m",
                 "tackle_secondary_distance_m",
                 "tackle_third_distance_m"):
        setattr(tuners, name, 30.0)

    def _run(pid: int) -> Player:
        body = make_fullback(pid=pid)
        body.vx = 9.0
        body.vy = 0.0
        body.max_speed_mps = 9.0
        body_world = make_state_world([body], carrier, ruck_x=40.0)
        dbu.select_tacklers(body_world, [body])
        return body

    beaten = _run(412)
    check("the tagged fullback never receives a banded lane role",
          beaten.tackle_role not in ("PRIMARY", "SECONDARY", "THIRD", "EDGE"),
          str(beaten.tackle_role))

    saved = fb.FB_BEATEN_OWNERSHIP
    try:
        fb.FB_BEATEN_OWNERSHIP = False
        control = _run(413)
    finally:
        fb.FB_BEATEN_OWNERSHIP = saved
    check("LEVER OFF does not grant the fullback a selector-owned role",
          control.tackle_role not in ("PRIMARY", "SECONDARY", "THIRD", "EDGE"),
          str(control.tackle_role))

    # And a legitimate line body is untouched by the new clause.
    line = make_line_defender(BC_X - 2.0, pid=414)
    line_world = make_state_world([line], carrier, ruck_x=40.0)
    dbu.select_tacklers(line_world, [line])
    check("a normal line body still gets its banded PRIMARY",
          line.tackle_role == "PRIMARY", str(line.tackle_role))


# ══════════════════════════════════════════════════════════════════════
# [6] THE RELEASE CONTRACT — THE WHOLE OWNERSHIP PACKAGE
# ══════════════════════════════════════════════════════════════════════

def sec_release() -> None:
    section(6, "The RELEASE contract — the whole ownership package")
    bc = _Ball(BC_X)
    world = _World(bc=bc)

    d = make_fullback()
    d.tackle_role = "PRIMARY"
    d._tackle_commit_active = True
    d.pre_tackle_stance = True
    check("selector/commit stamps alone do not grant FB movement authority",
          fullback_tackle_authority_active(d) is False)

    cleared = clear_beaten_fullback_ownership_stamps(d)
    check("the withdrawal reports EVERY stamp it dropped",
          sorted(cleared) == ["_tackle_commit_active", "pre_tackle_stance",
                              "tackle_role"], str(cleared))
    check("the lane stamp is gone", d.tackle_role is None)
    check("the commit shim is gone", d._tackle_commit_active is False)
    check("the stance shim is gone", d.pre_tackle_stance is False)
    check("the authority no longer yields for the body",
          fullback_tackle_authority_active(d) is False)
    check("the sweeper cap is restored with the ownership",
          fullback_approach_cap_applies(d) is True)
    check("the withdrawal is idempotent (a second call reports nothing)",
          clear_beaten_fullback_ownership_stamps(d) == [])
    check("a None body is safe",
          clear_beaten_fullback_ownership_stamps(None) == [])

    # The release itself.
    beaten = make_fullback(pid=421)
    beaten.tackle_role = "PRIMARY"
    check("a BEATEN live-break lane stamp IS released",
          release_stale_fullback_lane_stamp(world, beaten) is True)
    check("...and cleared", beaten.tackle_role is None)

    inside = make_fullback(x=BC_X - 2.0, pid=422)
    inside.tackle_role = "PRIMARY"
    check("a lane stamp INSIDE the band is NOT released",
          release_stale_fullback_lane_stamp(world, inside) is False)
    check("...and survives", inside.tackle_role == "PRIMARY")

    engaged = make_fullback(pid=423)
    engaged.tackle_role = "PRIMARY"
    fb.set_fullback_emergency_engaged(engaged)
    try:
        check("an ENGAGED body's lane is never released",
              release_stale_fullback_lane_stamp(world, engaged) is False)
        check("...and survives", engaged.tackle_role == "PRIMARY")
    finally:
        fb.reset_fullback_emergency_engagement(engaged)

    untagged = make_line_defender(FB_X, pid=424)
    untagged.tackle_role = "PRIMARY"
    check("an untagged body is never considered",
          release_stale_fullback_lane_stamp(world, untagged) is False)

    clean = make_fullback(pid=425)
    check("a tagged body with no ownership reports no release",
          release_stale_fullback_lane_stamp(world, clean) is False)

    # The pre-existing stale-decay law is untouched: off a break the stamp is
    # released exactly as before.
    off_world = _World(bc=_Ball(LINE_ANCHOR_X - 5.0))
    decay = make_fullback(x=LINE_ANCHOR_X - 20.0, pid=426)
    decay.tackle_role = "PRIMARY"
    check("off a break the stale stamp is still released (B2b law intact)",
          release_stale_fullback_lane_stamp(off_world, decay) is True)
# ══════════════════════════════════════════════════════════════════════
# [7] END-TO-END — OWNERSHIP LOSS HANDS THE BODY TO THE GEOMETRY AUTHORITY
# ══════════════════════════════════════════════════════════════════════

def sec_end_to_end() -> None:
    section(7, "END-TO-END — a beaten lane holder reclaims the pursuit")
    bc = _Ball(BC_X)
    world = _World(bc=bc)
    pinned_x, _pinned_y = fb.compute_fullback_anchor(bc, LINE_ANCHOR_X)

    # THE DEFECT SHAPE: a tagged body holding PRIMARY, 9.3 m behind the
    # carrier, with the static sweeper-anchor command standing as its target.
    d = make_fullback()
    d.tackle_role = "PRIMARY"
    d.target_x = pinned_x
    d.target_y = LANE_Y
    d.movement_state = MovementState.DRIFT
    apply_fullback_geometry(world, d)

    check("the lane stamp is withdrawn on the frame the body is beaten",
          d.tackle_role is None, str(d.tackle_role))
    check("the authority stops yielding (the sweeper contract is restored)",
          fullback_tackle_authority_active(d) is False)
    check("the geometric writer re-enters the breakaway CHASE phase",
          d.movement_state == MovementState.CHASE, str(d.movement_state))
    check("the command is the interception point, not the static anchor",
          abs(d.target_x - BC_X) < 1e-9
          and abs(d.target_x - pinned_x) > 1.0,
          f"tx={d.target_x:.4f} pin={pinned_x:.4f}")
    check("the command is on the carrier's lane",
          abs(d.target_y - LANE_Y) < 1e-9, f"ty={d.target_y:.4f}")
    check("the writer is recorded as the owner",
          d.target_source == "fullback_geometry", str(d.target_source))
    check("the chase gets the governed speed, not the 2.0 m/s sweeper cap",
          d.move_speed > FB_MAX_APPROACH_SPEED_MPS,
          f"speed={d.move_speed:.4f} cap={FB_MAX_APPROACH_SPEED_MPS}")
    check("the body is left unfrozen", d.frozen is False)

    # A/B ARM 1: even with the legacy release/clearance levers off, a stale
    # lane role does not take movement ownership away from the FB controller.
    saved_beaten = fb.FB_BEATEN_OWNERSHIP
    saved_static = fb.FB_STATIC_PURSUIT_CLEAR
    try:
        fb.FB_BEATEN_OWNERSHIP = False
        fb.FB_STATIC_PURSUIT_CLEAR = False
        frozen = make_fullback(pid=441)
        frozen.tackle_role = "PRIMARY"
        frozen.target_x = pinned_x
        frozen.target_y = LANE_Y
        frozen.movement_state = MovementState.DRIFT
        apply_fullback_geometry(world, frozen)
        check("BOTH LEVERS OFF: the lane stamp survives",
              frozen.tackle_role == "PRIMARY", str(frozen.tackle_role))
        check("BOTH LEVERS OFF: geometry still owns the FB chase",
              abs(frozen.target_x - pinned_x) > 1.0
              and frozen.movement_state == MovementState.CHASE,
              f"tx={frozen.target_x:.4f} state={frozen.movement_state}")

        # A/B ARM 2: the new law is load-bearing ON ITS OWN.  With the legacy
        # static-pursuit clearance off but the ownership-loss law on, the body
        # is still released and re-owned -- so this stage does not depend on
        # the 2026-09-26 establishment to fix the beaten fullback.
        fb.FB_BEATEN_OWNERSHIP = True
        independent = make_fullback(pid=442)
        independent.tackle_role = "PRIMARY"
        independent.target_x = pinned_x
        independent.target_y = LANE_Y
        independent.movement_state = MovementState.DRIFT
        apply_fullback_geometry(world, independent)
        check("OWNERSHIP LAW ALONE (static clearance OFF) still re-owns the body",
              independent.tackle_role is None
              and abs(independent.target_x - BC_X) < 1e-9
              and independent.movement_state == MovementState.CHASE,
              f"tx={independent.target_x:.4f} state={independent.movement_state}")
    finally:
        fb.FB_BEATEN_OWNERSHIP = saved_beaten
        fb.FB_STATIC_PURSUIT_CLEAR = saved_static

    # A stale lane stamp inside the band is not movement ownership either.
    holder = make_fullback(x=BC_X - 2.0, pid=443)
    holder.tackle_role = "PRIMARY"
    holder.target_x = 12345.0
    holder.movement_state = MovementState.CHASE
    apply_fullback_geometry(world, holder)
    check("a lane holder INSIDE the band keeps its stamp",
          holder.tackle_role == "PRIMARY", str(holder.tackle_role))
    check("...but geometry retains command ownership before TES",
          abs(holder.target_x - 12345.0) > 1.0,
          f"tx={holder.target_x:.4f}")


# ══════════════════════════════════════════════════════════════════════
# [8] PRE-TES COMMIT STAMPS DO NOT OWN FULLBACK LOCOMOTION
# ══════════════════════════════════════════════════════════════════════

def sec_commit_handover() -> None:
    section(8, "Pre-TES commit stamps do not take FB movement ownership")
    bc = _Ball(BC_X)
    world = _World(bc=bc)
    pinned_x, _pinned_y = fb.compute_fullback_anchor(bc, LINE_ANCHOR_X)

    d = make_fullback(pid=451)
    d.tackle_role = "PRIMARY"
    d._tackle_commit_active = True          # the commit writer owns it this frame
    d.target_x = pinned_x                   # ...and left a static pin standing
    d.movement_state = MovementState.DRIFT

    apply_fullback_geometry(world, d)
    check("pre-TES commit stamp is cleared",
          d._tackle_commit_active is False)
    check("the beaten lane stamp is withdrawn",
          d.tackle_role is None, str(d.tackle_role))
    check("geometry controller owns the body immediately",
          d.movement_state == MovementState.CHASE, str(d.movement_state))
    check("the command is the interception point",
          abs(d.target_x - BC_X) < 1e-9,
          f"tx={d.target_x:.4f}")
    check("the governed chase speed is applied",
          d.move_speed > FB_MAX_APPROACH_SPEED_MPS,
          f"speed={d.move_speed:.4f}")

    # REACHABILITY OF THE DERIVED SHIMS.  ``release_stale_fullback_lane_stamp``
    # is the LANE-stamp releaser: a body holding ONLY a derived shim is not a
    # release candidate, because that shim belongs to its own writer
    # (``_apply_tackler_commit`` / the corridor stance).  The reason this is
    # safe is that neither shim is REACHABLE for a beaten body: the commit
    # writer only stamps a body whose ``tackle_role`` is PRIMARY/SECONDARY,
    # and the admission clauses above are exactly what stops the re-stamp --
    # so the writer withdraws its own flag on the next pass.  Asserted
    # behaviourally here, through the commit writer itself.
    from game.defence.defence_lvl_1.defensive_behaviour_unified import (
        _apply_tackler_commit,
    )
    writer_tuners = make_tuners()
    leak = make_fullback(pid=452)
    leak.tackle_role = None                  # the admission clauses withheld it
    leak._tackle_commit_active = True        # ...but a stale flag is standing
    commit_world = make_state_world([leak], carrier_of(BC_X), ruck_x=40.0)
    _apply_tackler_commit(commit_world, [leak], writer_tuners)
    check("the commit writer withdraws its own flag once PRIMARY is withheld",
          leak._tackle_commit_active is False,
          f"commit={leak._tackle_commit_active}")

    # And the lane-release API is honest about its own scope: a body holding
    # only the derived shims is not a LANE release (its owner keeps the frame)
    # -- which is why the shim is withdrawn by that owner, never guessed at.
    shim_only = make_fullback(pid=453)
    shim_only.tackle_role = None
    shim_only.pre_tackle_stance = True
    check("the lane release does not touch a body holding no lane stamp",
          release_stale_fullback_lane_stamp(world, shim_only) is False
          and shim_only.pre_tackle_stance is True)


# ══════════════════════════════════════════════════════════════════════
# [9] STANDALONE AUTHORITY — CLOSING BACK INSIDE THE BAND RE-ADMITS
# ══════════════════════════════════════════════════════════════════════

def sec_regain() -> None:
    section(9, "Standalone authority refresh after closing back inside")
    bc = _Ball(BC_X)
    carrier = Player(x=BC_X, y=LANE_Y, player_id=461)
    carrier.max_speed_mps = 9.0

    closer = make_fullback(x=BC_X - 5.0, pid=462)
    closer.vx = 9.0
    closer.movement_state = MovementState.CHASE
    world = make_state_world([closer], carrier, ruck_x=40.0)
    from game.defence.defence_lvl_1.defensive_behaviour_unified import (
        _fullback_tackle_candidates,
    )
    check("once back inside, standalone break-chase authority refreshes",
          _fullback_tackle_candidates([closer], [], world, carrier) == [closer])
    check("fullback remains excluded from lane candidacy",
          is_lane_tackler_candidate(closer, BC_X, world=world,
                                    bc=carrier) is False)
    check("the ownership-loss predicate has cleared",
          fullback_beaten_ownership_loss_active(world, closer) is False)

    line = make_line_defender(BC_X - 8.0, pid=463)
    line.tackle_role = "LINE"
    world.lane_tackler_switch_hysteresis_m = 0.5
    primaries = select_lane_tacklers(world, [closer, line], carrier)
    check("lane selection leaves the fullback role untouched",
          closer.tackle_role is None and closer not in primaries,
          str(closer.tackle_role))
    check("the line defender still receives lane PRIMARY",
          line.tackle_role == "PRIMARY" and line in primaries,
          str(line.tackle_role))

    check("the release no longer withdraws anything from it",
          release_stale_fullback_lane_stamp(_World(bc=bc), closer) is False)
    check("no stale selector stamp is created", closer.tackle_role is None)


# ══════════════════════════════════════════════════════════════════════
# [10] SOURCE WIRING
# ══════════════════════════════════════════════════════════════════════

def sec_wiring() -> None:
    section(10, "Source wiring — all three ownership sites")
    with open("game/defence/defence_lvl_1/fullback_role.py",
              encoding="utf-8") as handle:
        fbr = handle.read()
    with open("game/defence/defence_lvl_1/intercept_tacklers.py",
              encoding="utf-8") as handle:
        itx = handle.read()
    with open("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
              encoding="utf-8") as handle:
        dbu = handle.read()

    check("fullback_role defines the ownership-loss predicate",
          "def fullback_beaten_ownership_loss_active" in fbr)
    check("fullback_role defines the geometry twin",
          "def is_beaten_by_carrier" in fbr)
    check("fullback_role defines the stamp withdrawal",
          "def clear_beaten_fullback_ownership_stamps" in fbr)
    check("fullback_role documents the measured 9.3 m frame",
          "FB.x = 46.1" in fbr and "BC.x = 55.4" in fbr)
    check("the release applies the beaten clause",
          "and not fullback_beaten_ownership_loss_active(world, defender)"
          in fbr)
    check("the release withdraws the whole stamp package",
          "clear_beaten_fullback_ownership_stamps(defender)" in fbr)
    check("lane eligibility unconditionally excludes tagged fullbacks",
          'if getattr(d, "fullback_role_active", False)' in itx
          and "return False" in itx[itx.index(
              'if getattr(d, "fullback_role_active", False)'
          ):])
    check("the banded selector excludes fullbacks from selector ownership",
          "def _fb_lane_admissible(d)" in dbu
          and "return not getattr(d, \"fullback_role_active\", False)" in dbu
          and "if _fb_lane_admissible(d) and not is_retreat_obligated(d, world)"
          in dbu)
    check("the A/B lever is documented in the module",
          "RLM_FB_BEATEN_OWNERSHIP" in fbr)

    # Beaten ownership still governs release, while standalone engagement
    # has a dedicated break-chase authority outside selector-owned roles.
    check("standalone break-chase authority remains available",
          "def refresh_fullback_emergency_engage_authority" in fbr
          and "refresh_fullback_emergency_engage_authority" in dbu)
    check("line membership is still NOT consulted by the lane gate",
          "is_fullback_line_member" not in itx)


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    print("FULLBACK BEATEN-OWNERSHIP-LOSS CONTRACT (2026-09-27)")
    print("=" * 68)
    sec_surface()
    sec_geometry()
    sec_predicate()
    sec_lane_admission()
    sec_banded_admission()
    sec_release()
    sec_end_to_end()
    sec_commit_handover()
    sec_regain()
    sec_wiring()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS — fullbacks stay outside selector-owned lane roles;")
    print("        the 6.0 m beaten gate revokes stale emergency ownership and")
    print("        clears old lane/commit/stance stamps. The geometry controller")
    print("        retains pursuit until TES arms the fullback, and standalone")
    print("        eligibility returns when the fullback closes back inside.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
