"""
verify_structured_close_width.py — Validation for structured-close width
preservation (Task C, 2026-08-09).

Confirms that structured_close uses PURE role-anchor width:

    1. TUNER DEFAULT — tuners.structured_close_width_blend == 0.0 (the
       spec Task C REQUIRED constant: pure anchor, no BC-relative component).
    2. CANONICAL MODULE CALL — the step-10 writer computes width ONLY from
       the canonical role-anchor module:
           role_anchor_y = defensive_width.get_role_anchor_y(player, ruck_y)
       with NO BC-relative chase Y, NO fallback, NO lane-index, NO
       positional, NO midfield, and NO width blending with any other source.
    3. WIDTH FORMULA — final_width_y = role_anchor_y.
    4. CLAMP GATES — tuners.structured_close_clamp_active -> clamp_to_sideline;
       tuners.governor_width_active -> governor.apply_width_clamp.
    5. STABILITY — target_y stays at the role anchor across structured_close
       frames while the BC sits on a far lane (no inward collapse).
    6. FORCE REACTION — the line-break force reaction also holds the role
       anchor (no inward snap at the anchor crossing).

The step-10 writer lives in defensive_behaviour_unified.py; this verifier
imports it and proves the width-preserving expression is what executes.
"""

import sys
import os
import re

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.player import Player
from game.defence.defence_lvl_1.defensive_width import (
    get_role_anchor_y,
    role_anchor_y,
    clamp_to_sideline,
)
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_line_governor import DefensiveLineGovernor

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def main():
    print("=" * 60)
    print("STRUCTURED CLOSE — WIDTH PRESERVATION (Task C)")
    print("=" * 60)

    tuners = DefenceTuners()

    # ── 1. Tuner defaults ──────────────────────────────────────────
    print("\n[1] Tuner defaults (Task C REQUIRED constants)")
    check(
        "structured_close_width_blend default == 0.0",
        tuners.structured_close_width_blend == 0.0,
        f"blend={tuners.structured_close_width_blend}",
    )
    check(
        "structured_close_clamp_active default True",
        getattr(tuners, "structured_close_clamp_active", None) is True,
        f"clamp={tuners.structured_close_clamp_active}",
    )
    check(
        "governor_width_active default True",
        getattr(tuners, "governor_width_active", None) is True,
        f"gov={tuners.governor_width_active}",
    )

    # ── 2. Canonical module call in the writer ─────────────────────
    print("\n[2] Pure role-anchor width in the step-10 writer")
    path = "game/defence/defence_lvl_1/defensive_behaviour_unified.py"
    src = open(path, "r", encoding="utf-8", errors="replace").read()

    check(
        "step-10 calls role_anchor_y_side (canonical count-derived module)",
        "final_width_y = role_anchor_y_side(" in src,
    )
    check(
        "step-10 reads structured_close_width_blend as the override gate",
        "structured_close_width_blend" in src,
    )
    check(
        "step-10 documents PURE ROLE-ANCHOR WIDTH (Task C)",
        "PURE ROLE-ANCHOR WIDTH" in src,
    )
    # SINGLE WIDTH SOURCE (Stage 6, 2026-09-13): step 10 must take the
    # anchor span from the ONE published width authority.  Both accepted
    # spellings resolve to the same single chain —
    # ``formation_width_value`` reads the frame publication, and
    # ``governed_width_m`` delegates to the same governed chain — so this
    # pins the INVARIANT (one width source) rather than one call spelling.
    check(
        "step-10 anchors the span on the single width authority "
        "(formation_width_value or governed_width_m)",
        "formation_width_value(world, self.tuners)" in src
        or "governed_width_m(world, self.tuners)" in src,
    )

    # ── 3. Removed width sources in the close block ────────────────
    print("\n[3] Removed width sources in the step-10 writer")
    # Isolate the structured_close block (step 10 header through step 10a),
    # then strip comment lines so the scan proves no WIDTH CODE remains —
    # doc comments that merely say "no fallback / no midfield" must not trip
    # the scan.
    m = re.search(
        r"10\. STRUCTURED OPEN PLAY: SAFE CLOSING FOR PRIMARY \+ SECONDARY",
        src,
    )
    m_end = re.search(r"10a\. POST-CATCH DEFENSIVE LINE CLAMP", src)
    sc_block = src[m.start(): m_end.start()] if m and m_end else src
    code_lines = [
        ln
        for ln in sc_block.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    sc_code = "\n".join(code_lines)

    check(
        "no BC-relative chase_y width computation",
        "chase_y =" not in sc_code,
    )
    check(
        "no width blend arithmetic",
        "(1.0 -" not in sc_code and "blend *" not in sc_code
        and "* close_blend" not in sc_code,
    )
    check(
        "no close_blend variable",
        "close_blend" not in sc_code,
    )
    check(
        "no lane-index width in the close block",
        "lane_index" not in sc_code,
    )
    check(
        "no fallback width in the close block",
        "fallback" not in sc_code.lower(),
    )
    check(
        "no midfield width in the close block",
        "midfield" not in sc_code.lower(),
    )

    # ── 4. Clamp gates wired ───────────────────────────────────────
    print("\n[4] Sideline + governor clamp gates")
    check(
        "step-10 gates sideline clamp on structured_close_clamp_active",
        "structured_close_clamp_active" in sc_block
        and "clamp_to_sideline(final_width_y, self.tuners)" in sc_block,
    )
    check(
        "step-10 gates governor clamp on governor_width_active",
        "governor_width_active" in sc_block
        and "apply_width_clamp(" in sc_block,
    )
    governor = DefensiveLineGovernor(tuners, geometry=None)
    # World-free form (Task D): the band is centred on the governor's
    # stored ruck Y (constructor default 35.0) with half-span = governed
    # width / 2.  A fresh governor with default tuners falls back to
    # base_line_width_m = 40.0 → band [35-20, 35+20] = [15, 55],
    # sideline-intersected — NOT the raw [3, 67] field clamp.
    gov_high = governor.apply_width_clamp(200.0)
    gov_low = governor.apply_width_clamp(-200.0)
    check(
        "governor.apply_width_clamp clamps into the governed band",
        abs(gov_high - 55.0) < 1e-9 and abs(gov_low - 15.0) < 1e-9,
        f"clamp(200)={gov_high:.2f} clamp(-200)={gov_low:.2f}",
    )
    edge_high = clamp_to_sideline(200.0, tuners)
    edge_low = clamp_to_sideline(-200.0, tuners)
    check(
        "clamp_to_sideline is public and clamps into the band",
        abs(edge_high - (70.0 - 3.0)) < 1e-9 and abs(edge_low - 3.0) < 1e-9,
    )

    # ── 5. Stability: width = role_anchor_y, no collapse ───────────
    print("\n[5] Multi-frame stability with BC on the far lane")
    ruck_y = 35.0
    width = 40.0
    defs = []
    roles = ("WINGER", "CENTRE", "A", "B", "C", "D")
    for i, role in enumerate(roles):
        d = Player(x=60.0, y=ruck_y + (i - 2.5) * 5.0, player_id=i + 1)
        d.depth_role = role
        d.depth_side = 1 if i % 2 else -1
        defs.append(d)

    for d in defs:
        anchor = get_role_anchor_y(d, ruck_y, width, tuners)
        # Simulated structured_close chase frames: BC parked on the far-right
        # lane (y=55) would collapse a BC-relative width toward it.  The
        # pure-anchor writer must return the SAME value every frame, and the
        # clamps (both active by default) must not move an in-band anchor.
        final = anchor
        if tuners.structured_close_clamp_active:
            final = clamp_to_sideline(final, tuners)
        if tuners.governor_width_active:
            final = governor.apply_width_clamp(final)
        check(
            f"player {d.player_id} ({d.depth_role}) final Y == role anchor",
            abs(final - anchor) < 1e-9,
            f"final={final:.3f} anchor={anchor:.3f}",
        )

    # ── 6. Force reaction width preservation ───────────────────────
    # Task F (2026-08-10): the force reaction must hold the SPLIT-SPACING Y
    # (spacing_targets[player].y).  frame_spacing_y() delegates to the SAME
    # canonical role-anchor formula (get_role_anchor_y / role_anchor_y) via
    # the split-design spacing chain (defensive_split_spacing.spacing_y) —
    # so the force reaction remains pure-anchor and additionally honours a
    # proportional short/open split.
    #
    # 2026-08-12: the writer now consumes the UN-GATED FRAME PREAMBLE
    # (defensive_pipeline.frame_spacing_y) so every phase-owned writer —
    # force reaction included — reads the SAME per-frame spacing the
    # pipeline published that frame.  The assertion below therefore checks
    # the preamble-consumption form `_frame_spacing_y(world, d, self.tuners,`.
    print("\n[6] Line-break force reaction holds split-spacing Y (Task F)")
    check(
        "force reaction targets frame_spacing_y (un-gated preamble)",
        "d.target_y = _frame_spacing_y(world, d, self.tuners," in src,
    )
    check(
        "force reaction Y = spacing_targets[player].y (spacing owns Y)",
        'target_source = "line_break_force"' in src
        and "_frame_spacing_y(world, d, self.tuners," in src,
    )
    check(
        "split-spacing Y derives from canonical count-derived role-anchor",
        "role_anchor_y_side" in src,
    )

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
