"""
spacing_authority.py — Spacing Chain Stabilisation (Task H, 2026-08-11).

PURPOSE
-------
Make spacing a pure lateral (Y-only) authority subsystem.  This module
wraps the split-design spacing chain (defensive_split_spacing.compute_targets)
with the Task H authority contracts:

  S0 — Y-only output: spacing NEVER outputs ``depth`` / ``target_x`` /
       ``anchor_x`` or any X-coordinate override.  It may only produce
       Y targets (``spacing_target_y`` / ``lateral_offset_y`` /
       ``width_signature_y`` / ``inter_defender_gap_y``).
  S1 — Anchor consumer: spacing reads ``defensive_line_anchor_m(world)``
       (the Task G anchor authority = ruck_x + 10) but NEVER recomputes /
       shifts / clamps / nudges / derives the anchor from BC/DH.
  S3 — Chase-target integrity: spacing NEVER touches ``chase_target_x`` /
       ``chase_target_y`` / ``mmdl_target_x`` / ``mmdl_target_y``.  It
       never "corrects" chase logic and never runs after chase.
  S4 — TES/PTB integrity: the gated entry (``run_spacing`` /
       ``assert_open_play``) refuses to run outside OPEN_PLAY, so spacing
       can never override TES chase/close targets, PTB retreat/marker/DH
       targets, or POST_RUCK writers.
  S5 — Stable depth roles: spacing consumes ``d.depth_role`` /
       ``d.depth_side`` (persistent Task A structural roles).  It never
       reclassifies / reassigns / overrides depth or flips side.
  S6 — Stable width signature: spacing is a pure function of (role, side,
       ruck_y, governed width) + deterministic clamps — identical inputs
       always yield identical outputs (verified over 200 frames).
  S7 — Role exemptions: the authority write applies only to PASSIVE /
       line-holding defenders.  PRIMARY/SECONDARY closers, markers,
       dummy-half, TES tacklers, DEF_CHASE and the dynamic fullback are
       exempt (their own writers own their targets).

ARCHITECTURE NOTE (decision 2)
------------------------------
``run_spacing`` is the OPEN_PLAY-gated authority entry.  Pure read-only
consumers of ``spacing_y`` inside TES/PTB/RELEASE retreat writers are NOT
routed here (they hold line shape by contract — Task E/F) and remain
unchanged.  The enforcement split:

  - Authority write gate  -> this module (OPEN_PLAY only)
  - Read-only Y consumers -> defensive_role_movement / lvl1 / lvl2 / DBU

USAGE in the structured OPEN_PLAY pipeline (step 6):
    split_targets = _split_targets(world, sorted_defs, self.tuners,
                                   governor=self.line_governor)
    SpacingGuard(world, sorted_defs).verify(split_targets)

``verify`` is a read-back assertion: ``compute_targets`` is a pure
function (it never writes defender attributes), so a violation raised
here means a future edit corrupted the chain — the spec's in-chain
enforcement point.
"""

from __future__ import annotations

from player.player import Player
from game.defence.defence_lvl_1.anchor_authority import (
    defensive_line_anchor_m,
    SpacingDepthViolation,
)

# Re-exported for convenience so Task H call sites import one module.
__all__ = [
    "SpacingDepthViolation",
    "SpacingOpenPlayGateViolation",
    "SpacingGuard",
    "SpacingAuthority",
    "assert_open_play",
    "is_open_play",
    "is_spacing_eligible",
    "run_spacing",
]


# ─────────────────────────────────────────────────────────────────────
# EXCEPTIONS
# ─────────────────────────────────────────────────────────────────────

class SpacingOpenPlayGateViolation(SpacingDepthViolation):
    """Raised when ``run_spacing`` is invoked outside the OPEN_PLAY sub-state.

    Spacing runs ONLY during OPEN_PLAY.  It must NEVER run during TES,
    PTB, POST_RUCK (or any sub-state whose phase handlers own targets).
    """


# ─────────────────────────────────────────────────────────────────────
# S4 — OPEN_PLAY GATE
# ─────────────────────────────────────────────────────────────────────

def is_open_play(world) -> bool:
    """Return True when *world* is in the OPEN_PLAY sub-state.

    Accepts the ``GameSubState`` enum, its value string (``"open_play"``),
    and the uppercase spellings used by verifier stubs (``"OPEN_PLAY"``).
    """
    sub = getattr(world, "sub_state", None)
    if sub is None:
        return False
    if hasattr(sub, "value"):
        sub = sub.value
    return str(sub).lower() == "open_play"


def assert_open_play(world) -> None:
    """S4 gate: spacing must run ONLY during OPEN_PLAY."""
    if not is_open_play(world):
        raise SpacingOpenPlayGateViolation(
            "spacing must never run outside OPEN_PLAY "
            f"(sub_state={getattr(world, 'sub_state', None)!r})"
        )


# ─────────────────────────────────────────────────────────────────────
# S7 — ROLE EXEMPTIONS
# ─────────────────────────────────────────────────────────────────────

# Roles that play tight to the ruck/set or are engaged in the tackle —
# excluded from the spacing authority write by contract.
_EXEMPT_DEFENDER_ROLES = frozenset({
    "MARKER_A",
    "MARKER_B",
    "DUMMY_HALF",
    "TACKLER_1",
    "TACKLER_2",
    "TACKLER_3",
    "DEF_CHASE",
})

# Depth classes that legally engage ahead of the line (structured-close
# / chase writers own their targets).  They CONSUME spacing Y through
# their writers (Task E/F) but never receive an authority write.
_EXEMPT_DEPTH_CLASSES = frozenset({"PRIMARY", "SECONDARY"})


def is_spacing_eligible(d) -> bool:
    """Return True when *d* is eligible for the spacing authority write.

    Spacing applies ONLY to PASSIVE / line-holding defenders.  Exempt:
      - Markers / dummy-half / TES tacklers / DEF_CHASE (role-based)
      - The dynamic fullback actor (backfield coverage, own anchor)
      - PRIMARY/SECONDARY closers (close/chase own their targets)
      - Frozen defenders (their targets are pinned by the freeze owner)

    NOTE: eligibility gates the *write* contract only.  The spacing
    output dict (``compute_targets``) is still returned for every
    defender so structured-close and step-7 consumers can read the Y
    target (Task E/F single geometry target generator).
    """
    if d is None:
        return False
    if not isinstance(d, Player):
        return False
    role = getattr(d, "defender_role", None)
    role_name = getattr(role, "name", role)
    if role_name in _EXEMPT_DEFENDER_ROLES:
        return False
    if getattr(d, "fullback_role_active", False):
        return False
    depth_class = getattr(d, "depth_class", "PASSIVE")
    if depth_class in _EXEMPT_DEPTH_CLASSES:
        return False
    if getattr(d, "frozen", False) and getattr(d, "freeze_timer", 0.0) > 0.0:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────
# S0/S1/S3 — SPACING GUARD (read-back authority assertions)
# ─────────────────────────────────────────────────────────────────────

# X-family fields the spacing chain is forbidden from mutating (S0/S3).
_PROTECTED_X_FIELDS = (
    "target_x",
    "role_depth_target_x",
    "chase_target_x",
    "chase_target_y",
    "mmdl_target_x",
    "mmdl_target_y",
)

# Anchor state the spacing chain is forbidden from mutating (S1).
_ANCHOR_FIELDS = ("defensive_line_anchor", "anchor_locked")


class SpacingGuard:
    """Verify the spacing chain produced a pure Y-only lateral output.

    Snapshots every defender's X-family fields and the anchor authority
    BEFORE the spacing body runs, then re-checks them AFTER.  Any
    mutation raises ``SpacingDepthViolation``:

      - a ``depth`` / ``target_x`` / ``anchor_x`` key in the output (S0)
      - any ``target_x`` / ``role_depth_target_x`` / chase / mmdl field
        changed on any defender (S0/S3)
      - the anchor authority value / world anchor fields changed (S1)

    Y targets are returned from ``compute_targets`` — they are not
    written onto defenders by the chain, so ``target_y`` is intentionally
    NOT in the protected set (step 7 owns the write).
    """

    def __init__(self, world, defenders):
        self.world = world
        self.defenders = list(defenders)

        # S1 — the authority value spacing must consume (ruck_x + 10).
        self.anchor_authority_before: float = defensive_line_anchor_m(world)
        self.anchor_state_before = {
            field: getattr(world, field, None) for field in _ANCHOR_FIELDS
        }

        # S0/S3 — per-defender X-family snapshots.
        self._x_before: dict = {}
        for d in self.defenders:
            if d is None or not isinstance(d, Player):
                continue
            snap = {}
            present = False
            for field in _PROTECTED_X_FIELDS:
                if hasattr(d, field):
                    snap[field] = getattr(d, field)
                    present = True
            if present:
                self._x_before[id(d)] = snap

    # ─────────────────────────────────────────────────────────────

    def verify(self, targets: dict) -> dict:
        """Assert the spacing body honoured the Y-only contracts.

        Raises ``SpacingDepthViolation`` on any violation; otherwise
        returns *targets* unchanged (pass-through for the pipeline).
        """
        # S0 — Y-only output shape.
        if not isinstance(targets, dict):
            raise SpacingDepthViolation(
                f"spacing output must be a dict (got {type(targets).__name__})"
            )
        for key in targets:
            if key == "depth" or key == "target_x" or key == "anchor_x":
                raise SpacingDepthViolation(
                    f"spacing attempted to modify depth (key='{key}')"
                )
            if not isinstance(key, int):
                raise SpacingDepthViolation(
                    f"spacing output key must be id(d) (got {key!r})"
                )
            value = targets[key]
            if isinstance(value, dict):
                raise SpacingDepthViolation(
                    f"spacing output value must be a single Y (got dict for {key!r})"
                )

        # S0/S3 — no defender X-family field mutated by the spacing body.
        for d in self.defenders:
            if d is None or not isinstance(d, Player):
                continue
            before = self._x_before.get(id(d))
            if before is None:
                continue
            for field, expected in before.items():
                current = getattr(d, field, None)
                if current is None and expected is None:
                    continue
                try:
                    changed = abs(float(current) - float(expected)) > 1e-9
                except (TypeError, ValueError):
                    changed = current != expected
                if changed:
                    raise SpacingDepthViolation(
                        "spacing touched an X-family field: "
                        f"id={getattr(d, 'player_id', '?')} {field} "
                        f"before={expected!r} after={current!r}"
                    )

        # S1 — anchor authority untouched.
        anchor_now = defensive_line_anchor_m(self.world)
        if abs(float(anchor_now) - float(self.anchor_authority_before)) > 1e-9:
            raise SpacingDepthViolation(
                "spacing attempted to modify the anchor authority: "
                f"before={self.anchor_authority_before:.4f} after={anchor_now:.4f}"
            )
        for field, expected in self.anchor_state_before.items():
            current = getattr(self.world, field, None)
            if current != expected:
                raise SpacingDepthViolation(
                    "spacing attempted to modify the anchor: "
                    f"{field} before={expected!r} after={current!r}"
                )

        return targets


# ─────────────────────────────────────────────────────────────────────
# GATED ENTRY — the spacing authority layer
# ─────────────────────────────────────────────────────────────────────

def run_spacing(
    world,
    defenders,
    tuners,
    governor=None,
    enforce_open_play: bool = True,
) -> dict:
    """Run the spacing chain with full Task H authority enforcement.

    Args:
        world: WorldState (or stub) — must be OPEN_PLAY when
            *enforce_open_play* is True (default).
        defenders: Iterable of Player objects forming the line.
        tuners: DefenceTuners instance.
        governor: Optional DefensiveLineGovernor (governed-width clamp).
        enforce_open_play: When True (default), raises
            ``SpacingOpenPlayGateViolation`` unless
            ``world.sub_state == OPEN_PLAY`` (S4 gate).

    Returns the ``{id(d): target_y}`` spacing dict.  The output is a pure
    function of (role, side, ruck_y, governed width): deterministic
    across frames (S6) and Y-only (S0).

    PRIMARY/SECONDARY/marker/tackler/DH/fullback are never written by
    this entry (S7) — the dict is still returned for all defenders so
    structured-close and step-7 consumers read the same Y target.
    """
    if enforce_open_play:
        assert_open_play(world)

    guard = SpacingGuard(world, defenders)

    # Lazy import: defensive_split_spacing pulls defensive_width; keeping
    # this local avoids any module-init ordering surprises.
    from game.defence.defence_lvl_1.defensive_split_spacing import compute_targets

    targets = compute_targets(world, defenders, tuners, governor=governor)
    return guard.verify(targets)


# ─────────────────────────────────────────────────────────────────────
# STAGE 3C — MANAGER-OWNED SPACING AUTHORITY
# ─────────────────────────────────────────────────────────────────────
# DefenceManager holds ONE ``SpacingAuthority``.  It is the SOLE place
# that assigns sides, builds the split-spacing chain (incl. role=None
# pre-ruck), runs SpacingGuard + WidthGuard, and PUBLISHES the two frame
# dicts every frame:
#
#     world.frame_spacing_targets  -> {id(d): spacing_y}   (Y authority)
#     world.frame_width_targets    -> {id(d): width_x}     (X authority)
#
# Every downstream writer (retreat, structured-close, role movement,
# post-ruck, MMDL) is a PURE CONSUMER of those dicts; none recomputes
# spacing or width.  This is the single-source contract that makes
# spacing and retreat never fight.
# ─────────────────────────────────────────────────────────────────────

class SpacingAuthority:
    """Sole spacing/width compute + publish authority (Stage 3C).

    Wraps ``run_defensive_pipeline`` (the un-gated preamble: depth-roles
    -> spacing -> width -> guards -> publish) so DefenceManager owns the
    geometry compute.  Provides pure dict reads for every movement
    writer:

        run(world, defenders, governor) -> (spacing, width)
            assign sides, build the chain, guard, publish both frame
            dicts.  Pure compute (never writes target_x/target_y).
        read_y(world, d)  -> dict-only frame spacing Y (no recompute)
        read_x(world, d)  -> dict-only frame width X (or None)
    """

    def __init__(self, tuners):
        self.tuners = tuners

    def run(self, world, defenders, governor=None):
        """Run the un-gated preamble: roles -> spacing -> width -> publish.

        Returns ``(spacing_targets, width_targets)``.  Publishes
        ``world.frame_spacing_targets`` / ``world.frame_width_targets``
        for the current frame_count.  Pure compute — never writes
        target_x/target_y.
        """
        # Lazy import: defensive_pipeline pulls defensive_width, depth
        # roles, and the guards; keeping it local mirrors the module's
        # established lazy pattern and avoids init-ordering surprises.
        from game.defence.defence_lvl_1.defensive_pipeline import (
            run_defensive_pipeline,
        )
        return run_defensive_pipeline(
            world, defenders, self.tuners, governor=governor
        )

    def read_y(self, world, d) -> float:
        """Dict-only frame spacing Y for *d* (no structural recompute)."""
        if not getattr(d, "fullback_role_active", False):
            if getattr(world, "frame_geometry_ready", False):
                dct = getattr(world, "frame_spacing_targets", None)
                if dct and id(d) in dct:
                    return float(dct[id(d)])
        return float(getattr(world, "ruck_y_m", 35.0))

    def read_x(self, world, d):
        """Dict-only frame width X for *d*, or None when width-ineligible."""
        dct = getattr(world, "frame_width_targets", None)
        if dct and id(d) in dct:
            return float(dct[id(d)])
        return None
