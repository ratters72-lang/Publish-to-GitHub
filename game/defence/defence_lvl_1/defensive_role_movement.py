"""
defensive_role_movement.py — Role→target mapping for TES/RELEASE/PTB phases.

Maps each defender's existing DefenderRole + depth_class to a target
position and max_speed using the existing DefenceTuners (ROLE_DEPTH_M,
def_line_depth_m, post_ptb_depth_buffer_m).

This is the SINGLE authority for movement targets during non-open-play
phases.  No other system (pursuit, chase, geometry) may override these
targets in TES/RELEASE/PTB.

TASK I-B PURE-CONSUMER CONTRACT (2026-08-11)
-------------------------------------------
Movement is a PURE CONSUMER of the authority layers:

  - spacing (Y)  — consumed via ``defensive_split_spacing.spacing_y``
  - width  (X)   — consumed via the role width anchors (WIDTH_OFFSETS /
                    ROLE_DEPTH_M tables)
  - anchor (X)   — consumed via ``anchor_authority.defensive_line_anchor_m``

Movement NEVER writes: spacing outputs, width outputs, the anchor, chase
targets, MMDL targets, or depth roles.  It writes ONLY the movement-owned
fields (target_x / target_y / max_speed / frozen).  When a live world is
available the real spacing chain and anchor authority are consumed; the
``_spacing_stub`` fallback exists ONLY for verifier call sites that have
no live world reference.

Key principle: roles drive movement.  Movement must express roles.
Each role maps to a distinct depth behind the ruck, producing a visible
staggered defensive line instead of a flat wall.
"""

from player.player import DefenderRole
from game.debug.target_write_log import stamp_target_write
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
from game.defence.defence_lvl_1.defensive_pipeline import frame_spacing_y as _frame_spacing_y
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m as _anchor_m,
)


def _spacing_stub(ruck_y: float):
    """Minimal world projection for spacing_y when no world is passed.

    spacing_y reads world.ruck_y_m (for depth_side and the chain base),
    world.open_side_width_m / short_side_width_m (overrides, default None),
    and the governor-active world flag (default False).  When the caller
    has no live world reference (PTB role pipeline), derive the base anchor
    from the phase's ruck_y so the spacing chain forms around the same
    anchor the role movement uses.
    """
    class _Stub:
        pass
    stub = _Stub()
    stub.ruck_y_m = ruck_y
    stub.open_side_width_m = None
    stub.short_side_width_m = None
    stub.governor_width_active = False
    return stub


# ── Phase gate for TACKLER_1 freeze ─────────────────────────────────
PTB_FREEZE_PHASES = frozenset({"PTB_PREP", "PTB_EXEC"})


def _consume_spacing_y(d, world, tuners, ruck_y):
    """Consume the spacing Y target for *d*.

    TASK I-B / INVARIANT SHEET: spacing is a read-only authority input.
    When a live world is available the FRAME-AUTHORITATIVE Y is consumed:
    ``frame_spacing_y`` prefers the un-gated preamble's fresh per-frame
    dict (``world.frame_spacing_targets``) and falls back to the real
    chain (with side-width overrides + governor clamp) when the dict is
    absent/stale or *d* has no depth role.  When no world reference is
    provided the verifier stub is used.

    This function only READS — it never writes spacing/width/anchor.
    """
    if world is not None:
        return _frame_spacing_y(world, d, tuners)
    return _spacing_y(_spacing_stub(ruck_y), d, tuners)


def _consume_anchor_x(world, ruck_x: float, tuners) -> float:
    """Consume the anchor X authority (ruck_x + 10)."""
    if world is not None:
        return _anchor_m(world)
    depth = float(getattr(tuners, "def_line_depth_m", 10.0))
    return ruck_x + depth


def apply_role_movement(
    d,
    phase: str,
    ruck_x: float,
    ruck_y: float,
    tuners: DefenceTuners,
    world=None,
) -> None:
    """Set target_x, target_y, max_speed, and frozen for a defender based
    on its existing DefenderRole and depth_class.

    Parameters
    ----------
    d : Player
        The defender to position.
    phase : str
        Current OPE internal phase (e.g. "TACKLE_INIT", "RELEASE", "PTB").
    ruck_x : float
        X coordinate of the ruck (metres).
    ruck_y : float
        Y coordinate of the ruck (metres).
    tuners : DefenceTuners
        The shared defensive tuners (provides ROLE_DEPTH_M, etc.).
    world : optional
        Live world reference when available (TASK I-B).  Enables real
        spacing + anchor authority consumption instead of the stub.
    """
    # ─────────────────────────────────────────────────────────────────
    # PHASE GUARD — this writer is forbidden in all non-OPEN_PLAY authority
    # windows.  During RELEASE / PTB_PREP / PTB_EXEC / PTB the phase-owned
    # handlers already write the live targets for DH, markers, and retreating
    # defenders.  Allowing the generic defensive role pipeline to run here
    # creates the same-frame dual-writer target conflict that manifests as
    # DH/jitter and delayed PTB readiness.
    #
    # OPEN-PLAY / POST_RUCK_OPEN_PLAY are already handled by the defence
    # pipeline and MMDL, so they are also excluded here as a defensive
    # fallback against future direct callers.
    # ─────────────────────────────────────────────────────────────────
    if phase in ("OPEN_PLAY", "POST_RUCK_OPEN_PLAY", "RELEASE", "PTB_PREP", "PTB_EXEC", "PTB"):
        return

    # ── FULLBACK HARD-EXCLUDE (2026-08-27) ──
    # The dynamic fullback is an independent backfield anchor owned by
    # fullback_geometry.apply_fullback_geometry — never a TES/RELEASE/PTB
    # role-movement actor.  Without this guard the role writer stamped
    # role_movement_DEF_LINE_READY onto the FB during PTB, pulling it off
    # its deep anchor and violating the "fullback_geometry is the sole
    # authority for FB positioning" invariant.
    if getattr(d, "fullback_role_active", False):
        return

    role = getattr(d, "defender_role", None)
    depth_class = getattr(d, "depth_class", "PASSIVE")

    # ─────────────────────────────────────────────────────────────────
    # TASK III HARD GUARD (2026-08-14) — defensive geometry is
    # AUTHORITATIVE when the defensive pipeline has stamped a live
    # engagement.  This writer is a POST-DISPATCH phase writer (called
    # from OpenPlayEngine.update() AFTER every OPE phase handler and the
    # SDM), so without this guard it silently overwrote committed
    # tacklers and MMDL-chased defenders with role_movement_* targets:
    #
    #     target_x / target_y / max_speed / target_source
    #     ↓ overwritten by
    #     role_movement_DEF_RETREAT_10 / role_movement_DEF_CHASE / ...
    #
    # causing the "PRIMARY/SECONDARY tackler stops committing" and
    # "tackler engine never engages" regressions on the same frame the
    # OPEN_PLAY pipeline assigned the roles.
    #
    # RULES (mirrors TASK_III §5.1–5.3):
    #   - A committed PRIMARY/SECONDARY tackler is owned by the
    #     tackler-commit writer; NOTHING here may overwrite it.
    #   - A defender already chased by the MMDL in this OPEN_PLAY frame
    #     (movement_state CHASE, target_source mmdl_CHASE) is owned by
    #     the MMDL; NOTHING here may overwrite it.
    #
    # TES tacklers (defender_role TACKLER_*) keep their phase-owned
    # freeze/retreat pins: their tackle_role is PASSIVE during TES, so
    # the PRIMARY/SECONDARY gate below does not skip them, and their
    # movement_mode is TACKLER (not MMDL CHASE), so the state gate does
    # not skip them either.
    # ─────────────────────────────────────────────────────────────────
    if getattr(d, "_tackle_commit_active", False):
        return
    if getattr(d, "tackle_role", "PASSIVE") in ("PRIMARY", "SECONDARY"):
        return
    _ms = getattr(d, "movement_state", None)
    _ms_name = getattr(_ms, "name", None) or str(_ms)
    if _ms_name == "CHASE" and getattr(d, "target_source", None) == "mmdl_CHASE":
        return

    # GLOBAL DEFENSIVE TOP-SPEED CEILING: every speed this writer emits must
    # derive from tuners.max_defensive_speed_mps — never the raw per-player
    # max.  Behaviour-specific scales (0.4–0.8) only reduce below the ceiling.
    def_top_speed = getattr(tuners, "max_defensive_speed_mps", 8.3)

    def _capped_speed(scale: float) -> float:
        """Per-player speed at *scale* of the unified ceiling."""
        return min(
            getattr(d, "max_speed_mps", def_top_speed) * scale,
            def_top_speed,
        )

    # TASK I-B: resolve the shared spacing/anchor inputs ONCE here.  Pure
    # consumers — never written back to spacing/width/anchor authorities.
    spacing_y = _consume_spacing_y(d, world, tuners, ruck_y)
    anchor_x = _consume_anchor_x(world, ruck_x, tuners)

    # TARGET-SOURCE STAMP (invariant 3.4): every target written by this
    # phase-owned writer must identify itself — no None/empty sources are
    # legal on defenders the role pipeline owns.
    def _stamp(tag: str) -> None:
        d.target_source = f"role_movement_{tag}"

    # ─────────────────────────────────────────────────────────────────
    # TACKLER roles — frozen during TES, always hold position
    # TACKLER_1 additionally frozen during PTB_PREP and PTB_EXEC
    # ─────────────────────────────────────────────────────────────────
    if role == DefenderRole.TACKLER_1:
        d.target_x = d.x
        d.target_y = d.y
        d.max_speed = 0.0
        _stamp("TACKLER_1")
        if phase in PTB_FREEZE_PHASES:
            d.frozen = True
        return

    if role in (DefenderRole.TACKLER_2, DefenderRole.TACKLER_3):
        d.target_x = d.x
        d.target_y = d.y
        d.max_speed = 0.0
        _stamp("TACKLER_2_3")
        # TACKLER_2/3 are frozen via apply_phase_invariants() — no extra logic needed
        return

    # ─────────────────────────────────────────────────────────────────
    # MARKER_A / MARKER_B — Engine-Spec X-Axis Alignment
    # Directly behind the ruck on the X axis, same Y as the ruck.
    # Marker A: (ruck_x + 1.0, ruck_y); Marker B: (ruck_x + 2.0, ruck_y)
    # No Y-offsets.  Mirrors OpenPlayEngine._move_markers_to_targets().
    # ─────────────────────────────────────────────────────────────────
    if role == DefenderRole.MARKER_A:
        d.target_x = ruck_x + 1.0
        d.target_y = ruck_y
        d.max_speed = tuners.release_peel_speed  # ~2.0 m/s
        _stamp("MARKER_A")
        return

    if role == DefenderRole.MARKER_B:
        d.target_x = ruck_x + 2.0
        d.target_y = ruck_y
        d.max_speed = tuners.release_peel_speed
        _stamp("MARKER_B")
        return

    # ─────────────────────────────────────────────────────────────────
    # DUMMY_HALF — at the ruck (attack role with defender_role set)
    # ─────────────────────────────────────────────────────────────────
    if role == DefenderRole.DUMMY_HALF:
        d.target_x = ruck_x
        d.target_y = ruck_y
        d.max_speed = _capped_speed(0.6)
        _stamp("DUMMY_HALF")
        return

    # ─────────────────────────────────────────────────────────────────
    # KICK_CHASE — not role-mapped during TES/RELEASE/PTB
    # ─────────────────────────────────────────────────────────────────
    if role == DefenderRole.KICK_CHASE:
        # KICK_CHASE is handled by kickoff logic — skip
        _stamp("KICK_CHASE")
        return

    # ─────────────────────────────────────────────────────────────────
    # DEF_RETREAT_10 — retreat to 10m behind ruck, hold line
    # ─────────────────────────────────────────────────────────────────
    if role == DefenderRole.DEF_RETREAT_10:
        depth = tuners.retreat_compliance_distance_m  # 10.0m
        d.target_x = ruck_x + depth
        # SPACING Y (Task F, 2026-08-10): retreat keeps spacing Y, never
        # the defender's current lane Y.  TASK I-B: consumed from the live
        # spacing chain when a world exists.
        d.target_y = spacing_y
        stamp_target_write(world, d, "role_movement", detail="spacing_y")
        d.max_speed = _capped_speed(0.7)
        _stamp("DEF_RETREAT_10")
        return

    # ─────────────────────────────────────────────────────────────────
    # DEF_LINE_READY — retreat to the defensive line anchor
    # ─────────────────────────────────────────────────────────────────
    if role == DefenderRole.DEF_LINE_READY:
        # TASK I-B: consume the ANCHOR AUTHORITY (ruck_x + 10) instead of
        # the local tuners.def_line_depth_m, so this writer and the anchor
        # authority can never drift apart.
        d.target_x = anchor_x
        # SPACING Y (Task F, 2026-08-10): line-ready keeps spacing Y.
        d.target_y = spacing_y
        stamp_target_write(world, d, "role_movement", detail="spacing_y")
        d.max_speed = _capped_speed(0.5)
        _stamp("DEF_LINE_READY")
        return

    # ─────────────────────────────────────────────────────────────────
    # DEF_CHASE — closer to the ruck (8m), more aggressive speed
    # ─────────────────────────────────────────────────────────────────
    if role == DefenderRole.DEF_CHASE:
        d.target_x = ruck_x + 8.0
        # SPACING Y (Task F, 2026-08-10): chase keeps spacing Y.
        d.target_y = spacing_y
        stamp_target_write(world, d, "role_movement", detail="spacing_y")
        d.max_speed = _capped_speed(0.8)
        _stamp("DEF_CHASE")
        return

    # ─────────────────────────────────────────────────────────────────
    # LINE (legacy role) — frozen, hold position
    # ─────────────────────────────────────────────────────────────────
    if role == DefenderRole.LINE:
        d.target_x = d.x
        d.target_y = d.y
        d.max_speed = 0.0
        _stamp("LINE")
        return

    # ─────────────────────────────────────────────────────────────────
    # FALLBACK for defenders with no specific DefenderRole:
    # Use depth_class + ROLE_DEPTH_M to compute a role-appropriate depth.
    #
    # depth_class values: "PRIMARY", "SECONDARY", "PASSIVE", "MARKER"
    # depth_role values:  "A", "B", "C", "D", "CENTRE", "WINGER"
    #
    # ROLE_DEPTH_M maps depth_role to metres offset from ruck_x.
    # post_ptb_depth_buffer_m adds extra separation from the ruck zone.
    # ─────────────────────────────────────────────────────────────────
    # "D" is the universal bulk/fallback depth role (ruck-driven role
    # system, 2026-08-08).
    depth_role = getattr(d, "depth_role", "D")
    role_offset = tuners.ROLE_DEPTH_M.get(depth_role, 5.0)
    depth_buffer = tuners.post_ptb_depth_buffer_m  # 2.0m
    raw_target_x = ruck_x + role_offset + depth_buffer

    d.target_x = raw_target_x
    # SPACING Y (Task F, 2026-08-10): fallback keeps spacing Y.
    d.target_y = spacing_y
    stamp_target_write(world, d, "role_movement", detail="spacing_y")
    _stamp("DEPTH_FALLBACK")

    # Speed scales with depth_class: PRIMARY fastest, PASSIVE slowest.  All
    # values derive from the unified defensive top-speed ceiling.
    speed_scale = {"PRIMARY": 0.9, "SECONDARY": 0.75, "PASSIVE": 0.5, "MARKER": 0.4}
    d.max_speed = _capped_speed(speed_scale.get(depth_class, 0.5))
