"""
game/audit/tackle_sdea.py — TACKLE SDEA (Structured Diagnostic Event Audit).

A strictly READ-ONLY, frame-accurate observer of the tackle chain:

    PRE-TACKLE → CONTACT → DRIVE → LOCK → PTB_PREP

Mapped onto the OpenPlayEngine phase machine:

    PRE-TACKLE       OPEN_PLAY (rolling snapshot buffer)
    CONTACT          TACKLE_INIT, TACKLE_CONTACT
    DRIVE            TACKLE_DRIVE
    LOCK             TACKLE_DOMINANCE
    PTB_PREP         RELEASE (+ PTB_PREP entry)

THE CONTRACT
------------
This auditor MUST NOT modify physics, roles, targets, freeze, ruck,
markers, SDM, OPE, or world state.  It only reads via getattr and keeps
its own internal buffers.  It does NOT hook the engine — it detects
phase transitions by observing `ope._phase` each frame, so the PRE-TACKLE
baseline comes from a rolling OPEN_PLAY snapshot (no engine changes).

OUTPUTS
-------
    audit_tackle_sdea_frames_{ts}.csv    — per-frame snapshot rows
    audit_tackle_sdea_tackles_{ts}.csv   — one summary row per tackle
    audit_tackle_sdea_issues_{ts}.txt    — tagged analysis lines:
        BC_BACKWARD_DRIVE, DRIVE_VECTOR_MISALIGNMENT, INCORRECT_TACKLE_SCORE,
        RUCK_PLACEMENT_ERROR, MARKER_ARRIVAL_DELAY, PTB_PREP_TOO_EARLY,
        FREEZE_ELIGIBILITY_VIOLATION

USAGE
-----
    from game.audit.tackle_sdea import TackleSDEA
    sdea = TackleSDEA()
    # each frame, after ope.update(dt):
    sdea.audit(world, ope)
    # at end:
    sdea.finalize()
"""

import csv
import math
from datetime import datetime

from world.rules.movement_contracts import MOVEMENT_RULES, get_role
# CONVERGENCE-FIRST RELEASE (2026-09-17): the RELEASE exit is a convergence
# predicate with a 0.10 s floor, not the old 1.54 s wall.  Import the
# authority's floor rather than re-hard-coding it, so this audit cannot
# drift from the engine again -- the old literal flagged EVERY cycle as
# PTB_PREP_TOO_EARLY once RELEASE began exiting at ~0.9 s.
from world.rules.release_gate import RELEASE_MIN_CONVERGENCE_S

# ───────────────────────────────────────────────────────────────────────
# TOLERANCES / EXPECTATIONS (Block 5 analysis thresholds)
# ───────────────────────────────────────────────────────────────────────
POS_TOLERANCE_M = 0.35          # marker arrival proximity (m)
DRIVE_MISALIGN_TOLERANCE_M = 0.5  # actual vs target delta tolerance (m)
RUCK_PLACEMENT_TOLERANCE_M = 1.0  # ruck_x vs baseline BC.x tolerance (m)
# RELEASE floor: RELEASE_MIN_CONVERGENCE_S, imported from the single
# authority (world/rules/release_gate.py) at the top of this module.

_PHASE_TO_STAGE = {
    "OPEN_PLAY": "PRE_TACKLE",
    "TACKLE_INIT": "CONTACT",
    "TACKLE_CONTACT": "CONTACT",
    "TACKLE_DRIVE": "DRIVE",
    "TACKLE_DOMINANCE": "LOCK",
    "RELEASE": "PTB_PREP",
    "PTB_PREP": "PTB_PREP",
}

# Phases that belong to an active tackle cycle.
_TACKLE_PHASES = frozenset({
    "TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE",
    "TACKLE_DOMINANCE", "RELEASE", "PTB_PREP", "PTB_EXEC", "PTB",
})


def _f(obj, name, default=None):
    """Safe attribute read — SDEA is strictly read-only."""
    if obj is None:
        return default
    return getattr(obj, name, default)


def _speed(p):
    return math.hypot(_f(p, "vx", 0.0), _f(p, "vy", 0.0))


def _phase_str(ope):
    return _f(ope, "_phase", "OPEN_PLAY")


class TackleSDEA:
    """Read-only per-tackle observer over the OPE phase machine."""

    def __init__(self):
        self.frame = 0
        self.ope = None

        # ── Rolling OPEN_PLAY snapshot (PRE-TACKLE baseline source) ──
        # Written every PRE-TACKLE frame; frozen on TACKLE_INIT entry.
        self._open_play_bc = None       # (x, y, vx, vy, speed)
        self._open_play_tackler = None  # (id, x, y, vx, vy, speed) closest defender

        # ── Current tackle cycle accumulation ──
        self._tackle = None             # dict being accumulated
        self._tackle_cycle_count = 0    # per-run tackle sequence number
        self._last_release_timer = None  # RELEASE elapsed, sampled at PTB_PREP entry
        self._prev_phase = "OPEN_PLAY"
        self._prev_tackle_frame = -1

        # ── Output buffers ──
        self._frames: list[list] = []
        self._tackles: list[list] = []
        self._issues: list[str] = []

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._frames_path = f"audit_tackle_sdea_frames_{ts}.csv"
        self._tackles_path = f"audit_tackle_sdea_tackles_{ts}.csv"
        self._issues_path = f"audit_tackle_sdea_issues_{ts}.txt"

        self._frame_headers = [
            "frame", "stage", "phase", "phase_timer",
            "bc_id", "bc_x", "bc_y", "bc_vx", "bc_vy", "bc_speed", "bc_frozen",
            "tackler_id", "tackler_x", "tackler_y", "tackler_vx", "tackler_vy",
            "tackler_speed", "tackler_frozen",
            "ruck_x", "ruck_y",
            "tes_stability", "tes_collapsed", "ptb_ready",
        ]
        self._tackle_headers = [
            "tackle_id", "tackle_number", "start_frame", "end_frame",
            "phase_timer_at_ptb_prep",
            "bc_pre_x", "bc_pre_y", "bc_pre_speed",
            "bc_post_x", "bc_post_y", "bc_post_speed",
            "bc_drive_start_x", "bc_drive_end_x", "bc_drive_delta_x",
            "tackler_pre_id", "tackler_pre_speed",
            "tackler_post_id", "tackler_post_speed",
            "drive_distance_m", "drive_target_delta_x", "attack_dir",
            "initial_impact_score", "attacker_dominance", "defender_dominance",
            "stability", "collapsed",
            "ruck_x", "ruck_y", "ruck_placement_error",
            "marker_a_arrival_frame", "marker_b_arrival_frame",
            "ptb_ready", "freeze_violations",
            "result", "drive_vector_misaligned", "backward_drive",
        ]

        with open(self._frames_path, "w", newline="") as f:
            csv.writer(f).writerow(self._frame_headers)
        with open(self._tackles_path, "w", newline="") as f:
            csv.writer(f).writerow(self._tackle_headers)
        with open(self._issues_path, "w") as f:
            f.write(f"TACKLE SDEA — {ts}\n")
            f.write("=" * 80 + "\n")
            f.write("Read-only per-frame and per-tackle audit of the tackle chain.\n\n")

    # ───────────────────────────────────────────────────────────────────
    # MAIN ENTRY — call once per frame AFTER ope.update(dt)
    # ───────────────────────────────────────────────────────────────────

    def audit(self, world, ope) -> None:
        self.frame = world.frame_count if _f(world, "frame_count", 0) else self.frame + 1
        self.ope = ope
        phase = _phase_str(ope)
        stage = _PHASE_TO_STAGE.get(phase, phase)

        # ── Detect new tackle cycle: OPEN_PLAY → TACKLE_INIT ──
        if phase == "TACKLE_INIT" and self._prev_phase != "TACKLE_INIT":
            self._begin_tackle(world, ope)

        # ── Close cycle: back to OPEN_PLAY ──
        if self._tackle is not None and phase == "OPEN_PLAY":
            self._complete_tackle()

        # ── PRE-TACKLE rolling snapshot (only when not in a tackle) ──
        if stage == "PRE_TACKLE" and self._tackle is None:
            self._capture_open_play_snapshot(world, ope)

        # ── Per-phase accumulation within an active cycle ──
        if self._tackle is not None and stage in ("CONTACT", "DRIVE", "LOCK", "PTB_PREP"):
            self._accumulate_phase(world, ope, phase, stage)

        # ── Per-frame row ──
        self._write_frame_row(world, ope, phase, stage)

        self._prev_phase = phase

    # ───────────────────────────────────────────────────────────────────
    # PRE-TACKLE BASELINE
    # ───────────────────────────────────────────────────────────────────

    def _capture_open_play_snapshot(self, world, ope):
        bc = world.ball_carrier
        if bc is not None:
            self._open_play_bc = (bc.x, bc.y, bc.vx, bc.vy, _speed(bc))
        else:
            self._open_play_bc = None

        # Closest defender to BC that is not the BC and not the DH.
        closest = None
        best_d = float("inf")
        if bc is not None:
            for d in world.defending_team:
                if d is bc:
                    continue
                if d.defender_role is not None and d.defender_role.name == "DUMMY_HALF":
                    continue
                dist = math.hypot(bc.x - d.x, bc.y - d.y)
                if dist < best_d:
                    best_d = dist
                    closest = d
        if closest is not None:
            self._open_play_tackler = (
                closest.player_id, closest.x, closest.y,
                closest.vx, closest.vy, _speed(closest),
            )
        else:
            self._open_play_tackler = None

    # ───────────────────────────────────────────────────────────────────
    # TACKLE CYCLE START / END
    # ───────────────────────────────────────────────────────────────────

    def _begin_tackle(self, world, ope):
        bc = world.ball_carrier
        ctx = ope.tes_ctx

        bc_pre = self._open_play_bc
        tackler_pre = self._open_play_tackler

        self._tackle_cycle_count += 1
        self._last_release_timer = None

        self._tackle = {
            "tackle_id": self._tackle_cycle_count,
            "start_frame": self.frame,
            "end_frame": None,
            "tackle_number": _f(ctx, "tackle_number", 1),
            "bc_pre_x": bc_pre[0] if bc_pre else _f(bc, "x", None),
            "bc_pre_y": bc_pre[1] if bc_pre else _f(bc, "y", None),
            "bc_pre_speed": bc_pre[4] if bc_pre else _speed(bc) if bc else None,
            "bc_post_x": _f(bc, "x", None),
            "bc_post_y": _f(bc, "y", None),
            "bc_post_speed": _speed(bc) if bc else None,
            "bc_drive_start_x": None,
            "bc_drive_end_x": None,
            "bc_drive_delta_x": None,
            "tackler_pre_id": tackler_pre[0] if tackler_pre else None,
            "tackler_pre_speed": tackler_pre[5] if tackler_pre else None,
            "tackler_post_id": _f(ope.primary, "player_id", None),
            "tackler_post_speed": _speed(ope.primary) if ope.primary else None,
            "drive_distance_m": None,
            "drive_target_delta_x": None,
            "attack_dir": _f(ctx, "team_attack_dir", 1),
            "initial_impact_score": _f(ctx, "initial_impact_score", None),
            "attacker_dominance": None,
            "defender_dominance": None,
            "stability": None,
            "collapsed": False,
            "ruck_x": None,
            "ruck_y": None,
            "ruck_placement_error": None,
            "marker_a_arrival_frame": None,
            "marker_b_arrival_frame": None,
            "ptb_ready": False,
            "freeze_violations": 0,
            "result": None,
            "drive_vector_misaligned": None,
            "backward_drive": None,
            "phase_timer_at_ptb_prep": None,
        }

    def _complete_tackle(self):
        if self._tackle is not None:
            self._tackle["end_frame"] = self.frame
            self._emit_tackle_issues(self._tackle)
            self._tackles.append([
                self._tackle.get(h) for h in self._tackle_headers
            ])
            with open(self._tackles_path, "a", newline="") as f:
                csv.writer(f).writerow(self._tackles[-1])
        self._tackle = None
        self._open_play_bc = None
        self._open_play_tackler = None

    # ───────────────────────────────────────────────────────────────────
    # PER-PHASE ACCUMULATION
    # ───────────────────────────────────────────────────────────────────

    def _accumulate_phase(self, world, ope, phase, stage):
        t = self._tackle
        bc = world.ball_carrier
        ctx = ope.tes_ctx
        outcome = ope.tes_outcome

        if stage == "CONTACT":
            # First TACKLE_INIT frame: record post-contact baseline + tackler id.
            if t["bc_post_x"] is None and bc is not None:
                t["bc_post_x"] = bc.x
                t["bc_post_y"] = bc.y
                t["bc_post_speed"] = _speed(bc)
            if t["tackler_post_id"] is None and ope.primary is not None:
                t["tackler_post_id"] = ope.primary.player_id
                t["tackler_post_speed"] = _speed(ope.primary)
            # Freeze eligibility per contract (count mismatches).
            t["freeze_violations"] += self._count_freeze_violations(world, phase)

        elif stage == "DRIVE":
            if t["bc_drive_start_x"] is None and bc is not None:
                t["bc_drive_start_x"] = bc.x
            if t["bc_drive_end_x"] is None or (bc is not None):
                t["bc_drive_end_x"] = bc.x
            if outcome is not None:
                t["drive_distance_m"] = outcome.drive_distance_m
            if ctx is not None:
                t["drive_target_delta_x"] = _f(ctx, "drive_target_x", None) - _f(ctx, "drive_start_x", 0.0) \
                    if _f(ctx, "drive_target_x", None) is not None else None
                t["attack_dir"] = ctx.team_attack_dir
            t["freeze_violations"] += self._count_freeze_violations(world, phase)

        elif stage == "LOCK":
            if outcome is not None:
                t["attacker_dominance"] = outcome.attacker_dominance
                t["defender_dominance"] = outcome.defender_dominance
                t["result"] = outcome.tackle_result.name if hasattr(outcome.tackle_result, "name") else str(outcome.tackle_result)
            if ctx is not None:
                t["stability"] = ctx.stability
                t["collapsed"] = ctx.collapsed
            t["freeze_violations"] += self._count_freeze_violations(world, phase)

        elif stage == "PTB_PREP":
            world_ruck_x = _f(world, "ruck_x_m", None)
            world_ruck_y = _f(world, "ruck_y_m", None)
            if t["ruck_x"] is None:
                t["ruck_x"] = world_ruck_x
                t["ruck_y"] = world_ruck_y
                if world_ruck_x is not None and t["bc_pre_x"] is not None:
                    t["ruck_placement_error"] = abs(world_ruck_x - t["bc_pre_x"])
            if ctx is not None:
                t["ptb_ready"] = bool(ctx.ptb_ready)
            # Marker arrival delay (frames since RELEASE entry).
            # PTB_PREP timing: track the RELEASE phase timer every RELEASE frame,
            # then snapshot it when the real PTB_PREP phase begins.  (Sampling the
            # timer on the first RELEASE frame would always read 0.0.)
            if phase == "RELEASE":
                self._last_release_timer = ope._phase_timer
            elif phase == "PTB_PREP" and t["phase_timer_at_ptb_prep"] is None:
                t["phase_timer_at_ptb_prep"] = (
                    self._last_release_timer if self._last_release_timer is not None else 0.0
                )
            self._check_marker_arrival(world, t)

    def _count_freeze_violations(self, world, phase):
        rules = MOVEMENT_RULES.get(phase)
        if rules is None:
            return 0
        must_frozen = rules["must_be_frozen"]
        allowed = rules["allowed_to_move"]
        violations = 0
        for p in world.defending_team:
            role = get_role(p)
            if role in must_frozen and not p.frozen:
                violations += 1
            elif role not in allowed and role not in must_frozen and not p.frozen:
                violations += 1
        return violations

    def _check_marker_arrival(self, world, t):
        ruck_x = t["ruck_x"]
        ruck_y = t["ruck_y"]
        if ruck_x is None or ruck_y is None:
            return
        from player.player import DefenderRole
        for d in world.defending_team:
            role = _f(d, "defender_role", None)
            if role == DefenderRole.MARKER_A and t["marker_a_arrival_frame"] is None:
                if math.hypot(d.x - (ruck_x + 1.0), d.y - ruck_y) <= POS_TOLERANCE_M:
                    t["marker_a_arrival_frame"] = self.frame
            elif role == DefenderRole.MARKER_B and t["marker_b_arrival_frame"] is None:
                if math.hypot(d.x - (ruck_x + 2.0), d.y - ruck_y) <= POS_TOLERANCE_M:
                    t["marker_b_arrival_frame"] = self.frame

    # ───────────────────────────────────────────────────────────────────
    # ISSUE EMISSION (Block 6 findings)
    # ───────────────────────────────────────────────────────────────────

    def _emit_tackle_issues(self, t):
        tid = t["tackle_number"]

        # BC backward drive: actual drive delta against attack direction.
        if t["bc_drive_start_x"] is not None and t["bc_drive_end_x"] is not None:
            drive_delta = t["bc_drive_end_x"] - t["bc_drive_start_x"]
            ad = (t["attack_dir"] or 1)
            dot = drive_delta * ad
            if dot < 0:
                self._issue(
                    f"BC_BACKWARD_DRIVE tackle={tid} "
                    f"drive_start_x={t['bc_drive_start_x']:.3f} "
                    f"drive_end_x={t['bc_drive_end_x']:.3f} "
                    f"delta={drive_delta:.3f} attack_dir={ad}"
                )
            t["backward_drive"] = (dot < 0)

            # Drive vector misalignment: computed target vs actual.
            if t["drive_target_delta_x"] is not None:
                target_dot = t["drive_target_delta_x"] * ad
                target_dir = 1 if abs(target_dot) < 1e-6 else (1 if target_dot > 0 else -1)
                actual_dir = 1 if abs(drive_delta) < 1e-6 else (1 if drive_delta * ad > 0 else -1)
                mag_dev = abs(abs(drive_delta) - abs(t["drive_target_delta_x"]))
                if target_dir != actual_dir or mag_dev > DRIVE_MISALIGN_TOLERANCE_M:
                    self._issue(
                        f"DRIVE_VECTOR_MISALIGNMENT tackle={tid} "
                        f"target_delta={t['drive_target_delta_x']:.3f} "
                        f"actual_delta={drive_delta:.3f} "
                        f"outcome_drive_m={t['drive_distance_m']}"
                    )
                    t["drive_vector_misaligned"] = True

        # Fixed initial impact score (1.5 constant) — suspicious.
        if t["initial_impact_score"] == 1.5:
            self._issue(
                f"INCORRECT_TACKLE_SCORE tackle={tid} "
                f"initial_impact_score={t['initial_impact_score']} "
                f"(constant 1.5 — never computed)"
            )

        # Ruck placement error.
        if t["ruck_placement_error"] is not None and t["ruck_placement_error"] > RUCK_PLACEMENT_TOLERANCE_M:
            self._issue(
                f"RUCK_PLACEMENT_ERROR tackle={tid} "
                f"ruck_x={t['ruck_x']:.3f} bc_pre_x={t['bc_pre_x']:.3f} "
                f"error={t['ruck_placement_error']:.3f}m"
            )

        # Marker arrival: zero delay or never arrived before PTB_PREP.
        for label, key in (("A", "marker_a_arrival_frame"), ("B", "marker_b_arrival_frame")):
            val = t[key]
            if val is None:
                self._issue(
                    f"MARKER_ARRIVAL_DELAY tackle={tid} marker_{label} never arrived "
                    f"before end of PTB_PREP (ruck=({t['ruck_x']},{t['ruck_y']}))"
                )
            elif val == t["start_frame"]:
                self._issue(
                    f"MARKER_ARRIVAL_DELAY tackle={tid} marker_{label} "
                    f"arrival_frame={val} == tackle_start (delay=0 frames)"
                )

        # PTB_PREP entered too early.
        if (t["phase_timer_at_ptb_prep"] is not None
                and t["phase_timer_at_ptb_prep"] < RELEASE_MIN_CONVERGENCE_S):
            self._issue(
                f"PTB_PREP_TOO_EARLY tackle={tid} "
                f"release_timer_at_entry={t['phase_timer_at_ptb_prep']:.3f}s "
                f"< min {RELEASE_MIN_CONVERGENCE_S}s"
            )

        # Freeze eligibility violations.
        if t["freeze_violations"] > 0:
            self._issue(
                f"FREEZE_ELIGIBILITY_VIOLATION tackle={tid} "
                f"count={t['freeze_violations']}"
            )

    def _issue(self, text):
        self._issues.append(text)
        with open(self._issues_path, "a") as f:
            f.write(text + "\n")

    # ───────────────────────────────────────────────────────────────────
    # PER-FRAME ROW
    # ───────────────────────────────────────────────────────────────────

    def _write_frame_row(self, world, ope, phase, stage):
        bc = world.ball_carrier
        primary = ope.primary
        row = [
            self.frame, stage, phase,
            round(_f(ope, "_phase_timer", 0.0), 3),
            _f(bc, "player_id", -1),
            round(_f(bc, "x", 0.0), 3), round(_f(bc, "y", 0.0), 3),
            round(_f(bc, "vx", 0.0), 4), round(_f(bc, "vy", 0.0), 4),
            round(_speed(bc), 4), _f(bc, "frozen", False),
            _f(primary, "player_id", -1),
            round(_f(primary, "x", 0.0), 3), round(_f(primary, "y", 0.0), 3),
            round(_f(primary, "vx", 0.0), 4), round(_f(primary, "vy", 0.0), 4),
            round(_speed(primary), 4), _f(primary, "frozen", False),
            round(_f(world, "ruck_x_m", 0.0), 3), round(_f(world, "ruck_y_m", 0.0), 3),
            round(_f(ope.tes_ctx, "stability", 0.0), 3),
            _f(ope.tes_ctx, "collapsed", False),
            _f(ope.tes_ctx, "ptb_ready", False),
        ]
        self._frames.append(row)
        with open(self._frames_path, "a", newline="") as f:
            csv.writer(f).writerow(row)

    # ───────────────────────────────────────────────────────────────────
    # FINALIZE
    # ───────────────────────────────────────────────────────────────────

    def finalize(self):
        if self._tackle is not None:
            self._complete_tackle()
        with open(self._issues_path, "a") as f:
            f.write("\n" + "=" * 80 + "\n")
            f.write(f"SUMMARY — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Frames logged: {self.frame}\n")
            f.write(f"Tackles logged: {len(self._tackles)}\n")
            f.write(f"Issues logged: {len(self._issues)}\n")
            f.write("=" * 80 + "\n")
            if not self._issues:
                f.write("  No issues — clean tackle chain.\n")
        print(f"[TACKLE SDEA] {len(self._tackles)} tackles, {len(self._issues)} issues")
        print(f"[TACKLE SDEA] Frames: {self._frames_path}")
        print(f"[TACKLE SDEA] Tackles: {self._tackles_path}")
        print(f"[TACKLE SDEA] Issues: {self._issues_path}")
