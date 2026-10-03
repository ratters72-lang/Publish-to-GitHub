"""
player/target_stabiliser.py — Geometry-signal stabiliser for AI movement targets.

WHY THIS EXISTS
---------------
Defender / DH targets are the SUM of several independent geometry systems
(SLIDE lanes, SECONDARY depth, width curve, retreat line, threat model,
team spacing, arrival mode).  Each is individually correct, but they write
the SAME target_x/target_y field every frame, so the final target jitters a
few centimetres per frame in random directions.  The solver then faithfully
accelerates toward every tiny change -> visible jiggle / twitching.

This stabiliser is the "merge + smooth" layer:
  1. DEADZONE     — ignore sub-threshold target changes (per-axis).
  2. HYSTERESIS   — only accept a new target when it differs enough from
                   the last accepted position (prevents lane/depth snapping).
  3. ARRIVAL INERTIA — freeze the target once the player is within the
                   stop radius, killing micro-corrections near the target.
  4. SMOOTHING    — exponential lerp toward the accepted target (alpha).

It wraps the EXISTING target generators — it never replaces them — so
TES/PTB physics, movement contracts, freeze authority, and speed caps
are untouched.  The gate that decides WHEN to apply it lives in the
caller (Player.update / DefensiveBehaviourUnified MMDL), not here.
"""

import math

DEFAULT_DEADZONE_M: float = 0.15
DEFAULT_HYSTERESIS_M: float = 0.40
DEFAULT_ARRIVAL_RADIUS_M: float = 0.20
DEFAULT_ALPHA: float = 0.15

# ── GAP-PROPORTIONAL ALPHA (2026-08-16) ───────────────────────────
# The post-PTB role-depth settle (flat ruck+10 -> A/B/C/D role depths)
# can be up to ~10 m.  At the fixed alpha = 0.15 that takes ~1.2 s to
# reach 98% — the visible "98% done -> jittery crawl".  When the
# geometry error is large, alpha ramps to DEFAULT_STABILISER_MAX_ALPHA
# so the settle converges in a few frames; it decays back to
# DEFAULT_STABILISER_ALPHA near the target, preserving the sub-30 cm
# jitter filter.  The MMDL/Player consumers create their TargetStabiliser
# with these values from DefensiveTuners when a live engine is present.
DEFAULT_STABILISER_ALPHA: float = 0.15
DEFAULT_STABILISER_MAX_ALPHA: float = 0.8
DEFAULT_STABILISER_FAST_BAND_M: float = 2.5

# Movement states that represent FAST PURSUIT of a live target (the BC).
# Stabilising a pursuing target would make the chaser track a ghost ~0.3-0.4 m
# behind the real ball carrier — exactly what the engine's existing close
# writers work hard to avoid.  The jiggle this stabiliser kills lives in the
# HOLD / SLIDE / DRIFT / RETREAT geometry-holding states, where targets are
# near-static and oscillate a few cm per frame.
#
# RECOVER (2026-09-21) — HIGH-VELOCITY OWED-GROUND RECOVERY.  The state is
# the explicit opt-OUT of the global line-drift dampeners: a body in RECOVER
# is closing a debt to its own slot at governed defensive speed, so the
# deadzone / hysteresis / alpha-lag chain must not throttle the target it is
# travelling to.  Its target is a COMMANDED slot (anchor or role depth), not
# a live pursuer, so the lag this stabiliser imposes measures out as the very
# "never gets home" stall the state exists to remove.
NON_STABILISED_MOVEMENT_STATES = ("CHASE", "SHOOT", "RECOVER")

# GameSubState VALUE STRINGS that allow stabilisation.  NOTE: the enum is
# inconsistent in this codebase — GameSubState.OPEN_PLAY.value is the
# lowercase "open_play", while GameSubState.POST_RUCK_OPEN_PLAY.value is the
# uppercase "POST_RUCK_OPEN_PLAY".  The gate compares against the ACTUAL
# .value strings, not the member names, so the match is exact.
#
# In every other phase movement is owned by PTBManager (DH), the TES role
# pipeline (arrival_mode), the phase handlers (_move_toward retreat), or the
# freeze authority — all of which either carry their own anti-oscillation
# layer or gate on EXACT raw targets (e.g. the 0.30 m PTB-position gate).
# Putting a hysteresis lag on those raw targets could stall a deterministic
# phase transition.
STABILISED_PHASES = ("open_play", "POST_RUCK_OPEN_PLAY")


def should_stabilise_target(player) -> bool:
    """Single source of truth: may *player*'s targets be geometry-stabilised?

    Returns True only for geometry-driven actors whose movement consumes
    target_x/target_y through a lerp/state accelerator during open play:

      - must be a defender or the dummy half
      - must NOT be frozen (frozen targets are pinned to x/y)
      - must NOT be in arrival mode (PTB-era velocity-proportional path)
      - must NOT be an attached tackler (TES owns their position)
      - must NOT be in TACKLER movement mode (direct-integration pursuit)
      - must NOT be in a fast-pursuit MovementState (CHASE/SHOOT)
      - world must exist and be in an open-play phase

    Used by BOTH Player.update() and the DefensiveBehaviourUnified MMDL so
    the two consume points of target_x/target_y agree on the same gate.
    """
    if getattr(player, "frozen", False):
        return False
    if getattr(player, "arrival_mode", False):
        return False
    if getattr(player, "tes_attached", False):
        return False
    # TASK III (2026-08-14): a COMMITTED tackler's collision target is
    # owned by the tackler-commit writer (target_source="tackler_commit",
    # movement_state=CHASE).  Smoothing it here would turn the live BC-
    # relative collision point into a ghost ~0.3-0.4 m behind the carrier
    # (the "tackler drifts / glide to wrong spot" symptom).  CHASE/SHOOT
    # are already excluded below, but THIS flag is the authoritative gate:
    # it is stamped by _apply_tackler_commit the same frame targets are
    # written, so it wins even if movement_state was not yet CHASE when
    # Player.update() runs (ordering safety).
    if getattr(player, "_tackle_commit_active", False):
        return False
    # DRIVE-END AUTHORITATIVE SLOT (2026-08-19): a defender whose
    # authoritative_until_frame has not yet expired must not be smoothed —
    # the drive-end slot compute wrote the raw target and the stabiliser's
    # deadzone/hysteresis/smoothing would lag it.
    world_ref = getattr(player, "world", None)
    if world_ref is not None:
        _aframe = getattr(player, "authoritative_until_frame", -1)
        _cframe = getattr(world_ref, "frame_count", 0)
        if _aframe >= _cframe:
            return False
    mode = getattr(player, "movement_mode", None)
    if mode is not None:
        # Compare by enum NAME to avoid importing MovementMode here (player.py
        # imports this module, and movement_states.py would create a cycle).
        # Only TACKLER is excluded — it is the direct-integration pursuit
        # contract whose approach is owned by OPE._update_tackler_movement.
        # RETREAT mode is NOT excluded: during open play those targets flow
        # through the geometry pipeline and are exactly the retreat-line
        # jitter population this stabiliser exists to smooth.
        if getattr(mode, "name", None) == "TACKLER":
            return False

    archetype_ok = (
        getattr(player, "is_defender", False)
        or getattr(player, "defender_role", None) is not None
        or getattr(player, "attack_role", "none") == "dummy_half"
    )
    if not archetype_ok:
        return False

    ms = getattr(player, "movement_state", None)
    if ms is not None:
        name = getattr(ms, "name", None) or str(ms)
        if name in NON_STABILISED_MOVEMENT_STATES:
            return False

    world = getattr(player, "world", None)
    if world is None:
        return False
    sub = getattr(world, "sub_state", None)
    phase_name = getattr(sub, "value", sub) if sub is not None else None
    if phase_name is None:
        phase_name = getattr(world, "phase", None)
    return phase_name in STABILISED_PHASES


class TargetStabiliser:
    """Stabilises a pair of AI movement targets against per-frame jitter.

    Stateful per player.  One instance per Player (created lazily in
    Player.__init__).  Memory is cleared by reset() — call it on
    kickoff reset so no stale target from a previous sequence leaks
    into the first OPEN_PLAY frames.
    """

    def __init__(
        self,
        deadzone_m: float = DEFAULT_DEADZONE_M,
        hysteresis_m: float = DEFAULT_HYSTERESIS_M,
        arrival_radius_m: float = DEFAULT_ARRIVAL_RADIUS_M,
        alpha: float = DEFAULT_ALPHA,
        max_alpha: float = DEFAULT_STABILISER_MAX_ALPHA,
        fast_band_m: float = DEFAULT_STABILISER_FAST_BAND_M,
    ):
        # Last stable (smoothed) target — what the accelerator actually
        # receives.
        self.last_tx: float | None = None
        self.last_ty: float | None = None

        # Hysteresis memory — last ACCEPTED raw target.  A raw target
        # must differ by > hysteresis_m from this before it is accepted.
        self.hysteresis_tx: float | None = None
        self.hysteresis_ty: float | None = None

        # tunables (exposed as instance attrs so they can be tuned at
        # runtime without touching every call site)
        self.deadzone_m = deadzone_m
        self.hysteresis_m = hysteresis_m
        self.arrival_radius_m = arrival_radius_m
        self.alpha = alpha
        # GAP-PROPORTIONAL ALPHA (2026-08-16): while |target - position|
        # exceeds fast_band_m the effective smoothing alpha ramps toward
        # max_alpha so large post-PTB role-depth settles converge in a few
        # frames instead of the fixed-alpha "98% crawl"; near the target
        # the base alpha is used, preserving the sub-30 cm jitter filter.
        self.max_alpha = max_alpha
        self.fast_band_m = fast_band_m

    def _effective_alpha(self, dst: float) -> float:
        """Return the smoothing alpha for geometry error *dst* (metres).

        Ramp: base alpha below fast_band_m; linear blend up to max_alpha
        between fast_band_m and 2.0 * fast_band_m (hard cap).
        """
        band = max(0.1, self.fast_band_m)
        if dst <= band:
            return self.alpha
        t = min(1.0, (dst - band) / band)
        return self.alpha + (self.max_alpha - self.alpha) * t

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    def stabilise(self, tx: float, ty: float, px: float, py: float):
        """Return (stable_tx, stable_ty) given the raw target (tx, ty)
        and the player position (px, py).

        Ordering matters:
          1. initialise memory (first call -> trust the raw target)
          2. deadzone (per-axis, against last stable target)
          3. hysteresis (per-axis, against last accepted target)
          4. arrival inertia (distance to player position)
          5. exponential smoothing
          6. update memory
        """
        # -- 1. INITIALISE MEMORY --------------------------------------
        if self.last_tx is None:
            self.last_tx = float(tx)
            self.last_ty = float(ty)
            self.hysteresis_tx = float(tx)
            self.hysteresis_ty = float(ty)
            return float(tx), float(ty)

        # -- 2. DEADZONE (ignore tiny per-frame changes) ---------------
        if abs(tx - self.last_tx) < self.deadzone_m:
            tx = self.last_tx
        if abs(ty - self.last_ty) < self.deadzone_m:
            ty = self.last_ty

        # -- 3. HYSTERESIS (prevent lane/depth snapping) ---------------
        if abs(tx - self.hysteresis_tx) > self.hysteresis_m:
            self.hysteresis_tx = float(tx)
        else:
            tx = self.hysteresis_tx

        if abs(ty - self.hysteresis_ty) > self.hysteresis_m:
            self.hysteresis_ty = float(ty)
        else:
            ty = self.hysteresis_ty

        # -- 4. ARRIVAL INERTIA (stop micro-corrections near target) ---
        dist = math.hypot(tx - px, ty - py)
        if dist < self.arrival_radius_m:
            tx = self.last_tx
            ty = self.last_ty

        # -- 5. SMOOTHING (gap-proportional exponential lerp) ----------
        # Large geometry errors (post-PTB settle) use max_alpha for fast
        # convergence; sub-band errors use the base alpha so the jitter
        # filter is preserved near the target.
        alpha_eff = self._effective_alpha(dist)
        stable_tx = self.last_tx + alpha_eff * (tx - self.last_tx)
        stable_ty = self.last_ty + alpha_eff * (ty - self.last_ty)

        # -- 6. UPDATE MEMORY ------------------------------------------
        self.last_tx = stable_tx
        self.last_ty = stable_ty

        return stable_tx, stable_ty

    # ------------------------------------------------------------------
    # RESET
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all memory.  Call on kickoff reset / player respawn.

        After reset, the next stabilise() call trusts the raw target so a
        new sequence starts immediately with no lerp lag.
        """
        self.last_tx = None
        self.last_ty = None
        self.hysteresis_tx = None
        self.hysteresis_ty = None
