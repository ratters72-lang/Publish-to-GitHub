"""
verify_width_chain.py — Task I: Width Chain Stabilisation verification.

Proves width recompute is a pure X-only authority subsystem:

  1. I0 — X-ONLY OUTPUT: recompute_width_x emits ONLY {id(d): target_x}
           (int keys, single numeric X values).  No depth / target_y /
           spacing_y / anchor / chase / mmdl keys anywhere.
  2. I1 — OPEN_PLAY GATE: run_width raises WidthOpenPlayGateViolation
           outside OPEN_PLAY; is_open_play / assert_open_play behave.
  3. I2 — 200-frame determinism: identical (defenders, ruck, governed
           width, spacing outputs, tuners) -> byte-identical width dict
           every frame, including under BC band/chase jitter.
  4. I3 — ANCHOR COMPLIANCE: width consumes defensive_line_anchor_m,
           never writes it, never produces an X beyond the anchor band
           (WINGER top depth == ruck_x + 10 == anchor exactly).
  5. I4 — SPACING COMPLIANCE: width treats spacing outputs as read-only
           authority inputs (dict untouched, spacing Y fields untouched).
  6. I5 — ROLE EXEMPTIONS: MARKER_A/B, DUMMY_HALF, TACKLER_*, DEF_CHASE,
           dynamic fullback, PRIMARY and SECONDARY receive NO width
           target; PASSIVE line holders DO.
  7. I6 — AUTHORITY SAFETY: WidthGuard snapshots all forbidden fields +
           anchor + spacing outputs before and asserts zero mutation;
           a simulated mutation / corrupt output / mutated spacing dict
           raises WidthAuthorityViolation.
  8. I7 — WIDTH AUDIT CORRECTNESS: the guard's verify() performs every
           audit (gate, X-only shape, exemptions, no-mutation, anchor,
           spacing) and the structured pipeline wires
           WidthGuard AFTER SpacingGuard in step 7 (governor -> spacing
           -> width recompute -> retreat/close/finalise -> anchor audit
           -> MMDL).

All checks exit 0.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_width import (
    recompute_width_x,
    governed_width_m,
)
from game.defence.defence_lvl_1.defensive_split_spacing import (
    compute_targets as spacing_compute_targets,
)
from game.defence.defence_lvl_1.width_authority import (
    WidthGuard,
    WidthAuthorityViolation,
    WidthOpenPlayGateViolation,
    assert_open_play,
    is_open_play,
    is_width_eligible,
    run_width,
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
        self.anchor_locked = True
        self.in_pursuit_mode = False
        self.first_tackle_not_made = False
        self.governor_width_active = True
        self.governor_line_width_m = 40.0
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
    print("WIDTH CHAIN STABILISATION (Task I) — X-ONLY AUTHORITY")
    print("=" * 72)

    tuners = DefenceTuners()
    world = MiniWorld()

    # ── I1 — OPEN_PLAY gate (also feeds every other section's gate) ──
    print("\n[I1] OPEN_PLAY gate")
    for phase_name, sub in (
        ("TES", GameSubState.TACKLE_CONTACT),
        ("DRIVE", GameSubState.TACKLE_DRIVE),
        ("PTB", GameSubState.PTB),
        ("RELEASE", GameSubState.TACKLE_RELEASE),
        ("POST_RUCK", GameSubState.POST_RUCK_OPEN_PLAY),
    ):
        w = MiniWorld()
        w.sub_state = sub
        raised = False
        try:
            run_width(w, make_line(w), DefenceTuners())
        except WidthOpenPlayGateViolation:
            raised = True
        check(f"run_width refuses during {phase_name}", raised, f"sub={sub}")
    check("is_open_play True in OPEN_PLAY", is_open_play(world))
    w = MiniWorld()
    w.sub_state = "TACKLE_DRIVE"
    check("is_open_play False outside OPEN_PLAY", not is_open_play(w))
    assert_open_play(MiniWorld())
    check("assert_open_play passes in OPEN_PLAY", True)

    # ── I0 — X-only output shape ────────────────────────────────────
    print("\n[I0] X-only output (no Y / depth / anchor / chase / mmdl)")
    line = make_line(world)
    spacing = spacing_compute_targets(world, line, tuners)
    targets = run_width(world, line, tuners, spacing_outputs=spacing)
    forbidden = frozenset({
        "depth", "target_y", "spacing_y", "role_anchor_y",
        "anchor_x", "anchor_y",
        "chase_target_x", "chase_target_y",
        "mmdl_target_x", "mmdl_target_y",
    })
    bad_keys = [k for k in targets if k in forbidden]
    check("no forbidden output keys", not bad_keys, str(bad_keys))
    shape_ok = all(
        isinstance(k, int) and not isinstance(v, dict)
        and isinstance(v, (int, float))
        for k, v in targets.items()
    )
    check(
        "output keys are id(d) with single numeric X values",
        shape_ok,
        f"keys={[k for k in targets if not isinstance(k, int)] or 'ok'}",
    )
    # Width never mutated any defender field (guard read-back).
    check("run_width guard passed without forbidden mutations", True)

    # ── I2 — 200-frame determinism ──────────────────────────────────
    print("\n[I2] deterministic width recompute across 200 frames")
    det_world = MiniWorld()
    det_world.frame_count = 0
    det_line = make_line(det_world)
    det_spacing = spacing_compute_targets(det_world, det_line, tuners)
    first = None
    for frame in range(200):
        det_world.frame_count = frame
        # Same defenders / ruck / governed width / spacing outputs /
        # tuners => identical width dict every frame.  BC position varies
        # (band/chase jitter proxy) to prove width never reads the BC.
        det_world.ball_carrier = type(
            "BC", (), {"x": 65.0 + frame * 0.01, "y": 35.0 + (frame % 3) * 0.1,
                       "vx": 0.0, "vy": 0.0}
        )()
        gw = governed_width_m(det_world, tuners)
        w_frame = recompute_width_x(
            det_world, det_line, tuners,
            governor_width=gw, spacing_targets=det_spacing,
        )
        if first is None:
            first = dict(w_frame)
        if w_frame != first:
            check("identical width dict across 200 frames", False, f"frame={frame}")
            break
    else:
        check(
            "identical width dict across 200 frames (deterministic)",
            True,
            f"sample={sorted(round(v, 3) for v in (first or {}).values())}",
        )
    # Spacing outputs feed the same recompute unchanged -> also stable.
    # NOTE: restore the exact input state the first computation used
    # (ball_carrier = None).  The Task IV engagement-band pull reads
    # world.ball_carrier.x, so recomputing with the loop's trailing BC
    # would legitimately differ — the determinism check must compare
    # like-for-like inputs.
    det_world.ball_carrier = None
    det_spacing_again = spacing_compute_targets(det_world, det_line, tuners)
    check(
        "spacing outputs are deterministic authority inputs for width",
        det_spacing == det_spacing_again,
    )

    # ── I3 — anchor compliance ──────────────────────────────────────
    print("\n[I3] anchor consumer, never producer")
    w = MiniWorld()
    anchor_before = defensive_line_anchor_m(w)
    anchor_state_before = (
        getattr(w, "defensive_line_anchor", None),
        getattr(w, "anchor_locked", None),
    )
    aw_line = make_line(w)
    aw_spacing = spacing_compute_targets(w, aw_line, tuners)
    aw = run_width(w, aw_line, tuners, spacing_outputs=aw_spacing)
    check(
        "anchor authority value unchanged after width recompute",
        abs(defensive_line_anchor_m(w) - anchor_before) < 1e-9,
    )
    check(
        "world anchor fields unchanged after width recompute",
        getattr(w, "defensive_line_anchor", None) == anchor_state_before[0]
        and getattr(w, "anchor_locked", None) == anchor_state_before[1],
    )
    check(
        "width recompute consumes the Task G authority read",
        "defensive_line_anchor_m" in open(
            "game/defence/defence_lvl_1/defensive_width.py",
            "r", encoding="utf-8", errors="replace",
        ).read(),
    )
    # Never pushes a line holder ahead of the anchor band: max width X
    # (WINGER = ruck_x + 10) equals the anchor exactly; nothing exceeds it.
    anchor_now = defensive_line_anchor_m(w)
    max_wx = max(aw.values())
    check(
        "no width output exceeds the anchor band",
        max_wx <= anchor_now + 1e-9,
        f"max_x={max_wx:.4f} anchor={anchor_now:.4f}",
    )

    # ── I4 — spacing compliance ─────────────────────────────────────
    print("\n[I4] spacing outputs are read-only authority inputs")
    w = MiniWorld()
    sp_line = make_line(w)
    sp_out = spacing_compute_targets(w, sp_line, tuners)
    sp_snapshot = dict(sp_out)
    run_width(w, sp_line, tuners, spacing_outputs=sp_out)
    check(
        "width recompute never mutates the spacing output dict",
        sp_out == sp_snapshot,
    )
    # Spacing Y / spacing fields on defenders untouched.
    for d in sp_line:
        y_before = getattr(d, "target_y", None)
        sy_before = getattr(d, "spacing_y", None)
        run_width(w, [d], tuners, spacing_outputs=sp_snapshot)
        check(
            f"spacing Y fields untouched for id={d.player_id}",
            getattr(d, "target_y", None) == y_before
            and getattr(d, "spacing_y", None) == sy_before,
        )

    # ── I5 — role exemptions ────────────────────────────────────────
    print("\n[I5] role exemptions (same predicate as spacing S7)")
    passive = role_defender("D", 1, 601, y=45.0)
    passive.depth_class = "PASSIVE"
    check("PASSIVE line defender width-eligible", is_width_eligible(passive))

    primary = role_defender("A", 1, 602, y=40.0)
    primary.depth_class = "PRIMARY"
    check("PRIMARY closer exempt", not is_width_eligible(primary))
    secondary = role_defender("B", -1, 603, y=30.0)
    secondary.depth_class = "SECONDARY"
    check("SECONDARY closer exempt", not is_width_eligible(secondary))

    for role_name in ("MARKER_A", "MARKER_B", "DUMMY_HALF", "TACKLER_1",
                      "TACKLER_2", "TACKLER_3", "DEF_CHASE"):
        d = role_defender("D", 1, 700 + len(role_name), y=35.0)
        d.defender_role = role_name
        check(f"{role_name} exempt", not is_width_eligible(d))

    fb = role_defender("D", -1, 710, y=40.0)
    fb.fullback_role_active = True
    check("dynamic fullback exempt", not is_width_eligible(fb))

    w = MiniWorld()
    ex_line = make_line(w)
    ex_line[0].defender_role = "MARKER_A"
    ex_line[0].depth_class = "MARKER"
    ex_line[1].depth_class = "PRIMARY"
    ex_line[2].depth_class = "SECONDARY"
    ex_spacing = spacing_compute_targets(w, ex_line, tuners)
    ex = run_width(w, ex_line, tuners, spacing_outputs=ex_spacing)
    exempt_ids = {id(ex_line[0]), id(ex_line[1]), id(ex_line[2])}
    check(
        "exempt defenders receive NO width target",
        all(pid not in ex for pid in exempt_ids),
        f"wide_targets={sorted(ex)}",
    )
    check(
        "eligible PASSIVE defenders receive width targets",
        all(id(d) in ex for i, d in enumerate(ex_line) if i not in (0, 1, 2)),
    )

    # ── I6 — authority safety ───────────────────────────────────────
    print("\n[I6] authority safety (guard raises on any forbidden write)")
    w = MiniWorld()
    safe_line = make_line(w)
    safe_spacing = spacing_compute_targets(w, safe_line, tuners)
    governor_w = governed_width_m(w, tuners)
    guard = WidthGuard(
        w, safe_line, tuners,
        governor_width=governor_w,
        spacing_outputs=safe_spacing,
    )
    ok_targets = recompute_width_x(
        w, safe_line, tuners,
        governor_width=governor_w,
        spacing_targets=safe_spacing,
    )
    guard.verify(ok_targets)  # must pass

    # 1. Corrupt output dict -> verified rejection.
    corrupt = {**ok_targets, "target_y": 30.0}
    raised = False
    try:
        guard.verify(corrupt)
    except WidthAuthorityViolation:
        raised = True
    check("width audit rejects a target_y output key", raised)

    # 2. Defender Y mutation between snapshot and verify -> rejection.
    guard2 = WidthGuard(
        w, safe_line, tuners,
        governor_width=governor_w,
        spacing_outputs=safe_spacing,
    )
    victim = safe_line[3]
    victim.target_y = victim.y + 5.0
    raised = False
    try:
        guard2.verify(ok_targets)
    except WidthAuthorityViolation:
        raised = True
    finally:
        victim.target_y = victim.y
    check("width audit rejects a defender target_y mutation", raised)

    # 3. Spacing-output dict mutation -> rejection.
    guard3 = WidthGuard(
        w, safe_line, tuners,
        governor_width=governor_w,
        spacing_outputs=dict(safe_spacing),
    )
    mutated_spacing = dict(safe_spacing)
    if mutated_spacing:
        k0 = list(mutated_spacing)[0]
        mutated_spacing[k0] = mutated_spacing[k0] + 1.0
    raised = False
    try:
        guard3._spacing_live = mutated_spacing
        guard3.verify(ok_targets)
    except WidthAuthorityViolation:
        raised = True
    check("width audit rejects a spacing-output mutation", raised)

    # 4. Anchor mutation -> rejection.
    guard4 = WidthGuard(
        w, safe_line, tuners,
        governor_width=governor_w,
        spacing_outputs=safe_spacing,
    )
    world_anchor_before = getattr(w, "defensive_line_anchor", None)
    setattr(w, "defensive_line_anchor", 99.0)  # illegal anchor write
    raised = False
    try:
        guard4.verify(ok_targets)
    except WidthAuthorityViolation:
        raised = True
    finally:
        setattr(w, "defensive_line_anchor", world_anchor_before)
    check("width audit rejects an anchor mutation", raised)

    # ── I7 — width audit correctness + pipeline order ───────────────
    print("\n[I7] width audit correctness + chain order in the pipeline")
    unified_src = open(
        "game/defence/defence_lvl_1/defensive_behaviour_unified.py",
        "r", encoding="utf-8", errors="replace",
    ).read()
    gov_i = unified_src.find("GOVERNOR - shared line state")
    spacing_i = unified_src.find("SpacingGuard(world, sorted_defs).verify")
    width_i = unified_src.find("WidthGuard(")
    recompute_i = unified_src.find("recompute_width_x(")
    retreat_i = unified_src.find("MANDATORY RETREAT")
    close_i = unified_src.find("STRUCTURED OPEN PLAY: SAFE CLOSING")
    finalise_i = unified_src.find("POST-CATCH DEFENSIVE LINE CLAMP — FINALISE PASS")
    anchor_i = unified_src.find("enforce_retreat_to_anchor(world, sorted_defs)")
    mmdl_i = unified_src.find("MOVEMENT MODE DECISION LAYER (MMDL)")
    order_ok = (
        gov_i != -1 and spacing_i != -1 and width_i != -1
        and recompute_i != -1 and retreat_i != -1 and close_i != -1
        and finalise_i != -1 and anchor_i != -1 and mmdl_i != -1
        and gov_i < spacing_i < width_i < retreat_i < close_i
        < finalise_i < anchor_i < mmdl_i
    )
    check(
        "chain order governor -> spacing -> width -> retreat/close/finalise "
        "-> anchor audit -> MMDL",
        order_ok,
        f"order=[governor:{gov_i}, spacing:{spacing_i}, width:{width_i}, "
        f"recompute:{recompute_i}, retreat:{retreat_i}, close:{close_i}, "
        f"finalise:{finalise_i}, anchor:{anchor_i}, mmdl:{mmdl_i}]",
    )
    # Width recompute runs AFTER SpacingGuard (spacing outputs feed it).
    check(
        "width recompute consumes the spacing output (post-spacing)",
        recompute_i > spacing_i,
        f"recompute:{recompute_i} spacing:{spacing_i}",
    )
    # Width guard never runs inside a TES/PTB writer: it is ONLY in the
    # structured OPEN_PLAY pipeline (single call site, post-spacing).
    check(
        "WidthGuard call site is inside _apply_structured_open_play (post-spacing)",
        unified_src.count("WidthGuard(") == 1 and width_i > spacing_i,
        f"count={unified_src.count('WidthGuard(')}",
    )

    print("\n" + "=" * 72)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED — width is a pure X-only authority layer")
    sys.exit(0)


if __name__ == "__main__":
    main()
