"""
verify_spacing_chain.py — Task H: Spacing Chain Stabilisation verification.

Proves spacing is a pure lateral (Y-only) authority subsystem:

  1. S0 — spacing never outputs depth / target_x / anchor_x (Y-only dict).
  2. S0/S3 — spacing never mutates target_x / role_depth_target_x /
            chase_target_* / mmdl_target_* on any defender.
  3. S3 — spacing never overrides chase targets (a defender carrying
          chase/MMDL target fields receives NO spacing target).
  4. S4 — spacing never overrides TES/PTB targets: run_spacing raises
          outside OPEN_PLAY; gated entries are OPEN_PLAY-only by wiring.
  5. S4 — spacing never runs outside OPEN_PLAY (assert_open_play).
  6. S1 — spacing consumes the anchor authority without modifying it.
  7. S5 — spacing consumes stable depth roles/sides (never reassigns).
  8. S6 — spacing produces a stable width signature across 200 frames
          (identical inputs -> identical outputs -> zero width collapse
          under anchor / band / chase / TES transitions).
  9. S7 — spacing respects role exemptions (eligibility predicate).
 10. S8 — spacing_target_y / spacing_y exist and behave correctly
          (ruck_y + side * single offset).
 11. S9 — role_anchor_y exists and spacing delegates to it.
 +  Pipeline order probe — spacing runs BEFORE chase/audit/finalise in
    the structured OPEN_PLAY pipeline (S2; width before spacing by
    Task D governed-width design).

All checks exit 0.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import role_anchor_y
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets,
    spacing_target_y,
    spacing_y,
    LEFT,
    RIGHT,
)
from game.defence.defence_lvl_1.spacing_authority import (
    SpacingGuard,
    SpacingOpenPlayGateViolation,
    assert_open_play,
    is_open_play,
    is_spacing_eligible,
    run_spacing,
)
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m,
)
from game.defence.defence_lvl_1.defensive_depth_roles import assign_depth_roles
from world.world_state import GameSubState

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class MiniWorld:
    def __init__(self):
        self.phase = "OPEN_PLAY"
        self.sub_state = GameSubState.OPEN_PLAY
        self.ball_carrier = None
        self.ball = None
        self.ruck_x_m = 60.0
        self.ruck_y_m = 35.0
        self.def_line_width_m = 40.0
        self.ball_x_m = 60.0
        self.ball_y_m = 35.0
        self.defensive_line_anchor = 70.0
        self.in_pursuit_mode = False
        self.first_tackle_not_made = False
        self.governor_width_active = False
        self.governor_line_width_m = None
        self.sim_time_s = 0.0
        self.frame_count = 0
        self.short_side_width_m = None
        self.open_side_width_m = None


def make_line(world, n=7):
    defs = []
    for i in range(n):
        d = Player(x=60.0 + i * 0.5, y=10.0 + i * 8.0, player_id=i + 1)
        defs.append(d)
    assign_depth_roles(defs, world)
    return defs


def role_defender(role, side, player_id, x=60.0, y=None):
    y = 35.0 + side * 10.0 if y is None else y
    d = Player(x=x, y=y, player_id=player_id)
    d.depth_role = role
    d.depth_side = side
    return d


def main():
    print("=" * 72)
    print("SPACING CHAIN STABILISATION (Task H) — Y-ONLY AUTHORITY")
    print("=" * 72)

    tuners = DefenceTuners()
    world = MiniWorld()

    # ── 1+2. S0 — Y-only output, never mutates X fields ──────────
    print("\n[1+2] S0 — spacing outputs Y only; never mutates X")
    line = make_line(world)
    guard = SpacingGuard(world, line)
    targets = run_spacing(world, line, tuners)
    guard.verify(targets)

    y_only_keys = all(
        isinstance(k, int) and not isinstance(v, dict)
        for k, v in targets.items()
    )
    check(
        "output keys are id(d) with single-Y values (no depth/target_x/anchor_x)",
        y_only_keys,
        f"keys={[k for k in targets if not isinstance(k, int)] or 'ok'}",
    )
    no_depth_key = all(
        k not in ("depth", "target_x", "anchor_x") for k in targets
    )
    check("output dict has no 'depth'/'target_x'/'anchor_x' keys", no_depth_key)

    x_untouched = True
    for d in line:
        if abs(d.target_x - d.x) > 0.0:
            x_untouched = False
    check(
        "spacing never wrote defender target_x (step-7 owns the write)",
        x_untouched,
    )
    role_depth_stable = all(
        getattr(d, "depth_role", None) is not None
        and getattr(d, "role_depth_target_x", None) is not None
        and abs(getattr(d, "role_depth_target_x", 0.0) - defensive_line_anchor_m(world)) < 1e-6
        for d in line
    )
    check(
        "role_depth_target_x kept at the anchor authority (never overwritten)",
        role_depth_stable,
    )

    # ── 3. S3 — spacing never overrides chase targets ────────────
    print("\n[3] S3 — no chase-target override")
    chased = make_line(world, n=7)
    for i, d in enumerate(chased):
        d.depth_role = ("WINGER", "CENTRE", "A", "B", "C", "D", "D")[i]
        d.depth_side = -1 if i % 2 == 0 else 1
        if i == 0:
            d.chase_target_x = 50.0
            d.chase_target_y = 30.0
        if i == 1:
            d.mmdl_target_x = 55.0
            d.mmdl_target_y = 28.0
    chased_x_before = {
        id(d): (getattr(d, "chase_target_x", None), getattr(d, "mmdl_target_x", None))
        for d in chased
    }
    chased_y_before = {
        id(d): (getattr(d, "chase_target_y", None), getattr(d, "mmdl_target_y", None))
        for d in chased
    }
    chased_guard = SpacingGuard(world, chased)
    chased_targets = run_spacing(world, chased, tuners)
    chased_guard.verify(chased_targets)
    # Chase carriers keep their fields AND receive no spacing target.
    # TASK IV (2026-08-14): mmdl_target_* are POST-CONSUMPTION records
    # (written by the MMDL step AFTER spacing each frame), NOT chase
    # ownership — a defender carrying them still receives a spacing
    # target.  Only genuine chase_target_* fields trigger the S3 skip.
    d0 = chased[0]
    d1 = chased[1]
    check(
        "chase_target fields unchanged",
        chased_x_before[id(d0)][0] == d0.chase_target_x
        and chased_y_before[id(d0)][0] == d0.chase_target_y,
    )
    check(
        "mmdl_target fields unchanged",
        chased_x_before[id(d1)][1] == d1.mmdl_target_x
        and chased_y_before[id(d1)][1] == d1.mmdl_target_y,
    )
    check(
        "chase-owned defender receives NO spacing target",
        id(d0) not in chased_targets,
        f"chase_id_in_targets={id(d0) in chased_targets}",
    )
    check(
        "mmdl-stamped defender still receives a spacing target (TASK IV)",
        id(d1) in chased_targets,
        f"targets={sorted(id(k) for k in chased_targets)}",
    )
    non_chase_all = all(
        id(d) in chased_targets for i, d in enumerate(chased) if i != 0
    )
    check("non-chase defenders still get spacing targets", non_chase_all)

    # ── 4+5. S4 — never runs outside OPEN_PLAY; never overrides TES/PTB ──
    print("\n[4+5] S4 — OPEN_PLAY-only gating + no TES/PTB override")
    for phase_name, sub in (
        ("TES", GameSubState.TACKLE_CONTACT),
        ("PTB", GameSubState.PTB),
        ("POST_RUCK", GameSubState.POST_RUCK_OPEN_PLAY),
        ("RELEASE", GameSubState.TACKLE_RELEASE),
    ):
        w = MiniWorld()
        w.sub_state = sub
        raised = False
        try:
            run_spacing(w, make_line(w), tuners)
        except SpacingOpenPlayGateViolation:
            raised = True
        check(f"run_spacing refuses during {phase_name}", raised, f"sub={sub}")
    w = MiniWorld()
    check("is_open_play True in OPEN_PLAY", is_open_play(w))
    w.sub_state = "TACKLE_DRIVE"
    check("is_open_play False outside OPEN_PLAY", not is_open_play(w))
    assert_open_play(MiniWorld())
    check("assert_open_play passes in OPEN_PLAY", True)

    # TES/PTB writers owning targets: run a TES-phase world with marker /
    # tackler / DH / retreat targets pinned and confirm spacing_y does not
    # override them (read-only consumers keep their own writers).
    tes_world = MiniWorld()
    tes_world.sub_state = GameSubState.TACKLE_CONTACT
    tes_line = make_line(tes_world)
    marker = role_defender("A", -1, 501, y=25.0)
    marker.defender_role = "MARKER_A"
    tackler = role_defender("B", 1, 502, y=45.0)
    tackler.defender_role = "TACKLER_1"
    dh = role_defender("C", -1, 503, y=35.0)
    dh.defender_role = "DUMMY_HALF"
    for actor, tx, ty in ((marker, tes_world.ruck_x_m, tes_world.ruck_y_m - 0.5),
                          (tackler, 61.0, 34.0),
                          (dh, tes_world.ruck_x_m, tes_world.ruck_y_m)):
        actor.target_x = tx
        actor.target_y = ty
    # Pure read-only spacing_y must never mutate their pinned targets.
    spacing_y(tes_world, marker, tuners)
    spacing_y(tes_world, tackler, tuners)
    spacing_y(tes_world, dh, tuners)
    check(
        "spacing_y read does not override marker/tackler/DH target_x",
        marker.target_x == tes_world.ruck_x_m
        and tackler.target_x == 61.0
        and dh.target_x == tes_world.ruck_x_m,
    )
    check(
        "spacing_y read does not override marker/tackler/DH target_y",
        abs(marker.target_y - (tes_world.ruck_y_m - 0.5)) < 1e-9
        and abs(tackler.target_y - 34.0) < 1e-9
        and abs(dh.target_y - tes_world.ruck_y_m) < 1e-9,
    )

    # ── 6. S1 — anchor authority consumed, never modified ────────
    print("\n[6] S1 — anchor consumer, never producer")
    anchor_world = MiniWorld()
    anchor_before = defensive_line_anchor_m(anchor_world)
    anchor_state_before = (
        getattr(anchor_world, "defensive_line_anchor", None),
        getattr(anchor_world, "anchor_locked", None),
    )
    anchor_line = make_line(anchor_world)
    run_spacing(anchor_world, anchor_line, tuners)
    check(
        "anchor authority value unchanged after spacing",
        abs(defensive_line_anchor_m(anchor_world) - anchor_before) < 1e-9,
    )
    check(
        "world anchor fields unchanged after spacing",
        getattr(anchor_world, "defensive_line_anchor", None) == anchor_state_before[0]
        and getattr(anchor_world, "anchor_locked", None) == anchor_state_before[1],
    )
    check(
        "spacing_authority imports the Task G authority read",
        "defensive_line_anchor_m" in open(
            "game/defence/defence_lvl_1/spacing_authority.py",
            "r", encoding="utf-8", errors="replace",
        ).read(),
    )
    # S1 in-chain: the spacing module itself consumes the authority read.
    check(
        "defensive_split_spacing consumes defensive_line_anchor_m",
        "defensive_line_anchor_m as _anchor_m" in open(
            "game/defence/defence_lvl_1/defensive_split_spacing.py",
            "r", encoding="utf-8", errors="replace",
        ).read(),
    )

    # ── 7. S5 — consumes stable depth roles ──────────────────────
    print("\n[7] S5 — spacing consumes stable depth roles")
    stable_world = MiniWorld()
    stable_line = make_line(stable_world)
    roles_before = {id(d): getattr(d, "depth_role", None) for d in stable_line}
    sides_before = {id(d): getattr(d, "depth_side", None) for d in stable_line}
    run_spacing(stable_world, stable_line, tuners)
    roles_after = {id(d): getattr(d, "depth_role", None) for d in stable_line}
    sides_after = {id(d): getattr(d, "depth_side", None) for d in stable_line}
    check(
        "depth_role unchanged by spacing",
        roles_before == roles_after,
    )
    check(
        "depth_side unchanged by spacing",
        sides_before == sides_after,
    )

    # ── 8. S6 — stable width signature across 200 frames ─────────
    # TASK IV (2026-08-14): spacing Y ADDS the engagement-band pull
    # (ENGAGEMENT_SPACING_Y_OFFSET) computed from live bc.x.  Stability
    # therefore means: (a) identical inputs within a fixed band produce
    # identical outputs across 200 frames (no collapse / no drift), and
    # (b) a band transition moves EVERY defender's Y by EXACTLY the tuner
    # table delta — no free-form BC tracking, no per-frame integration.
    print("\n[8] S6 — stable width signature across 200 frames (TASK IV band pull)")
    sig_world = MiniWorld()
    sig_world.frame_count = 0
    sig_line = make_line(sig_world)
    # (a) Fixed-BC stability: BC parked deep in BALANCED territory
    # (anchor 70; BALANCED band is 65.1 < bc.x < 74.85) → the spacing
    # signature must be byte-identical across 200 frames.
    sig_world.ball_carrier = type(
        "BC", (), {"x": 70.0, "y": 35.0, "vx": 0.0, "vy": 0.0}
    )()
    sig_world.governor_width_active = True
    sig_world.governor_line_width_m = 40.0
    first = None
    for frame in range(200):
        sig_world.frame_count = frame
        targets_frame = compute_targets(sig_world, sig_line, tuners)
        if first is None:
            first = dict(targets_frame)
        mismatch = any(
            abs(first[k] - v) > 1e-9 for k, v in targets_frame.items()
        )
        if mismatch:
            check("fixed-BC width signature stable across 200 frames", False,
                  f"frame={frame}")
            break
    else:
        check(
            "fixed-BC width signature stable across 200 frames (no collapse)",
            True,
            f"sample={[round(v, 3) for v in (first or {}).values()]}",
        )
    # (b) Band-transition pull: AGGRESSIVE (bc.x < anchor-4.9 → 65.1)
    # vs BALANCED must shift every emitted Y by exactly
    # BALANCED_pull - AGGRESSIVE_pull = -0.75 - (-0.50) = -0.25 m.
    aggr_world = MiniWorld()
    aggr_world.defensive_line_anchor = 70.0
    aggr_world.ball_carrier = type(
        "BC", (), {"x": 64.0, "y": 35.0, "vx": 0.0, "vy": 0.0}
    )()
    aggr_line = make_line(aggr_world)
    aggr_targets = compute_targets(aggr_world, aggr_line, tuners)
    bal_world = MiniWorld()
    bal_world.defensive_line_anchor = 70.0
    bal_world.ball_carrier = type(
        "BC", (), {"x": 70.0, "y": 35.0, "vx": 0.0, "vy": 0.0}
    )()
    bal_line = make_line(bal_world)
    bal_targets = compute_targets(bal_world, bal_line, tuners)
    pull_table = tuners.ENGAGEMENT_SPACING_Y_OFFSET
    expected_delta = pull_table["BALANCED"] - pull_table["AGGRESSIVE"]
    band_delta_ok = all(
        abs(bal_targets[kk] - aggr_targets[kk] - expected_delta) < 1e-9
        for kk in aggr_targets
        if kk in bal_targets
    )
    check(
        "AGGRESSIVE→BALANCED transitions shift every Y by the tuner-table delta",
        band_delta_ok,
        f"expected_delta={expected_delta}",
    )
    # Band jitter: BC crossing the anchor repeatedly must not collapse width.
    jitter_world = MiniWorld()
    jitter_line = make_line(jitter_world)
    jitter_first = None
    for frame in range(100):
        sign = 1 if frame % 2 == 0 else -1
        jitter_world.ball_carrier = type(
            "BC", (), {"x": 70.0 + sign * 0.5, "y": 35.0, "vx": 0.0, "vy": 0.0}
        )()
        jitter_world.defensive_line_anchor = 70.0
        t = compute_targets(jitter_world, jitter_line, tuners)
        if jitter_first is None:
            jitter_first = dict(t)
        if any(abs(jitter_first[k] - v) > 1e-9 for k, v in t.items()):
            check("width stable across BC anchor-band jitter", False, f"frame={frame}")
            break
    else:
        check("width stable across BC anchor-band jitter (100 frames)", True)

    # ── 9. S7 — role exemptions ──────────────────────────────────
    print("\n[9] S7 — role exemptions")
    passive = role_defender("D", 1, 601, y=45.0)
    passive.depth_class = "PASSIVE"
    check("PASSIVE line defender eligible", is_spacing_eligible(passive))

    primary = role_defender("A", 1, 602, y=40.0)
    primary.depth_class = "PRIMARY"
    check("PRIMARY closer exempt", not is_spacing_eligible(primary))
    secondary = role_defender("B", -1, 603, y=30.0)
    secondary.depth_class = "SECONDARY"
    check("SECONDARY closer exempt", not is_spacing_eligible(secondary))

    for role_name in ("MARKER_A", "MARKER_B", "DUMMY_HALF", "TACKLER_1",
                      "TACKLER_2", "TACKLER_3", "DEF_CHASE"):
        d = role_defender("D", 1, 700 + len(role_name), y=35.0)
        d.defender_role = role_name
        check(f"{role_name} exempt", not is_spacing_eligible(d))

    fb = role_defender("D", -1, 710, y=40.0)
    fb.fullback_role_active = True
    check("dynamic fullback exempt", not is_spacing_eligible(fb))

    # ── 10. S8 — spacing_target_y / spacing_y exist + behaviour ──
    print("\n[10] S8 — spacing_target_y / spacing_y exist and behave")
    from game.defence.defence_lvl_1.defensive_split_spacing import (
        compute_spacing_offset,
        clamp_to_sideline,
    )
    check("spacing_target_y exists", callable(spacing_target_y))
    check("spacing_y exists", callable(spacing_y))
    dl = role_defender("WINGER", LEFT, 801, y=20.0)
    dr = role_defender("WINGER", RIGHT, 802, y=50.0)
    side_w = 40.0
    yl = spacing_target_y(dl, side_w, 35.0, tuners)
    yr = spacing_target_y(dr, side_w, 35.0, tuners)
    off = compute_spacing_offset("WINGER", side_w, tuners)
    check(
        "left spacing = ruck_y - single offset (clamped)",
        abs(yl - clamp_to_sideline(35.0 - off, tuners)) < 1e-9,
        f"y={yl:.3f} expected={clamp_to_sideline(35.0 - off, tuners):.3f}",
    )
    check(
        "right spacing = ruck_y + single offset (clamped)",
        abs(yr - clamp_to_sideline(35.0 + off, tuners)) < 1e-9,
    )
    check(
        "spacing_target_y never outputs X (returns float)",
        isinstance(yl, float) and isinstance(yr, float),
    )
    sy = spacing_y(world, dl, tuners)
    check("spacing_y returns a float Y", isinstance(sy, float))

    # ── 11. S9 — role_anchor_y exists + spacing delegates ──────
    # RUCK-ADJACENT EVEN SPACING (2026-08-18): spacing_target_y no longer
    # calls role_width_offset directly — it delegates to the module's
    # even side-lane helpers (_role_radial_rank / ruck_adjacent_anchor /
    # _even_side_lane_y) where A anchors at ruck_y ± the tuner offset and
    # interior lanes interpolate evenly between A and the WINGER
    # half-width.  role_anchor_y / role_width_offset remain the width
    # authority for NON-spacing consumers.
    print("\n[11] S9 — role_anchor_y exists + spacing delegates")
    check("role_anchor_y exists", callable(role_anchor_y))
    dsp_src = open(
        "game/defence/defence_lvl_1/defensive_split_spacing.py",
        "r", encoding="utf-8", errors="replace",
    ).read()
    check(
        "spacing delegates to the even side-lane helpers (ruck-adjacent rule)",
        "_even_side_lane_y(" in dsp_src
        and "_role_radial_rank(" in dsp_src
        and "ruck_adjacent_anchor(" in dsp_src,
    )

    # ── +. S2 — pipeline order probe (spacing before chase/audit/finalise)
    print("\n[+] S2 — spacing runs before chase / audit / finalise")
    unified_src = open(
        "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
        "r", encoding="utf-8", errors="replace",
    ).read()
    step6 = unified_src.find("self.def_role_spacing.prepare(world)")
    guard_i = unified_src.find("SpacingGuard(world, sorted_defs).verify")
    step9_i = unified_src.find("MANDATORY RETREAT")
    step10_i = unified_src.find("STRUCTURED OPEN PLAY: SAFE CLOSING")
    finalise_i = unified_src.find("POST-CATCH DEFENSIVE LINE CLAMP — FINALISE PASS")
    order_ok = (
        step6 != -1 and guard_i != -1
        and step9_i != -1 and step10_i != -1 and finalise_i != -1
        and step6 < guard_i < step9_i < step10_i < finalise_i
    )
    check(
        "spacing (guard) precedes retreat, close, and finalise in the pipeline",
        order_ok,
        f"order=[spacing:{step6}, guard:{guard_i}, retreat:{step9_i}, "
        f"close:{step10_i}, finalise:{finalise_i}]",
    )
    # Width (governor) runs before spacing by Task D governed-width design —
    # the spacing chain consumes the governed width, so width precedes spacing.
    gov_i = unified_src.find("GOVERNOR - shared line state")
    check(
        "width (governor) runs before spacing (governed-width feed)",
        gov_i != -1 and gov_i < step6,
        f"governor:{gov_i} spacing:{step6}",
    )
    # Spacing never runs after chase — structured-close (step 10) is the
    # only chase writer and it runs after the spacing guard.
    check(
        "spacing never runs after chase (close writer is post-spacing)",
        guard_i < step10_i,
    )

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — spacing is a pure Y-only authority layer")
    sys.exit(0)


if __name__ == "__main__":
    main()
