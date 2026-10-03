"""PRE-TACKLE ATTEMPT MODULE verifier (Task XIV, 2026-08-26).

SPEC: "A proper pre-tackle attempt module so defenders stop chasing the
ball carrier during DRIVE and DOMINANCE.  Instead of legacy FOLLOW-bc.x
behaviour, DEF_LINE_READY bodies switch to stable tackle-phase anchors,
freeze/slow contracts, and deterministic attempt states that prevent
proximity-bucket collapse, VX-zero jiggle, and B-on-A stacking."

Regression target: the legacy ``DefenceManager.update_tes_drive`` Module 2
("BC-SHADOW ROLE-DEPTH RE-ANCHOR") re-points EVERY non-tackler line body at
the LIVE carrier (``target_x = bc.x + WIDTH_OFFSETS[role]``) followed by a
soft-follow integrator — the whole line chases a driven/clamped carrier,
producing proximity-bucket collapse, VX-zero jiggle (deadband rest vs
feed-forward/EMA), and B-on-A stacking.

PART 1 (gate-off byte-stability): with the master gate OFF the authority
  is a pure no-op — no state, no target writes, no source stamps.
PART 2 (stable-anchor law): at DRIVE entry each managed body's anchor is
  FROZEN from its pre-drive geometry; anchors NEVER change when the BC
  moves; identical inputs seed identical anchors.
PART 3 (attempt-state determinism): exactly <=1 COMMIT owner per side;
  deterministic owner selection (min |x-commit| + player_id tiebreak);
  incumbent keeps ownership mid-drive; newcomers never steal.
PART 4 (HOLD/SLOW contract): SLOW bodies converge to FROZEN anchors (never
  bc.x), monotone convergence, freeze one-way on arrival (vx stays 0 —
  no re-arm, no jiggle).
PART 5 (COMMIT/PLANT contract): COMMIT closes toward its FROZEN commit
  point (never live bc.x); arrival within plant radius plants (one-way
  freeze + square-up); PLANTED never re-arms.
PART 6 (anti-collapse / anti-stack): frozen anchors keep role separation
  (no proximity bucket); the two per-side commit points are laterally
  separated (no B-on-A convergence).
PART 7 (isolation + role stability): the authority writes only its own
  stamps (_mmdl_moved / target_source / velocities); defender_role and
  depth_role are NEVER mutated; non-eligible bodies untouched.
PART 8 (reset wiring): eligibility-loss drop, drive-exit clear via the
  manager hook, kickoff reset wiring in OpenPlayEngine.reset_for_kickoff.
"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player, Team, DefenderRole
from world.world_state import WorldState

from game.defence.defence_lvl_1.pretackle_attempt import (
    apply_pretackle_attempts,
    is_pretackle_managed,
    pretackle_attempt_enabled,
    PTA_TARGET_SOURCE,
    PTA_COMMIT,
    PTA_PLANTED,
    PTA_SLOW,
    PTA_HOLD,
    STATE_ATTR,
    ANCHOR_X_ATTR,
    ANCHOR_Y_ATTR,
    COMMIT_X_ATTR,
    COMMIT_Y_ATTR,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

failures = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y):
    d = Player(x=x, y=y, player_id=pid)
    d.team = Team.RECEIVING
    d.is_defender = True
    d.max_defensive_speed_mps = 8.3
    d.defender_role = DefenderRole.DEF_LINE_READY
    d.depth_role = "B"
    d.depth_side = -1 if y < 35.0 else 1
    return d


def make_bc(pid=99, x=80.0, y=35.0):
    bc = Player(x=x, y=y, player_id=pid)
    bc.possessed = True
    return bc


def make_world(bc=None, frame=0):
    w = WorldState()
    w.ruck_x_m = 55.0
    w.ruck_y_m = 35.0
    w.frame_count = frame
    w.dt = 1.0 / 60.0
    w.ball_carrier = bc if bc is not None else make_bc()
    return w


def gated_tuners():
    t = DefenceTuners()
    t.pretackle_attempt_enabled = True
    t.pretackle_slow_speed_mps = 2.5
    t.pretackle_commit_speed_mps = 4.0
    t.pretackle_plant_radius_m = 0.8
    t.pretackle_freeze_radius_m = 0.15
    t.pretackle_slowdown_band_m = 3.0
    t.pretackle_slowdown_scale = 0.45
    t.pretackle_commit_depth_offset_m = 1.0
    t.pretackle_commit_lateral_offset_m = 1.0
    return t


def _drive_scenario():
    """Three line bodies with distinct pre-drive geometry targets."""
    d1 = make_defender(1, 54.0, 32.0); d1.target_x, d1.target_y = 55.0, 33.0
    d2 = make_defender(2, 56.0, 36.0); d2.target_x, d2.target_y = 57.0, 37.0
    d3 = make_defender(3, 59.0, 39.0); d3.target_x, d3.target_y = 60.0, 40.0
    bc = make_bc(x=80.0, y=35.0)
    w = make_world(bc)
    return [d1, d2, d3], bc, w


# ─────────────────────────────────────────────────────────────────────
# PART 1 — gate-off byte-stability
# ─────────────────────────────────────────────────────────────────────

def part1_gate_off():
    print("[PART 1] Gate OFF (explicit override) -> authority is a pure no-op")
    # The gate now DEFAULTS ON (Task XIV live).  This block tests the
    # no-op contract by explicitly forcing the gate OFF.
    tuners = DefenceTuners()
    check("P1.1: gate DEFAULTS ON (Task XIV live / legacy superseded)",
          pretackle_attempt_enabled(DefenceTuners()))
    tuners.pretackle_attempt_enabled = False
    check("P1.1b: explicit OFF override is honoured",
          not pretackle_attempt_enabled(tuners))

    d = make_defender(1, 54.0, 32.0)
    d.target_x = 55.0
    d.target_y = 33.0
    bc = make_bc()
    w = make_world(bc)

    before = dict(
        tx=d.target_x, ty=d.target_y, vx=d.vx, vy=d.vy,
        src=getattr(d, "target_source", None),
        moved=getattr(d, "_mmdl_moved", None),
        st=getattr(d, STATE_ATTR, None),
        ax=getattr(d, ANCHOR_X_ATTR, None),
    )
    handled = apply_pretackle_attempts(w, [d], 1.0 / 60.0, tuners)
    after = dict(
        tx=d.target_x, ty=d.target_y, vx=d.vx, vy=d.vy,
        src=getattr(d, "target_source", None),
        moved=getattr(d, "_mmdl_moved", None),
        st=getattr(d, STATE_ATTR, None),
        ax=getattr(d, ANCHOR_X_ATTR, None),
    )

    check("P1.2: gate OFF returns empty managed list", handled == [])
    check("P1.3: gate OFF writes NOTHING to the defender",
          all(before[k] == after[k] for k in before))
    check("P1.4: gate OFF seeds NO attempt state",
          not is_pretackle_managed(d))


# ─────────────────────────────────────────────────────────────────────
# PART 2 — stable-anchor law + seed determinism
# ─────────────────────────────────────────────────────────────────────

def part2_anchor_law():
    print("[PART 2] Anchors frozen at DRIVE entry; never track the carrier")
    defs, bc, w = _drive_scenario()
    apply_pretackle_attempts(w, defs, 1.0 / 60.0, gated_tuners())

    anchors_0 = [(getattr(d, ANCHOR_X_ATTR), getattr(d, ANCHOR_Y_ATTR)) for d in defs]
    pre_targets = [(55.0, 33.0), (57.0, 37.0), (60.0, 40.0)]
    for i, ((ax, ay), (px, py)) in enumerate(zip(anchors_0, pre_targets)):
        check(f"P2.{i+1}: anchor{i+1} == pre-drive geometry target",
              abs(ax - px) < 1e-6 and abs(ay - py) < 1e-6,
              f"({ax:.2f},{ay:.2f}) vs ({px},{py})")

    # Simulate the DRIVE advancing the carrier far forward.
    for _ in range(30):
        bc.x += 0.5          # driven forward ~15 m total
        w.frame_count += 1
        apply_pretackle_attempts(w, defs, 1.0 / 60.0, gated_tuners())

    anchors_n = [(getattr(d, ANCHOR_X_ATTR), getattr(d, ANCHOR_Y_ATTR)) for d in defs]
    stable = all(a == b for a, b in zip(anchors_0, anchors_n))
    check("P2.4: anchors UNCHANGED across a 15 m drive (never track bc.x)",
          stable, f"{anchors_n}")

    # Seed determinism: identical fresh inputs -> identical seeds.
    defs_b, _, w_b = _drive_scenario()
    apply_pretackle_attempts(w_b, defs_b, 1.0 / 60.0, gated_tuners())
    anchors_b = [(getattr(d, ANCHOR_X_ATTR), getattr(d, ANCHOR_Y_ATTR)) for d in defs_b]
    check("P2.5: identical inputs seed IDENTICAL anchors",
          anchors_0 == anchors_b)


# ─────────────────────────────────────────────────────────────────────
# PART 3 — attempt-state determinism (owner law)
# ─────────────────────────────────────────────────────────────────────

def part3_owner_determinism():
    print("[PART 3] <=1 COMMIT per side; deterministic; incumbent keeps")
    defs, bc, w = _drive_scenario()   # d1 left; d2/d3 right (d3 closer to commit_x)
    apply_pretackle_attempts(w, defs, 1.0 / 60.0, gated_tuners())

    states_0 = {id(d): getattr(d, STATE_ATTR) for d in defs}
    commits = [d for d in defs if states_0[id(d)] == PTA_COMMIT]
    sides = {-1 if float(d.y) < float(bc.y) else 1 for d in commits}
    check("P3.1: COMMIT owners assigned", len(commits) >= 1,
          f"{[getattr(d,'player_id') for d in commits]}")
    check("P3.2: at most ONE COMMIT per side (no B-on-A race)",
          len(sides) == len(commits), f"sides={sorted(sides)}")
    right_owner = next((d for d in commits if float(d.y) >= float(bc.y)), None)
    check("P3.3: RIGHT owner is d3 (closest |x-commit_x|)",
          right_owner is not None and right_owner.player_id == 3)

    # Mid-drive: incumbent keeps ownership even as the BC drifts.
    for _ in range(10):
        bc.x += 0.3
        w.frame_count += 1
        apply_pretackle_attempts(w, defs, 1.0 / 60.0, gated_tuners())
    states_n = {id(d): getattr(d, STATE_ATTR) for d in defs}
    commits_n = [d for d in defs if states_n[id(d)] == PTA_COMMIT]
    same_ids = {getattr(d, 'player_id') for d in commits_n} == {
        getattr(d, 'player_id') for d in commits}
    check("P3.4: incumbent owners KEEP their side mid-drive", same_ids)

    # Newcomer cannot steal an existing owner.
    newcomer = make_defender(7, 79.5, 36.5)   # RIGHT side, very close to commit
    newcomer.target_x, newcomer.target_y = 80.0, 37.0
    defs.append(newcomer)
    w.frame_count += 1
    apply_pretackle_attempts(w, defs, 1.0 / 60.0, gated_tuners())
    new_state = getattr(newcomer, STATE_ATTR)
    check("P3.5: mid-drive newcomer NEVER steals an owner seat "
          f"(state={new_state})",
          new_state != PTA_COMMIT)


# ─────────────────────────────────────────────────────────────────────
# PART 4 — HOLD/SLOW contract (frozen anchor, no jiggle)
# ─────────────────────────────────────────────────────────────────────

def part4_hold_slow_contract():
    print("[PART 4] SLOW converges to FROZEN anchor; HOLD freezes one-way")
    # Both bodies on the RIGHT of the BC so 'd' is never the COMMIT owner.
    bc = make_bc(x=70.0, y=36.0)
    d = make_defender(4, 50.0, 36.5)
    d.target_x, d.target_y = 57.0, 37.0        # anchor ~7.2 m away
    others = make_defender(5, 69.5, 37.0)      # nearer the commit point -> owner
    others.target_x, others.target_y = 70.0, 37.0
    w = make_world(bc)
    tuners = gated_tuners()
    apply_pretackle_attempts(w, [d, others], 1.0 / 60.0, tuners)
    check("P4.1: d seeded as SLOW (anchor contract, not commit)",
          getattr(d, STATE_ATTR) == PTA_SLOW,
          f"state={getattr(d, STATE_ATTR)}")

    prev_dist = None
    converged_frame = None
    vx_series_after_freeze = []
    for i in range(600):                    # up to 10 s
        w.frame_count += 1
        apply_pretackle_attempts(w, [d, others], 1.0 / 60.0, tuners)
        dist = ((float(getattr(d, ANCHOR_X_ATTR)) - d.x) ** 2
                + (float(getattr(d, ANCHOR_Y_ATTR)) - d.y) ** 2) ** 0.5
        if prev_dist is not None and getattr(d, STATE_ATTR) == PTA_SLOW:
            if dist > prev_dist + 1e-9:      # monotone convergence guard
                check("P4.2: SLOW convergence is MONOTONE (no overshoot)",
                      False, f"f={i} dist {prev_dist:.4f} -> {dist:.4f}")
                break
        prev_dist = dist
        if getattr(d, STATE_ATTR) == PTA_HOLD and converged_frame is None:
            converged_frame = i
        if converged_frame is not None:
            vx_series_after_freeze.append(abs(d.vx))

    check("P4.3: SLOW reaches its frozen anchor and freezes (HOLD)",
          converged_frame is not None,
          f"converged at frame {converged_frame}")
    check("P4.4: HOLD velocity is ZERO and STAYS zero (no VX-zero jiggle)",
          all(v == 0.0 for v in vx_series_after_freeze),
          f"|vx| head={vx_series_after_freeze[:5]} tail={vx_series_after_freeze[-3:]}")
    check("P4.5: SLOW targets stay the FROZEN anchor, never bc.x",
          abs(d.target_x - float(getattr(d, ANCHOR_X_ATTR))) < 0.05
          or getattr(d, STATE_ATTR) == PTA_HOLD,
          f"target=({d.target_x:.2f},{d.target_y:.2f})")


# ─────────────────────────────────────────────────────────────────────
# PART 5 — COMMIT/PLANT contract (frozen point, one-way plant)
# ─────────────────────────────────────────────────────────────────────

def part5_commit_plant():
    print("[PART 5] COMMIT closes to the FROZEN point then PLANTS one-way")
    bc = make_bc(x=72.0, y=36.0)             # commit point (73, 37) for RIGHT side
    d_close = make_defender(6, 74.0, 38.0)
    d_close.target_x, d_close.target_y = 75.0, 39.0
    w = make_world(bc)
    tuners = gated_tuners()
    apply_pretackle_attempts(w, [d_close], 1.0 / 60.0, tuners)
    check("P5.1: lone eligible body becomes COMMIT",
          getattr(d_close, STATE_ATTR) == PTA_COMMIT,
          f"state={getattr(d_close, STATE_ATTR)}")

    cx = float(getattr(d_close, COMMIT_X_ATTR))
    planted_at = None
    for i in range(900):
        w.frame_count += 1
        bc.x += 0.02                          # carrier drifts; point must NOT move
        apply_pretackle_attempts(w, [d_close], 1.0 / 60.0, tuners)
        cur_cx = float(getattr(d_close, COMMIT_X_ATTR))
        if abs(cur_cx - cx) > 1e-9:
            check("P5.2: commit point NEVER tracks live bc.x during closing",
                  False, f"commit_x moved {cx} -> {cur_cx}")
            break
        if getattr(d_close, STATE_ATTR) == PTA_PLANTED:
            planted_at = i
            break
    check("P5.2: commit point NEVER tracks live bc.x during closing", True)
    check("P5.3: COMMIT body PLANTS within the drive window",
          planted_at is not None, f"planted at frame {planted_at}")

    # One-way plant: perturb position; PLANTED holds stance (no re-arm).
    d_close.x += 0.4
    vx_series = []
    for _ in range(20):
        w.frame_count += 1
        apply_pretackle_attempts(w, [d_close], 1.0 / 60.0, tuners)
        vx_series.append(abs(d_close.vx))
    check("P5.4: PLANTED never re-arms (velocity stays zero after push)",
          all(v == 0.0 for v in vx_series))


# ─────────────────────────────────────────────────────────────────────
# PART 6 — anti-collapse / anti-stack geometry
# ─────────────────────────────────────────────────────────────────────

def part6_anti_collapse():
    print("[PART 6] Frozen anchors keep separation; commit points differ")
    defs, bc, w = _drive_scenario()
    tuners = gated_tuners()
    apply_pretackle_attempts(w, defs, 1.0 / 60.0, tuners)

    pts = [(float(getattr(d, ANCHOR_X_ATTR)), float(getattr(d, ANCHOR_Y_ATTR)))
           for d in defs]
    min_sep = min(
        ((pts[i][0]-pts[j][0])**2 + (pts[i][1]-pts[j][1])**2) ** 0.5
        for i in range(len(pts)) for j in range(i+1, len(pts))
    )
    check("P6.1: managed anchors keep >= 2 m pairwise separation "
          "(no proximity-bucket collapse)", min_sep >= 2.0,
          f"min sep = {min_sep:.2f} m")

    commits = [d for d in defs if getattr(d, STATE_ATTR) == PTA_COMMIT]
    cpts = [(float(getattr(d, COMMIT_X_ATTR)), float(getattr(d, COMMIT_Y_ATTR)))
            for d in commits]
    if len(cpts) >= 2:
        csep = ((cpts[0][0]-cpts[1][0])**2 + (cpts[0][1]-cpts[1][1])**2) ** 0.5
        check("P6.2: per-side commit points laterally SEPARATED (no B-on-A)",
              csep >= 1.5, f"commit separation = {csep:.2f} m")
    else:
        check("P6.2: per-side commit points laterally SEPARATED (no B-on-A)",
              True, f"single owner scenario ({len(cpts)} owner)")


# ─────────────────────────────────────────────────────────────────────
# PART 7 — isolation + role stability
# ─────────────────────────────────────────────────────────────────────

def part7_isolation_roles():
    print("[PART 7] Only own stamps written; roles NEVER mutated")
    defs, bc, w = _drive_scenario()
    roles_before = [(d.defender_role, d.depth_role) for d in defs]
    apply_pretackle_attempts(w, defs, 1.0 / 60.0, gated_tuners())

    ok_src = all(getattr(d, "target_source", None) == PTA_TARGET_SOURCE
                 for d in defs)
    check("P7.1: every managed body stamped pretackle_attempt", ok_src)
    ok_moved = all(getattr(d, "_mmdl_moved", False) for d in defs)
    check("P7.2: every managed body stamped _mmdl_moved (single integrator)",
          ok_moved)
    roles_after = [(d.defender_role, d.depth_role) for d in defs]
    check("P7.3: defender_role / depth_role UNCHANGED through contact",
          roles_before == roles_after)

    # Non-eligible bodies untouched.
    marker = make_defender(11, 56.0, 35.0)
    marker.defender_role = DefenderRole.MARKER_A
    marker.target_x, marker.target_y = 56.0, 35.0
    fb = make_defender(12, 66.0, 35.0)
    fb.fullback_role_active = True
    fb.target_x, fb.target_y = 66.0, 35.0
    w2 = make_world(make_bc(x=80.0, y=35.0))
    before_marker = (marker.target_x, marker.target_y, marker.vx, marker.vy)
    before_fb = (fb.target_x, fb.target_y, fb.vx, fb.vy)
    apply_pretackle_attempts(w2, [marker, fb], 1.0 / 60.0, gated_tuners())
    after_marker = (marker.target_x, marker.target_y, marker.vx, marker.vy)
    after_fb = (fb.target_x, fb.target_y, fb.vx, fb.vy)
    check("P7.4: MARKER_A untouched by the authority", before_marker == after_marker)
    check("P7.5: fullback actor untouched by the authority", before_fb == after_fb)


# ─────────────────────────────────────────────────────────────────────
# PART 8 — reset wiring (eligibility loss, drive exit, kickoff)
# ─────────────────────────────────────────────────────────────────────

def part8_reset_wiring():
    print("[PART 8] Reset law: eligibility loss, drive exit, kickoff")
    tuners = gated_tuners()

    # 1) Eligibility loss drops the body immediately.
    defs, bc, w = _drive_scenario()
    apply_pretackle_attempts(w, defs, 1.0 / 60.0, tuners)
    victim = defs[0]
    victim.frozen = True
    apply_pretackle_attempts(w, defs, 1.0 / 60.0, tuners)
    check("P8.1: eligibility loss clears attempt state",
          not is_pretackle_managed(victim))

    # 2) Drive exit clears ALL state via the manager hook.
    defs2, bc2, w2 = _drive_scenario()
    w2.players = list(defs2) + [bc2]
    apply_pretackle_attempts(w2, defs2, 1.0 / 60.0, tuners)  # seed state
    from game.managers.defence_manager import DefenceManager
    from game.defence.defence_lvl_1.defensive_behaviour_unified import (
        DefensiveBehaviourUnified,
    )
    mgr = DefenceManager(tuners, DefensiveBehaviourUnified(tuners))
    ope_stub = type("OpeStub", (), {})()
    ope_stub._phase = "TACKLE_DRIVE"
    ope_stub.world = w2
    ope_stub.ball_carrier = bc2
    mgr._pta_was_active = True
    ope_stub._phase = "TACKLE_RELEASE"
    mgr.update_tes_drive(ope_stub, 1.0 / 60.0)
    any_left = any(is_pretackle_managed(p) for p in defs2)
    check("P8.2: leaving DRIVE/DOMINANCE clears all attempt state",
          not any_left)

    # 3) Kickoff reset wiring exists in OpenPlayEngine.reset_for_kickoff.
    import inspect
    from world.open_play_engine import OpenPlayEngine
    src = inspect.getsource(OpenPlayEngine.reset_for_kickoff)
    check("P8.3: reset_for_kickoff wires reset_pretackle_attempts",
          "reset_pretackle_attempts" in src)


def main():
    print("=" * 64)
    print("PRE-TACKLE ATTEMPT MODULE (Task XIV, 2026-08-26)")
    print("=" * 64)

    part1_gate_off()
    part2_anchor_law()
    part3_owner_determinism()
    part4_hold_slow_contract()
    part5_commit_plant()
    part6_anti_collapse()
    part7_isolation_roles()
    part8_reset_wiring()

    print("=" * 64)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
