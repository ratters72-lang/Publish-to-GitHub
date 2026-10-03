"""
width_authority.py — Width Chain Stabilisation (Task I, 2026-08-11).

PURPOSE
-------
Make width recompute a pure X-only authority subsystem, mirroring the
Task H spacing authority.  This module wraps the pure width recompute
(``defensive_width.recompute_width_x``) with the Task I contracts:

  I0 — X-only output: width recompute NEVER writes ``target_y`` /
       ``spacing_y`` / ``role_anchor_y`` / ``anchor_x`` / ``anchor_y`` /
       ``chase_target_x`` / ``chase_target_y`` / ``mmdl_target_x`` /
       ``mmdl_target_y`` / depth roles / spacing roles / spacing
       outputs.  It produces ONE thing: the per-defender line X
       (``{id(d): target_x}``).  Spacing is Y-only; width is X-only.
       This is the core orthogonality invariant.
  I1 — OPEN_PLAY gate: width recompute runs ONLY when
       ``world.sub_state == OPEN_PLAY``.  TES/PTB/RELEASE retreat
       writers may read width, but width recompute never runs in those
       phases (mirrors spacing's S4 gate).
  I2 — Determinism: given the same defenders, ruck, governed width,
       spacing outputs and tuners, width recompute produces identical
       outputs across any number of frames (verified over 200 frames
       by verify_width_chain.py, section I2).
  I3 — Anchor compliance: width reads the anchor via
       ``anchor_authority.defensive_line_anchor_m(world)``, never
       writes it, never violates anchor authority, never pushes a line
       holder ahead of the anchor band.  Width is a consumer of anchor,
       never a producer.
  I4 — Spacing compliance: width never overrides spacing Y, never
       overrides spacing's role-side width / exemptions / OPEN_PLAY
       gate / anchor consumption / deterministic signature.  Spacing
       outputs are read-only authority inputs.
  I5 — Role exemptions: width skips MARKER_A / MARKER_B / DUMMY_HALF /
       TACKLER_* / DEF_CHASE / the dynamic fullback / PRIMARY /
       SECONDARY.  Those defenders never receive width recompute
       targets (mirrors spacing's S7 predicate).
  I6 — Authority safety: the guard snapshots every defender's
       ``target_x`` + every forbidden field + the anchor authority +
       the spacing outputs BEFORE running, then asserts NOTHING was
       mutated.  ``recompute_width_x`` is a pure function (it never
       writes defender or world attributes), so a violation raised here
       means a future edit corrupted the chain — the spec's in-chain
       enforcement point (identical to spacing's guard layer).

ORTHOGONALITY NOTE (the X/Y split)
----------------------------------
The width recompute PRODUCES the X targets; the step-7 application loop
in DefensiveBehaviourUnified owns the WRITE of ``d.target_x`` (just as
step 7 owns the write of ``d.target_y`` from spacing).  The authority
layer is pure validation: it proves the width source is X-only and
mutation-free BEFORE the apply loop consumes it.

USAGE in the structured OPEN_PLAY pipeline (after step 6, Task H):
    from game.defence.defence_lvl_1.width_authority import WidthGuard
    from game.defence.defence_lvl_1.defensive_width import (
        recompute_width_x,
    )
    width_targets = recompute_width_x(
        world, sorted_defs, self.tuners,
        governor_width=_governed_width,
        spacing_targets=split_targets,
    )
    WidthGuard(
        world, sorted_defs, self.tuners, _governed_width,
        spacing_outputs=split_targets,
    ).verify(width_targets)
"""

from __future__ import annotations

from player.player import Player
from game.defence.defence_lvl_1.anchor_authority import (
    AnchorAuthorityViolation,
    defensive_line_anchor_m,
)

# Re-exported for convenience so Task I call sites import one module.
__all__ = [
    "WidthAuthorityViolation",
    "WidthOpenPlayGateViolation",
    "WidthGuard",
    "assert_open_play",
    "is_open_play",
    "is_width_eligible",
    "run_width",
]


# ─────────────────────────────────────────────────────────────────────
# EXCEPTIONS
# ─────────────────────────────────────────────────────────────────────

class WidthAuthorityViolation(AnchorAuthorityViolation):
    """Raised when the width chain attempts a forbidden write.

    Subclasses ``AnchorAuthorityViolation`` so Task G anchor guards and
    any upstream audit catch width violations with the anchor authority
    family (Layer 3/4 symmetry: spacing violations subclass the same
    root).
    """


class WidthOpenPlayGateViolation(WidthAuthorityViolation):
    """Raised when ``run_width`` is invoked outside the OPEN_PLAY sub-state.

    Width recompute runs ONLY during OPEN_PLAY.  It must NEVER run during
    TES, PTB, POST_RUCK (or any sub-state whose phase handlers own
    targets).  TES/PTB/RELEASE writers may READ width, but they never run
    the recompute.
    """


# ─────────────────────────────────────────────────────────────────────
# I1 — OPEN_PLAY GATE
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
    """I1 gate: width recompute must run ONLY during OPEN_PLAY."""
    if not is_open_play(world):
        raise WidthOpenPlayGateViolation(
            "width recompute must never run outside OPEN_PLAY "
            f"(sub_state={getattr(world, 'sub_state', None)!r})"
        )


# ─────────────────────────────────────────────────────────────────────
# I5 — ROLE EXEMPTIONS
# ─────────────────────────────────────────────────────────────────────

# Roles that play tight to the ruck/set or are engaged in the tackle —
# excluded from the width authority write by contract (mirrors spacing
# S7 exactly: these defenders' own writers own their targets).
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
# / chase writers own their X targets).
_EXEMPT_DEPTH_CLASSES = frozenset({"PRIMARY", "SECONDARY"})

# WIDTH-ELIGIBLE DEPTH ROLES (2026-08-17) — the lane-holding roles the
# width chain governs.  Explicit role-inclusion contract: width-eligible
# = A / B / C / D / CENTRE / WINGER (the depth-role system's full lane
# set).  A defender stamped with any OTHER depth_role (none exist in the
# engine today) is NOT a width lane holder and never receives the width
# X write — its own writer owns its X.  Mirrors spacing S7's role gate.
_WIDTH_ELIGIBLE_DEPTH_ROLES = frozenset({
    "A",
    "B",
    "C",
    "D",
    "CENTRE",
    "WINGER",
})


def is_width_eligible(d, world=None, tuners=None) -> bool:
    """Return True when *d* is eligible for the width authority write.

    Width recompute applies ONLY to PASSIVE / line-holding defenders.
    Exempt (I5):
      - Markers / dummy-half / TES tacklers / DEF_CHASE (role-based)
      - The dynamic fullback actor (backfield coverage, own anchor)
      - PRIMARY/SECONDARY closers (close/chase own their X targets)
      - Frozen defenders (their targets are pinned by the freeze owner)

    WIDTH-ELIGIBLE ROLE GATE (2026-08-17): a defender is width-eligible
    only when its depth_role is one of the lane-holding roles
    (A/B/C/D/CENTRE/WINGER), so width X authority never leaks onto
    non-lane actors.  An UNSEEDED bootstrap defender (depth_role None,
    the first frame before assign_depth_roles stamps roles) remains
    eligible — recompute_width_x's "D" default holds for it, exactly the
    pre-gate behaviour.

    STAGE 2 SECONDARY WIDTH-ELIGIBILITY GRACE (2026-09-06): when
    ``world`` and ``tuners`` are supplied AND
    ``tuners.secondary_grace_enabled`` is True, a PRIMARY/SECONDARY
    defender that is ruck-adjacent and freshly-promoted retains its
    width-lane eligibility for ``secondary_grace_duration_s`` (see
    secondary_width_grace.py).  The grace is consulted ONLY when the
    grace module reports an active grace; existing ``is_width_eligible(d)``
    call sites (no world/tuners) never trigger it, so the base predicate
    is byte-identical.

    This mirrors spacing's S7 predicate so the two authority layers skip
    the IDENTICAL defender set.
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
        # ── STAGE 2 SECONDARY WIDTH-ELIGIBILITY GRACE ──
        # A defender promoted to PRIMARY/SECONDARY while ruck-adjacent
        # keeps its width-lane eligibility for secondary_grace_duration_s
        # so the fallback spacing cannot pull that interior defender into
        # a fallback micro-gap during the marker lock window.  Waived only
        # when the grace module reports an active grace.
        if not _secondary_grace_would_waive(d, world, tuners):
            return False
    if getattr(d, "frozen", False) and getattr(d, "freeze_timer", 0.0) > 0.0:
        return False
    # WIDTH-ELIGIBLE ROLE GATE: only depth-role lane holders are width
    # lane holders.  None (unseeded bootstrap) stays eligible via the
    # recompute's "D" default so first-frame behaviour is unchanged.
    depth_role = getattr(d, "depth_role", None)
    if depth_role is not None and depth_role not in _WIDTH_ELIGIBLE_DEPTH_ROLES:
        return False
    return True


def _secondary_grace_would_waive(d, world, tuners) -> bool:
    """True when the Stage 2 grace waives the PRIMARY/SECONDARY exemption.

    Lazy-imports ``secondary_width_grace.secondary_grace_active`` to avoid
    any module-init ordering surprise (mirrors the lazy pattern used for
    the spacing / split-spacing imports).  When ``world`` / ``tuners`` are
    absent (legacy ``is_width_eligible(d)`` callers), returns False — the
    grace is NEVER consulted there, so the base predicate is unchanged.
    """
    if world is None or tuners is None:
        return False
    if not bool(getattr(tuners, "secondary_grace_enabled", False)):
        return False
    from game.defence.defence_lvl_1.secondary_width_grace import (
        secondary_grace_active,
    )
    return secondary_grace_active(d, world, tuners)


# ─────────────────────────────────────────────────────────────────────
# I0/I3/I4/I6 — WIDTH GUARD (read-back authority assertions)
# ─────────────────────────────────────────────────────────────────────

# X-family fields the width chain must never touch beyond its OWN output
# (``target_x`` is the width output — produced by the pure recompute and
# written by the step-7 apply loop, never mutated by the recompute
# itself).  ``role_depth_target_x`` is the depth-role X stamp = anchor
# authority Layer 2 — never mutated by width.
_PROTECTED_X_FIELDS = (
    "target_x",
    "role_depth_target_x",
)

# Y-family fields the width chain is forbidden from touching (I0).
_PROTECTED_Y_FIELDS = (
    "target_y",
    "spacing_y",
    "role_anchor_y",
    "anchor_x",
    "anchor_y",
)

# Chase / MMDL targets the width chain is forbidden from touching (I0).
_PROTECTED_CHASE_MMDL_FIELDS = (
    "chase_target_x",
    "chase_target_y",
    "mmdl_target_x",
    "mmdl_target_y",
)

# Spacing output fields the width chain is forbidden from touching (I0/I4).
_PROTECTED_SPACING_FIELDS = (
    "spacing_target_y",
    "width_signature_y",
    "inter_defender_gap_y",
)

# Stable depth roles/sides the width chain is forbidden from touching (I0).
_PROTECTED_DEPTH_FIELDS = ("depth_role", "depth_side", "depth_class")

# Anchor state the width chain is forbidden from mutating (I3).
_ANCHOR_FIELDS = ("defensive_line_anchor", "anchor_locked")

# World width fields the width chain is forbidden from mutating (I0 — the
# governor is the sole writer of world width).
_WORLD_WIDTH_FIELDS = (
    "def_line_width_m",
    "governor_width_active",
    "governor_line_width_m",
)


class WidthGuard:
    """Verify the width recompute produced a pure X-only output.

    Snapshots every protected defender field (X / Y / chase-MMDL /
    spacing / depth) plus the anchor authority and the spacing output
    dict BEFORE the width recompute runs, then re-checks them AFTER.
    ANY mutation raises ``WidthAuthorityViolation`` (I6).  The recompute
    is a pure function, so a violation means a future edit corrupted the
    chain — the spec's in-chain enforcement point.

    The width output dict is also audited: X-only keys, no depth / Y /
    chase / mmdl / anchor keys, numeric single-X values, and NO target
    for any width-exempt defender (I5 respected by construction).
    """

    def __init__(
        self,
        world,
        defenders,
        tuners=None,
        governor_width: float | None = None,
        spacing_outputs: dict | None = None,
    ):
        """Snapshot the pre-run state for the read-back assertions.

        Args:
            world: WorldState (or stub).  Must be OPEN_PLAY when passed
                through ``run_width`` (the gate lives there, matching
                spacing's S4 split).
            defenders: Iterable of Player objects forming the line.
            tuners: DefenceTuners instance (optional for the pure
                read-back; required by ``run_width``).
            governor_width: The governed width (Task D) the line uses this
                frame.  Optional — the recompute falls back to
                ``governed_width_m(world, tuners)``.
            spacing_outputs: The spacing chain's output dict (Task H) —
                snapshot as read-only authority inputs (I4).
        """
        self.world = world
        self.defenders = list(defenders)
        self.tuners = tuners
        self.governor_width = governor_width

        # I3 — the authority value width must consume (ruck_x + 10).
        self.anchor_authority_before: float = defensive_line_anchor_m(world)
        self.anchor_state_before = {
            field: getattr(world, field, None) for field in _ANCHOR_FIELDS
        }
        self.world_width_before = {
            field: getattr(world, field, None) for field in _WORLD_WIDTH_FIELDS
        }

        # I4 — spacing outputs snapshot (live dict + byte-compare copy).
        self._spacing_live = spacing_outputs
        self._spacing_snapshot = (
            dict(spacing_outputs) if spacing_outputs is not None else None
        )

        # I0/I6 — per-defender protected-field snapshots.
        self._fields_before: dict = {}
        for d in self.defenders:
            if d is None or not isinstance(d, Player):
                continue
            snap = {}
            present = False
            for field in (
                _PROTECTED_X_FIELDS
                + _PROTECTED_Y_FIELDS
                + _PROTECTED_CHASE_MMDL_FIELDS
                + _PROTECTED_SPACING_FIELDS
                + _PROTECTED_DEPTH_FIELDS
            ):
                if hasattr(d, field):
                    snap[field] = getattr(d, field)
                    present = True
            if present:
                self._fields_before[id(d)] = snap

    # ─────────────────────────────────────────────────────────────

    def verify(self, targets: dict | None = None) -> dict:
        """Assert the width recompute honoured the X-only contracts.

        Raises ``WidthAuthorityViolation`` on any violation; otherwise
        returns *targets* unchanged (pass-through for the pipeline).

        When *targets* is None (the Task I literal call form
        ``WidthGuard(...).verify()``), the recompute source is run
        internally through the same pure function the pipeline uses.
        """
        if targets is None:
            # I2 — the pure recompute source (same function the pipeline
            # calls explicitly).
            from game.defence.defence_lvl_1.defensive_width import (
                recompute_width_x,
            )
            targets = recompute_width_x(
                self.world,
                self.defenders,
                self.tuners,
                governor_width=self.governor_width,
                spacing_targets=self._spacing_live,
            )

        # I0 — X-only output shape.
        if not isinstance(targets, dict):
            raise WidthAuthorityViolation(
                f"width output must be a dict (got {type(targets).__name__})"
            )
        forbidden_keys = frozenset({
            "depth",
            "target_y",
            "spacing_y",
            "role_anchor_y",
            "anchor_x",
            "anchor_y",
            "chase_target_x",
            "chase_target_y",
            "mmdl_target_x",
            "mmdl_target_y",
        })
        for key in targets:
            if key in forbidden_keys:
                raise WidthAuthorityViolation(
                    f"width attempted to modify a forbidden field (key='{key}')"
                )
            if not isinstance(key, int):
                raise WidthAuthorityViolation(
                    f"width output key must be id(d) (got {key!r})"
                )
            value = targets[key]
            if isinstance(value, dict):
                raise WidthAuthorityViolation(
                    f"width output value must be a single X (got dict for {key!r})"
                )
            try:
                float(value)
            except (TypeError, ValueError):
                raise WidthAuthorityViolation(
                    f"width output value must be numeric X (got {value!r})"
                ) from None

        # I5 — role exemptions respected: no width target for an exempt
        # defender.  Grace-aware: a SECONDARY defender under the Stage 2
        # width-eligibility grace is not exempt, so the read-back must
        # consult the same grace-aware predicate the recompute used.
        for d in self.defenders:
            if d is None or not isinstance(d, Player):
                continue
            if id(d) in targets and not is_width_eligible(
                d, self.world, self.tuners,
            ):
                raise WidthAuthorityViolation(
                    "width produced a target for an exempt defender: "
                    f"id={getattr(d, 'player_id', '?')} "
                    f"role={getattr(d, 'defender_role', None)!r} "
                    f"depth_class={getattr(d, 'depth_class', None)!r}"
                )

        # I0/I6 — no defender field mutated by the width body.
        for d in self.defenders:
            if d is None or not isinstance(d, Player):
                continue
            before = self._fields_before.get(id(d))
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
                    raise WidthAuthorityViolation(
                        "width touched a forbidden field: "
                        f"id={getattr(d, 'player_id', '?')} {field} "
                        f"before={expected!r} after={current!r}"
                    )

        # I3 — anchor authority untouched.
        anchor_now = defensive_line_anchor_m(self.world)
        if abs(float(anchor_now) - float(self.anchor_authority_before)) > 1e-9:
            raise WidthAuthorityViolation(
                "width attempted to modify the anchor authority: "
                f"before={self.anchor_authority_before:.4f} "
                f"after={anchor_now:.4f}"
            )
        for field, expected in self.anchor_state_before.items():
            current = getattr(self.world, field, None)
            if current != expected:
                raise WidthAuthorityViolation(
                    "width attempted to modify the anchor: "
                    f"{field} before={expected!r} after={current!r}"
                )

        # I0 — world governor width fields untouched (governor owns them).
        for field, expected in self.world_width_before.items():
            current = getattr(self.world, field, None)
            if current != expected:
                raise WidthAuthorityViolation(
                    "width attempted to modify world width: "
                    f"{field} before={expected!r} after={current!r}"
                )

        # I4 — spacing outputs untouched (read-only authority inputs).
        if self._spacing_snapshot is not None:
            if not isinstance(self._spacing_live, dict):
                raise WidthAuthorityViolation(
                    "width spacing-output reference is no longer a dict"
                )
            if self._spacing_live != self._spacing_snapshot:
                raise WidthAuthorityViolation(
                    "width attempted to modify spacing outputs"
                )

        return targets


# ─────────────────────────────────────────────────────────────────────
# GATED ENTRY — the width authority layer
# ─────────────────────────────────────────────────────────────────────

def run_width(
    world,
    defenders,
    tuners,
    governor_width: float | None = None,
    spacing_outputs: dict | None = None,
) -> dict:
    """Run the width recompute with full Task I authority enforcement.

    Args:
        world: WorldState (or stub) — must be OPEN_PLAY (I1 gate).
        defenders: Iterable of Player objects forming the line.
        tuners: DefenceTuners instance.
        governor_width: The governed width (Task D) consumed by the
            recompute.  Falls back to ``governed_width_m(world, tuners)``.
        spacing_outputs: The spacing chain's output dict (Task H) —
            read-only authority input (I4).

    Raises ``WidthOpenPlayGateViolation`` when *world* is not OPEN_PLAY.
    Returns the verified ``{id(d): target_x}`` width dict (pass-through).

    The authority layer is pure validation: it never modifies behaviour —
    the recompute source is ``defensive_width.recompute_width_x`` and the
    ``d.target_x`` write stays owned by the step-7 apply loop.
    """
    assert_open_play(world)

    guard = WidthGuard(
        world,
        defenders,
        tuners,
        governor_width=governor_width,
        spacing_outputs=spacing_outputs,
    )

    # Lazy import: defensive_width pulls anchor_authority (and, lazily,
    # this module); keeping this local avoids any module-init ordering
    # surprises (mirrors spacing_authority.run_spacing).
    from game.defence.defence_lvl_1.defensive_width import recompute_width_x

    targets = recompute_width_x(
        world,
        defenders,
        tuners,
        governor_width=governor_width,
        spacing_targets=spacing_outputs,
    )
    return guard.verify(targets)
