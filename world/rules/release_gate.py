"""world/rules/release_gate.py -- CONVERGENCE-FIRST RELEASE exit authority.

WHY THIS MODULE EXISTS (2026-09-17)
-----------------------------------
RELEASE used to be a pure clock:

    exit  <=>  self._phase_timer >= max(1.54, tes_outcome.ptb_time_s)
               AND OpenPlayEngine._check_dh_ready_for_ptb()

Measured against the live engine (tmp/probe_release_convergence.py, 4
rollouts x 3600 frames, 35 complete tackle cycles):

    RELEASE actually runs                        mean 2.733 s   max 3.183 s
    time until ALL RELEASE-owned movers converge mean 0.690 s   max 1.233 s
    DEAD CLOCK (exit - convergence)              mean 2.043 s   max 2.850 s

i.e. ~75% of RELEASE was spent holding a phase whose work was already
finished.  The DH predicate passed on the first RELEASE frame in most
cycles (mean 0.081 s); the BALL CARRIER was already home in every cycle
(0.000 s); the ex-tackler MARKER pair was the genuine binding term
(31 of 35 cycles, mean 0.686 s, max 1.233 s).

CONTRACT
--------
RELEASE exits the moment every mover RELEASE owns has CONVERGED, not when
a clock expires.  The mover set is explicit and closed:

    BALL_CARRIER  -- recovering to its ruck-relative stand-up target
    DUMMY_HALF    -- at (bc.x - 1.0, ruck_y), stable, unfrozen
    MARKER_A      -- at its ruck-pin target (ruck_x + 1.0, ruck_y)
    MARKER_B      -- at its ruck-pin target (ruck_x + 2.0, ruck_y)

The DEF_RETREAT_10 defensive line is deliberately NOT in this set even
though MOVEMENT_RULES["RELEASE"] lets it move: its retreat to the anchor
(ruck_x + 10) spans the whole PTB era by design and is completed by the
POST_RUCK_OPEN_PLAY handler.  Gating RELEASE on the line would make the
phase LONGER, not shorter.

BOUNDS
------
  RELEASE_MIN_CONVERGENCE_S -- floor so the RELEASE entry work
      (_clear_tes_artefacts / _detach_all_tacklers / first marker target
      write) is never bypassed on a one-frame phase.  Non-binding in
      practice (observed convergence >= 0.333 s).
  RELEASE_MAX_DURATION_S -- hard watchdog.  RELEASE can never outlive
      this, whatever the movers do.  This is the guard that removes the
      historical 6.53 s / 13.72 s outliers (a failing DH gate used to be
      allowed a 300-frame / 5.0 s override countdown after the 1.54 s
      floor).  Observed worst convergence is 1.233 s, so 2.00 s leaves
      0.77 s of headroom while bounding the tail at 2.00 s.

POSITIONING INVARIANCE
----------------------
This module never writes a position, target, velocity or freeze flag --
it is pure read/compute.  It only decides WHEN the already-existing
RELEASE writers stop running.  Because every mover it gates must be
INSIDE its tolerance before the exit fires, the actors that can end up
short are bounded by the tolerances below, and everything downstream
(PTB_PREP / PTB_EXEC) keeps driving the same movers with the same
writers, so the arrival positions are unchanged.
"""

import math

from player.player import DefenderRole

__all__ = [
    "RELEASE_MIN_CONVERGENCE_S",
    "RELEASE_MAX_DURATION_S",
    "MARKER_CONVERGENCE_TOLERANCE_M",
    "BC_RECOVERY_TOLERANCE_M",
    "BC_SETTLED_SPEED_MPS",
    "RELEASE_CONVERGENCE_MOVERS",
    "marker_convergence",
    "bc_recovery",
    "release_convergence",
    "format_convergence_report",
]

# ── Window bounds (seconds) ──────────────────────────────────────────
RELEASE_MIN_CONVERGENCE_S: float = 0.10
RELEASE_MAX_DURATION_S: float = 2.00

# ── Convergence tolerances ───────────────────────────────────────────
# MARKER_CONVERGENCE_TOLERANCE_M: how close an ex-tackler marker must be
# to its ruck-pin target to count as home.  0.25 m is inside the ruck
# bubble (RUCK_BUBBLE_RADIUS_M 3.5) and below the marker Y tolerance the
# PTB era already tolerates.
MARKER_CONVERGENCE_TOLERANCE_M: float = 0.25
# BC_RECOVERY_TOLERANCE_M: the BC's stand-up target is only
# RECOVERY_BACKWARD_DISTANCE_M (0.15 m) from the ruck, so this is a
# formality -- it exists so the gate is explicit rather than implicit.
BC_RECOVERY_TOLERANCE_M: float = 0.20
# BC_SETTLED_SPEED_MPS: stall escape.  enforce_bc_backward_invariant()
# zeroes forward vx, so a BC wedged against the pile can never reach its
# target; a BC that has stopped is settled by definition.
BC_SETTLED_SPEED_MPS: float = 0.30

_MARKER_ROLES = (DefenderRole.MARKER_A, DefenderRole.MARKER_B)

# The closed mover set this authority owns.  Documentation only -- the
# predicate below resolves the live bodies.
RELEASE_CONVERGENCE_MOVERS = (
    "BALL_CARRIER", "DUMMY_HALF", "MARKER_A", "MARKER_B",
)


def marker_convergence(ope) -> tuple[bool, list[tuple[str, float]]]:
    """(converged, [(marker_name, error_m)]).

    A marker with no resolvable target geometry is skipped rather than
    treated as failed -- target geometry is owned by marker_authority and
    a transient miss must not wedge the phase.  When NO marker resolves,
    the term is reported converged (it is simply not a constraint this
    frame); the caller can see the empty error list.
    """
    errors: list[tuple[str, float]] = []
    try:
        markers = list(ope._get_markers())
    except Exception:
        return True, errors
    for body in markers:
        try:
            target = ope._get_marker_target(body)
        except Exception:
            target = None
        if target is None:
            continue
        tx, ty = float(target[0]), float(target[1])
        err = math.hypot(tx - float(body.x), ty - float(body.y))
        role = getattr(body, "defender_role", None)
        name = getattr(role, "name", str(role))
        errors.append((name, err))
    if not errors:
        return True, errors
    return all(err <= MARKER_CONVERGENCE_TOLERANCE_M for _, err in errors), errors


def bc_recovery(world) -> tuple[bool, float | None, float | None]:
    """(recovered, error_m, speed_mps) for the recovering ball carrier.

    ``error_m`` is the distance to the stand-up target written by
    ``_prepare_release``/``_update_release``; ``None`` when the BC (or its
    target) is absent, in which case only the speed test applies.
    """
    bc = getattr(world, "ball_carrier", None)
    if bc is None:
        return True, None, None
    speed = math.hypot(float(getattr(bc, "vx", 0.0)),
                       float(getattr(bc, "vy", 0.0)))
    tx = getattr(bc, "target_x", None)
    ty = getattr(bc, "target_y", None)
    if tx is None or ty is None:
        return speed <= BC_SETTLED_SPEED_MPS, None, speed
    err = math.hypot(float(tx) - float(bc.x), float(ty) - float(bc.y))
    return (err <= BC_RECOVERY_TOLERANCE_M or speed <= BC_SETTLED_SPEED_MPS), err, speed


def release_convergence(ope, world, dh_ready: bool) -> dict:
    """Evaluate the full RELEASE convergence predicate.

    *dh_ready* is the engine's own ``_check_dh_ready_for_ptb()`` verdict,
    passed in rather than recomputed so there is exactly ONE DH predicate
    in the engine (the gate that already guards RELEASE -> PTB_PREP).

    Returns a report dict; ``report["all_converged"]`` is the predicate.
    """
    markers_ok, marker_errors = marker_convergence(ope)
    bc_ok, bc_error, bc_speed = bc_recovery(world)
    return {
        "dh_ready": bool(dh_ready),
        "markers_ready": bool(markers_ok),
        "marker_errors": marker_errors,
        "bc_ready": bool(bc_ok),
        "bc_error": bc_error,
        "bc_speed": bc_speed,
        "all_converged": bool(dh_ready) and bool(markers_ok) and bool(bc_ok),
    }


def format_convergence_report(report: dict) -> str:
    """Compact one-line rendering of a convergence report (logging only)."""
    parts = [
        "dh=%s" % ("ok" if report.get("dh_ready") else "WAIT"),
        "markers=%s" % ("ok" if report.get("markers_ready") else "WAIT"),
        "bc=%s" % ("ok" if report.get("bc_ready") else "WAIT"),
    ]
    errs = report.get("marker_errors") or []
    if errs:
        parts.append(
            "marker_err="
            + ",".join("%s:%.2f" % (name, err) for name, err in errs)
        )
    bc_err = report.get("bc_error")
    if bc_err is not None:
        parts.append("bc_err=%.2f" % bc_err)
    return " ".join(parts)
