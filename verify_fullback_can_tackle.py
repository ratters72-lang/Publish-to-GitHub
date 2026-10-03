"""verify_fullback_can_tackle.py -- standalone emergency engage admission.

The fullback is never a lane candidate. On a genuine break chase it receives
an independent engage authority, retains fullback-controller locomotion, and
falls back into the existing tackle classifier after the lane primary.

Run:  python verify_fullback_can_tackle.py
Exit 0 on success.
"""
import sys

sys.path.insert(0, ".")

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from world.world_state import WorldState, GameSubState
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
    _apply_tackler_commit,
    _fullback_tackle_candidates,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.intercept_tacklers import (
    is_lane_tackler_candidate,
)
from game.defence.defence_lvl_1.fullback_role import (
    FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR,
    fullback_tackle_side_owner_live,
    fullback_tackle_authority_active,
    refresh_fullback_emergency_engage_authority,
)
from game.defence.defence_lvl_1.pretackle_classifier import is_engage_role

# The line anchor both scenarios are measured against (ruck_x + 10).
LINE_ANCHOR_X = 60.0


def make_player(pid, x, y, **kwargs):
    p = Player(x=x, y=y, player_id=pid)
    p.max_speed_mps = kwargs.get("max_speed_mps", 8.0)
    p.max_defensive_speed_mps = kwargs.get("max_defensive_speed_mps", 8.3)
    p.vx = kwargs.get("vx", 0.0)
    p.vy = kwargs.get("vy", 0.0)
    p.depth_role = kwargs.get("depth_role", None)
    p.defender_role = kwargs.get("defender_role", DefenderRole.DEF_LINE_READY)
    p.fullback_role_active = kwargs.get("fullback_role_active", False)
    p.frozen = False
    p.freeze_timer = 0.0
    p.tackle_role = "PASSIVE"
    p._tackle_commit_active = False
    p.lane_index = 0
    p.distance_to = lambda other: ((p.x - other.x) ** 2 + (p.y - other.y) ** 2) ** 0.5
    return p


def make_world(bc):
    world = WorldState()
    world.ball_carrier = bc
    world.defending_team = []
    world.sub_state = GameSubState.OPEN_PLAY
    world.ruck_x_m = LINE_ANCHOR_X - 10.0
    world.ruck_y_m = 35.0
    world.defensive_line_anchor = LINE_ANCHOR_X
    world.anchor_locked = True
    world.tackler_ids = set()
    world.primary_tackler_id = None
    world.secondary_tacklers = []
    world.frame_count = 0
    world.dt = 1.0 / 60.0
    world.in_pursuit_mode = False
    return world


def main():
    t = DefenceTuners()
    t.tackle_primary_distance_m = 3.0
    t.tackle_primary_angle_deg = 45.0
    t.tackle_primary_min_speed_mps = 1.0
    t.tackle_secondary_distance_m = 5.0
    t.tackle_secondary_angle_deg = 60.0
    t.tackle_secondary_min_speed_mps = 1.0
    t.tackle_third_distance_m = 7.0
    t.tackle_third_angle_deg = 75.0

    dbu = DefensiveBehaviourUnified(t)
    tackle_roles = ("PRIMARY", "SECONDARY", "THIRD", "EDGE")

    # ── (a) NO BREAK: the carrier is level with the anchor, so the tagged
    #     fullback standing right next to it must NOT be drafted.  This is
    #     the assertion the old "fullback is always tackle-eligible" contract
    #     got wrong.
    bc_off = make_player(99, LINE_ANCHOR_X, 35.0, max_speed_mps=9.0)
    world_off = make_world(bc_off)
    fb_off = make_player(101, LINE_ANCHOR_X - 1.0, 35.0,
                         fullback_role_active=True, vx=5.0)
    fb_off.can_acquire_tackle = True
    world_off.defending_team = [fb_off]
    refresh_fullback_emergency_engage_authority(world_off, fb_off, bc_off)
    if fb_off.tackle_role in tackle_roles:
        print(f"FAIL: tagged fullback drafted as {fb_off.tackle_role!r} "
              f"with NO line break")
        return 1
    print("PASS: tagged fullback stays PASSIVE with no line break")

    # ── (b) GENUINE BREAK CHASE: authority is standalone and does not stamp
    #     or compete for a lane PRIMARY.
    bc_on = make_player(98, LINE_ANCHOR_X + 6.0, 35.0, max_speed_mps=9.0)
    world_on = make_world(bc_on)
    fb_on = make_player(102, LINE_ANCHOR_X + 5.0, 35.0,
                        fullback_role_active=True, vx=5.0)
    fb_on.can_acquire_tackle = True
    fb_on.movement_state = MovementState.CHASE
    fb_on.tackle_role = "LINE"
    # Keep a line defender at/behind the anchor so this is a held-line break,
    # not merely a carrier running beyond an unoccupied anchor.
    line = make_player(103, LINE_ANCHOR_X - 0.5, 35.0)
    world_on.defending_team = [fb_on, line]
    dbu.select_tacklers(world_on, [fb_on, line])
    if fb_on.tackle_role != "PASSIVE":
        print("FAIL: legacy banded selector granted a role to the fullback")
        return 1
    fb_on.tackle_role = "LINE"
    if not refresh_fullback_emergency_engage_authority(world_on, fb_on, bc_on):
        print("FAIL: genuine break chase did not grant FB engage authority")
        return 1
    if fb_on.tackle_role != "LINE":
        print("FAIL: standalone authority rewrote the FB lane role")
        return 1
    dbu.select_lane_tacklers(world_on, [fb_on, line], bc_on)
    if line.tackle_role != "PRIMARY" or fb_on.tackle_role != "LINE":
        print("FAIL: FB displaced the line PRIMARY or lost its own role")
        return 1
    if _fullback_tackle_candidates([fb_on], [line], world_on, bc_on) != [fb_on]:
        print("FAIL: FB was not routed into the standalone engage pool")
        return 1
    if fullback_tackle_authority_active(fb_on):
        print("FAIL: standalone eligibility took movement from the FB controller")
        return 1
    if fullback_tackle_side_owner_live(fb_on):
        print("FAIL: pre-TES candidate is reported as tackle-side movement-owned")
        return 1
    for role in ("PRIMARY", "LINE", None):
        fb_on.tackle_role = role
        if not is_engage_role(fb_on):
            print(f"FAIL: classifier authority was lost at tackle_role={role!r}")
            return 1

    fb_on.tackle_role = "PRIMARY"
    fb_on._tackle_commit_active = True
    fb_on.target_x = 987.0
    fb_on.target_y = 654.0
    _apply_tackler_commit(world_on, [fb_on], t)
    if (
        fb_on._tackle_commit_active
        or (fb_on.target_x, fb_on.target_y) != (987.0, 654.0)
    ):
        print("FAIL: pre-TES commit writer took movement from the FB controller")
        return 1
    print("PASS: FB has standalone engage authority; line PRIMARY is unchanged")
    print("PASS: authority reaches classifier while movement remains FB-owned")

    # ── (c) The separate authority is restricted to live break chase.
    if is_lane_tackler_candidate(fb_on, bc_on.x, world=world_on, bc=bc_on):
        print("FAIL: tagged fullback entered lane candidacy")
        return 1
    if _fullback_tackle_candidates([fb_off], [], world_off, bc_off):
        print("FAIL: tagged fullback was admitted without a break chase")
        return 1
    fb_on.movement_state = MovementState.DRIFT
    if refresh_fullback_emergency_engage_authority(world_on, fb_on, bc_on):
        print("FAIL: authority survived exit from break chase")
        return 1
    if is_lane_tackler_candidate(fb_off, bc_off.x, world=world_off, bc=bc_off):
        print("FAIL: tagged fullback admitted as lane tackler with no break")
        return 1
    print("PASS: no lane candidacy or non-break/chase admission")

    # ── (d) The tag and separate authority survive lane role churn.
    fb_on.movement_state = MovementState.CHASE
    refresh_fullback_emergency_engage_authority(world_on, fb_on, bc_on)
    for role in ("PRIMARY", "LINE", None):
        fb_on.tackle_role = role
        if not getattr(fb_on, FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR, False):
            print(f"FAIL: authority was lost during role churn at {role!r}")
            return 1
    if not getattr(fb_on, "fullback_role_active", False):
        print("FAIL: the fullback tag was lost")
        return 1
    print("PASS: separate engage authority survives PRIMARY/LINE/None churn")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
