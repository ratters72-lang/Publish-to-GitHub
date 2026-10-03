"""
verify_stage5_population_authority.py — Stage 5 formation-population
authority verifier (2026-09-13).

CLAIM UNDER TEST
----------------
There is ONE authoritative line population, and the same defender set is
used by count ownership, side ownership, spacing ownership and every
population observer.  For EVERY measured frame:

    published_count_left + published_count_right
        == len(published line population)
        == (#depth_side == -1) + (#depth_side == +1)     [side ownership]
        == len(keys(world.frame_spacing_targets))        [spacing ownership]

and every body NOT in the population carries depth_side = None.

CHECKS (per measured frame)
---------------------------
  V0 POPULATION FRESHNESS  the published ``world.line_population`` equals a
                           fresh ``population_authority.line_population``
                           recomputation over the live team.
  V1 COUNT INTEGRITY       published_count_left + published_count_right
                           == len(published population).
  V2 SIDE INTEGRITY        over the WHOLE team, #(depth_side == -1) ==
                           published_count_left and #(depth_side == +1) ==
                           published_count_right, and #(depth_side in ±1)
                           == len(population)  (no stale stamp survives).
  V3 STAMP INTEGRITY       no body outside the population carries
                           depth_side in (-1, +1).
  V4 SPACING INTEGRITY     when the frame geometry is fresh, the spacing
                           dict keys are EXACTLY the population ids.
  V5 SINGLE PUBLISHER      within one frame, the SAME population signature
                           is never published with two different totals.
  V6 SOLVE TOTAL INTEGRITY every ``decide_side_counts`` result sums to the
                           n_total it was called with (the lag fix).

PASS iff every check has ZERO violations across the run.

READ-ONLY: no production file is modified.  The two instrumented symbols are
restored in a ``finally`` block.

Run:  python verify_stage5_population_authority.py
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

import game.defence.defence_lvl_1.population_authority as pa
import game.defence.defence_lvl_1.defensive_split_spacing as dss


DT = 1.0 / 60.0
MAX_FRAMES = 3600

POP_CSV = "stage5_population.csv"
REPORT = "stage5_report.txt"


# ─────────────────────────────────────────────────────────────────────
# INSTRUMENTATION
# ─────────────────────────────────────────────────────────────────────

# decide_side_counts solves: (frame, n_total, n_left, n_right)
_solve_calls: list = []
# publications: (frame, population signature, total, n_left, n_right, solve_index)
_pub_calls: list = []

_ORIG_DECIDE = None
_ORIG_PUBLISH = None


def install_wrappers():
    global _ORIG_DECIDE, _ORIG_PUBLISH
    _ORIG_DECIDE = dss.decide_side_counts
    _ORIG_PUBLISH = pa.publish_line_population

    def _decide(world, tuners, n_total, *a, **kw):
        res = _ORIG_DECIDE(world, tuners, n_total, *a, **kw)
        try:
            nl, nr = int(res[0]), int(res[1])
        except Exception:
            nl, nr = -1, -1
        _solve_calls.append(
            (getattr(world, "frame_count", None), int(n_total), nl, nr)
        )
        return res

    def _publish(world, defenders, tuners, *a, **kw):
        res = _ORIG_PUBLISH(world, defenders, tuners, *a, **kw)
        try:
            _pub_calls.append((
                res.get("frame"),
                res.get("population_ids"),
                int(res.get("total", -1)),
                int(res.get("n_left", -1)),
                int(res.get("n_right", -1)),
                int(res.get("solve_index", -1)),
            ))
        except Exception:
            pass
        return res

    dss.decide_side_counts = _decide
    pa.publish_line_population = _publish


def unwrap_all():
    if _ORIG_DECIDE is not None:
        dss.decide_side_counts = _ORIG_DECIDE
    if _ORIG_PUBLISH is not None:
        pa.publish_line_population = _ORIG_PUBLISH


# ─────────────────────────────────────────────────────────────────────
# BOOT + RUN
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
    return world, ball, open_play, kickoff_engine, collision_system


def _team(world):
    return list(getattr(world, "defending_team", None) or [])


def measure(world):
    """One per-frame measurement of the Stage 5 invariant."""
    team = _team(world)
    published = list(getattr(world, "line_population", None) or [])
    pub_ids = set(id(d) for d in published)
    fresh_pop = pa.line_population(team)
    fresh_ids = set(id(d) for d in fresh_pop)

    n_left = getattr(world, "published_count_left", None)
    n_right = getattr(world, "published_count_right", None)
    total = getattr(world, "published_count_total", None)

    # Whole-team depth_side partition.
    stamp_left = stamp_right = stamp_total = 0
    outside_stamped = []
    for d in team:
        side = getattr(d, "depth_side", None)
        if side in (-1, 1):
            stamp_total += 1
            if side == -1:
                stamp_left += 1
            else:
                stamp_right += 1
            if id(d) not in pub_ids:
                outside_stamped.append(getattr(d, "player_id", None))

    # Spacing population (fresh frame only).
    dct = getattr(world, "frame_spacing_targets", None) or {}
    spacing_ids = set(k for k in dct.keys())
    # Attribute any observer key that is NOT in the population.
    extra_info = []
    for k in (spacing_ids - pub_ids):
        body = next((d for d in team if id(d) == k), None)
        extra_info.append((
            getattr(body, "player_id", None) if body is not None else None,
            str(getattr(body, "defender_role", None)) if body is not None else "-",
            bool(getattr(body, "tackle_state_active", False)) if body is not None else None,
            bool(getattr(body, "fullback_role_active", False)) if body is not None else None,
        ))

    rec = {
        "frame": int(getattr(world, "frame_count", 0) or 0),
        "phase": str(getattr(world, "_ope_phase_label", "") or ""),
        "pub_len": len(published),
        "fresh_len": len(fresh_pop),
        "n_left": n_left,
        "n_right": n_right,
        "total": total,
        "stamp_left": stamp_left,
        "stamp_right": stamp_right,
        "stamp_total": stamp_total,
        "outside_stamped": outside_stamped,
        "spacing_len": len(spacing_ids),
        "spacing_extra": extra_info,
        "spacing_missing": sorted(
            pub_ids - spacing_ids
        ) if spacing_ids else [],
        "v0": pub_ids == fresh_ids,
        "v1": (n_left is not None and n_right is not None
               and int(n_left) + int(n_right) == len(published)),
        "v2": (n_left is not None and n_right is not None
               and stamp_left == int(n_left)
               and stamp_right == int(n_right)
               and stamp_total == len(published)),
        "v3": len(outside_stamped) == 0,
        "v4": (spacing_ids == pub_ids) if spacing_ids else True,
    }
    return rec
def run_sequence():
    """Boot, run headless, measure every OPEN_PLAY frame."""
    world, ball, ope, kickoff_engine, collision_system = boot_world()
    install_wrappers()

    frames = []
    engine_log = io.StringIO()
    try:
        with contextlib.redirect_stdout(engine_log):
            for _ in range(MAX_FRAMES):
                ball.update(DT)
                if world.phase == GamePhase.KICKOFF:
                    kickoff_engine.update(DT)
                    check_for_catch(world, ball, DT)
                    continue
                if world.phase == GamePhase.CATCH:
                    if world.ball_carrier is not None:
                        ope.start(world.ball_carrier)
                    world.phase = GamePhase.OPEN_PLAY
                    continue
                if world.phase != GamePhase.OPEN_PLAY:
                    continue
                ope.update(DT)
                if not getattr(world, "tes_active", False):
                    collision_system.resolve_all(world.players, world=world)
                frames.append(measure(world))
    finally:
        unwrap_all()

    return {
        "world": world,
        "frames": frames,
        "engine_log": engine_log.getvalue(),
    }


# ─────────────────────────────────────────────────────────────────────
# AGGREGATION
# ─────────────────────────────────────────────────────────────────────

CHECKS = ("v0", "v1", "v2", "v3", "v4")


def check_single_publisher():
    """V5 — within one frame a population signature has ONE total."""
    violations = []
    by_frame: dict = {}
    for frame, sig, total, nl, nr, idx in _pub_calls:
        by_frame.setdefault(frame, {}).setdefault(sig, set()).add(total)
    for frame, sigs in by_frame.items():
        for sig, totals in sigs.items():
            if len(totals) > 1:
                violations.append({
                    "frame": frame,
                    "n_pop": len(sig) if sig else 0,
                    "totals": sorted(totals),
                })
    return violations


def check_solve_integrity():
    """V6 — every decide_side_counts result sums to its n_total."""
    bad = []
    multi = {}
    for frame, n_total, nl, nr in _solve_calls:
        if nl + nr != n_total:
            bad.append({
                "frame": frame, "n_total": n_total,
                "n_left": nl, "n_right": nr,
            })
        multi.setdefault(frame, set()).add(n_total)
    return bad, multi


def summarise(frames):
    stats = {}
    for key in CHECKS:
        bad = [f for f in frames if not f[key]]
        stats[key] = {
            "violations": len(bad),
            "first": bad[0] if bad else None,
        }
    return stats


# ─────────────────────────────────────────────────────────────────────
# OUTPUT
# ─────────────────────────────────────────────────────────────────────

def write_csv(frames):
    cols = ["frame", "phase", "pub_len", "fresh_len", "n_left", "n_right",
            "total", "stamp_left", "stamp_right", "stamp_total",
            "spacing_len", "v0", "v1", "v2", "v3", "v4"]
    with open(POP_CSV, "w", encoding="utf-8", newline="") as fh:
        fh.write(",".join(cols) + "\n")
        for f in frames:
            fh.write(",".join(str(f.get(c)) for c in cols) + "\n")


def write_report(result, frames, stats, v5, v6, multi):
    lines = []
    lines.append("=" * 72)
    lines.append("STAGE 5 — FORMATION POPULATION AUTHORITY")
    lines.append("=" * 72)
    lines.append(f"frames measured (OPEN_PLAY) : {len(frames)}")
    lines.append(f"engine frames at end        : "
                 f"{getattr(result['world'], 'frame_count', None)}")
    lines.append("")
    lines.append("PER-FRAME CHECKS (0 violations required)")
    lines.append("-" * 72)
    labels = {
        "v0": "V0 population freshness (published == live line population)",
        "v1": "V1 count integrity    (n_left+n_right == len(population))",
        "v2": "V2 side integrity     (team stamp partition == counts)",
        "v3": "V3 stamp integrity    (no stamp outside the population)",
        "v4": "V4 spacing integrity  (spacing keys == population ids)",
    }
    for key in CHECKS:
        s = stats[key]
        lines.append(f"  {labels[key]}")
        lines.append(f"      violations = {s['violations']}")
        if s["first"] is not None:
            f0 = s["first"]
            lines.append(
                f"      first: frame={f0['frame']} phase={f0['phase']} "
                f"pub_len={f0['pub_len']} fresh_len={f0['fresh_len']} "
                f"n=({f0['n_left']},{f0['n_right']}) "
                f"stamp=({f0['stamp_left']},{f0['stamp_right']},"
                f"{f0['stamp_total']}) spacing_len={f0['spacing_len']}"
            )
            if f0["spacing_extra"]:
                lines.append(
                    f"      extra keys (pid, role, tackle_state, fb) = "
                    f"{f0['spacing_extra']}"
                )
            if f0["spacing_missing"]:
                lines.append(f"      missing keys = {f0['spacing_missing']}")
    lines.append("")
    lines.append("V4 VIOLATION FRAMES (attribution)")
    lines.append("-" * 72)
    for f in frames:
        if not f["v4"]:
            lines.append(
                f"  frame={f['frame']} phase={f['phase']} "
                f"pub={f['pub_len']} spacing={f['spacing_len']} "
                f"extra={f['spacing_extra']}"
            )
    lines.append("")
    lines.append("FRAME-LEVEL CHECKS")
    lines.append("-" * 72)
    lines.append(f"  V5 multi-publisher (same population, >1 total): "
                 f"{len(v5)}")
    for v in v5[:10]:
        lines.append(f"      frame={v['frame']} n_pop={v['n_pop']} "
                     f"totals={v['totals']}")
    lines.append(f"  V6 solve total integrity failures : {len(v6)}")
    for v in v6[:10]:
        lines.append(f"      frame={v['frame']} n_total={v['n_total']} "
                     f"n=({v['n_left']},{v['n_right']})")
    lines.append("")
    # distinct n_total per frame (diagnostic: multi-publisher source)
    multi_frames = {f: sorted(t) for f, t in multi.items() if len(t) > 1}
    lines.append(f"  frames with >1 distinct solve n_total : "
                 f"{len(multi_frames)}")
    for f in sorted(multi_frames)[:10]:
        lines.append(f"      frame={f} n_totals={multi_frames[f]}")
    lines.append("")

    ok = all(stats[k]["violations"] == 0 for k in CHECKS)
    ok = ok and not v5 and not v6
    lines.append("=" * 72)
    if ok:
        lines.append("VERDICT: PASS — one authoritative population; published")
        lines.append("         count == line population == spacing population")
        lines.append("         on every measured frame.")
    else:
        lines.append("VERDICT: FAIL — the invariant is violated (see above).")
    lines.append("=" * 72)

    text = "\n".join(lines)
    print(text)
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return ok


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    result = run_sequence()
    frames = result["frames"]
    if not frames:
        print("RESULT: no OPEN_PLAY frames recorded — nothing to measure.")
        return 1

    stats = summarise(frames)
    v5 = check_single_publisher()
    v6, multi = check_solve_integrity()

    write_csv(frames)
    ok = write_report(result, frames, stats, v5, v6, multi)

    with open("stage5_engine.log", "w", encoding="utf-8") as fh:
        fh.write(result["engine_log"])

    print(f"wrote: {POP_CSV}, {REPORT}, stage5_engine.log")
    return 0 if ok else 1


if __name__ == "__main__":
    _code = 0
    try:
        _code = main()
    finally:
        unwrap_all()
        pygame.quit()
    sys.exit(_code)
