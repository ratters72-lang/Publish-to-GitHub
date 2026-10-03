"""SLOT ALLOCATOR verifier — ONE-A-PER-SIDE enforcement (2026-08-24).

SPEC CONTEXT
------------
Depth-role geometry is now invalidated at tackle release
(tackle_state_gate._release_defender) and new-anchor events
(_prepare_release DEPTH-ROLE INVALIDATION sweep).  With stale spacing
eliminated, the remaining defect was the SLOT ALLOCATOR itself:

  HOLE 1 — the bootstrap path (_bootstrap_side_roles) had NO
      single-capacity de-dup and NO one-A guarantee.  Mixed
      seeded/unseeded bootstraps (rebalance flipper / force-innermost
      reseed) could leave two A's on one side (stacking at the
      ruck-adjacent band) or zero A's (outer-lane gap) for at least
      one frame.
  HOLE 2 — the maintenance ANCHOR-PROXIMITY GUARD (3.0 m) stranded
      freshly released holders: a TACKLE-STATE release exits the bubble
      at radius 3.5 + hysteresis 0.25 (+ peel lead 0.6) ~= 3.75-4.35 m
      from the ruck point, so |y - ruck_y| routinely exceeded the guard
      and the released holder could NEVER reclaim its interior band —
      it lingered as a stacked D while another defender was permanently
      promoted into the vacancy.

FIX UNDER TEST
--------------
  1. Shared slot-invariant pass (_dedupe_single_capacity_claims +
     _ensure_exactly_one_a) enforced at the end of BOTH the bootstrap
     and maintenance allocation paths.
  2. Rebalance flipper clears its stale depth_role when crossing sides
     (open_play_engine POST-DRIVE SIDE REBALANCE).
  3. Fresh-invalidated refill exemption: defenders entering
     assign_depth_roles with depth_role None may fill interior A/B/C
     slots by lane best-fit on that single call, bypassing the
     proximity guard (interior slots only).

PART 1 — post-release reclaim: a released LEFT A at 5 m lateral offset
  reclaims the A slot on the call it re-enters (guard would block).
PART 2 — rebalance-flipper bootstrap: a stale-role carrier crossing
  sides cannot produce two A's on either side.
PART 3 — mixed-bootstrap duplicate guard: pre-existing A holder +
  unseeded newcomer -> at most one A per side afterwards.
PART 4 — no-A repair: a side seeded W/C/B/B gains exactly one A.
PART 5 — settle stability: the invariant pass introduces ZERO flips on
  an already-valid line under drift.
PART 6 — adversarial property sweep: randomized duplicated/None/unseeded
  role states satisfy the invariants after EVERY assign_depth_roles
  call (bootstrap AND maintenance).

Exit code 0 when all scenarios pass, 1 otherwise.
"""
import sys
import json
import random

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, Team
from world.world_state import WorldState, GamePhase, GameSubState
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
    ROLE_SWAP_HYSTERESIS_M,
)
from game.defence.defence_lvl_1.tackle_state_gate import (
    stamp_tackle_state,
    is_tackle_state_flagged,
)

RUCK_X = 60.0
RUCK_Y = 35.0
DEF_LINE_WIDTH = 40.0

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class MiniWorld:
    def __init__(self, ruck_x=RUCK_X, ruck_y=RUCK_Y, width=DEF_LINE_WIDTH):
        self.ruck_x_m = ruck_x
        self.ruck_y_m = ruck_y
        self.def_line_width_m = width
        self.open_play_engine = None


def make_defender(player_id, x, y):
    return Player(x=x, y=y, player_id=player_id)


def side_counts(eligible):
    """{side: Counter of depth_role occupancy} for stamped defenders."""
    from collections import Counter
    per_side = {-1: Counter(), 1: Counter()}
    for d in eligible:
        per_side[getattr(d, "depth_side", None)][getattr(d, "depth_role", None)] += 1
    return per_side


def invariants_hold(eligible):
    """The slot-allocator post-condition for every side:

      - at most ONE occupant per single-capacity role (W/C/A/B/C);
      - EXACTLY one A whenever any non-WINGER/CENTRE defender exists
        on the side.
    """
    per_side = side_counts(eligible)
    for side, counts in per_side.items():
        for role in ("WINGER", "CENTRE", "A", "B", "C"):
            if counts.get(role, 0) > 1:
                return False, f"side {side}: {counts.get(role)}x {role}"
        interior = sum(
            n for r, n in counts.items()
            if r not in ("WINGER", "CENTRE") and isinstance(r, str)
        )
        if interior >= 1 and counts.get("A", 0) != 1:
            return False, f"side {side}: A count {counts.get('A', 0)} (interior={interior})"
    return True, ""


# ─────────────────────────────────────────────────────────────────────
# PART 1 — post-release reclaim (fresh-invalidated exemption)
# ─────────────────────────────────────────────────────────────────────

def part1_post_release_reclaim():
    print()
    print("[1] Released LEFT A reclaims its slot beyond the 3 m guard")
    world = MiniWorld()
    line = [
        make_defender(1, 70.0, 15.0),   # LEFT WINGER
        make_defender(2, 70.0, 24.0),   # LEFT CENTRE
        make_defender(3, 70.0, 31.0),   # LEFT A  <- will be released
        make_defender(4, 70.0, 39.0),   # RIGHT A
        make_defender(5, 70.0, 46.0),   # RIGHT CENTRE
        make_defender(6, 70.0, 55.0),   # RIGHT WINGER
    ]
    assign_depth_roles(line, world)
    check("P1.0: bootstrap gave id3 the LEFT A slot",
          getattr(line[2], "depth_role", None) == "A",
          f"role={getattr(line[2], 'depth_role', None)}")

    # ── Simulate tackle-release invalidation (tackle_state_gate) ──
    # depth_role cleared, _depth_seeded KEPT (maintenance entry), and
    # the holder stands 5 m off ruck_y — beyond the 3.0 m guard, as a
    # real peel exit (radius 3.5 + hysteresis 0.25 + lead 0.6) does.
    released = line[2]
    released.depth_role = None
    released.role_depth_target_x = None
    released.role_depth_target_y = None
    released.y = RUCK_Y - 5.0

    check("P1.1: precondition — released holder beyond the guard",
          abs(released.y - RUCK_Y) > ROLE_SWAP_HYSTERESIS_M,
          f"|dy|={abs(released.y - RUCK_Y):.1f} > {ROLE_SWAP_HYSTERESIS_M}")

    assign_depth_roles(line, world)

    ok, why = invariants_hold(line)
    check("P1.2: slot invariants hold after the release frame", ok, why)
    check("P1.3: released holder RECLAIMED an interior role (not stranded D)",
          getattr(released, "depth_role", None) == "A",
          f"role={getattr(released, 'depth_role', None)}")

    per_side = side_counts(line)
    check("P1.4: exactly one A per side",
          per_side[-1].get("A", 0) == 1 and per_side[1].get("A", 0) == 1,
          str({k: dict(v) for k, v in per_side.items()}))

    # Steady state: further calls keep the reclaim stable (no thrash).
    snap_before = {d.player_id: d.depth_role for d in line}
    assign_depth_roles(line, world)
    snap_after = {d.player_id: d.depth_role for d in line}
    check("P1.5: reclaim is stable on the next call",
          snap_before == snap_after,
          f"{snap_before} -> {snap_after}")


# ─────────────────────────────────────────────────────────────────────
# PART 2 — rebalance-flipper bootstrap (stale role carrier)
# ─────────────────────────────────────────────────────────────────────

def part2_flipper_bootstrap():
    print()
    print("[2] Stale-role carrier through a full re-bootstrap")
    world = MiniWorld()
    # 7 defenders positioned so the count-driven split lands 4 LEFT /
    # 3 RIGHT (band centres ~25 / ~45 at ruck_y=35, width 40).
    line = [
        make_defender(1, 70.0, 12.0),
        make_defender(2, 70.0, 20.0),
        make_defender(3, 70.0, 28.0),
        make_defender(4, 70.0, 32.0),
        make_defender(5, 70.0, 42.0),
        make_defender(6, 70.0, 50.0),
        make_defender(7, 70.0, 60.0),
    ]
    assign_depth_roles(line, world)

    # ── Simulate the POST-DRIVE SIDE REBALANCE flipper state ──
    # Pick the RIGHT-side A holder (or nearest-to-ruck right defender)
    # and flip it toward LEFT keeping its STALE role, unseeded — the
    # exact state open_play_engine produced before the fix.
    flipper = min(
        (d for d in line if getattr(d, "depth_side", None) == 1),
        key=lambda d: abs(d.y - RUCK_Y),
    )
    old_role = flipper.depth_role
    flipper.depth_side = -1          # crossed the ruck
    flipper._depth_seeded = False    # triggers FULL re-bootstrap
    # NOTE: depth_role deliberately KEPT (the pre-fix defect state).
    print(f"       flipper id={flipper.player_id} carried role={old_role}")

    assign_depth_roles(line, world)

    ok, why = invariants_hold(line)
    check("P2.0: slot invariants hold after the flipper bootstrap", ok, why)
    per_side = side_counts(line)
    check("P2.1: no side carries two A's (stacking)",
          per_side[-1].get("A", 0) <= 1 and per_side[1].get("A", 0) <= 1,
          str({k: dict(v) for k, v in per_side.items()}))
    check("P2.2: no side carries two of any single-capacity role",
          all(per_side[s].get(r, 0) <= 1
              for s in (-1, 1)
              for r in ("WINGER", "CENTRE", "A", "B", "C")),
          str({k: dict(v) for k, v in per_side.items()}))


# ─────────────────────────────────────────────────────────────────────
# PART 3 — mixed-bootstrap duplicate guard
# ─────────────────────────────────────────────────────────────────────

def part3_mixed_bootstrap_duplicate():
    print()
    print("[3] Pre-existing A holder + unseeded newcomer on one side")
    world = MiniWorld()
    line = [
        make_defender(1, 70.0, 14.0),
        make_defender(2, 70.0, 22.0),
        make_defender(3, 70.0, 30.0),   # LEFT A holder (seeded)
        make_defender(4, 70.0, 33.0),   # unseeded newcomer, closest to ruck
        make_defender(5, 70.0, 44.0),
        make_defender(6, 70.0, 52.0),
        make_defender(7, 70.0, 61.0),
    ]
    # Seed everyone except id4; give id3 a persistent LEFT A claim.
    for i, d in enumerate(line):
        d._depth_seeded = (i != 3)
    line[0].depth_role, line[0].depth_side = "WINGER", -1
    line[1].depth_role, line[1].depth_side = "CENTRE", -1
    line[2].depth_role, line[2].depth_side = "A", -1
    line[4].depth_role, line[4].depth_side = "WINGER", 1
    line[5].depth_role, line[5].depth_side = "CENTRE", 1
    line[6].depth_role, line[6].depth_side = "A", 1

    assign_depth_roles(line, world)

    ok, why = invariants_hold(line)
    check("P3.0: slot invariants hold after the mixed bootstrap", ok, why)


# ─────────────────────────────────────────────────────────────────────
# PART 4 — no-A repair (outer-lane gap closure)
# ─────────────────────────────────────────────────────────────────────

def part4_no_a_repair():
    print()
    print("[4] Side seeded W/C/B/B gains exactly one A")
    world = MiniWorld()
    line = [
        make_defender(1, 70.0, 14.0),
        make_defender(2, 70.0, 22.0),
        make_defender(3, 70.0, 29.0),
        make_defender(4, 70.0, 26.0),
        make_defender(5, 70.0, 44.0),
        make_defender(6, 70.0, 52.0),
        make_defender(7, 70.0, 60.0),
    ]
    for d in line:
        d._depth_seeded = True
    line[0].depth_role, line[0].depth_side = "WINGER", -1
    line[1].depth_role, line[1].depth_side = "CENTRE", -1
    line[2].depth_role, line[2].depth_side = "B", -1
    line[3].depth_role, line[3].depth_side = "B", -1
    line[4].depth_role, line[4].depth_side = "WINGER", 1
    line[5].depth_role, line[5].depth_side = "CENTRE", 1
    line[6].depth_role, line[6].depth_side = "A", 1

    assign_depth_roles(line, world)

    per_side = side_counts(line)
    check("P4.0: LEFT side now holds exactly one A",
          per_side[-1].get("A", 0) == 1,
          str({k: dict(v) for k, v in per_side.items()}))
    ok, why = invariants_hold(line)
    check("P4.1: full slot invariants hold", ok, why)


# ─────────────────────────────────────────────────────────────────────
# PART 5 — settle stability (invariant pass is a no-op on valid lines)
# ─────────────────────────────────────────────────────────────────────

def part5_settle_stability():
    print()
    print("[5] Zero flips on a valid line under drift")
    world = MiniWorld()
    line = [
        make_defender(1, 70.0, 12.0),
        make_defender(2, 70.0, 21.0),
        make_defender(3, 70.0, 30.0),
        make_defender(4, 70.0, 41.0),
        make_defender(5, 70.0, 49.0),
        make_defender(6, 70.0, 58.0),
    ]
    assign_depth_roles(line, world)
    before = {d.player_id: (d.depth_role, d.depth_side) for d in line}

    rng = random.Random(20260824)
    flips = []
    for _ in range(120):
        for d in line:
            d.y += rng.uniform(-0.01, 0.01)
        assign_depth_roles(line, world)
        cur = {d.player_id: (d.depth_role, d.depth_side) for d in line}
        for pid, v in cur.items():
            if v != before[pid]:
                flips.append((pid, before[pid], v))
                before[pid] = v
    check("P5.0: no role/side flips across 120 drifted frames",
          not flips, str(flips[:3]))
    ok, why = invariants_hold(line)
    check("P5.1: invariants hold at the end of the drift", ok, why)


# ─────────────────────────────────────────────────────────────────────
# PART 6 — adversarial property sweep
# ─────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────
# PART 7 — engine-level release-frame repair (slot-invariant pass AT
# tackle release, BEFORE the ruck-adjacent pin)
# ─────────────────────────────────────────────────────────────────────

def make_line_defender(pid, x, y):
    d = make_defender(pid, x, y)
    d.team = Team.RECEIVING
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    d.defender_role = None
    return d


def make_bc(pid=99, x=80.0, y=35.0):
    bc = make_line_defender(pid, x, y)
    bc.possessed = True
    return bc


def make_engine_world():
    w = WorldState()
    w.ruck_established = True  # STAGE 3B: post-TACKLE_INIT band/role scenario

    w.ruck_x_m = 55.0
    w.ruck_y_m = 35.0
    w.def_line_width_m = 40.0
    w.first_tackle_not_made = False
    w.in_pursuit_mode = False
    w.use_structured_open_play = True
    w.defence_enabled = True
    w.tes_active = False
    w.phase = GamePhase.OPEN_PLAY
    w.sub_state = GameSubState.OPEN_PLAY
    w.ball_owner = "BC"
    w.role_governs = False
    w.ctx = None
    w.frame_count = 0
    w.sim_time_s = 0.0
    w.dt = 1.0 / 60.0
    return w


def build_engine(defenders, bc):
    w = make_engine_world()
    w.defending_team = list(defenders)
    w.attacking_team = []
    w.players = list(defenders) + [bc]
    w.ball_carrier = bc
    from world.open_play_engine import OpenPlayEngine
    eng = OpenPlayEngine(w)
    w.open_play_engine = eng
    w.open_play = eng
    return w, eng


def part7_engine_release_frame():
    print()
    print("[7] Engine release frame: slot repair fires BEFORE the ruck-pin")
    # The holder sits nearest the ruck (A band) but is TACKLE-STATE-held
    # through the hold frames, so its side promotes a SUBSTITUTE A while
    # it is excluded.  On the release frame the repair must restore the
    # invariants and cover the released holder in the frame dicts —
    # WITHOUT waiting for the next frame's preamble.
    holder = make_line_defender(1, 56.0, 35.0)
    team = [
        holder,
        make_line_defender(2, 70.0, 15.0),
        make_line_defender(3, 71.0, 22.0),
        make_line_defender(4, 72.0, 30.0),
        make_line_defender(5, 73.0, 41.0),
        make_line_defender(6, 74.0, 49.0),
        make_line_defender(7, 75.0, 58.0),
    ]
    bc = make_bc(x=80.0, y=35.0)
    bc.vx = 5.0
    w, eng = build_engine(team, bc)

    # ── Hold era ──
    stamp_tackle_state(holder)
    # Positive marker-freeze timer keeps the peel writer off the holder
    # during the hold frames (peel skips while the timer is > 0).
    holder.marker_freeze_timer = 1.0
    dt = 1.0 / 60.0
    err = None
    for _ in range(6):
        try:
            eng.update(dt)
        except Exception as exc:
            err = exc
            break
    check("P7.0: hold frames run clean", err is None,
          f"{type(err).__name__}: {str(err)[:160]}" if err else "")
    check("P7.1: holder STILL held after 6 hold frames",
          is_tackle_state_flagged(holder))

    # ── Release frame: cross radius + hysteresis, run ONE update ──
    holder.x = w.ruck_x_m + 4.5      # 4.5 m > radius(3.5) + hysteresis(0.25)
    try:
        eng.update(dt)
    except Exception as exc:
        check("P7.2: release frame runs clean", False,
              f"{type(exc).__name__}: {str(exc)[:200]}")
        return
    check("P7.2: release frame runs clean", True)

    check("P7.3: holder released (flag cleared)",
          not is_tackle_state_flagged(holder))
    # THE REGRESSION TARGET: without the post-release repair the holder
    # would carry depth_role=None until the NEXT frame's preamble, and
    # the frame dicts (built while it was excluded) would not cover it.
    check("P7.4: repaired ON THE RELEASE FRAME — holder holds a depth role",
          getattr(holder, "depth_role", None)
          in ("A", "B", "C", "D", "CENTRE", "WINGER"),
          f"depth_role={getattr(holder, 'depth_role', None)}")
    dct = getattr(w, "frame_spacing_targets", None) or {}
    check("P7.5: frame spacing dict REFRESHED to cover the released holder",
          id(holder) in dct)
    ok, why = invariants_hold(list(w.defending_team))
    check("P7.6: slot invariants hold across the whole team", ok, why)


_PART6_ROLES = ("WINGER", "CENTRE", "A", "B", "C", "D", None)


def part6_property_sweep():
    print()
    print("[6] Adversarial sweep: invariants hold after EVERY call")
    rng = random.Random(777)
    bad = []
    for trial in range(200):
        world = MiniWorld()
        n = rng.randint(4, 13)
        line = [
            make_defender(i + 1, 70.0, rng.uniform(8.0, 62.0))
            for i in range(n)
        ]
        # Adversarial pre-state: random roles incl. duplicates and Nones,
        # random seed flags, random sides.
        for d in line:
            d.depth_role = rng.choice(_PART6_ROLES)
            d.depth_side = rng.choice((-1, 1))
            d._depth_seeded = rng.random() > 0.3

        assign_depth_roles(line, world)   # bootstrap or maintenance
        ok, why = invariants_hold(line)
        if not ok:
            bad.append((trial, "call1", why))
            continue

        # Jiggle + second call -> maintenance path must ALSO hold.
        for d in line:
            d.y += rng.uniform(-0.5, 0.5)
        assign_depth_roles(line, world)
        ok, why = invariants_hold(line)
        if not ok:
            bad.append((trial, "call2", why))

    check("P6.0: 200 adversarial trials x 2 calls all satisfy invariants",
          not bad, str(bad[:3]))


def main():
    print("=" * 64)
    print("SLOT ALLOCATOR — ONE-A-PER-SIDE VALIDATION (2026-08-24)")
    print("=" * 64)

    part1_post_release_reclaim()
    part2_flipper_bootstrap()
    part3_mixed_bootstrap_duplicate()
    part4_no_a_repair()
    part5_settle_stability()
    part6_property_sweep()
    part7_engine_release_frame()

    report = {
        "slot_allocator": {
            "one_a_per_side_enforced": not failures,
            "single_capacity_dedup_bootstrap": not failures,
            "fresh_invalidated_reclaim": not failures,
            "issues_remaining": failures,
        }
    }
    report_path = "verify_one_a_per_side_report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print()
    print("=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
