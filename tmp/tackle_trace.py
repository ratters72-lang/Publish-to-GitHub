"""tmp/tackle_trace.py -- single-tackle frame-by-frame FB trace (read-only, v3).

WHY THIS EXISTS
---------------
The handoff defect: "the fullback stays beside the ball carrier until PTB."
The tagged diagnostic ``tmp/fb_chase_diag.py`` cannot decide WHY:
  * it does not log ``tes_attached`` / ``attach_slot``,
  * it does not log the TES attachment registries
    (``ctx.attached_ids`` / ``ctx.attach_offsets`` / ``_attached_player_slots``),
  * it does not log ``depth_locked`` / ``depth_lock_y``,
  * it does not log WHICH system wrote the FB's position / target that frame.

This tracer boots the SHIPPED engine exactly like ``tmp/fb_chase_diag.py``
(dummy SDL, seed ``RLM_TRACE_SEED`` default 20260827, dt = 1/60) and, for a
SINGLE tackle window (contact -> PTB), records per frame:

  * FB and BC position/velocity and the FB-BC gap,
  * the FB's ``tes_attached`` flag and ``attach_slot``,
  * the TES attachment registries (attached_ids / attach_offsets / slots),
  * the FB's ``depth_locked`` / ``depth_lock_y``,
  * the FB's ``in_contact`` / ``is_tackler`` / ``_tackle_commit_active``,
  * the FB's movement state, target, ``target_source`` / ``_target_writer``,
  * whether the FB is in ``world.tackler_ids``,
  * EVERY system that wrote the FB's position/target that frame (in order).

Attribution wraps every writer-looking method on the live engine object (plus
``Player.update`` and ``CollisionSystem.resolve_all``) and records a change
only when no nested wrapper already recorded one -- so nested writers are not
double-counted and the ordered list names the real chain of writers.

Verdict classification:
  (a) ATTACHED-FOLLOWER       -- live TES tackler glued to the BC,
  (b) DETACHED-BUT-PINNED     -- not attached, frozen / zero-velocity,
  (c) DETACHED-BUT-OVERRIDDEN -- not attached, driven every frame by writers.

Read-only: monkeypatches ONLY the live engine object and ``Player.update`` in
this process.  No production file is edited.

Run:   python tmp/tackle_trace.py
Writes: tmp/tackle_trace.txt  and  tmp/ttrace2.txt
Env:   RLM_TRACE_SEED   (default 20260827)
       RLM_TRACE_FRAMES (default 12000)
       RLM_TRACE_INDEX  (-1 = auto-select the attached window)
"""

import collections
import io
import math
import os
import re
import sys


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, ".")


# ------------------------------------------------------------------ #
# BOOT (quiet: the engine prints its own filename + telemetry at import)
# ------------------------------------------------------------------ #
_REAL_STDOUT = sys.stdout


def _quiet():
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )


def _loud():
    sys.stdout = _REAL_STDOUT


_quiet()
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

_loud()


SEED = int(os.environ.get("RLM_TRACE_SEED", "20260827"))
MAX_FRAMES = int(os.environ.get("RLM_TRACE_FRAMES", "12000"))
DETAIL_INDEX = int(os.environ.get("RLM_TRACE_INDEX", "-1"))
DT = 1.0 / 60.0
BUILD_TAG = "v3"

_CONTACT_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT")
_CD_PHASES = ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
              "TACKLE_DOMINANCE")
_PTB_PHASES = ("RELEASE", "PTB_PREP", "PTB_EXEC", "PTB",
               "POST_RUCK_OPEN_PLAY")
_OPEN_PHASES = _CD_PHASES

# The FB tuple we watch; any change to one of these is a "write".
_WATCH_ATTRS = ("x", "y", "vx", "vy", "target_x", "target_y")
_STATE_ATTRS = (
    "tes_attached", "attach_slot", "movement_state",
    "depth_locked", "depth_lock_y", "in_contact", "is_tackler",
    "_tackle_commit_active", "frozen",
)

_HANDOFF_NAMES = ("_prepare_release", "_clear_tes_artefacts",
                  "_detach_all_tacklers")

_WRAP_RE = re.compile(
    r"^_(update|prepare|apply|clear|detach|start|handle|resolve|integrate|"
    r"glide|pin|move|arm|assign|stamp|drive)"
)


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _state_name(actor):
    ms = getattr(actor, "movement_state", None)
    return getattr(ms, "name", None) if ms is not None else None


def _phase_name(open_play):
    return getattr(open_play, "_phase", None)


def _tagged_fullbacks(world):
    return [
        p for p in getattr(world, "defending_team", [])
        if getattr(p, "fullback_role_active", False)
    ]


def _pid(actor):
    return getattr(actor, "player_id", None)


# ------------------------------------------------------------------ #
# THE TRACKER
# ------------------------------------------------------------------ #
class Tracker:
    """Watches one FB body and names every writer of each change."""

    def __init__(self):
        self.fb = None
        self.locked_pid = None
        self.events = []            # list of (name, before, after) this frame
        self.last_writer = None
        self.last_fields = ()
        self.handoff_rows = []

    def refresh_body(self, world):
        if self.fb is not None:
            return
        tagged = _tagged_fullbacks(world)
        if tagged:
            self.fb = tagged[0]
            self.locked_pid = _pid(self.fb)

    def snap(self):
        fb = self.fb
        if fb is None:
            return None
        values = [_num(getattr(fb, a, 0.0)) for a in _WATCH_ATTRS]
        values.extend(getattr(fb, a, None) for a in _STATE_ATTRS)
        return tuple(values)

    def _changed_fields(self, before, after):
        if before is None or after is None:
            return ()
        names = list(_WATCH_ATTRS) + list(_STATE_ATTRS)
        return tuple(
            names[i] for i in range(len(names)) if before[i] != after[i]
        )

    def wrap(self, owner, name):
        orig = getattr(owner, name, None)
        if orig is None or not callable(orig):
            return
        tracker = self

        def wrapper(*args, **kwargs):
            fb = tracker.fb
            before = tracker.snap()
            n_before = len(tracker.events)
            result = orig(*args, **kwargs)
            after = tracker.snap()
            if before != after and len(tracker.events) == n_before:
                tracker.events.append((name, before, after))
                tracker.last_writer = name
                tracker.last_fields = tracker._changed_fields(before, after)
            if name in _HANDOFF_NAMES:
                tracker._dump_handoff(name, fb, before, after)
            return result

        try:
            setattr(owner, name, wrapper)
        except (AttributeError, TypeError):
            pass

    def _dump_handoff(self, name, fb, before=None, after=None):
        if fb is None:
            return

        def fields(snap):
            if snap is None:
                return {}
            names = list(_WATCH_ATTRS) + list(_STATE_ATTRS)
            return {names[i]: snap[i] for i in range(len(names))}

        self.handoff_rows.append({
            "name": name,
            "pid": _pid(fb),
            "before": fields(before),
            "after": fields(after),
            "engaged_after": bool(getattr(fb, "_fb_emergency_engaged", False)),
            "return_after": bool(
                getattr(fb, "_fb_post_tackle_return_active", False)
            ),
        })

    def reset_frame(self):
        self.events = []
        self.last_writer = None
        self.last_fields = ()


def _tes_ctx_probe(open_play, pid):
    ctx = getattr(open_play, "tes_ctx", None)
    attached_ids = getattr(ctx, "attached_ids", None)
    attach_offsets = getattr(ctx, "attach_offsets", None)
    in_attached = None
    if attached_ids is not None:
        try:
            in_attached = bool(pid in attached_ids)
        except TypeError:
            in_attached = None
    offset = None
    if attach_offsets is not None:
        try:
            offset = attach_offsets.get(pid)
        except AttributeError:
            offset = None
    slots = getattr(open_play, "_attached_player_slots", None)
    slot = None
    if slots is not None:
        try:
            slot = slots.get(pid)
        except AttributeError:
            slot = None
    return in_attached, offset, slot


def build_row(frame, world, open_play, tracker):
    fb = tracker.fb
    if fb is None:
        return None
    bc = getattr(world, "ball_carrier", None)
    pid = _pid(fb)
    fx, fy = _num(getattr(fb, "x", 0.0)), _num(getattr(fb, "y", 0.0))
    bx = by = None
    gap = None
    if bc is not None:
        bx, by = _num(getattr(bc, "x", 0.0)), _num(getattr(bc, "y", 0.0))
        gap = math.hypot(fx - bx, fy - by)
    in_attached, offset, slot = _tes_ctx_probe(open_play, pid)
    primary = getattr(open_play, "primary", None)
    joiners = getattr(open_play, "joiners", None) or []
    # The snapshot Player.update actually consumed (x=idx0, vx=idx2, tx=idx4).
    pu_before = None
    for (name, before, _after) in tracker.events:
        if name == "Player.update":
            pu_before = before
    pu_tx = pu_before[4] if pu_before is not None else None
    pu_ty = pu_before[5] if pu_before is not None else None
    return {
        "frame": frame,
        "phase": _phase_name(open_play),
        "pid": pid,
        "x": fx, "y": fy,
        "vx": _num(getattr(fb, "vx", 0.0)),
        "vy": _num(getattr(fb, "vy", 0.0)),
        "bcx": bx, "bcy": by,
        "gap": gap,
        "tes_attached": bool(getattr(fb, "tes_attached", False)),
        "attach_slot": getattr(fb, "attach_slot", None),
        "in_attached_ids": in_attached,
        "attach_offset": offset,
        "attached_slot_map": slot,
        "depth_locked": getattr(fb, "depth_locked", None),
        "depth_lock_y": getattr(fb, "depth_lock_y", None),
        "in_contact": bool(getattr(fb, "in_contact", False)),
        "is_tackler": bool(getattr(fb, "is_tackler", False)),
        "commit": bool(getattr(fb, "_tackle_commit_active", False)),
        "state": _state_name(fb),
        "target_x": _num(getattr(fb, "target_x", 0.0)),
        "target_y": _num(getattr(fb, "target_y", 0.0)),
        "target_source": getattr(fb, "target_source", None),
        "target_writer": getattr(fb, "_target_writer", None),
        "frozen": bool(getattr(fb, "frozen", False)),
        "freeze_timer": _num(getattr(fb, "freeze_timer", 0.0)),
        "in_tackler_ids": bool(pid in getattr(world, "tackler_ids", set())),
        "is_primary": (primary is not None and _pid(primary) == pid),
        "is_joiner": any(_pid(j) == pid for j in joiners),
        "tes_active": bool(getattr(world, "tes_active", False)),
        "tagged": bool(getattr(fb, "fullback_role_active", False)),
        "arrival_mode": bool(getattr(fb, "arrival_mode", False)),
        "move_speed": _num(getattr(fb, "move_speed", 0.0)),
        "max_def_speed": _num(
            getattr(fb, "max_defensive_speed_mps", 0.0)
        ),
        "movement_writer": getattr(fb, "movement_writer", None),
        "pu_tx": pu_tx,
        "pu_ty": pu_ty,
        "last_writer": tracker.last_writer,
        "last_fields": tracker.last_fields,
        "writers": [
            (name, tracker._changed_fields(before, after))
            for (name, before, after) in tracker.events
        ],
    }


# ------------------------------------------------------------------ #
# WRAPPER INSTALLATION
# ------------------------------------------------------------------ #
def install_engine_wrappers(tracker, open_play):
    names = set()
    for cls in type(open_play).__mro__:
        for attr, value in vars(cls).items():
            if _WRAP_RE.match(attr) and callable(value):
                names.add(attr)
    for name in sorted(names):
        tracker.wrap(open_play, name)
    return sorted(names)


def install_player_update_wrapper(tracker):
    original = Player.update
    if getattr(original, "_tackle_trace_wrapped", False):
        return

    def traced_update(self, *args, **kwargs):
        fb = tracker.fb
        if self is not fb:
            return original(self, *args, **kwargs)
        before = tracker.snap()
        n_before = len(tracker.events)
        result = original(self, *args, **kwargs)
        after = tracker.snap()
        if before != after and len(tracker.events) == n_before:
            tracker.events.append(("Player.update", before, after))
            tracker.last_writer = "Player.update"
            tracker.last_fields = tracker._changed_fields(before, after)
        return result

    traced_update._tackle_trace_wrapped = True
    Player.update = traced_update


# ------------------------------------------------------------------ #
# WINDOW CAPTURE
# ------------------------------------------------------------------ #
def _run_capture():
    import random

    random.seed(SEED)
    if not pygame.display.get_init():
        pygame.display.init()
    pygame.display.set_mode((1, 1), pygame.NOFRAME)

    collision_system = CollisionSystem()
    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    open_play = OpenPlayEngine(world, seed=SEED)
    world.open_play = open_play
    world.open_play_engine = open_play
    kickoff_engine = KickoffEngine(world, ball, field)

    tracker = Tracker()
    wrapped = install_engine_wrappers(tracker, open_play)
    tracker.wrap(collision_system, "resolve_all")
    install_player_update_wrapper(tracker)

    windows = []
    in_window = False
    current = None

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)
    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    for frame in range(1, MAX_FRAMES + 1):
        world.frame_count = frame
        tracker.reset_frame()
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
                collision_system.resolve_all(world.players, world=world)
        else:
            continue

        tracker.refresh_body(world)
        phase = _phase_name(open_play)

        if not in_window and phase in _OPEN_PHASES:
            in_window = True
            current = {"start": frame, "rows": [], "handoff": []}
            windows.append(current)

        if in_window:
            row = build_row(frame, world, open_play, tracker)
            if row is not None:
                current["rows"].append(row)
            for h in tracker.handoff_rows:
                entry = dict(h)
                entry["frame"] = frame
                current["handoff"].append(entry)
            tracker.handoff_rows = []
            if phase == "OPEN_PLAY" and current["rows"]:
                current["end"] = frame
                in_window = False
                current = None

    if current is not None and current["rows"]:
        current["end"] = world.frame_count

    return {
        "windows": windows,
        "wrapped": wrapped,
        "seed": SEED,
        "frames": MAX_FRAMES,
        "locked_pid": tracker.locked_pid,
        "handoff_total": sum(len(w["handoff"]) for w in windows),
    }
# ------------------------------------------------------------------ #
# ANALYSIS + REPORT
# ------------------------------------------------------------------ #
def _stats(values):
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return (min(clean), max(clean), sum(clean) / len(clean))


def _fmt_stats(stats):
    if not stats:
        return "-"
    return "(%.2f/%.2f/%.2f)" % stats


def _window_summary(win):
    rows = win["rows"]
    cd = [r for r in rows if r["phase"] in _CD_PHASES]
    cdp = [r for r in rows if r["phase"] in _CONTACT_PHASES]
    ptb = [r for r in rows if r["phase"] in _PTB_PHASES]
    attached = sum(1 for r in cd if r["tes_attached"] or r["in_attached_ids"])
    tackler = sum(1 for r in cd if r["in_tackler_ids"])
    cd_min = None
    cd_min_frame = None
    for r in cd:
        if r["gap"] is None:
            continue
        if cd_min is None or r["gap"] < cd_min:
            cd_min = r["gap"]
            cd_min_frame = r["frame"]
    phases = []
    for r in rows:
        if not phases or phases[-1] != r["phase"]:
            phases.append(r["phase"])
    return {
        "start": win["start"],
        "end": win.get("end", rows[-1]["frame"] if rows else win["start"]),
        "pid": rows[0]["pid"] if rows else None,
        "phases": phases,
        "n_rows": len(rows),
        "n_contact": len(cdp),
        "n_drive": len(cd),
        "n_ptb": len(ptb),
        "attached_cd": attached,
        "tackler_cd": tackler,
        "cd_gap": _stats(r["gap"] for r in cd),
        "ptb_gap": _stats(r["gap"] for r in ptb),
        "cd_min_gap": cd_min,
        "cd_min_gap_frame": cd_min_frame,
    }


def _choose_window(win_summaries):
    """Prefer a real drive in which the FB was attached to the BC."""
    best_index = None
    best_score = None
    for i, s in enumerate(win_summaries):
        if s["n_drive"] < 10:
            continue
        if s["attached_cd"] > 0:
            rank = 0
        elif s["tackler_cd"] > 0:
            rank = 1
        else:
            rank = 2
        gap = s["cd_gap"][2] if s["cd_gap"] else 999.0
        score = (rank, gap)
        if best_score is None or score < best_score:
            best_score = score
            best_index = i
    if best_index is None and win_summaries:
        best_index = 0
    return best_index


def _verdict(win, summary):
    rows = win["rows"]
    cd = [r for r in rows if r["phase"] in _CD_PHASES]
    ptb = [r for r in rows if r["phase"] in _PTB_PHASES]
    attached_frames = [
        r for r in cd if r["tes_attached"] or r["in_attached_ids"]
    ]
    tackler_frames = [r for r in cd if r["in_tackler_ids"]]
    gap_band = None
    if summary["cd_gap"]:
        gmin, gmax, _ = summary["cd_gap"]
        gap_band = gmax - gmin
    pos_writers = collections.Counter(
        r["last_writer"] for r in rows if r["last_writer"]
    )
    tgt_writers = collections.Counter(
        (r["target_writer"] or r["target_source"] or "none") for r in rows
    )
    frozen_frames = sum(1 for r in cd if r["frozen"])
    zero_vel = sum(
        1 for r in cd if abs(r["vx"]) < 1e-6 and abs(r["vy"]) < 1e-6
    )
    ptb_glued = None
    if summary["ptb_gap"]:
        ptb_glued = summary["ptb_gap"][2]

    lines = []
    if attached_frames or tackler_frames:
        glued = (gap_band is not None and gap_band < 1.5
                 and summary["cd_min_gap"] is not None
                 and summary["cd_min_gap"] < 3.0)
        if glued:
            lines.append(
                "VERDICT: (a) ATTACHED-FOLLOWER -- the FB is a live TES "
                "tackler glued to the BC."
            )
        else:
            lines.append(
                "VERDICT: (a') ATTACHED-but-MOVING -- the FB carries the TES "
                "attachment during the drive but its FB-BC gap is not held "
                "constant."
            )
        lines.append(
            "  attachment live on %d/%d contact-drive frames "
            "(tes_attached: %d, in_tackler_ids: %d)."
            % (max(len(attached_frames), len(tackler_frames)), len(cd),
               len(attached_frames), len(tackler_frames))
        )
        lines.append(
            "  drive gap min/max/mean = %s (band %.2f m)."
            % (_fmt_stats(summary["cd_gap"]), gap_band or 0.0)
        )
    else:
        pinned = (frozen_frames > 0
                  or (zero_vel >= max(1, len(cd) // 2)))
        if pinned:
            lines.append(
                "VERDICT: (b) DETACHED-BUT-PINNED -- the FB is NOT attached "
                "during the drive and is pinned (frozen or zero-velocity)."
            )
        else:
            top = pos_writers.most_common(1)
            top_name = top[0][0] if top else "UNATTRIBUTED"
            lines.append(
                "VERDICT: (c) DETACHED-BUT-OVERRIDDEN -- the FB is NOT "
                "attached during the drive and is driven every frame."
            )
            lines.append("  dominant position writer: %s" % top_name)
        lines.append(
            "  no TES attachment on any of the %d contact-drive frames."
            % len(cd)
        )
        lines.append(
            "  frozen=%d, zero-velocity=%d of %d; drive gap = %s."
            % (frozen_frames, zero_vel, len(cd), _fmt_stats(summary["cd_gap"]))
        )
    lines.append(
        "  POST-CONTACT LEG (RELEASE -> PTB): %d frames, FB-BC gap mean=%.2f "
        "m -- the FB is %s the carrier after the tackle ends."
        % (len(ptb), (ptb_glued if ptb_glued is not None else -1.0),
           "still beside" if (ptb_glued is not None and ptb_glued < 1.0)
           else "clear of")
    )
    lines.append("  position-writer histogram: %s" % dict(pos_writers))
    lines.append("  target-writer histogram:   %s" % dict(tgt_writers))
    return lines


def _fmt_row(r):
    writer_chain = "; ".join(
        "%s{%s}" % (name, ",".join(fields))
        for name, fields in r.get("writers", [])
    ) or (r["last_writer"] or "-")
    return (
        "f=%-5s %-16s FB=(%7.2f,%7.2f v%6.2f,%6.2f) BC=(%7.2f,%7.2f) "
        "gap=%6.2f att=%d slot=%s ids=%s tl=%d st=%-7s tgt=(%7.2f,%7.2f) "
        "tsrc=%-18s dep=%s con=%d tk=%d com=%d fz=%d tkl=%d prim=%d join=%d "
        "| W:%s"
        % (
            r["frame"], r["phase"], r["x"], r["y"], r["vx"], r["vy"],
            (r["bcx"] if r["bcx"] is not None else float("nan")),
            (r["bcy"] if r["bcy"] is not None else float("nan")),
            (r["gap"] if r["gap"] is not None else float("nan")),
            1 if r["tes_attached"] else 0,
            r["attach_slot"] if r["attach_slot"] is not None else "-",
            "-" if r["in_attached_ids"] is None else int(r["in_attached_ids"]),
            1 if r["in_tackler_ids"] else 0,
            r["state"] or "-",
            r["target_x"], r["target_y"],
            (r["target_writer"] or r["target_source"] or "-")[:18],
            r["depth_locked"] if r["depth_locked"] is not None else "-",
            1 if r["in_contact"] else 0,
            1 if r["is_tackler"] else 0,
            1 if r["commit"] else 0,
            1 if r["frozen"] else 0,
            1 if r["in_tackler_ids"] else 0,
            1 if r["is_primary"] else 0,
            1 if r["is_joiner"] else 0,
            writer_chain,
        )
    )


def _fmt_fields(fields):
    if not fields:
        return "{}"
    parts = []
    for key in ("x", "y", "vx", "vy", "target_x", "target_y",
                "tes_attached", "attach_slot", "movement_state",
                "depth_locked", "depth_lock_y", "in_contact",
                "is_tackler", "_tackle_commit_active", "frozen"):
        if key not in fields:
            continue
        value = fields[key]
        if hasattr(value, "name"):
            value = value.name
        if isinstance(value, float):
            value = round(value, 3)
        parts.append("%s=%s" % (key, value))
    return " ".join(parts)


def _write_report(text):
    _REAL_STDOUT.write(text)
    for path in ("tmp/tackle_trace.txt", "tmp/ttrace2.txt"):
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass


def main():
    _quiet()
    result = _run_capture()
    _loud()

    windows = result["windows"]
    report = []
    emit = report.append

    emit("=" * 100)
    emit("SINGLE-TACKLE FULLBACK TRACE (%s)  seed %d  %d frames"
         % (BUILD_TAG, result["seed"], result["frames"]))
    emit("=" * 100)
    emit("  windows captured     : %d" % len(windows))
    emit("  locked FB pid        : %s" % result["locked_pid"])
    emit("  handoff rows total   : %d" % result["handoff_total"])
    emit("  wrapped engine methods (%d): %s"
         % (len(result["wrapped"]), ", ".join(result["wrapped"])))

    if not windows:
        emit("  NO tackle window captured -- increase RLM_TRACE_FRAMES?")
        _write_report("\n".join(report) + "\n")
        return 0

    summaries = [_window_summary(w) for w in windows]

    chosen = DETAIL_INDEX if 0 <= DETAIL_INDEX < len(windows) else None
    if chosen is None:
        chosen = _choose_window(summaries)
    if chosen is None:
        chosen = 0
    win = windows[chosen]
    summary = summaries[chosen]

    emit("")
    emit("=" * 100)
    emit("DETAILED WINDOW [%d]  f%s..%s  FB pid=%s"
         % (chosen, summary["start"], summary["end"], summary["pid"]))
    emit("=" * 100)
    for line in _verdict(win, summary):
        emit(line)

    emit("")
    emit("-- FRAME-BY-FRAME (contact -> PTB) --")
    emit("  att=tes_attached slot=attach_slot ids=in ctx.attached_ids "
         "tl=in world.tackler_ids st=movement_state dep=depth_locked "
        "con=in_contact tk=is_tackler com=commit fz=frozen prim=primary "
         "join=joiner tag=fb_role arr=arrival mw=move_writer "
         "pu=(target Player.update consumed) | "
         "W:ordered writers this frame {fields changed}")
    rows = win["rows"]
    if len(rows) > 160:
        head = rows[:40]
        tail = rows[-120:]
        for r in head:
            emit("  " + _fmt_row(r))
        emit("  ... (%d middle drive frames elided) ..."
             % (len(rows) - len(head) - len(tail)))
        for r in tail:
            emit("  " + _fmt_row(r))
    else:
        for r in rows:
            emit("  " + _fmt_row(r))

    if win["handoff"]:
        emit("")
        emit("-- DOMINANCE->RELEASE HANDOFF --")
        for h in win["handoff"]:
            emit("  f=%-5s %-22s pid=%-4s"
                 % (h["frame"], h["name"], h["pid"]))
            emit("     before: %s" % _fmt_fields(h["before"]))
            emit("     after : %s" % _fmt_fields(h["after"]))
            emit("     engaged_after=%s return_after=%s"
                 % (h["engaged_after"], h["return_after"]))

    emit("")
    emit("-- ALL TACKLE WINDOWS (compact) --")
    for i, s in enumerate(summaries):
        emit(
            "  [%2d] f%-5s..%-5s pid=%-4s rows=%-4s cd=%-3s ptb=%-3s "
            "att_cd=%-3s tkl_cd=%-3s cdgap=%s ptbgap=%s phases=%s"
            % (
                i, s["start"], s["end"], s["pid"], s["n_rows"], s["n_drive"],
                s["n_ptb"], s["attached_cd"], s["tackler_cd"],
                _fmt_stats(s["cd_gap"]), _fmt_stats(s["ptb_gap"]),
                ">".join(p or "-" for p in s["phases"]),
            )
        )

    _write_report("\n".join(report) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
