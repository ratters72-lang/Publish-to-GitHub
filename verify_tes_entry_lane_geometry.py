"""verify_tes_entry_lane_geometry.py — TES CHAMBER-ENTRY LANE-GEOMETRY verifier.

WHY THIS EXISTS
---------------
``verify_tes_lane_true_retreat.py`` validates the STABILITY of the latched
lanes, not the CORRECTNESS of the line geometry those lanes are seeded
from.  Every one of its clauses is measured against the seed it takes on
the chamber-entry frame:

  * clause C — the latch does not change inside a chamber;
  * clause D — gaps between LOCKED lanes do not move (it compares the
    present gap to the SEED gap, so a compressed line has a compressed
    baseline and passes);
  * clause E — the latch equals the body's OWN assigned lane.

A line that is laterally compressed on the entry frame therefore satisfies
every clause.  Measured live (``probe_tes_entry_compression.py``): the
actual adjacent gaps are 0.00-1.22 m against the shipped count-lane model
gap of ``defender_gap_m`` (4.0 m), and two-to-three line bodies routinely
share one lane.

WHICH GEOMETRY IS GRADED
------------------------
Under the RECOVERY-OWNERSHIP model the chamber-entry CENSUS — not the
bodies' live positions — is the lane authority.  ``establish_slot_ownership``
matches the bodies of each side, innermost-first, onto the count-lane model
``side_lanes(n_side, ruck_y, side)`` and LATCHES that assignment for the
chamber.  A body displaced at entry therefore no longer has to already sit
on the model: the hold drives it onto its OWN latched lane over the chamber
(the "displaced defenders recover to their assigned slot" clause, proved by
``verify_tes_slot_recovery_ownership.py``).

The clauses below therefore grade the LATTICE THE CENSUS PUBLISHED — the
``SLOT_LADDER_ATTR`` table — and each body's live displacement from its
assigned slot is reported as the recovery pull rather than graded, because
grading it would demand that the line is already formed at entry — exactly
the premise this stage removes.

WHAT THIS VERIFIER ASSERTS
--------------------------
On the TESTED chamber-entry frame (the first drive frame of every chamber),
for each side of the published slot ladder (``slot_ladder_for(world)``):

  F1  ASSIGNED LANE CORRECTNESS — the side's entries carry a CONTIGUOUS
      index run (0..n-1, no gap, no duplicate); the innermost entry sits on
      the ANCHOR LAW lane ``ruck_y ± RUCK_ADJACENT_ANCHOR_M``; every
      interior step between consecutive entries is the shipped count-lane
      gap ``defender_gap_m``; and no two owned bodies share one ladder
      entry.  A census anchored on the wrong ruck, built with the wrong
      gap, or one that drops/duplicates an entry fails here.
  F2  NO COMPRESSION — no two consecutive lanes sit closer than
      ``MIN_GAP_M``, except where ``side_lanes`` has itself clamped the
      outer lanes onto the sideline — a saturation of the ladder, not an
      ownership fault.
  F3  MODEL SPAN — the side's span is ``defender_gap_m * (n - 1)`` within
      ``SPAN_TOL_M`` (again modulo sideline clamping).

Exit 0 = every published chamber-entry ladder obeys the count-lane model.
Exit 1 = a clause is violated (the chamber then holds a malformed lattice).
Exit 2 = no drive chamber observed.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod
ope_mod.DEBUG_SDEA_PHYSICS = False

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
from game.defence.defence_lvl_1 import tes_drive_structure as tds
from game.defence.defence_lvl_1.defensive_split_spacing import (
    LEFT, RIGHT, RUCK_ADJACENT_ANCHOR_M,
)

DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
LINE_ROLES = ("DEF_LINE_READY", "DEF_CHASE", "DEF_RETREAT_10")
SLOT_ROLES = ("A", "B", "C", "D", "CENTRE", "WINGER")

LANE_TOL_M = 1.5      # F1: assigned lane / gap vs the count-lane model
MIN_GAP_M = 2.5       # F2: closest allowed pair on a side (model gap - 1.5)
SPAN_TOL_M = 4.0      # F3: |assigned span - model span|
MAX_FRAMES = 3000
ENGINE_LOG = "tmp_verify_tes_entry_lane_engine.log"


def _role_name(p):
    role = getattr(p, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


def boot_world():
    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()
    return world, ball, open_play, kickoff_engine, collision_system


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def slot_bodies(world):
    """Role-carrying line bodies — the bodies that occupy a count-lane slot."""
    bc = world.ball_carrier
    out = []
    for d in world.defending_team:
        if _role_name(d) not in LINE_ROLES:
            continue
        if getattr(d, "fullback_role_active", False):
            continue
        if d is bc:
            continue
        if getattr(d, "depth_role", None) not in SLOT_ROLES:
            continue
        out.append(d)
    return out


def _side_entries(ladder, side):
    """Plain copies of one side's published ladder entries."""
    if not isinstance(ladder, dict):
        return []
    sides = ladder.get("sides") or {}
    raw = sides.get(side) or sides.get(int(side)) or []
    out = []
    for e in raw:
        out.append({
            "index": e.get("index"),
            "y": _f(e.get("y")),
            "owner_id": e.get("owner_id"),
        })
    return sorted(out, key=lambda e: (e["index"] is None, e["index"]))


def _chamber_entries():
    """Return one record per TES chamber entry."""
    buf = io.StringIO()
    records = []
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        tuners = getattr(open_play, "def_tuners", None)
        dt = 1.0 / 60.0
        prev_cid = None
        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(dt)
            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)
            if open_play._phase not in DRIVE_PHASES:
                continue
            cid = getattr(world, tds.CHAMBER_ID_ATTR, None)
            if cid == prev_cid:
                continue
            prev_cid = cid
            # The census anchors the ladder on the band reference, NOT on
            # the raw ruck_m the harness reports; both are printed so a
            # disagreement between them is visible rather than hidden.
            anchor = _f(getattr(world, "ruck_y_m", 35.0))
            try:
                anchor = _f(tds.resolve_band_ref_y(
                    world, world.ball_carrier, tuners), anchor)
            except Exception:
                pass
            ladder = tds.slot_ladder_for(world)
            records.append({
                "cid": cid,
                "frame": world.frame_count,
                "ruck_y": anchor,
                "live_ruck_y": _f(getattr(world, "ruck_y_m", 35.0)),
                "tuners": tuners,
                "ladder": {
                    "seq": (ladder or {}).get("seq"),
                    "ruck_y": _f((ladder or {}).get("ruck_y")),
                    "sides": {
                        side: _side_entries(ladder, side)
                        for side in (LEFT, RIGHT)
                    } if isinstance(ladder, dict) else {},
                } if isinstance(ladder, dict) else None,
                "rows": [
                    {
                        "pid": getattr(d, "player_id", None),
                        "role": getattr(d, "depth_role", None),
                        "y": _f(getattr(d, "y", 0.0)),
                        "slot_y": tds.owned_slot_y(world, d),
                        "slot_side": getattr(d, tds.SLOT_SIDE_ATTR, None),
                        "slot_index": getattr(d, tds.SLOT_INDEX_ATTR, None),
                    }
                    for d in slot_bodies(world)
                ],
            })
    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    return records


def _at_sideline(y, edge_lo, edge_hi, tol):
    """True when the ladder clamped this lane onto the touchline."""
    return y <= edge_lo + tol or y >= edge_hi - tol


def main():
    records = _chamber_entries()
    print("=" * 78)
    print("TES CHAMBER-ENTRY LANE-GEOMETRY VERIFIER")
    print("=" * 78)
    if not records:
        print("\nRESULT: FAILED — no TES drive chamber reached.")
        return 2

    fails = []
    print(f"chambers observed : {len(records)}")
    print(f"model gap         : "
          f"{float(getattr(records[0]['tuners'], 'defender_gap_m', 4.0)):.2f} m")
    print(f"anchor law        : ruck_y +/- {RUCK_ADJACENT_ANCHOR_M:.2f} m")
    print(f"tolerances        : lane {LANE_TOL_M} m, min gap {MIN_GAP_M} m, "
          f"span {SPAN_TOL_M} m")

    for i, rec in enumerate(records, 1):
        tuners = rec["tuners"]
        ruck_y = rec["ruck_y"]
        rows = rec["rows"]
        ladder = rec["ladder"]
        gap = float(getattr(tuners, "defender_gap_m", 4.0) or 4.0)
        fw = float(getattr(tuners, "field_width_m", 70.0) or 70.0)
        mg = float(getattr(tuners, "sideline_margin_m", 3.0) or 0.0)
        edge_lo, edge_hi = mg, fw - mg
        unowned = [r["pid"] for r in rows if r["slot_y"] is None]
        print("\n" + "-" * 78)
        print(f"CHAMBER {i}  cid={rec['cid']}  entry frame={rec['frame']}  "
              f"census_ruck={ruck_y:.2f}  live_ruck={rec['live_ruck_y']:.2f}  "
              f"role-bodies={len(rows)}  owned={len(rows) - len(unowned)}  "
              f"unowned={unowned}")
        if ladder is None:
            fails.append(
                f"F1: chamber {rec['cid']} published NO slot ladder at entry"
            )
            continue
        if ladder["seq"] != rec["cid"]:
            fails.append(
                f"F1: chamber {rec['cid']} ladder seq={ladder['seq']} != chamber"
            )

        # Every owned role body must sit on exactly one ladder entry.
        claimed = {}
        for r in rows:
            if r["slot_y"] is None:
                continue
            key = (r["slot_side"], r["slot_index"])
            if key in claimed:
                fails.append(
                    f"F1: chamber {rec['cid']} slot {key} is claimed by both "
                    f"pid {claimed[key]} and pid {r['pid']}"
                )
            claimed[key] = r["pid"]
        worst_pull = 0.0
        for side, side_name in ((LEFT, "L"), (RIGHT, "R")):
            entries = _side_entries(ladder, side)
            if not entries:
                print(f"  side {side_name}: (no ladder entries)")
                continue
            ys = [e["y"] for e in entries]
            idxs = [e["index"] for e in entries]
            print(f"  side {side_name}: n={len(entries)} lanes="
                  f"{[round(v, 2) for v in ys]}  indices={idxs}  "
                  f"owners={[e['owner_id'] for e in entries]}")

            # F1a — contiguous index run.
            if idxs != list(range(len(idxs))):
                fails.append(
                    f"F1: chamber {rec['cid']} side {side_name} slot indices "
                    f"are not a contiguous 0..n-1 run: {idxs}"
                )
            # F1b — ANCHOR LAW on the innermost lane.
            want_inner = ruck_y + side * RUCK_ADJACENT_ANCHOR_M
            if abs(ys[0] - want_inner) > LANE_TOL_M:
                fails.append(
                    f"F1: chamber {rec['cid']} side {side_name} innermost lane "
                    f"{ys[0]:.2f} is not the anchor law lane "
                    f"{want_inner:.2f}"
                )
            # F1c — uniform count-lane gap, modulo sideline clamping.
            for j in range(1, len(ys)):
                step = abs(ys[j] - ys[j - 1])
                if (_at_sideline(ys[j], edge_lo, edge_hi, gap)
                        or _at_sideline(ys[j - 1], edge_lo, edge_hi, gap)):
                    continue
                if abs(step - gap) > LANE_TOL_M:
                    fails.append(
                        f"F1: chamber {rec['cid']} side {side_name} lane step "
                        f"{step:.2f} m != model gap {gap:.2f} m "
                        f"(index {j})"
                    )
            # F2 — no compression away from the sideline.
            if len(ys) > 1:
                steps = [abs(ys[j] - ys[j - 1]) for j in range(1, len(ys))]
                worst_step = min(steps)
                clamped = all(
                    _at_sideline(ys[j], edge_lo, edge_hi, gap)
                    or _at_sideline(ys[j - 1], edge_lo, edge_hi, gap)
                    for j in range(1, len(ys))
                    if abs(ys[j] - ys[j - 1]) < MIN_GAP_M
                )
                if worst_step < MIN_GAP_M and not clamped:
                    fails.append(
                        f"F2: chamber {rec['cid']} side {side_name} compressed: "
                        f"closest lanes {worst_step:.2f} m < {MIN_GAP_M} m"
                    )
            # F3 — model span.
            span = max(ys) - min(ys)
            want_span = gap * (len(ys) - 1)
            dev = abs(span - want_span)
            if dev > SPAN_TOL_M:
                fails.append(
                    f"F3: chamber {rec['cid']} side {side_name} span deviation "
                    f"{dev:.2f} m > {SPAN_TOL_M} m (assigned {span:.2f} vs "
                    f"model {want_span:.2f})"
                )
        for r in rows:
            if r["slot_y"] is None:
                continue
            worst_pull = max(worst_pull, abs(r["y"] - r["slot_y"]))
        print(f"  worst entry displacement from the assigned slot: "
              f"{worst_pull:.2f} m (recovered by the hold, not graded)")

    print("\n" + "=" * 78)
    if not fails:
        print("RESULT: ENTRY SLOT LATTICE CORRECT — the chamber-entry census "
              "published a count-lane ladder on every side (anchor, uniform "
              "gap, contiguous indices, one body per entry); each body's live "
              "displacement from its assigned slot is the recovery the hold "
              "then performs.")
        return 0
    print(f"RESULT: {len(fails)} VIOLATION(S) — the chamber-entry slot "
          f"lattice does NOT match the count-lane model:")
    for f in fails[:30]:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
