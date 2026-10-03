"""
verify_fullback_role.py — Validation for the dynamic fullback role module.

Confirms the FULLBACK ROLE MODULE spec:

  1. ANCHOR GEOMETRY — depth scales 3→30 m behind the defensive line as
     ruck_x moves from the left tryline (10) to halfway (60); lateral
     is 60% of (ball.y − field_centre_y); kickoff override = 2 m behind
     the line, central.
  2. KICKOFF OVERRIDE — shallow + central anchor during KICKOFF.
  3. DYNAMIC REASSIGNMENT — the actor swaps to the next best available
     player when the current actor becomes unavailable (frozen, marker,
     attached, tackler, too far).
  4. LINE-NUMBER INTEGRITY — a fullback-marked defender is excluded from
     depth-role assignment so it never occupies a line lane slot, and the
     structured pipeline's `assign_fullback_role` keeps the actor out of
     the PRIMARY/SECONDARY classification.
  5. STABILISER GATE — the fullback's forced DRIFT movement state is NOT
     in NON_STABILISED_MOVEMENT_STATES, so target smoothing applies.
  6. FORWARD-DECLARED SURFACE — the module exposes the exact functions the
     spec requires: compute_fullback_anchor, choose_best_available_player,
     assign_fullback_role (+ kickoff override + reassignment rules).
  7. CONTROLLED EMERGENCY-DEFENDER PATH (B2b, 2026-09-16) — TACKLE
     ELIGIBILITY and LINE MEMBERSHIP are separate concepts.  A tagged
     fullback may enter lane candidacy / pre-tackle commit / TES arming
     when, and ONLY when, a genuine line break exists, and stays excluded
     from the line population and depth roles throughout.  Arming never
     revokes the tag, and a break that decays without a tackle cannot
     strand a lane stamp that would suspend the sweeper contract.
"""

import sys
import math

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from player.movement_states import MovementState
from player.target_stabiliser import NON_STABILISED_MOVEMENT_STATES
from game.defence.defence_lvl_1.fullback_role import (
    compute_fullback_anchor,
    choose_best_available_player,
    assign_fullback_role,
    FIELD_CENTRE_Y,
    FIELD_MAX_X,
    LEFT_TRYLINE_X,
    HALFWAY_X,
    KICKOFF_FB_LATERAL_OFFSET_M,
    # B2b (2026-09-16): the controlled emergency-defender path.  TACKLE
    # ELIGIBILITY and LINE MEMBERSHIP are separate concepts; section [8]
    # below pins the module surface and the separation.
    is_fullback_line_member,
    fullback_emergency_defender_active,
    fullback_tackle_authority_active,
    fullback_approach_cap_applies,
    release_stale_fullback_lane_stamp,
    is_fullback_emergency_engaged,
    set_fullback_emergency_engaged,
    reset_fullback_emergency_engagement,
    line_break_active,
    FULLBACK_TACKLE_CANDIDACY,
    FB_MAX_APPROACH_SPEED_MPS,
    # OPEN-PLAY LATERAL MIRRORING (2026-09-22): the weight the anchor uses
    # whenever a LIVE CARRIER is the reference; the 0.6 LATERAL_TRACK sweep
    # is the loose-ball contract only.
    FB_LATERAL_TRACK_OPEN_PLAY,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def make_players(n, start_x=70.0, start_y=20.0, gap_y=6.0):
    """Build n defending players spread across Y (all defender_role=None)."""
    players = []
    for i in range(n):
        p = Player(x=start_x, y=start_y + i * gap_y, player_id=i + 1)
        p.defender_role = DefenderRole.DEF_LINE_READY
        p.is_defender = True
        players.append(p)
    return players


class FakeBall:
    def __init__(self, x, y):
        self.x = x
        self.y = y


def main():
    print("=" * 64)
    print("FULLBACK ROLE MODULE VALIDATION")
    print("=" * 64)

    # ── 0. Module surface (spec 6) ────────────────────────────────
    print("\n[0] Module surface")
    from game.defence.defence_lvl_1 import fullback_role as fb_mod
    for fn in ("compute_fullback_anchor", "choose_best_available_player",
               "assign_fullback_role"):
        check(f"defines {fn}()", callable(getattr(fb_mod, fn, None)))

    # ── 1. Anchor depth: tryline → halfway → midfield ─────────────
    # CORRECTED SPEC (2026-08-08, root cause #1/#9): the fullback sits
    # DEEP BEHIND the line toward +X (defence retreats RIGHT), so
    # anchor_x = defensive_line_x + depth.
    print("\n[1] Anchor geometry (depth 3..30 m behind line, +X)")

    # Goal-line: ruck ~10 → line_x 20 → t=0 → depth = 3 → anchor = 23.
    ax, ay = compute_fullback_anchor(FakeBall(10.0, 35.0), defensive_line_x=20.0)
    check(
        "goal-line depth = 3 m behind line (t=0)",
        abs(ax - (20.0 + 3.0)) < 0.01,
        f"anchor_x={ax:.2f}",
    )

    # Halfway: ruck 50 → line_x 60 → depth = lerp(3,30, ~0.8) ≈ 24.6
    ax, ay = compute_fullback_anchor(FakeBall(50.0, 35.0), defensive_line_x=60.0)
    expected_depth = 3.0 + (30.0 - 3.0) * ((50.0 - LEFT_TRYLINE_X) / (HALFWAY_X - LEFT_TRYLINE_X))
    check(
        "halfway depth matches formula",
        abs((ax - 60.0) - expected_depth) < 0.01,
        f"anchor_x={ax:.2f} expected_depth={expected_depth:.2f}",
    )

    # Midfield (ruck 35): depth ≈ 16.5 → anchor ≈ 89.5 for line_x 73.
    ax, ay = compute_fullback_anchor(FakeBall(35.0, 35.0), defensive_line_x=73.0)
    expected_depth = 3.0 + (30.0 - 3.0) * ((35.0 - LEFT_TRYLINE_X) / (HALFWAY_X - LEFT_TRYLINE_X))
    check(
        "midfield depth matches formula",
        abs((ax - 73.0) - expected_depth) < 0.01,
        f"anchor_x={ax:.2f} expected_depth={expected_depth:.2f}",
    )

    # Depth monotonic: halfway is DEEPER (lerp→30) than goal-line (lerp→3).
    goal_x, _ = compute_fullback_anchor(FakeBall(12.0, 35.0), defensive_line_x=22.0)
    half_x, _ = compute_fullback_anchor(FakeBall(55.0, 35.0), defensive_line_x=65.0)
    check(
        "depth is deeper at halfway than at goal line",
        (half_x - 65.0) > (goal_x - 22.0),
        f"goal_depth={(goal_x-22.0):.2f} half_depth={(half_x-65.0):.2f}",
    )

    # ── 2. Lateral: 60% toward ball.y ──────────────────────────────
    print("\n[2] Lateral geometry: loose-ball sweep + open-play mirroring")
    ax, ay = compute_fullback_anchor(
        FakeBall(35.0, 50.0), defensive_line_x=70.0, open_play=False
    )
    expected_y = FIELD_CENTRE_Y + (50.0 - FIELD_CENTRE_Y) * 0.6
    check(
        "ball.y=50 → anchor_y = 35 + 9 = 44",
        abs(ay - expected_y) < 0.01,
        f"anchor_y={ay:.2f} expected={expected_y:.2f}",
    )
    ax, ay = compute_fullback_anchor(
        FakeBall(35.0, 20.0), defensive_line_x=70.0, open_play=False
    )
    expected_y = FIELD_CENTRE_Y + (20.0 - FIELD_CENTRE_Y) * 0.6
    check(
        "ball.y=20 → anchor_y = 35 − 9 = 26",
        abs(ay - expected_y) < 0.01,
        f"anchor_y={ay:.2f} expected={expected_y:.2f}",
    )
    # LOOSE BALL (no carrier): the historical fractional sweep weight.
    ax, ay = compute_fullback_anchor(
        FakeBall(35.0, 60.0), defensive_line_x=70.0, open_play=False
    )
    check(
        "sideline loose ball (y=60) → fullback 12 m in from sideline (~50)",
        abs(ay - (FIELD_CENTRE_Y + (60.0 - FIELD_CENTRE_Y) * 0.6)) < 0.01,
        f"anchor_y={ay:.2f}",
    )

    # ── OPEN-PLAY MIRRORING (2026-09-22) ──────────────────────────
    # With a LIVE CARRIER as the anchor reference (the default,
    # open_play=True) the lateral destination IS the carrier's lane at
    # full weight, so the last defender is actually asked to leave its
    # rail and slide across the field with the play.  The 0.6 sweep
    # arithmetic pinned above is the LOOSE-BALL contract only.
    ax_m, ay_m = compute_fullback_anchor(FakeBall(35.0, 50.0), defensive_line_x=70.0)
    check(
        "open play mirrors the carrier lane at full weight (ball.y=50 -> 50)",
        abs(ay_m - 50.0) < 0.01,
        f"anchor_y={ay_m:.2f}",
    )
    ax_m, ay_m = compute_fullback_anchor(FakeBall(35.0, 20.0), defensive_line_x=70.0)
    check(
        "open play mirrors the carrier lane at full weight (ball.y=20 -> 20)",
        abs(ay_m - 20.0) < 0.01,
        f"anchor_y={ay_m:.2f}",
    )
    ax_m, ay_m = compute_fullback_anchor(FakeBall(35.0, 60.0), defensive_line_x=70.0)
    check(
        "open-play mirror reaches the wide lane (ball.y=60 -> 60), not the 0.6 rail",
        abs(ay_m - 60.0) < 0.01,
        f"anchor_y={ay_m:.2f}",
    )
    check(
        "open-play mirroring is FULL weight (not the loose-ball 0.6 sweep)",
        abs(FB_LATERAL_TRACK_OPEN_PLAY - 1.0) < 1e-9,
        f"weight={FB_LATERAL_TRACK_OPEN_PLAY}",
    )
    ax, ay = compute_fullback_anchor(FakeBall(35.0, 35.0), defensive_line_x=70.0)
    check(
        "central ball → central anchor",
        abs(ay - FIELD_CENTRE_Y) < 0.01,
        f"anchor_y={ay:.2f}",
    )

    # ── 3. Kickoff override ────────────────────────────────────────
    # CORRECTED SPEC (2026-08-11): the kickoff fullback occupies the SAME
    # flat formation line (halfway + 2.0) offset 3 m toward the kick side
    # — NO extra depth offset.
    print("\n[3] Kickoff override (formation line + side offset)")
    ax, ay = compute_fullback_anchor(
        FakeBall(10.0, 10.0),
        defensive_line_x=60.0,
        in_kickoff=True,
        formation_line_x=62.0,
        side_sign=1,
    )
    check(
        "kickoff side_sign=+1 → (62.0, 38.0)",
        abs(ax - 62.0) < 0.01 and abs(ay - (FIELD_CENTRE_Y + 3.0)) < 0.01,
        f"anchor=({ax:.2f},{ay:.2f})",
    )
    ax, ay = compute_fullback_anchor(
        FakeBall(10.0, 10.0),
        defensive_line_x=60.0,
        in_kickoff=True,
        formation_line_x=62.0,
        side_sign=-1,
    )
    check(
        "kickoff side_sign=-1 → (62.0, 32.0)",
        abs(ax - 62.0) < 0.01 and abs(ay - (FIELD_CENTRE_Y - 3.0)) < 0.01,
        f"anchor=({ax:.2f},{ay:.2f})",
    )
    # Backward compatible: no formation_line_x → falls back to the
    # caller's defensive_line_x.
    ax, ay = compute_fullback_anchor(
        FakeBall(10.0, 20.0),
        defensive_line_x=62.0,
        in_kickoff=True,
        side_sign=1,
    )
    check(
        "kickoff without formation_line_x uses defensive_line_x",
        abs(ax - 62.0) < 0.01 and abs(ay - (FIELD_CENTRE_Y + 3.0)) < 0.01,
        f"anchor=({ax:.2f},{ay:.2f})",
    )

    # ── 4. Actor selection + reassignment ──────────────────────────
    # CORRECTED SPEC (root cause #2): "deepest" = FURTHEST +X (defence
    # retreats RIGHT).  Anchor for ball(35,35) at line_x=70 is ~86.5 —
    # a deep defender must be within MAX_ANCHOR_DISTANCE_M (25 m) of it.
    print("\n[4] Dynamic actor selection / reassignment")
    players = make_players(5)
    players[2].x = 85.0  # deepest backfield (furthest +X, ~1.5 m from anchor)
    actor, (tx, ty) = assign_fullback_role(players, FakeBall(35.0, 35.0), 70.0)
    check("deepest defender selected", actor is players[2],
          f"actor id={actor.player_id if actor else None}")

    # Sticky: second frame, same actor stays (still available).
    actor2, _ = assign_fullback_role(players, FakeBall(35.0, 35.0), 70.0)
    check("actor sticky across frames", actor2 is players[2],
          f"actor2 id={actor2.player_id if actor2 else None}")

    # Reassignment: actor becomes unavailable → next best picks up.
    players[2].frozen = True
    actor3, _ = assign_fullback_role(players, FakeBall(35.0, 35.0), 70.0)
    check(
        "reassigns when actor frozen",
        actor3 is not None and actor3 is not players[2],
        f"new actor id={actor3.player_id if actor3 else None}",
    )
    check(
        "old actor flag cleared",
        players[2].fullback_role_active is False,
        f"flag={players[2].fullback_role_active}",
    )
    players[2].frozen = False

    # Marker / tackler exclusion
    players[0].defender_role = DefenderRole.MARKER_A
    players[1].defender_role = DefenderRole.TACKLER_1
    actor4, _ = assign_fullback_role(players, FakeBall(35.0, 35.0), 70.0)
    check(
        "marker/tackler never selected",
        actor4 not in (players[0], players[1]),
        f"actor id={actor4.player_id if actor4 else None}",
    )
    players[0].defender_role = DefenderRole.DEF_LINE_READY
    players[1].defender_role = DefenderRole.DEF_LINE_READY

    # Too-far-from-anchor leads to reassignment.
    players[2].x = 5.0  # ~49 m from anchor — beyond MAX_ANCHOR_DISTANCE_M
    actor5, _ = assign_fullback_role(players, FakeBall(35.0, 35.0), 70.0)
    check(
        "actor reassigned when too far from anchor",
        actor5 is not players[2],
        f"actor id={actor5.player_id if actor5 else None}",
    )
    players[2].x = 40.0

    # At most ONE active fullback per frame.
    assign_fullback_role(players, FakeBall(35.0, 35.0), 70.0)
    n_active = sum(1 for p in players if getattr(p, "fullback_role_active", False))
    check("exactly one active fullback", n_active == 1, f"count={n_active}")

    # ── 5. Line-number integrity (depth roles + classification) ────
    print("\n[5] Line-number integrity")
    players = make_players(6)
    assign_fullback_role(players, FakeBall(35.0, 35.0), 70.0)

    class FullbackWorld:
        """World stub providing the ruck geometry assign_depth_roles needs."""
        ruck_x_m = 60.0
        ruck_y_m = 35.0

    assign_depth_roles(players, FullbackWorld())
    fb_actor = next(p for p in players if getattr(p, "fullback_role_active", False))
    check(
        "fullback excluded from depth-role lanes",
        getattr(fb_actor, "depth_role", None) is None,
        f"depth_role={getattr(fb_actor, 'depth_role', None)}",
    )
    line_roles = [getattr(p, "depth_role", None) for p in players if p is not fb_actor]
    check(
        "remaining 5 defenders all get line roles",
        all(r is not None for r in line_roles),
        str(line_roles),
    )

    # ── 6. Stabiliser / movement-state gate ────────────────────────
    print("\n[6] Stabiliser movement-state gate")
    check(
        "DRIFT is stabilised (not in NON_STABILISED_MOVEMENT_STATES)",
        MovementState.DRIFT.name not in NON_STABILISED_MOVEMENT_STATES,
        str(NON_STABILISED_MOVEMENT_STATES),
    )
    check(
        "CHASE is NOT stabilised (guard intact)",
        MovementState.CHASE.name in NON_STABILISED_MOVEMENT_STATES,
        str(NON_STABILISED_MOVEMENT_STATES),
    )

    # ── 7. Anchor stays in bounds ──────────────────────────────────
    # The engine field runs x ∈ [10, 110].  The anchor clamps to it even
    # when the +depth push exceeds the defending tryline (ruck 110 →
    # line_x 120 → depth 30 → raw 150 → clamped to 110).
    print("\n[7] Anchor bounds")
    # BOUND SINGLE-SOURCE (2026-09-16): the anchor clamps to the module's OWN
    # documented strip -- FIELD_MAX_X is 112, not the 110 engine tryline,
    # because the last defender is explicitly allowed to sit a little
    # into-goal beyond the right tryline (see fullback_role._clamp_anchor).
    # The literal 110 this check previously asserted predated that in-goal
    # allowance, so the deep sample that lands exactly on the clamp (the
    # ruck-at-the-tryline case below) failed against the module's own
    # contract.  The assertion now reads the SAME constant the clamp uses.
    ax, ay = compute_fullback_anchor(FakeBall(110.0, 70.0), defensive_line_x=120.0)
    check(
        f"anchor within field x bounds ({LEFT_TRYLINE_X:.0f}..{FIELD_MAX_X:.0f})",
        LEFT_TRYLINE_X <= ax <= FIELD_MAX_X,
        f"ax={ax:.2f}",
    )
    check("anchor within field y bounds (0..70)", 0.0 <= ay <= 70.0, f"ay={ay:.2f}")
    ax, ay = compute_fullback_anchor(FakeBall(10.0, 0.0), defensive_line_x=20.0)
    check("goal-line anchor within x bounds",
          LEFT_TRYLINE_X <= ax <= FIELD_MAX_X, f"ax={ax:.2f}")
    check("goal-line anchor within y bounds", 0.0 <= ay <= 70.0, f"ay={ay:.2f}")

    # ── 8. Controlled emergency-defender path (B2b, 2026-09-16) ─────
    # TACKLE ELIGIBILITY and LINE MEMBERSHIP are SEPARATE concepts.  The
    # tagged fullback may enter lane candidacy / pre-tackle commit / TES
    # arming when -- and ONLY when -- a GENUINE LINE BREAK exists, and it
    # must stay excluded from the line population / depth roles throughout.
    print("\n[8] Controlled emergency-defender path (B2b)")

    for fn in (
        "is_fullback_line_member",
        "fullback_emergency_defender_active",
        "fullback_tackle_authority_active",
        "fullback_approach_cap_applies",
        "release_stale_fullback_lane_stamp",
        "is_fullback_emergency_engaged",
        "set_fullback_emergency_engaged",
        "reset_fullback_emergency_engagement",
        "line_break_active",
    ):
        check(f"defines {fn}()", callable(getattr(fb_mod, fn, None)))

    check("candidate lever is on by default", FULLBACK_TACKLE_CANDIDACY is True)
    check(
        "the sweeper cap is the documented constant",
        FB_MAX_APPROACH_SPEED_MPS == 2.0,
        str(FB_MAX_APPROACH_SPEED_MPS),
    )

    class EmergencyWorld:
        """Minimal world for the emergency gate: a locked line anchor."""

        def __init__(self, anchor_x=70.0):
            self.ruck_x_m = anchor_x - 10.0
            self.ruck_y_m = 35.0
            self.defensive_line_anchor = anchor_x
            self.anchor_locked = True
            self.frame_count = 1
            self.ball_carrier = None
            self.defending_team = []
            self.players = []
            self.tes_active = False

    eworld = EmergencyWorld(70.0)
    # The sweeper built by make_players() sits at y = 20.0 (its lane).  The
    # lateral half of the break gate measures the carrier against THAT lane
    # (see fullback_role.classify_fullback_break), so the "no break" fixtures
    # must place the carrier on the sweeper's own lane -- a carrier 10+ m
    # across the field from the last defender IS a lateral break by design.
    bc_break = Player(76.0, 35.0, 901)    # past anchor + the 1.0 m margin
    bc_behind = Player(64.0, 20.0, 902)   # behind the line, ON the FB lane
    # 2026-09-27 (LINE-INTACT EXCLUSION): the LATERAL branch admits a carrier
    # only once it has DRAWN LEVEL with the line -- within
    # ``FB_LATERAL_BREAK_LEVEL_M`` (3.0 m) below the line anchor.  These two
    # fixtures therefore sit 2 m short of the anchor (68.0 against 70.0)
    # instead of 6 m behind it: at 64.0 the EXCLUSION, not the channel or the
    # reach test, is what returns NONE, so the fixtures would silently have
    # stopped exercising the lateral branch at all.  The LANE offsets are
    # unchanged, so each still probes exactly what its check claims:
    #   - bc_wide is 10 m off the lane and 10.2 m away -> in reach, LATERAL
    #   - bc_far is 42 m off the lane and 42.0 m away  -> outside the reach
    bc_wide = Player(68.0, 30.0, 903)     # level with the line, 10 m off lane
    bc_far = Player(68.0, 62.0, 904)      # 42 m off the lane: out of reach

    check("line_break_active True past the anchor",
          line_break_active(eworld, bc_break) is True)
    check("line_break_active False behind the anchor",
          line_break_active(eworld, bc_behind) is False)

    sweeper = make_players(1)[0]
    sweeper.fullback_role_active = True
    sweeper.can_acquire_tackle = True
    eworld.ball_carrier = bc_break
    check("emergency gate opens on a genuine break",
          fullback_emergency_defender_active(eworld, sweeper) is True)
    eworld.ball_carrier = bc_behind
    check("emergency gate stays shut off a break",
          fullback_emergency_defender_active(eworld, sweeper) is False)

    # ── LATERAL BRANCH OF THE 2D GATE (2026-09-22) ──
    # A carrier outside the sweeper's LATERAL channel while still level with
    # / behind the line in X is a genuine break too.  Both CHASE and tackle
    # candidacy read this same classify_fullback_break, so the gate must
    # open here exactly as it does for the depth break.
    eworld.ball_carrier = bc_wide
    check("emergency gate opens on a LATERAL channel breach",
          fullback_emergency_defender_active(eworld, sweeper) is True)
    eworld.ball_carrier = bc_far
    check("emergency gate stays shut for a wide carrier OUT OF 2D REACH",
          fullback_emergency_defender_active(eworld, sweeper) is False)

    no_authority = make_players(1)[0]
    no_authority.fullback_role_active = True
    no_authority.can_acquire_tackle = False
    eworld.ball_carrier = bc_break
    check("emergency gate stays shut without can_acquire_tackle",
          fullback_emergency_defender_active(eworld, no_authority) is False)

    untagged = make_players(1)[0]
    untagged.can_acquire_tackle = True
    check("emergency gate stays shut for an untagged defender",
          fullback_emergency_defender_active(eworld, untagged) is False)

    # TACKLE AUTHORITY: the sweeper contract is suspended only while the
    # actor is genuinely playing as a defender.
    armed = make_players(1)[0]
    armed.fullback_role_active = True
    armed.tackle_role = "PRIMARY"
    check("a PRIMARY lane role grants authority",
          fullback_tackle_authority_active(armed) is True)
    check("a PRIMARY lane role lifts the 2.0 m/s sweeper cap",
          fullback_approach_cap_applies(armed) is False)
    armed.tackle_role = None
    check("a clean sweeper has no authority",
          fullback_tackle_authority_active(armed) is False)
    check("a clean sweeper keeps the 2.0 m/s sweeper cap",
          fullback_approach_cap_applies(armed) is True)

    # THE TAG IS NEVER LOST: arming does not revoke it.
    eworld.ball_carrier = bc_break
    set_fullback_emergency_engaged(armed)
    check("arming keeps the fullback tag", armed.fullback_role_active is True)
    check("arming sets the engagement marker",
          is_fullback_emergency_engaged(armed) is True)
    check("an armed fullback is still not a line member",
          is_fullback_line_member(armed) is False)
    check("reset reports that a body was reset",
          reset_fullback_emergency_engagement(armed) is True)
    check("reset KEEPS the fullback tag", armed.fullback_role_active is True)
    check("reset drops the engagement marker",
          is_fullback_emergency_engaged(armed) is False)
    check("reset restores DEF_LINE_READY",
          armed.defender_role is DefenderRole.DEF_LINE_READY)

    # MEMBERSHIP SEPARATION under granted authority: an armed fullback
    # wearing TACKLER_1 with a PRIMARY lane role must STILL be excluded
    # from the depth-role lanes.
    armed.defender_role = DefenderRole.TACKLER_1
    armed.tackle_role = "PRIMARY"
    armed.depth_role = None
    armed.depth_side = None
    mates = make_players(5)
    for i, mate in enumerate(mates):
        mate.y = 20.0 + i * 8.0
    assign_depth_roles([armed] + mates, FullbackWorld())
    check("an armed fullback still gets no depth role",
          getattr(armed, "depth_role", None) is None,
          f"depth_role={getattr(armed, 'depth_role', None)}")
    check("an armed fullback still gets no depth side",
          getattr(armed, "depth_side", None) is None,
          f"depth_side={getattr(armed, 'depth_side', None)}")
    check("armed fullback is still excluded from the line population",
          is_fullback_line_member(armed) is False)

    # STALE-STAMP RELEASE: a break that decays WITHOUT a tackle must not
    # strand a PRIMARY stamp (that would suspend the sweeper contract for
    # the rest of the match and the actor would never glide home again).
    stale = make_players(1)[0]
    stale.fullback_role_active = True
    stale.can_acquire_tackle = True
    stale.tackle_role = "PRIMARY"
    eworld.ball_carrier = bc_behind
    check("a stale lane stamp is released",
          release_stale_fullback_lane_stamp(eworld, stale) is True)
    check("the stale lane stamp is cleared", stale.tackle_role is None)
    check("the sweeper contract returns with the release",
          fullback_approach_cap_applies(stale) is True)
    eworld.ball_carrier = bc_break
    stale.tackle_role = "PRIMARY"
    check("a live lane stamp is NOT released",
          release_stale_fullback_lane_stamp(eworld, stale) is False)
    check("the live lane stamp survives", stale.tackle_role == "PRIMARY")

    # A LATERAL channel breach is a live break too, so its stamp must
    # survive; once the carrier is back on the sweeper's lane the stamp is
    # stale again and must be released.
    eworld.ball_carrier = bc_wide
    check("a LATERAL-breach lane stamp is NOT released",
          release_stale_fullback_lane_stamp(eworld, stale) is False)
    check("the lateral-breach stamp survives", stale.tackle_role == "PRIMARY")
    eworld.ball_carrier = bc_behind
    check("the stamp is released once the carrier is back on the lane",
          release_stale_fullback_lane_stamp(eworld, stale) is True)
    check("the released stamp is cleared", stale.tackle_role is None)

    print("\n" + "=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
