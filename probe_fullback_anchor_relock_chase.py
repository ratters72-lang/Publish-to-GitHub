"""probe_fullback_anchor_relock_chase.py — DOES AN ANCHOR RELOCK END A LIVE
CHASE?  (multi-seed A/B, 2026-09-28)

THE CLAIM UNDER TEST
--------------------
``classify_fullback_break`` reads the defensive line anchor, and the anchor IS
``ruck_x_m + 10``.  During a tackle the engine re-locks ``ruck_x_m`` onto the
CARRIER -- most importantly at the CONTACT -> DRIVE entry write in
``OpenPlayEngine._update_tackle_contact``, which also drops the anchor lock so
the next frame re-derives ``line = ruck_x + 10``.  That is INTENTIONAL for the
line (the drive-time line must anchor to the CURRENT tackle point, not the
stale pre-tackle ruck) -- but it moves the line anchor FORWARD, GOAL-SIDE of a
live carrier, and a consumer that re-reads the anchor every frame can lose a
verdict it had already reached.

The single recorded instance (``probe_fb_authority_timeline.py``, seed
20260827, window 16) shows exactly that:

    f=382  mode=CHASE  brk=DEPTH  bc_x=26.52  line=18.25  ruck=8.25
    f=383  mode=DRIFT  brk=NONE   bc_x=26.52  line=36.52  ruck=26.52

THE CARRIER NEVER MOVED.  Only the anchor did (+18.27 m).  The chase ended by
MODE_OVERWRITE: the same writer wrote the sweeper anchor + DRIFT, with no
engine event and nothing clearing the engagement.

WHAT THIS PROBE MEASURES
------------------------
On every frame of a real seeded rollout it samples the tagged fullback and
counts a RELOCK-INDUCED CHASE LOSS:

    prev mode was CHASE
    the mode is no longer CHASE
    the live line anchor moved FORWARD by more than FB_ANCHOR_RELOCK_EPS_M
    AND the carrier was STILL a break under the anchor live on the PREVIOUS
        frame -> classify_fullback_break(bc, fb, prev_live_line_anchor) is
        NOT FB_BREAK_NONE

The last clause is the discriminator.  It separates "the anchor moved and the
verdict silently went with it" from "the carrier genuinely dropped behind the
line" -- a chase that ends because the ground really was lost is NOT counted.

It is evaluated against the PREVIOUS FRAME'S LIVE ANCHOR, not against the
latch: with the lever DISARMED no latch can exist at all, so a latch-derived
counterfactual would read FB_BREAK_NONE on every frame and the defect class
could never be counted -- the measurement would be vacuous exactly where it
matters most.

THE A/B
-------
The same seed is run twice IN PROCESS, once with ``fullback_role.FB_BREAK_LATCH``
rebound to False (the pre-change engine) and once to True (the shipped law).
The only difference between the arms is the latch.  The prediction:

    OFF   relock-induced losses > 0   (the recorded defect reproduces)
    ON    relock-induced losses == 0  (a forward relock cannot withdraw the
                                       verdict; the chase is preserved)

It also dumps every OFF-arm loss row so the recorded frame-383 instance can be
matched by hand, and reports the ON-arm chase-exit reasons so a genuine exit
(carrier behind the LATCHED line, tag loss, tackle, TES) is never confused
with a lost one.

Run:    python probe_fullback_anchor_relock_chase.py
Report: tmp_fb_anchor_relock_chase.txt
"""
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import io
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

from game.defence.defence_lvl_1 import fullback_role as fbr
from game.defence.defence_lvl_1.fullback_role import (
    classify_fullback_break,
    compute_fullback_anchor,
    break_test_line_anchor_x,
    latched_fullback_break_anchor_x,
    line_anchor_x,
    is_fullback_emergency_engaged,
)

# ── ROLLOUT CONFIG ───────────────────────────────────────────────────
MAX_FRAMES = int(os.environ.get("RLM_FBRL_FRAMES", "3000"))
DT = 1.0 / 60.0

# Six seeds.  20260827 is the seed the recorded frame-382/383 telemetry came
# from, so the probe must reproduce that instance exactly; the rest are
# independent rollouts so a conclusion is not a single-episode fluke.
SEEDS = (
    20260827, 20260901, 11, 2024, 31337, 777,
)

# The recorded instance this probe must reproduce (seed 20260827).
RECORD_FRAME = 383
RECORD_PREV_LINE = 18.25
RECORD_LINE = 36.52
RECORD_BC_X = 26.52

EPS = float(getattr(fbr, "FB_ANCHOR_RELOCK_EPS_M", 0.5))
BREAK_NONE = fbr.FB_BREAK_NONE

REPORT_PATH = os.environ.get(
    "RLM_FBRL_REPORT", "tmp_fb_anchor_relock_chase.txt"
)

_OUT = []


def emit(text=""):
    """Write to stdout and to the report buffer."""
    print(text, flush=True)
    _OUT.append(text)


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


def mode_name(body):
    state = getattr(body, "movement_state", None)
    return getattr(state, "name", None) or str(state) if state is not None else None


def tagged_fullback(world):
    for d in (getattr(world, "defending_team", None) or []):
        if getattr(d, "fullback_role_active", False):
            return d
    return None


def sample(world):
    """One read-only row describing the chase-relevant state this frame."""
    body = tagged_fullback(world)
    bc = getattr(world, "ball_carrier", None)
    live_line = float(line_anchor_x(world))
    row = {
        "frame": int(getattr(world, "frame_count", 0) or 0),
        "body": body,
        "tag": body is not None,
        "mode": mode_name(body) if body is not None else None,
        "target_x": optnum(getattr(body, "target_x", None)) if body else None,
        "x": fnum(getattr(body, "x", 0.0)) if body else None,
        "ruck_x": optnum(getattr(world, "ruck_x_m", None)),
        "line": live_line,
        "latched": (
            latched_fullback_break_anchor_x(body) if body is not None else None
        ),
        "engaged": (
            bool(is_fullback_emergency_engaged(body)) if body is not None else False
        ),
        "bc_x": fnum(getattr(bc, "x", 0.0)) if bc is not None else None,
        "bc_y": fnum(getattr(bc, "y", 0.0)) if bc is not None else None,
        "tes": bool(getattr(world, "tes_active", False)),
        "live_kind": BREAK_NONE,
        "test_kind": BREAK_NONE,
        "test_anchor": live_line,
    }
    if body is not None and bc is not None:
        _ax, anchor_y = compute_fullback_anchor(bc, live_line)
        row["live_kind"] = classify_fullback_break(bc, body, live_line, anchor_y)
        test_anchor = float(break_test_line_anchor_x(world, body, live_line))
        row["test_anchor"] = test_anchor
        row["test_kind"] = classify_fullback_break(bc, body, test_anchor, anchor_y)
    return row
# ═════════════════════════════════════════════════════════════════════
# THE ROLLOUT
# ═════════════════════════════════════════════════════════════════════

def run_arm(seed, frames=MAX_FRAMES, lever=True):
    """Boot the real engine for *seed* with the latch lever at *lever*.

    Returns per-run totals plus every chase-exit row, each labelled with the
    reason the chase ended.
    """
    saved = fbr.FB_BREAK_LATCH
    fbr.FB_BREAK_LATCH = bool(lever)

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

    result = {
        "seed": seed,
        "frames": 0,
        "chase_frames": 0,
        "exits": [],          # every CHASE -> non-CHASE transition
        "relock_losses": [],  # the subset that is a relock-induced loss
        "relocks": [],        # EVERY forward line-anchor move (diagnostic)
        "armed_latch_frames": 0,
    }

    prev = None
    try:
        for frame in range(1, frames + 1):
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

            # SAMPLE AFTER the engine has finished the frame.
            row = sample(world)
            result["frames"] = frame

            # THE COUNTERFACTUAL VERDICT.  Would the carrier still be a break
            # under the anchor that was live on the PREVIOUS frame -- i.e. the
            # anchor from BEFORE whatever relock this frame performed?  This is
            # derived from the PREVIOUS live anchor and NOT from the latch, so
            # it is measurable with the lever in EITHER position.  (With the
            # lever OFF no latch can exist at all, so a latch-derived test
            # would be vacuously FB_BREAK_NONE and could never fire -- exactly
            # the flaw this clause replaces.)
            row["prev_anchor_kind"] = BREAK_NONE
            if (prev is not None and row["body"] is not None
                    and getattr(world, "ball_carrier", None) is not None):
                _bc = world.ball_carrier
                _ax, _ay = compute_fullback_anchor(_bc, row["line"])
                row["prev_anchor_kind"] = classify_fullback_break(
                    _bc, row["body"], prev["line"], _ay
                )
            if row["latched"] is not None:
                result["armed_latch_frames"] += 1
            if row["mode"] == "CHASE" and row["tag"]:
                result["chase_frames"] += 1

            if prev is not None:
                # ── the forward-relock ledger (every such frame, chase or not)
                forward_delta = float(row["line"]) - float(prev["line"])
                if forward_delta > EPS:
                    result["relocks"].append({
                        "frame": row["frame"],
                        "prev_line": float(prev["line"]),
                        "line": float(row["line"]),
                        "forward": forward_delta,
                        "ruck_x": row["ruck_x"],
                        "bc_x": row["bc_x"],
                        "prev_bc_x": prev["bc_x"],
                        "prev_mode": prev["mode"],
                        "mode": row["mode"],
                        "live_kind": row["live_kind"],
                        "prev_anchor_kind": row["prev_anchor_kind"],
                        "was_chase": prev["tag"] and prev["mode"] == "CHASE",
                        "is_chase": row["tag"] and row["mode"] == "CHASE",
                    })

                was_chase = prev["tag"] and prev["mode"] == "CHASE"
                is_chase = row["tag"] and row["mode"] == "CHASE"
                if was_chase and not is_chase:
                    entry = _classify_exit(prev, row)
                    result["exits"].append(entry)
                    if entry["reason"] == "RELOCK_LOSS":
                        result["relock_losses"].append(entry)
            prev = row
    finally:
        fbr.FB_BREAK_LATCH = saved

    return result


def _classify_exit(prev, row):
    """Label a CHASE -> non-CHASE transition with the reason it ended.

    ``RELOCK_LOSS`` is the defect class: the line anchor moved FORWARD past the
    carrier and that alone withdrew the verdict, while the carrier is still a
    break under the anchor the test SHOULD be using.  Everything else is a
    genuine end of the chase.
    """
    forward = float(row["line"]) - float(prev["line"])
    entry = {
        "frame": row["frame"],
        "prev_mode": prev["mode"],
        "mode": row["mode"],
        "prev_line": float(prev["line"]),
        "line": float(row["line"]),
        "forward": forward,
        "bc_x": row["bc_x"],
        "prev_bc_x": prev["bc_x"],
        "ruck_x": row["ruck_x"],
        "live_kind": row["live_kind"],
        "test_kind": row["test_kind"],
        "prev_anchor_kind": row.get("prev_anchor_kind", BREAK_NONE),
        "test_anchor": row["test_anchor"],
        "latched": row["latched"],
        "engaged": row["engaged"],
        "engagement_cleared": bool(prev["engaged"] and not row["engaged"]),
        "tes": row["tes"],
        "target_x": row["target_x"],
        "reason": "OTHER",
    }
    # The discriminator is the PREVIOUS-frame anchor verdict, not the latch:
    # "the carrier was still a genuine break one frame ago, and the only thing
    # that changed is that the line anchor moved FORWARD past it".
    #
    # ORDER MATTERS, and it is the ONLY ordering that can count this defect.
    # ``world.tes_active`` is True for the WHOLE tackle -- including the drive
    # -- so testing "is a tackle live?" cannot separate the relock frame from
    # a genuine resolution: on the recorded instance it is True on BOTH sides
    # of the transition.  The separating evidence is the ANCHOR, not the
    # tackle state: the line anchor moved FORWARD past a carrier that was
    # still a genuine break one frame earlier.  That is the defect, and it is
    # tested FIRST (after tag loss).  A chase that ends with the carrier no
    # longer past the previous anchor is a genuine end and lands in
    # CARRIER_BEHIND_LINE; a chase that ends with the engagement clearing and
    # no anchor move lands in TACKLE_OR_RELEASE.  ``engagement_cleared`` is
    # still recorded on every row so a coincident clear is visible.
    if not row["tag"]:
        entry["reason"] = "TAG_LOST"
    elif (row.get("prev_anchor_kind", BREAK_NONE) != BREAK_NONE
            and forward > EPS):
        entry["reason"] = "RELOCK_LOSS"
    elif row.get("prev_anchor_kind", BREAK_NONE) == BREAK_NONE:
        entry["reason"] = "CARRIER_BEHIND_LINE"
    elif row["tes"] or entry["engagement_cleared"]:
        entry["reason"] = "TACKLE_OR_RELEASE"
    else:
        entry["reason"] = "VERDICT_OTHER"
    return entry


# ═════════════════════════════════════════════════════════════════════
# THE A/B SWEEP
# ═════════════════════════════════════════════════════════════════════

def sweep(seeds=SEEDS, frames=MAX_FRAMES):
    """Run every seed under both lever states."""
    runs = []
    for seed in seeds:
        off = run_arm(seed, frames=frames, lever=False)
        on = run_arm(seed, frames=frames, lever=True)
        runs.append({"seed": seed, "off": off, "on": on})
        emit(
            "  seed %-9s OFF: chase=%-6d relock_losses=%-4d exits=%-4d | "
            "ON: chase=%-6d relock_losses=%-4d exits=%-4d"
            % (
                seed,
                off["chase_frames"], len(off["relock_losses"]), len(off["exits"]),
                on["chase_frames"], len(on["relock_losses"]), len(on["exits"]),
            )
        )
    return runs


def exit_histogram(runs, arm):
    counts = {}
    for run in runs:
        for entry in run[arm]["exits"]:
            counts[entry["reason"]] = counts.get(entry["reason"], 0) + 1
    return counts


def report(runs):
    emit("")
    emit("=" * 70)
    emit("POOLED RESULT  (%d seeds x %d frames x 2 arms = %d engine frames)"
         % (len(runs), MAX_FRAMES, len(runs) * MAX_FRAMES * 2))
    emit("=" * 70)
    emit("")

    off_losses = sum(len(r["off"]["relock_losses"]) for r in runs)
    on_losses = sum(len(r["on"]["relock_losses"]) for r in runs)
    off_chase = sum(r["off"]["chase_frames"] for r in runs)
    on_chase = sum(r["on"]["chase_frames"] for r in runs)

    emit("RELOCK-INDUCED CHASE LOSSES")
    emit("  lever OFF (pre-change engine) : %d" % off_losses)
    emit("  lever ON  (shipped law)       : %d" % on_losses)
    emit("")
    emit("TOTAL CHASE FRAMES")
    emit("  lever OFF : %d" % off_chase)
    emit("  lever ON  : %d  (%+d)" % (on_chase, on_chase - off_chase))
    emit("")
    emit("CHASE-EXIT REASONS")
    hist_off = exit_histogram(runs, "off")
    hist_on = exit_histogram(runs, "on")
    for reason in sorted(set(hist_off) | set(hist_on)):
        emit("  %-20s OFF=%-5d ON=%-5d"
             % (reason, hist_off.get(reason, 0), hist_on.get(reason, 0)))
    emit("")

    # ── every OFF-arm loss, in frame order, with the numbers ──
    emit("-" * 70)
    emit("EVERY RELOCK-INDUCED LOSS (lever OFF) -- the evidence rows")
    emit("-" * 70)
    rows = []
    for run in runs:
        for entry in run["off"]["relock_losses"]:
            rows.append((run["seed"], entry))
    rows.sort(key=lambda pair: (pair[0], pair[1]["frame"]))
    if not rows:
        emit("  (none -- the defect did not reproduce on these seeds)")
    for seed, e in rows:
        emit(
            "  seed %-9s f=%-6d line %.2f -> %.2f (%+.2f) ruck=%s bc_x=%.2f "
            "live=%s prev_anchor=%s engaged_cleared=%s"
            % (
                seed, e["frame"], e["prev_line"], e["line"], e["forward"],
                ("%.2f" % e["ruck_x"]) if e["ruck_x"] is not None else "None",
                e["bc_x"], e["live_kind"], e["prev_anchor_kind"],
                e.get("engagement_cleared"),
            )
        )
    emit("")

    # ── the recorded instance must be in the OFF evidence ──
    emit("-" * 70)
    emit("THE RECORDED INSTANCE (seed %d, frame %d)" % (SEEDS[0], RECORD_FRAME))
    emit("-" * 70)
    record = None
    for seed, e in rows:
        if seed == SEEDS[0] and e["frame"] == RECORD_FRAME:
            record = e
            break
    if record is None:
        emit("  NOT REPRODUCED on seed %d -- reported honestly."
             % SEEDS[0])
        near = [e for s, e in rows if s == SEEDS[0]]
        if near:
            emit("  nearest OFF-arm loss on that seed: f=%d line %.2f -> %.2f"
                 % (near[0]["frame"], near[0]["prev_line"], near[0]["line"]))
    else:
        emit("  REPRODUCED: line %.2f -> %.2f, bc_x %.2f (recorded %.2f/%.2f/%.2f)"
             % (record["prev_line"], record["line"], record["bc_x"],
                RECORD_PREV_LINE, RECORD_LINE, RECORD_BC_X))
        emit("  live verdict at that frame : %s (the recorded brk=NONE)"
             % record["live_kind"])
        emit("  latched-test verdict       : %s"
             % record["test_kind"])
        emit("  the SAME frame under the lever ON: %s"
             % ("PRESERVED (no such exit)"
                if all(e["frame"] != RECORD_FRAME
                       for r in runs for e in r["on"]["relock_losses"])
                else "STILL LOST"))
    emit("")

    # ── THE FORWARD-RELOCK LEDGER: every frame where the line anchor moved
    #    forward.  Printing it separates "the relock never happened" from
    #    "the relock happened and the chase survived it" -- without it a zero
    #    loss count is ambiguous between the two.
    emit("-" * 70)
    emit("FORWARD ANCHOR RELOCKS (the mechanism itself)")
    emit("-" * 70)
    off_relocks = sum(len(r["off"]["relocks"]) for r in runs)
    on_relocks = sum(len(r["on"]["relocks"]) for r in runs)
    emit("  total forward relocks: OFF=%d ON=%d" % (off_relocks, on_relocks))
    chase_ending = [
        (r["seed"], rl) for r in runs for rl in r["off"]["relocks"]
        if rl["was_chase"] and not rl["is_chase"]
    ]
    emit("  relocks that ENDED a chase (lever OFF): %d" % len(chase_ending))
    emit("")
    for r in runs:
        if r["seed"] != SEEDS[0]:
            continue
        for rl in r["off"]["relocks"]:
            emit(
                "  [seed %d] f=%-6d line %.2f -> %.2f (%+.2f) ruck=%s "
                "bc_x=%.2f prev_mode=%s mode=%s live=%s prev_anchor=%s%s"
                % (
                    r["seed"], rl["frame"], rl["prev_line"], rl["line"],
                    rl["forward"],
                    ("%.2f" % rl["ruck_x"])
                    if rl["ruck_x"] is not None else "None",
                    rl["bc_x"], rl["prev_mode"], rl["mode"], rl["live_kind"],
                    rl["prev_anchor_kind"],
                    "  <-- CHASE ENDED"
                    if (rl["was_chase"] and not rl["is_chase"]) else "",
                )
            )
    emit("")

    # ── the recorded instance, matched on the ANCHOR PAIR (not the frame
    #    number): the 18.25 -> 36.52 relock IS the recorded f=383 signature.
    emit("-" * 70)
    emit("THE RECORDED INSTANCE (the %.2f -> %.2f relock, recorded f=%d)"
         % (RECORD_PREV_LINE, RECORD_LINE, RECORD_FRAME))
    emit("-" * 70)
    matched = [
        (r["seed"], rl) for r in runs for rl in r["off"]["relocks"]
        if abs(rl["prev_line"] - RECORD_PREV_LINE) < 0.05
        and abs(rl["line"] - RECORD_LINE) < 0.05
    ]
    if not matched:
        emit("  NOT FOUND on the lever-OFF arm -- reported honestly.")
        emit("  (searched for a %.2f -> %.2f relock)"
             % (RECORD_PREV_LINE, RECORD_LINE))
    for seed, rl in matched:
        emit(
            "  seed %-9s f=%-6d line %.2f -> %.2f  ruck=%s  bc_x=%.2f "
            "(recorded %.2f)"
            % (seed, rl["frame"], rl["prev_line"], rl["line"],
               ("%.2f" % rl["ruck_x"]) if rl["ruck_x"] is not None else "None",
               rl["bc_x"], RECORD_BC_X)
        )
        emit("      prev_mode=%s mode=%s" % (rl["prev_mode"], rl["mode"]))
        emit("      verdict under the MOVED anchor : %s" % rl["live_kind"])
        emit("      verdict under the PREV anchor  : %s"
             % rl["prev_anchor_kind"])
        emit("      -> CHASE %s"
             % ("LOST by the relock"
                if (rl["was_chase"] and not rl["is_chase"])
                else "not lost here (%s -> %s)"
                % (rl["prev_mode"], rl["mode"])))
        on_here = [
            orl for r in runs for orl in r["on"]["relocks"]
            if r["seed"] == seed and orl["frame"] == rl["frame"]
        ]
        if on_here:
            emit("      lever ON at the same frame: %s -> %s (chase %s)"
                 % (on_here[0]["prev_mode"], on_here[0]["mode"],
                    "preserved" if on_here[0]["is_chase"] else "ended"))
        else:
            emit("      lever ON: no relock recorded at this frame")
    emit("")

    # ── verdict ──
    emit("=" * 70)
    ok = (off_losses > 0 and on_losses == 0)
    if ok:
        emit("VERDICT: CONFIRMED.")
        emit("  A FORWARD anchor relock ended %d live fullback chase(s) on the"
             % off_losses)
        emit("  pre-change engine, with the carrier never losing the break.")
        emit("  With the latch armed, ZERO chases end that way: the break test")
        emit("  reads the anchor latched when the chase was armed, so the")
        emit("  relock cannot withdraw a verdict already reached.")
    else:
        emit("VERDICT: NOT CONFIRMED AS PREDICTED.")
        emit("  OFF losses = %d (expected > 0), ON losses = %d (expected 0)."
             % (off_losses, on_losses))
    emit("=" * 70)
    return ok


def main():
    emit("FULLBACK ANCHOR-RELOCK CHASE PROBE (multi-seed A/B, 2026-09-28)")
    emit("=" * 70)
    emit("seeds   : %s" % (", ".join(str(s) for s in SEEDS),))
    emit("frames  : %d per arm" % MAX_FRAMES)
    emit("relock epsilon: %.2f m" % EPS)
    emit("")
    emit("SWEEP")
    emit("-" * 70)
    runs = sweep()
    report(runs)
    emit("")
    emit("report written to %s" % REPORT_PATH)

    try:
        with io.open(REPORT_PATH, "w", encoding="utf-8") as handle:
            handle.write("\n".join(_OUT) + "\n")
    except OSError as exc:
        print("[WARN] could not write report: %r" % (exc,), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
