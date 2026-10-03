"""verify_fullback_chase_hold_contract.py — THE LATCH-BACKED CHASE HOLD
(2026-10-01).

THE DEFECT THIS PINS
--------------------
``apply_fullback_geometry``'s breakaway branch is guarded by TWO conjuncts:

    break_kind != FB_BREAK_NONE  AND  fullback_defensive_line_occupied(...)

and returns to the SWEEPER branch (``enter_fullback_intent(SWEEP)`` ->
``MovementState.DRIFT`` + ``target_x = anchor_x``) whenever EITHER refuses.
The anchor-relock latch (2026-09-28) floors the ANCHOR the classifier reads
but does NOT guarantee the VERDICT, so a frame can still fail the guard
while the chase the latch armed is live.  The measured residual is the
authority-timeline probe's ``MODE_OVERWRITE`` class: the same writer replaces
CHASE with DRIFT under a live chase, mid-episode, with no engine event.

THE LAW
-------
While a latch is live it is AUTHORITY FOR PURSUIT OWNERSHIP.  The four
reversal classes the guard can suffer are:

    N1  ``carrier_behind_latched_line``   -- RELEASE (the hold ends)
    N2  the OCCUPANCY gate refuses        -- HELD
    N3  the LINE-INTACT LEVEL exclusion   -- implies N1 -> released
    N4  the 2D REACH test                 -- implies N1 -> released

N3/N4 are only reachable with the carrier level with / behind the test
anchor, so they are released by N1 by construction; N2 is reachable with the
carrier clearly past the latched line and is the case the hold keeps.  The
hold REUSES the existing pursuit command path byte-for-byte
(``fullback_chase_point`` + the CHASE intent) and commands the LATCHED kind,
so the chase point is the one the arming frame produced.  It releases ONLY
through the existing lifecycle clears (``clear_fullback_chase_state`` at
RELEASE entry / the miss teardown, tag loss, the engagement reset) or N1.
``classify_fullback_break`` itself is NOT made sticky and is not touched.

Legs:
  [1] The N2 fixture: occupancy refused under a live latch -> CHASE on the
      exact pursuit point, byte-for-byte the existing command law.
  [2] The latched KIND is commanded (DEPTH vs LATERAL floor).
  [3] N1 releases the hold (carrier behind the latched line -> sweeper).
  [4] No latch / no carrier -> no hold (the historical contract).
  [5] The lever: RLM_FB_BREAK_LATCH=0 is a no-op on the same fixture.
  [6] The lifecycle clears end the hold (cycle-end sweep, tag loss, reset).
  [7] Purity / totality of the hold readers.
  [8] Source wiring -- the writer consults the hold; the classifier is
      untouched; the occupancy conjunct literal survives.

Run:  python verify_fullback_chase_hold_contract.py
Exit code 0 = a live latch is authority for pursuit ownership.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from player.movement_states import MovementState

from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    arm_fullback_break_latch,
    carrier_behind_latched_line,
    classify_fullback_break,
    clear_fullback_break_latch,
    clear_fullback_chase_state,
    compute_fullback_anchor,
    fullback_chase_hold_kind,
    fullback_chase_hold_live,
    fullback_chase_point,
    is_fullback_break_latched,
    latched_fullback_break_kind,
    FB_BREAK_DEPTH,
    FB_BREAK_LATERAL,
    FB_BREAK_LATCH_KIND_ATTR,
    FB_CHASE_TRAIL_M,
)

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)
    return bool(condition)


def section(n, title: str) -> None:
    print(f"\n[{n}] {title}")


# ======================================================================
# FIXTURES
# ======================================================================
# The sweeper's own line anchor and the carrier that has clearly carried it.
ANCHOR_X: float = 20.0
LATCHED_X: float = ANCHOR_X - 4.0        # the anchor the chase was armed on
BC_X: float = 30.0
BC_Y: float = 35.0
FB_X: float = 33.0
FB_Y: float = 35.0


class _Ball:
    """A stationary carrier: no velocity -> the axis-margin trail point."""

    def __init__(self, x: float, y: float = BC_Y) -> None:
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0


class _Body:
    """A non-fullback defender: an X the occupancy gate can read."""

    def __init__(self, x: float, y: float = 25.0) -> None:
        self.x = x
        self.y = y
        self.fullback_role_active = False


class _World:
    def __init__(self, anchor_x: float, bc, team=(), frame: int = 1) -> None:
        self.ruck_x_m = anchor_x - 10.0
        self.ruck_y_m = BC_Y
        self.defensive_line_anchor = anchor_x
        self.anchor_locked = True
        self.ball_carrier = bc
        self.frame_count = frame
        self.tes_active = False
        self.in_kickoff = False
        self.fullback_recover_allowed = False
        self.defending_team = team


def make_fb(x: float = FB_X, y: float = FB_Y, pid: int = 201):
    from player.player import Player, DefenderRole

    p = Player(x=x, y=y, player_id=pid)
    p.jersey_number = 1
    p.max_speed_mps = 9.2
    p.max_defensive_speed_mps = 8.3
    p.fullback_role_active = True
    p.can_acquire_tackle = True
    p.defender_role = DefenderRole.DEF_LINE_READY
    p.tes_attached = False
    p.in_contact = False
    p._tackle_commit_active = False
    p.target_x = x
    p.target_y = y
    return p


# N2: every other defender is AHEAD of ``anchor + band`` -> NOT occupied.
AHEAD = tuple(_Body(40.0 + i) for i in range(6))
# OCCUPIED: one body behind the line -> the anchor is held.
OCCUPIED = tuple([_Body(18.0)] + [_Body(40.0 + i) for i in range(5)])


def run_writer(anchor_x: float, bc_x: float, team, latch_kind=None,
               latch_x: float | None = None):
    """Drive the REAL writer once; return (state, target_x, target_y)."""
    defender = make_fb()
    if latch_kind is not None:
        arm_fullback_break_latch(
            defender, latch_kind,
            LATCHED_X if latch_x is None else latch_x,
        )
    world = _World(anchor_x, _Ball(bc_x), team)
    apply_fullback_geometry(world, defender)
    return (
        fb.movement_state_name(defender),
        float(defender.target_x),
        float(defender.target_y),
        defender,
    )


# ======================================================================
# [1] THE N2 FIXTURE
# ======================================================================

def sec_n2_hold() -> None:
    section(1, "N2: occupancy refused under a live latch -> CHASE held")
    # The pure classifier still returns DEPTH: the carrier is clearly past
    # the (latched) test anchor -- only the OCCUPANCY conjunct refuses.
    probe = make_fb()
    arm_fullback_break_latch(probe, FB_BREAK_DEPTH, LATCHED_X)
    from game.defence.defence_lvl_1.fullback_role import (
        break_test_line_anchor_x, fullback_defensive_line_occupied,
    )
    test_anchor = break_test_line_anchor_x(
        _World(ANCHOR_X, _Ball(BC_X)), probe, ANCHOR_X,
    )
    check("the latched test anchor floors the live line",
          abs(test_anchor - LATCHED_X) < 1e-9, f"{test_anchor:.4f}")
    check("the classifier still says DEPTH (only occupancy refuses)",
          classify_fullback_break(
              _Ball(BC_X), probe, test_anchor, BC_Y
          ) == FB_BREAK_DEPTH)
    check("the occupancy conjunct refuses on this fixture",
          fullback_defensive_line_occupied(
              _World(ANCHOR_X, _Ball(BC_X), AHEAD), test_anchor, probe
          ) is False)

    # The hold predicate + writer.
    check("the hold is live on the latched body",
          fullback_chase_hold_live(probe, _Ball(BC_X)) is True)
    check("the hold commands the LATCHED kind",
          fullback_chase_hold_kind(probe, _Ball(BC_X)) == FB_BREAK_DEPTH)

    state, tx, ty, _d = run_writer(
        ANCHOR_X, BC_X, AHEAD, latch_kind=FB_BREAK_DEPTH,
    )
    check("the writer HOLDS the chase (CHASE, not SWEEP)",
          state == "CHASE", repr(state))
    # BYTE-FOR-BYTE: the existing pursuit command law, with the SAME anchor
    # the live breakaway branch passes (compute_fullback_anchor on the LIVE
    # line anchor).
    sweeper_x, _sweeper_y = compute_fullback_anchor(_Ball(BC_X), ANCHOR_X)
    exp_x, exp_y = fullback_chase_point(
        _Ball(BC_X), probe, sweeper_x, FB_BREAK_DEPTH,
    )
    check("...onto the existing pursuit point, byte-for-byte",
          abs(tx - exp_x) < 1e-9 and abs(ty - exp_y) < 1e-9,
          f"tx={tx:.4f} expected={exp_x:.4f}")
    check("...not the sweeper anchor (the fall-through this removes)",
          abs(tx - sweeper_x) > 1.0, f"tx={tx:.4f} anchor={sweeper_x:.4f}")

    # OCCUPIED control: the live branch owns it, hold not needed.
    state_o, tx_o, _ty_o, _d = run_writer(
        ANCHOR_X, BC_X, OCCUPIED, latch_kind=FB_BREAK_DEPTH,
    )
    check("[occupied] the live breakaway branch chases as before",
          state_o == "CHASE" and abs(tx_o - exp_x) < 1e-9,
          f"state={state_o!r} tx={tx_o:.4f}")


# ======================================================================
# [2] THE LATCHED KIND IS COMMANDED
# ======================================================================

def sec_latched_kind() -> None:
    section(2, "The hold commands the LATCHED kind")
    # A LATERAL latch floors the predicted interception point at the
    # sweeper's own depth; a DEPTH latch does not.
    _s_d, tx_depth, _ty, _d = run_writer(
        ANCHOR_X, BC_X, AHEAD, latch_kind=FB_BREAK_DEPTH,
    )
    _s_l, tx_lat, _ty2, _d2 = run_writer(
        ANCHOR_X, BC_X, AHEAD, latch_kind=FB_BREAK_LATERAL,
    )
    sweeper_x, _sy = compute_fullback_anchor(_Ball(BC_X), ANCHOR_X)
    check("a DEPTH latch commands the un-floored pursuit point",
          abs(tx_depth - BC_X) < 1e-9,
          f"{tx_depth:.4f}")
    check("a LATERAL latch floors the point at the sweeper depth",
          abs(tx_lat - max(BC_X, sweeper_x)) < 1e-9,
          f"{tx_lat:.4f} floor={sweeper_x:.4f}")
    check("...so the two latched kinds are genuinely distinguished",
          abs(tx_lat - tx_depth) > 1.0, f"d={tx_depth:.4f} l={tx_lat:.4f}")

    # A malformed / absent kind falls back to DEPTH (total).
    junk = make_fb(pid=299)
    arm_fullback_break_latch(junk, FB_BREAK_DEPTH, LATCHED_X)
    setattr(junk, FB_BREAK_LATCH_KIND_ATTR, "JUNK")
    check("an unknown latched kind reads None (closed vocabulary)",
          latched_fullback_break_kind(junk) is None)
    check("...and the hold falls back to DEPTH",
          fullback_chase_hold_kind(junk, _Ball(BC_X)) == FB_BREAK_DEPTH)


# ======================================================================
# [3] N1 RELEASES THE HOLD
# ======================================================================

def sec_n1_release() -> None:
    section(3, "N1 (carrier behind the latched line) releases the hold")
    probe = make_fb()
    arm_fullback_break_latch(probe, FB_BREAK_DEPTH, LATCHED_X)
    check("a carrier clearly past the latch is held",
          fullback_chase_hold_live(probe, _Ball(BC_X)) is True)
    check("a carrier behind the latch releases the hold",
          fullback_chase_hold_live(
              probe, _Ball(LATCHED_X - 5.0)
          ) is False)
    check("...which is exactly the N1 predicate",
          carrier_behind_latched_line(probe, _Ball(LATCHED_X - 5.0)) is True)

    # Through the REAL writer: N1 -> sweeper on the sweeper anchor.
    behind_x = LATCHED_X - 5.0
    state, tx, _ty, _d = run_writer(
        ANCHOR_X, behind_x, AHEAD, latch_kind=FB_BREAK_DEPTH,
    )
    anchor_x, _ay = compute_fullback_anchor(_Ball(behind_x), ANCHOR_X)
    check("N1 drives the writer to the sweeper contract",
          state in ("DRIFT", "RECOVER") and state != "CHASE", repr(state))
    check("...onto the sweeper depth anchor",
          abs(tx - anchor_x) < 1e-9, f"tx={tx:.4f} anchor={anchor_x:.4f}")


# ======================================================================
# [4] NO LATCH / NO CARRIER -> NO HOLD
# ======================================================================

def sec_no_hold() -> None:
    section(4, "No latch / no carrier keeps the historical contract")
    plain = make_fb()
    check("an unlatched body has no hold",
          fullback_chase_hold_live(plain, _Ball(BC_X)) is False
          and fullback_chase_hold_kind(plain, _Ball(BC_X)) is None)
    # Unlatched, the N2 fixture falls through to the sweeper (unchanged).
    state, tx, _ty, _d = run_writer(ANCHOR_X, BC_X, AHEAD)
    anchor_x, _ay = compute_fullback_anchor(_Ball(BC_X), ANCHOR_X)
    check("an unlatched refused break still sweeps (unchanged)",
          state in ("DRIFT", "RECOVER") and abs(tx - anchor_x) < 1e-9,
          f"state={state!r} tx={tx:.4f}")

    latched = make_fb(pid=202)
    arm_fullback_break_latch(latched, FB_BREAK_DEPTH, LATCHED_X)
    check("no ball carrier -> no hold (nothing to pursue)",
          fullback_chase_hold_live(latched, None) is False
          and fullback_chase_hold_kind(latched, None) is None)
    # ...and the writer falls through to the sweeper on a carrier-less frame.
    state_nc, _tx, _ty, _d = run_writer(ANCHOR_X, BC_X, AHEAD,
                                        latch_kind=FB_BREAK_DEPTH)
    world = _World(ANCHOR_X, None, AHEAD)
    defender = make_fb(pid=203)
    arm_fullback_break_latch(defender, FB_BREAK_DEPTH, LATCHED_X)
    apply_fullback_geometry(world, defender)
    check("...the carrier-less latched body sweeps",
          fb.movement_state_name(defender) in ("DRIFT", "RECOVER"),
          repr(fb.movement_state_name(defender)))


# ======================================================================
# [5] THE LEVER
# ======================================================================

def sec_lever() -> None:
    section(5, "RLM_FB_BREAK_LATCH=0 is a no-op (the historical engine)")
    saved = fb.FB_BREAK_LATCH
    try:
        fb.FB_BREAK_LATCH = False
        body = make_fb(pid=204)
        arm_fullback_break_latch(body, FB_BREAK_DEPTH, LATCHED_X)  # inert
        check("[OFF] no latch can be armed", not is_fullback_break_latched(body))
        check("[OFF] the hold is dark",
              fullback_chase_hold_live(body, _Ball(BC_X)) is False
              and fullback_chase_hold_kind(body, _Ball(BC_X)) is None)
        state, tx, _ty, _d = run_writer(ANCHOR_X, BC_X, AHEAD)
        anchor_x, _ay = compute_fullback_anchor(_Ball(BC_X), ANCHOR_X)
        check("[OFF] the N2 fixture sweeps (byte-identical to pre-change)",
              state in ("DRIFT", "RECOVER") and abs(tx - anchor_x) < 1e-9,
              f"state={state!r} tx={tx:.4f} anchor={anchor_x:.4f}")
    finally:
        fb.FB_BREAK_LATCH = saved


# ======================================================================
# [6] THE LIFECYCLE CLEARS END THE HOLD
# ======================================================================

def sec_lifecycle() -> None:
    section(6, "The existing lifecycle clears end the hold")
    # Cycle-end sweep (RELEASE entry / miss teardown).
    body = make_fb(pid=205)
    arm_fullback_break_latch(body, FB_BREAK_DEPTH, LATCHED_X)
    world = _World(ANCHOR_X, _Ball(BC_X), (body, AHEAD[0]))
    check("the hold is live before the cycle ends",
          fullback_chase_hold_live(body, _Ball(BC_X)) is True)
    clear_fullback_chase_state(world)
    check("clear_fullback_chase_state (cycle end) drops the hold",
          fullback_chase_hold_live(body, _Ball(BC_X)) is False)

    # The single-body clear + tag loss.
    body2 = make_fb(pid=206)
    arm_fullback_break_latch(body2, FB_BREAK_DEPTH, LATCHED_X)
    clear_fullback_break_latch(body2)
    check("clear_fullback_break_latch drops the hold",
          fullback_chase_hold_live(body2, _Ball(BC_X)) is False)


# ======================================================================
# [7] PURITY / TOTALITY
# ======================================================================

def sec_purity() -> None:
    section(7, "The hold readers are pure and total")
    body = make_fb(pid=207)
    arm_fullback_break_latch(body, FB_BREAK_DEPTH, LATCHED_X)
    before = (body.x, body.y, body.target_x, body.target_y,
              fb.latched_fullback_break_anchor_x(body))
    for _ in range(3):
        fullback_chase_hold_live(body, _Ball(BC_X))
        fullback_chase_hold_kind(body, _Ball(BC_X))
        latched_fullback_break_kind(body)
    after = (body.x, body.y, body.target_x, body.target_y,
             fb.latched_fullback_break_anchor_x(body))
    check("the readers never mutate the body", before == after)

    class _Broken:
        pass

    for bad in (None, _Broken()):
        try:
            fullback_chase_hold_live(bad, _Ball(BC_X))
            fullback_chase_hold_kind(bad, _Ball(BC_X))
            latched_fullback_break_kind(bad)
            ok = True
        except Exception as exc:            # noqa: BLE001
            ok = False
            print(f"      raised: {exc!r}")
        check(f"a degenerate body ({type(bad).__name__}) is tolerated", ok)

    class _BadBall:
        x = "junk"
        y = None

    try:
        fullback_chase_hold_live(body, _BadBall())
        ok = True
    except Exception as exc:                # noqa: BLE001
        ok = False
        print(f"      raised: {exc!r}")
    check("an unreadable carrier is tolerated", ok)


# ======================================================================
# [8] SOURCE WIRING
# ======================================================================

def sec_wiring() -> None:
    section(8, "Source wiring")
    with open("game/defence/defence_lvl_1/fullback_role.py",
              encoding="utf-8") as handle:
        src = handle.read()

    check("the hold predicate exists",
          "def fullback_chase_hold_live" in src)
    check("the hold-kind reader exists",
          "def fullback_chase_hold_kind" in src)
    check("the latched-kind reader exists",
          "def latched_fullback_break_kind" in src)
    check("the writer consults the hold",
          "fullback_chase_hold_kind(defender, bc)" in src)
    check("the writer commands the effective chase kind",
          "arm_fullback_break_latch(defender, _chase_kind, _break_test_anchor)"
          in src
          or "arm_fullback_break_latch(defender, break_kind, _break_test_anchor)"
          in src)
    check("the writer selects the hold kind in the else arm",
          "_chase_kind = fullback_chase_hold_kind(defender, bc)" in src)
    check("N1 is the stated release",
          "if carrier_behind_latched_line(defender, bc):" in src)
    check("the law is stated at the declaration",
          "A LIVE LATCH IS AUTHORITY FOR PURSUIT OWNERSHIP" in src
          or "it is AUTHORITY for pursuit ownership" in src)

    # THE CLASSIFIER IS UNTOUCHED: it must not consult the latch or the hold.
    classifier = src.split("def classify_fullback_break")[1].split("\ndef ")[0]
    check("classify_fullback_break is NOT made sticky",
          "is_fullback_break_latched" not in classifier
          and "fullback_chase_hold" not in classifier
          and "latched_fullback_break" not in classifier)

    # THE OCCUPANCY CONJUNCT LITERAL SURVIVES (pinned by the line-occupied
    # contract §7).
    check("the occupancy conjunct literal is preserved",
          "if break_kind != FB_BREAK_NONE and "
          "fullback_defensive_line_occupied(" in src)


def main() -> int:
    print("FULLBACK LATCH-BACKED CHASE HOLD CONTRACT (2026-10-01)")
    print("=" * 68)
    sec_n2_hold()
    sec_latched_kind()
    sec_n1_release()
    sec_no_hold()
    sec_lever()
    sec_lifecycle()
    sec_purity()
    sec_wiring()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS - a live latch is authority for pursuit ownership:")
    print("        the geometry writer holds the CHASE the latch granted on")
    print("        an occupancy reversal (N2) even though the classifier's")
    print("        guard refuses, reusing the existing pursuit command path")
    print("        byte-for-byte; N1 (carrier_behind_latched_line) and the")
    print("        existing lifecycle clears release it; the classifier is")
    print("        not made sticky; and RLM_FB_BREAK_LATCH=0 leaves the")
    print("        historical engine write-for-write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
