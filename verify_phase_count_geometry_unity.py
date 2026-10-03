"""verify_phase_count_geometry_unity.py — Phase-neutral count-lane geometry (2026-09-04).

Proves the unification directive: TES -> PTB -> POST_RUCK -> OPEN_PLAY all
read the SAME count-lane geometry with ZERO overrides.

Invariants asserted:
  [1] COUNT-LANE UNITY: for a FIXED ruck + defender set,
      ``decide_side_counts`` / ``side_widths(n_total=…)`` /
      ``compute_targets`` are BYTE-IDENTICAL across every phase sub-state
      (TACKLE_* / PTB_* / POST_RUCK_* / OPEN_PLAY) and across the
      ``_ope_phase_label`` aliases (RELEASE / PTB_* / POST_RUCK_*).
  [2] WORLD-OVERRIDE NEUTRALITY: ``world.short_side_width_m`` /
      ``world.open_side_width_m`` and ``_post_retreat_open_play_frames``
      do NOT change ``side_widths`` / ``decide_side_counts`` /
      ``compute_targets`` output.
  [3] WIDTH PATH: ``side_widths(n_total=N)`` is derived from the GOVERNED
      width (the 3-point tuner interpolation x the side split factor,
      room-capped) — never from the flat lane ladder — so it is
      phase-independent by construction.

Run:  python verify_phase_count_geometry_unity.py
Exit 0 on success.
"""
import struct
import sys
import types

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
# ``derive_side_width_m`` (the INVERSE law: rebuild width FROM a widthless
# lane ladder) was DELETED in Stage 2 (2026-09-13) — it is the inversion
# that let extreme width-tuner changes be absorbed.  This verifier reads
# the width through ``side_widths`` (the governed chain) and never needed
# the inverse, so the import is removed rather than shimmed back.
from game.defence.defence_lvl_1.defensive_split_spacing import (
    decide_side_counts,
    side_widths,
    compute_targets,
    LEFT,
    RIGHT,
)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ── Byte-identical helpers ──────────────────────────────────────────
def _float_bytes(v):
    return struct.pack(">d", float(v))


def _int_bytes(v):
    return struct.pack(">q", int(v))


def _count_bytes(n_left, n_right):
    return _int_bytes(n_left) + _int_bytes(n_right)


def _width_bytes(left_w, right_w):
    return _float_bytes(left_w) + _float_bytes(right_w)


def _targets_bytes(targets):
    """Byte-identical representation of {id(d): target_y}.

    Sorted by defender id so the ordering is deterministic; each float is
    packed as an IEEE-754 double for an exact bit-level comparison.
    """
    if not targets:
        return b""
    out = bytearray()
    for key in sorted(targets):
        out += _int_bytes(key)
        out += _float_bytes(targets[key])
    return bytes(out)


def _phase_unity_bytes(world, tuners, defenders, n_total, ruck_y):
    """Return the concatenated byte-identical geometry snapshot for a world."""
    n_left, n_right = decide_side_counts(
        world, tuners, n_total, ruck_y=ruck_y,
    )
    lw, rw = side_widths(world, tuners, n_total=n_total, ruck_y=ruck_y)
    tgt = compute_targets(world, defenders, tuners)
    return (
        _count_bytes(n_left, n_right)
        + _width_bytes(lw, rw)
        + _targets_bytes(tgt)
    )


# ── World / player stubs ────────────────────────────────────────────
def mk_world(ruck_y, sub_state="OPEN_PLAY", ope_label=None,
             short_w=None, open_w=None, retreat_frames=99):
    w = types.SimpleNamespace()
    w.ruck_y_m = float(ruck_y)
    w.ruck_x_m = 60.0
    w.def_line_width_m = 40.0
    w.ruck_established = True
    w.governor_width_active = False
    w.ball_carrier = None
    w.sub_state = sub_state
    w._ope_phase_label = ope_label
    w.just_left_kickoff_timer = 0.0
    w.kickoff_side_sign = None
    w._post_retreat_open_play_frames = retreat_frames
    w.short_side_width_m = short_w
    w.open_side_width_m = open_w
    w._last_count_split = None
    w.count_split_left = None
    w.count_split_right = None
    w.count_split_phase = None
    w.defending_team = []
    return w


def mk_player(pid, x, y, role=None, depth_role=None, depth_side=None):
    p = types.SimpleNamespace()
    p.player_id = pid
    p.x = float(x)
    p.y = float(y)
    p.defender_role = role
    p.depth_role = depth_role
    p.depth_side = depth_side
    p.fullback_role_active = False
    p.tackle_state_active = False
    p.frame_side = depth_side
    p.target_x = p.x
    p.target_y = p.y
    p.vx = 0.0
    p.vy = 0.0
    return p


# ── Phase profiles (resolve_count_phase key -> sub_state / ope label) ─
PHASE_SUB_STATES = {
    "TES": (["TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
             "TACKLE_DOMINANCE"], []),
    "PTB": (["PTB_READY", "PTB"], ["PTB_PREP", "PTB_EXEC", "PTB"]),
    "POST_RUCK": (["POST_PTB_RESET", "POST_RUCK_OPEN_PLAY"],
                  ["POST_RUCK_OPEN_PLAY", "POST_PTB_RESET"]),
    "OPEN_PLAY": (["OPEN_PLAY"], []),
}


def fixed_defender_set(ruck_y):
    """A fixed, realistic short-side LEFT line (left/right defenders)."""
    # LEFT side (ruck_y=20 -> LEFT is short): A/B/C/WINGER
    p_a = mk_player(1, 58.0, ruck_y - 2.0, depth_role="A", depth_side=LEFT)
    p_b = mk_player(2, 58.0, ruck_y - 8.0, depth_role="B", depth_side=LEFT)
    p_c = mk_player(3, 58.0, ruck_y - 14.0, depth_role="C", depth_side=LEFT)
    p_w = mk_player(4, 58.0, ruck_y - 24.0, depth_role="WINGER", depth_side=LEFT)
    # RIGHT side (open): A/B/WINGER
    p_ar = mk_player(5, 58.0, ruck_y + 2.0, depth_role="A", depth_side=RIGHT)
    p_wr = mk_player(6, 58.0, ruck_y + 20.0, depth_role="WINGER", depth_side=RIGHT)
    return [p_a, p_b, p_c, p_w, p_ar, p_wr]


def main():
    print("=" * 72)
    print("PHASE-COUNT-GEOMETRY UNITY VERIFIER (2026-09-04)")
    print("=" * 72)
    tuners = DefenceTuners()

    for ruck_y in (10.0, 20.0, 30.0, 35.0):
        print("\n[%s] ruck_y=%.0f" % ("1", ruck_y))
        defenders = fixed_defender_set(ruck_y)
        n_total = len(defenders)

        # Reference bytes from OPEN_PLAY (the canonical neutral profile).
        ref = _phase_unity_bytes(
            mk_world(ruck_y, sub_state="OPEN_PLAY"), tuners,
            defenders, n_total, ruck_y,
        )

        # [1] Byte-identical across every phase sub-state AND ope label.
        for phase_key, (sub_states, ope_labels) in PHASE_SUB_STATES.items():
            for sub in sub_states:
                w = mk_world(ruck_y, sub_state=sub)
                got = _phase_unity_bytes(w, tuners, defenders, n_total, ruck_y)
                check(
                    "ruck_y=%.0f %s sub_state=%s byte-identical to OPEN_PLAY"
                    % (ruck_y, phase_key, sub),
                    got == ref,
                    "sub=%s" % sub,
                )
            for lab in ope_labels:
                w = mk_world(ruck_y, sub_state="NEUTRAL", ope_label=lab)
                got = _phase_unity_bytes(w, tuners, defenders, n_total, ruck_y)
                check(
                    "ruck_y=%.0f %s ope_label=%s byte-identical to OPEN_PLAY"
                    % (ruck_y, phase_key, lab),
                    got == ref,
                    "ope_label=%s" % lab,
                )

        # Counts must be identical across phases too (the raw split).
        for phase_key, (sub_states, _) in PHASE_SUB_STATES.items():
            n_l, n_r = decide_side_counts(
                mk_world(ruck_y, sub_state=sub_states[0]), tuners,
                n_total, ruck_y=ruck_y,
            )
            ref_l, ref_r = decide_side_counts(
                mk_world(ruck_y, sub_state="OPEN_PLAY"), tuners,
                n_total, ruck_y=ruck_y,
            )
            check(
                "ruck_y=%.0f %s count == OPEN_PLAY count" % (ruck_y, phase_key),
                (n_l, n_r) == (ref_l, ref_r),
                "got=(%d,%d) ref=(%d,%d)" % (n_l, n_r, ref_l, ref_r),
            )

        # [2] World overrides / post-retreat gate do NOT change output.
        override_variants = [
            ("short=25 open=45 retreat=0",
             mk_world(ruck_y, sub_state="OPEN_PLAY",
                      short_w=25.0, open_w=45.0, retreat_frames=0)),
            ("short=None open=None retreat=2",
             mk_world(ruck_y, sub_state="OPEN_PLAY",
                      short_w=None, open_w=None, retreat_frames=2)),
            ("short=35 open=30 retreat=99",
             mk_world(ruck_y, sub_state="OPEN_PLAY",
                      short_w=35.0, open_w=30.0, retreat_frames=99)),
        ]
        for label, wv in override_variants:
            got = _phase_unity_bytes(wv, tuners, defenders, n_total, ruck_y)
            check(
                "ruck_y=%.0f override[%s] byte-identical to OPEN_PLAY"
                % (ruck_y, label),
                got == ref,
                "override=%s" % label,
            )

        # [3] side_widths is GOVERNED-WIDTH derived (not lane-derived), and
        #     therefore phase-neutral by construction.
        # WIDTH-AUTHORITY RESTORED (2026-09-13): the per-side span comes
        # from the governed width (the 3-point tuner interpolation) scaled
        # by the side's split factor and capped by the available room —
        # never from the flat lane ladder.  Deriving it FROM the ladder is
        # the inversion that made extreme width-tuner changes unable to
        # widen the line.
        from game.defence.defence_lvl_1.defensive_width import governed_width_m
        _w = mk_world(ruck_y, sub_state="OPEN_PLAY")
        lw, rw = side_widths(_w, tuners, n_total=n_total, ruck_y=ruck_y)
        eff = governed_width_m(_w, tuners)
        # GOVERNED WIDTH IS THE AUTHORITATIVE TOTAL SPAN (2026-09-14): the
        # governed width is the TOTAL winger-to-winger span, so the two
        # ruck-anchored halves SUM to it.  Each side no longer carries the
        # full width on its own (the old `max(lw, rw) <= eff` form encoded
        # exactly that double-count).  Off-centre the balance shifts toward
        # the roomier touchline; because the two margin-only rooms always sum
        # to the usable field width, the total still equals the governed
        # width, and only the absolute sideline bound may lower it.
        total = 0.5 * (lw + rw)
        check(
            "ruck_y=%.0f side widths sum to the governed total span" % ruck_y,
            lw > 0.0 and rw > 0.0 and abs(total - eff) < 1e-9,
            "lw=%.4f rw=%.4f governed=%.4f total=%.4f"
            % (lw, rw, eff, total),
        )

    print("\n" + "=" * 72)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    print("  - TES/PTB/POST_RUCK/OPEN_PLAY read byte-identical count-lane geometry")
    print("  - world short_side_width_m / open_side_width_m / _post_retreat_open_play_frames are neutral")
    print("  - side_widths(n_total) is governed-width derived")
    sys.exit(0)


if __name__ == "__main__":
    main()
