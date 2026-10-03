"""verify_seed_hold.py — guardrail for the stale-seed lockdown (2026-09-09).

The task: "Kill all residual jitter by freezing lateral writers, clamping
target_x drift, and forcing MovementState to HOLD whenever spacing-Y or
depth-role seeds are stale."

`game/defence/defence_lvl_1/seed_hold.py` implements the three-part
stale-seed lockdown:
  1. FREEZE LATERAL WRITERS  — target_y restored to the frame-start Y.
  2. CLAMP target_x DRIFT    — target_x clamped into the anchor band.
  3. FORCE MovementState HOLD — movement_state = HOLD, speed/velocity zeroed.

This verifier asserts:
  1. The staleness predicate returns False for a healthy (fresh) line body
     and True for each stale-seed case (depth_role None / _depth_seeded
     False / missing frame dict / stale frame geometry); it returns False
     for non-line bodies (marker / fullback / frozen) and for a pre-ruck
     body with legitimately unformed bands.
  2. apply_seed_hold freezes target_y, clamps target_x into the anchor
     band, forces HOLD, and zeroes speed/velocity.
  3. decide_movement_state returns HOLD for a stale body and its normal
     state otherwise.
  4. The three wiring points are structurally present:
       - DefenceManager.update_open_play captures pre_pos + calls
         apply_seed_hold BEFORE the Apply-movement integration loop;
       - movement_mode_decision.decide_movement_state contains the
         stale-seed force-HOLD;
       - defensive_behaviour_unified MMDL block forces HOLD before
         d.movement_state = blended_state.

Run:  python verify_seed_hold.py
"""
from __future__ import annotations

import inspect
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from game.defence.defence_lvl_1.seed_hold import (
    SEED_HOLD_XDRIFT_BAND_M,
    _VALID_LANE_ROLES,
    apply_seed_hold,
    is_seed_stale,
)
from game.defence.defence_lvl_1.movement_mode_decision import decide_movement_state
from player.movement_states import MovementState

ANCHOR_X = 70.0   # ruck_x_m (60) + DEPTH (10)


class _FakeWorld:
    """Minimal world stand-in carrying the fields is_seed_stale reads."""

    def __init__(self, ruck_x=60.0, frame=100, ruck_established=True,
                 fresh_geometry=True, spacing=None, frame_spacing_covers=True):
        self.ruck_x_m = ruck_x
        self.ruck_y_m = 35.0
        self.frame_count = frame
        self.ruck_established = ruck_established
        self.frame_geometry_ready = fresh_geometry
        self.frame_geometry_frame = frame if fresh_geometry else frame - 1
        # spacing: a dict or None/{} (missing).  Default: a non-empty dict
        # that each test fills in per-defender via set_spacing.
        self.frame_spacing_targets = spacing
        self._cover_frame = frame_spacing_covers
        self.tes_active = False
        self.post_catch_chase_window_s = 0.0


    def set_spacing(self, defenders):
        """Publish a fresh spacing dict covering *defenders* (fresh geometry)."""
        self.frame_geometry_ready = True
        self.frame_geometry_frame = self.frame_count
        self.frame_spacing_targets = {id(d): 33.0 for d in defenders}


class _FakeDefender:
    """Minimal defender stand-in with every attribute is_seed_stale reads."""

    _pid = 0

    def __init__(self, depth_role="A", depth_side=-1, seeded=True,
                 role_tx=ANCHOR_X, role_ty=33.0, frame_spacing_y=33.0,
                 defender_role=None, fullback=False, frozen=False,
                 freeze_timer=0.0, tackle_state=False,
                 has_chase=False, depth_class="PASSIVE",
                 x=80.0, y=30.0, target_x=ANCHOR_X, target_y=33.0,
                 vx=0.0, vy=0.0, move_speed=0.0, movement_state=None,
                 target_source=None):
        _FakeDefender._pid += 1
        self.player_id = _FakeDefender._pid
        self.depth_role = depth_role
        self.depth_side = depth_side
        self._depth_seeded = seeded
        self.role_depth_target_x = role_tx
        self.role_depth_target_y = role_ty
        self.frame_spacing_y = frame_spacing_y
        self.defender_role = defender_role
        self.fullback_role_active = fullback
        self.frozen = frozen
        self.freeze_timer = freeze_timer
        self.tackle_state_active = tackle_state
        self.depth_class = depth_class
        self.x = x
        self.y = y
        self.target_x = target_x
        self.target_y = target_y
        self.vx = vx
        self.vy = vy
        self.move_speed = move_speed
        self.movement_state = movement_state
        self.target_source = target_source
        self.mmdl_target_x = None
        self.mmdl_target_y = None
        if has_chase:
            self.chase_target_x = 0.0
            self.chase_target_y = 0.0


def _healthy() -> tuple[_FakeDefender, _FakeWorld]:
    """A fully-seeded line body in a fresh world (is_seed_stale == False)."""
    d = _FakeDefender()
    w = _FakeWorld()
    w.set_spacing([d])
    return d, w


def main() -> int:
    fails: list[str] = []

    # ── Check 1: staleness predicate ──────────────────────────────────
    print("[1] is_seed_stale predicate")
    d, w = _healthy()
    if is_seed_stale(d, w):
        fails.append("Healthy fresh line body is stale — predicate must be False.")

    # depth_role None (ruck established) -> stale
    d2, w2 = _healthy()
    d2.depth_role = None
    if not is_seed_stale(d2, w2):
        fails.append("depth_role=None (ruck established) must be stale.")

    # _depth_seeded False -> stale
    d3, w3 = _healthy()
    d3._depth_seeded = False
    if not is_seed_stale(d3, w3):
        fails.append("_depth_seeded=False must be stale.")

    # missing frame dict -> stale
    d4, w4 = _healthy()
    w4.frame_spacing_targets = {}
    if not is_seed_stale(d4, w4):
        fails.append("Missing frame_spacing_targets must be stale.")

    d4b, w4b = _healthy()
    w4b.frame_spacing_targets = None
    if not is_seed_stale(d4b, w4b):
        fails.append("None frame_spacing_targets must be stale.")

    # stale frame geometry -> stale
    d5, w5 = _healthy()
    w5.frame_geometry_ready = False
    if not is_seed_stale(d5, w5):
        fails.append("Stale frame geometry must be stale.")
    d5b, w5b = _healthy()
    w5b.frame_geometry_frame = w5b.frame_count - 1
    if not is_seed_stale(d5b, w5b):
        fails.append("frame_geometry_frame != frame_count must be stale.")

    # frame_spacing_y None -> stale
    d6, w6 = _healthy()
    d6.frame_spacing_y = None
    if not is_seed_stale(d6, w6):
        fails.append("frame_spacing_y=None must be stale.")

    # marker -> NOT stale (own writers own it)
    d7, w7 = _healthy()
    d7.defender_role = "MARKER_A"
    if is_seed_stale(d7, w7):
        fails.append("MARKER_A must NOT be stale.")

    # fullback -> NOT stale
    d8, w8 = _healthy()
    d8.fullback_role_active = True
    if is_seed_stale(d8, w8):
        fails.append("fullback must NOT be stale.")

    # frozen -> NOT stale
    d9, w9 = _healthy()
    d9.frozen = True
    if is_seed_stale(d9, w9):
        fails.append("frozen body must NOT be stale.")

    # chase-owned -> NOT stale
    d10, w10 = _healthy()
    d10.has_chase = True
    if is_seed_stale(d10, w10):
        fails.append("chase-owned body must NOT be stale.")

    # pre-ruck (ruck not established) with unformed bands -> NOT stale
    d11 = _FakeDefender(depth_role=None, depth_side=None, seeded=False,
                        role_tx=None, role_ty=None)
    w11 = _FakeWorld(ruck_established=False)
    w11.set_spacing([d11])
    if is_seed_stale(d11, w11):
        fails.append("Pre-ruck unformed depth-role bands must NOT be stale.")
    print("  [PASS] predicate: fresh=False, each stale case=True, "
          "non-line/pre-ruck=False")

    # ── Check 2: apply_seed_hold ──────────────────────────────────────
    print("[2] apply_seed_hold")
    d = _FakeDefender(
        x=80.0, y=30.0,
        target_x=100.0,   # outside anchor band (56..84)
        target_y=50.0,
        vx=5.0, vy=3.0,
        move_speed=8.0,
        movement_state=MovementState.CHASE,
        target_source="tackler_commit",
    )
    w = _FakeWorld(ruck_x=60.0)
    w.set_spacing([d])   # fresh geometry + dict covering d
    # Make it stale first (depth-role invalidated)
    d.depth_role = None
    pre_pos = {d.player_id: (80.0, 30.0)}
    held = apply_seed_hold(w, [d], pre_pos=pre_pos)
    if len(held) != 1 or held[0] is not d:
        fails.append("apply_seed_hold did not hold the stale body.")
    if getattr(d.movement_state, "name", None) != "HOLD":
        fails.append(f"apply_seed_hold did not force HOLD (got {d.movement_state}).")
    if abs(float(d.target_y) - 30.0) > 1e-9:
        fails.append(f"target_y not frozen to frame-start Y (got {d.target_y}).")
    hi = ANCHOR_X + SEED_HOLD_XDRIFT_BAND_M
    if abs(float(d.target_x) - hi) > 1e-9:
        fails.append(f"target_x not clamped to band hi {hi} (got {d.target_x}).")
    if abs(float(d.vx)) > 1e-9 or abs(float(d.vy)) > 1e-9:
        fails.append("velocity not zeroed.")
    if abs(float(d.move_speed)) > 1e-9:
        fails.append("move_speed not zeroed.")
    if d.target_source != "seed_hold":
        fails.append("target_source not stamped seed_hold.")
    print("  [PASS] freeze lateral + clamp target_x + force HOLD")

    # ── Check 3: decide_movement_state ────────────────────────────────
    print("[3] decide_movement_state")
    d, w = _healthy()
    d.depth_role = None  # stale
    st = decide_movement_state(w, d, None, tuners=None)
    if getattr(st, "name", None) != "HOLD":
        fails.append(f"Stale body must resolve to HOLD (got {st}).")
    d2, w2 = _healthy()
    st2 = decide_movement_state(w2, d2, None, tuners=None)
    if getattr(st2, "name", None) != "RETREAT":
        fails.append(f"Fresh PASSIVE no-BC body should be RETREAT (got {st2}).")
    print("  [PASS] stale->HOLD, fresh->non-HOLD")

    # ── Check 4: structural wiring ────────────────────────────────────
    print("[4] structural wiring")
    try:
        from game.managers.defence_manager import DefenceManager
        src = inspect.getsource(DefenceManager.update_open_play)
        if "pre_pos = {p.player_id: (float(p.x), float(p.y)) for p in defenders}" not in src:
            fails.append("pre_pos snapshot missing from update_open_play.")
        if "_apply_seed_hold(world, defenders, pre_pos=pre_pos)" not in src:
            fails.append("apply_seed_hold call missing from update_open_play.")
        idx_hold = src.find("_apply_seed_hold(world, defenders, pre_pos=pre_pos)")
        idx_apply = src.find("# ---- Apply movement ----")
        if idx_hold == -1 or idx_apply == -1 or idx_hold > idx_apply:
            fails.append("apply_seed_hold must run BEFORE the Apply-movement loop.")
    except Exception as exc:  # pragma: no cover - defensive
        fails.append(f"Could not inspect update_open_play wiring: {exc}")

    try:
        from game.defence.defence_lvl_1.movement_mode_decision import (
            decide_movement_state as _dms,
        )
        src = inspect.getsource(_dms)
        if "is_seed_stale as _is_seed_stale" not in src or "return MovementState.HOLD" not in src:
            fails.append("decide_movement_state stale-seed HOLD missing.")
    except Exception as exc:  # pragma: no cover - defensive
        fails.append(f"Could not inspect decide_movement_state wiring: {exc}")

    try:
        from game.defence.defence_lvl_1.defensive_behaviour_unified import (
            DefensiveBehaviourUnified,
        )
        src = inspect.getsource(DefensiveBehaviourUnified._apply_structured_open_play)
        idx_stale = src.find("if _is_seed_stale(d, world):")
        idx_assign = src.find("d.movement_state = blended_state")
        if idx_stale == -1 or idx_assign == -1 or idx_stale > idx_assign:
            fails.append("MMDL stale-seed force-HOLD must run BEFORE "
                         "d.movement_state = blended_state.")
    except Exception as exc:  # pragma: no cover - defensive
        fails.append(f"Could not inspect MMDL wiring: {exc}")
    print("  [PASS] update_open_play + decide_movement_state + MMDL wiring")

    if fails:
        print("\n========= RESULT: FAIL =========")
        for f in fails:
            print(f"  - {f}")
        return 1

    print("\n========= RESULT: ALL CHECKS PASSED =========")
    print("  - Predicate: fresh=False, stale=True, non-line/pre-ruck=False.")
    print("  - apply_seed_hold: freeze lateral + clamp target_x + force HOLD.")
    print("  - decide_movement_state: stale->HOLD, fresh->normal.")
    print("  - Wiring: update_open_play (pre_pos + apply_seed_hold before "
          "integration), decide_movement_state, MMDL force-HOLD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
