"""probe_tes_depth_debt.py — STAGE 1: MEASURE the TES DEPTH DEBT.

Rollout Steps 1-3 ("Measure the problem", "Prove debt is not discharging",
"Define obligation threshold").  NO code authority yet: this probe only
MEASURES, and its whole purpose is to prove or falsify the diagnosis
BEFORE any behaviour is touched.

WHAT IT MEASURES
----------------
Boots the engine headless (kickoff -> catch -> OPEN_PLAY -> tackle -> TES)
exactly like ``probe_tes_lane_true.py`` / ``probe_tes_hold_integrator.py``
and, for every body in the TES DEPTH OBLIGATION population of every TES
chamber, records the canonical debt from
``game.defence.defence_lvl_1.tes_depth_obligation``:

    role depth = bc.x + WIDTH_OFFSETS[depth_role]
    debt       = |d.x - role_depth|
    obligated  = debt > tuners.tes_depth_obligation_tolerance_m

Per body, per chamber:

    defender_id  defender_role  depth_role
    start_debt   peak_debt      end_debt     frames_in_debt
    ahead_frames behind_frames  signed_end   closure_rate_mps
    reacquisitions

Plus the aggregate metrics table:

    TES depth debt start / end            (cohort, per chamber)
    median / worst discharge time         (resolved runs only)
    obligated defender-frames
    debt > 5 m duration                   (worst consecutive run)
    closure rate (m/s)
    debt re-acquisition count             (oscillation detector)

THE FALSIFICATION TEST
----------------------
The diagnosis predicts bodies "sitting at 6m / 7m / 8m debt for hundreds
of frames while matching BC speed".  The ESSENCE of that claim is
PERSISTENCE — the debt does not return under tolerance — so the primary
measurement is a DEEP-DEBT RUN:

    consecutive frames with debt >= FLAT_DEBT_MIN_M (5.0 m)

A PERSISTENT run is a deep-debt run of >= FLAT_DEBT_MIN_FRAMES (60) whose
NET closure over the run is <= NEAR_FLAT_CLOSURE_MPS (0.25 m/s), i.e. it
is not meaningfully discharging.

The plan's literal qualifier ("while matching BC speed") is measured as a
WITHIN-RUN FRACTION and reported separately as the LITERAL variant.  It is
deliberately NOT used to break run continuity: a receding body has
|bc.vx - d.vx| ~= its recession rate (0.6-0.75 m/s, just outside a 0.5
epsilon), and a single carrier acceleration mid-run would reset a
per-frame counter — the first pass of this probe returned a false REFUTED
for exactly that reason.

A non-zero PERSISTENT count PROVES the diagnosis; zero REFUTES it.

EXIT CODE REPORTS THE FINDING, NOT A CONTRACT
---------------------------------------------
    0 — probe ran; diagnosis PROVEN (persistent non-discharge found)
    1 — probe ran; diagnosis REFUTED (no persistent deep debt)
    2 — no TES chamber observed
    3 — the module is wired into the engine (no longer measurement-only)

MEASUREMENT-ONLY, MACHINE-CHECKED
---------------------------------
The probe scans the engine tree and fails if ANY module other than this
probe imports ``tes_depth_obligation``.  That turns "no behaviour change"
from a claim into a check.

Run:  python probe_tes_depth_debt.py
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import ast
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
from game.defence.defence_lvl_1 import tes_drive_structure as tds

DT = 1.0 / 60.0
MAX_FRAMES = 3000
ENGINE_LOG = "tmp_tes_depth_debt_engine.log"

# ── FALSIFICATION-TEST CONSTANTS ─────────────────────────────────────
# The diagnosis is "debts sitting at 6-8 m for hundreds of frames while
# the defender merely paces the carrier".
#
# 5.0 m is the rollout's own "debt > 5m duration" bar; 60 frames (1 s) is
# the shortest run that is clearly not a transient.
#
# CORRECTION (measured, first pass): gating run CONTINUITY on a per-frame
# speed match was mis-specified and produced a false REFUTED.  A body that
# is RECEDING has |bc.vx - d.vx| ~= the recession rate (0.6-0.75 m/s),
# which sits just OUTSIDE a 0.5 m/s epsilon, and a single carrier
# acceleration mid-run reset the counter.  The defect under test is
# PERSISTENCE — the debt never returns under tolerance — so continuity is
# now measured on the DEBT alone.  Speed agreement is recorded as a
# WITHIN-RUN FRACTION, which keeps the plan's literal "while matching BC
# speed" hypothesis measurable as well.
FLAT_DEBT_MIN_M = 5.0            # "debt > 5 m" — the rollout's own bar
FLAT_DEBT_MIN_FRAMES = 60        # 1 s — clearly not a transient
SPEED_MATCH_EPS = 0.5            # "matching BC speed" epsilon (m/s)
SPEED_MATCH_FRACTION = 0.9       # the LITERAL variant needs 90% of frames
NEAR_FLAT_CLOSURE_MPS = 0.25     # "not meaningfully discharging" (m/s)

MAX_TABLE_ROWS = 200
MAX_PERSISTENT_ROWS = 20

# Directories scanned by the measurement-only import check.
_SCAN_DIRS = ("game", "world", "player", "ball", "kick_off", "ai_agents")
_SELF_NAME = "probe_tes_depth_debt.py"
_MODULE_NAME = "tes_depth_obligation"
_MODULE_PATH = os.path.join(
    "game", "defence", "defence_lvl_1", "tes_depth_obligation.py",
)


# ─────────────────────────────────────────────────────────────────────
# SMALL HELPERS
# ─────────────────────────────────────────────────────────────────────

def _role_name(p):
    role = getattr(p, "defender_role", None)
    return getattr(role, "name", None) or (
        str(role) if role is not None else None
    )


def _mstate(p):
    ms = getattr(p, "movement_state", None)
    return getattr(ms, "name", None) or (str(ms) if ms is not None else None)


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


def _imports_module(src):
    """True when *src* actually IMPORTS the obligation module.

    AST-based, deliberately NOT a substring test: the tuner attribute
    ``tes_depth_obligation_tolerance_m`` and the module's own name in a
    comment both CONTAIN ``tes_depth_obligation`` as a substring, and a
    substring scan would report them as offenders.  Only a real import
    statement counts.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[-1] == _MODULE_NAME:
                return True
            for alias in node.names:
                if alias.name == _MODULE_NAME:
                    return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == _MODULE_NAME:
                    return True
    return False


def check_measurement_only():
    """Fail when any engine module imports tes_depth_obligation.

    Stage 1 is measurement-only.  If a non-probe module imports the
    definition module, the module is wired into the engine and this is no
    longer a pure measurement — the check turns that claim into a fact.
    """
    offenders = []
    for root_dir in _SCAN_DIRS:
        if not os.path.isdir(root_dir):
            continue
        for dirpath, _dirnames, filenames in os.walk(root_dir):
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                if os.path.normpath(path) == os.path.normpath(_MODULE_PATH):
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        src = fh.read()
                except OSError:
                    continue
                if _imports_module(src):
                    offenders.append(path)
    return offenders


# ─────────────────────────────────────────────────────────────────────
# HEADLESS BOOT (identical to probe_tes_lane_true.py)
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
        "def_role": _role_name(d),
        "depth_role": getattr(d, "depth_role", None),
        "start_debt": None,
        "peak_debt": 0.0,
        "end_debt": None,
        "frames": 0,
        "frames_in_debt": 0,
        "ahead_frames": 0,
        "behind_frames": 0,
        "signed_end": 0.0,
        "reacq": 0,
        "close_sum": 0.0,
        "close_frames": 0,
        "flat_run": 0,
        "flat_max_run": 0,
        "deep": None,        # open deep-debt run (debt >= FLAT_DEBT_MIN_M)
        "deep_best": None,   # longest deep-debt run seen for this body
        "runs": [],          # closed obligation runs
        "cur_run": None,     # open run
        "prev_debt": None,
    }


def _keep_best_deep(rec):
    """Promote the open deep-debt run into the per-body best slot.

    "Best" = LONGEST deep-debt run, so a run that never returned under the
    deep threshold is still recorded at chamber close.
    """
    deep = rec["deep"]
    if deep is None or deep["frames"] <= 0:
        return
    best = rec["deep_best"]
    if best is None or deep["frames"] > best["frames"]:
        rec["deep_best"] = dict(deep)


def _net_closure(run):
    """Net closure rate (m/s) across a deep-debt run.

    POSITIVE = the body closed ground over the run; NEGATIVE = the debt
    grew.  This is the discharge verdict for the run: a run whose net
    closure is ~0 is a run that merely MAINTAINED the debt.
    """
    frames = int(run.get("frames", 0) or 0)
    if frames <= 0:
        return 0.0
    return (float(run["debt_start"]) - float(run["debt_end"])) / (frames * DT)


def _speed_ok_fraction(run):
    """Fraction of the run's frames in which |bc.vx - d.vx| <= epsilon."""
    n = int(run.get("speed_frames", 0) or 0)
    if n <= 0:
        return 0.0
    return float(run.get("speed_ok", 0)) / float(n)


def update_record(rec, d, world, debt, err, obligated, seq):
    """Fold this frame's reading into *rec*."""
    frames = rec["frames"]
    if frames == 0:
        rec["start_debt"] = debt
        rec["depth_role"] = getattr(d, "depth_role", rec["depth_role"])
        rec["def_role"] = _role_name(d)
    rec["frames"] = frames + 1
    rec["end_debt"] = debt
    rec["signed_end"] = err
    if debt > rec["peak_debt"]:
        rec["peak_debt"] = debt

    # ── obligation runs ──
    if obligated:
        rec["frames_in_debt"] += 1
        if err > 0.0:
            rec["ahead_frames"] += 1
        elif err < 0.0:
            rec["behind_frames"] += 1
        if rec["prev_debt"] is not None and rec["cur_run"] is None:
            rec["reacq"] += 1
        if rec["cur_run"] is None:
            rec["cur_run"] = {
                "start_seq": seq, "frames": 0,
                "debt_start": debt, "discharged": False,
            }
        rec["cur_run"]["frames"] += 1
    else:
        if rec["cur_run"] is not None:
            rec["cur_run"]["discharged"] = True
            rec["runs"].append(rec["cur_run"])
            rec["cur_run"] = None

    # ── closure rate ──
    if rec["prev_debt"] is not None and obligated:
        rec["close_sum"] += (rec["prev_debt"] - debt) / DT
        rec["close_frames"] += 1

    # ── DEEP-DEBT RUN (the persistence measurement) ──
    # Consecutive frames with debt >= FLAT_DEBT_MIN_M.  The run is NOT
    # broken by a transient speed mismatch — see the constants note.  The
    # plan's "while matching BC speed" qualifier is carried as a within-run
    # FRACTION so both hypotheses stay measurable.
    speed_match = (
        abs(_f(getattr(world.ball_carrier, "vx", 0.0))
            - _f(getattr(d, "vx", 0.0)))
        <= SPEED_MATCH_EPS
    )
    if debt >= FLAT_DEBT_MIN_M:
        if rec["deep"] is None:
            rec["deep"] = {
                "start_seq": seq, "frames": 0,
                "debt_start": debt, "debt_end": debt,
                "speed_frames": 0, "speed_ok": 0,
            }
        rec["deep"]["frames"] += 1
        rec["deep"]["debt_end"] = debt
        rec["deep"]["speed_frames"] += 1
        if speed_match:
            rec["deep"]["speed_ok"] += 1
    else:
        if rec["deep"] is not None:
            _keep_best_deep(rec)
            rec["deep"] = None
    if speed_match:
        rec["flat_run"] += 1
        if rec["flat_run"] > rec["flat_max_run"]:
            rec["flat_max_run"] = rec["flat_run"]
    else:
        rec["flat_run"] = 0

    rec["prev_debt"] = debt


def close_record(rec):
    """Close any open run at chamber end (counted as UNRESOLVED)."""
    if rec["cur_run"] is not None:
        rec["cur_run"]["discharged"] = False
        rec["runs"].append(rec["cur_run"])
        rec["cur_run"] = None
    # A deep-debt run that is still open at chamber end is precisely the
    # "carried the whole chamber" case — record it.
    _keep_best_deep(rec)
    rec["deep"] = None


# ─────────────────────────────────────────────────────────────────────
# THE RUN
# ─────────────────────────────────────────────────────────────────────

def run_probe():
    buf = io.StringIO()
    chambers = []
    cur = None
    prev_seq = None
    was_in_chamber = False
    exempt_seen = set()
    frames_in_chamber = 0

    with contextlib.redirect_stdout(buf):
        (world, ball, open_play, kickoff_engine,
         collision_system, field) = boot_world()

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

            # ── chamber boundaries ──
            new_chamber = False
            if in_chamber and not was_in_chamber:
                new_chamber = True
            elif in_chamber and seq is not None and prev_seq is not None \
                    and seq != prev_seq:
                new_chamber = True
            if new_chamber:
                if cur is not None:
                    for rec in cur["bodies"].values():
                        close_record(rec)
                    chambers.append(cur)
                cur = {
                    "seq": seq,
                    "enter_frame": world.frame_count,
                    "exit_frame": None,
                    "ruck_x": _f(getattr(world, "ruck_x_m", 0.0)),
                    "ruck_y": _f(getattr(world, "ruck_y_m", 0.0)),
                    "bodies": {},
                    "frames": 0,
                }
            if not in_chamber:
                if was_in_chamber and cur is not None:
                    cur["exit_frame"] = world.frame_count
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

            for d in (getattr(world, "defending_team", None) or []):
                if d is None or d is world.ball_carrier:
                    continue
                if tdo.is_depth_exempt_role(d, world):
                    if tdo.has_role_depth(d, tuners):
                        exempt_seen.add(
                            (getattr(d, "player_id", "?"), _role_name(d))
                        )
                    continue
                if not tdo.has_role_depth(d, tuners):
                    continue
                target = tdo.tes_depth_target_x(world, d, tuners)
                if target is None:
                    continue
                err = tdo.tes_depth_error_m(world, d, target_x=target)
                debt = tdo.tes_depth_debt_m(world, d, target_x=target)
                obligated = debt > tol
                if cur is None:
                    continue
                rec = cur["bodies"].get(id(d))
                if rec is None:
                    rec = new_record(d)
                    cur["bodies"][id(d)] = rec
                update_record(rec, d, world, debt, err, obligated, seq)

        if cur is not None:
            for rec in cur["bodies"].values():
                close_record(rec)
            chambers.append(cur)

    with open(ENGINE_LOG, "w", encoding="utf-8") as fh:
        fh.write(buf.getvalue())

    return {
        "chambers": chambers,
        "frames_in_chamber": frames_in_chamber,
        "exempt_seen": sorted(exempt_seen),
    }


# ─────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────

def report(result):
    chambers = result["chambers"]
    if not chambers or result["frames_in_chamber"] == 0:
        print("\nRESULT: no TES depth chamber observed — nothing measured.")
        return 2

    # ── aggregate over every body-chamber record ──
    # The 1-based chamber number travels as an id-keyed map: the chamber
    # records are dicts, so ``list.index(c)`` would compare them BY VALUE
    # and can name the wrong chamber.
    _ch_index = {id(c): i for i, c in enumerate(chambers)}
    all_recs = []
    for c in chambers:
        for rec in c["bodies"].values():
            all_recs.append((c, rec))

    obligated_body_frames = sum(r["frames_in_debt"] for _, r in all_recs)
    total_body_frames = sum(r["frames"] for _, r in all_recs)
    ahead_frames = sum(r["ahead_frames"] for _, r in all_recs)
    behind_frames = sum(r["behind_frames"] for _, r in all_recs)
    reacq = sum(r["reacq"] for _, r in all_recs)

    resolved = []
    unresolved = 0
    for _c, r in all_recs:
        for run in r["runs"]:
            if run["discharged"]:
                resolved.append(run["frames"])
            else:
                unresolved += 1
    med = _median(resolved)
    worst = max(resolved) if resolved else None

    close_sum = sum(r["close_sum"] for _, r in all_recs)
    close_n = sum(r["close_frames"] for _, r in all_recs)
    closure = (close_sum / close_n) if close_n else None

    # ── DEEP-DEBT RUNS (the persistence measurement) ──
    deep_runs = [
        (c, r, r["deep_best"]) for c, r in all_recs
        if r["deep_best"] is not None
    ]
    # Primary: a deep run long enough to be a defect whose NET closure says
    # it is not meaningfully discharging.
    persistent = [
        (c, r, run) for c, r, run in deep_runs
        if run["frames"] >= FLAT_DEBT_MIN_FRAMES
        and _net_closure(run) <= NEAR_FLAT_CLOSURE_MPS
    ]
    # Secondary: the plan's literal hypothesis — the same runs, but with the
    # carrier's speed matched on >= SPEED_MATCH_FRACTION of their frames.
    literal_flat = [
        (c, r, run) for c, r, run in persistent
        if _speed_ok_fraction(run) >= SPEED_MATCH_FRACTION
    ]
    worst_deep = None
    for c, r, run in deep_runs:
        if worst_deep is None or run["frames"] > worst_deep[2]["frames"]:
            worst_deep = (c, r, run)

    # ── header ──
    print("=" * 78)
    print("TES DEPTH DEBT PROBE — Stage 1: measure the problem, prove or")
    print("falsify the flat-debt diagnosis.  NO authority is involved.")
    print("=" * 78)
    print(f"\nTES chambers observed : {len(chambers)}")
    print(f"TES chamber frames    : {result['frames_in_chamber']}")
    print(f"instrumented bodies   : {len(all_recs)} body-chambers")
    print(f"exempt bodies seen    : {len(result['exempt_seen'])} "
          f"(markers / DH / fullback / tacklers — exempt by definition)")

    # ── per-chamber debt-start / debt-end cohort summary ──
    print("\n-- TES DEPTH DEBT START / END PER CHAMBER (cohort) --")
    print(f"  {'#':<4}{'seq':>6}{'enter':>7}{'frames':>8}{'bodies':>8}"
          f"{'mean_start':>12}{'mean_end':>10}{'net':>8}{'peak':>8}"
          f"{'never_dis':>10}")
    for i, c in enumerate(chambers):
        recs = list(c["bodies"].values())
        if not recs:
            continue
        starts = [r["start_debt"] for r in recs if r["start_debt"] is not None]
        ends = [r["end_debt"] for r in recs if r["end_debt"] is not None]
        mean_start = sum(starts) / len(starts) if starts else 0.0
        mean_end = sum(ends) / len(ends) if ends else 0.0
        peak = max((r["peak_debt"] for r in recs), default=0.0)
        # A body that spent EVERY frame of the chamber over tolerance.
        never_dis = sum(
            1 for r in recs
            if r["frames"] > 0 and r["frames_in_debt"] == r["frames"]
        )
        print(f"  {i + 1:<4}{str(c['seq']):>6}{c['enter_frame']:>7}"
              f"{c['frames']:>8}{len(recs):>8}{mean_start:>12.2f}"
              f"{mean_end:>10.2f}{mean_end - mean_start:>8.2f}{peak:>8.2f}"
              f"{never_dis:>10}")

    # ── per-body table ──
    print("\n-- PER-BODY TES DEPTH DEBT (per chamber) --")
    print(f"  {'ch':<4}{'pid':<5}{'def_role':<15}{'depth':<7}"
          f"{'start':>8}{'peak':>8}{'end':>8}{'debt_f':>8}"
          f"{'ahead_f':>9}{'behind_f':>9}{'sgn_end':>9}"
          f"{'closure':>9}{'reacq':>7}")
    printed = 0
    truncated = False
    for i, c in enumerate(chambers):
        for rec in sorted(c["bodies"].values(),
                          key=lambda r: (-r["peak_debt"], str(r["pid"]))):
            if printed >= MAX_TABLE_ROWS:
                truncated = True
                break
            closure_rec = (rec["close_sum"] / rec["close_frames"]
                           if rec["close_frames"] else 0.0)
            print(f"  {i + 1:<4}{str(rec['pid']):<5}"
                  f"{str(rec['def_role']):<15}{str(rec['depth_role']):<7}"
                  f"{_f(rec['start_debt']):>8.2f}{rec['peak_debt']:>8.2f}"
                  f"{_f(rec['end_debt']):>8.2f}{rec['frames_in_debt']:>8}"
                  f"{rec['ahead_frames']:>9}{rec['behind_frames']:>9}"
                  f"{rec['signed_end']:>9.2f}{closure_rec:>9.2f}"
                  f"{rec['reacq']:>7}")
            printed += 1
        if truncated:
            break
    if truncated:
        print(f"  ... table truncated at {MAX_TABLE_ROWS} rows")

    # ── metrics table ──
    print("\n-- METRICS --")
    print(f"  TES depth debt start (mean, all bodies) : "
          f"{sum(_f(r['start_debt']) for _, r in all_recs) / max(1, len(all_recs)):.2f} m")
    print(f"  TES depth debt end   (mean, all bodies) : "
          f"{sum(_f(r['end_debt']) for _, r in all_recs) / max(1, len(all_recs)):.2f} m")
    if med is not None:
        print(f"  median discharge time                   : "
              f"{med:.0f} frames ({med * DT:.2f} s)")
    else:
        print("  median discharge time                   : n/a "
              "(no obligation run ever discharged)")
    if worst is not None:
        print(f"  worst discharge time                    : "
              f"{worst} frames ({worst * DT:.2f} s)")
    else:
        print("  worst discharge time                    : n/a")
    print(f"  unresolved obligation runs              : {unresolved}")
    print(f"  obligated defender-frames               : "
          f"{obligated_body_frames} / {total_body_frames}"
          f" ({100.0 * obligated_body_frames / max(1, total_body_frames):.1f}%)")
    print(f"    ... of which AHEAD of role depth       : {ahead_frames}")
    print(f"    ... of which BEHIND role depth         : {behind_frames}")
    print(f"  closure rate (mean while obligated)      : "
          + (f"{closure:+.3f} m/s" if closure is not None else "n/a")
          + "   (+ = closing, - = losing ground)")
    print(f"  debt re-acquisition count               : {reacq}")

    # ── the falsification test ──
    print("\n-- FALSIFICATION TEST: persistent deep debt (not discharging) --")
    print(f"  deep-debt run definition : debt >= {FLAT_DEBT_MIN_M:.1f} m, "
          f"consecutive frames")
    print(f"  PERSISTENT run           : >= {FLAT_DEBT_MIN_FRAMES} frames "
          f"with net closure <= {NEAR_FLAT_CLOSURE_MPS:.2f} m/s")
    print(f"  LITERAL variant          : same run, with |bc.vx - d.vx| <= "
          f"{SPEED_MATCH_EPS:.1f} m/s on >= {SPEED_MATCH_FRACTION:.0%} "
          "of frames")
    print(f"\n  deep-debt runs observed   : {len(deep_runs)}")
    print(f"  PERSISTENT_DEBT_BODIES    : {len(persistent)}")
    print(f"  LITERAL_FLAT_BODIES       : {len(literal_flat)}")
    if worst_deep is not None:
        _c, _r, _run = worst_deep
        print(f"  longest deep-debt run     : {_run['frames']} frames "
              f"({_run['frames'] * DT:.2f} s)  "
              f"debt {_run['debt_start']:.2f} -> {_run['debt_end']:.2f} m  "
              f"net closure {_net_closure(_run):+.3f} m/s  "
              f"speed-match {_speed_ok_fraction(_run):.0%}")
        print(f"    body                    : pid={_r['pid']} "
              f"role={_r['def_role']} depth={_r['depth_role']}")

    if persistent:
        print("\n  -- PERSISTENT (non-discharging) deep-debt runs --")
        print(f"  {'ch':<4}{'pid':<5}{'depth':<7}{'frames':>8}"
              f"{'debt_s':>8}{'debt_e':>8}{'net_cl':>9}{'spd_ok':>8}")
        for c, r, run in sorted(
            persistent, key=lambda t: -t[2]["frames"]
        )[:MAX_PERSISTENT_ROWS]:
            print(f"  {_ch_index[id(c)] + 1:<4}{str(r['pid']):<5}"
                  f"{str(r['depth_role']):<7}{run['frames']:>8}"
                  f"{run['debt_start']:>8.2f}{run['debt_end']:>8.2f}"
                  f"{_net_closure(run):>9.3f}"
                  f"{_speed_ok_fraction(run):>8.0%}")
        if len(persistent) > MAX_PERSISTENT_ROWS:
            print(f"  ... {len(persistent) - MAX_PERSISTENT_ROWS} more")

    print("\n" + "=" * 78)
    if persistent:
        print("RESULT: DIAGNOSIS PROVEN — defenders hold deep TES depth debt "
              f"(>= {FLAT_DEBT_MIN_M:.1f} m) for >= {FLAT_DEBT_MIN_FRAMES} "
              "consecutive frames without discharging it "
              f"(net closure <= {NEAR_FLAT_CLOSURE_MPS:.2f} m/s).")
        return 0
    print("RESULT: DIAGNOSIS REFUTED — no body held deep debt "
          f"(>= {FLAT_DEBT_MIN_M:.1f} m) for {FLAT_DEBT_MIN_FRAMES} "
          "consecutive frames without closing it.")
    return 1


def main():
    print("=" * 78)
    print("TES DEPTH DEBT PROBE")
    print("=" * 78)

    offenders = check_measurement_only()
    if offenders:
        print("\n[MEASUREMENT-ONLY] FAILED — engine modules import "
              f"{_MODULE_NAME}:")
        for path in offenders:
            print(f"  {path}")
        print("Stage 1 must be measurement-only.  Aborting.")
        return 3
    print(f"\n[MEASUREMENT-ONLY] PASSED — only this probe references "
          f"{_MODULE_NAME}.")

    result = run_probe()
    return report(result)


if __name__ == "__main__":
    code = 2
    try:
        code = main()
    finally:
        pygame.quit()
    sys.exit(code)
