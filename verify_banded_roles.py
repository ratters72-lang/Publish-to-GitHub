"""
verify_banded_roles.py — Acceptance verifier for the banded depth-role fix
(2026-08-12, "tacklers go to the wrong spot").

PURPOSE
-------
The interior depth roles (A/B/C/D) are assigned by Y-DISTANCE TO THE BALL
CARRIER — two defenders per band — NOT by X-distance to the ruck (which is
arbitrary on a flat defensive line).  Wingers/centres (outside backs) and
the dynamic fullback are excluded from A/B/C/D.  Tackler selection consumes
those bands: PRIMARY from A, SECONDARY from A/B.

Checks:
  1. STRAIGHT-RUN STRUCTURE — BC runs straight up the middle: the interior
     line forms exactly A, A, B, B, C, C (two defenders per band), and
     spacing Y for the A/B defenders sits inside the C/D spacing Y.
  2. OUTSIDE-BACK EXCLUSION — the two widest defenders per side are WINGER
     and CENTRE and NEVER receive A/B/C/D; the dynamic fullback is excluded
     entirely.
  3. HYSTERESIS — sub-threshold drift never flips bands; a genuine band
     crossing (defender steps > hysteresis onto an adjacent band track)
     flips exactly once.
  4. TACKLER SELECTION — at first contact, PRIMARY comes from the A band and
     SECONDARY from the A/B bands (the real closers, not a distance fluke).
  5. SPACING/Y CHAIN — compute_targets consumes the correct A/B roles so the
     two closest-to-carrier defenders receive inner spacing Y (no "wrong Y
     axis point").

Exit code 0 when all checks pass, 1 otherwise.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, DefenderRole
from game.defence.defence_lvl_1.defensive_depth_roles import (
    assign_depth_roles,
    reset_depth_role_state,
    ROLE_SWAP_HYSTERESIS_M,
)
from game.defence.defence_lvl_1.defensive_split_spacing import compute_targets
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_behaviour_unified import (
    DefensiveBehaviourUnified,
)

FAILURES = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


class MiniWorld:
    """Minimal world stub with a configurable ball carrier."""

    def __init__(self, bc=None, ruck_x=60.0, ruck_y=35.0):
        self.ball_carrier = bc
        self.ruck_x_m = ruck_x
        self.ruck_y_m = ruck_y
        self.def_line_width_m = 40.0
        self.sub_state = None
        self.open_play_engine = None


def make_defender(player_id, x, y, defender_role=None):
    d = Player(x=x, y=y, player_id=player_id)
    if defender_role is not None:
        d.defender_role = defender_role
    return d


def roles_of(line):
    return [getattr(d, "depth_role", None) for d in line]


def build_flat_interior(centre_y=35.0, spacing=3.0, n_outer=2):
    """Build a flat defensive line: n_outer widest per side (wingers and
    centres) plus n_outer interior defenders per side around the centre.

    All defenders share the same X (flat line), as in the real catch frame.
    Returns (defenders, by_id).
    """
    defenders = []
    pid = 1
    # Left side: furthest from centre = WINGER, next = CENTRE, then interior.
    left_ys = [centre_y - 7.0, centre_y - 5.5]
    right_ys = [centre_y + 5.5, centre_y + 7.0]
    for i in range(n_outer):
        defenders.append(make_defender(pid, 60.0, left_ys[i] if i < len(left_ys) else centre_y - 14.0 - i))
        pid += 1
        defenders.append(make_defender(pid, 60.0, right_ys[i] if i < len(right_ys) else centre_y + 14.0 + i))
        pid += 1
    # Interior pool: even spread around the centre, all at the same X.
    interior_left = [centre_y - (i + 1) * spacing * 0.5 for i in range(n_outer)]
    interior_right = [centre_y + (i + 1) * spacing * 0.5 for i in range(n_outer)]
    for i in range(n_outer):
        defenders.append(make_defender(pid, 60.0, interior_left[i]))
        pid += 1
        defenders.append(make_defender(pid, 60.0, interior_right[i]))
        pid += 1
    return defenders


def main():
    print("=" * 64)
    print("BANDED DEPTH-ROLE / TACKLER-SELECTION VERIFICATION (2026-08-12)")
    print("=" * 64)

    tuners = DefenceTuners()

    # ═══════════════════════════════════════════════════════════════
    # 1. Straight run up the middle -> interior forms A,A,B,B,C,C
    # ═══════════════════════════════════════════════════════════════
    print("\n[1] Straight-run interior structure (2 per band)")
    # 8 defenders = 2 WINGER + 2 CENTRE + 4 interior -> A,A,B,B.
    line = build_flat_interior(centre_y=35.0, n_outer=2)  # 8 defenders
    bc = make_defender(100, 70.0, 35.0)  # BC straight up the middle
    world = MiniWorld(bc=bc)
    assign_depth_roles(line, world)
    roles = roles_of(line)
    from collections import Counter
    counts = Counter(roles)
    check(
        "n=8 interior: exactly 2x A and 2x B (WINGER/CENTRE excluded)",
        counts.get("A", 0) == 2 and counts.get("B", 0) == 2
        and counts.get("C", 0) == 0 and counts.get("D", 0) == 0,
        str(dict(counts)),
    )
    check(
        "WINGER/CENTRE are the outer-structure reserve slots",
        counts.get("WINGER", 0) == 2 and counts.get("CENTRE", 0) == 2,
        str(dict(counts)),
    )
    # The two A defenders must be strictly closer to bc.y than the two B's.
    a_ys = sorted(abs(d.y - bc.y) for d in line if d.depth_role == "A")
    b_ys = sorted(abs(d.y - bc.y) for d in line if d.depth_role == "B")
    check(
        "A band strictly closer to BC lane than B band",
        len(a_ys) == 2 and len(b_ys) == 2 and max(a_ys) < min(b_ys),
        f"A={a_ys} B={b_ys}",
    )

    # 12 defenders -> A,A,B,B,C,C, WINGER x2, CENTRE x2, D x2.
    line12 = build_flat_interior(centre_y=35.0, n_outer=3)  # 12 defenders
    assign_depth_roles(line12, world)
    counts12 = Counter(roles_of(line12))
    check(
        "n=12 interior: exactly 2x A / 2x B / 2x C (2 per band)",
        counts12.get("A", 0) == 2 and counts12.get("B", 0) == 2
        and counts12.get("C", 0) == 2,
        str(dict(counts12)),
    )
    c_ys = sorted(abs(d.y - bc.y) for d in line12 if d.depth_role == "C")
    check(
        "band order A < B < C holds by BC-lane distance",
        max(a_ys) < min(b_ys) and max(b_ys) < min(c_ys),
        f"A={a_ys} B={b_ys} C={c_ys}",
    )

    # ═══════════════════════════════════════════════════════════════
    # 2. Outside backs + fullback never receive A/B/C/D
    # ═══════════════════════════════════════════════════════════════
    print("\n[2] Outside-back + fullback exclusion")
    line_fb = build_flat_interior(centre_y=35.0, n_outer=2)
    # Deepest right defender becomes the dynamic fullback actor.
    fullback = max(line_fb, key=lambda d: d.y)
    fullback.fullback_role_active = True
    assign_depth_roles(line_fb, world)
    for d in line_fb:
        if d is fullback:
            check(
                f"fullback id={d.player_id} excluded (no A/B/C/D)",
                d.depth_role not in ("A", "B", "C", "D"),
                f"role={d.depth_role}",
            )
            break
    # Explicit positional outside backs: widen a marker-strip of defenders.
    outside = [
        make_defender(200, 60.0, 10.0),
        make_defender(201, 60.0, 18.0),
        make_defender(202, 60.0, 27.0),
        make_defender(203, 60.0, 43.0),
        make_defender(204, 60.0, 52.0),
        make_defender(205, 60.0, 60.0),
    ]
    assign_depth_roles(outside, world)
    role_by_y = {round(d.y): d.depth_role for d in outside}
    check(
        "widest left defender -> WINGER (never A)",
        role_by_y.get(10.0) == "WINGER",
        str(role_by_y),
    )
    check(
        "widest right defender -> WINGER (never A)",
        role_by_y.get(60.0) == "WINGER",
        str(role_by_y),
    )
    check(
        "centre-reserve slots are CENTRE, not A/B",
        role_by_y.get(18.0) == "CENTRE" and role_by_y.get(52.0) == "CENTRE",
        str(role_by_y),
    )
    check(
        "interior-only defenders get the A/B bands",
        role_by_y.get(27.0) == "A" and role_by_y.get(43.0) == "A",
        str(role_by_y),
    )

    # ═══════════════════════════════════════════════════════════════
    # 3. Hysteresis: no flip on drift, correct flip on true crossing
    # ═══════════════════════════════════════════════════════════════
    print("\n[3] Hysteresis (no flicker, correct band crossing)")
    line_h = build_flat_interior(centre_y=35.0, n_outer=3)  # 12
    assign_depth_roles(line_h, world)
    before = {d.player_id: d.depth_role for d in line_h}
    a_ids = [pid for pid, r in before.items() if r == "A"]
    b_ids = [pid for pid, r in before.items() if r == "B"]
    check("2 A-band defenders seeded", len(a_ids) == 2, str(a_ids))

    # Sub-hysteresis drift (0.3 m toward the BC lane) -> no flip.
    for d in line_h:
        if d.depth_role == "A":
            d.y += 0.3 if d.y < bc.y else -0.3
    assign_depth_roles(line_h, world)
    after_drift = {d.player_id: d.depth_role for d in line_h}
    same_a = [pid for pid, r in after_drift.items() if r == "A"]
    check(
        "0.3 m drift inside hysteresis -> A band unchanged",
        sorted(same_a) == sorted(a_ids),
        f"before={a_ids} after={same_a}",
    )

    # True crossing: the A defender steps OUTWARD past the B track by more
    # than the hysteresis dead-band (A swaps onto the deep B/C track while
    # the B defender holds the near lane).  The band swap must fire once,
    # then settle.  Note a defender moving TOWARD the lane keeps the band
    # order (no swap) — only a defender physically crossing the boundary
    # by more than hysteresis flips the band.
    a_def = next(d for d in line_h if d.player_id == a_ids[0])
    b_def = min((d for d in line_h if d.depth_role == "B"),
                key=lambda d: abs(d.y - a_def.y))
    a_def.y = bc.y - 5.5                     # deep past B's track (gap 5.5)
    b_def.y = bc.y - 1.0                     # holds the near A track (gap 1.0)
    assign_depth_roles(line_h, world)
    after_cross = {d.player_id: d.depth_role for d in line_h}
    check(
        "genuine band crossing flips the A/B roles exactly once",
        after_cross.get(a_ids[0]) == "B"
        and after_cross.get(b_def.player_id) == "A",
        f"a={a_ids[0]}->{after_cross.get(a_ids[0])} "
        f"b={b_def.player_id}->{after_cross.get(b_def.player_id)}",
    )
    # Repeated calls with no further movement -> stable.
    assign_depth_roles(line_h, world)
    assign_depth_roles(line_h, world)
    after_settle = {d.player_id: d.depth_role for d in line_h}
    check(
        "role mapping settles (no further flips)",
        after_settle == after_cross,
        f"first={after_cross} settled={after_settle}",
    )

    # ═══════════════════════════════════════════════════════════════
    # 4. Tackler selection uses the bands (PRIMARY in A, SECONDARY in A/B)
    # ═══════════════════════════════════════════════════════════════
    print("\n[4] Band-aware tackler selection at first contact")
    line_t = build_flat_interior(centre_y=35.0, n_outer=2)  # 8 defenders
    # Put the BC directly in front of the A-band defenders so the windows
    # are satisfied by the true closers (A within the 3 m primary window,
    # B within the 5 m secondary window, WINGER/CENTRE far outside).
    bc_t = make_defender(300, 65.5, 35.0)
    world_t = MiniWorld(bc=bc_t, ruck_x=62.0, ruck_y=35.0)
    for d in line_t:
        d.x = 63.0  # flat line ~2.5 m in front of BC
        d.vx = 2.0
        d.vy = 0.0  # satisfy the primary/secondary speed windows
        d.lane_index = 0
    assign_depth_roles(line_t, world_t)
    # Run the banded selector directly (pure function; no world writes).
    # We seed the OPE-independent path by calling the method on a bare
    # behaviour object.
    behaviour = DefensiveBehaviourUnified(tuners)
    behaviour._compute_lane_indices(world_t, line_t)
    behaviour.select_tacklers(world_t, line_t)
    primary = next((d for d in line_t if d.tackle_role == "PRIMARY"), None)
    secondaries = [d for d in line_t if d.tackle_role == "SECONDARY"]
    check("primary tackler assigned", primary is not None)
    if primary is not None:
        check(
            "PRIMARY tackler comes from the A band",
            primary.depth_role == "A",
            f"role={primary.depth_role} y={primary.y:.1f}",
        )
    check(
        "SECONDARY tacklers come from the A/B bands",
        len(secondaries) >= 1
        and all(d.depth_role in ("A", "B") for d in secondaries),
        [(d.player_id, d.depth_role) for d in secondaries],
    )

    # ═══════════════════════════════════════════════════════════════
    # 5. Spacing consumes the correct A/B roles (Y chain)
    # ═══════════════════════════════════════════════════════════════
    print("\n[5] Spacing chain consumes correct A/B roles")
    # Use the 12-defender line so the C/D outer structure exists.
    world5 = MiniWorld(bc=bc_t, ruck_x=62.0, ruck_y=35.0)
    assign_depth_roles(line12, world5)
    targets = compute_targets(world5, line12, tuners)
    a_targets = [targets[id(d)] for d in line12 if d.depth_role == "A"]
    b_targets = [targets[id(d)] for d in line12 if d.depth_role == "B"]
    c_targets = [targets[id(d)] for d in line12 if d.depth_role == "C"]
    d_targets = [targets[id(d)] for d in line12 if d.depth_role == "D"]
    interior_targets = a_targets + b_targets
    outer_targets = c_targets + d_targets
    check(
        "interior (A/B) spacing Y inside outer (C/D) spacing Y",
        interior_targets and outer_targets
        and max(abs(y - bc_t.y) for y in interior_targets)
        < max(abs(y - bc_t.y) for y in outer_targets),
        f"A/B={[round(y, 2) for y in interior_targets]} "
        f"C/D={[round(y, 2) for y in outer_targets]}",
    )
    check(
        "each A/B defender receives a spacing target (chain is role-driven)",
        len(a_targets) == 2 and len(b_targets) == 2,
        f"A={[round(y, 2) for y in a_targets]} B={[round(y, 2) for y in b_targets]}",
    )

    # ═══════════════════════════════════════════════════════════════
    # RESULT
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 64)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
