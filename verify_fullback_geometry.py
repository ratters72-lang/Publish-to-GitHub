"""
verify_fullback_geometry.py — MODULE 1 verifier (2026-08-13).

Proves the fullback geometry authority:
  1. After ANY line writer clobbers the fullback target (spacing Y /
     width X / retreat X / structured-close / MMDL), applying
     ``apply_fullback_geometry`` restores the authoritative deep anchor.
  2. Line defenders are untouched (self-gating on fullback_role_active).
  3. TES-attached / in-contact actors are never unpinned.
  4. Tackler-commit actors yield to the collision target.
  5. The actor is never frozen and it carries a movement STATE the shared
     movement-state table can price — the sweeper's PACE is the shared
     table's, never the writer's (2026-09-27).  2026-09-22: the break /
     step-out gate is 2D,
     so the authority legitimately selects TWO states here and both are
     pinned — DRIFT on a non-breaking carrier (the sweeper glide, carrier on
     the sweeper's own lane, inside FB_LATERAL_BREAK_M) and CHASE on a genuine
     LATERAL channel breach (carrier level with the line in X but outside the
     sweeper's lane, inside FB_LATERAL_BREAK_RADIUS_M).

  6. LINE-INTACT EXCLUSION (2026-09-27).  The lateral branch additionally
     requires the carrier to have DRAWN LEVEL with the line — within
     ``FB_LATERAL_BREAK_LEVEL_M`` BELOW the line anchor — so the last defender
     is never committed with the line still intact in front of it.  Both sides
     are pinned: the exclusion with ``RLM_FB_LATERAL_BREAK_LEVEL`` ON (the
     shipped default) and the historical channel-breach classification with
     the lever OFF.
  7. Both engine chokepoints (_update_defence + _update_defence_during_
     tes_drive) invoke the authority AFTER def_behaviour.apply() and
     BEFORE the movement-integration loop.
"""
import math
import sys

from player.player import Player
from player.movement_states import MovementState, MAX_SPEED_BY_STATE
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    assign_fullback_role,
    classify_fullback_break,
    compute_fullback_anchor,
    FB_BREAK_LATERAL,
    FB_BREAK_NONE,
    FB_LATERAL_BREAK_M,
    FB_MAX_APPROACH_SPEED_MPS,
)
from game.defence.defence_lvl_1.anchor_authority import ANCHOR_DEPTH_M


class _Ball:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _World:
    """Minimal world stub carrying the fields apply_fullback_geometry reads."""

    def __init__(self, anchor, ruck_x, bc):
        self.defensive_line_anchor = anchor
        self.ruck_x_m = ruck_x
        self.ball_carrier = bc


def _make_line():
    """Five defenders; jersey 1 will be the fullback actor."""
    players = []
    for i in range(5):
        p = Player(x=55.0 + i * 3.0, y=35.0 - 10.0 + i * 5.0, player_id=i + 1)
        p.jersey_number = i + 1
        p.max_speed_mps = 9.2
        p.max_defensive_speed_mps = 8.3
        players.append(p)
    return players


def _reset(players, target_x, target_y, source):
    """Simulate every line writer clobbering targets (spacing/width/retreat/close/MMDL)."""
    for p in players:
        p.target_x = target_x
        p.target_y = target_y
        p.target_source = source
        p.frozen = False


def _line_writer_clobber_check(world, defenders, fb, bc, anchor):
    """Core assertion: last-writer beats the clobber, line untouched."""
    # Simulate line writers pinning EVERYONE (including FB) to the line anchor.
    _reset(defenders, anchor, 35.0, "structured_open_play")

    for d in defenders:
        apply_fullback_geometry(world, d)

    expected_x, expected_y = compute_fullback_anchor(bc, anchor)

    for d in defenders:
        if d is fb:
            assert d.target_source == "fullback_geometry", (
                f"FB target_source = {d.target_source!r}, expected fullback_geometry"
            )
            assert abs(d.target_x - expected_x) < 1e-6, (
                f"FB target_x = {d.target_x:.4f}, expected {expected_x:.4f} "
                f"(previously clobbered to {anchor:.4f} = line anchor)"
            )
            assert abs(d.target_y - expected_y) < 1e-6, (
                f"FB target_y = {d.target_y:.4f}, expected {expected_y:.4f} "
                f"(previously clobbered to 35.0)"
            )
            # 2026-09-22: the break / step-out gate is 2D.  2026-09-27: it is
            # also LINE-INTACT-AWARE — the lateral branch admits a carrier
            # only once it has DRAWN LEVEL with the line (within
            # ``FB_LATERAL_BREAK_LEVEL_M`` below the line anchor), so the
            # sweeper is never committed with the line still covering in
            # front of it.  THIS fixture — carrier 2 m SHORT of the anchor,
            # 20 m outside the sweeper's own lane, ~23.9 m away — satisfies
            # BOTH halves and is therefore a genuine LATERAL channel breach,
            # so the authority routes the actor to the carrier-lane chase.
            # The kind is asserted explicitly so the fixture cannot silently
            # drift out from under the state pin.
            kind = classify_fullback_break(bc, d, anchor, expected_y)
            assert kind == FB_BREAK_LATERAL, (
                f"fixture drift: carrier at ({bc.x:.1f}, {bc.y:.1f}) against "
                f"FB lane y={d.y:.1f} classifies {kind}, expected LATERAL"
            )
            assert d.movement_state == MovementState.CHASE, (
                f"FB movement_state = {d.movement_state}, expected CHASE on a "
                f"{kind} break"
            )
            assert d.frozen is False, "FB must never be left frozen"
            # ── CHASE PACE, CORRECTED 2026-09-26 ──
            # This pin used to demand
            # ``move_speed <= FB_MAX_APPROACH_SPEED_MPS`` (the 2.0 m/s sweeper
            # anchor cap).  That was the SAME defect the depth break had, one
            # branch over: the CHASE branch computed its speed through
            # ``fullback_approach_speed``, whose FIRST LINE is
            # ``min(base, 2.0)``, because ``fullback_approach_cap_applies`` is
            # ALWAYS true at that call site -- an authority-active body is
            # diverted into the emergency yield and returns before the anchor
            # branch is ever reached, so the ``else: base_speed`` arm was DEAD
            # CODE.  A chaser pinned at its own anchor pace cannot contest the
            # break it was routed to.
            #
            # The invariant this check EXISTS for is unchanged and is asserted
            # above: the fullback OWNS its own command (target_source,
            # target_x, target_y) against every line writer's clobber.  What
            # follows is only that the owner is left a live, genuinely
            # governed speed -- never 0 (the original "structural
            # immobility" defect), never above the defensive ceiling, and no
            # longer pinned to the anchor cap.  ``RLM_FB_CHASE_SPEED=0``
            # restores the historical expression verbatim.
            # ── THE PACE IS THE SHARED TABLE'S, NOT THE WRITER'S (2026-09-27) ──
            # This writer no longer writes ``move_speed`` AT ALL.  The actor
            # integrates through the shared movement-state model
            # (player/movement_states.apply_fullback_locomotion), so its pace
            # is MAX_SPEED_BY_STATE[state] x max_speed_mps -- the SAME table
            # every line defender's pace comes from, bounded by the same
            # post-facing ``max_defensive_speed_mps`` clamp in Player.update.
            # What the authority owes the actor is therefore a movement STATE
            # the shared table can price, so that is what is asserted here: the
            # state is ON the table, and the pace it implies is live (never the
            # original "structural immobility" zero) and inside the ceiling.
            _scale = MAX_SPEED_BY_STATE.get(d.movement_state)
            assert _scale is not None, (
                f"FB movement_state = {d.movement_state} is not on "
                f"MAX_SPEED_BY_STATE, so the shared model cannot price it"
            )
            _implied_pace = d.max_speed_mps * _scale
            assert 0.0 < _implied_pace <= d.max_defensive_speed_mps + 1e-9, (
                f"implied FB pace = {_implied_pace:.4f} "
                f"(max_speed_mps {d.max_speed_mps} x {_scale}), expected "
                f"within (0, {d.max_defensive_speed_mps}]"
            )
            assert _implied_pace > FB_MAX_APPROACH_SPEED_MPS, (
                f"implied FB pace = {_implied_pace:.4f}: a live-break chase "
                f"must not be pinned to the {FB_MAX_APPROACH_SPEED_MPS} m/s "
                f"anchor cap"
            )
        else:
            # Line defenders keep the clobbered value — authority is gated.
            assert abs(d.target_x - anchor) < 1e-9, (
                f"line defender #{d.player_id} target_x mutated to {d.target_x}"
            )
            assert abs(d.target_y - 35.0) < 1e-9, (
                f"line defender #{d.player_id} target_y mutated to {d.target_y}"
            )
            assert d.target_source == "structured_open_play", (
                f"line defender #{d.player_id} target_source mutated to {d.target_source}"
            )


def _sweep_drift_check(world, defenders, fb, anchor):
    """A NON-breaking carrier keeps the sweeper glide (DRIFT).

    2026-09-22: with the 2D break gate, a carrier on the sweeper's OWN lane
    (inside ``FB_LATERAL_BREAK_M``) and level with the line in X is not a
    break, so the authority must still mirror that lane and command DRIFT —
    the pre-2026-09-22 behaviour is preserved for the geometry case it was
    written for.
    """
    bc = _Ball(x=62.0, y=fb.y)
    world.ball_carrier = bc
    # Bump the frame so the writer's same-frame anchor memo cannot replay the
    # previous fixture's anchor (this stub world has no frame_count).
    world.frame_count = 2

    _reset(defenders, anchor, 35.0, "structured_open_play")
    for d in defenders:
        apply_fullback_geometry(world, d)

    expected_x, expected_y = compute_fullback_anchor(bc, anchor)
    kind = classify_fullback_break(bc, fb, anchor, expected_y)

    assert abs(bc.y - fb.y) <= FB_LATERAL_BREAK_M, (
        "fixture must sit inside the channel-breach band"
    )
    assert kind == FB_BREAK_NONE, (
        f"a carrier on the sweeper's own lane must not be a break, got {kind}"
    )
    assert abs(fb.target_x - expected_x) < 1e-6, (
        f"FB target_x = {fb.target_x:.4f}, expected {expected_x:.4f}"
    )
    assert abs(fb.target_y - expected_y) < 1e-6, (
        f"FB target_y = {fb.target_y:.4f}, expected {expected_y:.4f}"
    )
    assert fb.movement_state == MovementState.DRIFT, (
        f"FB movement_state = {fb.movement_state}, expected DRIFT on a "
        f"non-breaking carrier"
    )
    assert fb.frozen is False, "FB must never be left frozen"
    # ── THE SWEEPER'S PACE IS THE SHARED DRIFT ROW (2026-09-27) ──
    # The retired 2.0 m/s approach write no longer exists: the authority stamps
    # only the STATE (DRIFT here), and the shared movement-state model prices
    # it.  Assert the state is priceable and the implied pace is live.
    assert fb.movement_state in MAX_SPEED_BY_STATE, (
        f"FB movement_state = {fb.movement_state} is not priceable by the "
        f"shared movement-state table"
    )
    _drift_pace = fb.max_speed_mps * MAX_SPEED_BY_STATE[fb.movement_state]
    assert 0.0 < _drift_pace <= fb.max_speed_mps + 1e-9, (
        f"implied sweeper pace = {_drift_pace:.4f}, expected within "
        f"(0, {fb.max_speed_mps}]"
    )


def _line_intact_exclusion_check(world, defenders, fb, anchor):
    """FIX 3 (2026-09-27) — a carrier SHORT of the line cannot pull the last
    defender off it.

    The lateral branch of the break gate used to fire on a channel breach
    ALONE, with no reference to the X-state of the line: a sweeper was
    committed on a 20 m off-lane carrier that was still 8 m BEHIND an intact
    line, abandoning the post it exists to cover.  The gate now requires the
    carrier to have drawn level with the line (within
    ``FB_LATERAL_BREAK_LEVEL_M`` below the line anchor).

    Both sides are pinned — the exclusion with the lever ON (the shipped
    default) and the historical classification with the lever OFF — so the
    A/B is real and a future loosening of the band cannot pass silently.
    """
    import game.defence.defence_lvl_1.fullback_role as fbmod

    # 8 m behind the line, 20 m off the sweeper's lane, 21.2 m away: INSIDE
    # the 2D reach and far OUTSIDE the 5 m channel, so the level gate is the
    # only thing that can be suppressing it.  That makes this a clean probe of
    # the exclusion rather than a coincidental non-break.
    short_bc = _Ball(x=anchor - 8.0, y=45.0)
    world.ball_carrier = short_bc
    world.frame_count = 3

    assert fbmod.FB_LATERAL_BREAK_LEVEL_ENABLED is True, (
        "the line-intact exclusion must ship ENABLED"
    )
    assert abs(short_bc.x - anchor) > fbmod.FB_LATERAL_BREAK_LEVEL_M, (
        "fixture must sit OUTSIDE the level band for the exclusion to apply"
    )
    _reach = math.hypot(short_bc.x - fb.x, short_bc.y - fb.y)
    assert _reach < 25.0, (
        f"fixture must sit INSIDE the 2D reach ({_reach:.2f} m) so the reach "
        "guard is not what suppresses the break"
    )

    _reset(defenders, anchor, 35.0, "structured_open_play")
    for d in defenders:
        apply_fullback_geometry(world, d)

    expected_x, expected_y = compute_fullback_anchor(short_bc, anchor)
    kind = classify_fullback_break(short_bc, fb, anchor, expected_y)
    assert kind == FB_BREAK_NONE, (
        f"LINE-INTACT: a carrier {anchor - short_bc.x:.1f} m SHORT of the line "
        f"classified {kind}; the last defender must not be committed with the "
        "line still intact in front of it"
    )
    assert fb.movement_state == MovementState.DRIFT, (
        f"LINE-INTACT: the uncommitted sweeper must hold its glide, got "
        f"{fb.movement_state}"
    )
    # The post itself is unchanged: the sweeper still mirrors the carrier's
    # lane while the line covers in front of it.
    assert abs(fb.target_y - expected_y) < 1e-6, (
        f"LINE-INTACT: sweeper target_y = {fb.target_y:.4f}, expected the "
        f"mirrored lane {expected_y:.4f}"
    )

    # ── THE A/B: the SAME carrier is a lateral break on the old gate ──
    saved = fbmod.FB_LATERAL_BREAK_LEVEL_ENABLED
    fbmod.FB_LATERAL_BREAK_LEVEL_ENABLED = False
    try:
        rolled_back = classify_fullback_break(
            short_bc, fb, anchor, expected_y
        )
    finally:
        fbmod.FB_LATERAL_BREAK_LEVEL_ENABLED = saved
    assert rolled_back == FB_BREAK_LATERAL, (
        "the RLM_FB_LATERAL_BREAK_LEVEL=0 rollback must restore the historical "
        f"channel-breach classification, got {rolled_back}"
    )


def _tes_guard_check(world, fb, bc, anchor):
    fb.tes_attached = True
    fb.target_x = 10.0
    fb.target_y = 10.0
    fb.target_source = "tes_pin"
    apply_fullback_geometry(world, fb)
    assert fb.tes_attached and fb.target_x == 10.0 and fb.target_y == 10.0, (
        "TES-attached FB must never be unpinned"
    )
    fb.tes_attached = False
    fb.in_contact = True
    fb.target_x = 11.0
    fb.target_y = 11.0
    fb.target_source = "tes_pin"
    apply_fullback_geometry(world, fb)
    assert fb.in_contact and fb.target_x == 11.0 and fb.target_y == 11.0, (
        "in-contact FB must never be unpinned"
    )
    fb.in_contact = False


def _commit_guard_check(world, fb, bc, anchor):
    fb._tackle_commit_active = True
    fb.target_x = bc.x + 1.0
    fb.target_y = bc.y
    fb.target_source = "tackler_commit"
    apply_fullback_geometry(world, fb)
    assert fb.target_source == "tackler_commit", (
        "commit-armed FB must yield to the collision point"
    )
    assert abs(fb.target_x - (bc.x + 1.0)) < 1e-9 and abs(fb.target_y - bc.y) < 1e-9
    fb._tackle_commit_active = False


def _engine_wiring_check():
    src = open("world/open_play_engine.py", encoding="utf-8").read()
    # _update_defence wiring: after def_behaviour.apply, before p.update(dt).
    assert src.count("apply_fullback_geometry(self.world") >= 2, (
        "expected both engine chokepoints to call apply_fullback_geometry"
    )
    # Order sanity: in _update_defence, the authority block precedes the
    # movement-integration loop.
    apply_idx = src.index("apply_fullback_geometry(self.world")
    update_loop_idx = src.index("for p in defenders:\n            if p.frozen:")
    assert apply_idx < update_loop_idx, (
        "authority must run before the movement-integration loop in _update_defence"
    )
    # Order sanity: in _update_defence_during_tes_drive, the authority block
    # runs after the try/finally projection and before its own movement loop.
    drive_apply = src.rindex("apply_fullback_geometry(self.world")
    drive_loop = src.rindex("for p in defenders:\n            if p.frozen:")
    assert drive_apply < drive_loop, (
        "authority must run before the movement loop in TES-drive"
    )


def main():
    anchor = 70.0
    ruck_x = anchor - ANCHOR_DEPTH_M
    bc = _Ball(x=62.0, y=45.0)
    world = _World(anchor=anchor, ruck_x=ruck_x, bc=bc)

    players = _make_line()
    fb, (fb_ex, fb_ey) = assign_fullback_role(players, bc, anchor)
    assert fb is not None, "no fullback actor assigned"
    assert fb.jersey_number == 1, "jersey 1 must be the designated fullback"

    # 2026-09-27: the clobber fixture's carrier must have DRAWN LEVEL with the
    # line for the lateral branch to admit it (FB_LATERAL_BREAK_LEVEL_M), so
    # the CHASE-state pin below stays a genuine channel breach instead of a
    # carrier the line is still in front of.  The assignment carrier is left
    # alone so the fullback selection inputs are unchanged.
    clobber_bc = _Ball(x=anchor - 2.0, y=45.0)
    world.ball_carrier = clobber_bc
    _line_writer_clobber_check(world, players, fb, clobber_bc, anchor)
    _tes_guard_check(world, fb, clobber_bc, anchor)
    _commit_guard_check(world, fb, clobber_bc, anchor)
    _sweep_drift_check(world, players, fb, anchor)
    _line_intact_exclusion_check(world, players, fb, anchor)
    _engine_wiring_check()

    print("PASS: fullback geometry authority — last-writer wins, line untouched,")
    print("      TES/commit guards hold, both engine chokepoints wired,")
    print("      and the 2D break gate selects CHASE (lateral channel breach)")
    print("      vs DRIFT (carrier on the sweeper's own lane), both pinned,")
    print("      with the LINE-INTACT exclusion refusing to commit the sweeper")
    print("      on a carrier still short of the line (lever A/B pinned).")
    print(f"      FB target = ({fb.target_x:.2f}, {fb.target_y:.2f}) @ {fb.target_source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
