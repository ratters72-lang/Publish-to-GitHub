"""probe_static_self_target.py -- NAME the writer that republishes
``target_x == player.x`` for defenders 203 / 207 in OPEN_PLAY after a miss.

THE QUESTION
------------
After a missed tackle, defender 203 / 207 is held in OPEN_PLAY with
``target_x`` equal to the body's own ``x`` every frame, while the ball
carrier's ``bc_x`` keeps increasing.  Several writers in the tree pin a
target to the body's own position (``d.target_x = d.x``):

  * ``defensive_behaviour_unified._apply_structured_open_play`` step 7
    (``target_source = "struct_freeze_timer"``) and its near-duplicate at
    ~line 4125 (``target_source = "freeze_timer"``);
  * ``defensive_behaviour_lvl1`` -- the ``freeze_timer`` branch of
    ``apply_step_up`` / ``apply_chase`` / ``apply`` / ``apply_contact``;
  * ``intercept_tacklers.apply_pre_tackle_stance``;
  * ``pre_tackle_authority._freeze_stance`` / ``_lock_depth``;
  * ``pretackle_attempt._freeze_stance``;
  * ``seed_hold.apply_seed_hold`` (pins ``target_y``, clamps ``target_x``).

Which one actually owns the frame cannot be read off the source: the write
path is plain last-writer-wins and several of the candidates do not stamp
``target_source`` at all.  This probe therefore ATTRIBUTES each self-pin
write by its exact CALL SITE (file:line) -- a ``Player.__setattr__`` spy
-- and stamps the frame number, so the FIRST writer to pin the target
after the line break can be named from measurement, not inference.

LOG (per frame, defenders 203 / 207, OPEN_PLAY only)
----------------------------------------------------
  frame, id, target_source, movement_state, role, x, target_x, bc_x

Written to ``tmp_self_target_log.csv``.  Nothing else is logged per frame.

GOAL
----
Identify the first writer owning the static self-target after the line
break.  Reported as the originating call site of the earliest self-pin
write at/after the first line-break frame for each pid, plus the
self-pin-writer histogram over the whole rollout.

Read-only: no engine module is written; the only patched attribute is
probe-local (``Player.__setattr__``) and every spy calls the original.
Run:  python probe_static_self_target.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import random
import sys
import traceback

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
from player.player import Player

# The stun / miss writer under measurement.
from game.defence.defence_lvl_1 import pretackle_machine as pm_mod

# The canonical line-break predicate (single source: bc.x > anchor + 1.0).
from game.defence.defence_lvl_1.movement_mode_decision import (
    carrier_past_defensive_line,
)


# -- ROLLOUT CONFIG ---------------------------------------------------
FRAMES = int(os.environ.get("RLM_SST_FRAMES", "1200"))
SEED = int(os.environ.get("RLM_SST_SEED", "20260827"))
DT = 1.0 / 60.0

# The defenders named in the question.
PIDS = (203, 207)

# ``abs(target_x - x)`` below which the write is a SELF-PIN.  Matches
# ``line_break_static_guard._LB_STATIC_SUPPRESSED_EPS_M`` and the
# ``freeze_audit`` soft-frozen offset test.
SELF_EPS = 0.05

LOG_PATH = os.environ.get("RLM_SST_LOG", "tmp_self_target_log.csv")
REPORT_PATH = os.environ.get("RLM_SST_REPORT", "tmp_self_target_report.txt")


def name_of(obj):
    """``obj.name`` when present, else ``str(obj)``, else None."""
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def pid_of(p):
    return getattr(p, "player_id", None)


def fnum(v):
    """Return *v* as float when numeric, else None (never raises)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def role_of(p):
    """The single ``role`` column: defender_role name, else role attr."""
    dr = getattr(p, "defender_role", None)
    if dr is not None:
        return name_of(dr)
    return getattr(p, "role", None)


# =====================================================================
# STATE
# =====================================================================
class Ledger:
    def __init__(self):
        self.frame = 0
        self.in_open_play = False
        self.bc_x = None
        # per-frame 8-column rows for PIDS (OPEN_PLAY only)
        self.frame_rows = []
        # every self-pin write captured, in frame order
        self.self_pin_events = []
        # call-site histogram of self-pin writes
        self.site_counts = {}
        # per-frame line-break flag
        self.break_frames = set()
        # apply_outcome (miss) records
        self.miss_rows = []
        self.open_play_frames = 0

    def note_self_pin(self, frame, pid, site, x, target_x, source, state, role):
        self.site_counts[site] = self.site_counts.get(site, 0) + 1
        self.self_pin_events.append({
            "frame": frame,
            "pid": pid,
            "site": site,
            "x": round(x, 4),
            "target_x": round(target_x, 4),
            "target_source": source,
            "movement_state": state,
            "role": role,
        })

    def note_miss(self, frame, pid, outcome, state):
        self.miss_rows.append({
            "frame": frame,
            "pid": pid,
            "outcome": name_of(outcome),
            "machine_state": name_of(state),
        })


_LEDGER = Ledger()
_REAL_SETATTR = None
_REAL_OUTCOME = None


# =====================================================================
# SPIES
# =====================================================================
def install_spies():
    """Attribute every self-pin ``target_x`` write on 203 / 207 by call site."""
    global _REAL_SETATTR, _REAL_OUTCOME

    _REAL_SETATTR = Player.__setattr__

    def setattr_spy(self, name, value):
        if name == "target_x" and _LEDGER.in_open_play:
            pid = pid_of(self)
            if pid in PIDS:
                v = fnum(value)
                x = fnum(getattr(self, "x", None))
                if v is not None and x is not None and abs(v - x) <= SELF_EPS:
                    stack = traceback.extract_stack()[:-1]
                    site = "unknown"
                    if stack:
                        fr = stack[-1]
                        site = "%s:%d" % (
                            os.path.basename(fr.filename), fr.lineno,
                        )
                    _LEDGER.note_self_pin(
                        _LEDGER.frame, pid, site, x, v,
                        getattr(self, "target_source", None),
                        name_of(getattr(self, "movement_state", None)),
                        role_of(self),
                    )
        _REAL_SETATTR(self, name, value)

    Player.__setattr__ = setattr_spy

    _REAL_OUTCOME = pm_mod.apply_outcome

    def outcome_spy(world, defender, bc, tuners, state=None, *a, **kw):
        res = _REAL_OUTCOME(world, defender, bc, tuners, state=state, *a, **kw)
        try:
            pid = pid_of(defender)
            if pid in PIDS:
                _LEDGER.note_miss(_LEDGER.frame, pid, res, state)
        except Exception:
            pass
        return res

    pm_mod.apply_outcome = outcome_spy


def rebuild_setattr():
    """Restore the true ``__setattr__`` (idempotent)."""
    if _REAL_SETATTR is not None:
        Player.__setattr__ = _REAL_SETATTR


# =====================================================================
# ROLLOUT
# =====================================================================
def run_rollout(frames=FRAMES, seed=SEED):
    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play

    kickoff_engine = KickoffEngine(world, ball, field)

    random.seed(seed)
    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    install_spies()

    for frame in range(1, frames + 1):
        _LEDGER.frame = frame
        world.frame_count = frame
        ball.update(DT)

        in_open_play = (world.phase == GamePhase.OPEN_PLAY)
        _LEDGER.in_open_play = in_open_play

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
                collision_system.resolve_all(world.players, world=world)

        _LEDGER.in_open_play = False

        # -- per-frame line-break flag (canonical predicate) --
        bc = getattr(world, "ball_carrier", None)
        _LEDGER.bc_x = None if bc is None else fnum(getattr(bc, "x", None))
        if in_open_play:
            _LEDGER.open_play_frames += 1
            try:
                if bc is not None and carrier_past_defensive_line(world, bc):
                    _LEDGER.break_frames.add(frame)
            except Exception:
                pass
            # -- the 8-column per-frame row --
            for p in (getattr(world, "defending_team", None) or []):
                pid = pid_of(p)
                if pid not in PIDS:
                    continue
                _LEDGER.frame_rows.append({
                    "frame": frame,
                    "id": pid,
                    "target_source": getattr(p, "target_source", None),
                    "movement_state": name_of(
                        getattr(p, "movement_state", None)),
                    "role": role_of(p),
                    "x": fnum(getattr(p, "x", None)),
                    "target_x": fnum(getattr(p, "target_x", None)),
                    "bc_x": _LEDGER.bc_x,
                })

        if frame % 100 == 0:
            print("    ... frame %d" % frame, file=_REAL_STDOUT, flush=True)

    rebuild_setattr()
    pm_mod.apply_outcome = _REAL_OUTCOME

    return {"world": world, "open_play": open_play, "ledger": _LEDGER}


# =====================================================================
# ANALYSIS / REPORT
# =====================================================================
def write_csv(rows, path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("frame,id,target_source,movement_state,role,x,target_x,bc_x\n")
        for r in rows:
            fh.write("%s,%s,%s,%s,%s,%s,%s,%s\n" % (
                r["frame"], r["id"],
                "" if r["target_source"] is None else r["target_source"],
                "" if r["movement_state"] is None else r["movement_state"],
                "" if r["role"] is None else r["role"],
                "" if r["x"] is None else round(r["x"], 4),
                "" if r["target_x"] is None else round(r["target_x"], 4),
                "" if r["bc_x"] is None else round(r["bc_x"], 4),
            ))


def histogram(rows, key_fn, limit=20):
    hist = {}
    for r in rows:
        k = key_fn(r)
        hist[k] = hist.get(k, 0) + 1
    return sorted(hist.items(), key=lambda kv: -kv[1])[:limit]


def first_break_frame(ledger):
    return min(ledger.break_frames) if ledger.break_frames else None


def first_self_pin_after(ledger, pid, frame):
    """Earliest self-pin write for *pid* with write-frame >= *frame*."""
    for ev in ledger.self_pin_events:
        if ev["pid"] == pid and ev["frame"] >= frame:
            return ev
    return None


def run_of_self_pins(ledger, pid):
    """Consecutive self-pin run stats per write-frame for *pid*."""
    frames = sorted({ev["frame"] for ev in ledger.self_pin_events
                     if ev["pid"] == pid})
    if not frames:
        return None
    best = cur = 1
    best_span = (frames[0], frames[0])
    start = frames[0]
    for i in range(1, len(frames)):
        if frames[i] == frames[i - 1] + 1:
            cur += 1
        else:
            cur = 1
            start = frames[i]
        if cur > best:
            best = cur
            best_span = (start, frames[i])
    return {"frames": len(frames), "longest": best, "first": frames[0],
            "last": frames[-1], "longest_span": best_span}


def report(res):
    led = res["ledger"]
    banner("STATIC SELF-TARGET -- MEASURED (target_x == player.x)")
    print("  seed                     : %d" % SEED)
    print("  frames stepped           : %d" % FRAMES)
    print("  OPEN_PLAY frames         : %d" % led.open_play_frames)
    print("  defenders of interest    : %s" % (list(PIDS),))
    print("  self-pin tolerance       : %.3f m" % SELF_EPS)
    print("  per-frame rows logged    : %d  -> %s"
          % (len(led.frame_rows), LOG_PATH))

    sub("A. LINE BREAK -- when did the carrier pass the line?")
    fb = first_break_frame(led)
    if fb is None:
        print("  NO line-break frame observed in this rollout.")
    else:
        bf = sorted(led.break_frames)
        print("  first line-break frame   : %d" % fb)
        print("  line-break frames        : %d  [%d .. %d]"
              % (len(bf), bf[0], bf[-1]))

    sub("B. SELF-PIN WRITES -- attributed by exact call site (file:line)")
    print("  self-pin writes on %s : %d" % (list(PIDS), len(led.self_pin_events)))
    if led.site_counts:
        for site, n in sorted(led.site_counts.items(), key=lambda kv: -kv[1]):
            print("    %6d  %s" % (n, site))
    else:
        print("    (none -- neither pid pinned its target to its own x)")

    sub("C. THE FIRST WRITER AFTER THE LINE BREAK -- per defender")
    if fb is None:
        print("  (no line break; reported from the first self-pin instead)")
    for pid in PIDS:
        anchor_frame = fb if fb is not None else 0
        ev = first_self_pin_after(led, pid, anchor_frame)
        run = run_of_self_pins(led, pid)
        print("")
        print("  --- pid %s ---" % pid)
        if run is None:
            print("    NO self-pin write observed for this pid.")
            continue
        print("    self-pin write-frames : n=%d  first=%d  last=%d  "
              "longest_run=%d %s"
              % (run["frames"], run["first"], run["last"],
                 run["longest"], run["longest_span"]))
        if ev is None:
            print("    FIRST self-pin at/after frame %d : (none)"
                  % anchor_frame)
        else:
            print("    FIRST self-pin at/after frame %d :" % anchor_frame)
            print("        frame          = %d" % ev["frame"])
            print("        call site      = %s" % ev["site"])
            print("        x / target_x   = %.4f / %.4f" % (ev["x"], ev["target_x"]))
            print("        target_source  = %s" % ev["target_source"])
            print("        movement_state = %s" % ev["movement_state"])
            print("        role           = %s" % ev["role"])

    sub("D. THE PERSISTENT WRITER -- every writer, by write count")
    if led.self_pin_events:
        worst_pid = max(PIDS, key=lambda p: (
            (run_of_self_pins(led, p) or {}).get("longest", 0)))
        run = run_of_self_pins(led, worst_pid)
        print("  worst pid = %s  longest_run = %s %s"
              % (worst_pid, run["longest"] if run else 0,
                 run["longest_span"] if run else None))
        if run:
            lo, hi = run["longest_span"]
            inside = [ev for ev in led.self_pin_events
                      if ev["pid"] == worst_pid and lo <= ev["frame"] <= hi]
            print("  writers inside that run:")
            for site, n in histogram(inside, lambda r: r["site"], 20):
                print("    %6d  %s" % (n, site))

    sub("E. THE MISS ITSELF -- apply_outcome on 203 / 207")
    print("  apply_outcome calls on %s : %d" % (list(PIDS), len(led.miss_rows)))
    if led.miss_rows:
        for k, v in histogram(led.miss_rows, lambda r: r["outcome"], 10):
            print("    %-20s = %d" % (str(k), v))
        for k, v in histogram(led.miss_rows, lambda r: r["machine_state"], 14):
            print("    %-20s = %d" % (str(k), v))

    banner("VERDICT")
    if led.site_counts:
        top_site, top_n = max(led.site_counts.items(), key=lambda kv: kv[1])
        print("  dominant self-pin writer  = %s  (%d writes)" % (top_site, top_n))
    if fb is None:
        print("  line break                = NOT observed")
    else:
        for pid in PIDS:
            ev = first_self_pin_after(led, pid, fb)
            if ev is None:
                print("  pid %-5s first after break = (no self-pin)" % pid)
            else:
                print("  pid %-5s first after break = %s  (frame %d)"
                      % (pid, ev["site"], ev["frame"]))
    print("")
    print("  CSV log -> %s" % LOG_PATH)
    print("  report  -> %s" % REPORT_PATH)


_REPORT = None
_REAL_STDOUT = sys.stdout


class tee:
    def write(self, s):
        try:
            if _REPORT is not None:
                _REPORT.write(s)
        except Exception:
            pass
        return _REAL_STDOUT.write(s)

    def flush(self):
        try:
            if _REPORT is not None:
                _REPORT.flush()
        except Exception:
            pass
        return _REAL_STDOUT.flush()


def banner(title):
    print("")
    print("=" * 100)
    print(title)
    print("=" * 100)


def sub(title):
    print("")
    print("-" * 100)
    print(title)
    print("-" * 100)


def main():
    global _REPORT
    real = sys.stdout

    _devnull = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = _devnull
    try:
        res = run_rollout()
    finally:
        sys.stdout = real
        try:
            _devnull.close()
        except Exception:
            pass

    write_csv(res["ledger"].frame_rows, LOG_PATH)

    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = tee()
    try:
        report(res)
    finally:
        sys.stdout = real
        try:
            _REPORT.flush()
            _REPORT.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
