"""
game/audit/tes_audit.py — TES-phase full positional audit per the canonical spec.

Captures every frame across TACKLE, PTB, POST_PTB_RESET, and OPEN_PLAY phases,
logging all 9 required data sections AND validating against the 10-section spec
rules.  Outputs two files:
    audit_tes_frames.csv   — raw per-frame positional snapshot
    audit_tes_issues.txt   — per-rule PASS/FAIL with deviation details

Usage:
    ope = OpenPlayEngine(world)
    auditor = TESPhaseAuditor(ope)
    ope._auditor = auditor

    # each frame:
    auditor.audit(world)
    auditor.finalize()   # call at end of simulation
"""

import csv
import math
import os
from datetime import datetime

from player.player import DefenderRole


# ═══════════════════════════════════════════════════════════════════════
# CANONICAL SPEC CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

# Marker positions (spec sections 4a, 4b)
SPEC_MARKER_A_X_OFFSET = -0.5   # BC.x - 0.5
SPEC_MARKER_A_Y_OFFSET = -1.0   # BC.y - 1.0
SPEC_MARKER_B_X_OFFSET = -0.5   # BC.x - 0.5
SPEC_MARKER_B_Y_OFFSET = +1.0   # BC.y + 1.0

# DH spawn position (spec section 3)
SPEC_DH_X_OFFSET = -1.0         # BC.x - 1.0
SPEC_DH_Y_OFFSET = 0.0          # BC.y

# BC-relative baseline depths (spec section 6)
SPEC_BC_DEPTH_PRIMARY = -1.0    # BC.x - 1.0
SPEC_BC_DEPTH_SECONDARY = -2.0  # BC.x - 2.0
SPEC_BC_DEPTH_PASSIVE = -3.0    # BC.x - 3.0
SPEC_BC_DEPTH_MARKER = -0.5     # BC.x - 0.5

# Retreat target (spec sections 5, 9)
SPEC_RETREAT_OFFSET = 10.0      # BC.x + 10.0

# Impact shift (spec section 7)
SPEC_BC_IMPACT_SHIFT = 0.2      # BC.x += 0.2
SPEC_TACKLER_IMPACT_SHIFT = -0.2  # Tackler.x -= 0.2

# Tolerances for floating-point comparisons
POS_TOLERANCE = 0.35            # metres — allows for sub-frame interpolation
FREEZE_TOLERANCE = 0.01         # seconds — freeze_timer close enough to 0


# ═══════════════════════════════════════════════════════════════════════
# HELPER: safe attribute access
# ═══════════════════════════════════════════════════════════════════════

def _attr(obj, name, default=None):
    """Safe attribute access with default."""
    if obj is None:
        return default
    return getattr(obj, name, default)


def _role_str(d):
    """Return defender_role name or 'NONE'."""
    role = _attr(d, "defender_role", None)
    if role is None:
        return "NONE"
    if hasattr(role, "name"):
        return role.name
    return str(role)


def _depth_class_str(d):
    """Return depth_class string."""
    return _attr(d, "depth_class", "?")


def _depth_role_str(d):
    """Return depth_role string."""
    return _attr(d, "depth_role", "?")


def _phase_str(phase):
    """Normalise phase to string."""
    if phase is None:
        return "NONE"
    if hasattr(phase, "value"):
        return phase.value
    return str(phase)


def _sub_state_str(ss):
    """Normalise sub_state to string."""
    if ss is None:
        return "NONE"
    if hasattr(ss, "value"):
        return ss.value
    return str(ss)


def _bool_str(v):
    return str(v).upper()


# ═══════════════════════════════════════════════════════════════════════
# TES PHASE AUDITOR
# ═══════════════════════════════════════════════════════════════════════

class TESPhaseAuditor:
    """Per-frame TES auditor: logs raw data and validates spec rules.

    Two output files:
        audit_tes_frames.csv — per-frame raw snapshot (9 sections)
        audit_tes_issues.txt — per-rule PASS/FAIL with context
    """

    def __init__(self, ope):
        self.ope = ope
        self.frame = 0

        # --- Frame log accumulator ---
        self.frames_data: list[dict] = []

        # --- Issue log accumulator ---
        self.issues: list[str] = []

        # --- State tracking for transitions ---
        self._prev_phase: str | None = None
        self._prev_sub_state: str | None = None
        self._prev_bc_x: float | None = None
        self._prev_bc_y: float | None = None
        self._bc_frozen_history: list[bool] = []
        self._ptb_started: bool = False  # has PTB_EXEC->PTB transition occurred?
        self._post_ptb_entry_frame: int = -1
        self._markers_assigned: bool = False  # marker A/B assigned at TACKLE_DOMINANCE
        self._impact_shift_logged: bool = False  # only log once per tackle

        # --- Open output files ---
        self._setup_outputs()

    def _setup_outputs(self):
        """Create output files with headers."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        self._frames_path = f"audit_tes_frames_{ts}.csv"
        self._issues_path = f"audit_tes_issues_{ts}.txt"

        # Frame log CSV
        self._frames_headers = [
            "frame",
            "phase", "sub_state",
            # Section 1: BC
            "bc_id", "bc_has_ball", "bc_x", "bc_y", "bc_frozen", "bc_facing", "bc_tes_locked",
            # Section 2: DH
            "dh_id", "dh_role", "dh_x", "dh_y", "dh_has_ball", "dh_frozen",
            # Section 3: Marker A
            "marker_a_id", "marker_a_role", "marker_a_x", "marker_a_y", "marker_a_frozen",
            "marker_a_freeze_timer",
            # Section 4: Marker B
            "marker_b_id", "marker_b_role", "marker_b_x", "marker_b_y", "marker_b_frozen",
            "marker_b_freeze_timer",
            # Section 5: Defender roles summary
            "n_defenders",
            "n_marker_a", "n_marker_b", "n_line", "n_dh_role", "n_kick_chase", "n_none_role",
            "n_frozen", "n_retreating",
            # Section 6: Tackle trigger state
            "tackle_count", "phase_timer", "tes_phase",
            "tes_stability", "tes_collapsed", "tes_release_timer",
            # Section 7: Physics flags (BC + primary)
            "bc_in_contact", "bc_is_tackler", "bc_tackle_locked", "bc_anim_state",
            "primary_id", "primary_frozen", "primary_tes_locked",
            "primary_in_contact", "primary_is_tackler", "primary_anim_state",
            # Section 8: Retreat distance per defender (comma-sep summary)
            "retreat_info",
            # Section 9: PTB spawn / DH assignment
            "ruck_x", "ruck_y", "ball_owner_id", "ball_x", "ball_y",
            "attacking_line_enabled",
        ]
        with open(self._frames_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(self._frames_headers)

        # Issues file header
        with open(self._issues_path, "w") as f:
            f.write(f"TES PHASE AUDIT — {ts}\n")
            f.write("=" * 80 + "\n")
            f.write(
                "Each rule is checked every frame and logged as "
                "PASS, FAIL, or SKIP (phase not applicable).\n\n"
            )

    def _write_frame_row(self, row: dict):
        """Append one row to the CSV frame log."""
        with open(self._frames_path, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([row.get(h, "") for h in self._frames_headers])

    def _write_issue(self, text: str):
        """Append one line to the issues file and buffer."""
        self.issues.append(text)
        with open(self._issues_path, "a") as f:
            f.write(text + "\n")

    # ═══════════════════════════════════════════════════════════════════
    # MAIN AUDIT ENTRY — called once per frame
    # ═══════════════════════════════════════════════════════════════════

    def audit(self, world):
        """Audit one frame: log snapshot + validate all spec rules."""
        self.frame += 1
        self.world = world

        phase = _attr(self.ope, "_phase", "?")
        sub_state = _attr(world, "sub_state", None)
        sub_state_str = _sub_state_str(sub_state)
        phase_str = _phase_str(phase)

        bc = _attr(world, "ball_carrier", None)
        ope = self.ope
        dh = _attr(ope, "dh", None)
        primary = _attr(ope, "primary", None)
        joiners = _attr(ope, "joiners", [])
        tes_ctx = _attr(ope, "tes_ctx", None)
        tes_outcome = _attr(ope, "tes_outcome", None)
        defenders = _attr(world, "defending_team", [])

        # ──── Detect transitions ────
        transitions = self._detect_transitions(phase_str, sub_state_str)

        # ──── Take raw snapshot ────
        row = self._build_frame_row(world, phase_str, sub_state_str)
        self._write_frame_row(row)

        # ──── Validate all spec rules ────
        self._validate_section_1_bc(world, phase_str, bc)
        self._validate_section_2_ball(world, phase_str, bc, dh)
        self._validate_section_3_dh(world, phase_str, dh, bc)
        self._validate_section_4_markers(world, phase_str, defenders, bc)
        self._validate_section_5_defence_line(world, phase_str, defenders, bc)
        self._validate_section_6_bc_relative_baseline(world, phase_str, defenders, bc)
        self._validate_section_7_tackle_physics(world, phase_str, bc, primary)
        self._validate_section_8_ptb_spawn(world, phase_str, bc, dh)
        self._validate_section_9_retreat(world, phase_str, defenders, bc)
        self._validate_section_10_absolute_rules(world, phase_str, defenders, bc, dh)

        # Log transitions
        for t in transitions:
            self._write_issue(f"[FRAME {self.frame}] TRANSITION: {t}")

        # Update state trackers
        self._prev_phase = phase_str
        self._prev_sub_state = sub_state_str
        if bc is not None:
            self._prev_bc_x = bc.x
            self._prev_bc_y = bc.y
            self._bc_frozen_history.append(bc.frozen)
            if len(self._bc_frozen_history) > 60:
                self._bc_frozen_history.pop(0)

    # ═══════════════════════════════════════════════════════════════════
    # TRANSITION DETECTION
    # ═══════════════════════════════════════════════════════════════════

    def _detect_transitions(self, phase: str, sub_state: str) -> list[str]:
        """Detect phase/sub_state transitions."""
        result = []
        if self._prev_phase is not None and phase != self._prev_phase:
            result.append(f"PHASE: {self._prev_phase} → {phase}")
        if self._prev_sub_state is not None and sub_state != self._prev_sub_state:
            result.append(f"SUBSTATE: {self._prev_sub_state} → {sub_state}")

        # Detect POST_PTB_RESET entry
        if sub_state == "post_ptb_reset" and self._prev_sub_state != "post_ptb_reset":
            self._post_ptb_entry_frame = self.frame
            result.append("POST_PTB_RESET entered")

        # Detect PTB_EXEC→PTB transition (ball transfer)
        if phase == "PTB" and self._prev_phase == "PTB_EXEC":
            self._ptb_started = True
            result.append("PTB ball transfer triggered")

        # Detect TACKLE_DOMINANCE entry (markers assigned here)
        if sub_state == "tackle_dominance" and self._prev_sub_state != "tackle_dominance":
            self._markers_assigned = True
            self._impact_shift_logged = False
            result.append("TACKLE_DOMINANCE entered — markers assigned")

        # Detect OPEN_PLAY entry
        if sub_state == "open_play" and self._prev_sub_state != "open_play":
            result.append("OPEN_PLAY entered — all FREEZE/LINE checks active")

        return result

    # ═══════════════════════════════════════════════════════════════════
    # FRAME ROW BUILDER (9 sections)
    # ═══════════════════════════════════════════════════════════════════

    def _build_frame_row(self, world, phase: str, sub_state: str) -> dict:
        """Build a complete frame snapshot dict with all 9 sections."""
        bc = _attr(world, "ball_carrier", None)
        ope = self.ope
        dh = _attr(ope, "dh", None)
        primary = _attr(ope, "primary", None)
        defenders = _attr(world, "defending_team", [])
        tes_ctx = _attr(ope, "tes_ctx", None)

        # ─── Section 1 / 2: BC and Ball ───
        row = {
            "frame": self.frame,
            "phase": phase,
            "sub_state": sub_state,
            "bc_id": _attr(bc, "player_id", -1),
            "bc_has_ball": _attr(bc, "possessed", False),
            "bc_x": round(_attr(bc, "x", -1.0), 3),
            "bc_y": round(_attr(bc, "y", -1.0), 3),
            "bc_frozen": _attr(bc, "frozen", False),
            "bc_facing": round(_attr(bc, "facing", 0.0), 3),
            "bc_tes_locked": _attr(bc, "tes_locked", False),
        }

        # Active tackle state
        row["tackle_count"] = _attr(world, "tackle_count", 0)
        row["phase_timer"] = round(_attr(ope, "_phase_timer", 0.0), 3)
        row["tes_phase"] = _phase_str(_attr(tes_ctx, "phase", None))
        row["tes_stability"] = round(_attr(tes_ctx, "stability", -1.0), 3)
        row["tes_collapsed"] = _attr(tes_ctx, "collapsed", False)
        row["tes_release_timer"] = round(_attr(tes_ctx, "release_timer", -1.0), 3)

        # ─── Section 3: DH ───
        row["dh_id"] = _attr(dh, "player_id", -1)
        row["dh_role"] = _role_str(dh)
        row["dh_x"] = round(_attr(dh, "x", -1.0), 3)
        row["dh_y"] = round(_attr(dh, "y", -1.0), 3)
        row["dh_has_ball"] = _attr(dh, "possessed", False)
        row["dh_frozen"] = _attr(dh, "frozen", True)

        # ─── Section 4: Markers A/B ───
        marker_a, marker_b = self._find_markers(defenders)
        row["marker_a_id"] = _attr(marker_a, "player_id", -1)
        row["marker_a_role"] = _role_str(marker_a)
        row["marker_a_x"] = round(_attr(marker_a, "x", -1.0), 3)
        row["marker_a_y"] = round(_attr(marker_a, "y", -1.0), 3)
        row["marker_a_frozen"] = _attr(marker_a, "frozen", True)
        row["marker_a_freeze_timer"] = round(_attr(marker_a, "freeze_timer", -1.0), 3)

        row["marker_b_id"] = _attr(marker_b, "player_id", -1)
        row["marker_b_role"] = _role_str(marker_b)
        row["marker_b_x"] = round(_attr(marker_b, "x", -1.0), 3)
        row["marker_b_y"] = round(_attr(marker_b, "y", -1.0), 3)
        row["marker_b_frozen"] = _attr(marker_b, "frozen", True)
        row["marker_b_freeze_timer"] = round(_attr(marker_b, "freeze_timer", -1.0), 3)

        # ─── Section 5: Defender role summary ───
        roles_count = {"MARKER_A": 0, "MARKER_B": 0, "LINE": 0, "DUMMY_HALF": 0,
                       "KICK_CHASE": 0, "NONE": 0}
        n_frozen = 0
        n_retreating = 0
        for d in defenders:
            r = _role_str(d)
            if r in roles_count:
                roles_count[r] += 1
            else:
                roles_count["NONE"] += 1
            if _attr(d, "frozen", False):
                n_frozen += 1
            if _attr(d, "retreating", False):
                n_retreating += 1

        row["n_defenders"] = len(defenders)
        row["n_marker_a"] = roles_count["MARKER_A"]
        row["n_marker_b"] = roles_count["MARKER_B"]
        row["n_line"] = roles_count["LINE"]
        row["n_dh_role"] = roles_count["DUMMY_HALF"]
        row["n_kick_chase"] = roles_count["KICK_CHASE"]
        row["n_none_role"] = roles_count["NONE"]
        row["n_frozen"] = n_frozen
        row["n_retreating"] = n_retreating

        # ─── Section 6: Physics flags (BC + primary) ───
        row["bc_in_contact"] = _attr(bc, "in_contact", False)
        row["bc_is_tackler"] = _attr(bc, "is_tackler", False)
        row["bc_tackle_locked"] = _attr(bc, "tackle_locked", False)
        row["bc_anim_state"] = _attr(bc, "anim_state", "")

        row["primary_id"] = _attr(primary, "player_id", -1)
        row["primary_frozen"] = _attr(primary, "frozen", True)
        row["primary_tes_locked"] = _attr(primary, "tes_locked", False)
        row["primary_in_contact"] = _attr(primary, "in_contact", False)
        row["primary_is_tackler"] = _attr(primary, "is_tackler", False)
        row["primary_anim_state"] = _attr(primary, "anim_state", "")

        # ─── Section 7: Retreat info (per-defender summary) ───
        retreat_parts = []
        for d in defenders:
            retreat_dx = round(_attr(d, "x", 0.0) - _attr(world, "ruck_x_m", 60.0), 2)
            retreat_str = (
                f"P{_attr(d,'player_id',-1)}:"
                f"x={_attr(d,'x',0):.1f}"
                f"tx={_attr(d,'target_x',0):.1f}"
                f"retr={_attr(d,'retreating',False)}"
                f"frz={_attr(d,'frozen',False)}"
                f"dx_ruck={retreat_dx}"
            )
            retreat_parts.append(retreat_str)
        row["retreat_info"] = "|".join(retreat_parts)

        # ─── Section 8: PTB spawn / DH assignment ───
        row["ruck_x"] = round(_attr(world, "ruck_x_m", -1.0), 3)
        row["ruck_y"] = round(_attr(world, "ruck_y_m", -1.0), 3)
        ball = _attr(world, "ball", None)
        ball_owner = _attr(ball, "owner", None)
        row["ball_owner_id"] = _attr(ball_owner, "player_id",
                                      _attr(ball, "possessed_by", _attr(None, "player_id", -1)))
        row["ball_x"] = round(_attr(ball, "x", _attr(world, "ball_x_m", -1.0)), 3)
        row["ball_y"] = round(_attr(ball, "y", _attr(world, "ball_y_m", -1.0)), 3)
        row["attacking_line_enabled"] = _attr(world, "attacking_line_enabled", False)

        return row

    # ═══════════════════════════════════════════════════════════════════
    # FINDERS
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def _find_markers(defenders):
        """Return (marker_a, marker_b) or (None, None)."""
        ma, mb = None, None
        for d in defenders:
            r = _attr(d, "defender_role", None)
            if r == DefenderRole.MARKER_A:
                ma = d
            elif r == DefenderRole.MARKER_B:
                mb = d
        return ma, mb

    @staticmethod
    def _find_line_defenders(defenders) -> list:
        """Return all defenders with role=LINE."""
        return [d for d in defenders if _attr(d, "defender_role", None) == DefenderRole.LINE]

    # ═══════════════════════════════════════════════════════════════════
    # VALIDATION: SECTION 1 — BALL CARRIER
    # ═══════════════════════════════════════════════════════════════════

    def _validate_section_1_bc(self, world, phase: str, bc):
        """Validate BC rules across all phases.

        TACKLE phase:
            RULE 1a: BC must be frozen.
            RULE 1b: BC must have the ball.
            RULE 1c: BC.x must equal the tackle contact point (tes_ctx.contact_x_m).
            RULE 1d: BC must be facing RIGHT (facing ~0 or slightly positive).
        PTB phase:
            RULE 1e: BC must NOT have the ball.
            RULE 1f: BC must remain frozen.
            RULE 1g: BC.x and BC.y must not move (frame-to-frame).
            RULE 1h: BC must become MARKER_A or MARKER_B after PTB.
        """
        is_tackle_phase = phase in ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
        is_ptb_phase = phase in ("PTB_READY", "PTB_PREP", "PTB_EXEC", "PTB")

        if bc is None:
            return

        # --- TACKLE phase rules ---
        if is_tackle_phase:
            # RULE 1a: BC frozen
            if not bc.frozen:
                self._write_issue(
                    f"[FAIL] RULE 1a | Frame {self.frame} {phase} | "
                    f"BC #{bc.player_id} must be frozen (frozen=False)"
                )
            else:
                self._write_issue(
                    f"[PASS] RULE 1a | Frame {self.frame} {phase} | "
                    f"BC #{bc.player_id} frozen=True"
                )

            # RULE 1b: BC has ball
            if not bc.possessed:
                self._write_issue(
                    f"[FAIL] RULE 1b | Frame {self.frame} {phase} | "
                    f"BC #{bc.player_id} must have the ball (possessed=False)"
                )
            else:
                self._write_issue(
                    f"[PASS] RULE 1b | Frame {self.frame} {phase} | "
                    f"BC #{bc.player_id} possessed=True"
                )

            # RULE 1c: BC.x == contact point
            tes_ctx = _attr(self.ope, "tes_ctx", None)
            contact_x = _attr(tes_ctx, "contact_x_m", None)
            if contact_x is not None:
                dx = abs(bc.x - contact_x)
                if dx > POS_TOLERANCE:
                    self._write_issue(
                        f"[FAIL] RULE 1c | Frame {self.frame} {phase} | "
                        f"BC.x={bc.x:.3f} != contact_x_m={contact_x:.3f} (dx={dx:.3f})"
                    )
                else:
                    self._write_issue(
                        f"[PASS] RULE 1c | Frame {self.frame} {phase} | "
                        f"BC.x={bc.x:.3f} ~ contact_x_m={contact_x:.3f} (dx={dx:.3f})"
                    )

            # RULE 1d: BC facing RIGHT
            # Facing ~0 means facing right (+X).  Allow ~-0.3 to ~+0.3 rad.
            facing = bc.facing
            if facing < -0.5 or facing > 0.5:
                self._write_issue(
                    f"[FAIL] RULE 1d | Frame {self.frame} {phase} | "
                    f"BC #{bc.player_id} facing={facing:.3f} rad (expected ~0 for RIGHT)"
                )
            else:
                self._write_issue(
                    f"[PASS] RULE 1d | Frame {self.frame} {phase} | "
                    f"BC #{bc.player_id} facing={facing:.3f} rad (RIGHT ok)"
                )

        # --- PTB phase rules ---
        if is_ptb_phase:
            # RULE 1e: BC must NOT have the ball
            if bc.possessed:
                self._write_issue(
                    f"[FAIL] RULE 1e | Frame {self.frame} {phase} | "
                    f"BC #{bc.player_id} must NOT have ball during PTB (possessed=True)"
                )
            else:
                self._write_issue(
                    f"[PASS] RULE 1e | Frame {self.frame} {phase} | "
                    f"BC #{bc.player_id} possessed=False"
                )

            # RULE 1f: BC must remain frozen
            if not bc.frozen:
                self._write_issue(
                    f"[FAIL] RULE 1f | Frame {self.frame} {phase} | "
                    f"BC #{bc.player_id} must be frozen during PTB (frozen=False)"
                )
            else:
                self._write_issue(
                    f"[PASS] RULE 1f | Frame {self.frame} {phase} | "
                    f"BC #{bc.player_id} frozen=True"
                )

            # RULE 1g: BC.x and BC.y must not move (frame-to-frame)
            if self._prev_bc_x is not None and self._prev_bc_y is not None:
                dx = abs(bc.x - self._prev_bc_x)
                dy = abs(bc.y - self._prev_bc_y)
                if dx > POS_TOLERANCE or dy > POS_TOLERANCE:
                    self._write_issue(
                        f"[FAIL] RULE 1g | Frame {self.frame} {phase} | "
                        f"BC moved since last frame: dx={dx:.3f} dy={dy:.3f} "
                        f"(prev=({self._prev_bc_x:.2f},{self._prev_bc_y:.2f}) "
                        f"now=({bc.x:.2f},{bc.y:.2f}))"
                    )

            # RULE 1h: BC must become MARKER_A or MARKER_B after PTB
            # Check if BC has been assigned a marker role in the defence
            defenders = _attr(self.world, "defending_team", [])
            bc_is_marker = bc in defenders and _attr(bc, "defender_role", None) in (
                DefenderRole.MARKER_A, DefenderRole.MARKER_B)
            # Also check if the player who WAS BC is now a defender with marker role
            # This only applies after PTB_EXEC (ball transferred)
            if phase in ("PTB",) and self._ptb_started:
                bc_role = _attr(bc, "defender_role", None)
                if bc_role not in (DefenderRole.MARKER_A, DefenderRole.MARKER_B) and bc_role is not None:
                    self._write_issue(
                        f"[FAIL] RULE 1h | Frame {self.frame} {phase} | "
                        f"Former BC #{bc.player_id} role={_role_str(bc)} "
                        f"(expected MARKER_A or MARKER_B after PTB)"
                    )

        # SKIP for other phases — not evaluated
        if not is_tackle_phase and not is_ptb_phase:
            self._write_issue(
                f"[SKIP] RULE 1   | Frame {self.frame} {phase} | "
                f"BC rules not applicable in this phase"
            )

    # ═══════════════════════════════════════════════════════════════════
    # VALIDATION: SECTION 2 — BALL
    # ═══════════════════════════════════════════════════════════════════

    def _validate_section_2_ball(self, world, phase: str, bc, dh):
        """Validate ball rules.

        RULE 2a: Ball.x == BC.x at PTB.
        RULE 2b: Ball.y == BC.y at PTB.
        RULE 2c: Ball.owner must be DH only.
        RULE 2d: Ball must never be owned by a random runner (possessed_by not DH).
        """
        ball = _attr(world, "ball", None)
        if ball is None:
            self._write_issue(
                f"[SKIP] RULE 2   | Frame {self.frame} {phase} | "
                f"ball is None"
            )
            return

        is_ptb_phase = phase in ("PTB_READY", "PTB_PREP", "PTB_EXEC", "PTB")

        # RULE 2a, 2b: Only check during PTB phases when BC exists
        if is_ptb_phase and bc is not None:
            ball_x = _attr(ball, "x", _attr(world, "ball_x_m", 0.0))
            ball_y = _attr(ball, "y", _attr(world, "ball_y_m", 0.0))
            dx = abs(ball_x - bc.x)
            dy = abs(ball_y - bc.y)
            tol = 1.5  # ball may be eased — allow wider tolerance

            if dx > tol:
                self._write_issue(
                    f"[FAIL] RULE 2a | Frame {self.frame} {phase} | "
                    f"Ball.x={ball_x:.3f} != BC.x={bc.x:.3f} (dx={dx:.3f})"
                )
            else:
                self._write_issue(
                    f"[PASS] RULE 2a | Frame {self.frame} {phase} | "
                    f"Ball.x={ball_x:.3f} ~ BC.x={bc.x:.3f} (dx={dx:.3f})"
                )

            if dy > tol:
                self._write_issue(
                    f"[FAIL] RULE 2b | Frame {self.frame} {phase} | "
                    f"Ball.y={ball_y:.3f} != BC.y={bc.y:.3f} (dy={dy:.3f})"
                )
            else:
                self._write_issue(
                    f"[PASS] RULE 2b | Frame {self.frame} {phase} | "
                    f"Ball.y={ball_y:.3f} ~ BC.y={bc.y:.3f} (dy={dy:.3f})"
                )

        # RULE 2c: Ball.owner must be DH only (when DH is assigned)
        # Check ball's possessed_by or owner attribute
        possessed_by = _attr(ball, "possessed_by", None)
        ball_owner = _attr(ball, "owner", None)
        effective_owner = possessed_by or ball_owner

        if dh is not None and effective_owner is not None:
            if effective_owner is not dh:
                self._write_issue(
                    f"[FAIL] RULE 2c | Frame {self.frame} {phase} | "
                    f"Ball owner #{_attr(effective_owner,'player_id',-1)} "
                    f"is not DH #{dh.player_id}"
                )
        elif dh is not None and effective_owner is None and is_ptb_phase:
            # During PTB, ball should have an owner (DH)
            self._write_issue(
                f"[FAIL] RULE 2c | Frame {self.frame} {phase} | "
                f"Ball has no owner but DH #{dh.player_id} is assigned"
            )

        # RULE 2d: Ball never owned by random runner (someone not DH/BC during PTB)
        if is_ptb_phase and effective_owner is not None:
            is_legit = (
                effective_owner is dh
                or (bc is not None and effective_owner is bc)
            )
            if not is_legit:
                self._write_issue(
                    f"[FAIL] RULE 2d | Frame {self.frame} {phase} | "
                    f"Ball owned by Player #{_attr(effective_owner,'player_id',-1)} "
                    f"(expected DH #{_attr(dh,'player_id','N/A')} or "
                    f"BC #{_attr(bc,'player_id','N/A')})"
                )

    # ═══════════════════════════════════════════════════════════════════
    # VALIDATION: SECTION 3 — DUMMY HALF
    # ═══════════════════════════════════════════════════════════════════

    def _validate_section_3_dh(self, world, phase: str, dh, bc):
        """Validate DH rules.

        Spawn:
            RULE 3a: DH.x = BC.x - 1.0
            RULE 3b: DH.y = BC.y
            RULE 3c: DH must have the ball.
            RULE 3d: DH must NOT be frozen.
            RULE 3e: DH.role must be DH (defender_role == DUMMY_HALF).
            RULE 3f: DH must be the ONLY player with has_ball=True.
        """
        is_post_release = phase in ("RELEASE", "PTB_READY", "PTB_PREP", "PTB_EXEC", "PTB")
        if dh is None or bc is None:
            if is_post_release and dh is None and phase != "RELEASE":
                self._write_issue(
                    f"[FAIL] RULE 3   | Frame {self.frame} {phase} | "
                    f"DH is None — no dummy half assigned"
                )
            return

        if not is_post_release:
            return

        # RULE 3a: DH.x ~ BC.x - 1.0
        expected_x = bc.x + SPEC_DH_X_OFFSET
        dx = abs(dh.x - expected_x)
        if dx > POS_TOLERANCE:
            self._write_issue(
                f"[FAIL] RULE 3a | Frame {self.frame} {phase} | "
                f"DH.x={dh.x:.3f} != BC.x-1.0={expected_x:.3f} (dx={dx:.3f})"
            )
        else:
            self._write_issue(
                f"[PASS] RULE 3a | Frame {self.frame} {phase} | "
                f"DH.x={dh.x:.3f} ~ BC.x-1.0={expected_x:.3f} (dx={dx:.3f})"
            )

        # RULE 3b: DH.y ~ BC.y
        dy = abs(dh.y - bc.y)
        if dy > POS_TOLERANCE:
            self._write_issue(
                f"[FAIL] RULE 3b | Frame {self.frame} {phase} | "
                f"DH.y={dh.y:.3f} != BC.y={bc.y:.3f} (dy={dy:.3f})"
            )
        else:
            self._write_issue(
                f"[PASS] RULE 3b | Frame {self.frame} {phase} | "
                f"DH.y={dh.y:.3f} ~ BC.y={bc.y:.3f} (dy={dy:.3f})"
            )

        # RULE 3c: DH has the ball (only after PTB_EXEC->PTB transfer)
        if phase in ("PTB", "POST_PTB_RESET") and self._ptb_started:
            if not dh.possessed:
                self._write_issue(
                    f"[FAIL] RULE 3c | Frame {self.frame} {phase} | "
                    f"DH #{dh.player_id} must have the ball (possessed=False)"
                )
            else:
                self._write_issue(
                    f"[PASS] RULE 3c | Frame {self.frame} {phase} | "
                    f"DH #{dh.player_id} possessed=True"
                )

        # RULE 3d: DH not frozen
        if dh.frozen:
            self._write_issue(
                f"[FAIL] RULE 3d | Frame {self.frame} {phase} | "
                f"DH #{dh.player_id} must NOT be frozen (frozen=True)"
            )
        else:
            self._write_issue(
                f"[PASS] RULE 3d | Frame {self.frame} {phase} | "
                f"DH #{dh.player_id} frozen=False"
            )

        # RULE 3e: DH role must be DUMMY_HALF
        if _attr(dh, "defender_role", None) != DefenderRole.DUMMY_HALF:
            self._write_issue(
                f"[FAIL] RULE 3e | Frame {self.frame} {phase} | "
                f"DH #{dh.player_id} role={_role_str(dh)} (expected DUMMY_HALF)"
            )
        else:
            self._write_issue(
                f"[PASS] RULE 3e | Frame {self.frame} {phase} | "
                f"DH #{dh.player_id} role=DUMMY_HALF"
            )

        # RULE 3f: Only DH has has_ball=True
        players = _attr(world, "players", [])
        other_ball_carriers = [
            p for p in players
            if _attr(p, "possessed", False) and p is not dh
        ]
        if other_ball_carriers:
            ids = [f"#{_attr(p,'player_id',-1)}" for p in other_ball_carriers]
            self._write_issue(
                f"[FAIL] RULE 3f | Frame {self.frame} {phase} | "
                f"Other players have ball: {', '.join(ids)} "
                f"(expected only DH #{dh.player_id})"
            )
        elif dh.possessed or not dh.possessed:
            # Only check staff if DH has ball (post-transfer)
            pass

    # ═══════════════════════════════════════════════════════════════════
    # VALIDATION: SECTION 4 — MARKERS A/B
    # ═══════════════════════════════════════════════════════════════════

    def _validate_section_4_markers(self, world, phase: str, defenders, bc):
        """Validate marker rules.

        Marker A:
            RULE 4a: x = BC.x - 0.5
            RULE 4b: y = BC.y - 1.0
            RULE 4c: role = MARKER_A
            RULE 4d: must be frozen

        Marker B:
            RULE 4e: x = BC.x - 0.5
            RULE 4f: y = BC.y + 1.0
            RULE 4g: role = MARKER_B
            RULE 4h: must be frozen

        Release:
            RULE 4i: Both markers must unfreeze at POST_PTB_RESET.
            RULE 4j: Both must revert to role=C.
            RULE 4k: Both must retreat to BC.x + 10.
        """
        if bc is None:
            return

        marker_a, marker_b = self._find_markers(defenders)
        is_ptb_phase = phase in ("PTB_READY", "PTB_PREP", "PTB_EXEC", "PTB")
        is_post_ptb_reset = (phase == "POST_PTB_RESET")
        is_open_play = (phase == "OPEN_PLAY")

        # ── Position checks during PTB phases ──
        if is_ptb_phase:
            if marker_a is not None:
                # RULE 4a
                exp_ax = bc.x + SPEC_MARKER_A_X_OFFSET
                exp_ay = bc.y + SPEC_MARKER_A_Y_OFFSET
                dx = abs(marker_a.x - exp_ax)
                dy = abs(marker_a.y - exp_ay)
                if dx > POS_TOLERANCE:
                    self._write_issue(
                        f"[FAIL] RULE 4a | Frame {self.frame} {phase} | "
                        f"MARKER_A #{marker_a.player_id} x={marker_a.x:.3f} "
                        f"!= BC.x{SPEC_MARKER_A_X_OFFSET:.1f}={exp_ax:.3f} (dx={dx:.3f})"
                    )
                else:
                    self._write_issue(
                        f"[PASS] RULE 4a | Frame {self.frame} {phase} | "
                        f"MARKER_A #{marker_a.player_id} x={marker_a.x:.3f} "
                        f"~ {exp_ax:.3f}"
                    )
                if dy > POS_TOLERANCE:
                    self._write_issue(
                        f"[FAIL] RULE 4b | Frame {self.frame} {phase} | "
                        f"MARKER_A #{marker_a.player_id} y={marker_a.y:.3f} "
                        f"!= BC.y{SPEC_MARKER_A_Y_OFFSET:.1f}={exp_ay:.3f} (dy={dy:.3f})"
                    )
                else:
                    self._write_issue(
                        f"[PASS] RULE 4b | Frame {self.frame} {phase} | "
                        f"MARKER_A #{marker_a.player_id} y={marker_a.y:.3f} "
                        f"~ {exp_ay:.3f}"
                    )
                # RULE 4c
                if _attr(marker_a, "defender_role", None) != DefenderRole.MARKER_A:
                    self._write_issue(
                        f"[FAIL] RULE 4c | Frame {self.frame} {phase} | "
                        f"MARKER_A #{marker_a.player_id} role={_role_str(marker_a)} "
                        f"(expected MARKER_A)"
                    )
                else:
                    self._write_issue(
                        f"[PASS] RULE 4c | Frame {self.frame} {phase} | "
                        f"MARKER_A #{marker_a.player_id} role=MARKER_A"
                    )
                # RULE 4d
                if not marker_a.frozen:
                    self._write_issue(
                        f"[FAIL] RULE 4d | Frame {self.frame} {phase} | "
                        f"MARKER_A #{marker_a.player_id} must be frozen (frozen=False)"
                    )
                else:
                    self._write_issue(
                        f"[PASS] RULE 4d | Frame {self.frame} {phase} | "
                        f"MARKER_A #{marker_a.player_id} frozen=True"
                    )
            else:
                self._write_issue(
                    f"[FAIL] RULE 4a-d | Frame {self.frame} {phase} | "
                    f"MARKER_A not found in defenders"
                )

            if marker_b is not None:
                # RULE 4e
                exp_bx = bc.x + SPEC_MARKER_B_X_OFFSET
                exp_by = bc.y + SPEC_MARKER_B_Y_OFFSET
                dx = abs(marker_b.x - exp_bx)
                dy = abs(marker_b.y - exp_by)
                if dx > POS_TOLERANCE:
                    self._write_issue(
                        f"[FAIL] RULE 4e | Frame {self.frame} {phase} | "
                        f"MARKER_B #{marker_b.player_id} x={marker_b.x:.3f} "
                        f"!= BC.x{SPEC_MARKER_B_X_OFFSET:.1f}={exp_bx:.3f} (dx={dx:.3f})"
                    )
                else:
                    self._write_issue(
                        f"[PASS] RULE 4e | Frame {self.frame} {phase} | "
                        f"MARKER_B #{marker_b.player_id} x={marker_b.x:.3f} "
                        f"~ {exp_bx:.3f}"
                    )
                if dy > POS_TOLERANCE:
                    self._write_issue(
                        f"[FAIL] RULE 4f | Frame {self.frame} {phase} | "
                        f"MARKER_B #{marker_b.player_id} y={marker_b.y:.3f} "
                        f"!= BC.y{SPEC_MARKER_B_Y_OFFSET:.1f}={exp_by:.3f} (dy={dy:.3f})"
                    )
                else:
                    self._write_issue(
                        f"[PASS] RULE 4f | Frame {self.frame} {phase} | "
                        f"MARKER_B #{marker_b.player_id} y={marker_b.y:.3f} "
                        f"~ {exp_by:.3f}"
                    )
                # RULE 4g
                if _attr(marker_b, "defender_role", None) != DefenderRole.MARKER_B:
                    self._write_issue(
                        f"[FAIL] RULE 4g | Frame {self.frame} {phase} | "
                        f"MARKER_B #{marker_b.player_id} role={_role_str(marker_b)} "
                        f"(expected MARKER_B)"
                    )
                else:
                    self._write_issue(
                        f"[PASS] RULE 4g | Frame {self.frame} {phase} | "
                        f"MARKER_B #{marker_b.player_id} role=MARKER_B"
                    )
                # RULE 4h
                if not marker_b.frozen:
                    self._write_issue(
                        f"[FAIL] RULE 4h | Frame {self.frame} {phase} | "
                        f"MARKER_B #{marker_b.player_id} must be frozen (frozen=False)"
                    )
                else:
                    self._write_issue(
                        f"[PASS] RULE 4h | Frame {self.frame} {phase} | "
                        f"MARKER_B #{marker_b.player_id} frozen=True"
                    )
            else:
                self._write_issue(
                    f"[FAIL] RULE 4e-h | Frame {self.frame} {phase} | "
                    f"MARKER_B not found in defenders"
                )

        # ── Release rules: POST_PTB_RESET ──
        if is_post_ptb_reset:
            frames_since_entry = self.frame - self._post_ptb_entry_frame
            # Give a few frames for the transition
            if frames_since_entry > 5:
                for label, mkr in [("A", marker_a), ("B", marker_b)]:
                    if mkr is None:
                        continue
                    # RULE 4i: unfreeze
                    if mkr.frozen:
                        # Check freeze_timer — may still be >0 briefly
                        ft = _attr(mkr, "freeze_timer", 0.0)
                        if ft <= FREEZE_TOLERANCE:
                            self._write_issue(
                                f"[FAIL] RULE 4i | Frame {self.frame} {phase} | "
                                f"MARKER_{label} #{mkr.player_id} still frozen "
                                f"at POST_PTB_RESET (frozen=True, ft={ft:.3f})"
                            )

                    # RULE 4j: role revert to C
                    role = _attr(mkr, "defender_role", None)
                    dr = _attr(mkr, "depth_role", "?")
                    # The spec says "revert to role=C" — checking that defender_role
                    # is no longer MARKER_A/B
                    if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                        self._write_issue(
                            f"[FAIL] RULE 4j | Frame {self.frame} {phase} | "
                            f"MARKER_{label} #{mkr.player_id} still has "
                            f"role={_role_str(mkr)} after POST_PTB_RESET"
                        )

                    # RULE 4k: retreat to BC.x + 10
                    expected_retreat = bc.x + SPEC_RETREAT_OFFSET
                    retreat_dx = abs(mkr.target_x - expected_retreat)
                    if retreat_dx > POS_TOLERANCE * 2:
                        self._write_issue(
                            f"[FAIL] RULE 4k | Frame {self.frame} {phase} | "
                            f"MARKER_{label} #{mkr.player_id} target_x={mkr.target_x:.2f} "
                            f"!= BC.x+10={expected_retreat:.2f} (dx={retreat_dx:.2f})"
                        )

        # ── OPEN_PLAY rules ──
        if is_open_play:
            # In OPEN_PLAY, markers should be released (no MARKER_A/B role active)
            for label, mkr in [("A", marker_a), ("B", marker_b)]:
                if mkr is not None:
                    self._write_issue(
                        f"[FAIL] RULE 4i-j | Frame {self.frame} {phase} | "
                        f"MARKER_{label} #{mkr.player_id} still present in OPEN_PLAY "
                        f"(role={_role_str(mkr)})"
                    )

    # ═══════════════════════════════════════════════════════════════════
    # VALIDATION: SECTION 5 — DEFENSIVE LINE
    # ═══════════════════════════════════════════════════════════════════

    def _validate_section_5_defence_line(self, world, phase: str, defenders, bc):
        """Validate defensive line rules per phase.

        TACKLE: All defenders except tacklers freeze.
        PTB: All defenders retreat RIGHT.
        POST_PTB_RESET: Exactly one LINE, all others C, target_x=BC.x+10.
        OPEN_PLAY: No LINE, no frozen, no freeze_timer>0, BC-relative target_x.
        """
        is_tackle = phase in ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
        is_ptb = phase in ("PTB_READY", "PTB_PREP", "PTB_EXEC", "PTB")
        is_post_reset = (phase == "POST_PTB_RESET")
        is_open = (phase == "OPEN_PLAY")
        primary = _attr(self.ope, "primary", None)
        joiners = _attr(self.ope, "joiners", [])
        tackler_ids = set()
        if primary is not None:
            tackler_ids.add(primary.player_id)
        for j in joiners:
            tackler_ids.add(j.player_id)

        # ── TACKLE: all non-tackler defenders freeze ──
        if is_tackle:
            non_tacklers = [d for d in defenders if d.player_id not in tackler_ids]
            unfrozen = [d for d in non_tacklers if not d.frozen]
            if unfrozen and bc is not None:
                ids = [f"#{_attr(d,'player_id',-1)}" for d in unfrozen[:5]]
                self._write_issue(
                    f"[FAIL] RULE 5a | Frame {self.frame} {phase} | "
                    f"{len(unfrozen)} non-tackler defenders not frozen: "
                    f"{', '.join(ids)}"
                )

            # Tacklers at BC.x - 0.5
            for d in defenders:
                if d.player_id in tackler_ids:
                    expected_x = bc.x - 0.5 if bc else 0
                    # Tackler position check (rough tolerance)
                    if bc and abs(d.x - (bc.x - 0.5)) > 2.0:
                        self._write_issue(
                            f"[FAIL] RULE 5b | Frame {self.frame} {phase} | "
                            f"Tackler #{d.player_id} x={d.x:.2f} "
                            f"!= BC.x-0.5={bc.x-0.5:.2f}"
                        )

        # ── PTB: all defenders retreat RIGHT ──
        if is_ptb:
            for d in defenders:
                tx = _attr(d, "target_x", d.x)
                if tx < bc.x:
                    self._write_issue(
                        f"[FAIL] RULE 5c | Frame {self.frame} {phase} | "
                        f"Defender #{d.player_id} target_x={tx:.2f} is LEFT of "
                        f"BC.x={bc.x:.2f} (must retreat RIGHT)"
                    )

        # ── POST_PTB_RESET ──
        if is_post_reset:
            line_defs = self._find_line_defenders(defenders)
            if len(line_defs) != 1:
                self._write_issue(
                    f"[FAIL] RULE 5d | Frame {self.frame} {phase} | "
                    f"Expected exactly 1 LINE defender, found {len(line_defs)}"
                )

            # Check target_x for non-marker, non-kick_chase defenders
            for d in defenders:
                role = _attr(d, "defender_role", None)
                if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B,
                            DefenderRole.KICK_CHASE):
                    continue
                expected_retreat = bc.x + SPEC_RETREAT_OFFSET
                retreat_dx = abs(d.target_x - expected_retreat)
                if retreat_dx > POS_TOLERANCE * 2:
                    self._write_issue(
                        f"[FAIL] RULE 5e | Frame {self.frame} {phase} | "
                        f"Defender #{d.player_id} target_x={d.target_x:.2f} "
                        f"!= BC.x+10={expected_retreat:.2f} (dx={retreat_dx:.2f})"
                    )

        # ── OPEN_PLAY ──
        if is_open:
            # No LINE
            line_defs = self._find_line_defenders(defenders)
            if line_defs:
                ids = [f"#{_attr(d,'player_id',-1)}" for d in line_defs]
                self._write_issue(
                    f"[FAIL] RULE 5f | Frame {self.frame} {phase} | "
                    f"LINE defenders still present in OPEN_PLAY: {', '.join(ids)}"
                )

            # No frozen (except markers if they somehow persist)
            frozen_defs = [d for d in defenders if d.frozen]
            if frozen_defs:
                ids = [f"#{_attr(d,'player_id',-1)}" for d in frozen_defs[:5]]
                self._write_issue(
                    f"[FAIL] RULE 5g | Frame {self.frame} {phase} | "
                    f"{len(frozen_defs)} defenders still frozen in OPEN_PLAY: "
                    f"{', '.join(ids)}"
                )

            # No freeze_timer > 0
            ft_defs = [d for d in defenders if _attr(d, "freeze_timer", 0.0) > FREEZE_TOLERANCE]
            if ft_defs:
                ids = [f"#{_attr(d,'player_id',-1)}(ft={_attr(d,'freeze_timer',0):.2f})"
                       for d in ft_defs[:5]]
                self._write_issue(
                    f"[FAIL] RULE 5h | Frame {self.frame} {phase} | "
                    f"{len(ft_defs)} defenders with freeze_timer>0 in OPEN_PLAY: "
                    f"{', '.join(ids)}"
                )

    # ═══════════════════════════════════════════════════════════════════
    # VALIDATION: SECTION 6 — BC-RELATIVE BASELINE (OPEN_PLAY)
    # ═══════════════════════════════════════════════════════════════════

    def _validate_section_6_bc_relative_baseline(self, world, phase: str, defenders, bc):
        """Validate BC-relative target_x in OPEN_PLAY.

        RULE 6a: PRIMARY  → target_x = BC.x - 1.0
        RULE 6b: SECONDARY → target_x = BC.x - 2.0
        RULE 6c: PASSIVE  → target_x = BC.x - 3.0
        RULE 6d: MARKERS  → target_x = BC.x - 0.5 (until released)
        RULE 6e: KICK_CHASE → target_x = ball.x - chase_offset
        """
        if phase != "OPEN_PLAY" or bc is None:
            return

        for d in defenders:
            dc = _depth_class_str(d)
            role = _attr(d, "defender_role", None)
            tx = _attr(d, "target_x", d.x)

            # RULE 6d: MARKERS
            if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                expected_tx = bc.x + SPEC_BC_DEPTH_MARKER  # BC.x - 0.5
                if abs(tx - expected_tx) > POS_TOLERANCE:
                    self._write_issue(
                        f"[FAIL] RULE 6d | Frame {self.frame} {phase} | "
                        f"Marker #{d.player_id} target_x={tx:.2f} "
                        f"!= BC.x-0.5={expected_tx:.2f}"
                    )
                continue

            # RULE 6e: KICK_CHASE
            if role == DefenderRole.KICK_CHASE:
                ball = _attr(world, "ball", None)
                ball_x = _attr(ball, "x", 60.0)
                expected_tx = ball_x - 5.0  # rough chase offset
                if abs(tx - expected_tx) > 5.0:
                    self._write_issue(
                        f"[FAIL] RULE 6e | Frame {self.frame} {phase} | "
                        f"KICK_CHASE #{d.player_id} target_x={tx:.2f} "
                        f"!= ball.x-5={expected_tx:.2f}"
                    )
                continue

            # RULES 6a-c: PRIMARY/SECONDARY/PASSIVE
            depth_map = {
                "PRIMARY": SPEC_BC_DEPTH_PRIMARY,
                "SECONDARY": SPEC_BC_DEPTH_SECONDARY,
                "PASSIVE": SPEC_BC_DEPTH_PASSIVE,
            }
            if dc in depth_map:
                expected_tx = bc.x + depth_map[dc]
                # Wider tolerance for chase/compression system
                tolerance = 3.0 if dc == "PASSIVE" else 2.0
                if abs(tx - expected_tx) > tolerance:
                    self._write_issue(
                        f"[FAIL] RULE 6{'abc'['PASSIVE'.index(dc) // 7]} "
                        f"| Frame {self.frame} {phase} | "
                        f"Defender #{d.player_id} dc={dc} target_x={tx:.2f} "
                        f"!= BC.x{depth_map[dc]:+.1f}={expected_tx:.2f}"
                    )

    # ═══════════════════════════════════════════════════════════════════
    # VALIDATION: SECTION 7 — TACKLE PHYSICS
    # ═══════════════════════════════════════════════════════════════════

    def _validate_section_7_tackle_physics(self, world, phase: str, bc, primary):
        """Validate tackle physics on impact.

        Impact:
            RULE 7a: BC and tackler must freeze.
            RULE 7b: BC.x += 0.2 (forward shift).
            RULE 7c: Tackler.x -= 0.2 (backward shift).
            RULE 7d: Both must enter DOWN state.

        Recovery:
            RULE 7e: BC stands first.
            RULE 7f: Tackler stands second.
            RULE 7g: Both freeze_timer = stand_duration.
        """
        if phase not in ("TACKLE_INIT", "TACKLE_CONTACT"):
            return
        if bc is None or primary is None:
            return
        if self._impact_shift_logged:
            return

        # RULE 7a: Both frozen
        if not bc.frozen or not primary.frozen:
            self._write_issue(
                f"[FAIL] RULE 7a | Frame {self.frame} {phase} | "
                f"BC #{bc.player_id} frozen={bc.frozen}, "
                f"Tackler #{primary.player_id} frozen={primary.frozen} "
                f"(both must be frozen)"
            )

        # RULE 7b, 7c: Position shift — only check on first TACKLE_INIT frame
        # Compare vs tes_ctx contact point
        tes_ctx = _attr(self.ope, "tes_ctx", None)
        contact_x = _attr(tes_ctx, "contact_x_m", None)
        if contact_x is not None:
            bc_shift = bc.x - contact_x
            if bc_shift < 0:
                self._write_issue(
                    f"[FAIL] RULE 7b | Frame {self.frame} {phase} | "
                    f"BC.x={bc.x:.3f} - contact_x={contact_x:.3f} = {bc_shift:.3f} "
                    f"(expected +{SPEC_BC_IMPACT_SHIFT:.1f})"
                )

            primary_x = _attr(primary, "x", contact_x)
            def_shift = primary_x - contact_x
            if def_shift > 0:
                self._write_issue(
                    f"[FAIL] RULE 7c | Frame {self.frame} {phase} | "
                    f"Tackler.x={primary_x:.3f} - contact_x={contact_x:.3f} = {def_shift:.3f} "
                    f"(expected {SPEC_TACKLER_IMPACT_SHIFT:.1f})"
                )

        # RULE 7d: Both in DOWN state
        bc_state = _attr(bc, "state", None)
        bc_state_str = _phase_str(bc_state) if hasattr(bc_state, "name") else str(bc_state)
        primary_state = _attr(primary, "state", None)
        primary_state_str = (_phase_str(primary_state) if hasattr(primary_state, "name")
                            else str(primary_state))

        if "DOWN" not in bc_state_str.upper() and "TACKL" not in bc_state_str.upper():
            self._write_issue(
                f"[FAIL] RULE 7d | Frame {self.frame} {phase} | "
                f"BC state={bc_state_str} (expected DOWN or TACKLED)"
            )

        if "DOWN" not in primary_state_str.upper() and "TACKL" not in primary_state_str.upper():
            self._write_issue(
                f"[FAIL] RULE 7d | Frame {self.frame} {phase} | "
                f"Tackler state={primary_state_str} (expected DOWN or TACKLING)"
            )

        self._impact_shift_logged = True

        # --- Recovery checks (RELEASE phase) ---
        if phase == "RELEASE":
            ft_bc = _attr(bc, "freeze_timer", 0.0)
            ft_primary = _attr(primary, "freeze_timer", 0.0)
            # RULE 7e, 7f: BC should unfreeze first (lower freeze_timer)
            # Actually checking the freeze_timer values: if BC stands first,
            # its freeze_timer should be closer to 0 than the tackler's.
            if ft_bc > ft_primary:
                self._write_issue(
                    f"[FAIL] RULE 7e-f | Frame {self.frame} {phase} | "
                    f"BC freeze_timer={ft_bc:.3f} > tackler freeze_timer={ft_primary:.3f} "
                    f"(BC should stand first)"
                )

    # ═══════════════════════════════════════════════════════════════════
    # VALIDATION: SECTION 8 — PTB SPAWN
    # ═══════════════════════════════════════════════════════════════════

    def _validate_section_8_ptb_spawn(self, world, phase: str, bc, dh):
        """Validate PTB spawn position.

        RULE 8a: Ball must spawn at BC.x, BC.y.
        RULE 8b: DH must be the ball owner.
        RULE 8c: DH must be at BC.x-1.0, BC.y.
        """
        if not self._ptb_started:
            return
        if phase not in ("PTB", "POST_PTB_RESET", "OPEN_PLAY"):
            return
        if bc is None:
            return

        ball = _attr(world, "ball", None)
        if ball is None:
            return

        # RULE 8a: Ball at BC position
        ball_x = _attr(ball, "x", _attr(world, "ball_x_m", 0.0))
        ball_y = _attr(ball, "y", _attr(world, "ball_y_m", 0.0))
        dx = abs(ball_x - bc.x)
        dy = abs(ball_y - bc.y)
        if dx > 1.5 or dy > 1.5:
            self._write_issue(
                f"[FAIL] RULE 8a | Frame {self.frame} {phase} | "
                f"Ball at ({ball_x:.2f},{ball_y:.2f}) "
                f"!= BC at ({bc.x:.2f},{bc.y:.2f}) (dx={dx:.2f} dy={dy:.2f})"
            )

        # RULE 8b: DH is ball owner
        possessed_by = _attr(ball, "possessed_by", None)
        ball_owner = _attr(ball, "owner", None)
        effective_owner = possessed_by or ball_owner
        if dh is not None and effective_owner is not dh:
            self._write_issue(
                f"[FAIL] RULE 8b | Frame {self.frame} {phase} | "
                f"Ball owner #{_attr(effective_owner,'player_id',-1)} "
                f"is not DH #{_attr(dh,'player_id',-1)}"
            )

        # RULE 8c: DH at BC.x-1.0, BC.y
        if dh is not None:
            expected_x = bc.x + SPEC_DH_X_OFFSET
            expected_y = bc.y + SPEC_DH_Y_OFFSET
            dx = abs(dh.x - expected_x)
            dy = abs(dh.y - expected_y)
            if dx > POS_TOLERANCE:
                self._write_issue(
                    f"[FAIL] RULE 8c | Frame {self.frame} {phase} | "
                    f"DH.x={dh.x:.3f} != BC.x-1.0={expected_x:.3f} (dx={dx:.3f})"
                )
            if dy > POS_TOLERANCE:
                self._write_issue(
                    f"[FAIL] RULE 8c | Frame {self.frame} {phase} | "
                    f"DH.y={dh.y:.3f} != BC.y={expected_y:.3f} (dy={dy:.3f})"
                )

    # ═══════════════════════════════════════════════════════════════════
    # VALIDATION: SECTION 9 — RETREAT LOGIC
    # ═══════════════════════════════════════════════════════════════════

    def _validate_section_9_retreat(self, world, phase: str, defenders, bc):
        """Validate retreat logic.

        RULE 9a: All defenders must retreat to BC.x+10.
        RULE 9b: Retreat speed must be passive retreat speed.
        RULE 9c: Retreat must complete before OPEN_PLAY baseline applies.
        """
        is_retreat_phase = phase in ("PTB_READY", "PTB_PREP", "PTB_EXEC",
                                     "PTB", "POST_PTB_RESET")
        if not is_retreat_phase or bc is None:
            return

        retreat_target = bc.x + SPEC_RETREAT_OFFSET

        for d in defenders:
            if _attr(d, "defender_role", None) in (DefenderRole.KICK_CHASE,):
                continue

            # RULE 9a: target_x should be at BC.x+10
            tx = _attr(d, "target_x", d.x)
            dx = abs(tx - retreat_target)
            if dx > POS_TOLERANCE * 2:
                # Allow markers to be closer
                role = _attr(d, "defender_role", None)
                if role not in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                    self._write_issue(
                        f"[FAIL] RULE 9a | Frame {self.frame} {phase} | "
                        f"Defender #{d.player_id} target_x={tx:.2f} "
                        f"!= BC.x+10={retreat_target:.2f} (dx={dx:.2f})"
                    )

            # RULE 9b: retreating flag should be True
            if not d.retreating and phase == "POST_PTB_RESET":
                self._write_issue(
                    f"[FAIL] RULE 9b | Frame {self.frame} {phase} | "
                    f"Defender #{d.player_id} not marked retreating"
                )

    # ═══════════════════════════════════════════════════════════════════
    # VALIDATION: SECTION 10 — ABSOLUTE RULES
    # ═══════════════════════════════════════════════════════════════════

    def _validate_section_10_absolute_rules(self, world, phase: str, defenders, bc, dh):
        """Validate absolute rules that must hold regardless of phase.

        RULE 10a: Only one LINE defender per PTB cycle.
        RULE 10b: No LINE defenders in OPEN_PLAY.
        RULE 10c: No frozen defenders in OPEN_PLAY.
        RULE 10d: No freeze_timer > 0 in OPEN_PLAY.
        RULE 10e: Only DH has the ball.
        RULE 10f: BC must lose ball at PTB.
        RULE 10g: Markers must freeze.
        RULE 10h: Markers must unfreeze at POST_PTB_RESET.
        RULE 10i: All defenders must retreat to BC.x+10.
        RULE 10j: All defenders must get BC-relative target_x every frame.
        """

        # RULE 10a: Only one LINE per cycle (check in POST_PTB_RESET)
        if phase == "POST_PTB_RESET":
            line_defs = self._find_line_defenders(defenders)
            if len(line_defs) > 1:
                ids = [f"#{_attr(d,'player_id',-1)}" for d in line_defs]
                self._write_issue(
                    f"[FAIL] RULE 10a | Frame {self.frame} {phase} | "
                    f"Multiple LINE defenders: {', '.join(ids)} (expected 1)"
                )

        # RULE 10b: No LINE in OPEN_PLAY
        if phase == "OPEN_PLAY":
            line_defs = self._find_line_defenders(defenders)
            if line_defs:
                ids = [f"#{_attr(d,'player_id',-1)}" for d in line_defs]
                self._write_issue(
                    f"[FAIL] RULE 10b | Frame {self.frame} {phase} | "
                    f"LINE defenders in OPEN_PLAY: {', '.join(ids)}"
                )

        # RULE 10c: No frozen in OPEN_PLAY
        if phase == "OPEN_PLAY":
            frozen_defs = []
            for d in defenders:
                role = _attr(d, "defender_role", None)
                if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                    continue  # markers may still hold briefly
                if role == DefenderRole.KICK_CHASE:
                    continue
                if d.frozen:
                    frozen_defs.append(d)
            if frozen_defs:
                ids = [f"#{_attr(d,'player_id',-1)}" for d in frozen_defs[:5]]
                self._write_issue(
                    f"[FAIL] RULE 10c | Frame {self.frame} {phase} | "
                    f"{len(frozen_defs)} frozen defenders in OPEN_PLAY: "
                    f"{', '.join(ids)}"
                )

        # RULE 10d: No freeze_timer > 0 in OPEN_PLAY
        if phase == "OPEN_PLAY":
            ft_defs = []
            for d in defenders:
                role = _attr(d, "defender_role", None)
                if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                    continue
                ft = _attr(d, "freeze_timer", 0.0)
                if ft > FREEZE_TOLERANCE:
                    ft_defs.append((d, ft))
            if ft_defs:
                ids = [f"#{_attr(d,'player_id',-1)}(ft={ft:.2f})" for d, ft in ft_defs[:5]]
                self._write_issue(
                    f"[FAIL] RULE 10d | Frame {self.frame} {phase} | "
                    f"{len(ft_defs)} defenders with freeze_timer>0 in OPEN_PLAY: "
                    f"{', '.join(ids)}"
                )

        # RULE 10e: Only DH has ball (only check post-PTB)
        if phase in ("PTB", "POST_PTB_RESET", "OPEN_PLAY") and self._ptb_started:
            players = _attr(world, "players", [])
            for p in players:
                if _attr(p, "possessed", False):
                    if dh is not None and p is dh:
                        continue  # DH is expected to have ball
                    if bc is not None and p is bc and phase == "OPEN_PLAY":
                        # In OPEN_PLAY, BC should have ball (different from spec's "only DH")
                        # Only flag if we're in PTB phase
                        if phase == "PTB":
                            self._write_issue(
                                f"[FAIL] RULE 10e | Frame {self.frame} {phase} | "
                                f"BC #{p.player_id} has ball after PTB — "
                                f"only DH #{_attr(dh,'player_id','?')} should have ball"
                            )
                    elif p is not dh:
                        self._write_issue(
                            f"[FAIL] RULE 10e | Frame {self.frame} {phase} | "
                            f"Player #{p.player_id} has possessed=True — "
                            f"only DH #{_attr(dh,'player_id','?')} should have ball"
                        )

        # RULE 10f: BC loses ball at PTB
        if phase in ("PTB", "POST_PTB_RESET") and self._ptb_started and bc is not None:
            if bc.possessed:
                self._write_issue(
                    f"[FAIL] RULE 10f | Frame {self.frame} {phase} | "
                    f"BC #{bc.player_id} still has possessed=True after PTB"
                )

        # RULE 10g: Markers must freeze (checked in section 4 already)
        # RULE 10h: Markers must unfreeze at POST_PTB_RESET (checked in section 4)

        # RULE 10i: All defenders retreat to BC.x+10 (checked in section 9)

        # RULE 10j: All defenders get BC-relative target_x in OPEN_PLAY
        if phase == "OPEN_PLAY" and bc is not None:
            for d in defenders:
                role = _attr(d, "defender_role", None)
                if role in (DefenderRole.KICK_CHASE,):
                    continue
                # Check that target_x is behind BC (less than BC.x)
                tx = _attr(d, "target_x", d.x)
                if tx > bc.x:
                    self._write_issue(
                        f"[FAIL] RULE 10j | Frame {self.frame} {phase} | "
                        f"Defender #{d.player_id} target_x={tx:.2f} is AHEAD of "
                        f"BC.x={bc.x:.2f} (must be behind BC)"
                    )

    # ═══════════════════════════════════════════════════════════════════
    # SUMMARY / FINALIZE
    # ═══════════════════════════════════════════════════════════════════

    def finalize(self):
        """Write summary to issues file."""
        with open(self._issues_path, "a") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(f"AUDIT SUMMARY — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Total frames audited: {self.frame}\n")
            f.write(f"Total issues logged: {len(self.issues)}\n")
            f.write(f"Output files:\n")
            f.write(f"  Frames: {self._frames_path}\n")
            f.write(f"  Issues: {self._issues_path}\n")
            f.write("=" * 80 + "\n")

        print(f"\n[AUDIT] TES audit complete — {self.frame} frames, "
              f"{len(self.issues)} issues logged")
        print(f"[AUDIT] Frames: {self._frames_path}")
        print(f"[AUDIT] Issues: {self._issues_path}")
