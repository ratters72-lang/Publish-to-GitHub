"""
movement_authority.py — Movement Authority Guard (Task I-B, 2026-08-11).

PURPOSE
-------
Make MOVEMENT a provable PURE CONSUMER of the authority layers:

  - spacing (Y) — consumed via ``defensive_split_spacing.spacing_y``
  - width  (X)  — consumed via the role width anchors (WIDTH_OFFSETS /
                   ROLE_DEPTH_M tables)
  - anchor (X)  — consumed via ``anchor_authority.defensive_line_anchor_m``

The guard snapshots every authority-owned field BEFORE the movement
consumer (MMDL, role movement, retreat, close) runs and asserts NOTHING
was mutated afterwards — mirroring the Task H (spacing) and Task I
(width) guards.  Movement may only WRITE its own outputs; it must never
write spacing outputs, width outputs, the anchor, chase targets, MMDL
targets (of other writers) or depth roles.

MOVEMENT-OWNED WRITES (allowed):
    target_x / target_y            — the movement output itself
    max_speed / move_speed         — speed output
    frozen / vx / vy / x / y       — physics integration
    mmdl_target_x / mmdl_target_y  — movement's own consumption stamp
    movement_state / movement_writer / target_source

FORBIDDEN MUTATIONS (any change raises ``MovementAuthorityViolation``):
    spacing outputs / spacing_y / role_anchor_y / inter_defender_gap_y
    width outputs / width_signature_y / def_line_width_m /
      governor_width_active / governor_line_width_m
    anchor authority (defensive_line_anchor / anchor_locked)
    chase_target_x / chase_target_y
    depth_role / depth_side / depth_class / role_depth_class
    world spacing-width override fields (short_side_width_m /
      open_side_width_m)

WIDTH-ELIGIBLE Y-INVARIANT (2026-08-17) — "movement-mode never sets Y
for lane holders; spacing owns Y; width governor owns width."
-------------------------------------------------------------------
Width-eligible defenders (A/B/C/D/CENTRE/WINGER line holders, see
``width_authority.is_width_eligible``) form the defensive line.  Their
``target_y`` is owned by the spacing authority: in structured mode the
spacing/geometry writers (split-spacing Y + role-spacing adjustment,
retreat, structured close, finalise) produce the frame's Y BEFORE the MMDL
block runs.  The MMDL must NEVER move that Y for a line-holding defender.

When *tuners* is passed to ``MovementAuthorityGuard`` (construction point
is immediately AFTER the writers and BEFORE the MMDL decision+physics
block in step 10b), the guard snapshots each width-eligible defender's
writer-produced ``target_y`` and asserts, after ``verify()``, that a line-
holding (RETREAT/DRIFT/HOLD) defender's ``target_y`` did not move off that
reference by more than ``_WIDTH_Y_TOLERANCE_M``.

The target stabiliser inside the MMDL block is ALLOWED to smooth
``target_y`` TOWARD the writer value, so the assertion uses a convergence
tolerance (``_WIDTH_Y_TOLERANCE_M``), not strict equality.  A genuine
movement-mode Y override (retreat-band / depth-default / any non-spacing
source) moves ``target_y`` METRES off the spacing-owned reference and
trips the guard immediately — the squash-after-tackle tripwire.
"""

from __future__ import annotations

from player.player import Player



# ─────────────────────────────────────────────────────────────────────
# EXCEPTION
# ─────────────────────────────────────────────────────────────────────

class MovementAuthorityViolation(Exception):
    """Raised when a movement consumer mutates an authority-owned field."""


# ─────────────────────────────────────────────────────────────────────
# PROTECTED FIELD TABLES
# ─────────────────────────────────────────────────────────────────────

# Per-defender authority fields movement must NEVER mutate.
_PROTECTED_DEFENDER_FIELDS = (
    # Spacing / width / anchor family (read-only authority inputs)
    "spacing_y",
    "role_anchor_y",
    "spacing_target_y",
    "width_signature_y",
    "inter_defender_gap_y",
    "role_depth_target_x",       # anchor-authority Layer 2 stamp
    # Chase / MMDL targets of OTHER writers
    "chase_target_x",
    "chase_target_y",
    # Stable depth-role system (Task A)
    "depth_role",
    "depth_side",
    "depth_class",
    "role_depth_class",
    "_depth_seeded",
)

# World anchor fields movement must NEVER mutate.
_ANCHOR_FIELDS = ("defensive_line_anchor", "anchor_locked")

# World width fields movement must NEVER mutate (governor owns them).
_WORLD_WIDTH_FIELDS = (
    "def_line_width_m",
    "governor_width_active",
    "governor_line_width_m",
    "short_side_width_m",
    "open_side_width_m",
)

# WIDTH-ELIGIBLE Y-INVARIANT CONVERGENCE TOLERANCE (metres).
# 0.6 m covers the FINALISE_BLEND per-frame delta cap (0.4 m max — see
# DefensiveBehaviourUnified.FINALISE_MAX_DELTA_M) plus the target
# stabiliser's smoothing overshoot toward the spacing Y, while still
# catching any metres-scale movement-mode Y override (the squash
# signature).  The assertion only fires for RETREAT/DRIFT/HOLD state
# holders, where spacing owns Y by contract.
_WIDTH_Y_TOLERANCE_M: float = 0.6

# Movement states in which the width-eligible Y-invariant applies.
# RETREAT / DRIFT / HOLD are the line-holding states the task names:
# spacing owns Y here — movement-mode must never set Y.  CHASE / SLIDE /
# SHOOT are pursuit/commit states whose Y legitimately tracks the BC
# (line-break collapse, structured close, tackle commit) and are exempt.
_Y_INVARIANT_STATES = frozenset({"RETREAT", "DRIFT", "HOLD"})



# ─────────────────────────────────────────────────────────────────────
# GUARD
# ─────────────────────────────────────────────────────────────────────

class MovementAuthorityGuard:
    """Snapshot the authority state and assert movement never mutates it.

    Typical usage around a movement consumer (MMDL block, role movement,
    retreat/close writers):

        guard = MovementAuthorityGuard(world, defenders, tuners)
        <movement consumer runs>
        guard.verify(movement_wrote=defenders)

    WIDTH-ELIGIBLE Y-INVARIANT (2026-08-17): when *tuners* is provided,
    ``__init__`` snapshots the frame-authoritative spacing Y for every
    width-eligible (lane-holding) defender, and ``verify()`` asserts each
    such defender's ``target_y`` stayed within ``_WIDTH_Y_TOLERANCE_M`` of
    that spacing Y after the consumer ran.  Frozen / freeze-timer
    defenders are exempt (their Y is a freeze pin owned by the freeze
    authority, and the MMDL loop skips them by construction).  PRIMARY /
    SECONDARY closers and width-exempt roles are never in the snapshot and
    are never checked — their own writers own their Y.
    """

    def __init__(self, world, defenders, tuners=None):
        """Snapshot the pre-consumer authority state.

        Args:
            world: WorldState (or stub).
            defenders: Iterable of Player objects the consumer may write.
            tuners: DefenceTuners.  When provided, the width-eligible
                Y-invariant is armed (spacing-Y reference captured for
                every lane holder).  When None, the invariant is disabled
                and the guard behaves exactly as before.
        """
        self.world = world
        self.defenders = list(defenders)
        self.tuners = tuners

        # Width-eligible spacing-owned Y reference (None -> invariant
        # disabled).  The guard construction point is immediately AFTER
        # every spacing/geometry/retreat/close writer and BEFORE the MMDL
        # decision+physics block, so each width-eligible defender's
        # target_y AT CONSTRUCTION is the spacing-owned Y (split-spacing
        # chain + role-spacing adjustment + finalise).  The invariant then
        # asserts the MMDL never moves that Y for a line-holding defender.
        # Frozen/freeze-timer defenders are exempt (their Y is a freeze
        # pin written by the freeze owner before this consumer ran).
        self._width_y_spacing: dict | None = None
        if tuners is not None:
            from game.defence.defence_lvl_1.width_authority import (
                is_width_eligible,
            )
            self._width_y_spacing = {}
            for d in self.defenders:
                if d is None or not isinstance(d, Player):
                    continue
                if not is_width_eligible(d):
                    continue
                if getattr(d, "frozen", False):
                    continue
                if getattr(d, "freeze_timer", 0.0) > 0.0:
                    continue
                ty = getattr(d, "target_y", None)
                if ty is None:
                    continue
                self._width_y_spacing[id(d)] = float(ty)

        # Anchor authority snapshot.
        self.anchor_state_before = {
            field: getattr(world, field, None) for field in _ANCHOR_FIELDS
        }
        # World width snapshot (governor owns width).
        self.world_width_before = {
            field: getattr(world, field, None) for field in _WORLD_WIDTH_FIELDS
        }

        # Per-defender protected-field snapshots.
        self._fields_before: dict = {}
        for d in self.defenders:
            if d is None or not isinstance(d, Player):
                continue
            snap = {}
            present = False
            for field in _PROTECTED_DEFENDER_FIELDS:
                if hasattr(d, field):
                    snap[field] = getattr(d, field)
                    present = True
            if present:
                self._fields_before[id(d)] = snap

    def verify(self, allowed_writes: dict | None = None) -> None:
        """Assert no authority field was mutated by the movement consumer.

        ``allowed_writes`` may map ``id(d) -> set(field_names)`` for
        movement-owned fields that the caller deliberately writes (a
        contract escape hatch for forward-compatible movement stamps NOT
        in the protected tables — protected fields can never be allowed).

        Raises ``MovementAuthorityViolation`` on any mutation.
        """
        if allowed_writes is None:
            allowed_writes = {}

        # Per-defender protected fields.
        for d in self.defenders:
            if d is None or not isinstance(d, Player):
                continue
            before = self._fields_before.get(id(d))
            if before is None:
                continue
            allowed = allowed_writes.get(id(d), set())
            for field, expected in before.items():
                if field in allowed:
                    continue
                current = getattr(d, field, None)
                if current is None and expected is None:
                    continue
                try:
                    changed = abs(float(current) - float(expected)) > 1e-9
                except (TypeError, ValueError):
                    changed = current != expected
                if changed:
                    raise MovementAuthorityViolation(
                        "movement mutated an authority field: "
                        f"id={getattr(d, 'player_id', '?')} {field} "
                        f"before={expected!r} after={current!r}"
                    )

        # Anchor authority untouched.
        for field, expected in self.anchor_state_before.items():
            current = getattr(self.world, field, None)
            if current != expected:
                raise MovementAuthorityViolation(
                    "movement attempted to modify the anchor: "
                    f"{field} before={expected!r} after={current!r}"
                )

        # World width untouched (governor owns width).
        for field, expected in self.world_width_before.items():
            current = getattr(self.world, field, None)
            if current != expected:
                raise MovementAuthorityViolation(
                    "movement attempted to modify world width: "
                    f"{field} before={expected!r} after={current!r}"
                )

        # ── WIDTH-ELIGIBLE Y-INVARIANT (2026-08-17) ──
        # Movement-mode must never set Y for lane holders: spacing owns Y.
        # Every width-eligible defender in a line-holding state
        # (RETREAT / DRIFT / HOLD — the states the task names) must keep
        # its post-consumer target_y on the frame spacing chain (target
        # stabiliser convergence allowed within _WIDTH_Y_TOLERANCE_M).
        # State-gated: CHASE / SLIDE / SHOOT pursuit or commit states are
        # exempt — their Y legitimately tracks the BC (line-break
        # collapse, structured close, tackle commit).  Frozen / freeze-
        # timer defenders are also exempt — their Y is the freeze pin
        # written by the freeze owner BEFORE this consumer ran (and the
        # MMDL loop skips them entirely).
        if self._width_y_spacing is not None:
            for d in self.defenders:
                if d is None or not isinstance(d, Player):
                    continue
                if id(d) not in self._width_y_spacing:
                    continue
                if getattr(d, "frozen", False):
                    continue
                if getattr(d, "freeze_timer", 0.0) > 0.0:
                    continue
                _ms = getattr(d, "movement_state", None)
                _ms_name = getattr(_ms, "name", None) or str(_ms)
                if _ms_name not in _Y_INVARIANT_STATES:
                    continue
                spacing_y = self._width_y_spacing[id(d)]
                current_y = getattr(d, "target_y", None)
                if current_y is None:
                    continue
                try:
                    deviation = abs(float(current_y) - spacing_y)
                except (TypeError, ValueError):
                    deviation = float("inf")
                if deviation > _WIDTH_Y_TOLERANCE_M:
                    raise MovementAuthorityViolation(
                        "movement-mode set Y on a width-eligible lane "
                        "holder (spacing owns Y): "
                        f"id={getattr(d, 'player_id', '?')} "
                        f"state={_ms_name} "
                        f"target_y={current_y!r} spacing_y={spacing_y:.3f} "
                        f"deviation={deviation:.3f}m "
                        f"(tolerance={_WIDTH_Y_TOLERANCE_M}m)"
                    )
