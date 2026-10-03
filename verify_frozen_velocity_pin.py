"""verify_frozen_velocity_pin.py - Prove apply_phase_invariants zeroes velocity on frozen players.

Defect: during RELEASE a freshly tackle-state-released DEF_LINE_READY was
re-integrated by the slot-invariant repair (run_defensive_pipeline) and ended
the frame with vx=-5.2, vy=4.17 while frozen - the MUST_BE_FROZEN_MOVING /
ILLEGAL_MOVE violations and the teleport-around-the-ruck symptom.  The FINAL
apply_phase_invariants re-froze the body but never zeroed the velocity, and
audit_movement then saw a frozen body moving.

Fix: apply_phase_invariants now zeroes vx/vy for every player that ends the
call frozen - the sole freeze authority is fully authoritative.

Run:  python verify_frozen_velocity_pin.py
Exit 0 on success.
"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from world.world_state import WorldState
from world.rules.movement_contracts import (
    apply_phase_invariants,
    audit_movement,
    enforce_movement_authority_invariant,
    zero_frozen_velocity,
)
from game.defence.defence_lvl_1.tackle_state_gate import (
    _release_defender,
)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y, defender_role, vx=0.0, vy=0.0,
                  fullback=False, depth_locked=False):
    d = Player(x=x, y=y, player_id=pid)
    d.defender_role = defender_role
    d.vx = vx
    d.vy = vy
    d.fullback_role_active = fullback
    d.depth_locked = depth_locked
    return d


def main():
    print("=" * 64)
    print("FROZEN-VELOCITY PIN VERIFIER (2026-09-07)")
    print("=" * 64)

    # TEST 1: RELEASE - frozen DEF_LINE_READY with stale velocity gets zeroed
    print("\n[1] RELEASE: frozen DEF_LINE_READY velocity zeroed by freeze authority")
    w = WorldState()
    d = make_defender(
        pid=207, x=40.68, y=20.02,
        defender_role=DefenderRole.DEF_LINE_READY,
        vx=-5.208, vy=4.169,
    )
    w.defending_team = [d]
    w.attacking_team = []
    w.tackler_ids = set()

    apply_phase_invariants(w, "RELEASE")

    check("1.1 DEF_LINE_READY ends frozen", d.frozen is True,
          "frozen=%r" % d.frozen)
    check("1.2 frozen DEF_LINE_READY has vx=0", abs(d.vx) < 1e-9,
          "vx=%.4f" % d.vx)
    check("1.3 frozen DEF_LINE_READY has vy=0", abs(d.vy) < 1e-9,
          "vy=%.4f" % d.vy)

    viols = audit_movement(w, "RELEASE")
    check("1.4 audit_movement(RELEASE) has no violation for id=207",
          all("id=207" not in v for v in viols),
          "violations=%s" % viols)

    # TEST 2: RELEASE - DEF_RETREAT_10 keeps movement authority (velocity kept)
    print("\n[2] RELEASE: DEF_RETREAT_10 keeps movement authority (velocity kept)")
    w2 = WorldState()
    d2 = make_defender(
        pid=212, x=40.37, y=35.08,
        defender_role=DefenderRole.DEF_RETREAT_10,
        vx=1.5, vy=0.5,
    )
    w2.defending_team = [d2]
    w2.attacking_team = []
    w2.tackler_ids = set()

    apply_phase_invariants(w2, "RELEASE")

    check("2.1 DEF_RETREAT_10 unfrozen", d2.frozen is False,
          "frozen=%r" % d2.frozen)
    check("2.2 DEF_RETREAT_10 velocity NOT zeroed by freeze authority",
          abs(d2.vx - 1.5) < 1e-9 and abs(d2.vy - 0.5) < 1e-9,
          "vx=%.4f vy=%.4f (expected 1.5, 0.5)" % (d2.vx, d2.vy))

    # TEST 3: end-of-frame re-freeze zeroes high velocity
    print("\n[3] RELEASE end-of-frame re-freeze zeroes high velocity")
    w3 = WorldState()
    d3 = make_defender(
        pid=207, x=40.68, y=20.02,
        defender_role=DefenderRole.DEF_LINE_READY,
        vx=-5.208, vy=4.169,
    )
    w3.defending_team = [d3]
    w3.attacking_team = []
    w3.tackler_ids = set()

    apply_phase_invariants(w3, "RELEASE")

    check("3.1 final freeze sets frozen=True", d3.frozen is True)
    check("3.2 vx reset to 0", abs(d3.vx) < 1e-9, "vx=%.4f" % d3.vx)
    check("3.3 vy reset to 0", abs(d3.vy) < 1e-9, "vy=%.4f" % d3.vy)

    ma = enforce_movement_authority_invariant(w3, "RELEASE")
    check("3.4 movement-authority invariant clean", not ma, "ma=%s" % ma)

    # TEST 4: depth-locked body keeps its velocity
    print("\n[4] TACKLE_DRIVE: depth-locked line body keeps Y motion (not frozen)")
    w4 = WorldState()
    d4 = make_defender(
        pid=208, x=40.68, y=25.01,
        defender_role=DefenderRole.DEF_LINE_READY,
        vx=0.0, vy=0.8, depth_locked=True,
    )
    w4.defending_team = [d4]
    w4.attacking_team = []
    w4.tackler_ids = set()
    ope = type("_O", (), {})()
    ope.def_tuners = type("_T", (), {"tes_drive_depth_track_enabled": True})()
    w4.open_play_engine = ope

    apply_phase_invariants(w4, "TACKLE_DRIVE")

    check("4.1 depth-locked body unfrozen", d4.frozen is False,
          "frozen=%r" % d4.frozen)
    check("4.2 depth-locked body vy preserved (lateral motion allowed)",
          abs(d4.vy - 0.8) < 1e-9, "vy=%.4f" % d4.vy)

    # TEST 5: tackle-gate release clears velocity (Part 3 - _release_defender)
    print("\n[5] TACKLE-STATE RELEASE: _release_defender zeroes velocity")
    w5 = WorldState()
    d5 = make_defender(
        pid=209, x=42.0, y=19.0,
        defender_role=DefenderRole.TACKLER_1,
        vx=-5.208, vy=4.169,
    )
    d5.tackle_state_active = True
    _release_defender(d5)

    check("5.1 released defender has vx=0", abs(d5.vx) < 1e-9,
          "vx=%.4f" % d5.vx)
    check("5.2 released defender has vy=0", abs(d5.vy) < 1e-9,
          "vy=%.4f" % d5.vy)
    check("5.3 released defender role -> DEF_LINE_READY",
          d5.defender_role is DefenderRole.DEF_LINE_READY,
          "role=%r" % d5.defender_role)
    check("5.4 released defender tackle_state_active cleared",
          d5.tackle_state_active is False,
          "tackle_state_active=%r" % d5.tackle_state_active)

    # TEST 6: zero_frozen_velocity zeroes a frozen body but not an unfrozen one
    print("\n[6] ZERO-FROZEN-VELOCITY: pre-audit pass zeroes only frozen bodies")
    w6 = WorldState()
    d6_marker = make_defender(
        pid=210, x=41.0, y=35.0,
        defender_role=DefenderRole.MARKER_A,
        vx=3.0, vy=2.0,
    )
    d6_marker.frozen = True
    d6_run = make_defender(
        pid=211, x=50.0, y=30.0,
        defender_role=DefenderRole.DEF_RETREAT_10,
        vx=1.5, vy=2.5,
    )
    d6_run.frozen = False
    w6.defending_team = [d6_marker, d6_run]
    w6.attacking_team = []
    w6.tackler_ids = set()

    zero_frozen_velocity(w6)

    check("6.1 frozen marker vx zeroed", abs(d6_marker.vx) < 1e-9,
          "vx=%.4f" % d6_marker.vx)
    check("6.2 frozen marker vy zeroed", abs(d6_marker.vy) < 1e-9,
          "vy=%.4f" % d6_marker.vy)
    check("6.3 unfrozen defender velocity NOT zeroed",
          abs(d6_run.vx - 1.5) < 1e-9 and abs(d6_run.vy - 2.5) < 1e-9,
          "vx=%.4f vy=%.4f (expected 1.5, 2.5)" % (d6_run.vx, d6_run.vy))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
