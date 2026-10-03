"""verify_post_tes_settle.py — Focused verifier for the post-TES drive-end settle window.

The TES drive now leaves defenders at natural BC-relative positions (depth
tracks the live carrier, lateral lanes held/breathe per role).  At
DOMINANCE→RELEASE the ruck forms and the post-TES writers (ruck-pin position
snap, spacing/width clamps) would snap bodies back to ruck-relative targets
on the first RELEASE frame — the teleporting-around-the-ruck symptom.

The fix gates those snaps for a few frames: ``world.post_tes_settle_frames``
is armed at DOMINANCE→RELEASE (tuners.post_tes_settle_frames) and decayed each
frame in OpenPlayEngine.update().  While > 0 the writers are gated; at 0 the
reform/retreat snaps resume.

This verifier proves the THREE writer-level gates:

  1. ruck-pin  → during the settle window the pin is TARGET-ONLY (slot-1
     position NOT snapped, slot-2 position NOT clamped); after decay to 0
     the reform position snap resumes.
  2. spacing   → ``frame_spacing_y`` holds the LAST-APPLIED Y during the
     settle window (no fresh ruck-relative re-pin); after decay it consumes
     the fresh frame dict.
  3. width     → ``recompute_width_x`` holds CURRENT X during the settle
     window; after decay it returns ruck_x + WIDTH_OFFSETS[depth_role].

The engine-level arm (DOMINANCE→RELEASE sets the counter) / decay (update()
decrements) are exercised by reading the engine wiring; the writer gates are
the testable units here.

Run:  python verify_post_tes_settle.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from world.world_state import WorldState, GameSubState
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.ruck_adjacent_pin import (
    enforce_ruck_adjacent_pin,
    LEFT,
    RIGHT,
    ruck_adjacent_anchor,
)
from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y
from game.defence.defence_lvl_1.defensive_width import recompute_width_x
from game.defence.defence_lvl_1.defensive_tuners import post_tes_settle_active


failures = []

# Isolate the settle gate from the slot-tight / `_lane` clamp (a no-BC world
# would pass `_lane` through, but disable it so the test is purely about the
# gate, not the slot clamp).
_tuners = DefenceTuners()
_tuners.slot_tight_spacing_enabled = False


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_defender(pid, x, y, depth_role=None, defender_role=None,
                  fullback=False, tackle_state=False, frozen=False,
                  depth_class="PASSIVE", frame_spacing_y=None):
    d = Player(x=x, y=y, player_id=pid)
    d.depth_role = depth_role
    d.defender_role = defender_role
    d.fullback_role_active = fullback
    d.tackle_state_active = tackle_state
    d.frozen = frozen
    d.freeze_timer = 0.0
    d.target_x = x
    d.target_y = y
    d.vx = 0.0
    d.vy = 0.0
    d.depth_class = depth_class
    if frame_spacing_y is not None:
        d.frame_spacing_y = float(frame_spacing_y)
    return d


def make_world(ruck_y, defenders, frame_spacing_targets=None, phase_label="",
               frame_count=0, settle_frames=0, ruck_x=50.0,
               settle_enabled=False):
    w = WorldState()
    w.ruck_y_m = ruck_y
    w.ruck_x_m = ruck_x
    w.defending_team = defenders
    w.frame_spacing_targets = frame_spacing_targets or {}
    w._ope_phase_label = phase_label
    w.sub_state = GameSubState.OPEN_PLAY
    w.tackler_ids = set()
    w.frame_count = frame_count
    w.post_tes_settle_frames = settle_frames
    w.post_tes_settle_enabled = settle_enabled
    w.defensive_line_anchor = ruck_x + 10.0
    w.def_line_width_m = 40.0
    w.governor_width_active = False
    # Mark the published geometry as fresh for this frame so frame_spacing_y
    # would NORMALLY consume the fresh ruck-relative dict — the settle gate
    # must override that and hold the last-applied instead.
    w.frame_geometry_ready = True
    w.frame_geometry_frame = frame_count
    return w


def main():
    print("=" * 64)
    print("POST-TES DRIVE-END SETTLE WINDOW VERIFIER (2026-09-06)")
    print("=" * 64)

    ruck_y = 32.86

    # ── TEST 1: ruck-pin is TARGET-ONLY during the settle window ──
    # While world.post_tes_settle_frames > 0, the pin must NOT position-snap
    # slot-1 (A-pivot) to the anchor (no teleport); it only pins the TARGET so
    # the body glides there via the movement integrator.
    print("\n[1] ruck-pin: settle window → target-only (no slot-1 position snap)")
    anchor_left = ruck_adjacent_anchor(ruck_y, LEFT)  # 30.86
    a_pivot = make_defender(1, x=50.0, y=27.0, depth_role="A")  # far from anchor
    w = make_world(ruck_y, [a_pivot], phase_label="TACKLE_RELEASE",
                   settle_frames=5)
    enforce_ruck_adjacent_pin(w)
    check("1.1 slot1 target_y pinned to anchor (target-only)",
          abs(a_pivot.target_y - anchor_left) < 1e-6,
          "target_y=%.2f (expected %.2f)" % (a_pivot.target_y, anchor_left))
    check("1.2 slot1 position NOT snapped (settle gate holds position)",
          abs(a_pivot.y - 27.0) < 1e-6,
          "y=%.2f (expected 27.0 — no teleport)" % a_pivot.y)

    # ── TEST 2: ruck-pin post-window → FAR body GLIDES, NEAR body SNAPS ──
    # The reform snap no longer teleports a far A-pivot onto the lane in one
    # frame (RUCK-PIN f=212: 25.73 -> 29.38).  A body farther than
    # RUCK_PIN_GLIDE_THRESHOLD_M glides toward the anchor by at most
    # RUCK_PIN_GLIDE_SPEED_MPS * dt; a body within the threshold still snaps so
    # the pin owns convergence when close.
    print("\n[2] ruck-pin: post-window → far body glides, near body snaps")
    glide_speed = 6.0
    dt = 1.0 / 60.0
    a_pivot2 = make_defender(2, x=50.0, y=27.0, depth_role="A")
    w2 = make_world(ruck_y, [a_pivot2], phase_label="TACKLE_RELEASE",
                    settle_frames=0)
    w2.dt = dt
    enforce_ruck_adjacent_pin(w2)
    _moved = abs(a_pivot2.y - 27.0)
    _max_step = glide_speed * dt
    check("2.1 far A-pivot glides <= max_step (no teleport)",
          _moved > 0.0 and _moved <= _max_step + 1e-6,
          "y=%.4f moved=%.4f (expected a %0.4f cap)" % (a_pivot2.y, _moved, _max_step))
    # Near body (within threshold) still snaps to the anchor.
    _near_y = anchor_left - 0.2
    a_pivot_near = make_defender(3, x=50.0, y=_near_y, depth_role="A")
    w2n = make_world(ruck_y, [a_pivot_near], phase_label="TACKLE_RELEASE",
                     settle_frames=0)
    w2n.dt = dt
    enforce_ruck_adjacent_pin(w2n)
    check("2.2 near A-pivot snaps to anchor (within threshold)",
          abs(a_pivot_near.y - anchor_left) < 1e-6,
          "y=%.2f (expected %.2f)" % (a_pivot_near.y, anchor_left))

    # ── TEST 3: spacing holds last-applied Y during the settle window ──
    # frame_spacing_y must NOT consume a freshly recomputed ruck-relative
    # spacing dict while the settle counter is armed — it returns the
    # drive-era settle slot (d.frame_spacing_y) so the line keeps its shape
    # and glides, not re-pins.
    print("\n[3] spacing: settle window → holds LAST-APPLIED Y (no ruck re-pin)")
    d_sp = make_defender(3, x=50.0, y=40.0, depth_role="WINGER",
                         frame_spacing_y=40.0)  # drive-era settle slot
    tiny_spacer = make_defender(4, x=50.0, y=35.0, depth_role="A")
    w3 = make_world(ruck_y, [d_sp, tiny_spacer],
                    frame_spacing_targets={id(d_sp): 10.0, id(tiny_spacer): 30.86},
                    phase_label="TACKLE_RELEASE", settle_frames=5)
    y3 = frame_spacing_y(w3, d_sp, _tuners)
    check("3.1 settle window spacing Y = last-applied (40.0), not fresh dict (10.0)",
          abs(y3 - 40.0) < 1e-6,
          "y=%.2f (expected 40.0)" % y3)

    # ── TEST 4: spacing resumes consuming the FRESH dict after decay ──
    print("\n[4] spacing: post-window → consumes fresh frame dict")
    w4 = make_world(ruck_y, [d_sp, tiny_spacer],
                    frame_spacing_targets={id(d_sp): 10.0, id(tiny_spacer): 30.86},
                    phase_label="TACKLE_RELEASE", settle_frames=0)
    y4 = frame_spacing_y(w4, d_sp, _tuners)
    check("4.1 post-window spacing Y = fresh dict (10.0)",
          abs(y4 - 10.0) < 1e-6,
          "y=%.2f (expected 10.0)" % y4)

    # ── TEST 5: width holds CURRENT X during the settle window ──
    # recompute_width_x must NOT re-pin to ruck_x + WIDTH_OFFSETS[depth_role]
    # while the settle counter is armed — it holds current x so no width snap
    # lands the instant the drive ends.
    print("\n[5] width: settle window → holds CURRENT X (no width snap)")
    d_w = make_defender(5, x=50.0, y=35.0, depth_role="D")  # WIDTH_OFFSETS[D]=5.0
    w5 = make_world(ruck_y, [d_w], phase_label="TACKLE_RELEASE",
                    settle_frames=5, ruck_x=50.0)
    width5 = recompute_width_x(w5, [d_w], _tuners)
    check("5.1 settle window width X = current x (50.0), not ruck_x+offset (55.0)",
          abs(width5.get(id(d_w), -1.0) - 50.0) < 1e-6,
          "x=%.2f (expected 50.0)" % width5.get(id(d_w), -1.0))

    # ── TEST 6: width resumes ruck_x + WIDTH_OFFSETS after decay ──
    print("\n[6] width: post-window → resumes ruck_x + WIDTH_OFFSETS")
    w6 = make_world(ruck_y, [d_w], phase_label="TACKLE_RELEASE",
                    settle_frames=0, ruck_x=50.0)
    width6 = recompute_width_x(w6, [d_w], _tuners)
    check("6.1 post-window width X = ruck_x + 5.0 (55.0)",
          abs(width6.get(id(d_w), -1.0) - 55.0) < 1e-6,
          "x=%.2f (expected 55.0)" % width6.get(id(d_w), -1.0))

    # ── TEST 7: tuner exists and is a positive int (default 5) ──
    print("\n[7] tuner: post_tes_settle_frames is a positive int")
    _n = getattr(_tuners, "post_tes_settle_frames", None)
    check("7.1 tuner present and > 0",
          isinstance(_n, int) and _n > 0,
          "value=%r" % (_n,))

    # ── TEST 8: post_tes_settle_active — phase-set coverage (2026-09-07) ──
    # With the latch armed and the frame counter burned to 0, the window
    # stays live through PTB_* / POST_RUCK_OPEN_PLAY and disengages at
    # OPEN_PLAY.  0 = disabled keeps the window inert.
    print("\n[8] settle-active: phase-set coverage (counter 0, latch armed)")
    w_ptb = make_world(ruck_y, [], phase_label="PTB_PREP", settle_frames=0,
                       settle_enabled=True)
    check("8.1 PTB_PREP + enabled → settle active",
          post_tes_settle_active(w_ptb) is True)
    w_pr = make_world(ruck_y, [], phase_label="POST_RUCK_OPEN_PLAY",
                      settle_frames=0, settle_enabled=True)
    check("8.2 POST_RUCK_OPEN_PLAY + enabled → settle active",
          post_tes_settle_active(w_pr) is True)
    w_op = make_world(ruck_y, [], phase_label="OPEN_PLAY", settle_frames=0,
                      settle_enabled=True)
    check("8.3 OPEN_PLAY + enabled → settle INACTIVE",
          post_tes_settle_active(w_op) is False)
    w_dis = make_world(ruck_y, [], phase_label="PTB_PREP", settle_frames=0,
                       settle_enabled=False)
    check("8.4 PTB_PREP + disabled (latch off) → settle INACTIVE",
          post_tes_settle_active(w_dis) is False)
    w_cnt = make_world(ruck_y, [], phase_label="PTB_PREP", settle_frames=5,
                       settle_enabled=False)
    check("8.5 counter > 0 always active (legacy path)",
          post_tes_settle_active(w_cnt) is True)

    # ── TEST 9: ruck-pin — phase-gated during PTB (counter 0, latch armed) ──
    print("\n[9] ruck-pin: PTB phase → target-only (no position snap)")
    a_pivot_ptb = make_defender(6, x=50.0, y=27.0, depth_role="A")
    w_ptb2 = make_world(ruck_y, [a_pivot_ptb], phase_label="PTB_PREP",
                        settle_frames=0, settle_enabled=True)
    enforce_ruck_adjacent_pin(w_ptb2)
    check("9.1 PTB slot1 target_y pinned to anchor (target-only)",
          abs(a_pivot_ptb.target_y - anchor_left) < 1e-6,
          "target_y=%.2f (expected %.2f)" % (a_pivot_ptb.target_y, anchor_left))
    check("9.2 PTB slot1 position NOT snapped (settle phase holds position)",
          abs(a_pivot_ptb.y - 27.0) < 1e-6,
          "y=%.2f (expected 27.0 — no teleport)" % a_pivot_ptb.y)

    # ── TEST 10: ruck-pin — phase-gated during POST_RUCK ──
    print("\n[10] ruck-pin: POST_RUCK_OPEN_PLAY → target-only")
    a_pivot_pr = make_defender(7, x=50.0, y=27.0, depth_role="A")
    w_pr2 = make_world(ruck_y, [a_pivot_pr], phase_label="POST_RUCK_OPEN_PLAY",
                       settle_frames=0, settle_enabled=True)
    enforce_ruck_adjacent_pin(w_pr2)
    check("10.1 POST_RUCK slot1 target_y pinned to anchor (target-only)",
          abs(a_pivot_pr.target_y - anchor_left) < 1e-6,
          "target_y=%.2f (expected %.2f)" % (a_pivot_pr.target_y, anchor_left))
    check("10.2 POST_RUCK slot1 position NOT snapped (settle phase holds position)",
          abs(a_pivot_pr.y - 27.0) < 1e-6,
          "y=%.2f (expected 27.0 — no teleport)" % a_pivot_pr.y)

    # ── TEST 11: spacing — phase-gated during PTB ──
    print("\n[11] spacing: PTB phase → holds LAST-APPLIED Y")
    d_sp_ptb = make_defender(8, x=50.0, y=40.0, depth_role="WINGER",
                             frame_spacing_y=40.0)
    tiny_ptb = make_defender(9, x=50.0, y=35.0, depth_role="A")
    w_sp_ptb = make_world(ruck_y, [d_sp_ptb, tiny_ptb],
                          frame_spacing_targets={id(d_sp_ptb): 10.0,
                                                 id(tiny_ptb): 30.86},
                          phase_label="PTB_PREP", settle_frames=0,
                          settle_enabled=True)
    y_sp_ptb = frame_spacing_y(w_sp_ptb, d_sp_ptb, _tuners)
    check("11.1 PTB spacing Y = last-applied (40.0), not fresh dict (10.0)",
          abs(y_sp_ptb - 40.0) < 1e-6, "y=%.2f (expected 40.0)" % y_sp_ptb)

    # ── TEST 12: width — phase-gated during POST_RUCK ──
    print("\n[12] width: POST_RUCK phase → holds CURRENT X")
    d_w_pr = make_defender(10, x=50.0, y=35.0, depth_role="D")
    w_pr3 = make_world(ruck_y, [d_w_pr], phase_label="POST_RUCK_OPEN_PLAY",
                       settle_frames=0, settle_enabled=True, ruck_x=50.0)
    width_pr = recompute_width_x(w_pr3, [d_w_pr], _tuners)
    check("12.1 POST_RUCK width X = current x (50.0), not ruck_x+offset (55.0)",
          abs(width_pr.get(id(d_w_pr), -1.0) - 50.0) < 1e-6,
          "x=%.2f (expected 50.0)" % width_pr.get(id(d_w_pr), -1.0))

    # ── TEST 13: OPEN_PLAY disengages (spacing fresh, width ruck_x+offset) ──
    print("\n[13] OPEN_PLAY: settle window disengaged")
    d_sp_op = make_defender(11, x=50.0, y=40.0, depth_role="WINGER",
                            frame_spacing_y=40.0)
    tiny_op = make_defender(12, x=50.0, y=35.0, depth_role="A")
    w_sp_op = make_world(ruck_y, [d_sp_op, tiny_op],
                         frame_spacing_targets={id(d_sp_op): 10.0,
                                                id(tiny_op): 30.86},
                         phase_label="OPEN_PLAY", settle_frames=0,
                         settle_enabled=True)
    y_sp_op = frame_spacing_y(w_sp_op, d_sp_op, _tuners)
    check("13.1 OPEN_PLAY spacing Y = fresh dict (10.0)",
          abs(y_sp_op - 10.0) < 1e-6, "y=%.2f (expected 10.0)" % y_sp_op)
    d_w_op = make_defender(13, x=50.0, y=35.0, depth_role="D")
    w_op2 = make_world(ruck_y, [d_w_op], phase_label="OPEN_PLAY",
                       settle_frames=0, settle_enabled=True, ruck_x=50.0)
    width_op = recompute_width_x(w_op2, [d_w_op], _tuners)
    check("13.2 OPEN_PLAY width X = ruck_x + 5.0 (55.0)",
          abs(width_op.get(id(d_w_op), -1.0) - 55.0) < 1e-6,
          "x=%.2f (expected 55.0)" % width_op.get(id(d_w_op), -1.0))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
