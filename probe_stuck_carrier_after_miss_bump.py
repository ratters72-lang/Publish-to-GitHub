"""probe_stuck_carrier_after_miss_bump.py — WHY A CARRIER STOPS MOVING.

THE QUESTION
------------
``frozen`` is the only flag ``Player.update()`` obeys, and after the Stage 3.5
freeze/unfreeze ownership fix the BUMP_OFF teardown clears it — that is gated
by ``verify_bump_clearance_contract.py`` §[5] (including the
reference-divergence arm).  The live symptom persists anyway.  So either the
carrier is STILL frozen after the event, or a SECOND authority that is not
``frozen`` is holding locomotion.

This probe does not argue that question.  It drives the real engine, detects a
MISS / ARM_GRAB / GLANCING / BUMP_OFF contact involving the ball carrier, and
then dumps the carrier's COMPLETE locomotion ledger for the frames after it.

THE CANDIDATES, IN THE ORDER THEY CAN STOP A CARRIER
----------------------------------------------------
  1. ``bc.frozen`` — ``Player.update()`` hard-returns and zeroes vx/vy.  This
     is the Stage 3.5 bug that was fixed and gated.
  2. ``bc.move_speed <= 0`` on a body with a defensive ceiling — the BC has
     ``max_defensive_speed_mps == 0``, so this path does not apply to it (it
     falls back to ``max_speed_mps``).  Recorded to prove it is not in play.
  3. ``world.sub_state`` outside {OPEN_PLAY, POST_RUCK_OPEN_PLAY, PTB} —
     ``AttackController.apply_vector()`` returns on its FIRST statement, so
     the BC receives NO new ``target_x``/``target_y``/``move_speed`` that
     frame.  The engine's ``_phase`` string and ``world.sub_state`` are
     DIFFERENT fields, the freeze authority keys off the phase string, and
     the BUMP_OFF teardown gate asserts ``_phase == "OPEN_PLAY"`` — it asserts
     nothing about ``sub_state``.  This is the "different movement-authority
     path" candidate.
  4. Repeated ARM_GRAB damping — ``contact_authority`` applies
     ``bc.vx *= 0.85`` / ``bc.vy *= 0.85`` once per in-radius
     PRIMARY/SECONDARY body, every frame.  k bodies in radius give a
     geometric ``0.85**k`` decay per frame.  MISS itself mutates nothing, so a
     post-MISS stall is either this or (3).
  5. Target suppression / arrival mode — a stale ``target_x`` the carrier is
     parked on, or ``arrival_mode`` / ``reached_target`` snap-to-target
     leakage.
  6. Writer disagreement — ``engine.ball_carrier`` is not
     ``world.ball_carrier``, the reference-divergence class of defect.

THE STUCK PREDICATE (stated once, used everywhere)
--------------------------------------------------
    stuck  <=>  hypot(vx, vy) < 0.05
            AND |target_x - x| > 0.05      (a live target it is not on)
            AND frozen is False            (so it is NOT a freeze pin)

A carrier that satisfies this is UNFROZEN, HOLDS A LIVE TARGET, AND IS NOT
MOVING — precisely the symptom.  The probe reports those frames and names the
first gate above that explains them.

THE A/B CONTROLS (attribution by measurement, never by argument)
---------------------------------------------------------------
  RUN A  lever armed, no interference                     — the observation.
  RUN B  lever armed, probe-side forced sub_state          — if the stalls
         (see ``run_rollout(force_sub_state=True)``)         vanish when the
                                                             attack gate is
                                                             satisfied, the
                                                             cause is (3).
  RUN C  lever DISARMED (``RLM_TES_BUMP_CLEARANCE=0``)     — how many of the
                                                             BUMP_OFF events
                                                             the stage owns.

RUN B writes ONLY ``world.sub_state``, and only on frames the engine's own
``_phase`` already says ``OPEN_PLAY``.  No gameplay file is touched by this
probe; it is a read-only instrument plus that one frame-local A/B.

Run:  python probe_stuck_carrier_after_miss_bump.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import math
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
from world.world_state import WorldState, GameSubState, GamePhase
from world.open_play_engine import OpenPlayEngine
from kick_off.kickoff_formations import (
    apply_receiving_kickoff_formation,
    apply_kicking_team_formation,
)
from game.kickoff_engine import KickoffEngine
from ball.ball import Ball
from game.physics.collision_system import CollisionSystem
from world.catch_system import check_for_catch

from game.defence.defence_lvl_1 import contact_authority as ca

# ── THE STUCK PREDICATE (one definition, used by every section) ──────
STUCK_SPEED_MPS = 0.05
TARGET_OFFSET_M = 0.05

# Post-event ledger window.
WINDOW_FRAMES = 150
MAX_FRAMES = 6000

# The contact-authority verdict vocabulary (read, never re-derived).
VERDICT_MISS = ca.VERDICT_MISS
VERDICT_ARM_GRAB = ca.VERDICT_ARM_GRAB
VERDICT_GLANCING = ca.VERDICT_GLANCING
VERDICT_CONTACT = ca.VERDICT_CONTACT

# The engine phases a BUMP_OFF teardown exits FROM.
TACKLE_PHASES = (
    "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE",
)

# The GameSubState members ``AttackController.apply_vector`` accepts.  Mirrors
# the gate's own tuple verbatim so the probe can never disagree with it.
ATTACK_SUB_STATES = (
    GameSubState.OPEN_PLAY,
    GameSubState.POST_RUCK_OPEN_PLAY,
    GameSubState.PTB,
)


# ═════════════════════════════════════════════════════════════════════
# THE LEDGER — one row per frame, every gate that could stop the carrier
# ═════════════════════════════════════════════════════════════════════

def _name(obj):
    """``obj.name`` when present, else ``str(obj)``, else ``None``."""
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def _num(v):
    """Return *v* as float when numeric, else None (never raises)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def snapshot(frame, world, open_play, ball):
    """Capture every authority's verdict on the carrier for one frame."""
    bc = getattr(world, "ball_carrier", None)
    engine_carrier = getattr(open_play, "ball_carrier", None)
    decision = getattr(world, ca.CONTACT_AUTHORITY_DECISION_ATTR, None)

    row = {
        "frame": frame,
        "world_phase": _name(getattr(world, "phase", None)),
        "sub_name": _name(getattr(world, "sub_state", None)),
        "sub_value": getattr(getattr(world, "sub_state", None), "value", None),
        "ope_phase": getattr(open_play, "_phase", None),
        "tes_active": bool(getattr(world, "tes_active", False)),
        "ball_owner": getattr(world, "ball_owner", None),
        "ruck_x": _num(getattr(world, "ruck_x_m", None)),
        "frames_since_ptb": getattr(world, "frames_since_ptb", None),
        "seconds_since_ptb": _num(getattr(world, "seconds_since_ptb", None)),
        # ── the attack pipeline's own gate, mirrored verbatim ──
        "attack_gate_ok": getattr(world, "sub_state", None) in ATTACK_SUB_STATES,
        # ── the contact authority's published verdict for this frame ──
        "ca_outcome": getattr(getattr(decision, "outcome", None), "value", None),
        "ca_reason": _name(getattr(decision, "reason", None)),
        "ca_arm_grab": bool(getattr(decision, "arm_grab_damping", False)) if decision else None,
        "ca_glancing": getattr(decision, "glancing_displacement", None) if decision else None,
        "ca_winner": getattr(decision, "winner_id", None) if decision else None,
        "ca_in_radius": getattr(decision, "in_radius_count", None) if decision else None,
        "ca_role_holders": getattr(decision, "role_holder_count", None) if decision else None,
        "ca_channel": getattr(decision, "channel", None) if decision else None,
        # how many bodies were inside the radius carrying tackle candidacy
        # this frame — the exponent of the ARM_GRAB decay when damping fires.
        "ca_damping_bodies": _damping_body_count(decision),
    }

    # ── the engine's whole TES chamber state (BUMP_OFF teardown evidence) ──
    ctx = getattr(open_play, "tes_ctx", None)
    row["tes_ctx_present"] = ctx is not None
    row["tes_token"] = (getattr(ctx, "wrap_collision_state", None)
                        if ctx is not None else None)
    row["tes_primary_id"] = getattr(
        getattr(open_play, "primary", None), "player_id", None
    )
    row["tes_joiner_ids"] = [
        getattr(j, "player_id", None)
        for j in (getattr(open_play, "joiners", None) or [])
    ]
    row["attach_slots"] = dict(
        getattr(open_play, "_attached_player_slots", None) or {}
    )
    row["burst_s"] = _num(getattr(open_play, "_bump_carrier_burst_s", None))
    row["just_entered_release"] = bool(
        getattr(open_play, "_just_entered_release", False)
    )
    row["tackler_ids"] = sorted(
        int(x) for x in (getattr(world, "tackler_ids", None) or ())
        if isinstance(x, int)
    )

    if bc is None:
        row.update({
            "bc": None, "carrier_identity_same": None,
            "frozen": None, "freeze_timer": None, "tackle_locked": None,
            "tes_locked": None, "in_contact": None, "is_tackler": None,
            "tes_attached": None, "attach_slot": None, "depth_locked": None,
            "mmdl_moved": None, "tackle_commit_active": None,
            "arrival_mode": None, "reached_target": None,
            "attack_role": None, "anim_state": None, "movement_state": None,
            "target_source": None, "movement_writer": None,
            "vector_source": None, "max_defensive_speed_mps": None,
            "x": None, "y": None, "vx": None, "vy": None,
            "move_speed": None, "max_speed_mps": None,
            "target_x": None, "target_y": None,
        })
        row["speed"] = None
        row["target_offset"] = None
        row["stuck"] = None
        return row

    row.update({
        "bc": bc,
        "carrier_identity_same": (engine_carrier is bc),
        "frozen": bool(getattr(bc, "frozen", False)),
        "freeze_timer": _num(getattr(bc, "freeze_timer", 0.0)),
        "tackle_locked": bool(getattr(bc, "tackle_locked", False)),
        "tes_locked": bool(getattr(bc, "tes_locked", False)),
        "in_contact": bool(getattr(bc, "in_contact", False)),
        "is_tackler": bool(getattr(bc, "is_tackler", False)),
        "tes_attached": bool(getattr(bc, "tes_attached", False)),
        "attach_slot": getattr(bc, "attach_slot", None),
        "depth_locked": bool(getattr(bc, "depth_locked", False)),
        "mmdl_moved": bool(getattr(bc, "_mmdl_moved", False)),
        "tackle_commit_active": bool(getattr(bc, "_tackle_commit_active", False)),
        "arrival_mode": bool(getattr(bc, "arrival_mode", False)),
        "reached_target": bool(getattr(bc, "reached_target", False)),
        "attack_role": getattr(bc, "attack_role", None),
        "anim_state": getattr(bc, "anim_state", None),
        "movement_state": _name(getattr(bc, "movement_state", None)),
        "target_source": getattr(bc, "target_source", None),
        "movement_writer": getattr(bc, "movement_writer", None),
        "vector_source": getattr(bc, "_vector_source", None),
        "max_defensive_speed_mps": _num(getattr(bc, "max_defensive_speed_mps", 0.0)),
        "x": _num(getattr(bc, "x", None)),
        "y": _num(getattr(bc, "y", None)),
        "vx": _num(getattr(bc, "vx", None)),
        "vy": _num(getattr(bc, "vy", None)),
        "move_speed": _num(getattr(bc, "move_speed", None)),
        "max_speed_mps": _num(getattr(bc, "max_speed_mps", None)),
        "target_x": _num(getattr(bc, "target_x", None)),
        "target_y": _num(getattr(bc, "target_y", None)),
    })

    speed = math.hypot(row["vx"] or 0.0, row["vy"] or 0.0)
    offset = None
    if row["target_x"] is not None and row["x"] is not None:
        offset = abs(row["target_x"] - row["x"])
    row["speed"] = speed
    row["target_offset"] = offset
    row["stuck"] = bool(
        speed < STUCK_SPEED_MPS
        and offset is not None and offset > TARGET_OFFSET_M
        and row["frozen"] is False
    )
    return row


def _damping_body_count(decision):
    """In-radius bodies holding tackle candidacy — the ARM_GRAB exponent.

    ``_apply_arm_grab`` runs once per body that passed conjuncts 1-3 and
    returned the ARM_GRAB verdict, so the number of in-radius role holders
    the authority actually consulted is the multiplier's exponent.
    """
    if decision is None:
        return None
    count = 0
    for row in getattr(decision, "evidence", ()) or ():
        if (getattr(row, "in_radius", False)
                and getattr(row, "tackle_role", None) in ("PRIMARY", "SECONDARY")):
            count += 1
    return count


def mark_activity(records):
    """Stamp per-frame deltas that prove whether the attack pipeline wrote.

    ``AttackController.apply_vector`` stamps ``bc._vector_source`` and
    re-derives ``bc.target_x`` on every frame it runs, so a change in either
    is direct evidence it ran.  Stamped after the frame loop so the ledger
    stays a pure capture.
    """
    for i, row in enumerate(records):
        prev = records[i - 1] if i > 0 else None
        if row.get("bc") is None:
            row["attack_wrote"] = None
            row["vector_source_changed"] = None
            row["move_speed_changed"] = None
            row["x_delta"] = None
            continue
        if prev is None or prev.get("bc") is None:
            row["attack_wrote"] = None
            row["vector_source_changed"] = None
            row["move_speed_changed"] = None
            row["x_delta"] = None
            continue
        row["vector_source_changed"] = (
            row["vector_source"] != prev["vector_source"]
        )
        row["move_speed_changed"] = row["move_speed"] != prev["move_speed"]
        row["x_delta"] = (
            None if (row["x"] is None or prev["x"] is None)
            else row["x"] - prev["x"]
        )
        row["attack_wrote"] = bool(
            row["vector_source_changed"] or row["move_speed_changed"]
        )
    return records


# ═════════════════════════════════════════════════════════════════════
# THE ROLLOUT — the real engine, booted exactly like verify_fullback_continuity
# ═════════════════════════════════════════════════════════════════════

def run_rollout(lever="armed", force_sub_state=False, max_frames=MAX_FRAMES,
                seed=20260827, verbose=False):
    """Boot the engine, kick off, step it, and return the frame ledger.

    ``lever``: "armed" (env unset — the shipped default), "disarmed"
    (``RLM_TES_BUMP_CLEARANCE=0``), or a raw string value.

    ``force_sub_state``: PROBE-SIDE A/B.  Before each ``open_play.update(dt)``
    on which the engine's own ``_phase`` already reads ``OPEN_PLAY``, force
    ``world.sub_state = GameSubState.OPEN_PLAY``.  This makes the attack
    pipeline's gate (candidate 3) unconditionally satisfied and NOTHING else
    — it writes one world field the engine itself owns.  If a stall vanishes
    under this control, the stall is caused by that field disagreeing.
    """
    var = "RLM_TES_BUMP_CLEARANCE"
    prev = os.environ.get(var)
    if lever == "armed":
        os.environ.pop(var, None)
    else:
        os.environ[var] = lever

    records = []
    forced = 0
    teardowns = []
    try:
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

        # ── TEARDOWN SPY (read-only) ──
        # The BUMP_OFF token is mirrored onto the context only while the TES
        # chamber is alive, and the clearance tears the chamber down INSIDE
        # the same frame it formed, so the token is never observable in a
        # post-frame snapshot.  This wrapper records the exact teardown:
        # which variant ran, the token it acted on, the phase / sub-state it
        # ran in, and the carrier's freeze state at that instant.  It calls
        # the real method unchanged and returns its result.
        _orig_teardown = open_play._handle_broken_tackle

        def _teardown_spy(*args, **kwargs):
            _ctx = getattr(open_play, "tes_ctx", None)
            _bc = getattr(world, "ball_carrier", None)
            teardowns.append({
                "frame": int(getattr(world, "frame_count", 0) or 0),
                "bump_off": bool(kwargs.get("bump_off", False)),
                "token": (getattr(_ctx, "wrap_collision_state", None)
                          if _ctx is not None else None),
                "phase_before": getattr(open_play, "_phase", None),
                "sub_before": _name(getattr(world, "sub_state", None)),
                "sub_before_value": getattr(
                    getattr(world, "sub_state", None), "value", None
                ),
                "carrier_frozen_before": bool(getattr(_bc, "frozen", False)),
                "carrier_id": getattr(_bc, "player_id", None),
            })
            return _orig_teardown(*args, **kwargs)

        open_play._handle_broken_tackle = _teardown_spy

        dt = 1.0 / 60.0
        for frame in range(1, max_frames + 1):
            world.frame_count = frame
            ball.update(dt)

            if force_sub_state and getattr(open_play, "_phase", None) == "OPEN_PLAY":
                if getattr(world, "sub_state", None) is not GameSubState.OPEN_PLAY:
                    world.sub_state = GameSubState.OPEN_PLAY
                    forced += 1

            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
            elif world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    world.open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
            elif world.phase == GamePhase.OPEN_PLAY:
                open_play.update(dt)
                if not world.tes_active:
                    collision_system.resolve_all(world.players, world=world)
            else:
                continue

            row = snapshot(frame, world, open_play, ball)
            row["ca_decision"] = getattr(
                world, ca.CONTACT_AUTHORITY_DECISION_ATTR, None
            )
            records.append(row)

            if verbose and frame % 500 == 0:
                print("    ... frame %d (carrier=%s)"
                      % (frame, getattr(world.ball_carrier, "player_id", None)))
    finally:
        if prev is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = prev

    mark_activity(records)
    return {
        "records": records,
        "forced_sub_state_frames": forced,
        "teardowns": teardowns,
    }
# ═════════════════════════════════════════════════════════════════════
# EVENT DETECTION — read-only, from the published boundary
# ═════════════════════════════════════════════════════════════════════

REASON_MISS = "SCORE_MISS"
REASON_ARM_GRAB = "SCORE_ARM_GRAB"
REASON_GLANCING = "SCORE_GLANCING"
REASON_FORMED = "FORMED"

# Gate labels, in the order the candidates were declared.
GATE_ORDER = (
    "1_FROZEN",
    "2_MOVE_SPEED_ZERO",
    "3_SUB_STATE_GATE",
    "4_ARM_GRAB",
    "5_ARRIVAL_MODE",
    "6_IDENTITY",
    "UNEXPLAINED",
)


def explain_stuck(row):
    """Name the FIRST declared authority that explains this stuck frame.

    Applied only to frames matching the stuck predicate (unfrozen + live
    target + not moving), so ``1_FROZEN`` is unreachable here by
    construction — it exists in the ordering so the label set is total and
    the same function can be read against a frozen-stationary frame.
    """
    if row.get("frozen") is True:
        return "1_FROZEN"
    if (row.get("max_defensive_speed_mps") or 0.0) > 0.0:
        if row.get("move_speed") is not None and row["move_speed"] <= 0.0:
            return "2_MOVE_SPEED_ZERO"
    if row.get("attack_gate_ok") is False:
        return "3_SUB_STATE_GATE"
    if row.get("ca_arm_grab"):
        return "4_ARM_GRAB"
    if row.get("arrival_mode") or row.get("reached_target"):
        return "5_ARRIVAL_MODE"
    if row.get("carrier_identity_same") is False:
        return "6_IDENTITY"
    return "UNEXPLAINED"


def find_events(teardowns):
    """The declared contact + teardown events, from the spies' records."""
    events = []
    for t in teardowns:
        kind = "BUMP_OFF_TEARDOWN" if t["bump_off"] else "PLAIN_TEARDOWN"
        events.append({
            "frame": t["frame"], "kind": kind,
            "detail": "token=%s phase_before=%s sub_before=%s "
                      "carrier_frozen_before=%s"
                      % (t["token"], t["phase_before"], t["sub_before"],
                         t["carrier_frozen_before"]),
        })
    return events


def verdict_events(records):
    """The contact-authority verdicts, counted per frame."""
    counts = {REASON_MISS: 0, REASON_ARM_GRAB: 0, REASON_GLANCING: 0,
              REASON_FORMED: 0}
    frames = {k: [] for k in counts}
    for row in records:
        reason = row.get("ca_reason")
        if reason in counts:
            counts[reason] += 1
            frames[reason].append(row["frame"])
    return counts, frames


def stuck_runs(records):
    """Consecutive stuck-frame runs, as (start_frame, end_frame, length)."""
    runs = []
    start = None
    prev_frame = None
    for row in records:
        if row.get("bc") is None:
            if start is not None:
                runs.append((start, prev_frame, 0))
                start = None
            continue
        if row.get("stuck"):
            if start is None:
                start = row["frame"]
            prev_frame = row["frame"]
        else:
            if start is not None:
                runs.append((start, prev_frame, 0))
                start = None
    if start is not None:
        runs.append((start, prev_frame, 0))
    # annotate lengths
    out = []
    for a, b, _ in runs:
        if a is None or b is None:
            continue
        out.append((a, b, b - a + 1))
    return out


def gate_histogram(records, key="stuck"):
    """Histogram of the explaining gate over every matching frame."""
    hist = {g: 0 for g in GATE_ORDER}
    examples = {g: None for g in GATE_ORDER}
    for row in records:
        if row.get("bc") is None:
            continue
        if not row.get(key):
            continue
        g = explain_stuck(row)
        hist[g] = hist.get(g, 0) + 1
        if examples.get(g) is None:
            examples[g] = row["frame"]
    return hist, examples


# ═════════════════════════════════════════════════════════════════════
# REPORTING
# ═════════════════════════════════════════════════════════════════════

def _fs(v, width=8, prec=3):
    """Format a possibly-None float."""
    if v is None:
        return "None".rjust(width)
    return ("%.*f" % (prec, v)).rjust(width)


# ── THE OUTPUT SINK ──
# The engine prints per-frame diagnostics on stdout (its freeze audit alone
# emits a report every few frames), so a captured shell log is megabytes of
# noise wrapped around the probe's own report.  The report is therefore
# written to its OWN file, and stdout is redirected to a null sink for the
# duration of every rollout, so only this probe's report ever lands in the
# artefact.
REPORT_PATH = "tmp_stuck_carrier_report.txt"
_REPORT_HANDLE = None
_REAL_STDOUT = sys.stdout


def open_report():
    """Open the probe's own report file and return its handle."""
    global _REPORT_HANDLE
    _REPORT_HANDLE = open(REPORT_PATH, "w", encoding="utf-8")
    return _REPORT_HANDLE


def silence_stdout():
    """Send stdout to a null sink (the engine's per-frame chatter)."""
    sys.stdout = open(os.devnull, "w", encoding="utf-8")


def speak_stdout():
    """Send stdout back to the probe's report file."""
    if _REPORT_HANDLE is not None:
        sys.stdout = _REPORT_HANDLE


def close_report():
    """Flush and close the probe's report file, restoring stdout."""
    global _REPORT_HANDLE
    if _REPORT_HANDLE is not None:
        _REPORT_HANDLE.flush()
        _REPORT_HANDLE.close()
        _REPORT_HANDLE = None
    sys.stdout = _REAL_STDOUT


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


def print_ledger_line(row):
    print("  f=%-5s ope=%-16s sub=%-22s wph=%-10s frozen=%-5s vx=%s spd=%s "
          "tx=%s x=%s off=%s | gate_ok=%-5s attack_wrote=%-5s src=%-9s "
          "ca=%-16s"
          % (
              row["frame"], row["ope_phase"], str(row["sub_name"]),
              str(row["world_phase"]), str(row["frozen"]),
              _fs(row["vx"]), _fs(row["speed"]), _fs(row["target_x"]),
              _fs(row["x"]), _fs(row["target_offset"], 7),
              str(row["attack_gate_ok"]), str(row["attack_wrote"]),
              str(row["vector_source"]), str(row["ca_reason"]),
          ))


def print_full_ledger_for(row):
    print("      FULL LEDGER (frame %s)" % row["frame"])
    print("        engine._phase           = %s" % row["ope_phase"])
    print("        world.phase             = %s" % row["world_phase"])
    print("        world.sub_state         = %s (value=%s)"
          % (row["sub_name"], row["sub_value"]))
    print("        attack_gate_ok          = %s   <-- AttackController's own gate"
          % row["attack_gate_ok"])
    print("        world.tes_active        = %s" % row["tes_active"])
    print("        world.ball_owner        = %s" % row["ball_owner"])
    print("        ruck_x / frames_since_ptb = %s / %s"
          % (_fs(row["ruck_x"], 8, 2), row["frames_since_ptb"]))
    print("        tes_ctx_present/token   = %s / %s"
          % (row["tes_ctx_present"], row["tes_token"]))
    print("        tackler_ids             = %s" % (row["tackler_ids"],))
    print("        attach_slots            = %s" % (row["attach_slots"],))
    print("        burst_s                 = %s" % _fs(row["burst_s"], 7, 3))
    print("        --- carrier ---")
    print("        frozen                  = %s      <-- THE QUESTION"
          % row["frozen"])
    print("        freeze_timer            = %s" % _fs(row["freeze_timer"]))
    print("        vx, vy                  = %s, %s"
          % (_fs(row["vx"]), _fs(row["vy"])))
    print("        speed                   = %s" % _fs(row["speed"]))
    print("        move_speed              = %s" % _fs(row["move_speed"]))
    print("        max_speed_mps           = %s" % _fs(row["max_speed_mps"]))
    print("        max_defensive_speed_mps = %s (0 => not a defender)"
          % _fs(row["max_defensive_speed_mps"]))
    print("        x, y                    = %s, %s"
          % (_fs(row["x"]), _fs(row["y"])))
    print("        target_x, target_y      = %s, %s"
          % (_fs(row["target_x"]), _fs(row["target_y"])))
    print("        |target_x - x|          = %s" % _fs(row["target_offset"]))
    print("        movement_state          = %s" % row["movement_state"])
    print("        target_source           = %s" % row["target_source"])
    print("        movement_writer         = %s" % row["movement_writer"])
    print("        _vector_source          = %s  (stamped only by apply_vector)"
          % row["vector_source"])
    print("        arrival_mode / reached  = %s / %s"
          % (row["arrival_mode"], row["reached_target"]))
    print("        _mmdl_moved             = %s" % row["mmdl_moved"])
    print("        _tackle_commit_active   = %s" % row["tackle_commit_active"])
    print("        tackle_locked           = %s" % row["tackle_locked"])
    print("        tes_locked / in_contact = %s / %s"
          % (row["tes_locked"], row["in_contact"]))
    print("        tes_attached / slot     = %s / %s"
          % (row["tes_attached"], row["attach_slot"]))
    print("        is_tackler / role       = %s / %s"
          % (row["is_tackler"], row["attack_role"]))
    print("        anim_state              = %s" % row["anim_state"])
    print("        identity engine==world  = %s" % row["carrier_identity_same"])
    print("        stuck                   = %s -> %s"
          % (row["stuck"], explain_stuck(row)))


def print_window(records, idx, start_frame, end_frame, label,
                 full_for_stuck=4, max_lines=70):
    """Print the ledger window [start_frame, end_frame] by frame number."""
    print("")
    print("  ── %s  (frames %s..%s) ──" % (label, start_frame, end_frame))
    printed = 0
    full_printed = 0
    for f in range(start_frame, end_frame + 1):
        pos = idx.get(f)
        if pos is None:
            continue
        row = records[pos]
        if row.get("bc") is None:
            continue
        print_ledger_line(row)
        printed += 1
        if row.get("stuck") and full_printed < full_for_stuck:
            print_full_ledger_for(row)
            full_printed += 1
        if printed >= max_lines:
            print("  ... (window truncated at %d lines)" % max_lines)
            break


def summarise(res):
    records = res["records"]
    counts, _frames = verdict_events(records)
    hist, examples = gate_histogram(records, key="stuck")
    frozen_hist, frozen_examples = gate_histogram(records, key=None) \
        if False else ({}, {})
    runs = stuck_runs(records)
    carrier_frames = sum(1 for r in records if r.get("bc") is not None)
    stuck_frames = sum(1 for r in records if r.get("stuck"))
    frozen_frames = sum(
        1 for r in records
        if r.get("frozen") is True
    )
    frozen_stationary = sum(
        1 for r in records
        if r.get("frozen") is True and r.get("bc") is not None
        and (r.get("speed") or 0.0) < STUCK_SPEED_MPS
    )
    gate_closed = sum(
        1 for r in records if r.get("attack_gate_ok") is False
    )
    identity_bad = sum(
        1 for r in records if r.get("carrier_identity_same") is False
    )
    return {
        "frames": len(records),
        "carrier_frames": carrier_frames,
        "verdicts": counts,
        "teardowns": res["teardowns"],
        "stuck_frames": stuck_frames,
        "frozen_frames": frozen_frames,
        "frozen_stationary": frozen_stationary,
        "gate_closed_frames": gate_closed,
        "identity_bad_frames": identity_bad,
        "runs": runs,
        "hist": hist,
        "examples": examples,
        "forced": res["forced_sub_state_frames"],
    }


def report(title, res, detail=True):
    s = summarise(res)
    records = res["records"]
    idx = {r["frame"]: i for i, r in enumerate(records)}

    banner(title)
    print("  frames stepped              = %d" % s["frames"])
    print("  frames with a ball carrier  = %d" % s["carrier_frames"])
    print("  forced sub_state writes     = %d" % s["forced"])

    sub("CONTACT VERDICTS (contact_authority's published decision, per frame)")
    for k in (REASON_MISS, REASON_ARM_GRAB, REASON_GLANCING, REASON_FORMED):
        print("  %-20s = %d" % (k, s["verdicts"][k]))

    sub("TEARDOWNS (the engine's own _handle_broken_tackle, via the spy)")
    if not s["teardowns"]:
        print("  none")
    for t in s["teardowns"]:
        print("  frame=%-5s variant=%-10s token=%-16s phase_before=%-16s "
              "sub_before=%-22s carrier_frozen_before=%s"
              % (t["frame"], "BUMP_OFF" if t["bump_off"] else "plain",
                 t["token"], t["phase_before"], t["sub_before"],
                 t["carrier_frozen_before"]))

    sub("THE CARRIER'S STALL — measured, not argued")
    print("  STUCK frames (unfrozen + live target + not moving) = %d"
          % s["stuck_frames"])
    print("  FROZEN frames                                      = %d"
          % s["frozen_frames"])
    print("  FROZEN and stationary (the Stage 3.5 class)        = %d"
          % s["frozen_stationary"])
    print("  frames where the attack gate was CLOSED            = %d"
          % s["gate_closed_frames"])
    print("  frames where engine.ball_carrier is not world's    = %d"
          % s["identity_bad_frames"])
    print("  consecutive stuck runs                             = %d %s"
          % (len(s["runs"]), s["runs"][:20]))

    sub("GATE ATTRIBUTION over the STUCK frames (first gate that explains it)")
    for g in GATE_ORDER:
        n = s["hist"].get(g, 0)
        if n:
            print("  %-20s = %-6d first at frame %s"
                  % (g, n, s["examples"].get(g)))

    if not detail:
        return s

    # ── Detail: the first MISS, the first BUMP_OFF teardown, the worst stall
    bumps = [t for t in s["teardowns"] if t["bump_off"]]
    plains = [t for t in s["teardowns"] if not t["bump_off"]]
    miss_frames = [r["frame"] for r in records if r.get("ca_reason") == REASON_MISS]
    grab_frames = [r["frame"] for r in records
                   if r.get("ca_reason") == REASON_ARM_GRAB]
    glance_frames = [r["frame"] for r in records
                     if r.get("ca_reason") == REASON_GLANCING]

    if miss_frames:
        f0 = miss_frames[0]
        print_window(records, idx, max(1, f0 - 2), f0 + WINDOW_FRAMES,
                     "AFTER THE FIRST MISS (frame %d)" % f0)
    if grab_frames:
        f0 = grab_frames[0]
        print_window(records, idx, max(1, f0 - 2), min(f0 + 40, f0 + WINDOW_FRAMES),
                     "AFTER THE FIRST ARM_GRAB (frame %d)" % f0, full_for_stuck=2)
    if glance_frames:
        f0 = glance_frames[0]
        print_window(records, idx, max(1, f0 - 2), f0 + 40,
                     "AFTER THE FIRST GLANCING (frame %d)" % f0, full_for_stuck=2)
    if bumps:
        f0 = bumps[0]["frame"]
        print_window(records, idx, max(1, f0 - 3), f0 + WINDOW_FRAMES,
                     "AFTER THE FIRST BUMP_OFF TEARDOWN (frame %d)" % f0)
    if plains:
        f0 = plains[0]["frame"]
        print_window(records, idx, max(1, f0 - 3), f0 + 60,
                     "AFTER THE FIRST PLAIN (break-tackle) TEARDOWN (frame %d)" % f0,
                     full_for_stuck=2)
    if s["runs"]:
        worst = max(s["runs"], key=lambda r: r[2])
        print_window(records, idx, max(1, worst[0] - 8), worst[1] + 12,
                     "THE WORST STALL (frames %d..%d, len %d)"
                     % (worst[0], worst[1], worst[2]), full_for_stuck=6)
    return s


# ═════════════════════════════════════════════════════════════════════
# THE ARM_GRAB DECAY — is the carrier's velocity being multiplied away?
# ═════════════════════════════════════════════════════════════════════

def arm_grab_decay_table(records, limit=25):
    """Print the carrier's vx ratio on ARM_GRAB frames against 0.85**k."""
    rows = [r for r in records if r.get("ca_reason") == REASON_ARM_GRAB]
    if not rows:
        print("  no ARM_GRAB frame in this run")
        return
    idx = {r["frame"]: i for i, r in enumerate(records)}
    print("  %-6s %-10s %-10s %-9s %-7s %-9s %s"
          % ("frame", "vx", "prev_vx", "ratio", "k", "0.85**k", "reading"))
    shown = 0
    for r in rows:
        i = idx[r["frame"]]
        if i == 0:
            continue
        prev = records[i - 1]
        if prev.get("bc") is None or r.get("vx") is None or prev.get("vx") is None:
            continue
        if abs(prev["vx"]) < 1e-9:
            continue
        ratio = r["vx"] / prev["vx"]
        k = r.get("ca_damping_bodies")
        predicted = (0.85 ** k) if isinstance(k, int) else None
        reading = ""
        if predicted is not None and abs(ratio - predicted) < 0.02:
            reading = "MATCHES the damping law"
        elif ratio < 0.95:
            reading = "decayed but NOT by the damping law alone"
        else:
            reading = "not damped this frame"
        print("  %-6s %s %s %s %-7s %-9s %s"
              % (r["frame"], _fs(r["vx"]), _fs(prev["vx"]),
                 _fs(ratio, 8, 4), str(k),
                 ("%.4f" % predicted) if predicted is not None else "None",
                 reading))
        shown += 1
        if shown >= limit:
            print("  ... (truncated)")
            break


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    open_report()
    speak_stdout()

    banner("STUCK CARRIER AFTER MISS / BUMP_OFF — the locomotion ledger")
    print("  stuck predicate : speed < %.2f m/s AND |target_x - x| > %.2f m "
          "AND frozen is False" % (STUCK_SPEED_MPS, TARGET_OFFSET_M))
    print("  attack gate     : world.sub_state in %s"
          % ([s.name for s in ATTACK_SUB_STATES],))
    print("  seed            : 20260827   frames: %d" % MAX_FRAMES)

    silence_stdout()
    run_a = run_rollout("armed")
    speak_stdout()
    s_a = report("RUN A — lever ARMED (shipped default), no interference",
                 run_a)
    sub("RUN A — ARM_GRAB DECAY on the carrier")
    arm_grab_decay_table(run_a["records"])

    silence_stdout()
    run_b = run_rollout("armed", force_sub_state=True)
    speak_stdout()
    s_b = report("RUN B — lever ARMED + PROBE-FORCED sub_state = OPEN_PLAY",
                 run_b, detail=False)

    silence_stdout()
    run_c = run_rollout("disarmed")
    speak_stdout()
    s_c = report("RUN C — lever DISARMED (RLM_TES_BUMP_CLEARANCE=0)",
                 run_c, detail=False)

    # ── THE VERDICT, from the three runs ──
    banner("VERDICT")
    print("  [1] Is the carrier FROZEN on a stall?")
    print("      RUN A: frozen frames = %d; frozen-and-stationary = %d; "
          "STUCK (unfrozen) frames = %d"
          % (s_a["frozen_frames"], s_a["frozen_stationary"],
             s_a["stuck_frames"]))
    if s_a["frozen_stationary"] == 0:
        print("      -> NO frozen-and-stationary carrier frame exists.  The "
              "freeze flag is NOT what stops it.")
    else:
        print("      -> %d frames ARE frozen and stationary: the freeze pin "
              "is still live." % s_a["frozen_stationary"])

    print("")
    print("  [2] Is a DIFFERENT authority holding locomotion?")
    print("      RUN A stuck frames = %d, gate attribution = %s"
          % (s_a["stuck_frames"],
             {g: n for g, n in s_a["hist"].items() if n}))
    print("      RUN A frames with the attack gate CLOSED = %d"
          % s_a["gate_closed_frames"])
    print("      RUN B (forced sub_state) stuck frames    = %d, gate = %s"
          % (s_b["stuck_frames"],
             {g: n for g, n in s_b["hist"].items() if n}))
    if s_a["stuck_frames"] and s_b["stuck_frames"] == 0:
        print("      -> A/B DECISIVE: closing the attack gate's own condition "
              "removes every stall.  The cause is the sub_state gate.")
    elif s_b["stuck_frames"] < s_a["stuck_frames"]:
        print("      -> A/B PARTIAL: forcing the gate removes %d of %d stuck "
              "frames; the remainder has another cause."
              % (s_a["stuck_frames"] - s_b["stuck_frames"],
                 s_a["stuck_frames"]))
    elif s_a["stuck_frames"] == 0 and s_b["stuck_frames"] == 0:
        print("      -> neither run produced a stuck carrier under this "
              "stuck predicate.")
    else:
        print("      -> A/B NEGATIVE: forcing the gate changed nothing, so "
              "the stall is NOT the sub_state gate.")

    print("")
    print("  [3] Does the bump clearance own any of it?")
    print("      RUN A teardowns: %d (%d bump-off, %d plain)"
          % (len(s_a["teardowns"]),
             sum(1 for t in s_a["teardowns"] if t["bump_off"]),
             sum(1 for t in s_a["teardowns"] if not t["bump_off"])))
    print("      RUN C (disarmed) teardowns: %d (%d bump-off, %d plain)"
          % (len(s_c["teardowns"]),
             sum(1 for t in s_c["teardowns"] if t["bump_off"]),
             sum(1 for t in s_c["teardowns"] if not t["bump_off"])))
    print("      RUN C stuck frames = %d, gate = %s"
          % (s_c["stuck_frames"],
             {g: n for g, n in s_c["hist"].items() if n}))

    print("")
    print("  [4] Writer-disagreement check (engine.ball_carrier != world's)")
    print("      RUN A frames where they disagree = %d (and %d runner frames "
          "in RUN B, %d in RUN C)"
          % (s_a["identity_bad_frames"], s_b["identity_bad_frames"],
             s_c["identity_bad_frames"]))

    print("")
    print("  Evidence collected.  Nothing was assumed.")
    print("  Report written to %s" % REPORT_PATH)
    close_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
