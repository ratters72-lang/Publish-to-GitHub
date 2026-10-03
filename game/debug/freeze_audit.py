"""
game/debug/freeze_audit.py — FreezeAndMovementAuditAgent.

Detects freeze-state errors, dual-movement conflicts, and movement-owner
violations across TES → RELEASE → PTB → POST_RUCK → OPEN_PLAY.

Usage:
    audit = FreezeAndMovementAuditAgent()
    audit.run(world, frame, phase, phase_timer)
    # If violations found, call audit.report() to print diagnostics.

Requires each player to have a `movement_writer` attribute (str | None)
cleared at the START of each frame by the engine, then set by each
subsystem that writes movement.

Writers stamped by OpenPlayEngine:
    "Player.update"        — d.update(dt) in _update_defence()
    "AttackController"     — apply_vector() on BC
    "TES_DRIVE"            — direct x = lerp in TACKLE_DRIVE
    "TES_LERP_MARKER"      — direct x = lerp on markers in RELEASE
    "PTBManager"           — PTBManager movement steps
    "retreat_move_toward"  — _move_toward() in POST_RUCK
    "OPE_direct"           — any direct x/vx assignment by OPE phase handlers
"""

import math
from collections import defaultdict
from typing import Any

from player.player import Player, DefenderRole

# -- DH PARKED-AT-RUCK TOLERANCE (2026-09-16) --------------------------------
# The dummy half is expected to stand behind the ruck and wait out the PTB
# clock once it has arrived.  With the tackle-era DH pre-position writer
# (OpenPlayEngine._drive_dh_preposition) the DH usually arrives BEFORE
# RELEASE begins, so that "standing in position" window now falls inside
# RELEASE, where the stuck check has no DH exemption.  A DH within this
# tolerance of its target is treated as IN POSITION (waiting by design);
# a DH stranded further away is still reported as STUCK_NOT_FROZEN.
_DH_PARK_TOLERANCE_M: float = 1.5


# ── Per-player rolling history (tracked across frames) ──────────────

class _PlayerTrace:
    """Rolling window for one player to detect freeze/velocity jiggle."""
    def __init__(self, window: int = 10):
        self.window = window
        self.frozen_history: list[bool] = []
        self.vx_history: list[float] = []
        self.vy_history: list[float] = []
        self.target_x_history: list[float] = []
        self.target_y_history: list[float] = []
        self.x_history: list[float] = []
        self.y_history: list[float] = []
        self.writer_history: list[str | None] = []

    def push(self, frozen: bool, vx: float, vy: float,
             tx: float, ty: float, x: float, y: float,
             writer: str | None) -> None:
        self.frozen_history.append(frozen)
        self.vx_history.append(vx)
        self.vy_history.append(vy)
        self.target_x_history.append(tx)
        self.target_y_history.append(ty)
        self.x_history.append(x)
        self.y_history.append(y)
        self.writer_history.append(writer)
        if len(self.frozen_history) > self.window:
            self.frozen_history.pop(0)
            self.vx_history.pop(0)
            self.vy_history.pop(0)
            self.target_x_history.pop(0)
            self.target_y_history.pop(0)
            self.x_history.pop(0)
            self.y_history.pop(0)
            self.writer_history.pop(0)

    @property
    def frames(self) -> int:
        return len(self.frozen_history)

    def is_vx_zero_while_tx_not_x(self) -> bool:
        """Stationary for > 10 frames while target_x != x (offset > 0.2m).

        Uses TOTAL velocity (hypot of vx AND vy): a defender in a pure
        lateral state (SLIDE/DRIFT) legitimately has vx ≈ 0 while moving
        along Y — checking only vx would flag those movers as stuck on
        every frame ("VX_ZERO_JIGGLE" false positives on the defensive
        line).  The invariant that matters is "zero movement with a live
        target".
        """
        if self.frames < self.window:
            return False
        return all(
            math.hypot(self.vx_history[i], self.vy_history[i]) < 0.01
            and abs(self.target_x_history[i] - self.x_history[i]) > 0.2
            for i in range(-self.window, 0)
        )

    def is_stuck(self) -> bool:
        """Position hasn't moved more than 0.05m in last 10 frames."""
        if self.frames < self.window:
            return False
        x0 = self.x_history[-self.window]
        y0 = self.y_history[-self.window]
        for i in range(-self.window + 1, 0):
            dx = self.x_history[i] - x0
            dy = self.y_history[i] - y0
            if math.hypot(dx, dy) > 0.05:
                return False
        return True

    def dual_writer_in_window(self) -> list[str]:
        """Return writer names written more than once (writer changed = dual write)."""
        unique = set(self.writer_history)
        if len(unique) >= 2 and all(w is not None for w in unique):
            return list(unique)
        return []

    def is_position_overwritten(self) -> bool:
        """
        Detect position-overwrite pattern: vx > 0.01 but x did not move
        (delta < 0.001) in the most recent frame.  Indicates a subsystem
        wrote d.x = anchor_x after Player.update() set velocity.
        Requires at least 2 frames of data.
        """
        if self.frames < 2:
            return False
        return (abs(self.vx_history[-1]) > 0.01
                and abs(self.x_history[-1] - self.x_history[-2]) < 0.001)

    def is_target_suppressed(self) -> bool:
        """
        Detect target-suppression pattern: target_x == x for every frame
        in the window.  Indicates a subsystem wrote d.target_x = d.x,
        cancelling all movement intent.  Requires full window.
        """
        if self.frames < self.window:
            return False
        return all(
            abs(self.target_x_history[i] - self.x_history[i]) < 0.01
            for i in range(-self.window, 0)
        )


# ═══════════════════════════════════════════════════════════════════
# FreezeAndMovementAuditAgent
# ═══════════════════════════════════════════════════════════════════

class FreezeAndMovementAuditAgent:
    """Full audit agent per RL spec: freeze, dual-movement, ownership violations."""

    def __init__(self, report_interval: int = 10, window: int = 10,
                 stuck_threshold: int = 10):
        self.report_interval = report_interval
        self.window = window
        self.last_report_frame = -999
        self._traces: dict[int, _PlayerTrace] = {}
        self._violations: list[str] = []
        self._last_violation_frame = -999
        # ── Soft-freeze stuck counters (frame-based, not history) ──
        self.stuck_counters: dict[int, int] = {}
        self.soft_freeze_counters: dict[int, int] = {}
        self.jiggle_counters: dict[int, int] = {}
        self.stuck_threshold_frames = stuck_threshold
        # ── OPEN_PLAY lock data (set each frame by _check_invariants) ──
        self._open_play_lock_data: dict | None = None
        # ── RELEASE lock data (set each frame by _print_report) ──
        self._release_lock_data: dict | None = None
        # ── Locked-write log (detects subsystems writing frozen outside apply_phase_invariants) ──
        self._frozen_writers_this_frame: set = set()

    # ── Reset for restart/new-match safety ─────────────────────────

    def reset(self) -> None:
        """Clear ALL audit state for restart/new match.

        Resident AI rule: On ANY restart or new match:
          - Clear ALL stuck/soft-freeze/jiggle counters
          - Clear ALL per-player traces
          - Clear ALL recorded violations
          - Clear ALL TES-era state

        If ANY audit state survives restart → corrupted audits, random
        violations, player swapping, crashes.
        """
        self.stuck_counters.clear()
        self.soft_freeze_counters.clear()
        self.jiggle_counters.clear()
        self._traces.clear()
        self._violations.clear()
        self._tes_players_for_run = None
        self._audit_frozen_ids_for_run = None
        self._open_play_lock_data = None
        self._release_lock_data = None

    # ── Collect all players from world ──────────────────────────────

    @staticmethod
    def _all_players(world) -> list[Player]:
        players: list[Player] = []
        if hasattr(world, "attacking_team"):
            players.extend(world.attacking_team)
        if hasattr(world, "defending_team"):
            players.extend(world.defending_team)
        bc = getattr(world, "ball_carrier", None)
        if bc is not None and bc not in players:
            players.append(bc)
        return players

    # ═══════════════════════════════════════════════════════════════
    # SOFT-FREEZE / VELOCITY-SUPPRESSION DETECTION (per-frame count)
    # ═══════════════════════════════════════════════════════════════

    def _is_soft_frozen(self, p: Player) -> bool:
        """Soft freeze = total velocity zero while target_x != x (offset > 0.05).

        Uses TOTAL velocity (hypot of vx AND vy) so pure-lateral movers
        (SLIDE/DRIFT states with vx ≈ 0) never count as soft-frozen —
        they ARE moving.
        """
        if hasattr(p, "target_x"):
            if (math.hypot(getattr(p, "vx", 0.0), getattr(p, "vy", 0.0)) < 0.01
                    and abs(p.target_x - p.x) > 0.05):
                return True
        return False

    def _record_stuck(self, p: Player) -> None:
        """Increment or reset stuck counter based on soft-freeze status."""
        pid = p.player_id
        if self._is_soft_frozen(p):
            self.stuck_counters[pid] = self.stuck_counters.get(pid, 0) + 1
        else:
            self.stuck_counters[pid] = 0

    def _is_stuck(self, p: Player) -> bool:
        """Player has been in soft-freeze for >= stuck_threshold_frames."""
        return self.stuck_counters.get(p.player_id, 0) >= self.stuck_threshold_frames

    # ── Main entry ──────────────────────────────────────────────────

    def run(self, world, frame: int, phase: str, phase_timer: float,
            tes_attackers: set = None, tes_defenders: set = None) -> None:
        """Check all players and detect violations.  Call once per frame.

        TES PARTICIPANT FILTER: During TES phases (TACKLE_INIT/CONTACT/DRIVE/DOMINANCE),
        only players in tes_attackers ∪ tes_defenders are evaluated for TES-specific
        checks (freeze, stuck, vx-zero jiggle). Non-TES players are:
            "NON-TES -- EXCLUDED FROM TES PHYSICS"

        TES FREEZE-MASK CONTRACT: During TACKLE_DRIVE the engine stamps
        ctx["frozen_ids"] with the full player list.  A player in that set
        is treated as FROZEN for AUDIT PURPOSES ONLY: the movement-idle
        checks that require an actual frozen flag (STUCK_NOT_FROZEN,
        STUCK_SOFT_FREEZE, VX_ZERO_JIGGLE) are suppressed for them.
        Nothing is written to player/world objects — real freeze state,
        physics, TES logic, and gameplay are untouched.
        """
        players = self._all_players(world)
        is_tes = phase in ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
        sub_state = getattr(world, "sub_state", None)
        sub_state_str = sub_state.value if hasattr(sub_state, "value") else str(sub_state)
        ruck_x = getattr(world, "ruck_x_m", None)
        bc = getattr(world, "ball_carrier", None)

        # ═══ Read the audit freeze-mask stamped by the engine (DRIVE only) ═══
        audit_frozen_ids = None
        world_ctx = getattr(world, "ctx", None)
        if isinstance(world_ctx, dict):
            raw_frozen_ids = world_ctx.get("frozen_ids")
            if raw_frozen_ids is not None:
                audit_frozen_ids = set(raw_frozen_ids)
        self._audit_frozen_ids_for_run = audit_frozen_ids

        # ═══ Compute TES participant filter BEFORE push/stuck loops ═══
        tes_union = (tes_attackers | tes_defenders) if (is_tes and tes_attackers is not None and tes_defenders is not None) else None
        self._tes_players_for_run = tes_union

        # ── Reset stuck counters when TES ends (prevents TES-era stuck counters
        #    from causing false violations in RELEASE/PTB/OPEN_PLAY) ──
        if tes_union is None:
            self.stuck_counters.clear()

        # ── Push frame data to per-player history ──
        for p in players:
            trace = self._traces.setdefault(p.player_id, _PlayerTrace(self.window))
            writer = getattr(p, "movement_writer", None)
            trace.push(
                frozen=p.frozen,
                vx=p.vx, vy=p.vy,
                tx=p.target_x, ty=p.target_y,
                x=p.x, y=p.y,
                writer=writer,
            )
            # ── Update soft-freeze stuck counters (TES-filtered) ──
            # TES FREEZE-MASK: a masked player is treated as FROZEN for audit
            # purposes, so it must NOT accumulate soft-freeze stuck counts.
            # This prevents DRIVE-era counters from leaking into DOMINANCE/
            # RELEASE (where no mask exists) and firing STUCK_SOFT_FREEZE.
            if (tes_union is None or p in tes_union):
                if audit_frozen_ids is None or p.player_id not in audit_frozen_ids:
                    self._record_stuck(p)

        # ── Check invariants ──────────────────────────────────────
        self._violations = []
        self._check_invariants(players, sub_state_str, phase, ruck_x, bc, frame, world)

        # ── Report every N frames (or immediately on violation) ──
        if (frame - self.last_report_frame >= self.report_interval
                or self._violations):
            self.last_report_frame = frame
            self._print_report(frame, phase, sub_state_str, phase_timer, players)

    # ═══════════════════════════════════════════════════════════════
    # INVARIANT CHECKS
    # ═══════════════════════════════════════════════════════════════

    def _check_invariants(self, players, sub_state: str, phase: str,
                          ruck_x: float | None, bc, frame: int, world=None) -> None:
        """Run all invariant checks and accumulate violations."""

        is_open_play = sub_state == "open_play"
        is_post_ruck = sub_state == "post_ruck_open_play"
        is_tes = phase in ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
        is_release = phase == "RELEASE"
        is_ptb = "PTB" in phase

        # ═══ TES PARTICIPANT FILTER: build lookup set ═══
        tes_players = getattr(self, '_tes_players_for_run', None)
        # ═══ TES FREEZE-MASK: set is None unless the engine stamped
        # ctx["frozen_ids"] this frame (TACKLE_DRIVE quieting contract) ═══
        audit_frozen_ids = getattr(self, '_audit_frozen_ids_for_run', None)

        for p in players:
            pid = p.player_id
            role = getattr(p, "defender_role", None)
            trace = self._traces.get(pid)
            if trace is None:
                continue

            # ═══ TES PARTICIPANT FILTER ═══
            if is_tes and tes_players is not None:
                if p not in tes_players:
                    continue  # NON-TES -- EXCLUDED FROM TES PHYSICS

            # ═══ TES FREEZE-MASK ═══
            # A masked player is treated as FROZEN for AUDIT PURPOSES ONLY:
            # suppress the three movement-idle checks (VX_ZERO_JIGGLE,
            # STUCK_NOT_FROZEN, STUCK_SOFT_FREEZE) that would otherwise
            # fire for idle-but-unfrozen players during TACKLE_DRIVE.
            # Real freeze invariants (UNEXPECTED_FREEZE, markers, etc.)
            # are NOT suppressed — they inspect p.frozen, not the mask.
            is_masked = (audit_frozen_ids is not None
                         and pid in audit_frozen_ids)
            # STAGE 1 MARKER AUDIT QUIETING (2026-09-05): markers re-forming
            # during PTB/RELEASE can be flagged STUCK_NOT_FROZEN even though
            # their target-lock / reform snap is intentionally holding them.
            # Suppress for marker roles in those phases.
            is_marker_reform = (
                role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B)
                and (is_ptb or is_release)
            )
            # DH PARKED AT THE RUCK (2026-09-16): RELEASE and PTB expect the
            # dummy half to move and then STAND behind the ball waiting for
            # the play-the-ball.  Once the DH pre-position writer has walked
            # it in during the tackle, that standing window sits in RELEASE
            # and the stuck check fired for it there -- 2 frames per cycle at
            # the RELEASE entry boundary, where bc.x settles and the 1m-behind
            # target steps ~1 m away from an already-stationary DH.  Suppress
            # the stuck check for a DH that is genuinely IN POSITION; one
            # stranded far from its target is still reported.
            is_dh_parked = (
                role == DefenderRole.DUMMY_HALF
                and (is_release or is_ptb)
                and p.target_x is not None
                and abs(p.target_x - p.x) <= _DH_PARK_TOLERANCE_M
                and (p.target_y is None
                     or abs(p.target_y - p.y) <= _DH_PARK_TOLERANCE_M)
            )

            # ── Freeze checks ────────────────────────────────────

            # Invariant 1: No defender may be frozen after POST_RUCK cleanup
            if (is_open_play
                    and role not in (None, DefenderRole.DUMMY_HALF)
                    and role in (DefenderRole.DEF_RETREAT_10,
                                 DefenderRole.DEF_LINE_READY,
                                 DefenderRole.DEF_CHASE)
                    and p.frozen):
                self._violations.append(
                    f"DEFENDER_FROZEN_OPEN_PLAY id={pid} role={role}"
                )

            # Invariant 2: No marker may remain frozen after cleanup
            if (is_open_play
                    and role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B)
                    and p.frozen):
                self._violations.append(
                    f"MARKER_FROZEN_OPEN_PLAY id={pid} role={role}"
                )

            # Invariant 3: No player frozen outside TES/marker windows
            if (not is_tes and not is_release
                    and not is_ptb
                    and not (phase == "POST_RUCK_OPEN_PLAY")
                    and p.frozen
                    and role not in (DefenderRole.MARKER_A, DefenderRole.MARKER_B)
                    and role not in (DefenderRole.TACKLER_1, DefenderRole.TACKLER_2,
                                     DefenderRole.TACKLER_3)):
                self._violations.append(
                    f"UNEXPECTED_FREEZE id={pid} phase={phase} sub={sub_state} role={role}"
                )

            # ── Velocity zero jiggle ──────────────────────────────
            # TES FREEZE-MASK: masked players are audit-frozen, so vx-zero
            # while target!=x is expected during DRIVE — not a violation.
            if is_tes and tes_players is not None:
                if p in tes_players:
                    if not is_masked and trace.is_vx_zero_while_tx_not_x():
                        self._violations.append(
                            f"VX_ZERO_JIGGLE id={pid} vx={p.vx:.3f} tx={p.target_x:.2f} x={p.x:.2f}"
                        )
            elif not is_masked and trace.is_vx_zero_while_tx_not_x():
                self._violations.append(
                    f"VX_ZERO_JIGGLE id={pid} vx={p.vx:.3f} tx={p.target_x:.2f} x={p.x:.2f}"
                )

            # ── Stuck detection ──────────────────────────────────
            # STUCK_NOT_FROZEN only fires when defender is NOT at their SDM target.
            # If x == target_x (within epsilon), they are "at target", not stuck.
            #
            # POST-RUCK SETTLING: for the first ~0.5s after a PTB
            # (frames_since_ptb < 30) plus the POST_RUCK_OPEN_PLAY /
            # PTB sub-states everyone is re-forming — defenders squat at
            # the retreat line / step into the new line while targets
            # plot new lanes.  A stationary stance there is planned
            # re-formation, not a stuck bug, so skip the trace-based
            # stuck check just like TARGET_JIGGLE.
            #
            at_target = abs(p.target_x - p.x) < 0.05
            # STAGE 3F: grace window mirrors the engine's dt-based quiet
            # window (0.5s of simulated time); falls back to the legacy
            # frame comparison when the time field is absent.
            _quiet_s = 0.5
            frames_since_ptb = getattr(world, "frames_since_ptb", None)
            seconds_since_ptb = getattr(
                world,
                "seconds_since_ptb",
                None if frames_since_ptb is None else frames_since_ptb / 60.0,
            )
            in_stuck_grace = (is_post_ruck or is_ptb
                              or (seconds_since_ptb is not None
                                  and seconds_since_ptb < _quiet_s))
            if in_stuck_grace:
                pass  # post-ruck re-formation is expected standing
            elif is_tes and tes_players is not None:
                if p in tes_players:
                    if (not is_masked and not is_marker_reform
                            and trace.is_stuck() and not p.frozen
                            and trace.frames >= self.window and not at_target):
                        self._violations.append(
                            f"STUCK_NOT_FROZEN id={pid} x={p.x:.2f} y={p.y:.2f}"
                        )
            elif (not is_masked and not is_marker_reform
                  and not is_dh_parked
                  and trace.is_stuck() and not p.frozen
                  and trace.frames >= self.window and not at_target):
                self._violations.append(
                    f"STUCK_NOT_FROZEN id={pid} x={p.x:.2f} y={p.y:.2f}"
                    f" [phase={phase} role={role}"
                    f" tx={p.target_x:.2f} ty={p.target_y:.2f}"
                    f" vx={p.vx:.3f} bc={getattr(bc, 'player_id', None)}]"
                )

            # ── Dual writer conflict (history-based) ──────────────
            writers = trace.dual_writer_in_window()
            if writers:
                self._violations.append(
                    f"DUAL_WRITER id={pid} writers={writers}"
                )

            # ── Target jiggle (target_x bouncing) ─────────────────
            # TES FREEZE-MASK: masked players are audit-frozen for the
            # whole TES phase.  Their 10-frame target history spans the
            # OPEN_PLAY->TES boundary (pre-tackle retreat targets ~10m
            # apart mixed with TES-pinned targets), which would fire a
            # static TARGET_JIGGLE for every defender on the trigger
            # frame even though nothing is thrashing.  Suppress masked
            # players exactly like VX_ZERO_JIGGLE / STUCK_NOT_FROZEN /
            # STUCK_SOFT_FREEZE — the mask is the audit-wide quieting
            # contract for TES phases.
            if not is_masked and trace.frames >= self.window:
                #
                # POST-RUCK RETARGET QUIETING:
                # Early OPEN_PLAY after a PTB is legitimately
                # re-targeting, not thrash:
                #   1. Moving players (|vx| >= 0.1): attackers running
                #      to new support lines / defenders retreating to
                #      depth produce large target spreads BY DESIGN.
                #      Retargeting while running is a planned move, not
                #      jiggle - skip.
                #   2. PTB / POST_RUCK_OPEN_PLAY sub-states, plus the
                #      same 30-frame post-PTB grace window used by the
                #      reactivity audit (~0.5s @ 60fps): both teams are
                #      re-forming, so even stationary players
                #      legitimately pick up new depth targets (the
                #      208/209 late-depth-switch flags).
                # The real signal - stationary players thrashing
                # targets outside the grace window - still fires.
                #
                in_post_ruck_grace = is_post_ruck or is_ptb
                # STAGE 3F: seconds-based grace, mirroring the engine's
                # dt-based quiet window; frame fallback for legacy fixtures.
                _frames_since_ptb = getattr(world, "frames_since_ptb", None)
                _seconds_since_ptb = getattr(
                    world,
                    "seconds_since_ptb",
                    None if _frames_since_ptb is None
                    else _frames_since_ptb / 60.0,
                )
                if (_seconds_since_ptb is not None
                        and _seconds_since_ptb < 0.5):
                    in_post_ruck_grace = True
                # "Moving" = any non-zero TOTAL velocity.  Pure-lateral
                # movers (SLIDE/DRIFT states) have vx ≈ 0 by design; they
                # must count as moving or every post-ruck line re-formation
                # would false-positive as TARGET_JIGGLE.
                moving = math.hypot(trace.vx_history[-1],
                                    trace.vy_history[-1]) >= 0.1
                if not in_post_ruck_grace and not moving:
                    tx_vals = trace.target_x_history[-self.window:]
                    tx_range = max(tx_vals) - min(tx_vals)
                    if tx_range > 5.0:
                        self._violations.append(
                            f"TARGET_JIGGLE id={pid} tx_range={tx_range:.2f}"
                        )

        # ── Soft-freeze stuck detection (counter-based) ─────────────
        for p in players:
            pid = p.player_id
            # ═══ TES PARTICIPANT FILTER for stuck counters ═══
            if is_tes and tes_players is not None:
                if p not in tes_players:
                    continue  # NON-TES -- EXCLUDED
            # ═══ TES FREEZE-MASK: masked players are audit-frozen ═══
            if audit_frozen_ids is not None and pid in audit_frozen_ids:
                continue
            if self._is_stuck(p) and not p.frozen:
                self._violations.append(
                    f"STUCK_SOFT_FREEZE id={pid} "
                    f"counter={self.stuck_counters.get(pid, 0)}/"
                    f"{self.stuck_threshold_frames} vx={p.vx:.3f} "
                    f"tx={p.target_x:.2f} x={p.x:.2f}"
                )

            # ── Dual writer list check (same-frame conflict) ─────
            mw = getattr(p, "movement_writer", None)
            if isinstance(mw, list) and len(mw) > 1:
                self._violations.append(
                    f"DUAL_WRITER_LIST id={pid} writers={mw}"
                )

        # ── Phase-level invariants ──────────────────────────────────

        # DefenceAI must be OFF during POST_RUCK
        if is_post_ruck:
            for p in players:
                writer = getattr(p, "movement_writer", None)
                if writer and "DefenceAI" in writer:
                    self._violations.append(
                        f"DEFENCEAI_IN_POST_RUCK id={p.player_id} writer={writer}"
                    )

        # RetreatAI must be OFF during OPEN_PLAY
        if is_open_play:
            for p in players:
                writer = getattr(p, "movement_writer", None)
                if writer and "retreat" in writer.lower():
                    self._violations.append(
                        f"RETREAT_IN_OPEN_PLAY id={p.player_id} writer={writer}"
                    )

        # No direct x overwrite during OPEN_PLAY (only Player.update)
        if is_open_play:
            for p in players:
                writer = getattr(p, "movement_writer", None)
                if writer and writer == "OPE_direct":
                    self._violations.append(
                        f"DIRECT_X_WRITE_OPEN_PLAY id={p.player_id} writer={writer}"
                    )

        # ══════════════════════════════════════════════════════════
        # GLOBAL MOVEMENT LOCK — RELEASE phase
        # ══════════════════════════════════════════════════════════
        if is_release:
            total = len(players)
            stuck_count = sum(1 for p in players if self._is_stuck(p))
            if total > 0 and stuck_count >= int(total * 0.8):
                self._violations.append(
                    f"GLOBAL_MOVEMENT_LOCK stuck={stuck_count}/{total} "
                    f"phase={phase} sub={sub_state}"
                )

        # ══════════════════════════════════════════════════════════
        # DEFENCE GATE VIOLATION — _update_defence() running outside
        # OPEN_PLAY
        # ══════════════════════════════════════════════════════════
        gate = self._detect_defence_gate_violation(phase, sub_state, players, world)
        if gate["violation"]:
            self._violations.append(
                f"DEFENCE_GATE_VIOLATION phase={gate['phase']} sub={gate['sub']} "
                f"players={gate['players']}"
            )

    # ═══════════════════════════════════════════════════════════════
    # OPEN_PLAY GLOBAL MOVEMENT LOCK DETECTOR
    # ═══════════════════════════════════════════════════════════════

    def _detect_open_play_lock(self, world, defenders, attackers):
        """
        Detect OPEN_PLAY global movement lock:
          - stuck counters
          - position overwrites (vx > 0 but x not changing)
          - target suppression (target_x == x)
          - dual movement writers
        """

        stuck = []
        pos_overwrite = []
        target_suppressed = []
        writer_conflicts = []

        for p in defenders + attackers:
            pid = p.player_id

            # Stuck counter (soft-freeze counter)
            if self._is_stuck(p):
                stuck.append(pid)

            # Position overwrite: velocity present but x not changing
            if abs(p.vx) > 0.01 and abs(p.prev_x - p.x) < 0.001:
                pos_overwrite.append(pid)

            # Target suppression: target_x == x cancels movement intent
            if hasattr(p, "target_x") and abs(p.target_x - p.x) < 0.01:
                target_suppressed.append(pid)

            # Dual movement writers (same-frame)
            w = getattr(p, "movement_writer", None)
            if isinstance(w, list) and len(w) > 1:
                writer_conflicts.append((pid, w))

        # Global lock = >60% stuck
        total = len(defenders) + len(attackers)
        global_lock = False
        if total > 0 and len(stuck) >= int(total * 0.6):
            global_lock = True

        return {
            "global_lock": global_lock,
            "stuck": stuck,
            "pos_overwrite": pos_overwrite,
            "target_suppressed": target_suppressed,
            "writer_conflicts": writer_conflicts,
        }

    # ═══════════════════════════════════════════════════════════════
    # DEFENCE GATE VIOLATION DETECTOR
    # ═══════════════════════════════════════════════════════════════

    def _detect_defence_gate_violation(self, phase: str, sub_state: str, players, world=None):
        """
        Defence MUST NOT run outside OPEN_PLAY.
        Uses the positive signal ``world.defence_ran_this_frame`` to decide
        whether a violation actually occurred.

        If phase != OPEN_PLAY but defence_ran_this_frame is False, there
        is no gate violation — defence simply wasn't called this frame
        (e.g. transition frame where the phase handler already advanced
        past OPEN_PLAY before the audit runs).
        """
        phase_str = phase.value if hasattr(phase, "value") else phase
        if phase_str == "OPEN_PLAY":
            return {"violation": False}

        # Positive signal: did _update_defence actually run this frame?
        if world is not None:
            if not getattr(world, "defence_ran_this_frame", False):
                return {"violation": False}

        # Phase is not OPEN_PLAY AND defence_ran_this_frame is True → real violation
        return {
            "violation": True,
            "phase": phase,
            "sub": sub_state,
            "players": len(players),
        }

    # ═══════════════════════════════════════════════════════════════
    # RELEASE GLOBAL MOVEMENT LOCK DETECTOR
    # ═══════════════════════════════════════════════════════════════

    def _detect_release_lock(self, players):
        """
        Detect RELEASE-phase global movement lock:
          - stuck counters (soft-freeze)
          - position overwrites (vx > 0 but x not changing)
          - target suppression (target_x == x)
          - dual movement writers

        Identical in structure to _detect_open_play_lock() but with an
        80% stuck threshold (harder to trigger) and operates on the full
        player list — in RELEASE, attackers AND defenders can be locked.
        """
        stuck = []
        pos_overwrite = []
        target_suppressed = []
        writer_conflicts = []

        for p in players:
            pid = p.player_id

            # Stuck counter (soft-freeze counter)
            if self._is_stuck(p):
                stuck.append(pid)

            # Position overwrite: velocity present but x not changing
            if abs(p.vx) > 0.01 and abs(p.prev_x - p.x) < 0.001:
                pos_overwrite.append(pid)

            # Target suppression: target_x == x cancels movement intent
            if hasattr(p, "target_x") and abs(p.target_x - p.x) < 0.01:
                target_suppressed.append(pid)

            # Dual movement writers (same-frame)
            w = getattr(p, "movement_writer", None)
            if isinstance(w, list) and len(w) > 1:
                writer_conflicts.append((pid, w))

        # Global lock = >80% stuck (RELEASE is stricter than OPEN_PLAY's 60%)
        total = len(players)
        global_lock = False
        if total > 0 and len(stuck) >= int(total * 0.8):
            global_lock = True

        return {
            "global_lock": global_lock,
            "stuck": stuck,
            "pos_overwrite": pos_overwrite,
            "target_suppressed": target_suppressed,
            "writer_conflicts": writer_conflicts,
        }

    # ═══════════════════════════════════════════════════════════════
    # REPORTING
    # ═══════════════════════════════════════════════════════════════

    def _print_report(self, frame: int, phase: str, sub_state: str,
                      phase_timer: float, players: list[Player]) -> None:
        """Print diagnostic report for this frame."""

        # ── Aggregated counts ──
        frozen_markers = []
        frozen_defenders = []
        frozen_attackers = []
        stuck_players = []
        soft_freeze_stuck_players = []
        dual_writers = []
        jiggling = []
        last_writers: dict[int, str] = {}
        has_global_lock = False

        tes_players = getattr(self, '_tes_players_for_run', None)
        is_tes = phase in ("TACKLE_INIT", "TACKLE_CONTACT", "TACKLE_DRIVE", "TACKLE_DOMINANCE")
        # TES FREEZE-MASK: set is None unless the engine stamped
        # ctx["frozen_ids"] this frame (TACKLE_DRIVE quieting contract).
        audit_frozen_ids = getattr(self, '_audit_frozen_ids_for_run', None)

        for p in players:
            pid = p.player_id
            trace = self._traces.get(pid)
            role = getattr(p, "defender_role", None)
            writer = getattr(p, "movement_writer", None)
            if writer:
                last_writers[pid] = writer

            # ═══ TES PARTICIPANT FILTER for reporting stats ═══
            is_excluded = is_tes and tes_players is not None and p not in tes_players
            # ═══ TES FREEZE-MASK for reporting stats ═══
            is_mask_quieted = (audit_frozen_ids is not None
                               and pid in audit_frozen_ids)

            if p.frozen:
                if role in (DefenderRole.MARKER_A, DefenderRole.MARKER_B):
                    frozen_markers.append(pid)
                elif role in (DefenderRole.DEF_RETREAT_10,
                              DefenderRole.DEF_LINE_READY,
                              DefenderRole.DEF_CHASE,
                              DefenderRole.TACKLER_1,
                              DefenderRole.TACKLER_2,
                              DefenderRole.TACKLER_3):
                    frozen_defenders.append(pid)
                else:
                    frozen_attackers.append(pid)

            if trace:
                # DH PARKED AT THE RUCK (2026-09-16): mirror the violation
                # suppression in _check_invariants so the report header does
                # not list a DH that is legitimately standing in position
                # waiting for the play-the-ball (see _DH_PARK_TOLERANCE_M).
                _dh_parked_report = (
                    role == DefenderRole.DUMMY_HALF
                    and phase in ("RELEASE", "PTB_PREP", "PTB_EXEC", "PTB")
                    and getattr(p, "target_x", None) is not None
                    and abs(p.target_x - p.x) <= _DH_PARK_TOLERANCE_M
                    and (getattr(p, "target_y", None) is None
                         or abs(p.target_y - p.y) <= _DH_PARK_TOLERANCE_M)
                )
                if (not is_excluded and not is_mask_quieted
                        and not _dh_parked_report
                        and trace.is_stuck() and not p.frozen):
                    stuck_players.append(pid)
                writers = trace.dual_writer_in_window()
                if writers:
                    dual_writers.append((pid, writers))
                if (not is_excluded and not is_mask_quieted
                        and trace.is_vx_zero_while_tx_not_x()):
                    jiggling.append(pid)

            # ── Soft-freeze stuck (from counter) ──
            if (not is_excluded and not is_mask_quieted
                    and self._is_stuck(p) and not p.frozen):
                soft_freeze_stuck_players.append(pid)

        # ── Check if any global lock / gate violation is present ──
        has_open_play_lock = False
        has_defence_gate_violation = False
        for v in self._violations:
            if v.startswith("GLOBAL_MOVEMENT_LOCK"):
                has_global_lock = True
            if v.startswith("DEFENCE_GATE_VIOLATION"):
                has_defence_gate_violation = True

        # ── Detect OPEN_PLAY global movement lock (report-only) ──
        if sub_state == "open_play" or phase == "OPEN_PLAY":
            defenders = [p for p in players
                         if getattr(p, "defender_role", None) is not None
                         and getattr(p, "defender_role", None)
                         not in (DefenderRole.DUMMY_HALF, None)]
            attackers = [p for p in players if p not in defenders]
            opl = self._detect_open_play_lock(None, defenders, attackers)
            self._open_play_lock_data = opl
            if opl["global_lock"]:
                has_open_play_lock = True

        # ── Detect RELEASE global movement lock (report-only) ──
        if phase == "RELEASE":
            rl = self._detect_release_lock(players)
            self._release_lock_data = rl

        # ── Build report header ──
        print(f"\n{'='*60}")
        print(f"[FREEZE_AUDIT @ frame {frame}] phase={phase} sub={sub_state} timer={phase_timer:.2f}")
        print(f"  Frozen -> markers: {frozen_markers}  defenders: {frozen_defenders}  attackers: {frozen_attackers}")
        print(f"  Stuck (not frozen): {stuck_players}")
        print(f"  Soft-freeze stuck (vx=0 while target!=x): {soft_freeze_stuck_players}")
        print(f"  VX-zero jiggle: {jiggling}")
        if dual_writers:
            for pid, w in dual_writers:
                print(f"  DUAL_WRITER id={pid} writers={w}")
        if last_writers:
            print(f"  Movement writers (last): {last_writers}")

        # ── Violations ──
        if self._violations:
            print(f"  ⚠ VIOLATIONS ({len(self._violations)}):")
            for v in self._violations:
                print(f"    {v}")
            if has_global_lock:
                print("  ⚠ GLOBAL MOVEMENT LOCK DETECTED — RELEASE is suppressing ALL movement")
                print("  ⚠ This means _update_defence() is overriding movement during RELEASE")
                print("  ⚠ Defence must NOT run during RELEASE/PTB/PTB_PREP/PTB_EXEC/POST_RUCK")
            if has_open_play_lock and self._open_play_lock_data is not None:
                opl = self._open_play_lock_data
                print("  ⚠ OPEN_PLAY GLOBAL MOVEMENT LOCK DETECTED")
                print("  ⚠ Defence systems are overriding movement every frame")
                print(f"  ⚠ Players stuck: {opl['stuck']}")
                print(f"  ⚠ Position overwrites: {opl['pos_overwrite']}")
                print(f"  ⚠ Target suppression: {opl['target_suppressed']}")
                print(f"  ⚠ Writer conflicts: {opl['writer_conflicts']}")
                print("  ⚠ FIX: Gate _update_defence(dt) to OPEN_PLAY only")
            if phase == "RELEASE" and self._release_lock_data is not None:
                rl = self._release_lock_data
                if rl["global_lock"]:
                    print("  ⚠ RELEASE GLOBAL MOVEMENT LOCK DETECTED")
                    print("  ⚠ Defence systems are overriding movement during RELEASE")
                    print(f"  ⚠ Players stuck: {rl['stuck']}")
                    print(f"  ⚠ Position overwrites: {rl['pos_overwrite']}")
                    print(f"  ⚠ Target suppression: {rl['target_suppressed']}")
                    print(f"  ⚠ Writer conflicts: {rl['writer_conflicts']}")
                    print("  ⚠ FIX: _update_defence(dt) MUST NOT RUN during RELEASE")
            if has_defence_gate_violation:
                print("  ⚠ DEFENCE GATE VIOLATION DETECTED")
                print(f"  ⚠ Defence is running during {phase} (sub={sub_state})")
                print("  ⚠ This WILL cause:")
                print("     - stuck-not-frozen")
                print("     - pos-overwrite")
                print("     - target suppression")
                print("     - DH crawl")
                print("     - PTB gate blocking")
                print("     - global movement lock")
                print("  ⚠ FIX: _update_defence(dt) MUST ONLY RUN IN OPEN_PLAY")
        else:
            print(f"  ✅ No violations")
        print(f"{'='*60}\n")

    # ═══════════════════════════════════════════════════════════════
    # DETAILED PLAYER DUMP (call on serious violation)
    # ═══════════════════════════════════════════════════════════════

    def dump_player(self, player: Player) -> None:
        """Print detailed diagnostic for one player."""
        trace = self._traces.get(player.player_id)
        print(f"  Player #{player.player_id}:")
        print(f"    x={player.x:.3f}  y={player.y:.3f}")
        print(f"    vx={player.vx:.3f}  vy={player.vy:.3f}")
        print(f"    target_x={player.target_x:.3f}  target_y={player.target_y:.3f}")
        print(f"    frozen={player.frozen}  freeze_timer={player.freeze_timer:.3f}")
        print(f"    role={getattr(player, 'defender_role', None)}")
        print(f"    movement_writer={getattr(player, 'movement_writer', None)}")
        if trace:
            print(f"    trace frames={trace.frames}")
            print(f"    vx_zero_jiggle={trace.is_vx_zero_while_tx_not_x()}")
            print(f"    stuck={trace.is_stuck()}")
            print(f"    dual_writers={trace.dual_writer_in_window()}")
