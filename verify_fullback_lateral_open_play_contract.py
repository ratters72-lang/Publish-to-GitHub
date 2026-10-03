"""verify_fullback_lateral_open_play_contract.py — the OPEN-PLAY LATERAL
MIRRORING + 2D ERROR contract (2026-09-22).

The dedicated verifier for the cure documented in
``FULLBACK_LATERAL_OPEN_PLAY_REPORT.md`` and quantified live by
``probe_fullback_lateral_tracking.py``.  It pins five things:

  [1] THE LEVER AND ITS CONSTANTS.  ``FB_LATERAL_TRACK_OPEN_PLAY`` is full
      weight (1.0), the loose-ball ``LATERAL_TRACK`` stays 0.6, the shipped
      default is ON, and the documented rollback
      ``RLM_FB_LATERAL_OPEN_PLAY=0`` really does re-arm the old 0.6 rail in a
      FRESH interpreter (checked in a subprocess, so a module already imported
      with the default cannot mask a broken reader).

  [2] THE ANCHOR MIRRORS THE CARRIER'S LANE in open play and sweeps the 0.6
      rail for the loose ball.  Depth is untouched, and the kickoff override
      is untouched.

  [3] THE WRITER WRITES THE LANE.  ``apply_fullback_geometry`` targets the
      carrier's own lane (not a rail), and the error it feeds the speed
      helpers is the 2D distance to that target.

  [4] THE BREAK / STEP-OUT GATE IS 2D.  A DEPTH break (the historical X test)
      OR a LATERAL channel breach inside ``FB_LATERAL_BREAK_RADIUS_M``; a wide
      carrier outside the reach does NOT open it.

  [5] SOURCE WIRING — THE 2D METRIC IS SINGLE-SOURCED.  ``fullback_role``
      defines ``fullback_target_distance`` exactly once and feeds it to its
      own approach / recovery-speed helpers; no X-only ``dx_to_anchor``
      parameter survives on them.

      THE FB SPEED HELPERS ARE NO LONGER IMPORTED BY THE DISPATCHER OR THE
      KICKOFF CHASE (consolidated 2026-09-27).  The engine owns only the
      actor's STATE and TARGET; the ACTOR'S PACE now comes from the shared
      movement-state table via
      ``player.movement_states.apply_fullback_locomotion``, a pure consumer
      that integrates with ``preserve_state=True``.  The pre-consolidation
      version of this section asserted the dispatcher / kickoff IMPORTS, which
      had to be dropped when the duplicate speed writers were removed.  The
      checks below pin the consolidation itself, so a second speed authority
      cannot silently grow back.

  [6] LIVE END-TO-END.  A bounded seeded rollout asserts the shipped default
      leaves ZERO destination gap to the carrier lane, and that the rollback
      lever still produces a non-zero gap (so the A/B in the probe is real).
      Frames: RLM_FB_OPEN_PLAY_LIVE_FRAMES (default 300 per leg).
"""
import io
import math
import os
import subprocess
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _line_anchor_m,
)
from game.defence.defence_lvl_1.fullback_role import (
    apply_fullback_geometry,
    classify_fullback_break,
    compute_fullback_anchor,
    fullback_target_distance,
    FB_BREAK_DEPTH,
    FB_BREAK_LATERAL,
    FB_BREAK_NONE,
    FB_LATERAL_BREAK_M,
    FB_LATERAL_BREAK_RADIUS_M,
    FB_LATERAL_OPEN_PLAY,
    FB_LATERAL_TRACK_OPEN_PLAY,
    FB_MAX_APPROACH_SPEED_MPS,
    FIELD_CENTRE_Y,
    LATERAL_TRACK,
)

FAILURES = []


def check(label, condition, detail=""):
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}"
          + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def section(n, title):
    print(f"\n[{n}] {title}")


class _Ball:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _World:
    """Minimal world stub carrying the fields the FB authority reads."""

    def __init__(self, anchor=70.0, ruck_x=60.0, bc=None, frame=1):
        self.defensive_line_anchor = anchor
        self.ruck_x_m = ruck_x
        self.ball_carrier = bc
        self.fullback_recover_allowed = True
        self.frame_count = frame
        self.tes_active = False
        self.in_kickoff = False


def make_fb(x, y, player_id=201):
    from player.player import Player

    p = Player(x=x, y=y, player_id=player_id)
    p.jersey_number = 1
    p.max_speed_mps = 9.2
    p.max_defensive_speed_mps = 8.3
    p.fullback_role_active = True
    p.tes_attached = False
    p.in_contact = False
    return p


# ══════════════════════════════════════════════════════════════════════
# [1] THE LEVER AND ITS CONSTANTS
# ══════════════════════════════════════════════════════════════════════

def sec_lever():
    section(1, "The open-play lever, its weights, and the shipped default")
    check("FB_LATERAL_TRACK_OPEN_PLAY is FULL weight (1.0)",
          abs(FB_LATERAL_TRACK_OPEN_PLAY - 1.0) < 1e-9,
          str(FB_LATERAL_TRACK_OPEN_PLAY))
    check("LATERAL_TRACK stays the LOOSE-BALL rail (0.6)",
          abs(LATERAL_TRACK - 0.6) < 1e-9,
          str(LATERAL_TRACK))
    check("the two weights are different (the mirror is not a no-op)",
          FB_LATERAL_TRACK_OPEN_PLAY > LATERAL_TRACK)
    check("the shipped default has open-play mirroring ENABLED",
          FB_LATERAL_OPEN_PLAY is True,
          f"FB_LATERAL_OPEN_PLAY={FB_LATERAL_OPEN_PLAY}")

    # The rollback lever must work in a FRESH interpreter: this process
    # already imported the module with the default, so only a subprocess can
    # prove the env reader is wired.
    code = (
        "import sys; sys.path.insert(0, '.');"
        "from game.defence.defence_lvl_1.fullback_role import "
        "FB_LATERAL_OPEN_PLAY as L; print('LEVER=%r' % (L,))"
    )
    for env_value, expected in (("0", False), ("1", True)):
        env = dict(os.environ)
        env["RLM_FB_LATERAL_OPEN_PLAY"] = env_value
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", code],
            capture_output=True, text=True, env=env,
        )
        out = [ln for ln in (proc.stdout or "").splitlines()
               if ln.startswith("LEVER=")]
        got = out[-1].strip() if out else "(no output)"
        check(f"RLM_FB_LATERAL_OPEN_PLAY={env_value} -> "
              f"FB_LATERAL_OPEN_PLAY is {expected}",
              got == f"LEVER={expected!r}",
              f"{got}  stderr={((proc.stderr or '').strip()[:80])!r}")


# ══════════════════════════════════════════════════════════════════════
# [2] THE ANCHOR MIRRORS THE CARRIER'S LANE
# ══════════════════════════════════════════════════════════════════════

def sec_anchor_matrix():
    section(2, "Anchor geometry — open-play mirror vs loose-ball rail")
    line_x = 70.0

    for bc_y in (0.0, 12.0, FIELD_CENTRE_Y, 48.0, 62.0, 70.0):
        _ax, ay_open = compute_fullback_anchor(_Ball(62.0, bc_y), line_x)
        check(f"open play mirrors the carrier lane exactly "
              f"(bc.y={bc_y:.0f} -> anchor_y={bc_y:.0f})",
              abs(ay_open - bc_y) < 1e-9,
              f"anchor_y={ay_open:.4f}")

    for bc_y, expected in ((0.0, 14.0), (70.0, 56.0)):
        _ax, ay_rail = compute_fullback_anchor(
            _Ball(62.0, bc_y), line_x, open_play=False
        )
        check(f"the loose ball keeps the 0.6 rail "
              f"(bc.y={bc_y:.0f} -> anchor_y={expected:.0f})",
              abs(ay_rail - expected) < 1e-9,
              f"anchor_y={ay_rail:.4f}")

    _ax, ay_centre = compute_fullback_anchor(
        _Ball(62.0, FIELD_CENTRE_Y), line_x, open_play=False
    )
    check("a centred loose ball anchors on field centre (both regimes agree)",
          abs(ay_centre - FIELD_CENTRE_Y) < 1e-9)

    # DEPTH IS UNTOUCHED: the two regimes share the same anchor X.
    ax_open, _ = compute_fullback_anchor(_Ball(50.0, 10.0), 60.0)
    ax_rail, _ = compute_fullback_anchor(_Ball(50.0, 10.0), 60.0,
                                        open_play=False)
    check("depth is identical in both regimes (only Y changes)",
          abs(ax_open - ax_rail) < 1e-12,
          f"open={ax_open:.4f} rail={ax_rail:.4f}")

    # THE KICKOFF OVERRIDE IS UNTOUCHED (side_sign only, no carrier lane).
    ax_k, ay_k = compute_fullback_anchor(
        _Ball(10.0, 10.0), 60.0, in_kickoff=True,
        formation_line_x=62.0, side_sign=1,
    )
    check("kickoff override still ignores the carrier lane",
          abs(ax_k - 62.0) < 1e-9
          and abs(ay_k - (FIELD_CENTRE_Y + 3.0)) < 1e-9,
          f"anchor=({ax_k:.2f},{ay_k:.2f})")


# ══════════════════════════════════════════════════════════════════════
# [3] THE WRITER WRITES THE LANE, AND MEASURES IT IN 2D
# ══════════════════════════════════════════════════════════════════════

def sec_writer():
    section(3, "Geometry writer — the lane is the destination")
    # The actor is 35 m off the lane and outside the 2D break reach, so the
    # sweeper path (not CHASE) is the owner of the target.
    bc = _Ball(62.0, 0.0)
    world = _World(anchor=70.0, bc=bc, frame=11)
    actor = make_fb(86.0, 35.0, player_id=301)
    apply_fullback_geometry(world, actor)
    check("the writer targets the carrier's own lane (not a 0.6 rail)",
          abs(actor.target_y - bc.y) < 1e-9,
          f"target_y={actor.target_y:.4f} carrier lane={bc.y:.4f}")
    check("the writer did NOT sweep to a rail 14 m short of the lane",
          abs(actor.target_y - 14.0) > 13.0,
          f"target_y={actor.target_y:.4f}")

    # ON the lane: the writer leaves the actor to glide, still at the lane.
    on_lane = make_fb(86.0, 0.0, player_id=302)
    apply_fullback_geometry(_World(anchor=70.0, bc=bc, frame=12), on_lane)
    check("an actor already on the lane is not sent back to a rail",
          abs(on_lane.target_y - bc.y) < 1e-9,
          f"target_y={on_lane.target_y:.4f}")

    # THE 2D METRIC.  Distance grows with Y exactly as it grows with X.
    probe = make_fb(60.0, 35.0)
    check("fullback_target_distance is the 2D hypot",
          abs(fullback_target_distance(probe, 70.0, 45.0)
              - math.hypot(10.0, 10.0)) < 1e-9)
    check("and it collapses to |dx| when the target is on the lane",
          abs(fullback_target_distance(probe, 70.0, 35.0) - 10.0) < 1e-9)
    check("a body 8 m off in Y is 8 m of real error, not 0",
          abs(fullback_target_distance(probe, 60.0, 43.0) - 8.0) < 1e-9)


# ══════════════════════════════════════════════════════════════════════
# [4] THE BREAK / STEP-OUT GATE IS 2D
# ══════════════════════════════════════════════════════════════════════

def sec_gate():
    section(4, "Break gate — DEPTH or a LATERAL channel breach, inside reach")
    anchor_x, anchor_y = 70.0, 35.0
    actor = make_fb(60.0, 35.0, player_id=401)

    check("the channel-breach threshold is the documented 5 m",
          abs(FB_LATERAL_BREAK_M - 5.0) < 1e-9, str(FB_LATERAL_BREAK_M))
    check("the 2D reach guard is the documented 25 m",
          abs(FB_LATERAL_BREAK_RADIUS_M - 25.0) < 1e-9,
          str(FB_LATERAL_BREAK_RADIUS_M))

    # DEPTH: the historical X test, unchanged.
    kind = classify_fullback_break(_Ball(anchor_x + 1.01, 35.0), actor,
                                   anchor_x, anchor_y)
    check("a carrier past the anchor line is a DEPTH break",
          kind == FB_BREAK_DEPTH, str(kind))
    kind = classify_fullback_break(_Ball(anchor_x + 0.5, 35.0), actor,
                                   anchor_x, anchor_y)
    check("a carrier level with the line, ON the lane, is no break",
          kind == FB_BREAK_NONE, str(kind))

    # LATERAL: level in X, outside the channel, inside the 2D reach.
    kind = classify_fullback_break(_Ball(anchor_x, 45.0), actor,
                                   anchor_x, anchor_y)
    check("a carrier 10 m off the lane, inside 2D reach, is a LATERAL break",
          kind == FB_BREAK_LATERAL, str(kind))
    kind = classify_fullback_break(_Ball(anchor_x, 39.0), actor,
                                   anchor_x, anchor_y)
    check("a carrier inside the 5 m channel is no break",
          kind == FB_BREAK_NONE, str(kind))

    # REACH GUARD: wide but far away -> the line's problem, not the sweeper's.
    kind = classify_fullback_break(_Ball(anchor_x, 70.0), actor,
                                   anchor_x, anchor_y)
    check("a wide carrier OUTSIDE the 2D reach is no break",
          kind == FB_BREAK_NONE,
          f"reach={math.hypot(anchor_x - 60.0, 70.0 - 35.0):.1f} m")

    # THE CHANNEL REFERENCE.  Under mirroring it is the defender's OWN lane;
    # under the rollback lever it is the caller's ``anchor_y`` — the rail the
    # writer computed — so the IDENTICAL carrier is judged against a
    # different line.  Both readings are pinned.
    saved = fb.FB_LATERAL_OPEN_PLAY
    try:
        fb.FB_LATERAL_OPEN_PLAY = True
        kind_mirror = classify_fullback_break(_Ball(anchor_x, 45.0), actor,
                                              anchor_x, anchor_y)
        fb.FB_LATERAL_OPEN_PLAY = False
        kind_rail_lane = classify_fullback_break(_Ball(anchor_x, 45.0), actor,
                                                 anchor_x, anchor_y)
        # The rail the writer would have handed this carrier: 35 + 10*0.6.
        kind_rail_rail = classify_fullback_break(_Ball(anchor_x, 45.0), actor,
                                                 anchor_x, 41.0)
    finally:
        fb.FB_LATERAL_OPEN_PLAY = saved
    check("mirroring: the channel reference is the defender's own lane, so a "
          "10 m off-lane carrier inside reach is a LATERAL break",
          kind_mirror == FB_BREAK_LATERAL, str(kind_mirror))
    check("rollback lever: the channel reference is the caller's anchor_y, so "
          "the same carrier stops being a break once that anchor is the rail",
          kind_rail_lane == FB_BREAK_LATERAL
          and kind_rail_rail == FB_BREAK_NONE,
          f"anchor_y=35 -> {kind_rail_lane}, anchor_y=41 -> {kind_rail_rail}")


# ══════════════════════════════════════════════════════════════════════
# [5] SOURCE WIRING — THE 2D METRIC IS SINGLE-SOURCED, AND THE SWEEPER'S
#     PACE BELONGS TO THE SHARED MOVEMENT-STATE TABLE
# ══════════════════════════════════════════════════════════════════════

def _src(rel_path):
    with open(rel_path, encoding="utf-8") as fh:
        return fh.read()


def sec_wiring():
    section(5, "Source wiring — the 2D metric is single-sourced")
    fr = _src("game/defence/defence_lvl_1/fullback_role.py")
    dbu = _src("game/defence/defence_lvl_1/defensive_behaviour_unified.py")
    ke = _src("game/kickoff_engine.py")
    ms = _src("player/movement_states.py")

    check("fullback_role defines the 2D metric",
          "def fullback_target_distance" in fr)
    check("fullback_role feeds the metric to its own speed helper",
          "fullback_target_distance(" in fr
          and "fullback_approach_speed(" in fr)
    check("no X-only error parameter survives on the helpers",
          "dx_to_anchor" not in fr)
    check("both helpers now name distance_to_target",
          fr.count("distance_to_target") >= 2)

    # ── SINGLE SOURCE ──
    # The metric must exist in exactly ONE module.  A second ``def`` in the
    # dispatcher or the kickoff engine is precisely the re-implementation this
    # contract forbids, so the count is pinned rather than each module being
    # merely mentioned.
    _defs = sum(text.count("def fullback_target_distance")
                for text in (fr, dbu, ke))
    check("the 2D metric is defined in exactly one module",
          _defs == 1,
          f"{_defs} definition(s) across fullback_role/dbu/kickoff_engine")

    # ── THE 2026-09-27 CONSOLIDATION, PINNED ──
    # The dispatcher and the kickoff chase each used to write the sweeper's
    # move_speed through ``fullback_approach_speed``.  They now write only
    # STATE + TARGET; PACE belongs to the shared table.  Asserting the
    # absence of the CALL (the drop is documented in a comment, so a plain
    # substring test would match that note) keeps the duplicate writer from
    # creeping back in unnoticed.
    check("the dispatcher no longer calls an FB speed helper of its own",
          "fullback_approach_speed(" not in dbu)
    check("the kickoff chase no longer calls an FB speed helper of its own",
          "fullback_approach_speed(" not in ke)

    # ── THE SHARED PACE AUTHORITY ──
    check("player.movement_states owns the FB pace integrator",
          "def apply_fullback_locomotion" in ms)
    check("the pace integrator consumes the authored state without writing it",
          "preserve_state=True" in ms)

    # Behavioural, not textual: a body with no authored MovementState must be
    # DECLINED by the integrator so the caller can fall back, never silently
    # integrated through a guessed state.
    from player.movement_states import apply_fullback_locomotion

    class _NoState:
        """A stub body: no ``movement_state`` attribute at all."""

    check("the pace integrator declines a body with no authored state",
          apply_fullback_locomotion(_NoState(), 1.0 / 60.0) is False)


# ══════════════════════════════════════════════════════════════════════
# [6] LIVE END-TO-END — ZERO DESTINATION GAP, AND THE LEVER STILL BITES
# ══════════════════════════════════════════════════════════════════════

def _run_leg(max_frames, open_play):
    """Bounded seeded rollout; returns max |bc.y - anchor_y| over FB frames."""
    saved = fb.FB_LATERAL_OPEN_PLAY
    fb.FB_LATERAL_OPEN_PLAY = bool(open_play)

    os.environ["SDL_VIDEODRIVER"] = "dummy"
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

    import random
    random.seed(20260827)

    field = Field(1920, 1080)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball
    engine = OpenPlayEngine(world)
    world.open_play = engine
    world.open_play_engine = engine
    kickoff_engine = KickoffEngine(world, ball, field)
    collisions = CollisionSystem()

    frames = 0
    max_gap = 0.0
    dt = 1.0 / 60.0
    try:
        kickoff_engine.reset_for_kickoff()
        apply_receiving_kickoff_formation(world, field)
        apply_kicking_team_formation(world, field)
        world.ball_owner = "NONE"
        world.ball_carrier = None
        world.ball.possessed_by = None
        kickoff_engine.kickoff()

        for frame in range(1, max_frames + 1):
            world.frame_count = frame
            ball.update(dt)
            if world.phase == GamePhase.KICKOFF:
                kickoff_engine.update(dt)
                check_for_catch(world, ball, dt)
            elif world.phase == GamePhase.CATCH:
                if world.ball_carrier is not None:
                    world.open_play.start(world.ball_carrier)
                world.phase = GamePhase.OPEN_PLAY
            elif world.phase == GamePhase.OPEN_PLAY:
                engine.update(dt)
                if not world.tes_active:
                    collisions.resolve_all(world.players, world=world)
            else:
                continue

            tagged = [p for p in world.defending_team
                      if getattr(p, "fullback_role_active", False)]
            if len(tagged) != 1:
                continue
            bc = getattr(world, "ball_carrier", None)
            if bc is None:
                continue
            frames += 1
            la = float(_line_anchor_m(world))
            _ax, anchor_y = compute_fullback_anchor(bc, la)
            gap = abs(float(bc.y) - anchor_y)
            if gap > max_gap:
                max_gap = gap
    finally:
        fb.FB_LATERAL_OPEN_PLAY = saved
        pygame.quit()

    return frames, max_gap


def _muted(run):
    """Run *run* with the engine's per-frame debug chatter discarded."""
    real = sys.stdout
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )
    try:
        return run()
    finally:
        sys.stdout = real


def sec_live():
    section(6, "LIVE — the shipped default leaves zero destination gap")
    max_frames = int(os.environ.get("RLM_FB_OPEN_PLAY_LIVE_FRAMES", "300"))
    legs = _muted(lambda: (
        _run_leg(max_frames, open_play=True),
        _run_leg(max_frames, open_play=False),
    ))
    (frames_after, gap_after), (frames_before, gap_before) = legs

    check("the AFTER leg reached fullback frames",
          frames_after > 0, f"{frames_after} frames")
    check("the BEFORE leg reached fullback frames",
          frames_before > 0, f"{frames_before} frames")
    check("AFTER: the destination gap to the carrier lane is exactly 0",
          gap_after < 1e-9, f"max gap={gap_after:.4f} m")
    check("BEFORE (rollback lever): the destination gap is NON-ZERO, so the "
          "A/B is real",
          gap_before > 0.0, f"max gap={gap_before:.4f} m")


def sec_zones():
    section(7, "The 4-zone lateral sweeper matrix and its try-line enforcement")

    check("the matrix declares FOUR zones",
          fb.FB_LATERAL_ZONE_COUNT == 4, str(fb.FB_LATERAL_ZONE_COUNT))
    check("the zone edges tile the anchor strip with no gap",
          fb.FB_LATERAL_ZONE_EDGES_M[0] == fb.LEFT_TRYLINE_X
          and fb.FB_LATERAL_ZONE_EDGES_M[-1] == fb.FIELD_MAX_X
          and len(fb.FB_LATERAL_ZONE_EDGES_M) == fb.FB_LATERAL_ZONE_COUNT + 1,
          f"{fb.FB_LATERAL_ZONE_EDGES_M}")
    check("there is exactly one declared weight per zone",
          len(fb.FB_LATERAL_ZONE_WEIGHTS) == fb.FB_LATERAL_ZONE_COUNT,
          f"{fb.FB_LATERAL_ZONE_WEIGHTS}")
    check("zone 0 is the try-line enforcement zone",
          fb.FB_LATERAL_ZONE_TRYLINE == 0,
          str(fb.FB_LATERAL_ZONE_TRYLINE))

    # The shipped default must be BEHAVIOUR-NEUTRAL: every zone ships at the
    # same full weight the single rail already applied in open play, so no
    # frame's geometry moves until a tuner dials a fluid zone.
    check("every SHIPPED zone weight is the full open-play weight",
          all(abs(w - FB_LATERAL_TRACK_OPEN_PLAY) < 1e-12
              for w in fb.FB_LATERAL_ZONE_WEIGHTS),
          f"{fb.FB_LATERAL_ZONE_WEIGHTS}")

    # The intervals are inclusive-low / exclusive-high, so a position exactly
    # on a boundary belongs to the zone above it.
    for x, expected in ((10.0, 0), (20.0, 0), (35.4999, 0),
                        (35.5, 1), (50.0, 1), (60.9999, 1),
                        (61.0, 2), (80.0, 2), (86.4999, 2),
                        (86.5, 3), (100.0, 3), (112.0, 3)):
        check(f"zone({x:.4f}) is {expected}", fb.fullback_lateral_zone(x)
              == expected, str(fb.fullback_lateral_zone(x)))

    # TOTAL: an unreadable position reports the ordinary-backfield zone rather
    # than inventing a try-line position the caller never asked for.
    for junk in (None, "x", float("nan"), float("inf")):
        check(f"zone({junk!r}) falls back to the farthest zone",
              fb.fullback_lateral_zone(junk)
              == fb.FB_LATERAL_ZONE_COUNT - 1,
              str(fb.fullback_lateral_zone(junk)))

    # ── THE CRITICAL LATERAL SWEEPER LAW, ENFORCED ──
    saved = fb.FB_LATERAL_ZONE_WEIGHTS
    try:
        fb.FB_LATERAL_ZONE_WEIGHTS = (0.1, 0.2, 0.3, 0.4)
        check("a try-line weight of 0.1 is FLOORED to full sweep",
              abs(fb.fullback_lateral_zone_weight(10.0) - 1.0) < 1e-12,
              str(fb.fullback_lateral_zone_weight(10.0)))
        check("a fluid zone's declared weight is returned EXACTLY",
              abs(fb.fullback_lateral_zone_weight(80.0) - 0.3) < 1e-12,
              str(fb.fullback_lateral_zone_weight(80.0)))
        # Zones 1..3 are FLUID: 50.0 is zone 1, 80.0 is zone 2 and 100.0 is
        # zone 3, so each must return its declared weight untouched.  (20.0
        # is NOT used here: it lands in zone 0, which is floored on purpose.)
        check("the enforcement never touches the fluid zones",
              abs(fb.fullback_lateral_zone_weight(50.0) - 0.2) < 1e-12
              and abs(fb.fullback_lateral_zone_weight(100.0) - 0.4) < 1e-12,
              "%s / %s" % (fb.fullback_lateral_zone_weight(50.0),
                           fb.fullback_lateral_zone_weight(100.0)))
        fb.FB_LATERAL_ZONE_WEIGHTS = (1.5, 1.0, 1.0, 1.0)
        check("an over-sweep ABOVE the floor is honoured",
              abs(fb.fullback_lateral_zone_weight(10.0) - 1.5) < 1e-12,
              str(fb.fullback_lateral_zone_weight(10.0)))
    finally:
        fb.FB_LATERAL_ZONE_WEIGHTS = saved

    # ── THE ONE-ARG COMPATIBILITY CONTRACT ──
    check("the one-argument call is UNCHANGED in open play",
          fb._lateral_track_weight(True) == FB_LATERAL_TRACK_OPEN_PLAY,
          str(fb._lateral_track_weight(True)))
    check("the one-argument call is UNCHANGED for the loose ball",
          fb._lateral_track_weight(False) == LATERAL_TRACK,
          str(fb._lateral_track_weight(False)))
    check("the zone-aware try-line weight matches the one-arg weight",
          fb._lateral_track_weight(True, 12.0)
          == fb._lateral_track_weight(True))
    check("the loose-ball rail ignores the zone entirely",
          fb._lateral_track_weight(False, 12.0) == LATERAL_TRACK,
          str(fb._lateral_track_weight(False, 12.0)))

    # ── THE WIRING: the anchor really consults the matrix ──
    _ax, ay = compute_fullback_anchor(_Ball(20.0, 5.0), 12.0)
    check("the try-line anchor mirrors the carrier's lane at FULL weight",
          abs(ay - 5.0) < 1e-9, f"anchor_y={ay:.6f}")
    _ax2, ay2 = compute_fullback_anchor(_Ball(20.0, 5.0), 12.0,
                                        open_play=False)
    check("the loose-ball anchor still keeps the 0.6 rail",
          abs(ay2 - (FIELD_CENTRE_Y
                     + (5.0 - FIELD_CENTRE_Y) * LATERAL_TRACK)) < 1e-9,
          f"anchor_y={ay2:.6f}")


def main():
    print("FULLBACK OPEN-PLAY LATERAL CONTRACT (2026-09-22)")
    print("=" * 68)
    sec_lever()
    sec_anchor_matrix()
    sec_writer()
    sec_gate()
    sec_wiring()
    sec_zones()
    sec_live()
    print("\n" + "=" * 68)
    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s))")
        for label in FAILURES:
            print(f"  - {label}")
        return 1
    print("RESULT: PASS — open play mirrors the carrier's lane at full "
          "weight, the")
    print("        speed commands are keyed on the 2D distance to that lane, "
          "the")
    print("        break gate is 2D (depth OR lateral, inside the reach), and "
          "the")
    print("        RLM_FB_LATERAL_OPEN_PLAY=0 rollback restores the 0.6 rail.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
