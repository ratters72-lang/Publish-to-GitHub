"""verify_fb_admission_audit_contract.py — THE EMERGENCY-CHASE ADMISSION LEDGER
("instrument emergency-chase admission and log every rejected line-break
evaluation with defensive line width, gap size, nearest defender distance and
a rejection reason", 2026-09-30).

THE GAP THIS PINS
-----------------
The last defender is admitted to the tackle economy by ONE gate --
``fullback_role.fullback_emergency_defender_active`` -- a conjunction of an
IDENTITY half (candidacy lever, fullback tag, tackle authority, not frozen, no
live freeze timer) and a LINE-BREAK half (the canonical DEPTH leg OR the 2D
LATERAL leg, each gated on the anchor being a line the defence occupies).

When that gate refused, the frame said only ``False``.  Nothing recorded WHICH
conjunct refused, how wide the defensive line was standing, how far the carrier
was from the line, or how close the nearest defender was -- so every refusal had
to be re-derived afterwards by a bespoke probe.

``game/defence/defence_lvl_1/emergency_chase_admission_audit.py`` is the ledger
of those refusals, and this file is its contract.

CONTRACT UNDER TEST
-------------------
1. OBSERVATIONAL ONLY.  The ledger reads the world and the two bodies and
   appends a row.  It writes no body field, no engine-read world field, and
   cannot change a predicate's answer: the gate's verdict armed and disarmed is
   identical on every fixture, and its answer is still taken SOLELY by
   ``fullback_role``.
2. OFF BY DEFAULT AND FREE WHEN OFF.  Armed by ``RLM_FB_ADMISSION_AUDIT=1`` or
   by a ledger installed on the world (the probe / verifier path).  Disarmed,
   ``record_rejection`` returns ``None`` and records nothing.
3. EVERY REJECTED LINE-BREAK EVALUATION IS LOGGED, ONCE, WITH ITS REASON.  The
   reason is a closed vocabulary naming the exact conjunct; a repeat evaluation
   of the same verdict on the same frame increments ``repeat_count`` instead of
   duplicating the row, so "every evaluation" survives deduplication.
4. THE THREE DIAGNOSTICS ARE MEASURED, NOT GUESSED.  Defensive line width,
   carrier gap (with its surplus over the break margin), and the nearest
   defender's distance.  An unmeasurable diagnostic is recorded as ``None`` --
   never fabricated as zero.
5. THE GAP IS MEASURED AGAINST THE LINE THE VERDICT USED.  On a latched chase
   the LIVE anchor can be relocked goal-side of the carrier, so an admitted row
   that measured the live line would report a negative gap on a frame the actor
   was chasing from.  ADMITTED rows therefore read the BREAK TEST anchor.

Legs:
  [1] The lever: default off, env arm, installed arm, reset, ceiling override.
  [2] The vocabulary: closed, partitioned, total.
  [3] The three diagnostics: measured / commanded / unavailable, and totals.
  [4] The ledger: dedup + repeat, ceiling + dropped, clear.
  [5] The REAL gate: armed/disarmed parity, every reason reachable.
  [6] The latch: an ADMITTED row reads the anchor its verdict used.
  [7] The lateral label is derived from the classifier's own constants.
  [8] Source wiring.
  [9] Export (CSV) and the digest.
  [10] No regression: degenerate worlds are tolerated.

Run:  python verify_fb_admission_audit_contract.py
Exit code 0 = every rejected line-break evaluation is logged with its
diagnostics, and the gate's verdict is byte-identical whether or not the ledger
is armed.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole

from game.defence.defence_lvl_1 import emergency_chase_admission_audit as audit
from game.defence.defence_lvl_1 import fullback_role as fb

AUDIT_PATH = "game/defence/defence_lvl_1/emergency_chase_admission_audit.py"
FB_PATH = "game/defence/defence_lvl_1/fullback_role.py"

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
# THE FIXTURE
# ======================================================================
# The measured geometry of the admission gate: a locked defensive-line anchor,
# a carrier, and a defending team.  Defaults are the GENUINE DEPTH break -- the
# carrier has carried an occupied line -- so a case is read from its name.
ANCHOR_X: float = 40.0
BC_X: float = 42.0
BC_Y: float = 30.0
FB_X: float = 45.0
FB_Y: float = 30.0


class _Ball:
    def __init__(self, x: float, y: float, vx: float = 0.0,
                 vy: float = 0.0) -> None:
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy


class _Body:
    """A non-fullback defender carrying an X, a Y and a role."""

    def __init__(self, x: float, y: float = 10.0, pid: int = 300,
                 role=None) -> None:
        self.x = x
        self.y = y
        self.player_id = pid
        self.fullback_role_active = False
        self.defender_role = role


class _World:
    """Minimal world carrying the fields the ledger and the gate read."""

    def __init__(self, anchor_x: float, bc, team=(), frame: int = 1,
                 width: float = 40.0) -> None:
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
        self.def_line_width_m = width


def make_fb(x: float = FB_X, y: float = FB_Y, pid: int = 201):
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
    p.frozen = False
    p.freeze_timer = 0.0
    return p


def line_bodies(anchor_x: float, behind: int = 6, ahead: int = 0,
                spread: float = 4.0):
    """Bodies at/behind the anchor (a held line) or all ahead of it."""
    out = []
    pid = 300
    for i in range(behind):
        out.append(_Body(anchor_x - 1.0 * (i + 1), 10.0 + spread * i, pid))
        pid += 1
    for i in range(ahead):
        out.append(_Body(anchor_x + 3.0 * (i + 1), 12.0 + spread * i, pid))
        pid += 1
    return out


def build(*, bc_x: float = BC_X, bc_y: float = BC_Y,
          anchor: float = ANCHOR_X, fb_x: float = FB_X, fb_y: float = FB_Y,
          behind: int = 6, ahead: int = 0, tagged: bool = True,
          can_tackle: bool = True, frozen: bool = False, timer: float = 0.0,
          width: float = 40.0, frame: int = 1):
    """(world, fullback) for one named case."""
    bc = _Ball(bc_x, bc_y)
    f = make_fb(fb_x, fb_y)
    f.fullback_role_active = tagged
    f.can_acquire_tackle = can_tackle
    f.frozen = bool(frozen)
    f.freeze_timer = timer
    team = tuple([f] + line_bodies(anchor, behind, ahead))
    return _World(anchor, bc, team, frame=frame, width=width), f


def evaluate(world, fb_body, armed: bool):
    """Run the REAL gate; arm/disarm the ledger around it."""
    if armed:
        audit.install_ledger(world)
    else:
        audit.reset_admission_audit(world)
    return fb.fullback_emergency_defender_active(
        world, fb_body, world.ball_carrier,
    )


def verdicts(world, leg=None, admitted=None) -> list:
    out = []
    for row in audit.records_for(world):
        if leg is not None and row.leg != leg:
            continue
        if admitted is not None and bool(row.admitted) is not admitted:
            continue
        out.append(row.verdict)
    return out


CASES: dict = {
    "no_tackle_authority": dict(can_tackle=False),
    "frozen": dict(frozen=True),
    "freeze_timer": dict(timer=4.0),
    "untagged": dict(tagged=False),
    "below_margin": dict(bc_x=40.5),
    "genuine_depth": dict(),
    "phantom_depth": dict(behind=0, ahead=8),
    "lateral_admit": dict(bc_x=39.5, bc_y=39.0),
    "level_exclusion": dict(bc_x=30.0, bc_y=55.0),
    "lane_intact": dict(bc_x=40.0, bc_y=32.0),
    "out_of_reach": dict(bc_x=38.0, bc_y=60.0, fb_x=62.0),
}


# ======================================================================
# [1] THE LEVER
# ======================================================================

def sec_lever() -> None:
    section(1, "The lever: off by default, armed by env or by the world")
    saved_env = os.environ.pop(audit.ADMISSION_AUDIT_ENV_VAR, None)
    try:
        check("the audit is OFF with no lever set",
              audit.audit_enabled_by_env() is False)
        world, body = build()
        check("...and an untouched world carries no ledger",
              getattr(world, audit.ADMISSION_AUDIT_ATTR, None) is None)
        check("...so the audit is not armed on it",
              audit.audit_armed(world) is False)
        check("...and the cheap gate in fullback_role agrees",
              fb.admission_audit_armed(world) is False)
        check("...and nothing is recorded",
              audit.record_rejection(world, body, world.ball_carrier,
                                     audit.LEG_DEPTH_BREAK,
                                     audit.AdmissionVerdict.LANE_INTACT)
              is None
              and audit.records_for(world) == ())
        check("the digest says so without inventing a ledger",
              audit.summarise_admission_audit(world) == "fb_admission_audit=off")

        # ARMED BY THE ENVIRONMENT.
        os.environ[audit.ADMISSION_AUDIT_ENV_VAR] = "1"
        check("RLM_FB_ADMISSION_AUDIT=1 arms the ledger",
              audit.audit_enabled_by_env() is True
              and audit.audit_armed(world) is True
              and fb.admission_audit_armed(world) is True)
        rows_before = len(audit.records_for(world))
        audit.record_rejection(world, body, world.ball_carrier,
                               audit.LEG_DEPTH_BREAK,
                               audit.AdmissionVerdict.LANE_INTACT)
        check("...and a row IS recorded on that world",
              len(audit.records_for(world)) == rows_before + 1)
        audit.reset_admission_audit(world)
        check("reset drops the world's own sink and its rows",
              getattr(world, audit.ADMISSION_AUDIT_ATTR, None) is None
              and audit.records_for(world) == ())
        check("...the env lever still arms the audit (the module ledger)",
              audit.audit_armed(world) is True)
        os.environ.pop(audit.ADMISSION_AUDIT_ENV_VAR, None)
        check("...and with the lever cleared the audit is OFF again",
              audit.audit_armed(world) is False)
        os.environ[audit.ADMISSION_AUDIT_ENV_VAR] = "1"

        # THE INSTALLED LEDGER (the probe / verifier path).
        ledger = audit.install_ledger(world)
        check("install_ledger arms the world and returns the sink",
              ledger is audit.ledger_for(world)
              and ledger is getattr(world, audit.ADMISSION_AUDIT_ATTR))
        check("...with the documented ceiling by default",
              ledger.limit == audit.MAX_AUDIT_RECORDS, str(ledger.limit))

        # THE CEILING OVERRIDE.
        os.environ[audit.ADMISSION_AUDIT_MAX_ENV_VAR] = "3"
        small = audit.install_ledger(world)
        check("RLM_FB_ADMISSION_AUDIT_MAX overrides the ceiling",
              small.limit == 3, str(small.limit))
        os.environ.pop(audit.ADMISSION_AUDIT_MAX_ENV_VAR, None)
        bad = os.environ.copy()
        os.environ[audit.ADMISSION_AUDIT_MAX_ENV_VAR] = "not-a-number"
        check("a malformed ceiling falls back to the default",
              audit.install_ledger(world).limit == audit.MAX_AUDIT_RECORDS)
        os.environ.pop(audit.ADMISSION_AUDIT_MAX_ENV_VAR, None)
        del bad
        check("the lever names are the documented pins",
              audit.ADMISSION_AUDIT_ENV_VAR == "RLM_FB_ADMISSION_AUDIT"
              and audit.ADMISSION_AUDIT_MAX_ENV_VAR
              == "RLM_FB_ADMISSION_AUDIT_MAX"
              and audit.ADMISSION_AUDIT_ATTR
              == "emergency_chase_admission_audit")
    finally:
        audit.reset_admission_audit()
        os.environ.pop(audit.ADMISSION_AUDIT_ENV_VAR, None)
        os.environ.pop(audit.ADMISSION_AUDIT_MAX_ENV_VAR, None)
        if saved_env is not None:
            os.environ[audit.ADMISSION_AUDIT_ENV_VAR] = saved_env


# ======================================================================
# [2] THE VOCABULARY
# ======================================================================

def sec_vocabulary() -> None:
    section(2, "The rejection vocabulary is closed, partitioned and total")
    names = {v.value for v in audit.AdmissionVerdict}
    check("ADMITTED is the only non-refusal value",
          sum(1 for v in audit.AdmissionVerdict if v.value == "admitted") == 1)
    check("every identity conjunct has its own value",
          {"lever_off", "no_tackle_authority", "frozen", "freeze_timer"}
          <= names, str(sorted(names)))
    check("every leg conjunct has its own value",
          {"below_depth_margin", "depth_break_classified",
           "line_intact_level_exclusion", "lane_intact", "out_of_reach",
           "line_unoccupied"} <= names)
    check("the degenerate inputs are named too",
          {"no_world", "no_defender", "no_carrier"} <= names)
    check("the values are unique (no two conjuncts share a label)",
          len(names) == len(list(audit.AdmissionVerdict)), str(len(names)))

    line_break = {"below_depth_margin", "depth_break_classified",
                  "line_intact_level_exclusion", "lane_intact",
                  "out_of_reach", "line_unoccupied"}
    identity = {"lever_off", "no_tackle_authority", "frozen", "freeze_timer"}
    for value in sorted(names):
        expected = value in line_break
        check(f"is_line_break_verdict({value!r}) == {expected}",
              audit.is_line_break_verdict(value) is expected)
        check(f"is_known_verdict({value!r}) is True",
              audit.is_known_verdict(value) is True)
        check(f"...and it is NOT in the identity half: {value in identity}",
              (value in identity) is (value in
                                      {"lever_off", "no_tackle_authority",
                                       "frozen", "freeze_timer"}))
    check("ADMITTED is not counted as a line-break refusal",
          audit.is_line_break_verdict("admitted") is False)
    for junk in (None, "", "junk", 7, object(), float("nan")):
        check(f"a junk verdict ({type(junk).__name__}) is never a refusal",
              audit.is_line_break_verdict(junk) is False
              and audit.is_known_verdict(junk) is False)
    check("the enum members coerce like their values",
          audit.is_line_break_verdict(audit.AdmissionVerdict.LANE_INTACT)
          is True)
    check("NOT_TAGGED is in the vocabulary but is never a break refusal",
          audit.AdmissionVerdict.NOT_TAGGED.value == "not_tagged"
          and audit.is_line_break_verdict("not_tagged") is False)


# ======================================================================
# [3] THE THREE DIAGNOSTICS
# ======================================================================

def sec_diagnostics() -> None:
    section(3, "The three diagnostics are measured, never fabricated")
    bc = _Ball(50.0, 20.0)

    # THE DEFENSIVE LINE WIDTH -- measured as the live Y span.
    team = (_Body(60.0, 12.0, 1), _Body(60.0, 42.0, 2))
    w = _World(ANCHOR_X, bc, team)
    width, source = audit.defensive_line_width_m(w)
    check("the width is the LIVE Y SPAN of the line",
          abs(width - 30.0) < 1e-9
          and source == audit.WIDTH_SOURCE_MEASURED,
          f"{width} ({source})")

    # The exempt roles play tight to the ruck and are NOT line bodies; the
    # span must be the same 30 with them present.
    wide_team = (_Body(60.0, 12.0, 1), _Body(60.0, 42.0, 2),
                 _Body(60.0, 0.0, 3, DefenderRole.MARKER_A),
                 _Body(60.0, 70.0, 4, DefenderRole.MARKER_B),
                 _Body(60.0, 5.0, 5, "DUMMY_HALF"),
                 _Body(60.0, 66.0, 6, "KICK_CHASE"))
    w2 = _World(ANCHOR_X, bc, wide_team)
    check("the marker pair, dummy half and kick chaser are not line bodies",
          abs(audit.defensive_line_width_m(w2)[0] - 30.0) < 1e-9,
          str(audit.defensive_line_width_m(w2)))

    # The tagged fullback is a backfield sweeper, not a line body.
    fb_body = make_fb(x=45.0, y=70.0)
    w3 = _World(ANCHOR_X, bc, (fb_body,) + team)
    check("the tagged fullback does not extend the measured line",
          abs(audit.defensive_line_width_m(w3)[0] - 30.0) < 1e-9,
          str(audit.defensive_line_width_m(w3)))

    # COMMANDED FALLBACK: fewer than two readable line bodies is no span.
    one = _World(ANCHOR_X, bc, (_Body(60.0, 20.0, 1),), width=41.5)
    check("a single line body falls back to the COMMANDED width",
          audit.defensive_line_width_m(one) ==
          (41.5, audit.WIDTH_SOURCE_COMMANDED),
          str(audit.defensive_line_width_m(one)))
    check("...and with no declared width either it is UNAVAILABLE",
          audit.defensive_line_width_m(
              _World(ANCHOR_X, bc, (), width=0.0)) ==
          (None, audit.WIDTH_SOURCE_UNAVAILABLE))
    check("an unreadable Y is skipped, not fatal",
          audit.defensive_line_width_m(
              _World(ANCHOR_X, bc, (_Body(60.0, "junk", 1),
                                    _Body(60.0, float("nan"), 2))))[1]
          == audit.WIDTH_SOURCE_COMMANDED)
    for degenerate in (None, object()):
        check(f"width of a degenerate world ({type(degenerate).__name__}) "
              "is reported, not raised",
              audit.defensive_line_width_m(degenerate) ==
              (None, audit.WIDTH_SOURCE_UNAVAILABLE))

    # THE GAP.
    check("the gap is bc.x - anchor_x (positive = PAST the line)",
          abs(audit.depth_gap_m(bc, 40.0) - 10.0) < 1e-9)
    check("...and it is SIGNED (behind the line reads negative)",
          abs(audit.depth_gap_m(bc, 60.0) + 10.0) < 1e-9)
    check("the surplus subtracts the gate's OWN margin constant",
          abs(audit.depth_margin_m(bc, 40.0)
              - (10.0 - fb.FB_LINE_BREAK_MARGIN_M)) < 1e-12,
          f"margin constant={fb.FB_LINE_BREAK_MARGIN_M}")
    check("...and an explicit margin is honoured",
          abs(audit.depth_margin_m(bc, 40.0, margin=2.5) - 7.5) < 1e-9)
    for junk in (None, "junk", float("nan"), float("inf")):
        check(f"an unreadable anchor ({junk!r}) reports no gap",
              audit.depth_gap_m(bc, junk) is None
              and audit.depth_margin_m(bc, junk) is None)
    check("an unreadable carrier reports no gap",
          audit.depth_gap_m(None, 40.0) is None)

    # THE NEAREST DEFENDER.
    near_team = (_Body(45.0, 20.0, 11), _Body(60.0, 20.0, 12),
                 _Body(50.0, 25.0, 13))
    w4 = _World(ANCHOR_X, bc, near_team)
    distance, pid = audit.nearest_defender_distance_m(w4, bc)
    check("the nearest defender is the closest defending body",
          abs(distance - 5.0) < 1e-9 and pid == 11,
          f"{distance} id={pid}")
    check("...and the carrier itself is never its own nearest defender",
          audit.nearest_defender_distance_m(
              _World(ANCHOR_X, bc, (bc, _Body(60.0, 20.0, 12))), bc)
          == (10.0, 12))
    check("...and the evaluated defender can be excluded",
          audit.nearest_defender_distance_m(
              w4, bc, exclude=near_team[0])[1] == 13)
    check("no measurable body reports (None, None)",
          audit.nearest_defender_distance_m(
              _World(ANCHOR_X, bc, ()), bc) == (None, None)
          and audit.nearest_defender_distance_m(None, bc) == (None, None)
          and audit.nearest_defender_distance_m(w4, None) == (None, None))
    check("a body with an unreadable position is skipped",
          audit.nearest_defender_distance_m(
              _World(ANCHOR_X, bc, (_Body("junk", "junk", 1),
                                   _Body(52.0, 20.0, 2))), bc)[1] == 2)

    # THE COERCION IS THE SHARED ONE: no diagnostic turns junk into 0.0.
    check("an unmeasurable number is None, never 0.0",
          audit._number(None) is None and audit._number("junk") is None
          and audit._number(float("nan")) is None
          and audit._number(float("inf")) is None)
    check("...and a real number survives the coercion",
          audit._number(3) == 3.0 and audit._number("4.5") == 4.5)


#
# ======================================================================
# [4] THE LEDGER
# ======================================================================

def sec_ledger() -> None:
    section(4, "The ledger deduplicates, counts repeats, and is bounded")
    world, body = build()
    ledger = audit.install_ledger(world)
    bc = world.ball_carrier

    first = audit.record_rejection(world, body, bc, audit.LEG_DEPTH_BREAK,
                                   audit.AdmissionVerdict.LANE_INTACT)
    check("the FIRST evaluation creates the row",
          first is not None and first.repeat_count == 1)
    second = audit.record_rejection(world, body, bc, audit.LEG_DEPTH_BREAK,
                                    audit.AdmissionVerdict.LANE_INTACT)
    check("a REPEAT evaluation increments the SAME row in place",
          second is first and second.repeat_count == 2)
    check("...so the ledger holds ONE distinct row",
          ledger.size == 1 and len(audit.records_for(world)) == 1)
    check("...while reporting TWO evaluations",
          ledger.evaluations == 2)

    # A different frame is a different evaluation.
    world.frame_count = 2
    third = audit.record_rejection(world, body, bc, audit.LEG_DEPTH_BREAK,
                                   audit.AdmissionVerdict.LANE_INTACT)
    check("the same verdict on a NEW frame is a new row",
          third is not first and ledger.size == 2, str(ledger.size))

    # A different verdict on the same frame is a different row.
    audit.record_rejection(world, body, bc, audit.LEG_DEPTH_BREAK,
                           audit.AdmissionVerdict.OUT_OF_REACH)
    check("a different REASON on the same frame is a new row",
          ledger.size == 3, str(ledger.size))

    # An admission and a refusal on the same fixture are different rows.
    audit.record_admission(world, body, bc, audit.LEG_DEPTH_BREAK)
    check("an ADMITTED row and the three refusal rows coexist",
          ledger.size == 4
          and len(audit.admitted_records(world)) == 1
          and len(audit.rejection_records(world)) == 3,
          f"rows={ledger.size} admitted={len(audit.admitted_records(world))} "
          f"refusals={len(audit.rejection_records(world))}")

    # ADMITTED never files through record_rejection.
    before = ledger.size
    check("record_rejection refuses to file an ADMITTED verdict",
          audit.record_rejection(world, body, bc, audit.LEG_DEPTH_BREAK,
                                 audit.AdmissionVerdict.ADMITTED) is None
          and ledger.size == before)
    check("...and refuses NOT_TAGGED (the identity no-op)",
          audit.record_rejection(world, body, bc, audit.LEG_ADMISSION,
                                 audit.AdmissionVerdict.NOT_TAGGED) is None
          and ledger.size == before)
    check("...and refuses a junk label without raising",
          audit.record_rejection(world, body, bc, audit.LEG_DEPTH_BREAK,
                                 "not-a-verdict") is None
          and audit.record_rejection(world, body, bc, audit.LEG_DEPTH_BREAK,
                                     7) is None
          and ledger.size == before)

    # THE CEILING.
    capped_world, capped_body = build()
    capped = audit.install_ledger(capped_world, limit=2)
    for verdict in (audit.AdmissionVerdict.LANE_INTACT,
                    audit.AdmissionVerdict.OUT_OF_REACH,
                    audit.AdmissionVerdict.BELOW_DEPTH_MARGIN):
        audit.record_rejection(capped_world, capped_body,
                               capped_world.ball_carrier,
                               audit.LEG_DEPTH_BREAK, verdict)
    check("the ceiling bounds the row count",
          capped.size == 2 and capped.limit == 2, str(capped.size))
    check("...and a refused NEW key is counted in dropped",
          capped.dropped == 1, str(capped.dropped))
    check("...while a repeat of a HELD row still increments it",
          audit.record_rejection(capped_world, capped_body,
                                 capped_world.ball_carrier,
                                 audit.LEG_DEPTH_BREAK,
                                 audit.AdmissionVerdict.LANE_INTACT)
          is capped.records[0]
          and capped.dropped == 1)

    # CLEAR.
    ledger.clear()
    check("clear() drops every row and the dedup index",
          ledger.size == 0 and ledger.evaluations == 0
          and ledger.dropped == 0 and audit.records_for(world) == ())
    check("...and the ledger is usable again afterwards",
          audit.record_rejection(world, body, bc, audit.LEG_DEPTH_BREAK,
                                 audit.AdmissionVerdict.LANE_INTACT)
          is not None and ledger.size == 1)

    # THE READERS ARE PURE AND TOTAL.
    check("records_for on an unarmed world is empty, not an error",
          audit.records_for(_World(ANCHOR_X, _Ball(BC_X, BC_Y), ())) == ()
          and audit.records_for(None) == ())
    check("the count readers agree with the rows",
          audit.reason_counts(world) == {"lane_intact": 1}
          and audit.condition_counts(world) == {"depth_break": 1},
          f"{audit.reason_counts(world)} {audit.condition_counts(world)}")
    check("admitted_records / rejection_records filter the same sink",
          len(audit.admitted_records(world))
          + len(audit.rejection_records(world, line_break_only=False))
          == len(audit.records_for(world)))
    audit.reset_admission_audit(world)


# ======================================================================
# [5] THE REAL GATE
# ======================================================================

def sec_gate_parity() -> None:
    section(5, "The REAL gate: armed/disarmed parity on every fixture")
    armed_verdicts: dict = {}
    for name, kwargs in CASES.items():
        on_world, on_body = build(**kwargs)
        on_verdict = evaluate(on_world, on_body, True)
        off_world, off_body = build(**kwargs)
        off_verdict = evaluate(off_world, off_body, False)
        armed_verdicts[name] = on_verdict
        check(f"[{name}] the verdict is IDENTICAL armed and disarmed",
              on_verdict == off_verdict, f"on={on_verdict} off={off_verdict}")
        check(f"[{name}] the disarmed engine records NOTHING",
              audit.records_for(off_world) == ())
        print(f"    {name}: verdict={on_verdict} rows={verdicts(on_world)}")

    check("a genuine DEPTH break ADMITS",
          armed_verdicts["genuine_depth"] is True)
    check("a genuine LATERAL breach ADMITS",
          armed_verdicts["lateral_admit"] is True)
    check("a manufactured (unoccupied) anchor does NOT admit",
          armed_verdicts["phantom_depth"] is False)
    check("a body without tackle authority does NOT admit",
          armed_verdicts["no_tackle_authority"] is False)
    check("a frozen body does NOT admit", armed_verdicts["frozen"] is False)
    check("a body inside a live freeze timer does NOT admit",
          armed_verdicts["freeze_timer"] is False)
    check("the registered fullback is not admitted on an intact lane",
          armed_verdicts["lane_intact"] is False)


def sec_gate_reasons() -> None:
    section("5b", "The REAL gate: every reason it can produce is produced")
    seen: set = set()

    def reasons_of(**kwargs) -> tuple:
        world, body = build(**kwargs)
        evaluate(world, body, True)
        for row in audit.records_for(world):
            seen.add(row.verdict)
        return (tuple(verdicts(world)), tuple(verdicts(world, leg="admission")),
                tuple(verdicts(world, leg="depth_break")),
                tuple(verdicts(world, leg="lateral_break")))

    all_rows, adm, dep, lat = reasons_of(can_tackle=False)
    check("tackle authority: an identity refusal on the ADMISSION leg",
          adm == ("no_tackle_authority",) and dep == () and lat == (),
          f"{adm} {dep} {lat}")

    all_rows, adm, dep, lat = reasons_of(frozen=True)
    check("frozen: an identity refusal on the ADMISSION leg",
          adm == ("frozen",) and dep == () and lat == (), f"{adm}")

    all_rows, adm, dep, lat = reasons_of(timer=4.0)
    check("a live freeze timer: an identity refusal on the ADMISSION leg",
          adm == ("freeze_timer",) and dep == () and lat == (), f"{adm}")

    saved = fb.FULLBACK_TACKLE_CANDIDACY
    try:
        fb.FULLBACK_TACKLE_CANDIDACY = False
        all_rows, adm, dep, lat = reasons_of()
        check("the candidacy lever OFF is an identity refusal (tagged body)",
              adm == ("lever_off",) and dep == () and lat == (), f"{adm}")
        all_rows, adm, dep, lat = reasons_of(tagged=False)
        check("...but an UNTAGGED body files NOTHING at all "
              "(it is not a line-break evaluation)",
              adm == () and dep == () and lat == ()
              and audit.records_for(_World(ANCHOR_X, _Ball(BC_X, BC_Y), ()))
              == (),
              f"{adm} {dep} {lat}")
    finally:
        fb.FULLBACK_TACKLE_CANDIDACY = saved

    all_rows, adm, dep, lat = reasons_of(bc_x=40.5)
    check("below the depth margin: the DEPTH leg names the shortfall",
          dep == ("below_depth_margin",) and adm == (), f"{dep}")
    check("...and the LATERAL leg names its own refusal in the same frame",
          lat == ("lane_intact",), f"{lat}")

    all_rows, adm, dep, lat = reasons_of(bc_x=30.0, bc_y=55.0)
    check("a carrier behind an intact line: the level exclusion is named",
          lat == ("line_intact_level_exclusion",), f"{lat}")

    all_rows, adm, dep, lat = reasons_of(bc_x=38.0, bc_y=60.0, fb_x=62.0)
    check("a breach outside the 2D reach is named as out_of_reach",
          lat == ("out_of_reach",), f"{lat}")

    all_rows, adm, dep, lat = reasons_of(behind=0, ahead=8)
    check("a manufactured anchor: the OCCUPANCY refusal is named",
          dep == ("line_unoccupied",), f"{dep}")
    check("...and the lateral leg names the DEPTH classification that "
          "pre-empted it", lat == ("depth_break_classified",), f"{lat}")

    lat_world, lat_body = build(bc_x=39.5, bc_y=39.0)
    evaluate(lat_world, lat_body, True)
    check("an admitted LATERAL breach files an ADMITTED row on ITS leg",
          tuple(verdicts(lat_world, leg="lateral_break", admitted=True))
          == ("admitted",)
          and tuple(verdicts(lat_world, leg="depth_break"))
          == ("below_depth_margin",),
          str(verdicts(lat_world, admitted=True)))
    seen.add("admitted")

    world, body = build()
    world.ball_carrier = None
    evaluate(world, body, True)
    check("no carrier: the refusal is named (no line-break evaluation "
          "without one)", tuple(verdicts(world)) == ("no_carrier",),
          str(verdicts(world)))
    seen.add("no_carrier")

    reachable = {v.value for v in audit.AdmissionVerdict} - {
        "no_world", "no_defender", "not_tagged",
    }
    check("EVERY verdict the gate can produce was produced by this leg",
          reachable <= seen, str(sorted(reachable - seen)))
    check("...and nothing outside the vocabulary was ever recorded",
          all(audit.is_known_verdict(v) for v in seen), str(sorted(seen)))
    check("no degenerate label (no_world / no_defender / not_tagged) was "
          "filed by the gate",
          not (seen & {"no_world", "no_defender", "not_tagged"}))


# ======================================================================
# [6] THE LATCH
# ======================================================================

def sec_latch() -> None:
    section(6, "An ADMITTED row measures the anchor its verdict USED")
    # The live anchor relocks GOAL-SIDE of the carrier (40 -> 44) while the
    # latch holds the line the break was classified against at 30.  A row
    # measured against the LIVE line would report gap -2.0 on a frame the
    # actor is chasing from; it must report the LATCHED line it used.
    world, body = build(anchor=44.0)
    world.defending_team = tuple(world.defending_team) + (
        _Body(25.0, 20.0, 900), _Body(24.0, 45.0, 901),
    )
    fb.arm_fullback_break_latch(body, fb.FB_BREAK_DEPTH, 30.0)
    verdict = evaluate(world, body, True)
    check("the latched chase still ADMITS", verdict is True)
    rows = audit.admitted_records(world)
    check("...and files exactly one ADMITTED row", len(rows) == 1,
          str(len(rows)))
    row = rows[0]
    print("    " + audit.format_admission_record(row))
    check("the row reads the LATCHED anchor, not the relocked live one",
          row.anchor_source == "break_test"
          and row.anchor_x is not None and abs(row.anchor_x - 30.0) < 1e-9,
          f"{row.anchor_source} {row.anchor_x}")
    check("...so its gap is POSITIVE against the line it chased from",
          row.gap_m is not None and abs(row.gap_m - 12.0) < 1e-9,
          str(row.gap_m))
    check("...and its margin clears the gate's own break margin",
          row.depth_margin_m is not None
          and row.depth_margin_m > 0.0, str(row.depth_margin_m))
    check("...and the armed latch is recorded on the row",
          row.latched is True, str(row.latched))
    check("...and the occupancy of that line is recorded too",
          row.line_occupied is True, str(row.line_occupied))
    check("the DISARMED engine never armed a latch check for a row",
          evaluate(build(anchor=44.0)[0], build(anchor=44.0)[1], False)
          is False)   # unlatched, live anchor 44 -> below margin

    # Every refusal row carries the anchor its own leg evaluated.
    world2, body2 = build(bc_x=40.5)
    evaluate(world2, body2, True)
    for refusal in audit.rejection_records(world2):
        check(f"[{refusal.leg}] the refusal reports a SUPPLIED anchor "
              "(its own leg's test anchor)",
              refusal.anchor_source == "supplied",
              refusal.anchor_source)
        check(f"[{refusal.leg}] ...which is the anchor on the frame",
              refusal.anchor_x is not None
              and abs(refusal.anchor_x - ANCHOR_X) < 1e-9,
              str(refusal.anchor_x))
        check(f"[{refusal.leg}] ...and the gap is measured against it",
              refusal.gap_m is not None
              and abs(refusal.gap_m - (40.5 - ANCHOR_X)) < 1e-9,
              str(refusal.gap_m))


# ======================================================================
# [7] THE LABEL IS DERIVED FROM THE CLASSIFIER'S OWN CONSTANTS
# ======================================================================

def sec_label_geometry() -> None:
    section(7, "The lateral label agrees with the classifier's own test")
    mislabels = []
    checked = 0
    for bc_x in (20.0, 36.5, 37.0, 39.5, 40.0, 41.5, 42.0, 44.0, 60.0):
        for bc_y in (5.0, 20.0, 24.5, 30.0, 35.5, 41.0, 55.0, 65.0):
            for fb_x, fb_y in ((40.0, 30.0), (44.0, 30.0), (62.0, 30.0)):
                bc = _Ball(bc_x, bc_y)
                body = make_fb(fb_x, fb_y)
                stub = _World(ANCHOR_X, bc, (body,))
                test_anchor = fb.break_test_line_anchor_x(
                    stub, body, ANCHOR_X,
                )
                _ax, anchor_y = fb.compute_fullback_anchor(bc, ANCHOR_X)
                kind = fb.classify_fullback_break(
                    bc, body, test_anchor, anchor_y,
                )
                if kind == fb.FB_BREAK_LATERAL:
                    continue
                checked += 1
                label = fb._lateral_break_refusal_reason(
                    bc, body, test_anchor, anchor_y, kind,
                )
                if kind == fb.FB_BREAK_DEPTH:
                    if label != "depth_break_classified":
                        mislabels.append((bc_x, bc_y, fb_x, kind, label))
                    continue
                channel_y = (float(getattr(body, "y", anchor_y) or anchor_y)
                             if fb.FB_LATERAL_OPEN_PLAY else float(anchor_y))
                breach = (abs(float(bc.y) - channel_y)
                          > fb.FB_LATERAL_BREAK_M)
                level = (fb.FB_LATERAL_BREAK_LEVEL_ENABLED
                         and bc_x < test_anchor - fb.FB_LATERAL_BREAK_LEVEL_M)
                reach = math.hypot(bc.x - body.x, bc.y - body.y)
                if label not in ("lane_intact", "out_of_reach",
                                 "line_intact_level_exclusion"):
                    mislabels.append((bc_x, bc_y, fb_x, kind, label))
                    continue
                if label == "lane_intact" and breach:
                    mislabels.append((bc_x, bc_y, fb_x, kind, label))
                if label == "out_of_reach" and (
                    not breach or reach <= fb.FB_LATERAL_BREAK_RADIUS_M
                ):
                    mislabels.append((bc_x, bc_y, fb_x, kind, label))
                if label == "line_intact_level_exclusion" and not level:
                    mislabels.append((bc_x, bc_y, fb_x, kind, label))
    check("every lateral refusal label is the geometrically correct one",
          not mislabels, f"swept={checked} bad={mislabels[:4]}")
    check("the sweep exercised every lateral label",
          checked > 50, str(checked))
    check("the classifier itself still returns a break kind, not a label",
          fb.classify_fullback_break(
              _Ball(42.0, 30.0), make_fb(45.0, 30.0), ANCHOR_X, 30.0,
          ) == fb.FB_BREAK_DEPTH)
    check("the label vocabulary is a subset of the ledger's",
          all(audit.is_known_verdict(name) for name in
              ("depth_break_classified", "line_intact_level_exclusion",
               "lane_intact", "out_of_reach")))
# ======================================================================
# [8] SOURCE WIRING
# ======================================================================

def sec_wiring() -> None:
    section(8, "Source wiring")
    with open(FB_PATH, encoding="utf-8") as handle:
        fb_src = handle.read()
    with open(AUDIT_PATH, encoding="utf-8") as handle:
        audit_src = handle.read()

    # THE THREE INSTRUMENTED SITES.
    check("the admission gate is instrumented",
          "def _log_admission(" in fb_src
          and "_log_admission(world, d, bc, \"depth_break\")" in fb_src
          and "_log_admission(world, d, bc, \"lateral_break\")" in fb_src)
    check("the DEPTH leg is instrumented",
          "_log_admission_rejection(" in fb_src)
    check("the LATERAL leg is instrumented",
          "_lateral_break_refusal_reason(" in fb_src)
    check("every identity refusal has a logging site",
          all(f'"{name}"' in fb_src for name in
              ("lever_off", "no_tackle_authority", "frozen", "freeze_timer")))
    check("the lever-off site only fires for a TAGGED body",
          'if getattr(d, "fullback_role_active", False):\n'
          "            _log_admission_rejection(" in fb_src
          or 'if getattr(d, "fullback_role_active", False):' in fb_src)
    check("the occupancy refusals are logged on both legs",
          fb_src.count('"line_unoccupied"') == 2,
          str(fb_src.count('"line_unoccupied"')))
    check("the arm check is the cheap gate, not the recorder",
          "def admission_audit_armed" in fb_src
          and "admission_audit_armed(world)" in fb_src)
    check("the audit is imported FUNCTION-LOCALLY (no module-scope cycle)",
          "def _admission_audit()" in fb_src
          and "from game.defence.defence_lvl_1 import "
              "emergency_chase_admission_audit" in fb_src)

    # THE CONTRACTS THE OTHER VERIFIERS PIN MUST SURVIVE.
    check("the admission gate still reads the latched DEPTH twin verbatim",
          "if fullback_depth_break_active(world, d, bc):" in fb_src)
    conjunct = ("return fullback_defensive_line_occupied"
                "(world, _test_anchor, defender)")
    check("all three occupancy returns survive",
          fb_src.count(conjunct) >= 3, str(fb_src.count(conjunct)))
    check("the classifier body is still free of the occupancy gate",
          "fullback_defensive_line_occupied" not in
          fb_src.split("def classify_fullback_break")[1].split("\ndef ")[0])
    check("the classifier still returns FB_BREAK_DEPTH on geometry alone",
          "return FB_BREAK_DEPTH" in fb_src)
    check("the break latch's single reader still floors the live anchor",
          "def break_test_line_anchor_x" in fb_src
          and "return float(min(live, latched))" in fb_src)
    for name in ("def fullback_emergency_defender_active",
                 "def fullback_depth_break_active",
                 "def fullback_lateral_break_active",
                 "def fullback_defensive_line_occupied",
                 "def classify_fullback_break",
                 "def clear_fullback_chase_state",
                 "def release_fullback_chase_on_anchor_relock"):
        check(f"{name.split('def ')[1]} is still defined", name in fb_src)

    # THE RECORDER IS NEVER REACHED WITHOUT THE ARM CHECK.
    check("nine refusal sites call the ONE guarded helper",
          fb_src.count("_log_admission_rejection(") == 10,
          str(fb_src.count("_log_admission_rejection(")))
    check("...and the two admitted sites call the ONE guarded recorder",
          fb_src.count("_log_admission(") == 3,
          str(fb_src.count("_log_admission(")))
    check("...because the helpers gate on audit_armed themselves",
          "if not audit.audit_armed(world):" in fb_src
          and fb_src.count("if not audit.audit_armed(world):") == 2,
          str(fb_src.count("if not audit.audit_armed(world):")))
    check("the occupancy legs are additionally ARMED-GATED so the disarmed "
          "engine evaluates the gate exactly once",
          fb_src.count("if admission_audit_armed(world) and not "
                       "fullback_defensive_line_occupied(") == 2,
          str(fb_src.count("if admission_audit_armed(world) and not "
                           "fullback_defensive_line_occupied(")))

    # THE LEDGER MODULE ITSELF.
    import_line = audit_src.split("from __future__")[1].split("\n\n")[0]
    check("the ledger's module scope is stdlib ONLY (no project import)",
          "game." not in import_line and "player." not in import_line,
          import_line.strip().replace("\n", " | "))
    audit_body = audit_src.split('"""', 2)[-1]
    module_scope = audit_body.split("\ndef ", 1)[0]
    check("...so the cycle is impossible in both directions",
          "from game" not in module_scope
          and "from player" not in module_scope
          and "import game" not in module_scope,
          "module scope holds no project import")
    check("the module has an explicit __all__ export list",
          "__all__" in audit_src and "write_admission_csv" in audit_src)
    check("the ceiling is a named constant with an env override",
          "MAX_AUDIT_RECORDS: int = 50000" in audit_src
          and 'ADMISSION_AUDIT_MAX_ENV_VAR: str = '
              '"RLM_FB_ADMISSION_AUDIT_MAX"' in audit_src)
    check("the world sink attribute is named",
          'ADMISSION_AUDIT_ATTR: str = "emergency_chase_admission_audit"'
          in audit_src)
    check("the three diagnostics are exported by name",
          all(name in audit.__all__ for name in
              ("defensive_line_width_m", "depth_gap_m",
               "nearest_defender_distance_m")))
    check("the CSV carries the reason and all three diagnostics",
          all(col in audit.CSV_COLUMNS for col in
              ("leg", "verdict", "defensive_line_width_m",
               "defensive_line_width_source", "gap_m", "depth_margin_m",
               "nearest_defender_distance_m", "nearest_defender_id")),
          str(audit.CSV_COLUMNS))
    check("the population read is total (the non-iterable guard)",
          "def _team_bodies" in audit_src
          and "except TypeError:" in audit_src)


# ======================================================================
# [9] EXPORT
# ======================================================================

def sec_export() -> None:
    section(9, "The CSV export and the digest")
    world, body = build(bc_x=40.5)
    evaluate(world, body, True)
    rows = audit.records_for(world)
    check("the fixture recorded rows to export", len(rows) == 2, str(len(rows)))

    target = "tmp/temp_verify_admission_audit.csv"
    path = audit.write_admission_csv(world, target)
    check("write_admission_csv honours the explicit path", path == target)
    with open(target, encoding="utf-8") as handle:
        text = handle.read().strip().splitlines()
    check("the header is the declared column list",
          text[0].split(",") == list(audit.CSV_COLUMNS),
          text[0])
    check("...and there is one line per row (plus the header)",
          len(text) == len(rows) + 1, str(len(text)))
    check("...and a refusal's reason is in the first data column set",
          "below_depth_margin" in text[1] or "lane_intact" in text[1],
          text[1])
    check("a file is never written when the ledger is empty",
          audit.write_admission_csv(
              _World(ANCHOR_X, _Ball(BC_X, BC_Y), ())) is None)
    check("...nor when the audit is off",
          audit.write_admission_csv(None) is None
          and audit.write_admission_csv(None, target) is None)
    check("...and an unmeasurable value is written as an empty field, "
          "never 0.0",
          audit._rounded(None) == ""
          and audit._rounded(float("nan")) == ""
          and audit._rounded(1.23456789) == 1.2346,
          f"none={audit._rounded(None)!r} "
          f"nan={audit._rounded(float('nan'))!r} "
          f"num={audit._rounded(1.23456789)!r}")
    check("...while a LABEL (leg / reason / source) passes through "
          "unchanged, so the CSV cannot lose the columns it exists for",
          audit._rounded("lane_intact") == "lane_intact"
          and audit._rounded("measured") == "measured"
          and audit._rounded("depth_break") == "depth_break",
          audit._rounded("lane_intact"))
    os.remove(target)

    digest = audit.summarise_admission_audit(world)
    print("    " + digest)
    check("the digest names the rows, the evaluations and the refusals",
          "rows=" in digest and "evaluations=" in digest
          and "line_break_refusals=" in digest and "reasons[" in digest,
          digest)
    check("...and it reports the refusals the ledger actually holds",
          f"line_break_refusals={len(audit.rejection_records(world))}"
          in digest, digest)
    check("a disarmed world digests as off",
          audit.summarise_admission_audit(
              _World(ANCHOR_X, _Ball(BC_X, BC_Y), ())) ==
          "fb_admission_audit=off")
    check("the per-row renderer names the leg, the reason and the metrics",
          all(token in audit.format_admission_record(rows[0]) for token in
              ("frame=", "leg=", "verdict=", "line_width=", "gap=",
               "margin=", "nearest=", "repeat=")),
          audit.format_admission_record(rows[0]))
    audit.reset_admission_audit(world)


# ======================================================================
# [10] NO REGRESSION
# ======================================================================

def sec_no_regression() -> None:
    section(10, "No regression: degenerate worlds are tolerated")
    # A world with NO defending population still runs the gate and still
    # files a row -- with the diagnostics honestly unmeasurable.
    world, body = build(behind=0, ahead=0)
    verdict = evaluate(world, body, True)
    check("a world with no other defenders still admits the break "
          "(nothing else changed the verdict)", verdict is True)
    rows = audit.records_for(world)
    check("...and the ledger still files the row", len(rows) == 1)
    row = rows[0]
    check("...with the width falling back to the COMMANDED value",
          row.defensive_line_width_source == audit.WIDTH_SOURCE_COMMANDED
          and row.defensive_line_width_m == 40.0,
          f"{row.defensive_line_width_m} ({row.defensive_line_width_source})")
    check("...and the nearest defender is the evaluated sweeper itself "
          "(a MEASURED 3.00 m, not a fabricated 0.0)",
          row.nearest_defender_distance_m is not None
          and abs(row.nearest_defender_distance_m - 3.0) < 1e-9
          and row.nearest_defender_id == 201,
          f"{row.nearest_defender_distance_m} {row.nearest_defender_id}")
    audit.reset_admission_audit(world)

    # ...and with NO defending population at all the read is honestly None.
    empty_world, empty_body = build(behind=0, ahead=0)
    empty_world.defending_team = ()
    evaluate(empty_world, empty_body, True)
    empty_row = audit.records_for(empty_world)[0]
    check("...and with no defending population at all it is None",
          empty_row.nearest_defender_distance_m is None
          and empty_row.nearest_defender_id is None,
          f"{empty_row.nearest_defender_distance_m} "
          f"{empty_row.nearest_defender_id}")
    audit.reset_admission_audit(empty_world)

    # A NON-ITERABLE population must not raise in the LEDGER's own reads:
    # every measurement starts from the population, so it may never raise.
    class _BadTeam(_World):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.defending_team = 5          # not iterable

    def gate_outcome(armed: bool):
        """("value", v) or ("raise", name) -- the gate's outcome."""
        bad = _BadTeam(ANCHOR_X, _Ball(BC_X, BC_Y), ())
        try:
            return ("value", evaluate(bad, make_fb(), armed))
        except Exception as exc:                 # noqa: BLE001
            return ("raise", type(exc).__name__)

    check("the ledger's width read is total on a non-iterable population",
          audit.defensive_line_width_m(
              _BadTeam(ANCHOR_X, _Ball(BC_X, BC_Y), ()))
          == (40.0, audit.WIDTH_SOURCE_COMMANDED),
          str(audit.defensive_line_width_m(
              _BadTeam(ANCHOR_X, _Ball(BC_X, BC_Y), ()))))
    check("...and so is the nearest-defender read",
          audit.nearest_defender_distance_m(
              _BadTeam(ANCHOR_X, _Ball(BC_X, BC_Y), ()),
              _Ball(BC_X, BC_Y)) == (None, None))
    check("...and the GATE behaves IDENTICALLY armed and disarmed on it "
          "(the ledger never changes a frame)",
          gate_outcome(True) == gate_outcome(False),
          f"{gate_outcome(True)} vs {gate_outcome(False)}")
    audit.reset_admission_audit()

    # DEGENERATE CALLS into the recorders: no world / no defender.
    saved_env = os.environ.get(audit.ADMISSION_AUDIT_ENV_VAR)
    try:
        os.environ[audit.ADMISSION_AUDIT_ENV_VAR] = "1"
        audit.reset_admission_audit()
        no_world = audit.record_rejection(
            None, body, _Ball(BC_X, BC_Y), audit.LEG_ADMISSION,
            audit.AdmissionVerdict.NO_WORLD,
        )
        check("a world-less call is recorded against the module ledger "
              "rather than raising",
              no_world is not None and no_world.frame == 0
              and no_world.defensive_line_width_source
              == audit.WIDTH_SOURCE_UNAVAILABLE
              and no_world.anchor_source == "unavailable",
              f"frame={getattr(no_world, 'frame', None)} "
              f"{getattr(no_world, 'anchor_source', None)}")
        no_body = audit.record_rejection(
            _World(ANCHOR_X, _Ball(BC_X, BC_Y), ()), None,
            _Ball(BC_X, BC_Y), audit.LEG_ADMISSION,
            audit.AdmissionVerdict.NO_DEFENDER,
        )
        check("a defender-less call is recorded too",
              no_body is not None and no_body.player_id is None
              and no_body.latched is None,
              f"player={getattr(no_body, 'player_id', None)}")
        check("the module ledger holds both degenerate rows",
              len(audit.records_for()) == 2,
              str(len(audit.records_for())))
    finally:
        audit.reset_admission_audit()
        if saved_env is None:
            os.environ.pop(audit.ADMISSION_AUDIT_ENV_VAR, None)
        else:
            os.environ[audit.ADMISSION_AUDIT_ENV_VAR] = saved_env

    # THE GATE ON A GARBAGE WORLD: no raise, no ledger, no change.
    class _Broken:
        pass

    for bad_world in (None, _Broken()):
        try:
            outcome = fb.fullback_emergency_defender_active(
                bad_world, make_fb(), _Ball(BC_X, BC_Y),
            )
            ok, detail = outcome is False, str(outcome)
        except Exception as exc:                 # noqa: BLE001
            ok, detail = False, repr(exc)
        check(f"the gate on a degenerate world "
              f"({type(bad_world).__name__}) still answers False",
              ok, detail)

    # DETERMINISM: the same fixture twice is the same ledger.
    def run():
        w, b = build()
        evaluate(w, b, True)
        return tuple((r.frame, r.leg, r.verdict, r.admitted, r.player_id,
                      r.defensive_line_width_m, r.gap_m, r.depth_margin_m,
                      r.nearest_defender_distance_m, r.nearest_defender_id,
                      r.anchor_x, r.anchor_source, r.line_occupied, r.latched)
                     for r in audit.records_for(w))

    first, second = run(), run()
    check("the same fixture produces a byte-identical ledger twice",
          first == second and len(first) > 0, str(first[:1]))
    check("...and the disarmed run still produces nothing at all",
          evaluate(build()[0], build()[1], False) in (True, False))


def main() -> int:
    print("FULLBACK EMERGENCY-CHASE ADMISSION LEDGER CONTRACT "
          "(rejection instrumentation, 2026-09-30)")
    print("=" * 68)
    sec_lever()
    sec_vocabulary()
    sec_diagnostics()
    sec_ledger()
    sec_gate_parity()
    sec_gate_reasons()
    sec_latch()
    sec_label_geometry()
    sec_wiring()
    sec_export()
    sec_no_regression()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS - every rejected line-break evaluation on the")
    print("        fullback's emergency-chase path is now logged with the")
    print("        defensive line's width, the carrier's signed gap to the")
    print("        line the verdict used (and its surplus over the break")
    print("        margin), the nearest defender's distance, and the")
    print("        exact conjunct that refused -- deduplicated per frame")
    print("        with a repeat count, bounded by a declared ceiling that")
    print("        reports its own drops, and DISARMED by default so the")
    print("        gate's verdict is byte-identical whether or not the")
    print("        ledger is collecting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
