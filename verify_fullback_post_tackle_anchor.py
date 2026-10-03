"""Verify the fullback returns to its ruck-relative anchor during PTB phases."""

from types import SimpleNamespace

from player.movement_states import MovementState
from player.player import Player
from game.defence.defence_lvl_1.contact_authority import (
    ContactOutcome,
    evaluate_contact,
)
from game.defence.defence_lvl_1.fullback_role import (
    LATERAL_TRACK,
    FIELD_CENTRE_Y,
    apply_fullback_geometry,
    compute_fullback_anchor,
    compute_fullback_ruck_anchor,
    arm_fullback_post_tackle_return,
    clear_fullback_emergency_engaged,
    fullback_retreat_10_compliant,
    set_fullback_emergency_engaged,
)
from game.managers.defence_manager import DefenceManager


class _BallCarrier:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _World:
    def __init__(self, ball_carrier):
        self.ball_carrier = ball_carrier
        self.ruck_x_m = 40.0
        self.ruck_y_m = 47.0
        self.defensive_line_anchor = self.ruck_x_m + 10.0
        self.defending_team = []
        self.frame_count = 1
        self.fullback_recover_allowed = False
        self.tes_active = False
        self.in_kickoff = False
        self.attacking_direction = 1


def main():
    carrier = _BallCarrier(x=45.0, y=62.0)
    world = _World(carrier)
    fb = Player(x=58.0, y=32.0, player_id=200)
    fb.fullback_role_active = True
    fb.depth_role = None
    fb.depth_side = None
    fb.movement_state = MovementState.DRIFT
    fb.tes_attached = False
    fb.in_contact = False
    fb._tackle_commit_active = False
    fb._world_ref = world
    world.defending_team = [fb]

    carrier_anchor = compute_fullback_anchor(
        carrier, world.defensive_line_anchor
    )
    post_tackle_anchor = compute_fullback_ruck_anchor(
        world, world.defensive_line_anchor
    )
    expected_x = world.ruck_x_m + 10.0 + 16.0
    expected_y = FIELD_CENTRE_Y + (
        world.ruck_y_m - FIELD_CENTRE_Y
    ) * LATERAL_TRACK
    assert abs(post_tackle_anchor[0] - expected_x) < 1e-9
    assert abs(post_tackle_anchor[1] - expected_y) < 1e-9
    assert abs(carrier_anchor[0] - post_tackle_anchor[0]) > 0.5
    assert abs(carrier_anchor[1] - post_tackle_anchor[1]) > 0.5

    set_fullback_emergency_engaged(fb)
    assert arm_fullback_post_tackle_return(fb)
    clear_fullback_emergency_engaged(fb)
    world.fullback_recover_allowed = True
    world.tes_active = True
    apply_fullback_geometry(world, fb)
    assert abs(fb.target_x - expected_x) < 1e-9, (
        f"post-tackle return X target {fb.target_x}, expected {expected_x}"
    )
    assert abs(fb.target_y - expected_y) < 1e-9, (
        f"post-tackle return Y target {fb.target_y}, expected {expected_y}"
    )
    assert fb.frozen is False
    assert fb.target_source == "fullback_geometry"
    assert fb.movement_state == MovementState.RECOVER, (
        "completed fullback must enter recovery immediately at drive end"
    )
    assert fullback_retreat_10_compliant(world, fb), (
        "fullback at its depth anchor has cleared the 10 m line"
    )

    # Exercise the RELEASE/PTB-only integration path with a stale freeze,
    # which the previous return branch preserved indefinitely.
    fb.x = world.ruck_x_m + 1.0
    fb.y = world.ruck_y_m
    fb.frozen = True
    fb.vx = 0.0
    fb.vy = 0.0
    world.tes_active = False
    world.fullback_recover_allowed = False
    manager = object.__new__(DefenceManager)
    ope = type("OPE", (), {"world": world})()
    manager.reassert_fullback(ope, 1.0 / 60.0)
    assert not fb.frozen, "live post-tackle return must clear stale freeze"
    assert fb.movement_state == MovementState.RECOVER, (
        "live post-tackle return must restore RECOVER locomotion"
    )
    assert fb.x > world.ruck_x_m + 1.0, (
        "offside fullback must keep moving back toward its depth anchor"
    )
    assert not fullback_retreat_10_compliant(world, fb), (
        "a small recovery step must not prematurely satisfy the 10 m retreat"
    )

    fb.x = world.ruck_x_m + 10.0
    assert fullback_retreat_10_compliant(world, fb), (
        "fullback becomes tackle-eligible at the 10 m line"
    )
    contact_bc = Player(x=world.ruck_x_m + 9.0, y=world.ruck_y_m, player_id=202)
    fb.x = world.ruck_x_m + 9.0
    fb.y = world.ruck_y_m
    fb.can_acquire_tackle = True
    fb.tackle_role = "PRIMARY"
    world.ball_carrier = contact_bc
    world.players = [fb, contact_bc]
    world.attacking_team = [contact_bc]
    world.open_play_engine = SimpleNamespace(
        TACKLE_RADIUS_M=2.0,
        _phase="OPEN_PLAY",
        _current_tackle_seq=0,
        _check_pre_tackle=lambda *_: True,
    )
    world.frame_count += 1
    offside_decision = evaluate_contact(
        world, world.open_play_engine, [fb], contact_bc,
    )
    assert offside_decision.outcome is ContactOutcome.CONTACT_FAILED, (
        "offside fullback must not initiate contact even with PRIMARY"
    )

    fb.x = world.ruck_x_m + 10.0
    contact_bc.x = fb.x
    world.frame_count += 1
    onside_decision = evaluate_contact(
        world, world.open_play_engine, [fb], contact_bc,
    )
    assert onside_decision.outcome is ContactOutcome.CONTACT_FORMED, (
        "fullback tackle admission resumes after clearing 10 m"
    )

    world.ball_carrier = carrier
    world.frame_count += 1
    apply_fullback_geometry(world, fb)
    assert abs(fb.target_x - expected_x) < 1e-9, (
        "post-tackle return must persist after the phase returns to open play"
    )
    assert abs(fb.target_y - expected_y) < 1e-9, (
        "post-tackle return Y must remain ruck-relative in open play"
    )

    unassigned = Player(x=0.0, y=0.0, player_id=201)
    unassigned.fullback_role_active = False
    set_fullback_emergency_engaged(unassigned)
    assert not arm_fullback_post_tackle_return(unassigned), (
        "only the tagged fullback may be assigned a post-tackle return"
    )

    carrier.x = world.ruck_x_m
    carrier.y = world.ruck_y_m
    fb.x = world.ruck_x_m + 1.0
    fb.y = world.ruck_y_m
    fb.vx = 0.0
    fb.vy = 0.0
    start_error = ((fb.x - expected_x) ** 2 + (fb.y - expected_y) ** 2) ** 0.5
    start_y_error = abs(fb.y - expected_y)
    for _ in range(90):
        world.frame_count += 1
        apply_fullback_geometry(world, fb)
        fb.update(1.0 / 60.0)
    end_error = ((fb.x - expected_x) ** 2 + (fb.y - expected_y) ** 2) ** 0.5
    end_y_error = abs(fb.y - expected_y)
    assert end_error < start_error, (
        f"fullback must physically return toward depth ({start_error:.2f}m "
        f"to {end_error:.2f}m)"
    )
    assert start_y_error - end_y_error > 1.0, (
        f"fullback must return laterally while close to the PTB "
        f"({start_y_error:.2f}m to {end_y_error:.2f}m remaining)"
    )

    carrier.x = 80.0
    world.frame_count += 1
    apply_fullback_geometry(world, fb)
    assert fb.movement_state == MovementState.CHASE, (
        "a live line break must override the post-tackle return target"
    )

    with open("game/managers/defence_manager.py", encoding="utf-8") as source:
        assert "post_tackle_ruck_anchor=True" in source.read(), (
            "PTB-era re-assert must opt into the ruck-relative anchor"
        )
    with open("world/open_play_engine.py", encoding="utf-8") as source:
        engine_source = source.read()
        assert "_fb_arm_post_tackle_return(_p)" in engine_source, (
            "completed fullback tackles must arm the return-to-depth target"
        )
        release_prep = engine_source[
            engine_source.index("def _prepare_release(self)"):
            engine_source.index("def _update_release(self,")
        ]
        assert (
            "_fb.tes_attached = False" in release_prep
            and "_fb.in_contact = False" in release_prep
            and "_fb.depth_locked = False" in release_prep
            and "_fb.depth_lock_y = None" in release_prep
            and "self._attached_player_slots.pop(_pid, None)" in release_prep
            and "_fb.update(self.world.dt)" in release_prep
        ), (
            "drive completion must clear tackle attachment and positional "
            "locks before integrating the fullback return"
        )
    with open("world/open_play_engine.py", encoding="utf-8") as source:
        engine_source = source.read()
    ptb_shutdown = engine_source.index(
        'if self._phase in ("PTB_PREP", "PTB_EXEC", "PTB"):'
    )
    ptb_shutdown_end = engine_source.index(
        "self._apply_defence_sdm(dt)", ptb_shutdown
    )
    ptb_block = engine_source[ptb_shutdown:ptb_shutdown_end]
    assert (
        'if getattr(d, "fullback_role_active", False):\n'
        "                    continue"
    ) in ptb_block, (
        "PTB generic movement shutdown must preserve the re-asserted fullback"
    )

    print("PASS: completed fullback tackles retain the ruck-relative X/Y target")
    print("      fullback makes measurable two-axis recovery from the PTB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
