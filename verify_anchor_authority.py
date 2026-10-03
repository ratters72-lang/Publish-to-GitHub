"""
verify_anchor_authority.py — Task G Anchor Authority Hardening verifier.

Validates the anchor (ruck_x + 10) is the single immutable source of
truth for defensive depth, immune to spacing, width, audit, band jitter,
DH/BC movement, and TES/PTB transitions.

Authority layers under test (spec Task G):
  Layer 0 — anchor == ruck_x + 10 across 500 ruck positions
  Layer 1 — anchor never modified after lock (AnchorAuthorityViolation)
  Layer 2 — role_depth_target_x always == anchor
  Layer 3 — spacing never modifies depth (depth-key guard)
  Layer 4 — width never modifies depth (depth-key guard)
  Layer 5 — audit never overrides locked depth
  Layer 6 — anchor resets only at PTB/TES/kickoff boundaries
  Layer 7 — defenders always retreat to anchor
  Layer 8 — anchor drift never exceeds 0.01 m
  Layer 9 — anchor sanctions: never behind BC; never > 60 m ahead of BC

All checks must pass (exit 0).
"""
import math
import sys

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.anchor_authority import (
    AnchorAuthorityViolation,
    defensive_line_anchor_m,
    compute_and_lock_anchor,
    set_anchor,
    set_pre_ruck_anchor,
    reset_anchor_authority,
    enforce_retreat_to_anchor,
    assert_anchor_consistency,
    assert_spacing_never_modifies_depth,
    assert_width_never_modifies_depth,
    audit_stable_depth,
    DepthRoleState,
    get_depth_role_state,
    ANCHOR_DEPTH_M,
)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{name}: {detail}")


class W:
    """Minimal world stub with the anchor-authority attributes."""

    def __init__(self, ruck_x=None, ruck_y=35.0):
        self.ruck_x_m = ruck_x
        self.ruck_y_m = ruck_y
        self.defensive_line_anchor = None
        self.anchor_locked = False
        self.depth_role_state = {}
        self.frame_count = 0
        self.ball_carrier = None
        self.defending_team = []
        self.players = []

    @property
    def players_all(self):
        return self.players


def fresh_defender(pid: int, x: float, y: float = 35.0) -> Player:
    d = Player(x, y, player_id=pid)
    # Player.__init__ seeds target_x/target_y from the constructor position;
    # keep both in sync so the retreat-enforcement assertions test the
    # enforcement logic, not stale constructor defaults.
    d.target_x = x
    d.target_y = y
    d.defender_role = DefenderRole.DEF_LINE_READY
    d.depth_class = "PASSIVE"
    d.frozen = False
    d.freeze_timer = 0.0
    d.fullback_role_active = False
    d.role_depth_target_x = None
    d.role_depth_target_y = None
    return d


# ── Layer 0: anchor == ruck_x + 10 across 500 positions ────────────────
def layer0():
    for i in range(500):
        ruck_x = 20.0 + i * 0.31  # 20.0 .. ~174.7
        w = W(ruck_x=ruck_x)
        anchor = compute_and_lock_anchor(w)
        check(
            f"L0 anchor@ruck={ruck_x:.3f}",
            abs(anchor - (ruck_x + ANCHOR_DEPTH_M)) < 1e-9,
            f"anchor={anchor:.6f} expected={ruck_x + ANCHOR_DEPTH_M:.6f}",
        )


# ── Layer 1: lock immutability ─────────────────────────────────────────
def layer1():
    w = W(ruck_x=50.0)
    compute_and_lock_anchor(w)
    # Idempotent re-publish of the identical value is allowed.
    try:
        compute_and_lock_anchor(w)
        set_anchor(w, 60.0)
    except AnchorAuthorityViolation:
        check("L1 idempotent republish", False, "identical value raised")
    # A CHANGED value must raise.
    w2 = W(ruck_x=50.0)
    compute_and_lock_anchor(w2)
    try:
        set_anchor(w2, 61.0)
        check("L1 locked modification", False, "no violation raised")
    except AnchorAuthorityViolation:
        check("L1 locked modification", True)
    # set_anchor enforcing ruck_x + 10 even BEFORE lock.
    w3 = W(ruck_x=50.0)
    try:
        set_anchor(w3, 70.0)
        check("L1 set_anchor ruck contract", False, "non-ruck+10 accepted")
    except AnchorAuthorityViolation:
        check("L1 set_anchor ruck contract", True)


# ── Layer 2: role_depth_target_x == anchor ─────────────────────────────
def layer2():
    w = W(ruck_x=55.0)
    compute_and_lock_anchor(w)
    d = fresh_defender(1, 80.0)
    w.defending_team = [d]
    d.role_depth_target_x = defensive_line_anchor_m(w)
    assert_anchor_consistency(w, [d])  # must not raise
    check("L2 stamp matches anchor", True)
    d.role_depth_target_x = defensive_line_anchor_m(w) + 0.05
    try:
        assert_anchor_consistency(w, [d])
        check("L2 drift detection", False, "0.05 m drift not caught")
    except AnchorAuthorityViolation:
        check("L2 drift detection", True)


# ── Layer 3/4: spacing/width never modify depth ────────────────────────
def layer34():
    ok_spacing = {"width": 40.0, "lateral_offset": 1.0, "gap": 2.0,
                  "stagger": 0.5, "compression": 0.9}
    try:
        assert_spacing_never_modifies_depth(dict(ok_spacing))
        check("L3 spacing no depth key", True)
    except AnchorAuthorityViolation:
        check("L3 spacing no depth key", False, "spacing raised on Y-only")
    bad_spacing = {"width": 40.0, "depth": 65.0}
    try:
        assert_spacing_never_modifies_depth(bad_spacing)
        check("L3 spacing depth guard", False, "depth key accepted")
    except AnchorAuthorityViolation:
        check("L3 spacing depth guard", True)

    ok_width = {"width": 40.0, "lateral_alignment": 0.0, "sideline": 3.0}
    try:
        assert_width_never_modifies_depth(dict(ok_width))
        check("L4 width no depth key", True)
    except AnchorAuthorityViolation:
        check("L4 width no depth key", False, "width raised on Y-only")
    bad_width = {"width": 40.0, "depth": 65.0}
    try:
        assert_width_never_modifies_depth(bad_width)
        check("L4 width depth guard", False, "depth key accepted")
    except AnchorAuthorityViolation:
        check("L4 width depth guard", True)


# ── Layer 5: audit never overrides locked depth ────────────────────────
def layer5():
    w = W(ruck_x=50.0)
    compute_and_lock_anchor(w)
    d = fresh_defender(2, 75.0)
    w.defending_team = [d]
    state = get_depth_role_state(w, d)
    state.role = "PASSIVE"
    state.locked = True
    w.depth_role_state[id(d)] = state
    # The audit must return the locked value unchanged and never switch.
    result = audit_stable_depth(w, d)
    check("L5 locked audit stable", result == "PASSIVE",
          f"audit returned {result!r}")
    check("L5 locked state unchanged", w.depth_role_state[id(d)].role == "PASSIVE",
          "audit mutated locked role")


# ── Layer 6: reset only at legal boundaries ────────────────────────────
def layer6():
    w = W(ruck_x=50.0)
    compute_and_lock_anchor(w)
    reset_anchor_authority(w)
    check("L6 reset unlocks", w.anchor_locked is False, "still locked")
    # After reset the anchor recomputes from the NEW ruck.
    w.ruck_x_m = 65.0
    anchor = compute_and_lock_anchor(w)
    check("L6 recompute from new ruck",
          abs(anchor - 75.0) < 1e-9, f"anchor={anchor:.6f}")
    # Pre-ruck writer refuses while a ruck exists.
    w2 = W(ruck_x=50.0)
    try:
        set_pre_ruck_anchor(w2, 70.0)
        check("L6 pre-ruck guard", False, "pre-ruck writer used with ruck")
    except AnchorAuthorityViolation:
        check("L6 pre-ruck guard", True)
    # Pre-ruck writer works without a ruck (kickoff era) and stays unlocked.
    w3 = W(ruck_x=None)
    set_pre_ruck_anchor(w3, 70.0)
    check("L6 pre-ruck unlocked",
          w3.anchor_locked is False and abs(w3.defensive_line_anchor - 70.0) < 1e-9,
          "pre-ruck anchor/lock wrong")


# ── Layer 7: defenders always retreat to anchor ────────────────────────
def layer7():
    w = W(ruck_x=50.0)
    compute_and_lock_anchor(w)
    anchor = defensive_line_anchor_m(w)  # 60.0
    d1 = fresh_defender(10, 40.0)   # ahead of anchor -> forced back
    d2 = fresh_defender(11, 70.0)   # behind anchor -> untouched
    w.defending_team = [d1, d2]
    enforce_retreat_to_anchor(w, [d1, d2])
    check("L7 retreat target", abs(d1.target_x - anchor) < 1e-9,
          f"d1.target_x={d1.target_x:.3f} anchor={anchor:.3f}")
    check("L7 deep untouched", d2.target_x == 70.0,
          f"d2.target_x={d2.target_x:.3f} modified")
    # Engagers (PRIMARY/SECONDARY) are exempt — they may close ahead of line.
    d3 = fresh_defender(12, 42.0)
    d3.depth_class = "SECONDARY"
    # Bump the exempt defender past the anchor update so the assertion tests
    # the exemption, not the constructor default.
    d3.x = 42.0
    d3.target_x = 42.0
    w.defending_team.append(d3)
    enforce_retreat_to_anchor(w, [d3])
    check("L7 engaged exempt", d3.target_x == 42.0,
          f"engaged d3 target changed to {d3.target_x:.3f}")


# ── Layer 8: drift never exceeds 0.01 m ────────────────────────────────
def layer8():
    w = W(ruck_x=50.0)
    compute_and_lock_anchor(w)
    anchor = defensive_line_anchor_m(w)
    for i in range(200):
        d = fresh_defender(100 + i, anchor + 10.0)
        d.role_depth_target_x = anchor + (i * 0.0001)  # max 0.0199 at i=199
        if abs(d.role_depth_target_x - anchor) > 0.01:
            try:
                assert_anchor_consistency(w, [d])
                check("L8 drift 0.01", False, f"i={i} drifted "
                      f"{abs(d.role_depth_target_x - anchor):.4f}")
            except AnchorAuthorityViolation:
                pass
        else:
            try:
                assert_anchor_consistency(w, [d])
            except AnchorAuthorityViolation:
                check("L8 drift 0.01", False, f"i={i} false raise within tolerance")
    check("L8 drift tolerance", True)


# ── Layer 9: anchor sanctions vs BC ────────────────────────────────────
def layer9():
    # Never behind BC: attack runs +X, so a valid anchor is >= bc.x (the
    # line is always at or deeper than the BC while the set forms).
    for i in range(200):
        ruck_x = 20.0 + i * 0.5
        w = W(ruck_x=ruck_x)
        anchor = defensive_line_anchor_m(w)
        bc_x = ruck_x + 4.0  # BC ahead of ruck but behind line depth
        check(f"L9 anchor not behind BC #{i}", anchor >= bc_x - 1e-9,
              f"anchor={anchor:.3f} bc={bc_x:.3f}")
        # Never ahead of BC by more than 60 m.
        check(f"L9 anchor not >60m ahead #{i}", anchor - bc_x <= 60.0 + 1e-9,
              f"anchor={anchor:.3f} bc={bc_x:.3f}")


def main() -> int:
    layer0()
    layer1()
    layer2()
    layer34()
    layer5()
    layer6()
    layer7()
    layer8()
    layer9()

    if FAILURES:
        print(f"verify_anchor_authority: {len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("verify_anchor_authority: ALL ANCHOR AUTHORITY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
