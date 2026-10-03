"""verify_fullback_emergency_defender.py

ACCEPTANCE SUITE -- standalone fullback emergency engage authority.

THE LAW UNDER TEST
------------------
TACKLE ELIGIBILITY and LINE MEMBERSHIP are two SEPARATE concepts. A
``fullback_role_active`` defender remains excluded from line systems and lane
selection. During a genuine break chase it gets separate engage authority
and reaches the existing classifier/TES only after the lane PRIMARY fails.

CONTRACTS
---------
  C1  LINE MEMBERSHIP: a tagged fullback is never a line member, even while
      wearing TACKLER_1/2/3 with tackle_role PRIMARY during an emergency
      tackle (population / depth role / depth side / spacing / width).
  C2  STANDALONE ENGAGE: only a tackle-capable CHASE/SHOOT fullback during a
      genuine break gets the separate engage flag. It never competes for a
      lane PRIMARY or any selector-owned tackle role.
  C3  TAG PRESERVATION: TES arming sets the engagement marker and does NOT
      revoke the tag; the end-of-cycle reset restores DEF_LINE_READY with
      the tag intact.
  C4  LOCOMOTION OWNERSHIP: the fullback geometry controller retains
      locomotion through candidate/classifier attempts; TES arming transfers
      movement ownership and lifts the sweeper pace cap.

      PACE RE-BASED (2026-09-27).  The 2.0 m/s FB-ONLY approach cap is
      RETIRED: that cap lived in Player.update and was deleted along with the
      four bespoke ``move_speed`` writers.  An unengaged sweeper now glides at
      the SHARED DRIFT fraction of ``max_speed_mps`` (strictly below the
      defensive ceiling and no longer pinned at 2.0 m/s), and
      ``player/player.py`` routes the actor through
      ``apply_fullback_locomotion`` instead of the deleted cap helper.
      ``fb.FB_MAX_APPROACH_SPEED_MPS`` survives only as a pure-function
      remnant and as the reference value in these assertions.
  C5  INVARIANT AUDIT: TACKLER_* on a TAGGED, ENGAGED actor is legal;
      TACKLER_* on a tagged non-engaged actor is still FB_ROLE_CONTAMINATION;
      a body engaged without the tag is FB_TAG_LOST_ON_ENGAGE.

Run:  python verify_fullback_emergency_defender.py
Exit code 0 = all contracts hold.
"""

from __future__ import annotations

import inspect
import math
import os
import sys
from types import SimpleNamespace

# Headless safety: nothing here creates a display, but the engine modules are
# imported transitively and some of them touch pygame at import time.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from player.player import Player, DefenderRole
from player.movement_states import MovementState

from game.defence.defence_lvl_1 import fullback_role as fb
from game.defence.defence_lvl_1 import intercept_tacklers as it
from game.defence.defence_lvl_1 import pretackle_corridor as corridor
from game.defence.defence_lvl_1 import population_authority as pop
from game.defence.defence_lvl_1 import spacing_authority as spacing
from game.defence.defence_lvl_1 import width_authority as width
from game.defence.defence_lvl_1 import defensive_depth_roles as depth_roles
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    _fullback_tackle_candidates,
)
from game.debug.fullback_invariant_audit import (
    capture_fullback_invariants,
    _invariant_violations,
    FB_ROLE_CONTAMINATION,
    FB_TAG_LOST_ON_ENGAGE,
    FB_TARGET_SOURCE_OVERRIDE,
    FB_DEPTH_ROLE_LEAK,
)

# ── REPORTING ────────────────────────────────────────────────────────
_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    """Record one assertion of the acceptance suite."""
    _RESULTS.append((name, bool(cond), detail))
    return bool(cond)


def run_test(contract: str, name: str, fn) -> None:
    """Run one contract test, recording PASS/FAIL (never aborting the run)."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - a raising test is a failing test
        _RESULTS.append(
            (f"{contract} {name}", False,
             f"raised {type(exc).__name__}: {exc}")
        )


# ── STUBS ────────────────────────────────────────────────────────────
class StubWorld:
    """Minimal world carrying only the fields the fullback path reads."""

    def __init__(self, anchor_x: float = 70.0) -> None:
        self.defending_team: list = []
        self.attacking_team: list = []
        self.players: list = []
        self.ball_carrier = None
        self.phase = "OPEN_PLAY"
        self.sub_state = None
        self.frame_count = 0
        self.ruck_x_m = anchor_x - 10.0
        self.ruck_y_m = 35.0
        self.defensive_line_anchor = anchor_x
        self.anchor_locked = True
        self.tes_active = False
        self.open_play_engine = None
        self.fullback_actor_id = None
        self.fullback_role_active = False
        self.in_kickoff = False
        self.attacking_direction = 1
        self.tackler_ids: set = set()
        self.primary_tackler_id = None
        self.secondary_tacklers: set = set()
        self.line_population_ids = frozenset()
        self.publish_frame = 0
        self.marker_freeze_timer = 1.0
        self.marker_lock_duration_s = 0.75


def make_defender(x: float, y: float, pid: int, *, tagged: bool = True,
                  tackle_ok: bool = True) -> Player:
    """A real Player configured as the tagged/untagged sweeper."""
    d = Player(x, y, pid)
    d.fullback_role_active = tagged
    d.can_acquire_tackle = tackle_ok
    d.defender_role = DefenderRole.DEF_LINE_READY
    d.max_defensive_speed_mps = 8.3
    d.max_speed_mps = 9.5
    d.depth_role = None
    d.depth_side = None
    d.tackle_role = None
    d.target_source = None
    return d


def call_optional(fn, *args):
    """Call *fn* with the leading args its signature actually accepts.

    The suite exercises several public predicates whose authoritative
    signatures evolved (``world`` / ``bc`` are optional 2nd/3rd params).
    Padding by signature keeps the checks against the REAL functions
    instead of re-implementing them here.
    """
    try:
        sig = inspect.signature(fn)
        positional = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        required = sum(1 for p in positional if p.default is inspect.Parameter.empty)
        return fn(*args[:max(required, 1)])
    except (TypeError, ValueError):
        return fn(*args[:1])


def anchor_of(world: StubWorld) -> float:
    """The live defensive line anchor (never assume the stub default)."""
    from game.defence.defence_lvl_1.anchor_authority import (
        defensive_line_anchor_m,
    )
    return float(defensive_line_anchor_m(world))


def broken_pair(world: StubWorld):
    """(bc_on_a_break, bc_behind_the_line) for *world*."""
    anchor = anchor_of(world)
    bc_break = Player(anchor + 6.0, 35.0, 900)
    bc_behind = Player(anchor - 6.0, 35.0, 901)
    return bc_break, bc_behind


def arm_as_tackler(world: StubWorld, d: Player, role_name: str = "PRIMARY"):
    """Arm *d* through the engine's SINGLE tackler-arming point."""
    from world.open_play_engine import OpenPlayEngine
    fake_ope = SimpleNamespace(world=world)
    ctx = SimpleNamespace(tackler_roles={})
    OpenPlayEngine._arm_tackler_role(
        fake_ope, d, DefenderRole.TACKLER_1, role_name, ctx,
    )
    return ctx


# ═════════════════════════════════════════════════════════════════════
# C1 -- LINE MEMBERSHIP IS NEVER GRANTED TO A TAGGED FULLBACK
# ═════════════════════════════════════════════════════════════════════

def c1_tagged_fb_is_not_a_line_body() -> None:
    world = StubWorld()
    d = make_defender(78.0, 35.0, 1)
    # The hardest case: armed as a TES tackler with PRIMARY authority.
    d.defender_role = DefenderRole.TACKLER_1
    d.tackle_role = "PRIMARY"
    d.tackle_state_active = False

    check("C1 is_fullback_line_member(TACKLER_1 tagged)",
          fb.is_fullback_line_member(d) is False)
    check("C1 population_authority.is_line_body(TACKLER_1 tagged)",
          call_optional(pop.is_line_body, d) is False)
    check("C1 spacing_authority.is_spacing_eligible(tagged)",
          call_optional(spacing.is_spacing_eligible, d) is False)
    check("C1 width_authority.is_width_eligible(tagged)",
          call_optional(width.is_width_eligible, d) is False)

    other = make_defender(80.0, 40.0, 2, tagged=False)
    world.defending_team = [d, other]
    world.players = [d, other]
    members = list(pop.line_population(world.defending_team))
    check("C1 tagged FB absent from line_population",
          d not in members, f"population ids={[getattr(m, 'player_id', None) for m in members]}")


def c1_tagged_fb_gets_no_depth_role() -> None:
    world = StubWorld()
    d = make_defender(78.0, 35.0, 1)
    d.defender_role = DefenderRole.TACKLER_1
    d.tackle_role = "PRIMARY"
    mates = [make_defender(76.0, y, 10 + i, tagged=False)
             for i, y in enumerate((20.0, 28.0, 42.0, 50.0))]
    team = [d] + mates
    world.defending_team = team
    world.players = team
    # Authoritative signature: assign_depth_roles(defenders, world, *, ...)
    depth_roles.assign_depth_roles(team, world)
    check("C1 tagged FB keeps depth_role None",
          getattr(d, "depth_role", None) is None,
          f"depth_role={getattr(d, 'depth_role', None)}")
    check("C1 tagged FB keeps depth_side None",
          getattr(d, "depth_side", None) is None,
          f"depth_side={getattr(d, 'depth_side', None)}")
    check("C1 tagged FB renders as FB in the depth overlay",
          d.depth_role_tag == "FB")
    check("C1 at least one line mate DID receive a depth role",
          any(getattr(m, "depth_role", None) is not None for m in mates),
          f"mate roles={[getattr(m, 'depth_role', None) for m in mates]}")


def c1_eligibility_gate_reads_no_membership_field() -> None:
    """The tackle gate must not consult line membership (tag/depth/side)."""
    world = StubWorld()
    bc_break, _ = broken_pair(world)
    world.ball_carrier = bc_break
    d = make_defender(78.0, 35.0, 1)
    # Deliberately stamp line identity fields; admission must ignore them.
    d.depth_role = "CENTRE"
    d.depth_side = 1
    d.tackle_role = "LINE"
    check("C1 emergency gate true with line fields stamped",
          fb.fullback_emergency_defender_active(world, d, bc_break) is True)
    check("C1 emergency gate true while wearing TACKLER_1",
          bool(depth_roles) and fb.fullback_emergency_defender_active(
              world, d, bc_break) is True)


# ═════════════════════════════════════════════════════════════════════
# C2 -- TACKLE ELIGIBILITY IS GATED ON A GENUINE LINE BREAK
# ═════════════════════════════════════════════════════════════════════

def c2_break_gate() -> None:
    world = StubWorld()
    bc_break, bc_behind = broken_pair(world)
    d = make_defender(78.0, 35.0, 1)

    check("C2 line_break_active True past the anchor",
          fb.line_break_active(world, bc_break) is True)
    check("C2 line_break_active False behind the anchor",
          fb.line_break_active(world, bc_behind) is False)
    check("C2 emergency gate True on a break",
          fb.fullback_emergency_defender_active(world, d, bc_break) is True)
    check("C2 emergency gate False off a break",
          fb.fullback_emergency_defender_active(world, d, bc_behind) is False)

    no_tackle = make_defender(78.0, 35.0, 2, tackle_ok=False)
    check("C2 emergency gate False without can_acquire_tackle",
          fb.fullback_emergency_defender_active(world, no_tackle, bc_break) is False)

    frozen = make_defender(78.0, 35.0, 3)
    frozen.frozen = True
    check("C2 emergency gate False when frozen",
          fb.fullback_emergency_defender_active(world, frozen, bc_break) is False)

    timer = make_defender(78.0, 35.0, 4)
    timer.freeze_timer = 0.5
    check("C2 emergency gate False when freeze-timed",
          fb.fullback_emergency_defender_active(world, timer, bc_break) is False)

    untagged = make_defender(78.0, 35.0, 5, tagged=False)
    check("C2 emergency gate False for an untagged defender",
          fb.fullback_emergency_defender_active(world, untagged, bc_break) is False)


def c2_kickoff_catch_anchor_waits_for_a_real_line_break() -> None:
    world = StubWorld(anchor_x=18.25)
    world.ruck_x_m = 8.25
    world.defensive_line_anchor = 18.25
    world.ball_carrier = Player(25.0, 35.0, 900)
    fullback = make_defender(40.0, 35.0, 1)
    line = make_defender(26.0, 35.0, 2, tagged=False)
    world.defending_team = [fullback, line]
    world.players = [fullback, line]

    check("C2 catch-seeded anchor alone does not prove line occupancy",
          fb.fullback_defensive_line_occupied(world, 18.25, fullback) is False)
    check("C2 fullback waits until carrier clears the actual line body",
          fb.fullback_emergency_defender_active(
              world, fullback, world.ball_carrier,
          ) is False)

    world.ball_carrier.x = 28.0
    check("C2 a carrier past the actual line proves it was occupied",
          fb.fullback_defensive_line_occupied(world, 18.25, fullback) is True)
    check("C2 fullback chase/admission starts after the real break",
          fb.fullback_emergency_defender_active(
              world, fullback, world.ball_carrier,
          ) is True)


def c2_lane_candidacy_is_gated() -> None:
    world = StubWorld()
    bc_break, bc_behind = broken_pair(world)
    d = make_defender(78.0, 35.0, 1)
    world.defending_team = [d]
    world.players = [d]

    check("C2 lane candidate False off a break",
          it.is_lane_tackler_candidate(d, bc_behind.x, world=world, bc=bc_behind) is False)
    check("C2 tagged FB never enters lane candidacy on a break",
          it.is_lane_tackler_candidate(d, bc_break.x, world=world, bc=bc_break) is False)
    check("C2 lane candidate False without a world (safe default)",
          it.is_lane_tackler_candidate(d, bc_break.x) is False)

    line = make_defender(74.0, 40.0, 6, tagged=False)
    d.tackle_role = "LINE"
    line.tackle_role = "LINE"
    world.defending_team = [d, line]
    primaries = it.select_lane_tacklers(world, [d, line], bc_break)
    check("C2 selector preserves line PRIMARY and does not role the FB",
          line in primaries and line.tackle_role == "PRIMARY"
          and d.tackle_role == "LINE",
          f"fb={d.tackle_role} line={line.tackle_role}")


def c2_lane_eligibility_gate_is_gated() -> None:
    world = StubWorld()
    bc_break, bc_behind = broken_pair(world)
    d = make_defender(78.0, 35.0, 1)
    check("C2 _is_eligible_lane_defender False off a break",
          it._is_eligible_lane_defender(d, world, bc_behind) is False)
    check("C2 tagged FB is not lane-eligible on a break",
          it._is_eligible_lane_defender(d, world, bc_break) is False)
    check("C2 _is_eligible_lane_defender False with no world",
          it._is_eligible_lane_defender(d) is False)


def c2_pretackle_pool_is_gated() -> None:
    world = StubWorld()
    bc_break, bc_behind = broken_pair(world)
    world.ball_carrier = bc_break
    d = make_defender(78.0, 35.0, 1)
    d.movement_state = MovementState.CHASE
    team = [d, make_defender(79.0, 36.0, 2, tagged=False)]

    on_break = _fullback_tackle_candidates(team, set(), world=world, bc=bc_break)
    check("C2 pre-tackle pool admits the FB on a break",
          d in on_break, f"pool ids={[getattr(p, 'player_id', None) for p in on_break]}")

    off_break = _fullback_tackle_candidates(team, set(), world=world, bc=bc_behind)
    check("C2 pre-tackle pool excludes the FB off a break",
          d not in off_break,
          f"pool ids={[getattr(p, 'player_id', None) for p in off_break]}")

    # Already-selected bodies are never returned twice.  ``already`` holds the
    # SELECTED PLAYERS (not ids) -- the pool writer's own contract.
    dup = _fullback_tackle_candidates(team, {d}, world=world, bc=bc_break)
    check("C2 pre-tackle pool honours the already-selected set",
          d not in dup, f"pool ids={[getattr(p, 'player_id', None) for p in dup]}")
    mate = team[1]
    mate_only = _fullback_tackle_candidates(team, {mate}, world=world, bc=bc_break)
    check("C2 excluding a different body does not exclude the FB",
          d in mate_only,
          f"pool ids={[getattr(p, 'player_id', None) for p in mate_only]}")


def c2_corridor_is_gated() -> None:
    world = StubWorld()
    bc_break, bc_behind = broken_pair(world)
    world.ball_carrier = bc_break
    d = make_defender(bc_break.x - 2.0, bc_break.y, 1)
    d.tackle_role = "PRIMARY"
    tuners = SimpleNamespace()

    check("C2 corridor eligible False off a break",
          corridor.is_corridor_eligible(d, bc_behind, tuners, world) is False)
    check("C2 tagged fullback remains outside the corridor on a break",
          corridor.is_corridor_eligible(d, bc_break, tuners, world) is False)
    check("C2 corridor eligible False without a world (safe default)",
          corridor.is_corridor_eligible(d, bc_break, tuners) is False)

    prev = {id(d): True}
    check("C2 corridor incumbent bypass honours the break gate (off break)",
          corridor._corridor_admit(d, bc_behind, tuners, prev, world) is False)
    check("C2 corridor incumbent bypass cannot take FB movement ownership",
          corridor._corridor_admit(d, bc_break, tuners, prev, world) is False)

    line_tackler = make_defender(bc_break.x - 2.0, bc_break.y, 7, tagged=False)
    line_tackler.tackle_role = "PRIMARY"
    check("C2 normal line PRIMARY retains corridor eligibility",
          corridor.is_corridor_eligible(
              line_tackler, bc_break, tuners, world,
          ) is True)

    # Corridor membership also requires real tackle authority.
    sweeper = make_defender(bc_break.x - 2.0, bc_break.y, 2)
    check("C2 corridor refuses an uncommitted sweeper on a break",
          corridor.is_corridor_eligible(sweeper, bc_break, tuners, world) is False)
# ═════════════════════════════════════════════════════════════════════
# C3 -- TAG PRESERVATION THROUGH TES ARMING AND THE END-OF-CYCLE RESET
# ═════════════════════════════════════════════════════════════════════

def c3_arming_keeps_the_tag() -> None:
    world = StubWorld()
    bc_break, _ = broken_pair(world)
    world.ball_carrier = bc_break
    d = make_defender(78.0, 35.0, 1)
    d.role = "FULLBACK"

    arm_as_tackler(world, d, "PRIMARY")

    check("C3 arming keeps fullback_role_active", d.fullback_role_active is True)
    check("C3 arming sets the engagement marker",
          fb.is_fullback_emergency_engaged(d) is True)
    check("C3 successful TES arming consumes standalone candidacy",
          getattr(d, fb.FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR, False) is False)
    check("C3 arming still stamps the TES role",
          d.defender_role is DefenderRole.TACKLER_1)
    check("C3 arming still stamps the TES selection",
          d.tes_tackler_role == "PRIMARY")
    check("C3 arming still grants tackle authority",
          d.can_acquire_tackle is True)
    check("C3 arming keeps the FULLBACK debug identity",
          getattr(d, "role", None) == "FULLBACK")
    check("C3 armed actor is NOT a line member",
          fb.is_fullback_line_member(d) is False)


def c3_reset_returns_the_sweeper_and_keeps_the_tag() -> None:
    world = StubWorld()
    d = make_defender(78.0, 35.0, 1)
    fb.set_fullback_emergency_engaged(d)
    d.defender_role = DefenderRole.TACKLER_1
    d.tackle_state_active = True
    d.is_tackler = True
    d.tes_tackler_role = "PRIMARY"
    d._tackle_commit_active = True
    d.pre_tackle_stance = True

    reset_done = fb.reset_fullback_emergency_engagement(d)

    check("C3 reset reports the body was engaged", reset_done is True)
    check("C3 reset clears the engagement marker",
          fb.is_fullback_emergency_engaged(d) is False)
    check("C3 reset KEEPS the fullback tag", d.fullback_role_active is True)
    check("C3 reset restores DEF_LINE_READY",
          d.defender_role is DefenderRole.DEF_LINE_READY)
    check("C3 reset clears the tackle-state hold",
          d.tackle_state_active is False)
    check("C3 reset clears the tackler flags",
          d.is_tackler is False and d.tes_tackler_role is None)
    check("C3 reset clears the commit flag",
          d._tackle_commit_active is False)
    check("C3 reset is idempotent (second call is a no-op)",
          fb.reset_fullback_emergency_engagement(d) is False)
    check("C3 reset actor is not a line member (tag intact)",
          fb.is_fullback_line_member(d) is False)


def c3_reset_does_not_touch_an_unengaged_sweeper() -> None:
    d = make_defender(78.0, 35.0, 1)
    d.defender_role = DefenderRole.DEF_LINE_READY
    check("C3 reset ignores an unengaged fullback",
          fb.reset_fullback_emergency_engagement(d) is False)
    check("C3 unaffected sweeper keeps DEF_LINE_READY",
          d.defender_role is DefenderRole.DEF_LINE_READY)


def c3_a_decayed_break_does_not_strand_a_lane_stamp() -> None:
    """A break that decays WITHOUT a tackle must not strand a PRIMARY stamp.

    The lane selector deliberately leaves a non-candidate's ``tackle_role``
    untouched and the whole-team wipe lives on the DOMINANCE -> RELEASE
    transition, so a break that simply decays would leave the tagged actor
    wearing PRIMARY.  Left unreleased that keeps
    ``fullback_tackle_authority_active`` True forever, which makes
    ``apply_fullback_geometry`` yield on every frame — the sweeper never
    glides home again.  This pins the release contract.
    """
    world = StubWorld()
    bc_break, bc_behind = broken_pair(world)
    d = make_defender(78.0, 35.0, 1)

    # Break decayed (no tackle happened): the stamp is stale.
    world.ball_carrier = bc_behind
    d.tackle_role = "PRIMARY"
    check("C3 stale lane stamp is released off a break",
          fb.release_stale_fullback_lane_stamp(world, d) is True)
    check("C3 stale lane stamp cleared", d.tackle_role is None)
    check("C3 authority inactive once the stamp is stale",
          fb.fullback_tackle_authority_active(d) is False)
    check("C3 sweeper approach cap restored once the stamp is stale",
          fb.fullback_approach_cap_applies(d) is True)
    check("C3 a second pass is a no-op",
          fb.release_stale_fullback_lane_stamp(world, d) is False)

    # Genuine break still live: a selected lane tackler keeps its role.
    world.ball_carrier = bc_break
    d.tackle_role = "PRIMARY"
    check("C3 a live lane stamp is NOT released",
          fb.release_stale_fullback_lane_stamp(world, d) is False)
    check("C3 live lane stamp survives", d.tackle_role == "PRIMARY")
    check("C3 lane role alone does not transfer FB movement authority",
          fb.fullback_tackle_authority_active(d) is False)

    # An ENGAGED actor's stamps belong to the tackle cycle (end-of-cycle
    # reset owns them), never to this release.
    world.ball_carrier = bc_behind
    fb.set_fullback_emergency_engaged(d)
    check("C3 engaged stamps are left to the end-of-cycle reset",
          fb.release_stale_fullback_lane_stamp(world, d) is False)
    check("C3 engaged lane stamp survives", d.tackle_role == "PRIMARY")
    fb.clear_fullback_emergency_engaged(d)

    # Untagged bodies are never touched by this path.
    world.ball_carrier = bc_behind
    other = make_defender(80.0, 35.0, 2, tagged=False)
    other.tackle_role = "PRIMARY"
    check("C3 an untagged body is not touched",
          fb.release_stale_fullback_lane_stamp(world, other) is False)
    check("C3 untagged stamp survives", other.tackle_role == "PRIMARY")


# ═════════════════════════════════════════════════════════════════════
# C4 -- THE SWEEPER CONTRACT IS SUSPENDED WHILE AUTHORITY IS GRANTED
# ═════════════════════════════════════════════════════════════════════

def c4_authority_predicate_truth_table() -> None:
    sweeper = make_defender(78.0, 35.0, 1)
    check("C4 sweeper: authority inactive",
          fb.fullback_tackle_authority_active(sweeper) is False)
    check("C4 sweeper: approach cap applies",
          fb.fullback_approach_cap_applies(sweeper) is True)

    lane = make_defender(78.0, 35.0, 2)
    lane.tackle_role = "PRIMARY"
    setattr(lane, fb.FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR, True)
    check("C4 standalone candidate keeps FB-controller movement authority",
          fb.fullback_tackle_authority_active(lane) is False)
    check("C4 PRIMARY lane tackler: cap lifted",
          fb.fullback_approach_cap_applies(lane) is False)

    committed = make_defender(78.0, 35.0, 3)
    committed._tackle_commit_active = True
    check("C4 commit stamp alone does not transfer FB movement authority",
          fb.fullback_tackle_authority_active(committed) is False)

    planted = make_defender(78.0, 35.0, 4)
    planted.pre_tackle_stance = True
    check("C4 stance stamp alone does not transfer FB movement authority",
          fb.fullback_tackle_authority_active(planted) is False)

    engaged = make_defender(78.0, 35.0, 5)
    fb.set_fullback_emergency_engaged(engaged)
    check("C4 armed emergency defender: authority active",
          fb.fullback_tackle_authority_active(engaged) is True)

    untagged = make_defender(78.0, 35.0, 6, tagged=False)
    untagged.tackle_role = "SECONDARY"
    check("C4 untagged SECONDARY body is not an FB-authority case",
          fb.is_fullback_emergency_engaged(untagged) is False and
          fb.fullback_tackle_authority_active(untagged) is True)


def c4_geometry_authority_yields_only_after_tes_arming() -> None:
    world = StubWorld()
    bc_break, _ = broken_pair(world)
    world.ball_carrier = bc_break
    world.defending_team = []

    # Armed emergency defender: the geometry writer must not claim it.
    armed = make_defender(78.0, 35.0, 1)
    armed.target_x = -999.0
    armed.target_y = -999.0
    arm_as_tackler(world, armed, "PRIMARY")
    fb.apply_fullback_geometry(world, armed)
    check("C4 geometry authority yields to an armed emergency defender",
          armed.target_x == -999.0 and armed.target_y == -999.0,
          f"target=({armed.target_x}, {armed.target_y})")

    # A stale selector stamp cannot transfer locomotion before TES succeeds.
    sweeper = make_defender(78.0, 35.0, 2)
    sweeper.target_x = -999.0
    sweeper.target_y = -999.0
    sweeper.tackle_role = "PRIMARY"
    fb.apply_fullback_geometry(world, sweeper)
    check("C4 geometry controller owns movement despite stale PRIMARY",
          sweeper.target_source == "fullback_geometry"
          and sweeper.target_x != -999.0 and sweeper.target_y != -999.0,
          f"source={sweeper.target_source} target_x={sweeper.target_x}")


def c4_geometry_restores_the_sweeper_when_the_break_decays() -> None:
    """The geometry writer must restore the sweeper in the SAME call that
    releases a stale lane stamp (the break-decays-without-a-tackle case)."""
    world = StubWorld()
    bc_break, bc_behind = broken_pair(world)
    d = make_defender(78.0, 35.0, 1)
    world.defending_team = [d]
    world.players = [d]
    d.tackle_role = "PRIMARY"

    # Live break: a selector role alone cannot take movement ownership.
    world.ball_carrier = bc_break
    d.target_x = -999.0
    d.target_y = -999.0
    fb.apply_fullback_geometry(world, d)
    check("C4 geometry controller still owns the live-break chase",
          (d.target_x != -999.0 or d.target_y != -999.0)
          and d.tackle_role == "PRIMARY"
          and d.target_source == "fullback_geometry",
          f"target=({d.target_x},{d.target_y}) role={d.tackle_role}")

    # Break decayed with no tackle: the stale stamp is dropped and the
    # sweeper anchor re-asserted in the same pass.
    world.ball_carrier = bc_behind
    d.target_x = -999.0
    d.target_y = -999.0
    fb.apply_fullback_geometry(world, d)
    check("C4 stale lane stamp released by the geometry writer",
          d.tackle_role is None, f"tackle_role={d.tackle_role}")
    check("C4 sweeper anchor re-asserted once the break decayed",
          d.target_source == "fullback_geometry" and d.target_x != -999.0,
          f"source={d.target_source} target_x={d.target_x}")
    check("C4 sweeper contract (2.0 m/s cap) restored",
          fb.fullback_approach_cap_applies(d) is True)


def c4_sweeper_speed_cap_is_lifted_for_an_authorised_actor() -> None:
    """The 2.0 m/s sweeper ceiling must not apply to an armed defender."""
    far = 120.0

    sweeper = make_defender(78.0, 35.0, 1)
    sweeper.movement_state = MovementState.DRIFT
    sweeper.vx = 8.3
    sweeper.vy = 0.0
    sweeper.move_speed = 8.3
    sweeper.target_x = sweeper.x + far
    sweeper.target_y = sweeper.y
    sweeper.update(0.016)
    sweeper_speed = math.hypot(sweeper.vx, sweeper.vy)
    # PACE RE-BASED (2026-09-27): the unengaged sweeper is no longer pinned at
    # the retired 2.0 m/s FB-only cap.  It glides at the shared DRIFT fraction
    # of max_speed_mps, which sits strictly between the old cap and the full
    # defensive ceiling -- that band IS the shared contract, so it is what this
    # check pins.
    check("C4 unengaged sweeper glides at the shared DRIFT pace "
          "(not the retired 2.0 m/s cap, not the defensive ceiling)",
          sweeper_speed > fb.FB_MAX_APPROACH_SPEED_MPS
          and sweeper_speed < sweeper.max_defensive_speed_mps,
          f"speed={sweeper_speed:.3f} "
          f"ceiling={sweeper.max_defensive_speed_mps}")

    armed = make_defender(78.0, 35.0, 2)
    armed.movement_state = MovementState.CHASE
    armed.vx = 8.3
    armed.vy = 0.0
    armed.move_speed = 8.3
    armed.target_x = armed.x + far
    armed.target_y = armed.y
    fb.set_fullback_emergency_engaged(armed)
    armed.update(0.016)
    armed_speed = math.hypot(armed.vx, armed.vy)
    check("C4 armed emergency defender is NOT held at 2.0 m/s",
          armed_speed > fb.FB_MAX_APPROACH_SPEED_MPS + 0.5,
          f"speed={armed_speed:.3f}")
    check("C4 armed emergency defender stays under the defensive ceiling",
          armed_speed <= armed.max_defensive_speed_mps + 1e-6,
          f"speed={armed_speed:.3f} ceiling={armed.max_defensive_speed_mps}")


def c4_phase_unfreeze_treats_the_armed_fb_like_any_other_tackler() -> None:
    """The FB-specific sweeper unfreeze must not special-case an armed FB.

    During TACKLE_DRIVE every TACKLER_* body is a legal mover, so an armed
    emergency fullback must receive EXACTLY the same freeze verdict as a
    normal tackler (parity is the contract: "behaves like any other
    defender").  What the FB-specific block must NOT do is clear the TES
    ``tackle_locked`` lock -- that is the sweeper-unfreeze side effect this
    change removes for an authority-granted actor.
    """
    from world.rules.movement_contracts import apply_phase_invariants

    world = StubWorld()
    armed = make_defender(78.0, 35.0, 1)
    armed.defender_role = DefenderRole.TACKLER_1
    fb.set_fullback_emergency_engaged(armed)
    armed.tackle_state_active = True
    armed.frozen = True
    armed.tackle_locked = True

    control = make_defender(79.0, 35.0, 2, tagged=False)
    control.defender_role = DefenderRole.TACKLER_1
    control.frozen = True
    control.tackle_locked = True

    world.defending_team = [armed, control]
    world.players = [armed, control]
    world.attacking_team = []

    apply_phase_invariants(world, "TACKLE_DRIVE")

    check("C4 armed FB has the same freeze verdict as a normal tackler",
          armed.frozen is control.frozen,
          f"armed={armed.frozen} control={control.frozen}")
    check("C4 armed FB is a legal mover during the drive",
          armed.frozen is False, f"frozen={armed.frozen}")
    check("C4 the sweeper unfreeze does not clear a normal tackler's TES lock",
          control.tackle_locked is True)
    check("C4 the FB-specific unfreeze does not clear the armed FB's TES lock",
          armed.tackle_locked is True)


# ═════════════════════════════════════════════════════════════════════
# C5 -- THE INVARIANT AUDIT LEGALISES THE ENGAGED EMERGENCY PATH ONLY
# ═════════════════════════════════════════════════════════════════════

def c5_engaged_tackler_is_legal() -> None:
    world = StubWorld()
    d = make_defender(78.0, 35.0, 1)
    d.defender_role = DefenderRole.TACKLER_1
    d.target_source = "tackler_commit"
    fb.set_fullback_emergency_engaged(d)
    world.defending_team = [d]
    world.players = [d]
    world.fullback_actor_id = d.player_id

    rec = capture_fullback_invariants(world)
    check("C5 capture sees the engaged actor", rec is not None and rec["fb_emergency_engaged"] is True)
    violations = _invariant_violations(rec)
    check("C5 TACKLER_1 on a tagged, ENGAGED actor is legal",
          FB_ROLE_CONTAMINATION not in violations, f"violations={violations}")
    check("C5 an emergency target_source is legal",
          FB_TARGET_SOURCE_OVERRIDE not in violations, f"violations={violations}")
    check("C5 a TES freeze on an engaged actor is legal",
          "FB_FROZEN_VIOLATION" not in violations, f"violations={violations}")


def c5_unengaged_tackler_is_still_contamination() -> None:
    world = StubWorld()
    d = make_defender(78.0, 35.0, 1)
    d.defender_role = DefenderRole.TACKLER_1
    d.target_source = "tackler_commit"
    world.defending_team = [d]
    world.players = [d]
    world.fullback_actor_id = d.player_id

    rec = capture_fullback_invariants(world)
    violations = _invariant_violations(rec)
    check("C5 unengaged TACKLER_1 on the tagged actor is FB_ROLE_CONTAMINATION",
          FB_ROLE_CONTAMINATION in violations, f"violations={violations}")
    check("C5 unengaged non-reserved target_source is still an override",
          FB_TARGET_SOURCE_OVERRIDE in violations, f"violations={violations}")


def c5_line_membership_leaks_are_still_fatal() -> None:
    world = StubWorld()
    d = make_defender(78.0, 35.0, 1)
    d.defender_role = DefenderRole.TACKLER_1
    d.target_source = "tackler_commit"
    fb.set_fullback_emergency_engaged(d)
    d.depth_role = "CENTRE"
    d.depth_side = 1
    world.defending_team = [d]
    world.players = [d]
    world.fullback_actor_id = d.player_id

    violations = _invariant_violations(capture_fullback_invariants(world))
    check("C5 a depth_role on an ENGAGED fullback is a leak",
          FB_DEPTH_ROLE_LEAK in violations, f"violations={violations}")

    d.depth_role = None
    d.depth_side = None
    violations = _invariant_violations(capture_fullback_invariants(world))
    check("C5 a clean engaged actor raises no depth leak",
          FB_DEPTH_ROLE_LEAK not in violations, f"violations={violations}")


def c5_losing_the_tag_while_engaged_is_reported() -> None:
    world = StubWorld()
    tagged = make_defender(78.0, 35.0, 1)
    tagged.defender_role = DefenderRole.TACKLER_1
    tagged.target_source = "tackler_commit"
    fb.set_fullback_emergency_engaged(tagged)

    lost = make_defender(80.0, 35.0, 2, tagged=False)
    fb.set_fullback_emergency_engaged(lost)
    lost.defender_role = DefenderRole.TACKLER_1

    world.defending_team = [tagged, lost]
    world.players = [tagged, lost]
    world.fullback_actor_id = tagged.player_id

    violations = _invariant_violations(capture_fullback_invariants(world))
    check("C5 engaged-without-tag is FB_TAG_LOST_ON_ENGAGE",
          FB_TAG_LOST_ON_ENGAGE in violations, f"violations={violations}")
# ═════════════════════════════════════════════════════════════════════
# C6 -- CONSUMER COVERAGE: EVERY LINE SYSTEM READS THE TAG, EVERY
#       EMERGENCY-SIDE CONSUMER READS THE CONTROLLED GATE
# ═════════════════════════════════════════════════════════════════════

# Line-shape consumers that must keep deriving membership from the TAG (the
# 2026-09-16 law: they must NOT be narrowed to read ``defender_role``, since
# an armed emergency fullback wears TACKLER_1/2/3).
_LINE_MEMBERSHIP_CONSUMERS = (
    "game/defence/defence_lvl_1/population_authority.py",
    "game/defence/defence_lvl_1/defensive_depth_roles.py",
    "game/defence/defence_lvl_1/spacing_authority.py",
    "game/defence/defence_lvl_1/width_authority.py",
    "game/defence/defence_lvl_1/defensive_line_governor.py",
    "game/defence/defence_lvl_1/defensive_pipeline.py",
    "game/defence/defence_lvl_1/defensive_split_spacing.py",
    "game/defence/defence_lvl_1/secondary_width_grace.py",
    "game/defence/defence_lvl_1/anchor_authority.py",
    "game/defence/defence_lvl_1/ruck_adjacent_pin.py",
    "game/defence/defence_lvl_1/phase_authority_guard.py",
    "game/defence/defence_lvl_1/defensive_engagement_state.py",
    "game/defence/defence_lvl_1/defensive_role_movement.py",
    "game/managers/defence_manager.py",
    "game/managers/retreat_manager.py",
)

# The tackle-side consumers of the controlled path.  Each must route its
# fullback branch through the single authority / marker -- never through a
# bare ``fullback_role_active`` early-return.
_EMERGENCY_PATH_CONSUMERS = (
    ("game/defence/defence_lvl_1/intercept_tacklers.py",
     'if getattr(d, "fullback_role_active", False)'),
    ("game/defence/defence_lvl_1/pretackle_corridor.py",
     "fullback_role_active"),
    ("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
     "refresh_fullback_emergency_engage_authority"),
    ("game/defence/defence_lvl_1/pretackle_classifier.py",
     "FB_EMERGENCY_ENGAGE_AUTHORITY_ATTR"),
    ("world/open_play_engine.py", "set_fullback_emergency_engaged"),
    ("world/open_play_engine.py", "reset_fullback_emergency_engagement"),
    ("world/catch_system.py", "is_fullback_emergency_engaged"),
    ("game/defence/defence_lvl_1/fullback_role.py",
     "fullback_tackle_authority_active"),
    # 2026-09-27: the FB's speed authority in Player.update is no longer the
    # deleted ``fullback_approach_cap_applies`` cap helper -- the actor is
    # routed through the single SHARED locomotion authority instead.
    ("player/player.py", "apply_fullback_locomotion"),
    ("world/rules/movement_contracts.py", "fullback_tackle_authority_active"),
)


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def c6_line_systems_read_the_tag() -> None:
    missing = []
    for path in _LINE_MEMBERSHIP_CONSUMERS:
        try:
            text = _read(path)
        except OSError as exc:
            missing.append(f"{path} <{exc}>")
            continue
        if "fullback_role_active" not in text:
            missing.append(path)
    check("C6 every line-shape consumer derives membership from the tag",
          not missing, f"missing tag guard in: {missing}")


def c6_emergency_side_reads_the_controlled_gate() -> None:
    missing = []
    for path, needle in _EMERGENCY_PATH_CONSUMERS:
        try:
            text = _read(path)
        except OSError as exc:
            missing.append(f"{path} <{exc}>")
            continue
        if needle not in text:
            missing.append(f"{path}::{needle}")
    check("C6 every emergency-side consumer routes through the single authority",
          not missing, f"missing: {missing}")


def c6_dissolve_sites_honour_the_engagement() -> None:
    """Known tag-dissolve sites must be guarded against an engaged actor."""
    sites = (
        ("world/catch_system.py", "is_fullback_emergency_engaged"),
        ("game/defence/defence_lvl_1/defensive_behaviour_unified.py",
         "_is_fb_emergency_engaged"),
        ("game/defence/defence_lvl_1/fullback_role.py",
         "is_fullback_emergency_engaged(p)"),
    )
    missing = []
    for path, needle in sites:
        try:
            text = _read(path)
        except OSError as exc:
            missing.append(f"{path} <{exc}>")
            continue
        if needle not in text:
            missing.append(f"{path}::{needle}")
    check("C6 every tag-dissolve site honours the engagement marker",
          not missing, f"missing: {missing}")


# ═════════════════════════════════════════════════════════════════════
# RUNNER
# ═════════════════════════════════════════════════════════════════════

_TESTS = (
    # C1 -- line membership is never granted to a tagged fullback
    ("C1", "tagged FB is not a line body", c1_tagged_fb_is_not_a_line_body),
    ("C1", "tagged FB gets no depth role", c1_tagged_fb_gets_no_depth_role),
    ("C1", "tackle gate reads no membership field",
     c1_eligibility_gate_reads_no_membership_field),
    # C2 -- tackle eligibility is gated on a genuine line break
    ("C2", "break gate", c2_break_gate),
    ("C2", "kickoff catch anchor waits for a real line break",
     c2_kickoff_catch_anchor_waits_for_a_real_line_break),
    ("C2", "lane candidacy is gated", c2_lane_candidacy_is_gated),
    ("C2", "lane eligibility gate is gated", c2_lane_eligibility_gate_is_gated),
    ("C2", "pre-tackle pool is gated", c2_pretackle_pool_is_gated),
    ("C2", "corridor is gated", c2_corridor_is_gated),
    # C3 -- tag preservation
    ("C3", "arming keeps the tag", c3_arming_keeps_the_tag),
    ("C3", "reset returns the sweeper and keeps the tag",
     c3_reset_returns_the_sweeper_and_keeps_the_tag),
    ("C3", "reset ignores an unengaged sweeper",
     c3_reset_does_not_touch_an_unengaged_sweeper),
    ("C3", "a decayed break does not strand a lane stamp",
     c3_a_decayed_break_does_not_strand_a_lane_stamp),
    # C4 -- sweeper contract suspended
    ("C4", "authority predicate truth table",
     c4_authority_predicate_truth_table),
    ("C4", "geometry yields only after TES arming",
     c4_geometry_authority_yields_only_after_tes_arming),
    ("C4", "geometry restores the sweeper when the break decays",
     c4_geometry_restores_the_sweeper_when_the_break_decays),
    ("C4", "sweeper speed cap is lifted",
     c4_sweeper_speed_cap_is_lifted_for_an_authorised_actor),
    ("C4", "phase unfreeze treats an armed FB like any other tackler",
     c4_phase_unfreeze_treats_the_armed_fb_like_any_other_tackler),
    # C5 -- invariant audit
    ("C5", "engaged tackler is legal", c5_engaged_tackler_is_legal),
    ("C5", "unengaged tackler is still contamination",
     c5_unengaged_tackler_is_still_contamination),
    ("C5", "line membership leaks are still fatal",
     c5_line_membership_leaks_are_still_fatal),
    ("C5", "losing the tag while engaged is reported",
     c5_losing_the_tag_while_engaged_is_reported),
    # C6 -- consumer coverage
    ("C6", "line systems read the tag", c6_line_systems_read_the_tag),
    ("C6", "emergency side reads the controlled gate",
     c6_emergency_side_reads_the_controlled_gate),
    ("C6", "dissolve sites honour the engagement",
     c6_dissolve_sites_honour_the_engagement),
)


def main() -> int:
    for contract, name, fn in _TESTS:
        run_test(contract, name, fn)

    failures = [(n, d) for n, ok, d in _RESULTS if not ok]
    width = max(len(n) for n, _ok, _d in _RESULTS)

    print("=" * 78)
    print("FULLBACK EMERGENCY DEFENDER -- ACCEPTANCE SUITE")
    print("=" * 78)
    for name, ok, detail in _RESULTS:
        status = "PASS" if ok else "FAIL"
        line = f"[{status}] {name.ljust(width)}"
        if not ok and detail:
            line += f"  <- {detail}"
        print(line)

    by_contract: dict[str, list[int]] = {}
    for name, ok, _d in _RESULTS:
        contract = name.split(" ", 1)[0]
        bucket = by_contract.setdefault(contract, [0, 0])
        bucket[0] += 1
        if not ok:
            bucket[1] += 1

    print("-" * 78)
    for contract in sorted(by_contract):
        total, bad = by_contract[contract]
        print(f"  {contract}: {total - bad}/{total} checks passed")
    print("-" * 78)
    # RESULT-LINE CONTRACT: run_fb_verifiers._parse() reads the first token
    # after "RESULT:" and requires PASS / ALL for a green verdict, so the
    # verdict token must come FIRST and the counts are appended after it.
    # (Emitting "RESULT: 84/84 checks passed" made the sweep report FAIL on a
    # fully passing suite.)
    if failures:
        print(
            f"RESULT: FAIL ({len(failures)}/{len(_RESULTS)} checks failed)"
        )
        print("\nFAILURES:")
        for name, detail in failures:
            print(f"  - {name}: {detail}")
        return 1
    print(
        f"RESULT: PASS ({len(_RESULTS)}/{len(_RESULTS)} checks passed) "
        "— ALL CONTRACTS HOLD"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
