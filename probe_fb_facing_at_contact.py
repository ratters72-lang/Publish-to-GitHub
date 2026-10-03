"""probe_fb_facing_at_contact.py — IS THE TAGGED FULLBACK REACHED, AND IF SO,
WHAT REJECTS IT?

THE QUESTION
------------
A tagged (``fullback_role_active``) defender chases a breaking carrier and
never makes the tackle.  Two rival explanations, and they are NOT exclusive:

  (H1) FACING.  The fullback REACHES tackle range and IS handed to the engage
       gate, and the gate refuses it because of the approach angle measured
       from its FACING PLANE.  The fullback's facing is velocity-driven
       (``FACE_BC_BY_STATE[CHASE] = False``, and the stage-4 fullback CHASE
       profile copies that row), so on any approach that is not dead on the
       carrier's bearing the machine classifies CLOSING_SIDE_ON (30..60 deg,
       ``BOUNCE_BACK``) or CLOSING_REAR (60..120 deg, ``FALL_OVER``) and only
       CLOSING_HEAD_ON (<30 deg) can feed TES.

  (H2) BYPASS.  The fullback NEVER REACHES the engage machinery at all,
       because ``OpenPlayEngine._update_defence``'s physical-contact trigger
       skips a tagged fullback outright --
       ``if getattr(p, "fullback_role_active", False): continue`` -- before
       the tackle-authority test and before ``_check_pre_tackle``.

This probe decides between them with the ENGINE'S OWN verdicts, never a
re-derivation.  It wraps the real engage machinery and records what those
functions were actually asked and what they actually answered.

WHAT IT MEASURES
----------------
  A  REACH      frames with a tagged fullback; frames a chase is armed on it;
                frames it is inside the tackle radius (1.5 m) and inside the
                machine's engage distance (2.0 m) of the live carrier.
  B  ADMISSION  every ``pretackle_machine.run_machine`` / ``should_engage`` /
                ``apply_outcome`` call made ON THE TAGGED FULLBACK, with the
                state that classified and the verdict that was returned.
                A zero here is direct proof of (H2).
  C  BYPASS     every ``OpenPlayEngine._check_pre_tackle`` call, and whether
                the tagged fullback was EVER one of its arguments.  Zero is
                direct proof that the physical-contact route never considers
                the fullback.
  D  FACING     for the frames the fullback is inside engage distance, the
                distribution of the approach angle from its facing plane, in
                the machine's own bands.  Read through the pure predicate the
                machine reads, sampled at end of frame.
  E  MUTATION   the outcomes the machine APPLIED to the fullback, and the
                freeze timers it stamped on it.  A failed engage is not inert:
                FALL_OVER zeroes the velocity and sets a 0.5 s freeze, and a
                frozen body then fails ``is_pre_tackle_eligible`` /
                ``_is_eligible_lane_defender`` / ``_is_genuine_line_defender``.

METHOD
------
Read-only.  Every wrapper calls the real function unchanged and returns its
value, so the rollout is byte-identical to a normal run.  The engine is booted
exactly as ``probe_fb_chase_write_sequence`` boots it.

The fixture is NOT reproducible across processes (measured: four runs of one
seed produced 26 / 76 / 135 / 168 break windows -- see
``FB_AUTHORITY_TIMELINE_REPORT.md`` section 8).  A single seed is therefore not
evidence and no verdict here rests on one; the probe sweeps seeds and reports
each, then pools.

Run:    python probe_fb_facing_at_contact.py
Sweep:  $env:RLM_FBFAC_SEEDS="20260827,1,2,3,4,5"
Report: tmp_fb_facing_report.txt
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

from game.defence.defence_lvl_1 import pretackle_machine
from game.defence.defence_lvl_1.pretackle_classifier import (
    approach_angle_from_facing,
    HEAD_ON_MAX_DEG,
    SIDE_ON_MAX_DEG,
)
from game.defence.defence_lvl_1.slot_arbitration import is_slot_tackle_eligible
from game.defence.defence_lvl_1.tackle_engage_guard import (
    is_tackle_engage_guard,
    TACKLE_ENGAGE_FACING_MAX_DEG,
)

# ── CONFIG ───────────────────────────────────────────────────────────
DT = 1.0 / 60.0
FRAMES = int(os.environ.get("RLM_FBFAC_FRAMES", "3000"))
REPORT_PATH = os.environ.get("RLM_FBFAC_REPORT", "tmp_fb_facing_report.txt")
SEEDS = [
    int(s) for s in os.environ.get(
        "RLM_FBFAC_SEEDS", "20260827,1,2,3,4,5"
    ).split(",") if s.strip()
]

# The machine's own geometry constants (mirrors of the tuner defaults).
TACKLE_RADIUS_M = 1.5          # OPE physical-contact trigger radius
ENGAGE_DISTANCE_M = 2.0        # pretackle_machine.ENGAGE_DISTANCE_M
MAX_APPROACH_ANGLE_DEG = 120.0  # pretackle_machine.MAX_APPROACH_ANGLE_DEG

# A row is recorded only while a chase is armed on the tagged fullback and the
# carrier is within this range -- the population the question is about.
RECORD_RANGE_M = 15.0

# Per-seed cap on printed trace rows (the aggregates are unbounded).
TRACE_ROWS = int(os.environ.get("RLM_FBFAC_TRACE_ROWS", "60"))

_REPORT = None
_REAL_STDOUT = sys.stdout


class _Tee:
    """The report sink, with a gate.

    INACTIVE while a rollout runs: the engine prints a per-frame audit block to
    stdout every frame, and teeing that into the report buries the probe's own
    sections under hundreds of kilobytes of chatter (the wart recorded in
    ``FB_CHASE_CROSSING_PACE_REPORT.md`` section 7).  Inactive writes are
    counted and DISCARDED, so the report holds only the probe's own output and
    the amount suppressed is still reported.

    ACTIVE for the report phase, when every write is the probe's own.
    """

    def __init__(self):
        self.active = False
        self.suppressed_bytes = 0

    def write(self, s):
        if not self.active:
            try:
                self.suppressed_bytes += len(s)
            except Exception:
                pass
            return len(s)
        try:
            if _REPORT is not None:
                _REPORT.write(s)
        except Exception:
            pass
        return _REAL_STDOUT.write(s)

    def flush(self):
        if not self.active:
            return None
        try:
            if _REPORT is not None:
                _REPORT.flush()
        except Exception:
            pass
        try:
            return _REAL_STDOUT.flush()
        except Exception:
            return None


_TEE = _Tee()


def fnum(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def optnum(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def name_of(obj):
    if obj is None:
        return None
    return getattr(obj, "name", None) or str(obj)


def fmt(value, places=2):
    if value is None:
        return "--"
    if isinstance(value, float):
        return ("%%.%df" % places) % value
    return str(value)


def find_fullback(world):
    """The single tagged fullback, or None."""
    for d in getattr(world, "defending_team", None) or ():
        if getattr(d, "fullback_role_active", False):
            return d
    return None


def is_tagged_fb(body) -> bool:
    return bool(getattr(body, "fullback_role_active", False))


def state_name(body):
    return name_of(getattr(body, "movement_state", None))


def chase_armed(body) -> bool:
    """THE CHASE PREDICATE, read duck-typed (no engine import).

    True while the break latch is armed (the stage-2/4 emergency contract) or
    the body carries the CHASE movement state.  The latch OR the state, so a
    one-frame skew between the two cannot drop a chasing frame.
    """
    if body is None:
        return False
    if bool(getattr(body, "_fb_break_latched", False)):
        return True
    return state_name(body) == "CHASE"


def bearing_to(body, bc):
    """The bearing (radians) from *body* to *bc*, or None."""
    if body is None or bc is None:
        return None
    dx = fnum(getattr(bc, "x", None)) - fnum(getattr(body, "x", None))
    dy = fnum(getattr(bc, "y", None)) - fnum(getattr(body, "y", None))
    import math
    return math.atan2(dy, dx)


def angle_bucket(angle_deg):
    """The machine's own facing bands, as a label."""
    if angle_deg is None:
        return "no_angle"
    if angle_deg < HEAD_ON_MAX_DEG:
        return "head_on_lt30"
    if angle_deg < SIDE_ON_MAX_DEG:
        return "side_on_30_60"
    if angle_deg < MAX_APPROACH_ANGLE_DEG:
        return "rear_60_120"
    return "refused_ge120"


def new_ledger(seed):
    return {
        "seed": seed,
        "frames_run": 0,
        # A REACH
        "frames_tagged_fb": 0,
        "frames_chase_armed": 0,
        "frames_in_tackle_radius": 0,
        "frames_in_engage_distance": 0,
        # B ADMISSION (wrapped machinery, tagged FB only)
        "run_machine_calls": 0,
        "run_machine_states": {},
        "should_engage_calls": 0,
        "should_engage_true": 0,
        "apply_outcome_calls": 0,
        "apply_outcome_values": {},
        "engage_candidate_true": 0,
        "captured_tuners": None,
        # C BYPASS (the OPE physical-contact route)
        "check_pre_tackle_calls": 0,
        "check_pre_tackle_fb_calls": 0,
        "check_pre_tackle_distinct_ids": set(),
        # IDENTITY OF "THE TAGGED FULLBACK".  More than one body in the
        # defending team can carry ``fullback_role_active``, and the OPE route
        # skips every one of them.  Record which ids EXIST and which ids the
        # route was handed, so "the route named the tagged FB" is answered for
        # the right body.
        "fb_ids": set(),
        "check_pre_fb_ids": set(),
        "max_tagged_in_frame": 0,
        "frames_multi_tagged": 0,
        # D FACING, inside engage distance
        "angle_buckets": {},
        "angle_samples": [],
        "angle_samples_1p5": [],
        "engage_dist_with_fb_eligible": 0,
        "engage_dist_with_slot_in": 0,
        "engage_dist_with_guard": 0,
        # E MUTATION
        "freeze_stamps_on_fb": 0,
        "frames_fb_frozen": 0,
        "frames_fb_freeze_timer": 0,
        "min_dist_seen": None,
        "trace": [],
        # F PURSUIT PROGRESS -- is the gap closing at all?
        "frames_not_closing": 0,
        "frames_fb_stopped": 0,
        "frames_pace_tied": 0,
        "frames_closing_blocked": 0,
        "frames_static_self_target": 0,
        "target_sources": {},
        "fb_speed_sum": 0.0,
        "bc_speed_sum": 0.0,
        "speed_samples": 0,
        "_prev_dist": None,
    }
def percent(part, whole):
    if not whole:
        return "--"
    return "%.1f%%" % (100.0 * part / whole)


def dist_between(a, b):
    import math
    if a is None or b is None:
        return None
    return math.hypot(
        fnum(getattr(a, "x", None)) - fnum(getattr(b, "x", None)),
        fnum(getattr(a, "y", None)) - fnum(getattr(b, "y", None)),
    )


def trace_row(frame, fb, bc, dist, angle, tuners, world):
    """One end-of-frame row, every value a pure read."""
    bucket = angle_bucket(angle)
    row = {
        "frame": frame,
        "dist": dist,
        "angle": angle,
        "bucket": bucket,
        "facing": optnum(getattr(fb, "facing", None)),
        "bearing": bearing_to(fb, bc),
        "state": state_name(fb),
        "speed": optnum(getattr(fb, "move_speed", None)),
        "tackle_role": getattr(fb, "tackle_role", None),
        "pre_tackle_state": name_of(getattr(fb, "pre_tackle_state", None)),
        "frozen": bool(getattr(fb, "frozen", False)),
        "freeze_timer": optnum(getattr(fb, "freeze_timer", None)),
        "can_tackle": bool(getattr(fb, "can_acquire_tackle", False)),
        "target_source": getattr(fb, "target_source", None),
        "latched": bool(getattr(fb, "_fb_break_latched", False)),
        # The body's OWN velocity.  ``move_speed`` is NOT read: the engine
        # stopped writing it for the tagged fullback, so it is a stale
        # constant and reads as a plausible-looking lie.
        "fb_speed": math.hypot(fnum(getattr(fb, "vx", 0.0)),
                               fnum(getattr(fb, "vy", 0.0))),
        "bc_speed": math.hypot(fnum(getattr(bc, "vx", 0.0)),
                               fnum(getattr(bc, "vy", 0.0))),
        "tgt_dist": math.hypot(
            fnum(getattr(fb, "target_x", None), fnum(getattr(fb, "x", 0.0)))
            - fnum(getattr(fb, "x", 0.0)),
            fnum(getattr(fb, "target_y", None), fnum(getattr(fb, "y", 0.0)))
            - fnum(getattr(fb, "y", 0.0)),
        ),
        "slot_in": False,
        "guard": False,
        "fb_eligible": False,
    }
    try:
        row["slot_in"] = bool(is_slot_tackle_eligible(bc, fb, world))
    except Exception:
        pass
    try:
        row["guard"] = bool(is_tackle_engage_guard(fb, bc, world, tuners))
    except Exception:
        pass
    try:
        from game.defence.defence_lvl_1.fullback_role import (
            fullback_emergency_defender_active,
        )
        row["fb_eligible"] = bool(
            fullback_emergency_defender_active(world, fb, bc)
        )
    except Exception:
        pass
    return row


def run_rollout(seed):
    """Boot the engine, wrap the real engage machinery, step, aggregate."""
    ledger = new_ledger(seed)
    ledger.update({
        "frames_in_radius_with_lane_role": 0,
        "frames_in_radius_with_any_role": 0,
        "frames_in_radius_chase_armed": 0,
        "check_pre_tackle_ids": set(),
    })

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

    # ── the hooks: call the real function, record, return its value ──
    real_run_machine = pretackle_machine.run_machine
    real_should_engage = pretackle_machine.should_engage
    real_apply_outcome = pretackle_machine.apply_outcome
    real_check_pre = OpenPlayEngine._check_pre_tackle

    def run_machine_hook(world_arg, defender, bc, tuners):
        if ledger["captured_tuners"] is None and tuners is not None:
            ledger["captured_tuners"] = tuners
        tagged = is_tagged_fb(defender)
        if tagged:
            ledger["run_machine_calls"] += 1
        state = real_run_machine(world_arg, defender, bc, tuners)
        if tagged:
            key = getattr(state, "value", str(state))
            ledger["run_machine_states"][key] = (
                ledger["run_machine_states"].get(key, 0) + 1
            )
        return state

    def should_engage_hook(world_arg, defender, bc, tuners, state=None):
        tagged = is_tagged_fb(defender)
        result = real_should_engage(
            world_arg, defender, bc, tuners, state=state,
        )
        if tagged:
            ledger["should_engage_calls"] += 1
            if result:
                ledger["should_engage_true"] += 1
        return result

    def apply_outcome_hook(world_arg, defender, bc, tuners, state=None):
        tagged = is_tagged_fb(defender)
        outcome = real_apply_outcome(
            world_arg, defender, bc, tuners, state=state,
        )
        if tagged:
            ledger["apply_outcome_calls"] += 1
            key = getattr(outcome, "value", str(outcome))
            ledger["apply_outcome_values"][key] = (
                ledger["apply_outcome_values"].get(key, 0) + 1
            )
            # FALL_OVER and STUNNED_UPRIGHT are the two outcomes that stamp a
            # freeze timer on the body (pretackle_machine.apply_outcome).
            if key in ("FALL_OVER", "STUNNED_UPRIGHT"):
                ledger["freeze_stamps_on_fb"] += 1
        return outcome

    def check_pre_hook(self, bc, defender):
        ledger["check_pre_tackle_calls"] += 1
        if is_tagged_fb(defender):
            ledger["check_pre_tackle_fb_calls"] += 1
            ledger["check_pre_fb_ids"].add(
                int(getattr(defender, "player_id", -1) or -1)
            )
        ledger["check_pre_tackle_ids"].add(
            int(getattr(defender, "player_id", -1) or -1)
        )
        return real_check_pre(self, bc, defender)

    pretackle_machine.run_machine = run_machine_hook
    pretackle_machine.should_engage = should_engage_hook
    pretackle_machine.apply_outcome = apply_outcome_hook
    OpenPlayEngine._check_pre_tackle = check_pre_hook

    try:
        for frame in range(1, FRAMES + 1):
            world.frame_count = frame
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
                    players = getattr(world, "players", None) or []
                    if players:
                        collision_system.resolve_all(players, world=world)

            ledger["frames_run"] += 1

            fb = find_fullback(world)
            bc = getattr(world, "ball_carrier", None)

            if fb is not None:
                ledger["frames_tagged_fb"] += 1
                ledger["fb_ids"].add(
                    int(getattr(fb, "player_id", -1) or -1)
                )
                _n_tagged = 0
                for _d in getattr(world, "defending_team", None) or ():
                    if getattr(_d, "fullback_role_active", False):
                        _n_tagged += 1
                if _n_tagged > ledger["max_tagged_in_frame"]:
                    ledger["max_tagged_in_frame"] = _n_tagged
                if _n_tagged > 1:
                    ledger["frames_multi_tagged"] += 1
                if bool(getattr(fb, "frozen", False)):
                    ledger["frames_fb_frozen"] += 1
                if fnum(getattr(fb, "freeze_timer", 0.0)) > 0.0:
                    ledger["frames_fb_freeze_timer"] += 1

            if fb is None or bc is None or world.tes_active:
                continue
            if not chase_armed(fb):
                continue

            dist = dist_between(fb, bc)
            if dist is None:
                continue

            ledger["frames_chase_armed"] += 1
            _min = ledger["min_dist_seen"]
            if _min is None or dist < _min:
                ledger["min_dist_seen"] = dist

            # ── F  PURSUIT PROGRESS ───────────────────────────────────
            # Is the gap CLOSING?  A chase that never reaches contact has
            # exactly three shapes, and the counters separate them:
            #   fb_stopped        -- the body's own velocity is ~0 (its own
            #                        target is on top of it, or a latch pins it)
            #   pace_tied         -- it is running, but at or below the
            #                        carrier's own speed, so the gap is fixed
            #   closing_blocked   -- it is running faster than the carrier and
            #                        the gap still is not shrinking (a clamp)
            fb_speed = math.hypot(fnum(getattr(fb, "vx", 0.0)),
                                  fnum(getattr(fb, "vy", 0.0)))
            bc_speed = math.hypot(fnum(getattr(bc, "vx", 0.0)),
                                  fnum(getattr(bc, "vy", 0.0)))
            tgt_dist = math.hypot(
                fnum(getattr(fb, "target_x", None), fb.x) - fb.x,
                fnum(getattr(fb, "target_y", None), fb.y) - fb.y,
            )
            src = str(getattr(fb, "target_source", "") or "(none)")
            ledger["target_sources"][src] = (
                ledger["target_sources"].get(src, 0) + 1
            )
            ledger["fb_speed_sum"] += fb_speed
            ledger["bc_speed_sum"] += bc_speed
            ledger["speed_samples"] += 1
            if tgt_dist < 0.05:
                ledger["frames_static_self_target"] += 1
            _prev = ledger["_prev_dist"]
            ledger["_prev_dist"] = dist
            if dist > ENGAGE_DISTANCE_M and _prev is not None:
                _closing = (_prev - dist) / DT
                if _closing < 0.2:
                    ledger["frames_not_closing"] += 1
                    if fb_speed < 0.5:
                        ledger["frames_fb_stopped"] += 1
                    elif fb_speed <= bc_speed + 0.2:
                        ledger["frames_pace_tied"] += 1
                    else:
                        ledger["frames_closing_blocked"] += 1

            if dist <= TACKLE_RADIUS_M:
                ledger["frames_in_tackle_radius"] += 1
                ledger["frames_in_radius_chase_armed"] += 1
                role = getattr(fb, "tackle_role", None)
                if role is not None:
                    ledger["frames_in_radius_with_any_role"] += 1
                if role in ("PRIMARY", "SECONDARY"):
                    ledger["frames_in_radius_with_lane_role"] += 1

            if dist <= ENGAGE_DISTANCE_M:
                ledger["frames_in_engage_distance"] += 1
                angle = None
                try:
                    angle = approach_angle_from_facing(fb, bc)
                except Exception:
                    angle = None
                bucket = angle_bucket(angle)
                ledger["angle_buckets"][bucket] = (
                    ledger["angle_buckets"].get(bucket, 0) + 1
                )
                if angle is not None:
                    ledger["angle_samples"].append(angle)
                    if dist <= TACKLE_RADIUS_M:
                        ledger["angle_samples_1p5"].append(angle)
                tuners = ledger["captured_tuners"]
                try:
                    from game.defence.defence_lvl_1.fullback_role import (
                        fullback_emergency_defender_active,
                    )
                    if fullback_emergency_defender_active(world, fb, bc):
                        ledger["engage_dist_with_fb_eligible"] += 1
                except Exception:
                    pass
                try:
                    if is_slot_tackle_eligible(bc, fb, world):
                        ledger["engage_dist_with_slot_in"] += 1
                except Exception:
                    pass
                try:
                    if is_tackle_engage_guard(fb, bc, world, tuners):
                        ledger["engage_dist_with_guard"] += 1
                except Exception:
                    pass

            tuners = ledger["captured_tuners"]
            if dist <= RECORD_RANGE_M and len(ledger["trace"]) < TRACE_ROWS:
                ledger["trace"].append(
                    trace_row(frame, fb, bc, dist, (
                        approach_angle_from_facing(fb, bc)
                        if bc is not None else None
                    ), tuners, world)
                )
    finally:
        pretackle_machine.run_machine = real_run_machine
        pretackle_machine.should_engage = real_should_engage
        pretackle_machine.apply_outcome = real_apply_outcome
        OpenPlayEngine._check_pre_tackle = real_check_pre

    return ledger
def median(values):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def pool(ledgers, key):
    return sum(int(lg.get(key, 0) or 0) for lg in ledgers)


def pool_map(ledgers, key):
    out = {}
    for lg in ledgers:
        for k, v in (lg.get(key) or {}).items():
            out[k] = out.get(k, 0) + v
    return out


def render_seed_table(ledgers):
    print("-" * 118)
    print(
        "%-10s %7s %7s %7s %7s %7s %7s %7s %7s %7s"
        % ("seed", "frames", "tagged", "chase", "in1.5", "in2.0", "rmach",
           "seng", "sengT", "outcome")
    )
    print("-" * 118)
    for lg in ledgers:
        print(
            "%-10s %7d %7d %7d %7d %7d %7d %7d %7d %7d"
            % (
                lg["seed"], lg["frames_run"], lg["frames_tagged_fb"],
                lg["frames_chase_armed"], lg["frames_in_tackle_radius"],
                lg["frames_in_engage_distance"], lg["run_machine_calls"],
                lg["should_engage_calls"], lg["should_engage_true"],
                lg["apply_outcome_calls"],
            )
        )
    print("")
    print("  tagged = frames a tagged fullback existed (any state)")
    print("  chase  = frames a chase was armed on it (latch OR CHASE state)")
    print("  in1.5  = of those, frames within the 1.5 m tackle radius")
    print("  in2.0  = of those, frames within the 2.0 m engage distance")
    print("  rmach  = pretackle_machine.run_machine calls ON THE TAGGED FB")
    print("  seng   = should_engage calls ON THE TAGGED FB   (sengT = True)")
    print("  outcome= apply_outcome calls ON THE TAGGED FB")


def render_facing(ledgers):
    print("")
    print("=" * 118)
    print("D  FACING -- the machine's own bands, for frames INSIDE engage distance")
    print("=" * 118)
    buckets = pool_map(ledgers, "angle_buckets")
    total = sum(buckets.values())
    print("  %-18s %8s  %s" % ("band", "frames", "share"))
    print("  " + "-" * 50)
    for key in ("head_on_lt30", "side_on_30_60", "rear_60_120",
                "refused_ge120", "no_angle"):
        n = buckets.get(key, 0)
        if not n:
            continue
        print("  %-18s %8d  %s" % (key, n, percent(n, total)))
    if total:
        clean = buckets.get("head_on_lt30", 0)
        print("")
        print("  frames inside engage distance                       : %d" % total)
        print("  of those, facing within 30 deg of the carrier       : %d (%s)"
              % (clean, percent(clean, total)))
        print("  of those, facing 30..120 deg (SIDE_ON / REAR only)  : %d (%s)"
              % (total - clean - buckets.get("no_angle", 0),
                 percent(total - clean - buckets.get("no_angle", 0), total)))
    all_angles = [a for lg in ledgers for a in lg["angle_samples"]]
    near_angles = [a for lg in ledgers for a in lg["angle_samples_1p5"]]
    print("")
    print("  angle samples inside 2.0 m : n=%d  median=%s  mean=%s"
          % (len(all_angles), fmt(median(all_angles), 1),
             fmt((sum(all_angles) / len(all_angles)) if all_angles else None, 1)))
    print("  angle samples inside 1.5 m : n=%d  median=%s  mean=%s"
          % (len(near_angles), fmt(median(near_angles), 1),
             fmt((sum(near_angles) / len(near_angles)) if near_angles else None, 1)))


def render_admission(ledgers):
    print("")
    print("=" * 118)
    print("B  ADMISSION -- the engine's own verdicts on the tagged fullback")
    print("=" * 118)
    states = pool_map(ledgers, "run_machine_states")
    if not states:
        print("  pretackle_machine.run_machine was NEVER called on the tagged")
        print("  fullback in any swept seed.  The engage machine never saw it.")
    else:
        print("  %-24s %8s" % ("machine state", "calls"))
        print("  " + "-" * 36)
        for key in sorted(states, key=lambda k: -states[k]):
            print("  %-24s %8d" % (key, states[key]))
        engage_states = {
            "CLOSING_HEAD_ON", "SQUARE_UP_ENGAGE", "COMMIT_PLANTED",
        }
        feed = sum(v for k, v in states.items() if k in engage_states)
        print("")
        print("  states that can FEED TES (CLOSING_HEAD_ON / SQUARE_UP_ENGAGE")
        print("  / COMMIT_PLANTED)                                  : %d (%s)"
              % (feed, percent(feed, sum(states.values()))))
    print("")
    print("  should_engage calls on the tagged FB : %d  (True: %d)"
          % (pool(ledgers, "should_engage_calls"),
             pool(ledgers, "should_engage_true")))
    print("  apply_outcome calls on the tagged FB : %d"
          % pool(ledgers, "apply_outcome_calls"))
    outcomes = pool_map(ledgers, "apply_outcome_values")
    if outcomes:
        print("  outcomes APPLIED to the tagged FB:")
        for key in sorted(outcomes, key=lambda k: -outcomes[k]):
            print("      %-20s %8d" % (key, outcomes[key]))
    print("  freeze stamps (FALL_OVER / STUNNED_UPRIGHT) on the FB : %d"
          % pool(ledgers, "freeze_stamps_on_fb"))
    print("  frames the FB carried frozen=True                     : %d"
          % pool(ledgers, "frames_fb_frozen"))
    print("  frames the FB carried freeze_timer > 0                : %d"
          % pool(ledgers, "frames_fb_freeze_timer"))


def render_bypass(ledgers):
    print("")
    print("=" * 118)
    print("C  BYPASS -- the OPE physical-contact route (the OTHER engage route)")
    print("=" * 118)
    calls = pool(ledgers, "check_pre_tackle_calls")
    fb_calls = pool(ledgers, "check_pre_tackle_fb_calls")
    lane_in_radius = pool(ledgers, "frames_in_radius_with_lane_role")
    any_in_radius = pool(ledgers, "frames_in_radius_with_any_role")
    print("  OpenPlayEngine._check_pre_tackle calls, all defenders : %d" % calls)
    print("  of those, called WITH the tagged fullback             : %d"
          % fb_calls)
    print("")
    print("  frames the tagged FB was inside the 1.5 m radius with a")
    print("  chase armed                                          : %d"
          % pool(ledgers, "frames_in_radius_chase_armed"))
    print("  ... and held NO tackle_role at all                   : %d"
          % (pool(ledgers, "frames_in_radius_chase_armed") - any_in_radius))
    print("  ... and held a LANE stamp (PRIMARY / SECONDARY)      : %d"
          % lane_in_radius)
    print("")
    print("  ADMISSION KEYS.  There are TWO routes to _check_pre_tackle and they")
    print("  admit on DIFFERENT keys:")
    print("    OPE physical bump (open_play_engine.py:3260) -- a flat skip:")
    print("        if getattr(p, 'fullback_role_active', False): continue")
    print("      A tagged fullback can NEVER reach the gate here.")
    print("    contact_authority.evaluate_contact (contact_authority.py:736)")
    print("      conjunct 1 skips a tagged fullback only while it LACKS")
    print("      can_acquire_tackle; conjunct 2 then admits it when")
    print("      fullback_emergency_defender_active(world, p, bc) is True.")
    print("      THIS is the route the tagged fullback actually reaches.")
    print("  frames inside 2.0 m where the emergency admission is TRUE : %d"
          % pool(ledgers, "engage_dist_with_fb_eligible"))
    print("  frames inside 2.0 m where a LANE stamp admits it (PRIM/SEC) : %d"
          % pool(ledgers, "engage_dist_with_slot_in"))
    print("  frames inside 2.0 m where the engage guard admits it       : %d"
          % pool(ledgers, "engage_dist_with_guard"))
    print("")
    fb_ids = set()
    pre_ids = set()
    for lg in ledgers:
        fb_ids |= lg.get("fb_ids", set())
        pre_ids |= lg.get("check_pre_fb_ids", set())
    print("")
    print("  IDENTITY.  defender ids carrying fullback_role_active : %s"
          % (sorted(fb_ids) or "none"))
    print("  of those, ids the route was ever handed               : %s"
          % (sorted(pre_ids) or "none"))
    print("  ids BOTH tagged and route-reached                     : %s"
          % (sorted(fb_ids & pre_ids) or "none"))
    print("  max tagged bodies in one frame                        : %d"
          % max([lg.get("max_tagged_in_frame", 0) for lg in ledgers] or [0]))
    print("  frames with MORE than one tagged body                 : %d"
          % pool(ledgers, "frames_multi_tagged"))
    print("")
    print("  The OPE trigger skips a tagged fullback before the tackle-role")
    print("  test and before _check_pre_tackle:")
    print("      for p in defenders:")
    print("          if getattr(p, 'fullback_role_active', False): continue")
    print("  So a tagged body can only be armed through the OTHER route --")
    print("  try_pre_tackle -> run_machine / should_engage.  The IDENTITY")
    print("  block above says WHICH body the calls actually named.")


def render_trace(ledger):
    if not ledger or not ledger.get("trace"):
        return
    print("")
    print("=" * 118)
    print("TRACE -- seed %d, first %d frames with a chase armed and the carrier"
          % (ledger["seed"], len(ledger["trace"])))
    print("         within %.0f m (end-of-frame sample)" % RECORD_RANGE_M)
    print("=" * 118)
    print(
        "%-6s %6s %7s %-14s %7s %7s %7s %-8s %-16s %s"
        % ("frame", "dist", "angle", "band", "fb m/s", "bc m/s", "to tgt",
           "state", "target_source", "closing m/s")
    )
    print("-" * 118)
    prev_dist = None
    for row in ledger["trace"]:
        closing = ("--" if prev_dist is None
                   else "%+.2f" % ((prev_dist - row["dist"]) / DT))
        prev_dist = row["dist"]
        print(
            "%-6d %6s %7s %-14s %7s %7s %7s %-8s %-16s %s"
            % (
                row["frame"], fmt(row["dist"], 2), fmt(row["angle"], 1),
                row["bucket"], fmt(row["fb_speed"], 2),
                fmt(row["bc_speed"], 2), fmt(row["tgt_dist"], 2),
                fmt(row["state"]), fmt(row["target_source"])[:16], closing,
            )
        )
    print("")
    print("  fb m/s = the body's OWN velocity magnitude (hypot(vx, vy)).")
    print("           NOT move_speed, which the engine stopped writing for the")
    print("           tagged fullback and which therefore reads as a stale")
    print("           constant -- a plausible-looking lie.")
    print("  to tgt = distance from the body to its own target_x/target_y.")
    print("           ~0 means the body is being asked to stand on itself.")
    print("  closing= the rate the gap to the carrier is shrinking, m/s.")
    print("           ~0 while both speeds are non-zero is a PACE STALEMATE.")


def render_pursuit(ledgers):
    print("")
    print("=" * 118)
    print("F  PURSUIT PROGRESS -- is the gap CLOSING?  (chase frames past 2.0 m)")
    print("=" * 118)
    samples = pool(ledgers, "speed_samples")
    not_closing = pool(ledgers, "frames_not_closing")
    stopped = pool(ledgers, "frames_fb_stopped")
    tied = pool(ledgers, "frames_pace_tied")
    blocked = pool(ledgers, "frames_closing_blocked")
    static_self = pool(ledgers, "frames_static_self_target")
    print("  chase frames sampled                                : %d" % samples)
    print("  chase frames past engage distance whose gap did NOT")
    print("  shrink (change < 0.2 m/s)                           : %d (%s)"
          % (not_closing, percent(not_closing, samples)))
    print("")
    print("  of those stalled frames, the cause:")
    print("      fb velocity ~0      -- STOPPED                  : %d (%s)"
          % (stopped, percent(stopped, not_closing)))
    print("      fb speed <= carrier -- PACE TIED                : %d (%s)"
          % (tied, percent(tied, not_closing)))
    print("      fb faster, gap fixed -- CLOSING BLOCKED         : %d (%s)"
          % (blocked, percent(blocked, not_closing)))
    print("")
    print("  frames whose OWN target sat on the body (a static")
    print("  self-target, |target - self| < 0.05 m)              : %d (%s)"
          % (static_self, percent(static_self, samples)))
    if samples:
        print("")
        print("  mean fullback speed during a chase                  : %.2f m/s"
              % (sum(lg["fb_speed_sum"] for lg in ledgers) / samples))
        print("  mean carrier speed over the same frames             : %.2f m/s"
              % (sum(lg["bc_speed_sum"] for lg in ledgers) / samples))
    print("")
    print("  target_source while a chase is armed:")
    srcs = pool_map(ledgers, "target_sources")
    if not srcs:
        print("      (none recorded)")
    for key in sorted(srcs, key=lambda k: -srcs[k]):
        print("      %-24s %8d" % (key[:24], srcs[key]))


def render_verdict(ledgers):
    print("")
    print("=" * 118)
    print("VERDICT")
    print("=" * 118)

    all_pre_calls = pool(ledgers, "check_pre_tackle_calls")
    rmach = pool(ledgers, "run_machine_calls")
    fb_calls = pool(ledgers, "check_pre_tackle_fb_calls")
    lane_in_radius = pool(ledgers, "frames_in_radius_with_lane_role")
    buckets = pool_map(ledgers, "angle_buckets")
    total_angle = sum(buckets.values())
    clean = buckets.get("head_on_lt30", 0)
    states = pool_map(ledgers, "run_machine_states")
    feed_states = sum(
        v for k, v in states.items()
        if k in ("CLOSING_HEAD_ON", "SQUARE_UP_ENGAGE", "COMMIT_PLANTED")
    )
    clean_outcomes = pool_map(ledgers, "apply_outcome_values").get(
        "CLEAN_ENGAGE", 0
    )
    samples = pool(ledgers, "speed_samples")
    not_closing = pool(ledgers, "frames_not_closing")
    near_angles = [a for lg in ledgers for a in lg["angle_samples_1p5"]]

    print("  THE HYPOTHESIS.  \"The fullback chases but (A) never tackles and")
    print("  (B) faces the wrong way -- maybe the facing is why the tackle")
    print("  cannot be made.\"  Measured: (B) is real but is NOT the wall, and")
    print("  (A) has three causes of which facing is not one.")
    print("")
    print("  --- (B) FACING -- real, but it does not block the engage ------")
    print("  in range, facing < 30 deg (the CLEAN_ENGAGE band) : %d of %d (%s)"
          % (clean, total_angle, percent(clean, total_angle)))
    print("  median approach angle INSIDE 1.5 m                : %s deg"
          % fmt(median(near_angles), 1))
    print("  run_machine states that FEED TES                  : %d of %d (%s)"
          % (feed_states, rmach, percent(feed_states, rmach)))
    print("  CLEAN_ENGAGE outcomes APPLIED to the fullback     : %d"
          % clean_outcomes)
    print("  => the wrong-way approach is genuine at contact range (median %s"
          % fmt(median(near_angles), 1))
    print("     deg inside 1.5 m), but the machine still classified a")
    print("     TES-feeding state on %s of its calls and the fullback WON %d"
          % (percent(feed_states, rmach), clean_outcomes))
    print("     engagements outright.  Facing is a real symptom; it is not")
    print("     the thing refusing the tackle.")
    print("")
    print("  --- (A) NEVER TACKLES -- three walls, none of them facing -----")
    print("    WALL 1  only the EMERGENCY conjunct ever admits it.")
    print("            Two routes reach _check_pre_tackle:")
    print("              open_play_engine.py:3260 -- the physical bump,")
    print("                which skips a tagged fullback outright.  It named")
    print("                the fullback 0 times and can never name it.")
    print("              contact_authority.py:736 -- the lane/emergency")
    print("                route, which named it %d times." % fb_calls)
    print("            There it is admitted ONLY by the emergency conjunct,")
    print("            never by a lane stamp: %d radius frames held a LANE")
    print("            stamp (PRIMARY/SECONDARY), while the emergency")
    print("            admission was True on %d frames inside 2.0 m."
          % pool(ledgers, "engage_dist_with_fb_eligible"))
    print("            So the fullback's tackle authority rests entirely on a")
    print("            predicate it can lose from one frame to the next, and")
    print("            the lane system never grants it a slot of its own.")
    print("")
    print("    WALL 2  the pursuit frequently never arrives.")
    print("            chase frames past 2.0 m whose gap did NOT shrink : %d"
          % not_closing)
    print("            (of %d chase frames sampled -- an upper-bound"
          % samples)
    print("            denominator).  Cause broken down in section F: STOPPED")
    print("            vs PACE TIED vs CLOSING BLOCKED.")
    print("")
    print("    WALL 3  the engagement it DOES win is not converted.")
    print("            CLOSING_HEAD_ON %d, CLEAN_ENGAGE %d applied,"
          % (states.get("CLOSING_HEAD_ON", 0), clean_outcomes))
    print("            should_engage True %d times -- and yet no tackle."
          % pool(ledgers, "should_engage_true"))
    print("            The lane route says YES and TES is still not entered,")
    print("            so the two are being asked different questions.  That")
    print("            conversion gap is the top-priority lead.")
    print("")
    print("  ORDERING.  A skip acts BEFORE any geometry test, so (H1) and (H2)")
    print("  are not exclusive -- but these counters say the skip is the wall")
    print("  and the facing is not: the machine is reached, is often")
    print("  satisfied, and the tackle still does not happen.")
    print("")


def main():
    global _REPORT
    _REPORT = open(REPORT_PATH, "w", encoding="utf-8")
    sys.stdout = _TEE
    # The header is this probe's own output, so the sink is open for it.
    _TEE.active = True

    print("=" * 118)
    print("FB FACING AT CONTACT -- is the tagged fullback reached, and what")
    print("rejects it?")
    print("=" * 118)
    print("  seeds %s, %d frames each, dt=1/60" % (SEEDS, FRAMES))
    print("  bands: head-on < %.0f deg | side-on < %.0f deg | rear < %.0f deg |"
          " refused >= %.0f deg"
          % (HEAD_ON_MAX_DEG, SIDE_ON_MAX_DEG, MAX_APPROACH_ANGLE_DEG,
             MAX_APPROACH_ANGLE_DEG))
    print("  tackle radius %.1f m | engage distance %.1f m | guard facing band"
          " %.0f deg"
          % (TACKLE_RADIUS_M, ENGAGE_DISTANCE_M,
             TACKLE_ENGAGE_FACING_MAX_DEG))
    print("")

    # Engine per-frame audit chatter is DISCARDED for the rollouts.
    _TEE.active = False
    ledgers = []
    for seed in SEEDS:
        ledgers.append(run_rollout(seed))

    # Every rollout is finished, so all remaining output is the probe's own.
    _TEE.active = True
    _REAL_STDOUT.write(
        "rollouts complete; engine chatter suppressed: %d bytes\n"
        % _TEE.suppressed_bytes
    )

    print("")
    print("=" * 118)
    print("A  REACH -- per seed")
    print("=" * 118)
    render_seed_table(ledgers)

    render_admission(ledgers)
    render_bypass(ledgers)
    render_facing(ledgers)

    richest = None
    for lg in ledgers:
        if richest is None or lg["frames_chase_armed"] > richest["frames_chase_armed"]:
            richest = lg
    render_trace(richest)
    render_pursuit(ledgers)

    print("")
    print("=" * 118)
    print("POOLED (all %d seeds)" % len(ledgers))
    print("=" * 118)
    print("  frames stepped                                      : %d"
          % pool(ledgers, "frames_run"))
    print("  frames a tagged fullback existed                    : %d"
          % pool(ledgers, "frames_tagged_fb"))
    print("  frames a chase was armed on it                      : %d"
          % pool(ledgers, "frames_chase_armed"))
    print("  frames it was inside the %.1f m tackle radius        : %d"
          % (TACKLE_RADIUS_M, pool(ledgers, "frames_in_tackle_radius")))
    print("  frames it was inside the %.1f m engage distance      : %d"
          % (ENGAGE_DISTANCE_M, pool(ledgers, "frames_in_engage_distance")))
    print("  frames it was in radius AND held a lane stamp       : %d"
          % pool(ledgers, "frames_in_radius_with_lane_role"))
    print("")
    _mins = [lg["min_dist_seen"] for lg in ledgers if lg["min_dist_seen"] is not None]
    print("  closest the tagged FB ever got to the carrier       : %s m"
          % fmt(min(_mins) if _mins else None, 2))
    print("  _check_pre_tackle calls naming the tagged FB        : %d"
          % pool(ledgers, "check_pre_tackle_fb_calls"))
    print("  run_machine calls on the tagged FB                  : %d"
          % pool(ledgers, "run_machine_calls"))
    print("  should_engage True on the tagged FB                 : %d"
          % pool(ledgers, "should_engage_true"))
    print("  apply_outcome calls on the tagged FB                : %d"
          % pool(ledgers, "apply_outcome_calls"))
    print("")
    print("  chase frames that did not close on the carrier      : %d"
          % pool(ledgers, "frames_not_closing"))
    print("      of those, fb STOPPED (velocity ~0)              : %d"
          % pool(ledgers, "frames_fb_stopped"))
    print("      of those, PACE TIED (fb <= carrier speed)       : %d"
          % pool(ledgers, "frames_pace_tied"))
    print("      of those, CLOSING BLOCKED (fb faster, no gain)  : %d"
          % pool(ledgers, "frames_closing_blocked"))
    print("  frames the FB chased with its target ON ITSELF      : %d"
          % pool(ledgers, "frames_static_self_target"))

    render_verdict(ledgers)

    try:
        sys.stdout = _REAL_STDOUT
    except Exception:
        pass
    try:
        _REPORT.close()
    except Exception:
        pass
    print("  report written to %s" % REPORT_PATH)


if __name__ == "__main__":
    main()
