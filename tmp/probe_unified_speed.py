"""Verifies every defensive writer derives from the unified ceiling (5.0)."""
import sys
sys.path.insert(0, ".")

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_behaviour_unified import DefensiveBehaviourUnified
from game.defence.defence_lvl_1.defensive_line_governor import DefensiveLineGovernor
from game.defence.defence_lvl_1.defensive_geometry import DefensiveGeometry
from game.defence.defence_lvl_1.defensive_role_movement import apply_role_movement
from game.kickoff_engine import KickoffEngine
from world.world_state import GameSubState
from player.player import Player
from ball.ball import Ball


class StubWorld:
    """GameWorldState is an Enum in this engine, so writers get a stub."""
    def __init__(self):
        self.in_pursuit_mode = False
        self.in_kickoff = False
        self.ball_carrier = None
        self.defensive_line_anchor = 60.0
        self.first_tackle_not_made = False
        self.sub_state = GameSubState.OPEN_PLAY
        self.first_receiver = None
        self.kickoff_landing_y = 35.0
        self.ruck_x_m = 60.0
        self.ruck_y_m = 35.0
        self.def_line_width_m = 40.0
        self.open_play_engine = None
        self.kicking_team = []
        self.receiving_team = []
        self.defending_team = []
        self.current_target_writer = None
        self.use_structured_open_play = True
        self.defence_enabled = True
        self.players = []
        self.primary_catcher = None


class FakeField:
    width_m = 70.0
    halfway_m = 55.0
    centre_m = 35.0


def make_defender():
    d = Player(60.0, 35.0)
    d.max_speed_mps = 9.2
    return d


def main():
    tuners = DefenceTuners()
    assert tuners.max_defensive_speed_mps == 5.0
    assert tuners.post_catch_cap_mps == 5.0

    u = DefensiveBehaviourUnified(tuners)
    d = make_defender()
    assert u._def_speed(d) == 5.0

    # pursuit
    w = StubWorld()
    w.in_pursuit_mode = True
    bc = Player(70.0, 40.0)
    bc.max_speed_mps = 9.2
    w.ball_carrier = bc
    u.apply(w, [d], 60.0, 35.0)
    assert d.move_speed <= 5.0, ("pursuit", d.move_speed)

    # kickoff chase
    w2 = StubWorld()
    w2.in_kickoff = True
    w2.first_receiver = Player(70.0, 35.0)
    w2.kickoff_landing_y = 35.0
    d2 = make_defender()
    u.apply(w2, [d2], 60.0, 35.0)
    # Defender already at the anchor (dist ~ 0) -> 0.5x band: 5.0 * 0.5 = 2.5.
    # Must derive from the unified ceiling and never exceed it.
    assert 0.0 < d2.move_speed <= 5.0, ("kickoff", d2.move_speed)

    # governor
    geom = DefensiveGeometry(tuners)
    g = DefensiveLineGovernor(tuners, geometry=geom)
    wg = StubWorld()
    wg.ball_carrier = Player(69.0, 35.0)
    st = g.compute_line_state(wg, [make_defender()])
    assert st["speed_mps"] <= 5.0, st["speed_mps"]

    # role movement
    for role in ("DEF_RETREAT_10", "DEF_LINE_READY", "DEF_CHASE"):
        p = make_defender()
        p.defender_role = role
        apply_role_movement(p, "RELEASE", 60.0, 35.0, tuners)
        assert p.max_speed <= 5.0, (role, p.max_speed)

    # KMC
    w3 = StubWorld()
    ke = KickoffEngine(w3, Ball(FakeField()), FakeField())
    ke.world = w3
    assert ke._def_top_speed() == 5.0

    print("PASS: pursuit=%.1f kickoff=%.1f governor=%.1f clamp=%.1f kmc=%.1f"
          % (d.move_speed, d2.move_speed, st["speed_mps"],
             tuners.post_catch_cap_mps, ke._def_top_speed()))
    print("ALL UNIFIED-SPEED WRITER CHECKS PASSED (ceiling 5.0 m/s)")


if __name__ == "__main__":
    main()
