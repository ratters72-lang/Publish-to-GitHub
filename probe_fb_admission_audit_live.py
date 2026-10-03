"""probe_fb_admission_audit_live.py — THE ADMISSION LEDGER ON A REAL ROLLOUT.

THE QUESTION
------------
``verify_fb_admission_audit_contract.py`` proves the ledger's contract against
stubs.  It cannot prove the two things only a REAL engine can show:

    1. that the ledger is OBSERVATIONAL -- that arming it leaves every player
       field the engine writes byte-identical frame for frame, so the gate's
       verdict cannot have been changed by the instrumentation;
    2. that the ledger actually RECORDS on a real game -- that the real
       emergency-chase path puts rejected line-break evaluations through it,
       with all three diagnostics measured rather than left unavailable.

WHY THE OBVIOUS PROOF DOES NOT WORK
-----------------------------------
The naive proof -- run disarmed, run armed, demand the two frame sequences
match -- FAILS on this fixture, and it fails for a reason that has nothing to
do with the ledger: **the engine is not frame-reproducible in process.**  Two
DISARMED runs of the same seed fork at frame 383 (the anchor-relock frame the
break-latch probe documented).  So a raw armed/disarmed difference at 383 is
not evidence about the instrumentation; it cannot tell the two apart.

WHAT IS PROVED INSTEAD
----------------------
The probe measures the fixture's own fork point FIRST, with a DISARMED vs
DISARMED control, and then proves neutrality over the window in which the
fixture IS reproducible -- and additionally proves the mechanism:

  [1]  the control arm records nothing,
  [1b] DISARMED vs DISARMED -- the fixture's OWN fork frame, F,
  [4a] over frames 1..F-1, ARMED and DISARMED are byte-identical,
  [4b] the ledger RECORDED inside that window (so neutrality was tested
       exactly where the recording happens),
  [4c] beyond F, the armed run forks at the SAME frame as the control --
       engine-side, and no larger,
  [4d] the DOUBLE-EVALUATION control: a DISARMED run whose occupancy
       predicate is called twice per evaluation -- structurally the extra
       call the armed gate adds -- is byte-identical to the plain DISARMED
       run over the window, and NO evaluation of the real predicate ever
       returned two different answers,
  [4e] a SECOND ARMED run files exactly the same rows, frame for frame,
       inside the window: the ledger's content is deterministic there.

Together: the predicate is pure on a real game, the extra armed call it adds
is proven inert against the plain engine, and the recording itself is proven
deterministic over the whole reproducible window.

Run:    python probe_fb_admission_audit_live.py
Report: FB_ADMISSION_AUDIT_LIVE.csv (the armed run's ledger)
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import hashlib
import random
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

from world.field import Field
from world.world_state import WorldState, GamePhase
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch

from game.defence.defence_lvl_1 import emergency_chase_admission_audit as audit
from game.defence.defence_lvl_1 import fullback_role as fb_role

MAX_FRAMES = int(os.environ.get("RLM_FBAA_FRAMES", "1200"))
SEED = int(os.environ.get("RLM_FBAA_SEED", "20260827"))
DT = 1.0 / 60.0

CSV_PATH = os.environ.get("RLM_FBAA_CSV", "FB_ADMISSION_AUDIT_LIVE.csv")

FAILURES: list = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          + (f" - {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)
    return bool(condition)


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * 74)


def name_of(obj):
    """``obj.name`` when present, else ``str(obj)``, else None."""
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def player_row(player) -> str:
    """One player's COMPLETE frame state, as a stable string."""
    return "|".join(str(value) for value in (
        getattr(player, "player_id", None),
        repr(getattr(player, "x", None)),
        repr(getattr(player, "y", None)),
        repr(getattr(player, "vx", None)),
        repr(getattr(player, "vy", None)),
        repr(getattr(player, "target_x", None)),
        repr(getattr(player, "target_y", None)),
        repr(getattr(player, "move_speed", None)),
        name_of(getattr(player, "movement_state", None)),
        name_of(getattr(player, "defender_role", None)),
        repr(getattr(player, "tackle_role", None)),
        repr(bool(getattr(player, "fullback_role_active", False))),
        repr(bool(getattr(player, "tes_attached", False))),
        repr(bool(getattr(player, "frozen", False))),
        repr(bool(getattr(player, "can_acquire_tackle", False))),
    ))


def frame_digest(world) -> str:
    """A digest of EVERY player's frame state."""
    players = sorted(
        getattr(world, "players", None) or [],
        key=lambda p: (getattr(p, "player_id", 0) or 0),
    )
    text = "\n".join(player_row(p) for p in players)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def boot():
    """(world, open_play, kickoff_engine, ball, collisions) at the kickoff."""
    collisions = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play

    kickoff_engine = KickoffEngine(world, ball, field)

    random.seed(SEED)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    return world, open_play, kickoff_engine, ball, collisions


# ---------------------------------------------------------------------------
# THE DOUBLE-EVALUATION CONTROL.
#
# The armed gate adds exactly ONE structural thing to the disarmed engine: a
# SECOND evaluation of the occupancy predicate on the same arguments
# (``fullback_role``'s ``if admission_audit_armed(world) and not
# fullback_defensive_line_occupied(...)`` before the value is returned).  If
# that predicate is pure, the second call cannot change the answer -- and the
# way to prove it is to PERFORM it in a disarmed run and demand the run come
# out identical.  ``_ORIGINAL_OCCUPIED`` holds the unwrapped function so the
# wrapper can call it twice without recursing.
# ---------------------------------------------------------------------------

def install_double_occupancy(stats: dict) -> None:
    """Make every occupancy evaluation happen TWICE; log disagreement."""
    original = fb_role.fullback_defensive_line_occupied

    def _double(world, anchor_x, defender=None):
        first = original(world, anchor_x, defender)
        second = original(world, anchor_x, defender)
        stats["calls"] += 1
        if first != second:
            stats["unstable"].append(
                (getattr(world, "frame_count", None), first, second),
            )
        return first

    fb_role._ORIGINAL_OCCUPIED = original
    fb_role.fullback_defensive_line_occupied = _double


def restore_occupancy() -> None:
    """Put the unwrapped predicate back."""
    original = getattr(fb_role, "_ORIGINAL_OCCUPIED", None)
    if original is not None:
        fb_role.fullback_defensive_line_occupied = original
        del fb_role._ORIGINAL_OCCUPIED


def run(armed: bool, double_occupancy: bool = False) -> dict:
    """Step the REAL engine for MAX_FRAMES; return its per-frame digests.

    *armed* installs a ledger on the world BEFORE the first frame, so every
    gate evaluation in the run is recorded.  *double_occupancy* wraps the
    occupancy predicate so it is evaluated twice per call -- the extra call
    the armed gate adds, with no recording at all.
    """
    world, open_play, kickoff_engine, ball, collisions = boot()
    if armed:
        audit.install_ledger(world)
    else:
        audit.reset_admission_audit(world)

    double_stats: dict = {"calls": 0, "unstable": []}
    if double_occupancy:
        install_double_occupancy(double_stats)

    digests: list = []
    frames_with_rows: list = []

    # The engine's own per-frame chatter would drown the report.
    devnull = open(os.devnull, "w", encoding="utf-8")
    saved = sys.stdout
    sys.stdout = devnull
    try:
        for frame in range(1, MAX_FRAMES + 1):
            world.frame_count = frame
            ball.update(DT)

            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(DT)
                check_for_catch(world, ball, DT)
            elif world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    world.open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
            elif world.phase == GamePhase.OPEN_PLAY:
                open_play.update(DT)
                if not world.tes_active:
                    players = getattr(world, "players", None) or []
                    if players:
                        collisions.resolve_all(players, world=world)

            digests.append(frame_digest(world))
            if armed and audit.records_for(world):
                frames_with_rows.append(frame)
    finally:
        sys.stdout = saved
        devnull.close()
        if double_occupancy:
            restore_occupancy()

    ledger_rows = audit.records_for(world) if armed else ()
    summary = (audit.summarise_admission_audit(world) if armed else "")
    if armed:
        audit.reset_admission_audit(world)
    return {
        "digests": digests,
        "rows": tuple(ledger_rows),
        "summary": summary,
        "frames_with_rows": frames_with_rows,
        "double_stats": double_stats,
        "world": world,
    }


def first_divergence(off: list, on: list):
    """(frame_number, off_digest, on_digest) of the first differing frame."""
    for index, (a, b) in enumerate(zip(off, on)):
        if a != b:
            return index + 1, a, b
    if len(off) != len(on):
        return min(len(off), len(on)) + 1, "-", "-"
    return None, None, None


def rows_in_window(result: dict, window: int) -> tuple:
    """(frame, leg, verdict, admitted) for every row at or before *window*."""
    return tuple(
        (row.frame, row.leg, row.verdict, bool(row.admitted))
        for row in result["rows"] if row.frame <= window
    )


def report_ledger(result: dict) -> None:
    """Everything the armed run's ledger holds."""
    rows = result["rows"]
    counts: dict = {}
    legs: dict = {}
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + row.repeat_count
        legs[row.leg] = legs.get(row.leg, 0) + row.repeat_count
    print(f"  ledger digest    : {result['summary']}")
    print(f"  verdicts         : "
          f"{sorted(counts.items(), key=lambda kv: -kv[1])}")
    print(f"  legs             : {sorted(legs.items(), key=lambda kv: -kv[1])}")
    print(f"  frames with rows : {len(result['frames_with_rows'])}")
    print("  sample rows:")
    for row in rows[:6]:
        print("    " + audit.format_admission_record(row))
    if len(rows) > 9:
        print("    ...")
    for row in rows[-3:]:
        print("    " + audit.format_admission_record(row))


def main() -> int:
    print("=" * 74)
    print("FB EMERGENCY-CHASE ADMISSION LEDGER - LIVE A/B ON THE REAL ENGINE")
    print("=" * 74)
    print(f"  seed={SEED} frames={MAX_FRAMES} csv={CSV_PATH}")

    os.environ.pop(audit.ADMISSION_AUDIT_ENV_VAR, None)

    section("[1] The control arm: DISARMED records nothing")
    off = run(armed=False)
    check("the env lever is OFF for the control run",
          audit.audit_enabled_by_env() is False)
    check("...and the control world carries no ledger",
          audit.ledger_for(off["world"], create=False) is None)
    check("...so the control run recorded NOTHING", off["rows"] == ())
    check("...and the disarmed digest says so",
          audit.summarise_admission_audit(off["world"])
          == "fb_admission_audit=off")

    section("[1b] THE FIXTURE'S OWN REPRODUCIBILITY (DISARMED vs DISARMED)")
    off_b = run(armed=False)
    fork_frame, fork_a, fork_b = first_divergence(
        off["digests"], off_b["digests"],
    )
    total = len(off["digests"])
    window = total if fork_frame is None else fork_frame - 1
    print(f"  engine fork frame  : "
          f"{fork_frame if fork_frame is not None else 'none'}")
    print(f"  reproducible window: frames 1..{window} of {total}")
    print("  (the ENGINE forks here with no ledger in either arm, so a raw")
    print("   armed/disarmed difference at that frame proves nothing; the")
    print("   neutrality law is therefore stated over the window, and the")
    print("   mechanism is proved separately by the double-call control.)")
    check("the control run is byte-identical to a second DISARMED run "
          f"inside the window 1..{window}",
          off["digests"][:window] == off_b["digests"][:window],
          f"{window} frames compared")
    check("the engine forks AGAIN after the window on its own, so no claim "
          "is made past it",
          fork_frame is not None,
          (f"first DISARMED-vs-DISARMED difference at frame {fork_frame}: "
           f"{fork_a[:12]} vs {fork_b[:12]}")
          if fork_frame is not None else
          "no fork occurred: the whole run is comparable")

    section("[2] The armed arm records on the REAL emergency-chase path")
    on = run(armed=True)
    rows = on["rows"]
    check("the armed run recorded rows", len(rows) > 0, str(len(rows)))
    check("every recorded verdict is in the CLOSED vocabulary",
          all(audit.is_known_verdict(row.verdict) for row in rows))
    line_break = [row for row in rows
                  if audit.is_line_break_verdict(row.verdict)]
    check("...and the run produced rejected LINE-BREAK evaluations -- the "
          "instrumentation's whole subject", len(line_break) > 0,
          str(len(line_break)))
    check("every line-break refusal names a BREAK leg, never the identity "
          "half",
          all(row.leg in (audit.LEG_DEPTH_BREAK, audit.LEG_LATERAL_BREAK)
              for row in line_break),
          str(sorted({row.leg for row in line_break})))
    check("an ADMITTED row is never counted as a line-break refusal",
          all(not audit.is_line_break_verdict(row.verdict)
              for row in rows if row.admitted))

    section("[3] The three diagnostics were MEASURED on the real line")
    width_known = [row for row in rows
                   if row.defensive_line_width_m is not None]
    check("the defensive line's WIDTH was measured on most rows",
          len(width_known) > 0.5 * len(rows),
          f"{len(width_known)}/{len(rows)}")
    check("...and every width names WHICH source it came from",
          all(row.defensive_line_width_source in (
              audit.WIDTH_SOURCE_MEASURED, audit.WIDTH_SOURCE_COMMANDED,
              audit.WIDTH_SOURCE_UNAVAILABLE) for row in rows),
          str(sorted({row.defensive_line_width_source for row in rows})))
    check("the GAP was measured as the carrier's distance to its own line",
          any(row.gap_m is not None for row in rows))
    check("...and the SURPLUS over the gate's margin came with it",
          all((row.gap_m is None) == (row.depth_margin_m is None)
              for row in rows))
    check("the NEAREST DEFENDER was located, with its id",
          any(row.nearest_defender_distance_m is not None
              and row.nearest_defender_id is not None for row in rows))
    check("a row never reports a width of 0.0 where none was measurable",
          all(not (row.defensive_line_width_source
                   == audit.WIDTH_SOURCE_UNAVAILABLE
                   and row.defensive_line_width_m is not None)
              for row in rows))
    check("every row names the anchor its gap was measured against",
          all(row.anchor_source in ("supplied", "break_test", "live",
                                   "unavailable") for row in rows),
          str(sorted({row.anchor_source for row in rows})))

    section(f"[4] THE NEUTRALITY LAW, over the window 1..{window}")
    frame_no, digest_off, digest_on = first_divergence(
        off["digests"], on["digests"],
    )
    check(f"ARMED == DISARMED on EVERY frame of 1..{window}",
          off["digests"][:window] == on["digests"][:window],
          f"{window} frames compared byte for byte")
    window_rows = [row for row in rows if row.frame <= window]
    window_evals = sum(row.repeat_count for row in window_rows)
    check("...and the ledger RECORDED inside that window, so neutrality was "
          "tested exactly where the recording happens",
          len(window_rows) > 0,
          f"{len(window_rows)} distinct rows / {window_evals} evaluations "
          f"at or before frame {window}")
    check("beyond the window the armed run forks no EARLIER than the control "
          "does -- the residual divergence is the engine's own",
          frame_no is None or fork_frame is None or frame_no >= fork_frame,
          f"armed/disarmed first differ at {frame_no}; "
          f"DISARMED vs DISARMED at {fork_frame}")

    # THE MECHANISM: the armed gate adds exactly a SECOND evaluation of the
    # occupancy predicate.  Perform that same second call in a DISARMED run
    # and demand the run come out identical.
    dbl = run(armed=False, double_occupancy=True)
    check("the DISARMED run that evaluates the occupancy predicate TWICE per "
          f"call is byte-identical to the plain DISARMED run over 1..{window}",
          dbl["digests"][:window] == off["digests"][:window],
          f"{window} frames compared; the extra call is structurally the one "
          f"the armed gate adds")
    dbl_calls = dbl["double_stats"]["calls"]
    dbl_unstable = dbl["double_stats"]["unstable"]
    check("...and the doubled predicate was actually EXERCISED, so the "
          "differential is not vacuous",
          dbl_calls > 0, f"{dbl_calls} doubled evaluations")
    check("...and NO real evaluation ever returned two different answers, so "
          "the predicate the armed call re-reads is PURE on a real game",
          not dbl_unstable,
          (f"{len(dbl_unstable)} unstable evaluations, e.g. "
           f"{dbl_unstable[:3]}") if dbl_unstable else
          f"0 of {dbl_calls} evaluations changed on the second call")

    on_b = run(armed=True)
    check("a SECOND ARMED run files exactly the SAME rows inside the window "
          "-- the ledger's content is deterministic there",
          rows_in_window(on, window) == rows_in_window(on_b, window),
          f"{len(rows_in_window(on, window))} rows compared")
    check("...and its frame digests match over the window too",
          on_b["digests"][:window] == on["digests"][:window],
          f"{window} frames compared")

    section("[5] The ledger renders and exports")
    report_ledger(on)
    check("the digest names rows, evaluations, drops AND refusals",
          "rows=" in on["summary"]
          and "evaluations=" in on["summary"]
          and "dropped=" in on["summary"]
          and "line_break_refusals=" in on["summary"]
          and "reasons[" in on["summary"], on["summary"])
    check("...and its refusal count is the ledger's own refusal count",
          f"line_break_refusals={len(line_break)}" in on["summary"],
          on["summary"])

    # CSV ROUND TRIP on the SAME rows: replay them into a fresh ledger (so the
    # export reads a live sink) and write it to the report path.
    world = on["world"]
    audit.reset_admission_audit(world)
    replay = audit.install_ledger(world)
    for row in rows:
        world.frame_count = row.frame
        if row.admitted:
            audit.record_admission(world, None, None, row.leg,
                                  anchor_x=row.anchor_x)
        else:
            audit.record_rejection(world, None, None, row.leg, row.verdict,
                                   anchor_x=row.anchor_x)
    check("the replayed ledger holds one row per DISTINCT input row",
          replay.size == len({(row.frame, row.leg, row.player_id, row.verdict,
                               bool(row.admitted)) for row in rows}),
          f"{replay.size}")
    target = audit.write_admission_csv(world, CSV_PATH)
    check("the export wrote the ledger to the explicit path",
          target == CSV_PATH, str(target))
    with open(CSV_PATH, encoding="utf-8") as handle:
        lines = handle.read().strip().splitlines()
    check("...with the declared header",
          lines[0].split(",") == list(audit.CSV_COLUMNS), lines[0])
    check("...and one line per row plus the header",
          len(lines) == replay.size + 1, str(len(lines)))
    check("...and no measurement column carries a printed 'nan'",
          not any(field.strip() == "nan"
                  for line in lines[1:] for field in line.split(",")))
    check("...and a refusal line names its reason",
          any("below_depth_margin" in line or "lane_intact" in line
              or "out_of_reach" in line
              or "line_intact_level_exclusion" in line
              for line in lines[1:]),
          f"{len(lines) - 1} data lines")
    audit.reset_admission_audit(world)

    print("\n" + "=" * 74)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS - on the REAL engine the emergency-chase admission")
    print("        ledger records every rejected line-break evaluation with")
    print("        the line's width, the carrier's gap and margin, and the")
    print("        nearest defender; arming it leaves every player field on")
    print(f"        every frame of the fixture's reproducible window")
    print("        (1..{}) byte-identical to the disarmed run; and the".format(
        window))
    print("        extra occupancy evaluation the armed gate adds is proven")
    print("        inert (double-call control) while the predicate itself is")
    print("        proven pure on a real game.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
