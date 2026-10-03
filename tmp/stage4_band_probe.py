"""Stage 4 probe: do the positional profiles actually move the collision band?

Builds REAL ``Player`` bodies, stamps them through the real positional mapper,
drives the REAL contact authority on a mocked proximity line, and derives the
REAL wrap model -- then prints the margin and the resolved collision state.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from player.player import DefenderRole, Player
from player.positional_traits import apply_positional_traits, traits_for_jersey
from game.defence.defence_lvl_1 import contact_authority as ca
from game.defence.defence_lvl_1 import contest_preservation as cp
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.tes import tes_multi_body as mb

BC_X, BC_Y = 60.0, 35.0


class _StubOPE:
    def __init__(self, verdicts):
        self._phase = "OPEN_PLAY"
        self.verdicts = dict(verdicts)
        self.calls = []

    def _check_pre_tackle(self, bc, body):
        self.calls.append(getattr(body, "player_id", None))
        return self.verdicts.get(getattr(body, "player_id", None), "MISS")

    def _start_tackle(self, bc, body):
        pass


def _body(pid, x, jersey, vx):
    p = Player(x=float(x), y=BC_Y, player_id=int(pid))
    p.jersey_number = int(jersey)
    apply_positional_traits(p)
    p.tackle_role = "PRIMARY"
    p.defender_role = DefenderRole.DEF_LINE_READY
    p.depth_class = "SECONDARY"
    p.pre_tackle_state = None
    p.fullback_role_active = False
    p.can_acquire_tackle = False
    p._pta_corridor = False
    p._pta_corridor_frames = 0
    p._machine_state = None
    p._tackle_engage_guard = False
    p.vx = float(vx)
    p.vy = 0.0
    return p


def _bc(pid, jersey, vx):
    p = Player(x=BC_X, y=BC_Y, player_id=int(pid))
    p.jersey_number = int(jersey)
    apply_positional_traits(p)
    p.vx = float(vx)
    p.vy = 0.0
    p.tackle_role = None
    p.fullback_role_active = False
    return p


def _world(team):
    return SimpleNamespace(
        frame_count=21,
        ball_carrier=None,
        defending_team=list(team),
        tes_active=False,
        ball_owner="BC",
        players=[],
    )


def run_case(label, carrier_jersey, carrier_vx, defender_specs):
    bc = _bc(1, carrier_jersey, carrier_vx)
    line = []
    verdicts = {}
    for pid, x, jersey, vx, verdict in defender_specs:
        line.append(_body(pid, x, jersey, vx))
        if verdict is not None:
            verdicts[pid] = verdict
    world = _world(line)
    ope = _StubOPE(verdicts)
    ca.reset_contact_authority(world)
    cp.reset_preservation(world)

    decision = ca.evaluate_contact(world, ope, line, bc, DefenceTuners())
    record = cp.last_record(world)
    bodies = {int(getattr(b, "player_id")): b for b in line}
    bodies[int(bc.player_id)] = bc
    model = mb.derive_wrap_model(record, bodies, carrier=bc)

    print("")
    print("=" * 74)
    print("%s  (carrier jersey %s @ vx=%.2f)" % (label, carrier_jersey, carrier_vx))
    print("=" * 74)
    print("  carrier mass_kg=%s fend_attr=%s snapshot_fend=%s"
          % (getattr(bc, "mass_kg", None), getattr(bc, "fend_trait", None),
             record.carrier.fend_trait))
    for row in record.candidates:
        body = bodies.get(row.player_id)
        print("  row %-4s %-26s mass_kg=%-7s skill=%-5s in_radius=%s"
              % (row.player_id, row.disposition.name,
                 getattr(body, "mass_kg", None),
                 row.tackle_skill_trait, row.in_radius))
    print("  winner=%s predicate_calls=%s" % (decision.winner_id, ope.calls))
    if model is None:
        print("  MODEL: NONE")
        return
    print("  assists=%s contributions=%d"
          % (list(model.assist_ids), len(model.contributions)))
    print("  baseline_wrap=%.2f effective_wrap=%.2f"
          % (model.baseline_wrap_momentum_kgmps, model.wrap_momentum_kgmps))
    print("  carrier_baseline=%.2f carrier_effective=%.2f fend=%.3f"
          % (model.carrier_momentum_kgmps, model.carrier_effective_momentum_kgmps,
             model.carrier_fend_trait))
    print("  MARGIN=%+.2f  threshold=%.1f  ->  STATE=%s"
          % (model.carrier_momentum_margin_kgmps,
             mb.COLLISION_MARGIN_THRESHOLD_KGMPS, model.collision_state))


def main():
    print("MAPPER TABLE")
    for j in (0, 1, 3, 7, 8, 11, 13, 14, 17, 18, 99, None, "x"):
        t = traits_for_jersey(j)
        print("  jersey %-5r -> %s" % (j, t.position if t else "UNMAPPED"))

    run_case("A  BACK carries vs single FORWARD", 3, 8.0,
             [(11, 59.0, 11, 4.0, "CONTACT")])
    run_case("B  FORWARD carries vs FORWARD CLUSTER", 11, 5.0,
             [(11, 59.0, 11, 3.0, "CONTACT"), (12, 59.6, 12, 3.0, None)])
    run_case("C  BACK carries vs single BACK", 3, 8.0,
             [(2, 59.0, 2, 4.0, "CONTACT")])


if __name__ == "__main__":
    main()
