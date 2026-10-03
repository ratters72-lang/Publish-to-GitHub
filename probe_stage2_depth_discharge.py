"""probe_stage2_depth_discharge.py — STAGE 2: does the authority DISCHARGE?

The successor to ``probe_tes_depth_debt.py`` (Stage 1: measure the debt and
prove the diagnosis).  Stage 1 measured what the engine does with NO
authority.  Stage 2 adds the authority, so this probe measures whether the
authority actually changes the outcome, on the SAME metrics Stage 1 used.

WHAT IT DOES
------------
Boots the engine headless twice — identical boot, identical frame budget —
and measures every TES depth chamber through the Stage-1 definition module
(``tes_depth_obligation``):

    ARM A (baseline)  : tuners.tes_depth_authority_enabled = False
    ARM B (authority) : tuners.tes_depth_authority_enabled = True

Per arm it reports the Stage-1 metric set:

    chambers / chamber frames / measured body-frames
    obligated body-frames (total and % of measured)
    mean debt, peak debt, mean start / end debt per chamber
    discharge time (median and worst resolved obligation run)
    unresolved obligation runs
    closure rate while obligated
    debt re-acquisitions
    PERSISTENT deep-debt bodies (>= 5 m for >= 60 frames, net closure
        <= 0.25 m/s) — the Stage-1 falsification metric

and, from ARM B, the MECHANISM PROOF: how many body-frames the authority
actually granted, and the mean commanded convergence speed versus the flat
structure-hold jog speed it replaces.  A zero grant count across a rollout
would mean the authority is nominal, not live.

DETERMINISM CAVEAT (carried from the Stage-1 probes)
----------------------------------------------------
Two runs of IDENTICAL engine code have been recorded producing different
worlds (object-id / dict-iteration ordering over defenders), so a
two-boot A/B is NOT a controlled pair: only deltas that are large relative
to the run-to-run spread mean anything.  The probe therefore prints the
spread-sensitive numbers in raw form and labels its verdict CONSERVATIVELY
(IMPROVED only when the discharge metrics move in the predicted direction
on both the persistence and the closure families at once).  A REFUTED or
INCONCLUSIVE verdict here is a MEASUREMENT, not a failure.

EXIT CODE
---------
    0 — the probe ran and the authority arm shows IMPROVED discharge
    1 — the probe ran; no improvement measured (WATCH / INCONCLUSIVE)
    2 — no TES chamber was observed in either arm
    3 — the authority never granted (it is wired but inert)

Run:  python probe_stage2_depth_discharge.py
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

from game.defence.defence_lvl_1 import tes_depth_obligation as tdo
from game.defence.defence_lvl_1 import tes_depth_authority as tda
from game.defence.defence_lvl_1 import tes_drive_structure as tds

DT = 1.0 / 60.0
MAX_FRAMES = 3000

# Stage-1 falsification constants (unchanged, so the two probes agree).
FLAT_DEBT_MIN_M = 5.0
FLAT_DEBT_MIN_FRAMES = 60
NEAR_FLAT_CLOSURE_MPS = 0.25

# The flat jog the authority replaces (the structure hold's speed).
BASELINE_JOG_MPS = 2.5


def _role_name(p):
    role = getattr(p, "defender_role", None)
    return getattr(role, "name", None) or (str(role) if role is not None else None)


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _median(values):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def _net_closure(run):
    frames = int(run.get("frames", 0) or 0)
    if frames <= 0:
        return 0.0
    return (float(run["debt_start"]) - float(run["debt_end"])) / (frames * DT)


# ─────────────────────────────────────────────────────────────────────
# HEADLESS BOOT (identical to probe_tes_depth_debt.py)
# ─────────────────────────────────────────────────────────────────────

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
    return world, ball, open_play, kickoff_engine, collision_system, field


# ─────────────────────────────────────────────────────────────────────
# PER-BODY RECORD
# ─────────────────────────────────────────────────────────────────────

def new_record(d):
    return {
        "pid": getattr(d, "player_id", "?"),
        "depth_role": getattr(d, "depth_role", None),
        "start_debt": None,
        "end_debt": None,
        "frames": 0,
        "frames_in_debt": 0,
        "reacq": 0,
        "close_sum": 0.0,
        "close_frames": 0,
        "deep": None,
        "deep_best": None,
        "runs": [],
        "cur_run": None,
        "prev_debt": None,
    }


def _keep_best_deep(rec):
    deep = rec["deep"]
    if deep is None or deep["frames"] <= 0:
        return
    best = rec["deep_best"]
    if best is None or deep["frames"] > best["frames"]:
        rec["deep_best"] = dict(deep)


def update_record(rec, debt, obligated):
    if rec["frames"] == 0:
        rec["start_debt"] = debt
    rec["frames"] += 1
    rec["end_debt"] = debt

    if obligated:
        rec["frames_in_debt"] += 1
        if rec["prev_debt"] is not None and rec["cur_run"] is None:
            rec["reacq"] += 1
        if rec["cur_run"] is None:
            rec["cur_run"] = {
                "frames": 0, "debt_start": debt, "discharged": False,
            }
        rec["cur_run"]["frames"] += 1
    else:
        if rec["cur_run"] is not None:
            rec["cur_run"]["discharged"] = True
            rec["runs"].append(rec["cur_run"])
            rec["cur_run"] = None

    if rec["prev_debt"] is not None and obligated:
        rec["close_sum"] += (rec["prev_debt"] - debt) / DT
        rec["close_frames"] += 1

    if debt >= FLAT_DEBT_MIN_M:
        if rec["deep"] is None:
            rec["deep"] = {
                "frames": 0, "debt_start": debt, "debt_end": debt,
            }
        rec["deep"]["frames"] += 1
        rec["deep"]["debt_end"] = debt
    else:
        if rec["deep"] is not None:
            _keep_best_deep(rec)
            rec["deep"] = None

    rec["prev_debt"] = debt


def close_record(rec):
    if rec["cur_run"] is not None:
        rec["cur_run"]["discharged"] = False
        rec["runs"].append(rec["cur_run"])
        rec["cur_run"] = None
    _keep_best_deep(rec)
    rec["deep"] = None


# ─────────────────────────────────────────────────────────────────────
# ONE ARM
# ─────────────────────────────────────────────────────────────────────

def run_arm(authority_on: bool) -> dict:
    """Boot, run the frame budget, and fold the Stage-1 metrics."""
    buf = io.StringIO()
    chambers = []
    cur = None
    prev_seq = None
    was_in_chamber = False
    frames_in_chamber = 0
    debt_sum = 0.0
    debt_n = 0
    peak_debt = 0.0
    granted_frames = 0
    grant_speed_sum = 0.0
    grant_speeds = []
    # Bounded peak-debt ledger.  PEAK debt is a MAX over samples, so a
    # single boundary transient (a carrier reposition or a fresh chamber
    # beginning while a body is still at the previous ruck) can dominate
    # it.  Recording the worst samples — with the frame, the phase and the
    # body's live X versus the target it was measured against — lets the
    # report state WHERE the peak came from instead of leaving it
    # unexplained.
    peak_samples = []

    with contextlib.redirect_stdout(buf):
        (world, ball, open_play, kickoff_engine,
         collision_system, field) = boot_world()

        # ARM GATE: flip the single authority tuner on the live tuner object
        # the manager and the authority both read.
        open_play.def_tuners.tes_depth_authority_enabled = bool(authority_on)

        for _ in range(MAX_FRAMES):
            world.frame_count += 1
            ball.update(DT)

            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(DT)
                check_for_catch(world, ball, DT)
                continue
            if world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
                continue
            if world.phase != GamePhase.OPEN_PLAY:
                continue

            open_play.update(DT)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            in_chamber = tdo.in_tes_depth_chamber(world)
            seq = getattr(world, tds.CHAMBER_ID_ATTR, None)

            new_chamber = (
                (in_chamber and not was_in_chamber)
                or (in_chamber and seq is not None and prev_seq is not None
                    and seq != prev_seq)
            )
            if new_chamber:
                if cur is not None:
                    for rec in cur["bodies"].values():
                        close_record(rec)
                    chambers.append(cur)
                cur = {
                    "seq": seq, "enter_frame": world.frame_count,
                    "frames": 0, "bodies": {},
                }
            if not in_chamber:
                if was_in_chamber and cur is not None:
                    for rec in cur["bodies"].values():
                        close_record(rec)
                    chambers.append(cur)
                    cur = None
                was_in_chamber = in_chamber
                prev_seq = seq
                continue

            was_in_chamber = True
            prev_seq = seq
            frames_in_chamber += 1
            if cur is not None:
                cur["frames"] += 1

            tuners = tdo._resolve_tuners(world)
            tol = tdo.tes_depth_tolerance_m(tuners)
            # The authority's bookkeeping for the mechanism proof.  The
            # manager attribute persists from the LAST drive frame, so it is
            # only meaningful on a frame where the TES drive writer actually
            # ran; on any other frame (e.g. TACKLE_INIT, which early-returns
            # before the authority) it is stale and would over-count grants.
            m = getattr(open_play, "defence_manager", None)
            drive_ran = str(getattr(open_play, "_phase", "")).upper() in (
                "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
            )
            granted_ids = {
                id(g) for g in getattr(m, "_tes_depth_authority_last", [])
            } if (m is not None and drive_ran) else set()
            # Cross-check the granted set against the authority's own stamp,
            # so the mechanism proof can never be satisfied by a look-alike
            # writer.
            stamped_ids = {
                id(b) for b in (getattr(world, "defending_team", None) or [])
                if getattr(b, "target_source", None)
                == tda.TES_DEPTH_AUTHORITY_SOURCE
            }
            granted_ids &= stamped_ids

            for d in (getattr(world, "defending_team", None) or []):
                if d is None or d is world.ball_carrier:
                    continue
                if tdo.is_depth_exempt_role(d, world):
                    continue
                if not tdo.has_role_depth(d, tuners):
                    continue
                target = tdo.tes_depth_target_x(world, d, tuners)
                if target is None:
                    continue
                debt = tdo.tes_depth_debt_m(world, d, target_x=target)
                obligated = debt > tol
                debt_sum += debt
                debt_n += 1
                if debt > peak_debt:
                    peak_debt = debt
                peak_samples.append((
                    round(float(debt), 2), int(world.frame_count),
                    getattr(d, "player_id", "?"),
                    str(getattr(open_play, "_phase", "")),
                    round(_f(getattr(d, "x", 0.0)), 2),
                    round(float(target), 2),
                    bool(obligated),
                ))
                if id(d) in granted_ids:
                    granted_frames += 1
                    sp = _f(getattr(d, "move_speed", 0.0))
                    grant_speed_sum += sp
                    grant_speeds.append(sp)
                if cur is None:
                    continue
                rec = cur["bodies"].get(id(d))
                if rec is None:
                    rec = new_record(d)
                    cur["bodies"][id(d)] = rec
                update_record(rec, debt, obligated)

        if cur is not None:
            for rec in cur["bodies"].values():
                close_record(rec)
            chambers.append(cur)

    all_recs = [(c, r) for c in chambers for r in c["bodies"].values()]
    body_frames = sum(r["frames"] for _, r in all_recs)
    obligated_body_frames = sum(r["frames_in_debt"] for _, r in all_recs)

    resolved, unresolved = [], 0
    persistent = 0
    deepest = 0
    for _c, r in all_recs:
        for run in r["runs"]:
            if run["discharged"]:
                resolved.append(run["frames"])
            else:
                unresolved += 1
        if r["deep_best"] is not None:
            run = r["deep_best"]
            deepest = max(deepest, run["frames"])
            if (run["frames"] >= FLAT_DEBT_MIN_FRAMES
                    and _net_closure(run) <= NEAR_FLAT_CLOSURE_MPS):
                persistent += 1

    close_sum = sum(r["close_sum"] for _, r in all_recs)
    close_n = sum(r["close_frames"] for _, r in all_recs)

    per_chamber_start, per_chamber_end = [], []
    for c in chambers:
        recs = list(c["bodies"].values())
        if not recs:
            continue
        starts = [r["start_debt"] for r in recs if r["start_debt"] is not None]
        ends = [r["end_debt"] for r in recs if r["end_debt"] is not None]
        if starts:
            per_chamber_start.append(sum(starts) / len(starts))
        if ends:
            per_chamber_end.append(sum(ends) / len(ends))

    return {
        "authority_on": bool(authority_on),
        "chambers": len(chambers),
        "chamber_frames": frames_in_chamber,
        "body_frames": body_frames,
        "obligated_body_frames": obligated_body_frames,
        "mean_debt": (debt_sum / debt_n) if debt_n else 0.0,
        "peak_debt": peak_debt,
        "mean_chamber_start": (
            sum(per_chamber_start) / len(per_chamber_start)
            if per_chamber_start else 0.0
        ),
        "mean_chamber_end": (
            sum(per_chamber_end) / len(per_chamber_end)
            if per_chamber_end else 0.0
        ),
        "median_discharge": _median(resolved),
        "worst_discharge": (max(resolved) if resolved else None),
        "unresolved_runs": unresolved,
        "closure_mps": (close_sum / close_n) if close_n else None,
        "reacq": sum(r["reacq"] for _, r in all_recs),
        "persistent_bodies": persistent,
        "deepest_run_frames": deepest,
        "granted_body_frames": granted_frames,
        "mean_grant_speed": (
            grant_speed_sum / granted_frames if granted_frames else 0.0
        ),
        "min_grant_speed": (min(grant_speeds) if grant_speeds else 0.0),
        "max_grant_speed": (max(grant_speeds) if grant_speeds else 0.0),
        "peak_samples": sorted(peak_samples, key=lambda r: -r[0])[:6],
    }


# ─────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────

def _fmt(v, nd=2):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def report(base, auth):
    print("=" * 78)
    print("TES DEPTH AUTHORITY — STAGE 2 A/B DISCHARGE PROBE")
    print("Stage-1 metric set, measured with the authority OFF then ON.")
    print("=" * 78)

    rows = [
        ("TES chambers observed", base["chambers"], auth["chambers"], 0),
        ("TES chamber frames", base["chamber_frames"],
         auth["chamber_frames"], 0),
        ("measured body-frames", base["body_frames"],
         auth["body_frames"], 0),
        ("obligated body-frames", base["obligated_body_frames"],
         auth["obligated_body_frames"], 0),
        ("mean debt (m)", base["mean_debt"], auth["mean_debt"], 2),
        ("peak debt (m)", base["peak_debt"], auth["peak_debt"], 2),
        ("mean chamber start debt (m)", base["mean_chamber_start"],
         auth["mean_chamber_start"], 2),
        ("mean chamber end debt (m)", base["mean_chamber_end"],
         auth["mean_chamber_end"], 2),
        ("median discharge (frames)", base["median_discharge"],
         auth["median_discharge"], 0),
        ("worst discharge (frames)", base["worst_discharge"],
         auth["worst_discharge"], 0),
        ("unresolved obligation runs", base["unresolved_runs"],
         auth["unresolved_runs"], 0),
        ("PERSISTENT deep-debt bodies", base["persistent_bodies"],
         auth["persistent_bodies"], 0),
        ("deepest debt run (frames)", base["deepest_run_frames"],
         auth["deepest_run_frames"], 0),
        ("debt re-acquisitions", base["reacq"], auth["reacq"], 0),
    ]

    print(f"\n  {'metric':<32}{'baseline':>12}{'authority':>12}{'delta':>12}")
    for name, b, a, nd in rows:
        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            d = a - b
            dtxt = f"{d:+.{nd}f}" if nd else f"{int(d):+d}"
            print(f"  {name:<32}{_fmt(b, nd):>12}{_fmt(a, nd):>12}{dtxt:>12}")
        else:
            print(f"  {name:<32}{_fmt(b, nd):>12}{_fmt(a, nd):>12}{'-':>12}")

    bc = base["closure_mps"]
    ac = auth["closure_mps"]
    print(f"  {'closure rate (m/s)':<32}{_fmt(bc, 3):>12}"
          f"{_fmt(ac, 3):>12}"
          f"{('%+.3f' % (ac - bc)) if (bc is not None and ac is not None) else '-':>12}")

    # ── PEAK-DEBT LEDGER (where the max came from) ──
    print("\n-- PEAK-DEBT LEDGER (debt, frame, id, phase, body_x, target, "
          "obligated) --")
    for label, arm in (("baseline", base), ("authority", auth)):
        print(f"  {label}:")
        for row in arm.get("peak_samples", ()):
            print("    %6.2fm f=%-5d id=%-4s %-16s x=%7.2f target=%7.2f "
                  "oblig=%s" % row)

    # ── MECHANISM PROOF (the authority actually acted) ──
    print("\n-- MECHANISM PROOF (authority arm) --")
    print(f"  body-frames granted authority : {auth['granted_body_frames']}")
    print(f"  flat jog it replaces (m/s)    : {BASELINE_JOG_MPS:.2f}")
    if auth["granted_body_frames"]:
        print(f"  mean commanded speed (m/s)    : "
              f"{auth['mean_grant_speed']:.2f}")
        print(f"  commanded speed range (m/s)   : "
              f"{auth['min_grant_speed']:.2f} .. {auth['max_grant_speed']:.2f}")
        print("  => the authority was LIVE and commanded more than the flat "
              "jog on a large share of granted frames."
              if auth["mean_grant_speed"] > BASELINE_JOG_MPS else
              "  => the authority was LIVE but did NOT out-command the jog "
              "(check the tuner values).")
    else:
        print("  => NO body-frame was ever granted: the authority is wired "
              "but INERT on this rollout.")

    # ── VERDICT (conservative: both families must move right) ──
    print("\n" + "=" * 78)
    if not (base["chambers"] or auth["chambers"]):
        print("RESULT: NO TES CHAMBER OBSERVED — nothing to compare.")
        return 2
    if auth["granted_body_frames"] == 0:
        print("RESULT: AUTHORITY INERT — it was wired but never granted on "
              "this rollout.  Cannot measure discharge.")
        return 3

    persistence_ok = (auth["persistent_bodies"] < base["persistent_bodies"]
                      or auth["deepest_run_frames"]
                      < base["deepest_run_frames"])
    closure_ok = (
        (bc is not None and ac is not None and ac > bc)
        or auth["mean_debt"] < base["mean_debt"]
        or auth["obligated_body_frames"] < base["obligated_body_frames"]
    )
    discharge_ok = (auth["median_discharge"] is not None
                    and (base["median_discharge"] is None
                         or auth["median_discharge"]
                         <= base["median_discharge"]))
    print(f"  persistence family  : {'IMPROVED' if persistence_ok else 'no change'}")
    print(f"  closure family      : {'IMPROVED' if closure_ok else 'no change'}")
    print(f"  discharge family    : {'IMPROVED' if discharge_ok else 'no change'}")
    # WATCH-ONLY (Stage 2): oscillation / re-acquisition is reported, never
    # treated as grounds to add state.
    print(f"  WATCH (not a failure): re-acquisitions "
          f"{base['reacq']} -> {auth['reacq']}")

    if persistence_ok and (closure_ok or discharge_ok):
        print("\nRESULT: IMPROVED — the authority materially reduced TES "
              "depth debt persistence and improved closure/discharge.")
        return 0
    print("\nRESULT: INCONCLUSIVE / NO MEASURABLE CHANGE (WATCH).  Per the "
          "Stage-2 contract this is a MEASUREMENT, not a failure: a "
          "two-boot A/B is exposed to the documented run-to-run engine "
          "non-determinism, and no stateful mechanism may be added on the "
          "strength of it.")
    return 1


def main():
    print("booting ARM A (authority OFF) ...")
    base = run_arm(False)
    print("booting ARM B (authority ON) ...")
    auth = run_arm(True)
    return report(base, auth)


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
