"""verify_tes_slot_recovery_ownership.py — TES RECOVERY-OWNERSHIP verifier.

CONTRACT UNDER TEST
-------------------
During the TES drive chamber (TACKLE_CONTACT / TACKLE_DRIVE /
TACKLE_DOMINANCE) the drive hold's lateral cell is each line body's
ASSIGNED SLOT, produced by a ONE-PASS CHAMBER-ENTRY CENSUS and then
IMMUTABLE for the chamber:

  * defenders already correctly slotted RETAIN ownership and hold shape;
  * displaced defenders RECOVER to their ORIGINALLY LATCHED slot;
  * vacant slots remain vacant and keep their owner identity;
  * there is no chamber-wide rebalancing and no dynamic spacing
    redistribution.

CLAUSES
-------
  V0  SINGLE SOURCE      — the census function is the only writer of the
                           ownership table; no other engine module even
                           mentions the ownership attributes (static scan).
  V1  CENSUS AT ENTRY    — on every chamber-entry frame each eligible line
                           body owns exactly one slot and every ladder
                           entry has an owner; owner set == entry line set.
  V2  RETAIN             — a body already on its slot (within tolerance at
                           entry) never leaves it for the chamber.
  V3  RECOVER            — a body displaced at entry is driven toward its
                           OWN slot: never overshoots, never parked.
  V4  IMMUTABLE / VACANCY— side / index / slot-Y per body are byte-stable
                           for the chamber (zero reassignments); a slot
                           whose owner is absent is never occupied or
                           targeted by another body.

Exit 0 = contract intact.  Exit 1 = violation (listed).  Exit 2 = no drive
chamber observed.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import contextlib
import io
import math
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
MAX_FRAMES = 3000
ENGINE_LOG = "tmp_verify_tes_slot_recovery_engine.log"

# Tolerances.
RETAIN_TOL_M = 0.6      # V2: how far a "correctly slotted" body may sit
SLOT_FREEZE_TOL_M = 0.5  # authority's freeze band (tes_slot_recovery_tolerance_m)
PARKED_MIN_M = 0.7      # V3: beyond freeze band + slack, movement is required
PARKED_STALL_FRAMES = 45  # V3: ~0.75 s of graded frames beyond PARKED_MIN_M
PARKED_NET_M = 0.10     # V3: net Y travel under this over that run == parked
LANE_DUP_M = 0.30       # V4: two bodies within this share a lane
IMMUT_TOL_M = 1e-6      # V4: byte-stability of the latched slot
ENTRY_TOL_M = 0.6       # V2/V3 split: entry err above this == "displaced"

# Engine modules whose source must NOT mention slot ownership at all.
ENGINE_DIRS = ("game", "world", "player", "kick_off", "ball", "ai_agents")
OWNERSHIP_ATTRS = (
    "SLOT_LADDER_ATTR", "SLOT_SEQ_ATTR", "SLOT_SIDE_ATTR",
    "SLOT_INDEX_ATTR", "SLOT_Y_ATTR",
)
CENSUS_FILE = "tes_drive_structure.py"
CENSUS_WRITER_FNS = ("establish_slot_ownership", "sync_tes_drive_chamber")


def _role_name(p):
    role = getattr(p, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


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


def line_bodies(world):
    """Structure-hold-eligible line bodies of the defending team."""
    bc = world.ball_carrier
    return [
        d for d in world.defending_team
        if tds.is_structure_hold_eligible(d) and d is not bc
    ]


def _slot_ref_y(world, bc, tuners, slot_y, band):
    """The slot clamped into the governed width band (the reachable lane)."""
    if band is None:
        return float(slot_y)
    lo, hi = band
    return min(max(float(slot_y), float(lo)), float(hi))


def collect():
    """Walk the harness and return per-frame chamber records."""
    frames = []          # one row per (chamber, frame, body)
    entries = []         # one row per chamber-entry frame
    buf = io.StringIO()
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
                prev_cid = None
                continue

            cid = getattr(world, tds.CHAMBER_ID_ATTR, None)
            bc = world.ball_carrier
            frame = int(world.frame_count)
            ladder = tds.slot_ladder_for(world)
            band = tds.width_band_for(world, bc, tuners)
            bodies = line_bodies(world)
            is_entry = cid != prev_cid
            if is_entry:
                prev_cid = cid
                entries.append({
                    "cid": cid,
                    "frame": frame,
                    "ruck_y": _f(getattr(world, "ruck_y_m", 35.0)),
                    "ladder": ladder,
                    "band": band,
                    "pids": sorted(
                        getattr(d, "player_id", 0) for d in bodies
                    ),
                    "rows": [
                        {
                            "pid": getattr(d, "player_id", None),
                            "y": _f(getattr(d, "y", 0.0)),
                            "slot_y": tds.owned_slot_y(world, d),
                            "slot_seq": getattr(d, tds.SLOT_SEQ_ATTR, None),
                            "slot_side": getattr(d, tds.SLOT_SIDE_ATTR, None),
                            "slot_index": getattr(d, tds.SLOT_INDEX_ATTR, None),
                            "held": bool(
                                getattr(d, tds.HOLD_ACTIVE_ATTR, False)
                            ),
                            "slot_held": bool(
                                getattr(d, tds.SLOT_HELD_ATTR, False)
                            ),
                        }
                        for d in bodies
                    ],
                })

            for d in bodies:
                frames.append({
                    "cid": cid,
                    "frame": frame,
                    "pid": getattr(d, "player_id", None),
                    "y": _f(getattr(d, "y", 0.0)),
                    "slot_y": tds.owned_slot_y(world, d),
                    "slot_seq": getattr(d, tds.SLOT_SEQ_ATTR, None),
                    "slot_side": getattr(d, tds.SLOT_SIDE_ATTR, None),
                    "slot_index": getattr(d, tds.SLOT_INDEX_ATTR, None),
                    "held": bool(getattr(d, tds.HOLD_ACTIVE_ATTR, False)),
                    "slot_held": bool(getattr(d, tds.SLOT_HELD_ATTR, False)),
                    "hold_y": _f(getattr(d, tds.HOLD_Y_ATTR, 0.0)),
                    "target_y": _f(getattr(d, "target_y", 0.0)),
                    "move_speed": _f(getattr(d, "move_speed", 0.0)),
                    "band": band,
                })
    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    return entries, frames


# ── V0 — STATIC SINGLE-SOURCE SCAN ───────────────────────────────────

def _function_spans(lines):
    """Return [(name, start_idx, end_idx)] for top-level defs (0-based)."""
    spans = []
    for i, line in enumerate(lines):
        if line.startswith("def "):
            name = line[4:].split("(")[0].strip()
            spans.append([name, i, len(lines)])
    for j in range(len(spans) - 1):
        spans[j][2] = spans[j + 1][1]
    return [(n, a, b) for n, a, b in spans]


def check_v0():
    fails = []
    root = os.path.dirname(os.path.abspath(__file__))
    for sub in ENGINE_DIRS:
        base = os.path.join(root, sub)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d for d in dirnames
                if d not in ("__pycache__", ".git", "node_modules")
            ]
            for fn in filenames:
                if not fn.endswith(".py") or fn == CENSUS_FILE:
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    continue
                if "_tes_slot_" in text:
                    rel = os.path.relpath(path, root)
                    fails.append(
                        f"V0: {rel} mentions a slot-ownership attribute — "
                        f"ownership must live only in {CENSUS_FILE}"
                    )

    census_path = os.path.join(root, "game", "defence", "defence_lvl_1",
                               CENSUS_FILE)
    with open(census_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    spans = _function_spans(lines)
    for name, a, b in spans:
        for i in range(a, b):
            line = lines[i]
            if "setattr(" not in line and "delattr(" not in line:
                continue
            if not any(attr in line for attr in OWNERSHIP_ATTRS):
                continue
            if name not in CENSUS_WRITER_FNS:
                fails.append(
                    f"V0: {CENSUS_FILE}:{i + 1} — '{name}' writes ownership; "
                    f"only {CENSUS_WRITER_FNS} may"
                )
    return fails


# ── V1..V4 — RUNTIME CLAUSES ─────────────────────────────────────────

def _check_stall(fails, cid, pid, hist, a, b):
    """Fail when a displaced body sits beyond PARKED_MIN_M without travelling.

    ``hist`` is the ordered ``(frame, y, err)`` list for one body on the
    frames the structure authority actually owned it.  A run of frames that
    stays beyond PARKED_MIN_M while the body's Y travels less than
    PARKED_NET_M is a genuine park — it is measured from observed motion
    because ``move_speed`` is not the motion signal on this path.
    """
    run = b - a + 1
    if run < PARKED_STALL_FRAMES:
        return
    net = abs(hist[b][1] - hist[a][1])
    if net <= PARKED_NET_M:
        fails.append(
            f"V3: chamber {cid} pid {pid} parked {hist[b][2]:.2f} m off its "
            f"own slot for {run} frames (frames {hist[a][0]}-{hist[b][0]}, "
            f"net travel {net:.2f} m)"
        )


def check_runtime(entries, frames):
    fails = []
    owned = {}          # (cid, pid) -> dict(side, index, slot_y)
    entry_err = {}      # (cid, pid) -> error at entry vs reachable slot
    pool_pids = {}      # cid -> pids the hold authority owns that frame
    vacant_violations = []
    recover_hist = {}   # (cid, pid) -> [(frame, y, err)] while displaced

    # ---- V1 CENSUS AT ENTRY ----
    # The census population is EXACTLY the set of bodies the structure-hold
    # authority owns this frame — the caller's pool, which is the set that
    # carries the hold stamp.  A body that is merely role-eligible but
    # outside that pool (frozen / TES participant / attached / pre-tackle
    # pile / tackle mover) is NOT part of the ownership system and owns
    # nothing; demanding a slot for it would be asserting a contract the
    # authority never took on.
    for ent in entries:
        cid = ent["cid"]
        pool = [r for r in ent["rows"] if r["held"]]
        pool_pids[cid] = {r["pid"] for r in pool}
        ladder = ent["ladder"]
        if not pool:
            # No line body to own: the authority never ran for this chamber,
            # so there is no census table and nothing to assert.
            continue
        if not isinstance(ladder, dict):
            fails.append(
                f"V1: chamber {cid} entered with NO slot table for "
                f"{len(pool)} held bodies"
            )
            continue
        if ladder.get("seq") != cid:
            fails.append(
                f"V1: chamber {cid} slot table seq={ladder.get('seq')} != chamber"
            )
        owners = []
        for side_entries in (ladder.get("sides") or {}).values():
            for e in side_entries:
                owners.append(e.get("owner_id"))
        for row in pool:
            pid = row["pid"]
            if row["slot_seq"] != cid or row["slot_y"] is None:
                fails.append(
                    f"V1: chamber {cid} pid {pid} is held but owns no slot "
                    f"(seq={row['slot_seq']}, y={row['slot_y']})"
                )
                continue
            if row["slot_side"] not in (-1, 1):
                fails.append(
                    f"V1: chamber {cid} pid {pid} slot side={row['slot_side']}"
                )
            if not isinstance(row["slot_index"], int) or row["slot_index"] < 0:
                fails.append(
                    f"V1: chamber {cid} pid {pid} slot index={row['slot_index']}"
                )
            if pid not in owners:
                fails.append(
                    f"V1: chamber {cid} pid {pid} not present in the ladder"
                )
            entry_err[(cid, pid)] = abs(
                row["y"] - _slot_ref_y(None, None, None, row["slot_y"], ent["band"])
            )
            owned[(cid, pid)] = {
                "side": row["slot_side"],
                "index": row["slot_index"],
                "slot_y": row["slot_y"],
            }
        # ONE-TO-ONE: no owner may hold two slots, and every ladder entry
        # must own a body that is actually held this frame.
        seen = set()
        for o in owners:
            if o in seen:
                fails.append(f"V1: chamber {cid} owner {o} holds TWO slots")
            seen.add(o)
        for o in owners:
            if o not in pool_pids[cid]:
                fails.append(
                    f"V1: chamber {cid} ladder owner {o} is not a held body"
                )

    # ---- V2/V3/V4 per body ----
    max_err = {}        # (cid, pid) -> worst error vs reachable slot
    for row in frames:
        # Grade ONLY frames on which the structure authority both holds the
        # body AND has its cell on the body's own slot.  Any other frame is
        # written by a different authority and says nothing about ownership.
        if not (row["held"] and row["slot_held"]):
            continue
        cid, pid = row["cid"], row["pid"]
        key = (cid, pid)
        if key not in owned or row["slot_y"] is None:
            continue
        if row["slot_seq"] != cid:
            continue
        # ---- V4 immutability ----
        prev = owned[key]
        if (prev["side"] != row["slot_side"]
                or prev["index"] != row["slot_index"]
                or abs(prev["slot_y"] - row["slot_y"]) > IMMUT_TOL_M):
            fails.append(
                f"V4: chamber {cid} pid {pid} slot changed mid-chamber "
                f"({prev} -> side={row['slot_side']} index={row['slot_index']} "
                f"y={row['slot_y']:.3f})"
            )
            owned[key] = {
                "side": row["slot_side"],
                "index": row["slot_index"],
                "slot_y": row["slot_y"],
            }
        ref = _slot_ref_y(None, None, None, row["slot_y"], row["band"])
        err = abs(row["y"] - ref)
        e0 = entry_err.get(key, err)
        # ---- V2 retain (already correctly slotted) ----
        if e0 <= ENTRY_TOL_M and err > RETAIN_TOL_M:
            fails.append(
                f"V2: chamber {cid} pid {pid} was slotted ({e0:.2f} m) but "
                f"left its slot by {err:.2f} m at frame {row['frame']}"
            )
        # ---- V3 recover (displaced) ----
        if e0 > ENTRY_TOL_M:
            worst = max_err.get(key, e0)
            if err > worst + 1e-6:
                fails.append(
                    f"V3: chamber {cid} pid {pid} overshot its own slot "
                    f"({worst:.2f} -> {err:.2f} m) at frame {row['frame']}"
                )
            worse = max(worst, err)
            max_err[key] = worse
            # Motion history for the stall test below.  ``move_speed`` is NOT
            # the motion signal on this path: the TES drive moves bodies
            # through glide writers that never touch it, so "parked" has to
            # be measured from observed Y travel.
            recover_hist.setdefault(key, []).append(
                (row["frame"], row["y"], err)
            )

    # ---- V3 parked: measured from observed motion, never from move_speed ----
    for key, hist in recover_hist.items():
        cid, pid = key
        run_start = None
        for i in range(len(hist)):
            if hist[i][2] > PARKED_MIN_M:
                if run_start is None:
                    run_start = i
                continue
            if run_start is not None:
                _check_stall(fails, cid, pid, hist, run_start, i - 1)
                run_start = None
        if run_start is not None:
            _check_stall(fails, cid, pid, hist, run_start, len(hist) - 1)

    # ---- V4 vacancy: a vacant slot is never occupied/targeted ----
    per_frame = {}
    for row in frames:
        per_frame.setdefault((row["cid"], row["frame"]), []).append(row)
    for ent in entries:
        cid = ent["cid"]
        ladder = ent["ladder"] if isinstance(ent["ladder"], dict) else {}
        slot_y_by_owner = {}
        for side_entries in (ladder.get("sides") or {}).values():
            for e in side_entries:
                slot_y_by_owner[e.get("owner_id")] = e.get("y")
        for (fcid, frame), rows in per_frame.items():
            if fcid != cid:
                continue
            # Only a body the structure authority actually owns can adopt a
            # vacant lane; bodies driven by other authorities are out of scope.
            held = [r for r in rows if r["held"] and r["slot_held"]]
            live_pids = {r["pid"] for r in held}
            for owner, slot_y in slot_y_by_owner.items():
                if owner in live_pids:
                    continue
                y_owner = _f(slot_y)
                for r in held:
                    if r["pid"] == owner:
                        continue
                    if abs(r["hold_y"] - y_owner) <= LANE_DUP_M:
                        vacant_violations.append(
                            f"V4: chamber {cid} frame {frame} vacant slot "
                            f"(owner {owner}, y={y_owner:.2f}) occupied by "
                            f"pid {r['pid']} (cell {r['hold_y']:.2f})"
                        )
                    if (r["slot_y"] is not None
                            and abs(r["slot_y"] - y_owner) <= LANE_DUP_M):
                        vacant_violations.append(
                            f"V4: chamber {cid} frame {frame} vacant slot "
                            f"(owner {owner}, y={y_owner:.2f}) claimed by "
                            f"pid {r['pid']} (slot {r['slot_y']:.2f})"
                        )
    fails += vacant_violations[:20]
    return fails, owned, entry_err


def main():
    print("=" * 78)
    print("TES RECOVERY OWNERSHIP VERIFIER")
    print("=" * 78)

    fails = check_v0()
    print(f"V0 static single-source violations : {len(fails)}")

    entries, frames = collect()
    if not entries:
        print("\nRESULT: FAILED — no TES drive chamber reached.")
        return 2

    runtime_fails, owned, entry_err = check_runtime(entries, frames)
    fails += runtime_fails

    chambers = len(entries)
    slotted = sum(1 for v in entry_err.values() if v <= ENTRY_TOL_M)
    displaced = sum(1 for v in entry_err.values() if v > ENTRY_TOL_M)
    print(f"chambers observed                 : {chambers}")
    print(f"owned body-chambers (V1)           : {len(owned)}")
    print(f"  already slotted at entry (V2)    : {slotted}")
    print(f"  displaced at entry (V3)          : {displaced}")
    print(f"body-frames observed               : {len(frames)}")
    print(f"runtime violations                 : {len(runtime_fails)}")

    print("\n" + "=" * 78)
    if not fails:
        print("RESULT: RECOVERY OWNERSHIP INTACT — the chamber-entry census "
              "is the sole ownership source; slotted defenders retain, "
              "displaced defenders recover to their own latched slot, "
              "vacant slots stay vacant, and no ownership is re-solved.")
        return 0
    print(f"RESULT: {len(fails)} VIOLATION(S) — the TES recovery-ownership "
          f"contract is broken:")
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
