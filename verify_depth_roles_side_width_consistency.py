"""verify_depth_roles_side_width_consistency.py — Focused verifier.

Confirms the depth-role chain (defensive_depth_roles._build_role_anchors) now
builds the structural role anchors from the SAME PER-SIDE widths the spacing
chain (side_widths / _clamped_winger_y) consumes, so the short-side width is
consistent across both chains.

The bug: _build_role_anchors used a single FULL governed_width_m for BOTH sides,
so the short-side WINGER role anchor landed at the sideline MARGIN (3.0) while
the spacing chain's WINGER target (available-room-capped per-side width) landed
at MARGIN+BUFFER (6.0) — two different side_width values in the line->spacing
chain, making the short-side width wrong.

Run:  python verify_depth_roles_side_width_consistency.py
Exit 0 on success; prints a PASS/FAIL line per assertion.
"""
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from world.world_state import WorldState
from player.player import Player
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_split_spacing import (
    side_widths, _clamped_winger_y, LEFT, RIGHT,
)
from game.defence.defence_lvl_1.defensive_depth_roles import (
    _build_role_anchors,
)
from game.defence.defence_lvl_1.defensive_width import (
    role_anchor_y_side,
)


failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def make_world(ruck_y):
    w = WorldState()
    w.ruck_x_m = 60.0
    w.ruck_y_m = ruck_y
    w.def_line_width_m = 40.0
    w.governor_width_active = False
    w._post_retreat_open_play_frames = 99
    w.short_side_width_m = None
    w.open_side_width_m = None
    return w


def main():
    print("=" * 64)
    print("DEPTH-ROLE vs SPACING SIDE-WIDTH CONSISTENCY (2026-09-02)")
    print("=" * 64)

    tuners = DefenceTuners()

    def make_wing(side):
        d = Player(x=70.0, y=35.0, player_id=1)
        d.depth_role = "WINGER"
        d.depth_side = side
        d._depth_seeded = True
        return d

    for ruck_y in (10.0, 20.0, 30.0, 50.0, 60.0):
        w = make_world(ruck_y)
        left_w, right_w = side_widths(w, tuners)

        # NEW depth-role chain: per-side widths.
        anchors = _build_role_anchors(ruck_y, left_w, right_w, tuners)
        dr_wl = anchors[LEFT]["WINGER"]
        dr_wr = anchors[RIGHT]["WINGER"]

        # Spacing chain WINGER targets (the SAME per-side widths).
        sp_wl = _clamped_winger_y(left_w, ruck_y, LEFT, tuners)
        sp_wr = _clamped_winger_y(right_w, ruck_y, RIGHT, tuners)

        # SINCE 2026-09-03: role_anchor_y_side is the SINGLE per-side width
        # canonical anchor consumed by frame_spacing_y fallback + structured
        # close.  It MUST equal the spacing WINGER on BOTH sides (one width
        # source for the entire defensive line).
        ra_wl = role_anchor_y_side(make_wing(LEFT), ruck_y, w, tuners)
        ra_wr = role_anchor_y_side(make_wing(RIGHT), ruck_y, w, tuners)

        print("\nruck_y=%.0f  left_w=%.2f right_w=%.2f" % (ruck_y, left_w, right_w))
        print("  depth-role WINGER: L=%.2f R=%.2f" % (dr_wl, dr_wr))
        print("  spacing   WINGER: L=%.2f R=%.2f" % (sp_wl, sp_wr))
        print("  side-anchor WINGER: L=%.2f R=%.2f" % (ra_wl, ra_wr))

        check("ruck_y=%.0f LEFT depth-role WINGER == spacing WINGER" % ruck_y,
              abs(dr_wl - sp_wl) < 1e-6,
              "dr=%.4f sp=%.4f" % (dr_wl, sp_wl))
        check("ruck_y=%.0f RIGHT depth-role WINGER == spacing WINGER" % ruck_y,
              abs(dr_wr - sp_wr) < 1e-6,
              "dr=%.4f sp=%.4f" % (dr_wr, sp_wr))
        check("ruck_y=%.0f LEFT side-anchor WINGER == spacing WINGER" % ruck_y,
              abs(ra_wl - sp_wl) < 1e-6,
              "ra=%.4f sp=%.4f" % (ra_wl, sp_wl))
        check("ruck_y=%.0f RIGHT side-anchor WINGER == spacing WINGER" % ruck_y,
              abs(ra_wr - sp_wr) < 1e-6,
              "ra=%.4f sp=%.4f" % (ra_wr, sp_wr))

    # ── Explicit short-side case (ruck_y=10): the depth-role WINGER_L must be
    #    at MARGIN+BUFFER (6.0), NOT the old full-width MARGIN (3.0). ──
    # GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the
    # short-side "margin + buffer" reduction is REMOVED.  The governed width
    # is now distributed across the two ruck-anchored halves, so the short
    # side's half-span is exactly what the ABSOLUTE sideline clamp can hold
    # and its WINGER lands ON the margin instead of being held a buffer
    # inside it.  The absolute sideline clamp is the only safety bound left.
    print("\n[short-side] ruck_y=10 depth-role WINGER_L == its own half-span")
    w = make_world(10.0)
    left_w, right_w = side_widths(w, tuners)
    anchors = _build_role_anchors(10.0, left_w, right_w, tuners)
    dr_wl = anchors[LEFT]["WINGER"]
    ra_wl = role_anchor_y_side(make_wing(LEFT), 10.0, w, tuners)
    want_wl = max(float(tuners.sideline_margin_m),
                  10.0 - float(left_w) * 0.5)
    check("depth-role WINGER_L lands on its own half-span anchor",
          abs(dr_wl - want_wl) < 1e-6,
          "WINGER_L=%.4f expected %.4f" % (dr_wl, want_wl))
    check("side-anchor WINGER_L == depth-role WINGER_L (single width source)",
          abs(ra_wl - dr_wl) < 1e-6,
          "side-anchor=%.4f depth-role=%.4f" % (ra_wl, dr_wl))
    check("short-side WINGER_L never crosses the ABSOLUTE sideline margin",
          dr_wl >= float(tuners.sideline_margin_m) - 1e-9,
          "WINGER_L=%.4f margin=%.4f" % (dr_wl, tuners.sideline_margin_m))

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
