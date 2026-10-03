"""glide_budget.py — THE SINGLE GLIDE CEILING + bounded-step primitives.

WHY THIS MODULE EXISTS
----------------------
Five separate defensive authorities enforced a "glide" ceiling of 6.0 m/s,
each declaring its own constant in its own file:

    defence_manager.DEPTH_LOCK_GLIDE_SPEED_MPS        = 6.0
    role_depth_glide.TACKLE_BEGIN_GLIDE_SPEED_MPS     = 6.0
    target_x_glide.TARGET_X_GLIDE_SPEED_MPS           = 6.0
    attach_band.enforce_attach_band(glide_speed_mps=6.0)   (call-site literal)
    ruck_adjacent_pin.RUCK_PIN_GLIDE_SPEED_MPS        = 6.0

Nothing kept them equal, and — more importantly — nothing made the *enforcement*
consistent.  Two of those authorities (``role_depth_glide``, ``attach_band``) wrote
a defender's ``x``/``y`` directly and then rescaled its velocity with

    vscale = CEILING / spd

which is an AMPLIFIER whenever ``spd < CEILING``: a defender backpedalling into
its line at 1.0 m/s that tripped the clamp was silently accelerated to 6.0 m/s in
that frame, and decelerated again when the clamp released.  Both modules also ran
an UNBOUNDED Y clamp (``d.y = band_lo`` / ``band_hi``) AFTER applying the ceiling,
so a body metres outside its slot band was teleported onto the band edge in one
frame — the cap the module docstring claimed to enforce was exceeded by the very
next statement.  The result is the reported "re-targets or corrects too
aggressively, creating visible jumps during defensive retreat".

THE CONTRACT THIS MODULE MAKES POSSIBLE
---------------------------------------
  1. ONE CEILING.  ``GLIDE_SPEED_MPS`` is the single glide speed.  Every glide
     authority imports it; none may re-declare a number.

  2. NEVER AMPLIFY.  A glide may only ever REDUCE a body's speed toward the
     ceiling (``cap_body_velocity``).  A body travelling slower than the ceiling
     is left exactly as it is — the glide never manufactures motion.

  3. ONE PER-FRAME BUDGET PER BODY PER FRAME.  ``frame_budget`` is
     ``ceiling * dt``; ``remaining`` reports how much of that budget is still
     unspent after earlier writers ran; ``bounded_step`` applies a correction
     within the remainder.  A correction therefore CONVERGES over frames instead
     of teleporting, and the total displacement of every glide write in a frame
     stays inside the one ceiling.

  4. REVERSIBLE.  ``glide_smooth_enabled`` reads a tuner and the ``RLM_GLIDE_SMOOTH``
     runtime lever.  Disabled, every caller falls back to its pre-fix byte-identical
     path (the amplifying vscale, the unbounded band snap), so the change is a
     one-flag A/B.

WHAT IT DELIBERATELY IS NOT
---------------------------
Pure arithmetic + one flag read.  It writes nothing, owns no body, holds no
state, and imports only stdlib at module scope — so every defensive module may
import it in any order with no cycle (mirrors ``retreat_locomotion`` and
``tes_depth_obligation``).

The bounded-step primitives are deliberately dumb: every caller passes its OWN
reference point (the frame-start snapshot, exactly as before), so switching a
caller onto this module changes only the SIZE of the correction, never its
target, direction or ownership.
"""

from __future__ import annotations

import math
import os

__all__ = [
    "GLIDE_SPEED_MPS",
    "GLIDE_FALLBACK_DT_S",
    "GLIDE_ENV_LEVER",
    "GLIDE_TUNER_ATTR",
    "glide_dt",
    "glide_smooth_enabled",
    "frame_budget",
    "spent",
    "remaining",
    "bounded_step",
    "cap_body_velocity",
]

# ── THE ONE GLIDE CEILING (m/s) ──────────────────────────────────────
# The per-frame movement cap every glide authority enforces.  Mirrors the
# historical 6.0 m/s shared by all five authorities; declared ONCE here and
# imported everywhere, so the five can never drift apart again.
GLIDE_SPEED_MPS: float = 6.0

# dt fallback when a caller has no frame delta available (60 Hz).
GLIDE_FALLBACK_DT_S: float = 1.0 / 60.0

# ── THE REVERSIBILITY LEVERS ─────────────────────────────────────────
# ``RLM_GLIDE_SMOOTH=0`` (env) or ``tuners.glide_smooth_enabled = False``
# restores the pre-fix behaviour byte-for-byte in every caller.
GLIDE_ENV_LEVER: str = "RLM_GLIDE_SMOOTH"
GLIDE_TUNER_ATTR: str = "glide_smooth_enabled"


def glide_dt(dt) -> float:
    """Return a usable, positive frame delta (never None / zero / negative).

    Falls back to ``GLIDE_FALLBACK_DT_S`` so a stub caller with no dt still
    gets a sane budget rather than a zero-length one (a zero budget would
    silently disable every correction).
    """
    try:
        value = float(dt)
    except (TypeError, ValueError):
        return GLIDE_FALLBACK_DT_S
    if not math.isfinite(value) or value <= 0.0:
        return GLIDE_FALLBACK_DT_S
    return value


def glide_smooth_enabled(tuners=None, world=None) -> bool:
    """True when the bounded (non-amplifying, non-teleporting) glide is armed.

    Precedence, highest first:

      1. ``RLM_GLIDE_SMOOTH=0`` — the runtime lever wins outright.
      2. ``world.glide_smooth_enabled`` — the explicit world override used by
         verifiers and canary runs.
      3. ``tuners.glide_smooth_enabled`` — the tuner (default True).

    When this returns False every caller takes its byte-identical legacy path.
    """
    if os.environ.get(GLIDE_ENV_LEVER, "") == "0":
        return False
    if world is not None:
        override = getattr(world, GLIDE_ENV_LEVER and GLIDE_TUNER_ATTR, None)
        if override is None:
            override = getattr(world, GLIDE_TUNER_ATTR, None)
        if override is not None:
            return bool(override)
    if tuners is None:
        return True
    return bool(getattr(tuners, GLIDE_TUNER_ATTR, True))


def frame_budget(dt, ceiling: float | None = None) -> float:
    """The maximum displacement (metres) one glide may apply this frame.

    ``ceiling * dt``, with the ceiling single-sourced from ``GLIDE_SPEED_MPS``
    unless a caller passes its own.  A non-positive ceiling yields 0.0, which
    callers treat as "no correction" rather than "unlimited correction".
    """
    cap = GLIDE_SPEED_MPS if ceiling is None else float(ceiling)
    if not math.isfinite(cap) or cap <= 0.0:
        return 0.0
    return cap * glide_dt(dt)


def spent(ref_xy, x, y) -> float:
    """Distance already travelled from a frame-start reference point.

    ``ref_xy`` is the caller's ``(ref_x, ref_y)`` — the same frame-start
    snapshot the legacy clamps measured against, so a caller can switch onto
    this budget without changing what it measures from.
    """
    if ref_xy is None:
        return 0.0
    try:
        ref_x = float(ref_xy[0])
        ref_y = float(ref_xy[1])
    except (TypeError, ValueError, IndexError):
        return 0.0
    try:
        return math.hypot(float(x) - ref_x, float(y) - ref_y)
    except (TypeError, ValueError):
        return 0.0


def remaining(ref_xy, x, y, dt, ceiling: float | None = None) -> float:
    """How much of the frame's glide budget is STILL unspent.

    Never negative.  A body that already travelled the whole budget this frame
    gets 0.0 — its correction is deferred to the next frame instead of being
    stacked on top of motion the ceiling has already accounted for.
    """
    return max(0.0, frame_budget(dt, ceiling) - spent(ref_xy, x, y))


def bounded_step(current: float, target: float, budget: float) -> float:
    """Move *current* toward *target* by AT MOST *budget*, never overshooting.

    The convergence primitive: applied once per frame it reaches *target* in
    ``|target - current| / budget`` frames — i.e. it glides.  A non-finite or
    zero budget returns *current* unchanged.
    """
    try:
        cur = float(current)
        tgt = float(target)
        b = abs(float(budget))
    except (TypeError, ValueError):
        return float(current)
    if not math.isfinite(cur) or not math.isfinite(tgt):
        return cur
    if not math.isfinite(b) or b <= 0.0:
        return cur
    delta = tgt - cur
    if abs(delta) <= b:
        return tgt
    return cur + math.copysign(b, delta)


def cap_body_velocity(d, ceiling: float | None = None) -> bool:
    """Throttle *d*'s ``vx``/``vy`` DOWN to *ceiling*.  NEVER up.

    Returns True when the velocity was reduced, False when it was already at or
    below the ceiling (in which case nothing is written at all).

    This is the anti-amplifier: the legacy glide rescaled velocity with
    ``CEILING / spd``, which multiplied a slow body's speed by up to 6x.  A
    pace-reducing glide must only ever remove excess speed, so a body
    backpedalling at 1.0 m/s is left at 1.0 m/s — its movement mode is
    preserved rather than overwritten by the clamp.
    """
    if d is None:
        return False
    cap = GLIDE_SPEED_MPS if ceiling is None else float(ceiling)
    if not math.isfinite(cap) or cap <= 0.0:
        return False
    try:
        vx = float(getattr(d, "vx", 0.0) or 0.0)
        vy = float(getattr(d, "vy", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    spd = math.hypot(vx, vy)
    if spd <= cap or spd <= 0.0:
        return False
    scale = cap / spd
    d.vx = vx * scale
    d.vy = vy * scale
    return True
