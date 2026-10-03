"""ATTACK GEOMETRY STABILITY verifier (TASK XII, 2026-08-26).

SPEC CONTEXT
------------
The attack side was the last noisy system: every support runner chased the
ball carrier directly (lane drift -> blob), there was no depth/stagger law,
no sweep/support identity, no settle timing after the ruck, and no audit
trail.  Downstream systems (BC brain, pre-tackle logic, open-play
depth-role rewrite) need a deterministic attack shape and a structured
dump chain instead of compensating for noise.

FIX UNDER TEST
--------------
game/attack/attack_lvl_1/attack_structure_authority.py
    Single-writer authored lanes: identity slots (FR / SWEEP / SUPPORT_B..),
    ruck_y-anchored lane law, bc.x-depth law with stagger minimums,
    wall-clock settle window on POST_RUCK / first OPEN_PLAY entry,
    hysteresis-protected identity reconciliation.
game/attack/attack_lvl_1/attack_tuners.py  AttackStructureTuners
game/debug/attack_structure_audit.py       structured dump chain
game/managers/attack_manager.py            structured-or-legacy split

PARTS
-----
  P1  Gate-off byte-stability: apply_support_orders returns False; the
      manager runs the legacy BC-chase verbatim.
  P2  Lane law: target_y anchors to ruck_y + open_side*lane_m per slot
      (NEVER bc.y); no convergence-on-BC targets.
  P3  Depth/stagger: depth offsets increase FR < B < C < SWEEP behind the
      BC; adjacent authored lanes keep >= stagger_min_m Y separation.
  P4  Identity: same input state across N frames is identical;
      small perturbations do NOT flip FR (hysteresis); a real side-cross
      DOES swap identities (refit).
  P5  Settle: window length deterministic in sim-time; identities frozen
      during settle even under large perturbation; refit fires after end.
  P6  Phase gating + DH ownership: no orders outside OPEN_PLAY /
      POST_RUCK_OPEN_PLAY; DH never receives a lane order.
  P7  Dump chain: SETTLE_BEGIN/END, LANE_READ, ESCAPE_WINDOW_*, and
      BC_VECTOR_SOURCE transitions land in the event ring; format_event()
      renders canonical lines.
  P8  Wiring regression: engine modules import cleanly; kickoff reset
      clears structure state; prior Task XI verifier still green.

NOTE ON SETTLE
--------------
Every fresh world auto-begins a 1.0 s settle window on the first
OPEN_PLAY / POST_RUCK frame (that is the designed behaviour).  Identity
reconciliation (hysteresis, refits) is a POST-settle contract, so P2/P3/P4
burn the window to expiry before asserting it.  P5 asserts the window
itself freezes identities.

Exit code 0 when all scenarios pass, 1 otherwise.
"""
import math
import subprocess
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.attack.attack_lvl_1.attack_tuners import AttackStructureTuners
from game.attack.attack_lvl_1.attack_structure_authority import (
    _STRUCTURE_STATE,
    apply_support_orders,
    reset_attack_structure,
)
import game.debug.attack_structure_audit as asa
from game.managers.attack_manager import AttackManager


RUCK_X = 60.0
RUCK_Y = 30.0            # < 35 -> open side = +1 (toward y=70)
DT = 0.016

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class Tuners(AttackStructureTuners):
    def __init__(self, **overrides):
        super().__init__()
        self.structure_enabled = True          # tests opt IN to structure
        for key, value in overrides.items():
            setattr(self, key, value)


class DarkTuners(AttackStructureTuners):
    """Explicitly-disabled tuners: exercises the legacy rollback path.

    TASK XIII flipped the production defaults ON (structure_enabled and
    steering_enabled both True).  The dark-safe gate remains available by
    construction — this subclass is how the verifier proves the legacy
    BC-chase / raw-steering path is still byte-stable when an instance is
    explicitly re-disabled.
    """

    def __init__(self):
        super().__init__()
        self.structure_enabled = False
        self.steering_enabled = False


class StubWorld:
    def __init__(self):
        self.ruck_x_m = RUCK_X
        self.ruck_y_m = RUCK_Y
        self.attacking_team = []
        self.defending_team = []
        self.ball_carrier = None
        self.frame_count = 100
        self.sim_time_s = 12.0
        self.sub_state = "OPEN_PLAY"


class StubController:
    def __init__(self):
        self.calls = 0

    def apply_vector(self, bc, world, dt):
        self.calls += 1


class StubOPE:
    def __init__(self, world, phase="OPEN_PLAY", dh=None):
        self.world = world
        self._phase = phase
        self.dh = dh
        self.attack_controller = StubController()


def make_player(player_id, x, y):
    return Player(x=x, y=y, player_id=player_id)


def fresh_world(runners):
    w = StubWorld()
    bc = make_player(99, 70.0, 32.0)
    w.ball_carrier = bc
    w.attacking_team = [bc] + runners
    reset_attack_structure(w)
    asa.reset_events()
    return w, bc


def slot_map(world):
    out = {}
    for p in world.attacking_team:
        pid = getattr(p, "player_id", None)
        slot = getattr(p, "attack_lane_slot", None)
        if slot is not None:
            out[pid] = slot
    return out


def role_map(world):
    out = {}
    for p in world.attacking_team:
        pid = getattr(p, "player_id", None)
        role = getattr(p, "attack_role", None)
        if role is not None:
            out[pid] = role
    return out


def run_frames(w, tuners, n=3, phase="OPEN_PLAY"):
    ope = StubOPE(w, phase=phase)
    for _ in range(n):
        assert apply_support_orders(ope, DT, tuners) is True


def burn_settle(w, tuners, phase="OPEN_PLAY", cap=200):
    """Run frames until the auto-begun settle window expires."""
    ope = StubOPE(w, phase=phase)
    for _ in range(cap):
        apply_support_orders(ope, DT, tuners)
        st = _STRUCTURE_STATE.get(id(w))
        if not st["settling"]:
            break


# ─────────────────────────────────────────────────────────────────────
# P1 — gate off keeps the legacy chase
# ─────────────────────────────────────────────────────────────────────

def p1_gate_off_byte_stable():
    print()
    print("[P1] Gate OFF (explicitly-disabled): authority declines; "
          "manager runs legacy chase")
    # (a) authority-level decline — dark-safe rollback path.
    w, bc = fresh_world([make_player(1, 62.0, 28.0), make_player(2, 64.0, 36.0)])
    ope = StubOPE(w)
    check("P1.0: gate-off apply_support_orders returns False",
          apply_support_orders(ope, DT, DarkTuners()) is False)

    # Defaults are ACTIVATED by TASK XIII ("XII to be ON"): the production
    # gate is live for authored lanes AND steering.  The dark-safe path is
    # exercised explicitly below, never relied on as the default.
    _default_tuners = AttackStructureTuners()
    check("P1.1: production defaults are ACTIVATED (structure ON)",
          getattr(_default_tuners, "structure_enabled", False) is True)
    check("P1.1b: production defaults are ACTIVATED (steering ON)",
          getattr(_default_tuners, "steering_enabled", False) is True)

    # (b) manager-level legacy chase semantics (velocity toward the BC)
    mgr = AttackManager()                       # default gate ON -> force OFF
    mgr.structure_tuners = DarkTuners()
    w2 = StubWorld()
    bc2 = make_player(99, 70.0, 32.0)
    r1 = make_player(1, 66.0, 30.0)
    r2 = make_player(2, 68.0, 38.0)
    w2.ball_carrier = bc2
    w2.attacking_team = [bc2, r1, r2]
    ope2 = StubOPE(w2)
    mgr.update(ope2, DT)
    toward_bc = all(
        math.copysign(1.0, p.vx) == math.copysign(1.0, bc2.x - p.x) or p.vx == 0.0
        for p in (r1, r2)
    )
    check("P1.2: gate-off manager issues legacy chase vectors", toward_bc,
          f"r1.vx={r1.vx:.2f} r2.vx={r2.vx:.2f}")
    check("P1.3: gate-off manager steered the BC via controller",
          ope2.attack_controller.calls == 1)


# ─────────────────────────────────────────────────────────────────────
# P2 — lane law: ruck_y anchoring, no convergence-on-BC
# ─────────────────────────────────────────────────────────────────────

def p2_lane_law():
    print()
    print("[P2] Authored lanes anchor to ruck_y, not to the ball carrier")
    runners = [
        make_player(1, 68.0, 31.0),
        make_player(2, 69.0, 34.0),
        make_player(3, 67.0, 40.0),
        make_player(4, 66.0, 48.0),
    ]
    w, bc = fresh_world(runners)
    t = Tuners()
    burn_settle(w, t)

    sm = slot_map(w)
    check("P2.0: four distinct slots assigned",
          len(set(sm.values())) == 4, str(sm))
    fr = next(pid for pid, s in sm.items() if s == "FR")
    sweep = next(pid for pid, s in sm.items() if s == "SWEEP")

    fr_p = next(p for p in w.attacking_team if p.player_id == fr)
    sw_p = next(p for p in w.attacking_team if p.player_id == sweep)
    exp_fr_y = RUCK_Y + t.lane_fr_m
    exp_sw_y = RUCK_Y + t.lane_sweep_m
    check("P2.1: FR lane anchored at ruck_y + lane_fr_m",
          abs(fr_p.target_y - exp_fr_y) < 1e-9,
          f"{fr_p.target_y:.2f} vs {exp_fr_y:.2f}")
    check("P2.2: SWEEP lane anchored at ruck_y + lane_sweep_m",
          abs(sw_p.target_y - exp_sw_y) < 1e-9,
          f"{sw_p.target_y:.2f} vs {exp_sw_y:.2f}")

    # Move the BC far sideways: lane Y MUST NOT move with it (drift lock).
    bc.y = 20.0
    run_frames(w, t, n=1)
    fr_after = next(p for p in w.attacking_team if p.player_id == fr)
    sw_after = next(p for p in w.attacking_team if p.player_id == sweep)
    check("P2.3: lanes stay ruck-locked while the BC jinks",
          abs(fr_after.target_y - exp_fr_y) < 1e-9
          and abs(sw_after.target_y - exp_sw_y) < 1e-9)

    # No convergence-on-BC: every support target differs from the BC spot
    # by at least the smallest depth offset.
    min_sep = min(abs(p.target_x - bc.x) for p in w.attacking_team
                  if p.player_id != 99)
    check("P2.4: no support target converges on the BC position",
          min_sep >= t.depth_fr_m - 1e-9, f"min sep {min_sep:.2f}")

    # Manager attribution stamp present (sole-authority audit rule).
    stamped = all(
        getattr(p, "movement_writer", None) == "manager:attack"
        for p in w.attacking_team if p.player_id != 99
    )
    check("P2.5: every order stamped manager:attack", stamped)


# ─────────────────────────────────────────────────────────────────────
# P3 — depth/stagger law
# ─────────────────────────────────────────────────────────────────────

def p3_depth_stagger():
    print()
    print("[P3] Depth ordering FR<B<C<SWEEP behind the BC; stagger separation")
    runners = [
        make_player(1, 68.0, 31.0),   # closest -> FR
        make_player(2, 69.0, 35.0),   # mid   -> SUPPORT_B
        make_player(3, 67.0, 41.0),   # mid   -> SUPPORT_C
        make_player(4, 66.0, 50.0),   # wide  -> SWEEP
    ]
    w, bc = fresh_world(runners)
    t = Tuners()
    burn_settle(w, t)

    sm = slot_map(w)
    depths = {}
    for pid, s in sm.items():
        p = next(q for q in w.attacking_team if q.player_id == pid)
        depths[s] = abs(bc.x - p.target_x)
    order_ok = (
        depths["FR"] < depths["SUPPORT_B"] < depths["SUPPORT_C"] < depths["SWEEP"]
    )
    check("P3.0: depth increases FR < SUPPORT_B < SUPPORT_C < SWEEP",
          order_ok, str({k: round(v, 2) for k, v in depths.items()}))

    anchors = {
        "FR": RUCK_Y + t.lane_fr_m,
        "SUPPORT_B": RUCK_Y + t.lane_support_b_m,
        "SUPPORT_C": RUCK_Y + t.lane_support_c_m,
        "SWEEP": RUCK_Y + t.lane_sweep_m,
    }
    seq = ["FR", "SUPPORT_B", "SUPPORT_C", "SWEEP"]
    gaps_ok = all(
        anchors[b] - anchors[a] >= t.stagger_min_m
        for a, b in zip(seq, seq[1:])
    )
    check("P3.1: adjacent authored lanes keep >= stagger_min_m separation",
          gaps_ok,
          str([round(anchors[b] - anchors[a], 2) for a, b in zip(seq, seq[1:])]))


# ─────────────────────────────────────────────────────────────────────
# P4 — identity determinism + hysteresis anti-flip + refit on side-cross
# ─────────────────────────────────────────────────────────────────────

def p4_identity_stability_and_hysteresis():
    print()
    print("[P4] Determinism, anti-flip hysteresis, and real refits")
    # (a) determinism: identical inputs -> identical assignment (post-settle)
    runners = [
        make_player(1, 68.0, 31.0),
        make_player(2, 69.0, 36.0),
        make_player(3, 67.0, 44.0),
    ]
    w, bc = fresh_world(runners)
    t = Tuners()
    burn_settle(w, t)
    snap = slot_map(w)
    run_frames(w, t, n=6)
    check("P4.0: repeated frames are byte-stable", slot_map(w) == snap,
          f"{snap} vs {slot_map(w)}")

    # (b) anti-flip: tiny perturbation must NOT move FR off its holder.
    w2, bc2 = fresh_world([
        make_player(1, 68.0, 32.0),   # A: sits ON the FR anchor
        make_player(2, 69.0, 33.0),   # B: just outside
    ])
    t2 = Tuners(identity_hysteresis_m=2.0)
    burn_settle(w2, t2)
    check("P4.1: A holds FR initially",
          slot_map(w2).get(1) == "FR", str(slot_map(w2)))
    # Nudge: B now strictly closer than A, delta well inside hysteresis.
    w2.attacking_team[1].y = 32.6     # A (id 1)
    w2.attacking_team[2].y = 31.9     # B (id 2)
    run_frames(w2, t2, n=3)
    check("P4.2: hysteresis holds FR through the small perturbation",
          slot_map(w2).get(1) == "FR", str(slot_map(w2)))

    # (c) genuine refit: A crosses sides of the ruck -> identities swap.
    w3, bc3 = fresh_world([
        make_player(1, 68.0, 32.0),
        make_player(2, 69.0, 33.0),
    ])
    burn_settle(w3, t2)
    check("P4.3: pre-cross A holds FR", slot_map(w3).get(1) == "FR",
          str(slot_map(w3)))
    w3.attacking_team[1].y = 24.0     # A crosses below ruck_y=30
    run_frames(w3, t2, n=3)
    check("P4.4: crossed holder released FR (refit)",
          slot_map(w3).get(1) != "FR" and slot_map(w3).get(2) == "FR",
          str(slot_map(w3)))
    check("P4.5: roles remain one-per-slot after the refit",
          len(set(slot_map(w3).values())) == 2)


# ─────────────────────────────────────────────────────────────────────
# P5 — settle timing determinism + frozen identities through the window
# ─────────────────────────────────────────────────────────────────────

def p5_settle_window():
    print()
    print("[P5] Settle: deterministic duration, frozen identities, post-refit")
    runners = [
        make_player(1, 68.0, 32.0),
        make_player(2, 69.0, 36.0),
    ]
    w, bc = fresh_world(runners)
    t = Tuners(settle_window_s=1.0)

    ope = StubOPE(w, phase="POST_RUCK_OPEN_PLAY")
    # Frame 1: POST_RUCK entry begins the settle AND seeds identities.
    assert apply_support_orders(ope, DT, t) is True
    seeded = slot_map(w)
    check("P5.0: identities seeded on settle begin", len(seeded) == 2, str(seeded))

    # Teleport a player across the ruck mid-settle: identity must NOT flip.
    w.attacking_team[1].y = 10.0
    elapsed = 0.0
    flips_during = False
    st = None
    for _ in range(300):
        assert apply_support_orders(ope, DT, t) is True
        elapsed += DT
        st = _STRUCTURE_STATE.get(id(w))
        if st["settling"]:
            if slot_map(w).get(1) != seeded.get(1):
                flips_during = True
        else:
            break
    check("P5.1: identity frozen through the whole settle window",
          flips_during is False, str(slot_map(w)))

    check("P5.2: settle window expired within ~window_s of sim time",
          st["settling"] is False and abs(elapsed - 1.0) <= DT * 2,
          f"elapsed={elapsed:.3f}s settling={st['settling']}")

    # After expiry the refit is allowed: the crossed player loses FR.
    assert apply_support_orders(ope, DT, t) is True
    check("P5.3: refit fires immediately after settle end",
          slot_map(w).get(1) != "FR" and slot_map(w).get(2) == "FR",
          str(slot_map(w)))

    # Determinism of the window itself: identical replay gives identical
    # frame count until expiry.
    w4, bc4 = fresh_world([make_player(1, 68.0, 32.0)])
    t4 = Tuners(settle_window_s=1.0)
    ope4 = StubOPE(w4, phase="POST_RUCK_OPEN_PLAY")
    frames_to_expire = 0
    while True:
        apply_support_orders(ope4, DT, t4)
        frames_to_expire += 1
        st4 = _STRUCTURE_STATE.get(id(w4))
        if not st4["settling"]:
            break
        if frames_to_expire > 200:
            break
    check("P5.4: settle consumes exactly ceil(window/dt) frames",
          frames_to_expire == int(math.ceil(1.0 / DT)),
          f"{frames_to_expire} frames")


# ─────────────────────────────────────────────────────────────────────
# P6 — phase gating and DH ownership
# ─────────────────────────────────────────────────────────────────────

def p6_phase_gating_and_dh():
    print()
    print("[P6] No orders outside open-play phases; DH untouched")
    runners = [make_player(1, 68.0, 32.0)]
    dh = make_player(7, 61.0, 29.5)
    w, bc = fresh_world(runners)

    for phase in ("TACKLE_INIT", "TACKLE_DRIVE", "RELEASE",
                  "PTB_PREP", "PTB_EXEC", "PTB"):
        ope_bad = StubOPE(w, phase=phase)
        check(f"P6.{phase}: authority declines outside open play",
              apply_support_orders(ope_bad, DT, Tuners()) is False)

    # DH ownership: with dh set, it receives NO identity/lane order.
    ope = StubOPE(w, phase="OPEN_PLAY", dh=dh)
    dh_tx_before, dh_ty_before = dh.target_x, dh.target_y
    dh_role_before = getattr(dh, "attack_role", None)
    assert apply_support_orders(ope, DT, Tuners()) is True
    check("P6.dh: DH excluded from the identity map",
          getattr(dh, "attack_lane_slot", None) is None)
    check("P6.dh: DH target untouched by the authority",
          dh.target_x == dh_tx_before and dh.target_y == dh_ty_before)
    check("P6.dh: DH attack_role unchanged",
          getattr(dh, "attack_role", None) == dh_role_before)
    check("P6.dh: the remaining runner still gets a lane order",
          getattr(w.attacking_team[1], "attack_lane_slot", None) == "FR")


# ─────────────────────────────────────────────────────────────────────
# P7 — structured dump chain
# ─────────────────────────────────────────────────────────────────────

def p7_dump_chain():
    print()
    print("[P7] Macro event ring captures every transition kind")
    asa.reset_events()

    runners = [
        make_player(1, 68.0, 32.0),
        make_player(2, 69.0, 40.0),
        make_player(3, 67.0, 52.0),
    ]
    w, bc = fresh_world(runners)
    t = Tuners(lane_read_delta_m=1.0)

    ope = StubOPE(w, phase="POST_RUCK_OPEN_PLAY")
    # Frame 1: settle begins (LANE_READ fires only on a SUBSEQUENT target
    # shift, so this frame records SETTLE_BEGIN).
    apply_support_orders(ope, DT, t)
    kinds_now = {e["kind"] for e in asa.get_events()}
    check("P7.0: SETTLE_BEGIN recorded", "SETTLE_BEGIN" in kinds_now,
          str(sorted(kinds_now)))

    # Burn the settle window to expiry (records SETTLE_END).
    elapsed = 0.0
    while True:
        apply_support_orders(ope, DT, t)
        elapsed += DT
        st = _STRUCTURE_STATE.get(id(w))
        if not st["settling"]:
            break
        if elapsed > 2.0:
            break
    kinds_after_burn = {e["kind"] for e in asa.get_events()}
    check("P7.1: SETTLE_END recorded at expiry",
          "SETTLE_END" in kinds_after_burn, str(sorted(kinds_after_burn)))

    # Now drive a large target shift: LANE_READ fires on the next frame.
    before = len(asa.get_events())
    bc.x += 8.0
    apply_support_orders(ope, DT, t)
    new_kinds = [e["kind"] for e in asa.get_events()[before:]]
    check("P7.2: LANE_READ emitted after a large target shift",
          "LANE_READ" in new_kinds, str(new_kinds))

    # BC decision provenance: escape enter/exit + source transitions.
    asa.reset_events()
    asa.record_bc_decision(w, "escape", True)
    asa.record_bc_decision(w, "escape", True)      # steady: no new events
    asa.record_bc_decision(w, "geometry", False)   # exit + source change
    kinds = [e["kind"] for e in asa.get_events()]
    check("P7.3: ESCAPE_WINDOW_ENTER then EXIT recorded",
          kinds.count("ESCAPE_WINDOW_ENTER") == 1
          and kinds.count("ESCAPE_WINDOW_EXIT") == 1, str(kinds))
    check("P7.4: BC_VECTOR_SOURCE transition recorded",
          kinds.count("BC_VECTOR_SOURCE") >= 1, str(kinds))

    line = asa.format_event(asa.get_events()[0])
    check("P7.5: canonical event line is renderable",
          "ESCAPE_WINDOW_ENTER" in line and "[frame=" in line, line)


# ─────────────────────────────────────────────────────────────────────
# P8 — wiring regression
# ─────────────────────────────────────────────────────────────────────

def p8_wiring_regression():
    print()
    print("[P8] Engine imports cleanly; reset wired; Task XI verifier green")
    # (a) full engine import (boot smoke through OPE's import graph)
    import importlib
    mod = importlib.import_module("world.open_play_engine")
    check("P8.0: OpenPlayEngine imports with the new chain", mod is not None)
    eng_cls = getattr(mod, "OpenPlayEngine", None)
    check("P8.1: OpenPlayEngine class present", eng_cls is not None)

    # (b) kickoff reset wiring exists in source
    src = open("world/open_play_engine.py", encoding="utf-8").read()
    check("P8.2: reset_for_kickoff clears attack-structure state",
          "reset_attack_structure(w)" in src and "reset_events()" in src)

    # (c) reset actually drops state + ring
    w, bc = fresh_world([make_player(1, 68.0, 32.0)])
    run_frames(w, Tuners(), n=2)
    check("P8.3: state existed before reset", id(w) in _STRUCTURE_STATE)
    reset_attack_structure(w)
    check("P8.4: reset_attack_structure drops the cycle state",
          id(w) not in _STRUCTURE_STATE)
    asa.reset_events()
    check("P8.5: audit ring cleared by reset_events", asa.get_events() == [])

    # (d) prior-task defence verifier still passes (no cross-regression).
    proc = subprocess.run(
        [sys.executable, "verify_edge_release_conversion.py"],
        capture_output=True, text=True, timeout=180,
    )
    tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    check("P8.6: verify_edge_release_conversion still green",
          proc.returncode == 0, tail[0])


def main():
    print("=" * 64)
    print("ATTACK GEOMETRY STABILITY VALIDATION (TASK XII)")
    print("=" * 64)

    p1_gate_off_byte_stable()
    p2_lane_law()
    p3_depth_stagger()
    p4_identity_stability_and_hysteresis()
    p5_settle_window()
    p6_phase_gating_and_dh()
    p7_dump_chain()
    p8_wiring_regression()

    print()
    print("=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
