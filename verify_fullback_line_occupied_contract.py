"""verify_fullback_line_occupied_contract.py — THE DEFENSIVE-LINE OCCUPANCY
GATE ("the fullback runs in on a kick return with no line break", 2026-09-29).

THE DEFECT THIS PINS
--------------------
``world/catch_system.py::_perform_catch`` seeds the post-catch line from the
BALL CARRIER:

    world.ruck_x_m = bc.x
        -> reset_anchor_authority + compute_and_lock_anchor
        -> line anchor = ruck_x + 10 = bc.x + 10, LOCKED

On a kickoff reception that is not a defensive line at all: the kicking team
has NOT formed one, it is still downfield, GOAL-SIDE of that anchor.  The
carrier then only has to run ~11 m to trip the DEPTH leg

    G10a  bc.x > anchor + FB_LINE_BREAK_MARGIN_M (1.0)

and it is classified FB_BREAK_DEPTH on a line NOBODY OCCUPIES.  The fullback
is granted emergency tackle candidacy, goes CHASE at the governed ceiling and
runs the carrier down.

MEASURED (probe_fb_kick_return_admission.py, seed 20260827; report
tmp_fb_kick_return_admission.txt / tmp_kra_ab.txt):

    frame   gain    bc_x   d_marg   live    dep  lat  emg   near  bhd  ahd
    215     0.00     8.25  -11.00   NONE      0    0    0      0    0   12
    309    11.03    19.28    0.03   DEPTH     0    0    0      0    0   12
    780    30.91    39.16    0.08   DEPTH     1    0    1      8    8    4

The DEPTH leg first fires at f=309 with the carrier 11.03 m past the
reception, and on that frame ALL TWELVE other defenders are still AHEAD of
the anchor — none is behind it and none is on it.  Six of eight swept seeds
reproduced the admission through this leg (``lateral_from`` was ``never`` in
every seed), first firing at depth frames 298-320, i.e. ~95-115 frames after
the catch EVERY time.  The second row is the contrast: at f=780 the carrier
has genuinely carried the line and EIGHT bodies sit behind the anchor.

CONTRACT UNDER TEST (the law)
-----------------------------
A LINE THE DEFENCE HAS NOT REACHED CANNOT BE BROKEN.  A break verdict
therefore requires the anchor to be a line the defence actually HOLDS:
``fullback_defensive_line_occupied`` reports True when at least one other
defender sits at or behind ``anchor_x + FB_LINE_OCCUPIED_BAND_M`` (2.0).

The gate is a CONJUNCT on every classify consumer, never a rewrite of the
classifier, and it is scoped to ``world.defending_team``: a world carrying no
readable population (every verifier stub, the detached geometry harness)
keeps the historical contract byte-for-byte.

Legs:
  [1] The measured fixture: the reception-seeded anchor, and the occupancy
      count that separates f=309 from f=780.
  [2] ``fullback_defensive_line_occupied`` — the band, the self-exclusion,
      the empty-world passthrough, malformed input, and the lever.
  [3] The classifier is UNCHANGED — the phantom anchor still classifies
      DEPTH; only the conjunct refuses it.
  [4] All four consumers refuse the phantom break and admit the genuine one.
  [5] The REAL geometry writer: phantom -> sweeper DRIFT + anchor pin;
      occupied -> CHASE on the carrier's pursuit point.
  [6] The lever-OFF control reproduces the pre-change engine.
  [7] Source wiring.
  [8] No regression: no readable population keeps the historical verdict.

Run:  python verify_fullback_line_occupied_contract.py
Exit code 0 = a manufactured line can no longer admit the last defender.
"""
from __future__ import annotations

import math
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.movement_states import MovementState

from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    classify_fullback_break,
    compute_fullback_anchor,
    fullback_breakaway_live,
    fullback_defensive_line_occupied,
    fullback_depth_break_active,
    fullback_emergency_defender_active,
    fullback_lateral_break_active,
    FB_BREAK_DEPTH,
    FB_CHASE_TRAIL_M,
    FB_CHASE_LEAD_MAX_M,
    FB_LATERAL_BREAK_M,
    FB_LATERAL_BREAK_RADIUS_M,
    FB_LINE_BREAK_MARGIN_M,
    FB_LINE_OCCUPIED_BAND_M,
    FB_LINE_OCCUPIED_ENV_VAR,
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
# THE MEASURED FIXTURE
# ======================================================================
# The reception frame: catch_system seeds ruck = bc.x = 8.25, so the locked
# anchor is 18.25.
CATCH_X: float = 8.25
PHANTOM_ANCHOR_X: float = 18.25
PHANTOM_BC_X: float = 19.28          # f=309, gain 11.03
PHANTOM_BC_Y: float = 20.18
PHANTOM_DEPTH_MARGIN: float = 0.03   # bc.x - (anchor + 1.0), the probe's dp
PHANTOM_DEFENDER_XS = tuple(
    25.0 + 2.0 * i for i in range(12)          # all 12 AHEAD of 20.25
)

# The genuine break (f=780 / f=781): the carrier has carried the line.
REAL_ANCHOR_X: float = 38.07
REAL_BC_X: float = 39.16
REAL_BC_Y: float = 21.82
REAL_DEFENDER_XS = tuple(
    [30.0 + 0.95 * i for i in range(8)]        # 8 bodies BEHIND 38.07
    + [40.0 + 1.6 * i for i in range(4)]       # 4 still ahead
)

FB_X: float = 47.50
FB_Y: float = 19.31


class _Ball:
    """A stationary carrier: the command is the axis-margin trail point."""

    def __init__(self, x: float, y: float) -> None:
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
    """Minimal world carrying the fields the FB authority reads."""

    def __init__(self, anchor_x: float, bc: _Ball | None = None,
                 team: tuple = (), frame: int = 1) -> None:
        self.ruck_x_m = anchor_x - 10.0
        self.ruck_y_m = 25.0
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


def occupancy_of(defender_xs, anchor_x: float) -> tuple[int, int, int]:
    """(near, behind, ahead) for a defender X list, as the probe counts it."""
    near = behind = ahead = 0
    for x in defender_xs:
        dx = x - anchor_x
        if abs(dx) <= 3.0:
            near += 1
        if dx < 0.0:
            behind += 1
        else:
            ahead += 1
    return near, behind, ahead


# ======================================================================
# [1] THE MEASURED FIXTURE
# ======================================================================

def sec_fixture() -> None:
    section(1, "The measured fixture: a reception-seeded anchor")
    check("the reception seeds ruck_x from the carrier",
          abs((PHANTOM_ANCHOR_X - 10.0) - CATCH_X) < 1e-9,
          f"ruck={PHANTOM_ANCHOR_X - 10.0} catch={CATCH_X}")
    check("the carrier is 11.03 m past the reception when DEPTH first fires",
          abs((PHANTOM_BC_X - CATCH_X) - 11.03) < 1e-9,
          f"gain={PHANTOM_BC_X - CATCH_X:.2f}")
    check("...which matches the probe's recorded depth margin to 1e-2",
          abs((PHANTOM_BC_X - (PHANTOM_ANCHOR_X + FB_LINE_BREAK_MARGIN_M))
              - PHANTOM_DEPTH_MARGIN) < 1e-2,
          f"d_margin={PHANTOM_BC_X - (PHANTOM_ANCHOR_X + 1.0):.4f}")

    near, behind, ahead = occupancy_of(PHANTOM_DEFENDER_XS, PHANTOM_ANCHOR_X)
    check("PHANTOM: every one of the 12 defenders is AHEAD of the anchor",
          behind == 0 and ahead == 12 and near == 0,
          f"near={near} behind={behind} ahead={ahead}")

    near_r, behind_r, ahead_r = occupancy_of(REAL_DEFENDER_XS, REAL_ANCHOR_X)
    check("GENUINE: the carrier has carried the line - 8 bodies behind it",
          behind_r == 8, f"near={near_r} behind={behind_r} ahead={ahead_r}")
    check("GENUINE: the carrier is past the anchor on that frame too",
          REAL_BC_X > REAL_ANCHOR_X + FB_LINE_BREAK_MARGIN_M,
          f"bc={REAL_BC_X} anchor+margin={REAL_ANCHOR_X + 1.0:.2f}")

    print(f"    phantom occ near/behind/ahead = {near}/{behind}/{ahead}")
    print(f"    genuine occ near/behind/ahead = {near_r}/{behind_r}/{ahead_r}")


# ======================================================================
# [2] THE PREDICATE
# ======================================================================

def sec_predicate() -> None:
    section(2, "fullback_defensive_line_occupied")
    saved = fb.FB_LINE_OCCUPIED_REQUIRED
    try:
        fb.FB_LINE_OCCUPIED_REQUIRED = True
        fb_body = make_fb()

        phantom_world = _World(
            PHANTOM_ANCHOR_X, _Ball(PHANTOM_BC_X, PHANTOM_BC_Y),
            tuple([fb_body] + [_Body(x) for x in PHANTOM_DEFENDER_XS]),
        )
        real_world = _World(
            REAL_ANCHOR_X, _Ball(REAL_BC_X, REAL_BC_Y),
            tuple([fb_body] + [_Body(x) for x in REAL_DEFENDER_XS]),
        )

        check("PHANTOM -> NOT occupied", not fullback_defensive_line_occupied(
            phantom_world, PHANTOM_ANCHOR_X, fb_body))
        check("GENUINE -> occupied", fullback_defensive_line_occupied(
            real_world, REAL_ANCHOR_X, fb_body))

        # THE SELF-EXCLUSION: the sweeper is the last defender, not a line
        # holder, so its own X must never make an empty line look occupied.
        # Place the FULLBACK exactly ON the anchor band and keep the only
        # other body far downfield: if the sweeper counted, the anchor would
        # report occupied.  It must not.
        on_line_fb = make_fb(x=PHANTOM_ANCHOR_X + 1.0, y=FB_Y, pid=202)
        excl_world = _World(PHANTOM_ANCHOR_X,
                            _Ball(PHANTOM_BC_X, PHANTOM_BC_Y),
                            (on_line_fb, _Body(60.0)))
        check("the tagged fullback itself never counts as a line holder",
              not fullback_defensive_line_occupied(
                  excl_world, PHANTOM_ANCHOR_X, on_line_fb),
              f"fb.x={on_line_fb.x} anchor={PHANTOM_ANCHOR_X}")
        check("...but an UNTAGGED body in the same place does",
              fullback_defensive_line_occupied(
                  _World(PHANTOM_ANCHOR_X, _Ball(0.0, 0.0),
                         (_Body(PHANTOM_ANCHOR_X + 1.0),)),
                  PHANTOM_ANCHOR_X))

        # THE BAND.
        for offset, expected in ((FB_LINE_OCCUPIED_BAND_M, True),
                                 (FB_LINE_OCCUPIED_BAND_M + 0.5, False),
                                 (-FB_LINE_OCCUPIED_BAND_M - 5.0, True)):
            anchor = 50.0
            w = _World(anchor, _Ball(REAL_BC_X, REAL_BC_Y),
                       (_Body(anchor + offset),))
            check(f"a body at anchor{offset:+.2f} -> occupied={expected}",
                  fullback_defensive_line_occupied(w, anchor) is expected,
                  f"band={FB_LINE_OCCUPIED_BAND_M}")

        # SCOPE: no readable population keeps the historical contract.
        for label, w in (
            ("empty tuple", _World(PHANTOM_ANCHOR_X, _Ball(0.0, 0.0), ())),
            ("missing attribute", type("W", (), {"ruck_x_m": 0.0})()),
        ):
            check(f"an inadmissible world ({label}) reports occupied "
                  "(the historical contract)", 
                  fullback_defensive_line_occupied(w, PHANTOM_ANCHOR_X)
                  is True)

        # TOTALS: malformed input never silences a break.
        for bad_world in (None, _World(PHANTOM_ANCHOR_X, None, ())):
            check(f"a degenerate world ({type(bad_world).__name__}) is "
                  "tolerated", fullback_defensive_line_occupied(
                      bad_world, PHANTOM_ANCHOR_X) is True)
        for bad_anchor in (None, "junk", float("nan"), float("inf")):
            check(f"a malformed anchor ({bad_anchor!r}) reports occupied",
                  fullback_defensive_line_occupied(
                      phantom_world, bad_anchor, fb_body) is True)

        # A body with an unreadable X is skipped, not fatal: both bodies
        # here sit far downfield, so neither holds the anchor and the single
        # readable body decides the verdict.
        weird = _World(PHANTOM_ANCHOR_X, _Ball(0.0, 0.0),
                       (_Body(60.0), _Body(63.0)))
        weird.defending_team[0].x = "junk"
        check("an unreadable body X is skipped without raising",
              fullback_defensive_line_occupied(
                  weird, PHANTOM_ANCHOR_X, fb_body) is False)
        check("...and the readable body alone can still hold the line",
              fullback_defensive_line_occupied(
                  _World(PHANTOM_ANCHOR_X, _Ball(0.0, 0.0),
                         (_Body(17.0),)), PHANTOM_ANCHOR_X))

        # THE LEVER.
        fb.FB_LINE_OCCUPIED_REQUIRED = False
        check("[OFF] the phantom anchor reports occupied (the historical "
              "engine)",
              fullback_defensive_line_occupied(
                  phantom_world, PHANTOM_ANCHOR_X, fb_body) is True)
    finally:
        fb.FB_LINE_OCCUPIED_REQUIRED = saved

    check("the band is the documented pin",
          abs(FB_LINE_OCCUPIED_BAND_M - 2.0) < 1e-12,
          str(FB_LINE_OCCUPIED_BAND_M))
    check("the lever has a named env var",
          FB_LINE_OCCUPIED_ENV_VAR == "RLM_FB_LINE_OCCUPIED",
          FB_LINE_OCCUPIED_ENV_VAR)
# ======================================================================
# [3] THE CLASSIFIER IS UNCHANGED
# ======================================================================

def sec_classifier_unchanged() -> None:
    section(3, "The classifier is unchanged; only the conjunct refuses")
    defender = make_fb()
    phantom_bc = _Ball(PHANTOM_BC_X, PHANTOM_BC_Y)

    check("the phantom anchor still CLASSIFIES as a DEPTH break",
          classify_fullback_break(
              phantom_bc, defender, PHANTOM_ANCHOR_X, PHANTOM_BC_Y
          ) == FB_BREAK_DEPTH,
          "the gate must be a separate conjunct, not a rewrite")

    # The same anchor, one metre short of the margin, is not a break at all.
    short = _Ball(PHANTOM_ANCHOR_X + 0.5, PHANTOM_BC_Y)
    check("...and an anchor the carrier has NOT reached is not a break",
          classify_fullback_break(
              short, defender, PHANTOM_ANCHOR_X, PHANTOM_BC_Y
          ) == fb.FB_BREAK_NONE)

    # The LATERAL leg is untouched too: a channel breach with a live reach
    # still classifies, and the occupancy gate is what refuses the phantom.
    lat_bc = _Ball(PHANTOM_ANCHOR_X + 0.5, PHANTOM_BC_Y + 9.0)
    lat_fb = make_fb(x=PHANTOM_ANCHOR_X + 4.0, y=PHANTOM_BC_Y)
    kind = classify_fullback_break(
        lat_bc, lat_fb, PHANTOM_ANCHOR_X, PHANTOM_BC_Y
    )
    check("a lateral channel breach inside the reach still classifies",
          kind in (FB_BREAK_DEPTH, fb.FB_BREAK_LATERAL), kind)
    check("...and the lateral reach constant is the documented pin",
          abs(FB_LATERAL_BREAK_RADIUS_M - 25.0) < 1e-12
          and abs(FB_LATERAL_BREAK_M - 5.0) < 1e-12,
          f"radius={FB_LATERAL_BREAK_RADIUS_M} breach={FB_LATERAL_BREAK_M}")


# ======================================================================
# [4] EVERY CONSUMER
# ======================================================================

def sec_consumers() -> None:
    section(4, "Every classify consumer refuses the phantom break")
    saved = fb.FB_LINE_OCCUPIED_REQUIRED
    try:
        fb.FB_LINE_OCCUPIED_REQUIRED = True
        fb_body = make_fb()

        for label, anchor, bc, xs, expected in (
            ("PHANTOM", PHANTOM_ANCHOR_X, _Ball(PHANTOM_BC_X, PHANTOM_BC_Y),
             PHANTOM_DEFENDER_XS, False),
            ("GENUINE", REAL_ANCHOR_X, _Ball(REAL_BC_X, REAL_BC_Y),
             REAL_DEFENDER_XS, True),
        ):
            team = tuple([fb_body] + [_Body(x) for x in xs])
            w = _World(anchor, bc, team)

            depth = fullback_depth_break_active(w, fb_body, bc)
            live = fullback_breakaway_live(w, fb_body)
            emergency = fullback_emergency_defender_active(w, fb_body, bc)
            lateral = fullback_lateral_break_active(w, fb_body, bc)

            check(f"[{label}] fullback_depth_break_active == {expected}",
                  depth is expected, str(depth))
            check(f"[{label}] fullback_breakaway_live == {expected} "
                  "(the pin and the chase agree)", live is expected,
                  str(live))
            check(f"[{label}] fullback_emergency_defender_active == "
                  f"{expected} (tackle candidacy follows)", 
                  emergency is expected, str(emergency))
            check(f"[{label}] fullback_lateral_break_active is False "
                  "(an X break is never a channel breach)",
                  lateral is False, str(lateral))

        # THE LEVER-OFF CONTROL on the same fixture.
        fb.FB_LINE_OCCUPIED_REQUIRED = False
        w = _World(PHANTOM_ANCHOR_X, _Ball(PHANTOM_BC_X, PHANTOM_BC_Y),
                   tuple([fb_body] + [_Body(x) for x in PHANTOM_DEFENDER_XS]))
        check("[OFF] the phantom break is admitted again on every consumer",
              fullback_depth_break_active(w, fb_body, w.ball_carrier)
              and fullback_breakaway_live(w, fb_body)
              and fullback_emergency_defender_active(
                  w, fb_body, w.ball_carrier),
              "the pre-change engine is reproduced")
    finally:
        fb.FB_LINE_OCCUPIED_REQUIRED = saved


# ======================================================================
# [5] THE REAL GEOMETRY WRITER
# ======================================================================

def writer_state(anchor_x: float, bc: _Ball, xs) -> tuple[str, float, float]:
    """Drive the REAL writer; return (state, target_x, sweeper_anchor_x)."""
    fb_body = make_fb()
    world = _World(anchor_x, bc, tuple([fb_body] + [_Body(x) for x in xs]))
    apply_fullback_geometry(world, fb_body)
    anchor_x_written, _anchor_y = compute_fullback_anchor(bc, anchor_x)
    return (fb.movement_state_name(fb_body), float(fb_body.target_x),
            float(anchor_x_written))


def sec_writer() -> None:
    section(5, "The REAL geometry writer on both fixtures")
    saved = fb.FB_LINE_OCCUPIED_REQUIRED
    try:
        fb.FB_LINE_OCCUPIED_REQUIRED = True

        p_state, p_tx, p_anchor = writer_state(
            PHANTOM_ANCHOR_X, _Ball(PHANTOM_BC_X, PHANTOM_BC_Y),
            PHANTOM_DEFENDER_XS,
        )
        print(f"    PHANTOM  state={p_state!r} tx={p_tx:.4f} "
              f"sweeper_anchor={p_anchor:.4f}")
        check("[PHANTOM] the writer does NOT put the fullback into CHASE",
              p_state != "CHASE", repr(p_state))
        check("[PHANTOM] ...the actor holds the sweeper contract "
              "(DRIFT / RECOVER)",
              p_state in ("DRIFT", "RECOVER"), repr(p_state))
        check("[PHANTOM] ...and the command is the sweeper DEPTH anchor, "
              "not the carrier",
              abs(p_tx - p_anchor) < 1e-9,
              f"tx={p_tx:.4f} anchor={p_anchor:.4f}")
        check("[PHANTOM] ...which is NOT the pursuit point",
              abs(p_tx - (PHANTOM_BC_X - FB_CHASE_TRAIL_M)) > 1.0,
              f"tx={p_tx:.4f} pursuit={PHANTOM_BC_X - FB_CHASE_TRAIL_M:.4f}")

        r_state, r_tx, r_anchor = writer_state(
            REAL_ANCHOR_X, _Ball(REAL_BC_X, REAL_BC_Y), REAL_DEFENDER_XS,
        )
        print(f"    GENUINE  state={r_state!r} tx={r_tx:.4f} "
              f"sweeper_anchor={r_anchor:.4f}")
        check("[GENUINE] the writer DOES chase a genuine break",
              r_state == "CHASE", repr(r_state))
        check("[GENUINE] ...onto the carrier's interception point",
              abs(r_tx - REAL_BC_X) <= 1e-9,
              f"tx={r_tx:.4f} intercept={REAL_BC_X:.4f}")
        check("[GENUINE] ...and the pursuit point is bounded by the "
              "documented lead",
              abs(r_tx - REAL_BC_X) <= FB_CHASE_LEAD_MAX_M + 1e-9,
              f"tx={r_tx:.4f}")
        check("[GENUINE] the A/B is real: the two fixtures disagree",
              abs(r_tx - p_tx) > 8.0, f"phantom={p_tx:.4f} real={r_tx:.4f}")

        # Determinism.
        runs = [writer_state(REAL_ANCHOR_X, _Ball(REAL_BC_X, REAL_BC_Y),
                             REAL_DEFENDER_XS) for _ in range(3)]
        check("the gated path is deterministic across repeated runs",
              all(r == runs[0] for r in runs), str(runs[0]))
        runs_p = [writer_state(PHANTOM_ANCHOR_X, _Ball(PHANTOM_BC_X,
                                                       PHANTOM_BC_Y),
                               PHANTOM_DEFENDER_XS) for _ in range(3)]
        check("...and so is the refused path",
              all(r == runs_p[0] for r in runs_p), str(runs_p[0]))
    finally:
        fb.FB_LINE_OCCUPIED_REQUIRED = saved


# ======================================================================
# [6] THE LEVER-OFF CONTROL
# ======================================================================

def sec_lever_off() -> None:
    section(6, "RLM_FB_LINE_OCCUPIED=0 reproduces the pre-change engine")
    saved = fb.FB_LINE_OCCUPIED_REQUIRED
    try:
        fb.FB_LINE_OCCUPIED_REQUIRED = False
        state, tx, anchor = writer_state(
            PHANTOM_ANCHOR_X, _Ball(PHANTOM_BC_X, PHANTOM_BC_Y),
            PHANTOM_DEFENDER_XS,
        )
        print(f"    [OFF] state={state!r} tx={tx:.4f} anchor={anchor:.4f}")
        check("[OFF] the phantom anchor put the fullback back into CHASE",
              state == "CHASE", repr(state))
        check("[OFF] ...onto the carrier's interception point",
              abs(tx - PHANTOM_BC_X) <= 1e-9,
              f"tx={tx:.4f}")
        check("[OFF] ...so the refusal is the gate's doing, not the "
              "fixture's", abs(tx - anchor) > 1.0,
              f"tx={tx:.4f} anchor={anchor:.4f}")

        # With the lever off, an OCCUPIED line is byte-identical to lever on.
        fb.FB_LINE_OCCUPIED_REQUIRED = True
        on = writer_state(REAL_ANCHOR_X, _Ball(REAL_BC_X, REAL_BC_Y),
                          REAL_DEFENDER_XS)
        fb.FB_LINE_OCCUPIED_REQUIRED = False
        off = writer_state(REAL_ANCHOR_X, _Ball(REAL_BC_X, REAL_BC_Y),
                           REAL_DEFENDER_XS)
        check("on a GENUINE break the two lever states are byte-identical",
              on == off, f"on={on} off={off}")
    finally:
        fb.FB_LINE_OCCUPIED_REQUIRED = saved
# ======================================================================
# [7] SOURCE WIRING
# ======================================================================

def sec_wiring() -> None:
    section(7, "Source wiring")
    with open("game/defence/defence_lvl_1/fullback_role.py",
              encoding="utf-8") as handle:
        fb_src = handle.read()

    check("the gate exists and is named for what it does",
          "def fullback_defensive_line_occupied" in fb_src)
    check("the lever is declared with a named env var",
          'FB_LINE_OCCUPIED_ENV_VAR: str = "RLM_FB_LINE_OCCUPIED"' in fb_src)
    check("the occupancy band is a named constant",
          "FB_LINE_OCCUPIED_BAND_M: float = 2.0" in fb_src)
    check("the law is stated at the declaration",
          "THE LAW: a line the defence has not reached cannot be broken."
          in fb_src)
    check("the measured occupancy counts are recorded",
          "ALL TWELVE other defenders are still AHEAD of the anchor"
          in fb_src)
    check("the self-exclusion is documented in the predicate",
          "the tagged fullback itself is never counted" in fb_src)
    check("the empty-world passthrough is documented",
          "NO readable defending population reports True" in fb_src)

    conjunct = ("return fullback_defensive_line_occupied"
                "(world, _test_anchor, defender)")
    check("THREE consumers gate on the latched test anchor",
          fb_src.count(conjunct) >= 3,
          f"count={fb_src.count(conjunct)}")
    check("the geometry writer gates its CHASE on the anchor",
          "if break_kind != FB_BREAK_NONE and "
          "fullback_defensive_line_occupied(" in fb_src)
    check("...and that gate is documented as the line-exists rule",
          "THE LINE MUST EXIST TO BE BROKEN" in fb_src)
    check("the breakaway-live pin is gated too (the disagreeing-verdict "
          "defect)",
          "Holding this pin to the same law as" in fb_src)

    # THE CLASSIFIER ITSELF IS UNTOUCHED.
    check("the classifier still returns FB_BREAK_DEPTH purely on geometry",
          "return FB_BREAK_DEPTH" in fb_src
          and "fullback_defensive_line_occupied" not in
          fb_src.split("def classify_fullback_break")[1].split(
              "\ndef ")[0])


# ======================================================================
# [8] NO REGRESSION
# ======================================================================

def sec_no_regression() -> None:
    section(8, "No regression: an inadmissible world keeps the contract")
    saved = fb.FB_LINE_OCCUPIED_REQUIRED
    try:
        fb.FB_LINE_OCCUPIED_REQUIRED = True

        # A stub world with NO defending_team: the historical verdict must
        # survive byte-for-byte, because that is what every other verifier
        # and the detached geometry harness present.
        stub = _World(PHANTOM_ANCHOR_X, _Ball(PHANTOM_BC_X, PHANTOM_BC_Y), ())
        body = make_fb()
        check("a world with no team still admits the break (stubs are safe)",
              fullback_depth_break_active(stub, body, stub.ball_carrier)
              and fullback_breakaway_live(stub, body)
              and fullback_emergency_defender_active(
                  stub, body, stub.ball_carrier),
              "the historical contract")

        stub_state, stub_tx, _ = writer_state(
            PHANTOM_ANCHOR_X, _Ball(PHANTOM_BC_X, PHANTOM_BC_Y), ())
        check("...and the writer still chases on such a world",
              stub_state == "CHASE", repr(stub_state))
        check("...to the interception point the current engine produces",
              abs(stub_tx - PHANTOM_BC_X) <= 1e-9,
              f"tx={stub_tx:.4f}")

        # THE LEVER-OFF IDENTITY on the same stub.
        fb.FB_LINE_OCCUPIED_REQUIRED = False
        off_state, off_tx, _ = writer_state(
            PHANTOM_ANCHOR_X, _Ball(PHANTOM_BC_X, PHANTOM_BC_Y), ())
        check("the stub contract is identical under both lever states",
              (off_state, off_tx) == (stub_state, stub_tx),
              f"on=({stub_state!r}, {stub_tx:.4f}) "
              f"off=({off_state!r}, {off_tx:.4f})")

        # A garbage world must not raise anywhere on the path.
        class _Broken:
            pass

        fb.FB_LINE_OCCUPIED_REQUIRED = True
        for bad in (None, _Broken()):
            try:
                fullback_defensive_line_occupied(bad, 10.0)
                ok = True
            except Exception as exc:            # noqa: BLE001
                ok = False
                print(f"      raised: {exc!r}")
            check(f"the predicate tolerates a degenerate world "
                  f"({type(bad).__name__})", ok)
    finally:
        fb.FB_LINE_OCCUPIED_REQUIRED = saved


def main() -> int:
    print("FULLBACK DEFENSIVE-LINE OCCUPANCY GATE CONTRACT "
          "(kick-return admission, 2026-09-29)")
    print("=" * 68)
    sec_fixture()
    sec_predicate()
    sec_classifier_unchanged()
    sec_consumers()
    sec_writer()
    sec_lever_off()
    sec_wiring()
    sec_no_regression()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS - a reception-seeded line the defence does not")
    print("        occupy can no longer be 'broken': on the measured")
    print("        fixture the carrier ran 11.03 m and tripped the DEPTH")
    print("        leg with all twelve defenders still AHEAD of the")
    print("        anchor, and the fullback ran the carrier down.  Every")
    print("        classify consumer (geometry writer, breakaway pin,")
    print("        depth/lateral break predicates, emergency candidacy)")
    print("        now requires at least one other defender at or behind")
    print("        the anchor, while a world with no readable defending")
    print("        population keeps the historical contract and")
    print("        RLM_FB_LINE_OCCUPIED=0 restores the pre-change engine")
    print("        write-for-write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
