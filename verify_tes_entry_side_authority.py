"""verify_tes_entry_side_authority.py — TES CHAMBER-ENTRY SIDE authority verifier.

WHY THIS EXISTS
---------------
``probe_tes_entry_side_ownership.py`` measures the drive-chamber entry and
reports, live: 1 of 1103 drive frames carries a fresh lane dict, 8 of 9
chambers enter with ``frame_spacing_targets is None`` (every line body absent
from the ladder), 8 of 9 chambers enter with a body whose ``depth_side``
contradicts its own live side, and 15 side flips occur inside the chambers.

Those were SYMPTOMS of one contract break: the un-gated preamble was the
single authority for BOTH halves of lateral ownership — the SIDE (via
``assign_depth_roles`` -> ``assign_split_sides`` stamping ``d.depth_side``)
and the LANE (via ``world.frame_spacing_targets``), and the TES early-return
in ``run_defensive_pipeline`` sat after the first and before the second.

WHAT THIS VERIFIER ASSERTS NOW
------------------------------
Under the RECOVERY-OWNERSHIP model the chamber-entry CENSUS
(``establish_slot_ownership``) is the single side/lane authority: it matches
the bodies of each side, innermost-first, onto the count-lane model, and
LATCHES side + index + lane for the whole chamber.  The legacy
``frame_spacing_targets`` lane dict is therefore legitimately ABSENT during a
drive chamber (the census ladder replaced it), and the legacy ``depth_side``
stamp is no longer a TES authority at all: neither ``tes_drive_structure``
nor ``defence_manager.update_tes_drive`` reads it, and the engine's only
mid-chamber writer is the deliberate overload flipper in
``open_play_engine``.

So this verifier grades the SIDE-facing half of the census — the half its
name owns — and reports the legacy signals as attributed diagnostics:

  V1  ENTRY SIDE ASSIGNMENT COMPLETE — on every chamber-entry frame the
      published ladder exists and is the current chamber's, each side's
      index run is contiguous 0..n-1, and every body carrying the hold
      stamp holds a (side, index) that EXISTS in the ladder, with a lane Y
      equal to the ladder's lane for that entry.  A body whose side/index
      stamp names an entry the ladder does not contain is side-owned by
      nothing.
  V2  SIDE IS LATCHED — no owned body's census side changes while the
      chamber is live.
  V3  LADDER IS LATCHED — the published ladder never changes while the
      chamber is live: same per-side count, same lane Ys, same owners.
      Re-solving it mid-chamber is the chamber-wide rebalancing /
      dynamic spacing redistribution this stage forbids.
  V4  DIAGNOSTIC (reported, never graded) — the legacy ``depth_side``
      re-solves and the legacy lane dict's absence, both with attribution.

Exit 0 = the entry side authority is intact.  Exit 1 = a clause is violated,
with the offending bodies listed.  Exit 2 = no drive chamber observed.
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

DRIVE_PHASES = ("TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
LANE_MATCH_TOL_M = 1e-6     # V1: stamp lane vs the ladder's lane
MAX_FRAMES = 3000
ENGINE_LOG = "tmp_verify_tes_entry_side_authority_engine.log"


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


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


def eligible_bodies(world):
    """Structure-hold-eligible line bodies of the defending team."""
    bc = world.ball_carrier
    return [
        d for d in world.defending_team
        if tds.is_structure_hold_eligible(d) and d is not bc
    ]


def ladder_map(ladder):
    """{(side, index): lane_y} for the published ladder."""
    out = {}
    if not isinstance(ladder, dict):
        return out
    for side, side_entries in (ladder.get("sides") or {}).items():
        for e in side_entries:
            out[(side, e.get("index"))] = _f(e.get("y"))
    return out


def ladder_signature(ladder):
    """Canonical, comparable form of the whole published ladder."""
    if not isinstance(ladder, dict):
        return None
    sides = {}
    for side, side_entries in (ladder.get("sides") or {}).items():
        sides[side] = tuple(
            (e.get("index"), round(_f(e.get("y")), 6), e.get("owner_id"))
            for e in side_entries
        )
    return (ladder.get("seq"), tuple(sorted(sides.items())))


def collect():
    """Walk the harness and return (entries, side flips, ladder changes, diag)."""
    entries = []
    side_flips = []
    ladder_changes = []
    diag = {
        "depth_side_flips": [],
        "legacy_dict_frames": 0,
        "legacy_ready_frames": 0,
        "drive_frames": 0,
    }
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        world, ball, open_play, kickoff_engine, collision_system = boot_world()
        dt = 1.0 / 60.0
        prev_cid = None
        chamber_frames = {}
        chamber_sides = {}
        chamber_ladder = {}
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
                prev_cid = None
                continue

            cid = getattr(world, tds.CHAMBER_ID_ATTR, None)
            frame = int(world.frame_count)
            ruck_y = _f(getattr(world, "ruck_y_m", 35.0))
            ladder = tds.slot_ladder_for(world)
            bodies = eligible_bodies(world)
            diag["drive_frames"] += 1
            if not isinstance(getattr(world, "frame_spacing_targets", None), dict):
                diag["legacy_dict_frames"] += 1
            if not bool(getattr(world, "frame_geometry_ready", False)):
                diag["legacy_ready_frames"] += 1

            if cid != prev_cid:
                prev_cid = cid
                chamber_frames[cid] = frame
                chamber_sides[cid] = {}
                chamber_ladder[cid] = ladder_signature(ladder)
                entries.append({
                    "cid": cid,
                    "frame": frame,
                    "ruck_y": ruck_y,
                    "ladder": ladder,
                    "rows": [
                        {
                            "pid": getattr(d, "player_id", None),
                            "held": bool(getattr(d, tds.HOLD_ACTIVE_ATTR,
                                                 False)),
                            "slot_y": tds.owned_slot_y(world, d),
                            "side": getattr(d, tds.SLOT_SIDE_ATTR, None),
                            "index": getattr(d, tds.SLOT_INDEX_ATTR, None),
                            "live_side": -1 if _f(d.y) < ruck_y else 1,
                        }
                        for d in bodies
                    ],
                })
                for r in entries[-1]["rows"]:
                    if r["held"]:
                        chamber_sides[cid][r["pid"]] = r["side"]

            # V3 — the ladder must stay latched for the chamber.
            sig = ladder_signature(ladder)
            if chamber_ladder.get(cid) is not None and sig != chamber_ladder[cid]:
                ladder_changes.append({"cid": cid, "frame": frame})
                chamber_ladder[cid] = sig

            for d in bodies:
                pid = getattr(d, "player_id", None)
                if pid not in chamber_sides.get(cid, {}):
                    continue
                side = getattr(d, tds.SLOT_SIDE_ATTR, None)
                prev = chamber_sides[cid].get(pid)
                if side in (-1, 1) and prev in (-1, 1) and side != prev:
                    side_flips.append({
                        "cid": cid, "frame": frame, "pid": pid,
                        "from": prev, "to": side,
                    })
                chamber_sides[cid][pid] = side
                # V4 diagnostic only — the legacy stamp is not a TES authority.
                ds = getattr(d, "depth_side", None)
                ds_prev = diag.setdefault("_last_ds", {}).get((cid, pid))
                if ds in (-1, 1) and ds_prev in (-1, 1) and ds != ds_prev:
                    diag["depth_side_flips"].append({
                        "cid": cid, "frame": frame, "pid": pid,
                        "from": ds_prev, "to": ds,
                    })
                diag.setdefault("_last_ds", {})[(cid, pid)] = ds
    diag.pop("_last_ds", None)
    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    return entries, side_flips, ladder_changes, diag


def main():
    entries, side_flips, ladder_changes, diag = collect()
    print("=" * 78)
    print("TES CHAMBER-ENTRY SIDE AUTHORITY VERIFIER")
    print("=" * 78)
    if not entries:
        print("\nRESULT: FAILED — no TES drive chamber reached.")
        return 2

    fails = []
    print(f"chambers observed : {len(entries)}")
    print(f"tolerances        : lane match {LANE_MATCH_TOL_M} m, "
          f"latched side changes 0, ladder changes 0")

    for i, rec in enumerate(entries, 1):
        cid = rec["cid"]
        rows = rec["rows"]
        lm = ladder_map(rec["ladder"])
        pool = [r for r in rows if r["held"]]
        owned = [r for r in pool if r["side"] in (-1, 1)
                 and isinstance(r["index"], int)]
        unowned = [r["pid"] for r in pool if r["side"] not in (-1, 1)
                   or not isinstance(r["index"], int)]
        unknown = [r["pid"] for r in owned
                   if (r["side"], r["index"]) not in lm]
        lane_dev = [
            (r["pid"], abs(_f(r["slot_y"]) - lm[(r["side"], r["index"])]))
            for r in owned if (r["side"], r["index"]) in lm
        ]
        worst_lane = max((d for _, d in lane_dev), default=0.0)
        # Every side's index run must be contiguous 0..n-1.
        runs = {}
        for (side, index), _y in lm.items():
            runs.setdefault(side, []).append(index)
        bad_runs = {
            side: sorted(idxs) for side, idxs in runs.items()
            if sorted(idxs) != list(range(len(idxs)))
        }
        print("\n" + "-" * 78)
        print(f"CHAMBER {i}  cid={cid}  entry frame={rec['frame']}  "
              f"ruck_y={rec['ruck_y']:.2f}  eligible={len(rows)}  "
              f"held={len(pool)}  owned={len(owned)}  unowned={unowned}")
        print(f"  V1 ladder seq={rec['ladder'].get('seq') if isinstance(rec['ladder'], dict) else None}  "
              f"lanes={len(lm)}  index-runs={ {s: sorted(v) for s, v in runs.items()} }  "
              f"worst lane dev={worst_lane:.3f} m")
        if not isinstance(rec["ladder"], dict):
            fails.append(
                f"V1: chamber {cid} entered with NO published ladder for "
                f"{len(pool)} held bodies"
            )
        else:
            if rec["ladder"].get("seq") != cid:
                fails.append(
                    f"V1: chamber {cid} ladder seq="
                    f"{rec['ladder'].get('seq')} != chamber"
                )
            if bad_runs:
                fails.append(
                    f"V1: chamber {cid} side index runs are not contiguous "
                    f"0..n-1: {bad_runs}"
                )
            if unowned:
                fails.append(
                    f"V1: chamber {cid} held bodies with no census side/index: "
                    f"{unowned}"
                )
            if unknown:
                fails.append(
                    f"V1: chamber {cid} bodies stamped onto ladder entries "
                    f"that do not exist: {unknown}"
                )
            off = [pid for pid, d in lane_dev if d > LANE_MATCH_TOL_M]
            if off:
                fails.append(
                    f"V1: chamber {cid} bodies whose stamped lane differs from "
                    f"the ladder lane: {off}"
                )
        for r in sorted(rows, key=lambda r: (-r["live_side"], r["pid"] or 0)):
            if not r["held"]:
                continue
            side_s = {-1: "L", 1: "R"}.get(r["side"], "-")
            live_s = {-1: "L", 1: "R"}.get(r["live_side"], "-")
            lane = lm.get((r["side"], r["index"]))
            lane_s = "-" if lane is None else f"{lane:.2f}"
            print(f"  pid {str(r['pid']):<5} side={side_s} index="
                  f"{str(r['index']):<3} lane={lane_s:>7} live={live_s}")

    # V2 — the census side must not move while the chamber is live.
    print("\n" + "-" * 78)
    print(f"V2 latched census-side changes : {len(side_flips)}")
    for f in side_flips[:20]:
        print(f"     chamber {f['cid']} frame {f['frame']} pid {f['pid']} "
              f"side {f['from']} -> {f['to']}")
    if side_flips:
        fails.append(
            f"V2: {len(side_flips)} latched census-side change(s) — the "
            f"chamber's side ownership was re-solved while live"
        )

    # V3 — the ladder itself must not move while the chamber is live.
    print(f"V3 published-ladder changes    : {len(ladder_changes)}")
    for c in ladder_changes[:10]:
        print(f"     chamber {c['cid']} frame {c['frame']} ladder re-published")
    if ladder_changes:
        fails.append(
            f"V3: {len(ladder_changes)} published-ladder change(s) — the "
            f"chamber re-balanced its slot lattice while live"
        )

    # V4 — diagnostics, never graded.
    print(f"V4 diagnostic: legacy depth_side re-solves (not graded) : "
          f"{len(diag['depth_side_flips'])}")
    for f in diag["depth_side_flips"][:20]:
        print(f"     chamber {f['cid']} frame {f['frame']} pid {f['pid']} "
              f"{f['from']} -> {f['to']}")
    print(f"V4 diagnostic: drive frames without the legacy lane dict : "
          f"{diag['legacy_dict_frames']}/{diag['drive_frames']} "
          f"(frame_geometry_ready false on "
          f"{diag['legacy_ready_frames']}) — expected: the census ladder is "
          f"the drive lateral authority")

    print("\n" + "=" * 78)
    if not fails:
        print("RESULT: ENTRY SIDE AUTHORITY INTACT — every chamber entered "
              "with a current ladder whose entries every held body is "
              "stamped onto, on a contiguous per-side index run, and both "
              "the census side and the ladder stayed latched for the whole "
              "chamber.")
        return 0
    print(f"RESULT: {len(fails)} VIOLATION(S) — the TES chamber-entry side "
          f"authority is broken:")
    for f in fails[:40]:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
