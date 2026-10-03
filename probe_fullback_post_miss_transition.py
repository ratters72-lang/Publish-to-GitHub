"""probe_fullback_post_miss_transition.py — MINIMAL post-miss change ledger.

THE QUESTION
------------
A tagged fullback (``fullback_role_active``) is beaten by the ball carrier
(a missed tackle).  Over the next 60 frames the body appears to churn through
an unstable recovery phase before it commits to the chase.  Is that churn
caused by

    (A) ROLE REASSIGNMENT     — the tag / designated actor moves to another body,
    (B) TARGET REWRITES       — ``target_source`` changes (a different system
                                authors the command),
    (C) WRITER OWNERSHIP      — ``_target_writer`` changes hands, or
    (D) MOVEMENT-STATE CHURN  — ``movement_state`` flips with A/B/C static?

WHAT IT LOGS — THE MINIMAL SET
------------------------------
ONLY a change to one of these is printed, and ONLY the fields that changed:

    designated_fb_id   ``world.fullback_actor_id`` (the republish indirection)
    tagged_pids        EVERY body carrying ``fullback_role_active`` this frame,
                       as ``(pid, id(obj))`` — pid alone cannot prove the same
                       body survived a republish/rebuild, so the object identity
                       is logged alongside it
    role               ``player.role`` / ``player.defender_role`` / ``fb`` tag
    movement_state     MovementState name
    target_source      behaviour stamp
    _target_writer     the canonical last-writer tag
    tackle_role        the pre-tackle / lane stamp

Nothing else.  No target coordinates, no speeds, no authority predicates, no
freeze flags.  A frame that changes none of the above produces NO row.

METHOD
------
Read-only.  The spy wraps ``pretackle_machine.apply_outcome`` and calls the
real function UNCHANGED; it only records the miss.  The per-frame row is
SAMPLED after the engine has finished the frame, so every printed value is the
frame's FINAL applied state.  A change row carries the previous value, the new
value and the frame index, so the ledger reads as an ordered change history.

Run:    python probe_fullback_post_miss_transition.py
Report: tmp_fullback_post_miss_transition.txt
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import random
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

# The engine's own per-frame physics chatter would drown the report.
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

# The miss writer under measurement.
from game.defence.defence_lvl_1 import pretackle_machine as pm


# ── ROLLOUT CONFIG ───────────────────────────────────────────────────
MAX_FRAMES = int(os.environ.get("RLM_FBPM_FRAMES", "6000"))
SEED = int(os.environ.get("RLM_FBPM_SEED", "20260827"))
DT = 1.0 / 60.0

# Frames traced after each miss event.
WINDOW_FRAMES = int(os.environ.get("RLM_FBPM_WINDOW", "60"))

# Cap on events reported in detail.
MAX_EVENTS_REPORTED = int(os.environ.get("RLM_FBPM_EVENTS", "4"))

REPORT_PATH = os.environ.get(
    "RLM_FBPM_REPORT", "tmp_fullback_post_miss_transition.txt"
)

# The only outcome that engages a tackle; everything else is a miss.
CLEAN_ENGAGE = "CLEAN_ENGAGE"

# The seven tracked field groups, in report order.
TRACKED_FIELDS = (
    "designated_fb_id",
    "tagged_pids",
    "role",
    "movement_state",
    "target_source",
    "target_writer",
    "tackle_role",
)


# ═════════════════════════════════════════════════════════════════════
# SMALL HELPERS
# ═════════════════════════════════════════════════════════════════════

def name_of(obj):
    """``obj.name`` when present, else ``str(obj)``, else None."""
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def pid_of(p):
    return getattr(p, "player_id", None)


def defending_team(world):
    return list(getattr(world, "defending_team", None) or [])


def tagged_bodies(world):
    """Every body carrying the fullback tag, in defending-team order."""
    return [
        d for d in defending_team(world)
        if getattr(d, "fullback_role_active", False)
    ]


def find_body(world, pid):
    """The defending-team body with *pid* (None when it is gone)."""
    if pid is None:
        return None
    for d in defending_team(world):
        if pid_of(d) == pid:
            return d
    return None


def role_tuple(d):
    """The unified role tuple for *d* (None when the body is gone)."""
    if d is None:
        return None
    return (
        getattr(d, "role", None),
        name_of(getattr(d, "defender_role", None)),
        bool(getattr(d, "fullback_role_active", False)),
    )


# ═════════════════════════════════════════════════════════════════════
# THE LEDGER
# ═════════════════════════════════════════════════════════════════════

class ChangeLedger:
    """Records the miss events and the change-only trace after each one."""

    def __init__(self):
        self.frame = 0
        self.miss_events = []      # every non-engage outcome applied
        self.armed_events = []     # TES arming (context only)
        self.traces = {}           # event index -> ordered change rows
        self.frame_rows = {}       # event index -> per-frame row list
        self.frames_stepped = 0
        self._active = []          # [(event_index, until_frame)]
        self._prev = {}            # event index -> previous snapshot
        self._tracked = {}         # event index -> tracked pid

    # ── frame boundary ───────────────────────────────────────────────
    def begin_frame(self, world):
        self.frame = int(getattr(world, "frame_count", 0) or 0)

    # ── event recorders ──────────────────────────────────────────────
    def note_miss(self, defender, outcome_name):
        """A non-engage outcome was applied to *defender*."""
        self.miss_events.append({
            "frame": self.frame,
            "pid": pid_of(defender),
            "oid": id(defender),
            "outcome": outcome_name,
            "tagged": bool(getattr(defender, "fullback_role_active", False)),
        })

    def note_armed(self, d, role_name):
        self.armed_events.append({
            "frame": self.frame,
            "pid": pid_of(d),
            "role_name": role_name,
            "tagged": bool(getattr(d, "fullback_role_active", False)),
        })

    # ── opening / closing a trace window ─────────────────────────────
    def open_trace(self, index, tracked_pid, until_frame):
        self._tracked[index] = tracked_pid
        self._prev.pop(index, None)
        self.traces[index] = []
        self.frame_rows[index] = []
        self._active.append((index, until_frame))

    def close_due(self):
        self._active = [
            (i, until) for (i, until) in self._active
            if self.frame <= until
        ]

    # ── the snapshot ─────────────────────────────────────────────────
    def snapshot(self, world, tracked_pid):
        """The seven tracked fields, for the tracked body and the world."""
        body = find_body(world, tracked_pid)
        tagged = tagged_bodies(world)
        return {
            "designated_fb_id": getattr(world, "fullback_actor_id", None),
            "tagged_pids": tuple((pid_of(d), id(d)) for d in tagged),
            "role": role_tuple(body),
            "movement_state": (
                name_of(getattr(body, "movement_state", None))
                if body is not None else None
            ),
            "target_source": (
                getattr(body, "target_source", None)
                if body is not None else None
            ),
            "target_writer": (
                getattr(body, "_target_writer", None)
                if body is not None else None
            ),
            "tackle_role": (
                getattr(body, "tackle_role", None)
                if body is not None else None
            ),
        }

    # ── per-frame sample ─────────────────────────────────────────────
    def sample(self, world):
        """Diff each open trace's snapshot against the previous frame's."""
        for index, _until in list(self._active):
            tracked_pid = self._tracked.get(index)
            snap = self.snapshot(world, tracked_pid)
            prev = self._prev.get(index)
            self._prev[index] = snap

            if prev is None:
                # First frame of the window: record the state the miss left
                # behind as an explicit BASELINE row, not a change.
                self.frame_rows[index].append({
                    "frame": self.frame,
                    "baseline": True,
                    "changes": [],
                })
                self.traces[index].append({
                    "frame": self.frame,
                    "field": "(baseline)",
                    "old": None,
                    "new": summary(snap),
                })
                continue

            changes = [
                (field, prev.get(field), snap.get(field))
                for field in TRACKED_FIELDS
                if prev.get(field) != snap.get(field)
            ]
            self.frame_rows[index].append({
                "frame": self.frame,
                "baseline": False,
                "changes": [c[0] for c in changes],
            })
            for field, old, new in changes:
                self.traces[index].append({
                    "frame": self.frame,
                    "field": field,
                    "old": old,
                    "new": new,
                })

        self.close_due()


def summary(snap):
    """Compact one-line rendering of a baseline snapshot."""
    role = snap.get("role")
    role_txt = (
        "role=%s defender_role=%s fb=%s" % role
        if role is not None else "BODY_ABSENT"
    )
    return (
        "designated_fb_id=%s tagged=%s | %s | state=%s src=%s writer=%s "
        "tackle_role=%s"
        % (
            snap.get("designated_fb_id"),
            snap.get("tagged_pids"),
            role_txt,
            snap.get("movement_state"),
            snap.get("target_source"),
            snap.get("target_writer"),
            snap.get("tackle_role"),
        )
    )


def render_field(field, value):
    """One-line rendering of a single tracked field's value."""
    if field == "role" and isinstance(value, tuple):
        return "role=%s defender_role=%s fb=%s" % value
    if field == "tagged_pids":
        return "tagged=%s" % (tuple(value),)
    return "%s=%s" % (field, value)


def pair_label(field, old, new):
    """Compact ``old -> new`` rendering for a transition histogram."""
    return "%s -> %s" % (
        render_field(field, old),
        render_field(field, new),
    )


# ═════════════════════════════════════════════════════════════════════
# SPY INSTALLATION
# ═════════════════════════════════════════════════════════════════════

def install_spies(ledger):
    """Wrap the miss writer and the TES armer; both call the real thing."""
    real_outcome = pm.apply_outcome
    real_arm = OpenPlayEngine._arm_tackler_role

    def outcome(world, defender, bc, tuners, state=None, *a, **kw):
        res = real_outcome(world, defender, bc, tuners, state=state, *a, **kw)
        try:
            if name_of(res) != CLEAN_ENGAGE:
                ledger.note_miss(defender, name_of(res))
        except Exception:
            pass
        return res

    def arm(ope_self, d, role_enum, role_name, ctx):
        res = real_arm(ope_self, d, role_enum, role_name, ctx)
        try:
            ledger.note_armed(d, role_name)
        except Exception:
            pass
        return res

    pm.apply_outcome = outcome
    OpenPlayEngine._arm_tackler_role = arm
    return {"outcome": real_outcome, "arm": real_arm}


def uninstall_spies(originals):
    """Restore the real callables (probe hygiene)."""
    pm.apply_outcome = originals["outcome"]
    OpenPlayEngine._arm_tackler_role = originals["arm"]


# ═════════════════════════════════════════════════════════════════════
# THE ROLLOUT — the real engine, booted like the ownership probe
# ═════════════════════════════════════════════════════════════════════

def run_rollout(frames=MAX_FRAMES, seed=SEED, verbose=False):
    """Boot the engine, kick off, step it, return the change ledger."""
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

    ledger = ChangeLedger()
    originals = install_spies(ledger)
    opened = 0

    try:
        for frame in range(1, frames + 1):
            world.frame_count = frame
            ball.update(DT)
            ledger.begin_frame(world)

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
                        collision_system.resolve_all(players, world=world)

            # Open a window for every miss recorded during this frame.
            while opened < len(ledger.miss_events):
                ev = ledger.miss_events[opened]
                if ev["tagged"]:
                    ledger.open_trace(
                        opened, ev["pid"], frame + WINDOW_FRAMES
                    )
                opened += 1

            # SAMPLE AFTER the engine has finished the frame.
            ledger.sample(world)
            ledger.frames_stepped = frame

            if verbose and frame % 1000 == 0:
                print(
                    "    ... frame %d (misses=%d)"
                    % (frame, len(ledger.miss_events)),
                    file=_REAL_STDOUT, flush=True,
                )
    finally:
        uninstall_spies(originals)

    return {"ledger": ledger, "world": world, "open_play": open_play}
# ═════════════════════════════════════════════════════════════════════
# REPORT
# ═════════════════════════════════════════════════════════════════════

_REPORT = None
_REAL_STDOUT = sys.stdout


class _tee:
    """Write to the report file and the real stdout at once."""

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
    print("=" * 108)
    print(title)
    print("=" * 108)


def sub(title):
    print("")
    print("-" * 108)
    print(title)
    print("-" * 108)


def histogram(values, limit=24):
    hist = {}
    for v in values:
        hist[v] = hist.get(v, 0) + 1
    return sorted(hist.items(), key=lambda kv: -kv[1])[:limit]


def report(res):
    ledger = res["ledger"]

    banner("FULLBACK POST-MISS TRANSITION — MINIMAL CHANGE LEDGER")
    print("  seed                        : %d" % SEED)
    print("  frames stepped              : %d" % ledger.frames_stepped)
    print("  dt                          : %.6f s" % DT)
    print("  window traced per event     : %d frames" % WINDOW_FRAMES)
    print("  fields tracked              : %s"
          % ", ".join(TRACKED_FIELDS))

    # ── A. COVERAGE ──
    sub("A. COVERAGE — did the miss path fire on a tagged fullback?")
    print("  non-engage apply_outcome calls : %d"
          % len(ledger.miss_events))
    tagged_misses = [e for e in ledger.miss_events if e["tagged"]]
    print("  of those, on a TAGGED fullback : %d" % len(tagged_misses))
    if ledger.miss_events:
        print("  -- outcome distribution (all bodies) --")
        for k, v in histogram(e["outcome"] for e in ledger.miss_events):
            print("    %-24s = %d" % (k, v))
    if tagged_misses:
        print("  -- outcome distribution (tagged fullback only) --")
        for k, v in histogram(e["outcome"] for e in tagged_misses):
            print("    %-24s = %d" % (k, v))
        print("  first tagged miss frame        : %d"
              % tagged_misses[0]["frame"])
        print("  tagged miss pids               : %s"
              % sorted({e["pid"] for e in tagged_misses}))
    else:
        print("")
        print("  !! NO miss on a tagged fullback was captured.  The")
        print("     transition question was not exercised.  Nothing is")
        print("     claimed below.")
    if ledger.armed_events:
        tagged_armed = [e for e in ledger.armed_events if e["tagged"]]
        print("  TES arming calls               : %d (tagged: %d)"
              % (len(ledger.armed_events), len(tagged_armed)))

    # ── B. WHICH FIELD CHANGED ──
    sub("B. WHICH TRACKED FIELD CHANGED, ACROSS ALL TRACED FRAMES")
    all_rows = [r for rows in ledger.traces.values() for r in rows]
    change_rows = [r for r in all_rows if r["field"] != "(baseline)"]
    frames_total = sum(
        len([r for r in rows if not r["baseline"]])
        for rows in ledger.frame_rows.values()
    )
    frames_with_change = sum(
        1 for rows in ledger.frame_rows.values()
        for r in rows if (not r["baseline"]) and r["changes"]
    )
    print("  traced frames                : %d" % frames_total)
    print("  change events (rows printed) : %d" % len(change_rows))
    if change_rows:
        print("  -- change events by field --")
        for k, v in histogram(r["field"] for r in change_rows):
            print("    %-24s = %d" % (k, v))
    if frames_total:
        print("  frames carrying >=1 change   : %d / %d (%.1f%%)"
              % (frames_with_change, frames_total,
                 100.0 * frames_with_change / frames_total))

    # ── C. THE DETAILED WINDOWS ──
    sub("C. THE CHANGE-ONLY TRACE, FIRST %d TAGGED EVENTS"
        % MAX_EVENTS_REPORTED)
    shown = 0
    for index, ev in enumerate(ledger.miss_events):
        if not ev["tagged"]:
            continue
        if shown >= MAX_EVENTS_REPORTED:
            break
        shown += 1
        rows = ledger.traces.get(index) or []
        print("")
        print("  ═══════════════════════════════════════════════════════════")
        print("  EVENT %d — miss frame %d, pid=%s, outcome=%s"
              % (index, ev["frame"], ev["pid"], ev["outcome"]))
        print("  ═══════════════════════════════════════════════════════════")
        print("  (a frame is printed ONLY when a tracked field CHANGED)")
        if not rows:
            print("    no rows (the window was never sampled)")
            continue
        for row in rows:
            if row["field"] == "(baseline)":
                print("    f=%-6s BASELINE  %s" % (row["frame"], row["new"]))
            else:
                print("    f=%-6s CHANGE    %-18s %s  ->  %s"
                      % (row["frame"], row["field"],
                         render_field(row["field"], row["old"]),
                         render_field(row["field"], row["new"])))

    # ── E. THE TRANSITIONS THEMSELVES ──
    sub("D. THE TRANSITIONS THEMSELVES (old -> new, per tracked field)")
    pairs = {}
    for r in change_rows:
        key = (r["field"], pair_label(r["field"], r["old"], r["new"]))
        pairs[key] = pairs.get(key, 0) + 1
    for field in TRACKED_FIELDS:
        field_rows = [(k, v) for k, v in pairs.items() if k[0] == field]
        if not field_rows:
            continue
        print("")
        print("  %s:" % field)
        for (_f, pair), n in sorted(field_rows, key=lambda kv: -kv[1])[:10]:
            print("    %-62s = %d" % (pair, n))

    # ROLE-CHANGE DECOMPOSITION — the difference that decides the answer:
    # a change to the fullback TAG (or to the tagged SET) is REASSIGNMENT;
    # a change to ``defender_role`` while the tag holds is a STAMP FLIP on
    # the SAME body.
    fb_flag_flips = 0
    defender_role_flips = 0
    absent_flips = 0
    for r in change_rows:
        if r["field"] != "role":
            continue
        old, new = r["old"], r["new"]
        if old is None or new is None:
            absent_flips += 1
            continue
        if old[2] != new[2]:
            fb_flag_flips += 1
        elif old[1] != new[1]:
            defender_role_flips += 1
    print("")
    print("  role-change decomposition:")
    print("    fullback TAG flips (fb True<->False)  : %d" % fb_flag_flips)
    print("    defender_role flips (SAME body)       : %d"
          % defender_role_flips)
    print("    body-absent transitions               : %d" % absent_flips)

    # ── D. VERDICT ──
    banner("VERDICT — measured, not inferred")
    if not tagged_misses:
        print("  INCONCLUSIVE: no missed tackle was applied to a tagged")
        print("  fullback in this rollout, so the post-miss transition was")
        print("  never exercised.  Nothing is claimed.")
        print("")
        print("  Report written to %s" % REPORT_PATH)
        return

    counts = dict(histogram(r["field"] for r in change_rows))
    n = max(1, frames_total)
    print("  tracked frames analysed      = %d" % frames_total)
    print("  frames carrying a change     = %d (%.1f%%)"
          % (frames_with_change, 100.0 * frames_with_change / n))
    print("")
    print("  change events per field (rate per traced frame):")
    for field in TRACKED_FIELDS:
        c = counts.get(field, 0)
        print("    %-20s %5d   (%.3f per frame)" % (field, c, c / n))

    designated = counts.get("designated_fb_id", 0)
    tagged = counts.get("tagged_pids", 0)
    role_ch = counts.get("role", 0)
    state_ch = counts.get("movement_state", 0)
    src_ch = counts.get("target_source", 0)
    writer_ch = counts.get("target_writer", 0)
    tackle_ch = counts.get("tackle_role", 0)

    print("")
    print("  ── CAUSE CLASSIFICATION ──")
    print("    ROLE REASSIGNMENT : %s"
          % ("REASSIGNED (designated_fb_id=%d, tagged-set=%d, fb-tag=%d)"
             % (designated, tagged, fb_flag_flips)
             if (designated or tagged or fb_flag_flips)
             else "STABLE — designated_fb_id never changed, the tagged"
                  " SET never changed, and the tag never left the body")
          )
    print("    ROLE-STAMP FLIP   : %s"
          % ("CHURN (%d defender_role change(s) on the SAME tagged body)"
             % defender_role_flips
             if defender_role_flips
             else "STABLE (defender_role never changed)"))
    print("    TARGET REWRITES   : %s"
          % ("REWRITTEN (%d target_source change(s))" % src_ch
             if src_ch else "STABLE (target_source never changed)"))
    print("    WRITER OWNERSHIP  : %s"
          % ("CHANGED HANDS (%d _target_writer change(s))" % writer_ch
             if writer_ch else "STABLE (_target_writer never changed)"))
    print("    MOVEMENT-STATE    : %s"
          % ("CHURN (%d movement_state change(s))" % state_ch
             if state_ch else "STABLE (movement_state never changed)"))
    print("    TACKLE-ROLE STAMP : %s"
          % ("CHURN (%d tackle_role change(s))" % tackle_ch
             if tackle_ch else "STABLE (tackle_role never changed)"))

    print("")
    print("  ── THE ANSWER ──")
    if designated or tagged or fb_flag_flips:
        print("    Post-miss instability is ROLE REASSIGNMENT: the tagged")
        print("    body / designated actor changes inside the traced window,")
        print("    so the beaten body stops being the body the fullback")
        print("    systems own.  exact counts: designated_fb_id=%d,"
              % designated)
        print("    tagged-set=%d, fb-tag flips=%d." % (tagged, fb_flag_flips))
        if src_ch or writer_ch:
            print("    (Secondary: the command's author also changes —")
            print("     target_source=%d, _target_writer=%d.)"
                  % (src_ch, writer_ch))
    elif defender_role_flips or src_ch or writer_ch:
        print("    NOT reassignment.  The fullback role never migrates:")
        print("    designated_fb_id changed 0 times and the tagged set kept")
        print("    ONE identity (pid + object) for every traced frame.")
        print("    The instability is an AUTHORITY-STAMP / OWNERSHIP")
        print("    transition on the SAME body:")
        print("      defender_role flips    : %d" % defender_role_flips)
        print("      target_source rewrites : %d" % src_ch)
        print("      _target_writer changes : %d" % writer_ch)
        print("      movement_state churn   : %d" % state_ch)
        print("      tackle_role churn      : %d" % tackle_ch)
    elif src_ch or writer_ch:
        print("    Post-miss instability is a WRITE-OWNERSHIP change on a")
        print("    SINGLE body: the tag never moves, but the command's author")
        print("    changes hands (target_source=%d, _target_writer=%d)."
              % (src_ch, writer_ch))
        if not state_ch:
            print("    movement_state never changed, so the churn is")
            print("    ownership-only, not locomotion.")
    elif state_ch or tackle_ch:
        print("    Ownership is STABLE — no reassignment, no target-source or")
        print("    writer change.  The tag, the writer and the source all")
        print("    hold on ONE body for the whole window.")
        print("      movement_state changes : %d" % state_ch)
        print("      tackle_role changes    : %d" % tackle_ch)
        if state_ch:
            print("    The churn is the movement STATE itself: a")
            print("    locomotion-state oscillation, not an authority")
            print("    transition.")
        else:
            print("    movement_state NEVER changes — the locomotion state is")
            print("    held.  The only churn is the TACKLE-ROLE STAMP, i.e.")
            print("    the lane/pre-tackle authority stamp flipping while the")
            print("    tag, the target source and the writer stay put.")
    else:
        print("    No tracked field changed across the window.  The post-miss")
        print("    transition is stable on all seven surfaces; any observed")
        print("    instability is NOT role reassignment, target rewriting or")
        print("    writer ownership.")
    print("")
    print("  Report written to %s" % REPORT_PATH)


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    global _REPORT
    real = sys.stdout

    # PHASE 1: the rollout, with the engine's own chatter discarded.
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

    # PHASE 2: the report, into its own clean file and the terminal.
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _tee()
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
