"""
span_contract.py - the SINGLE published span authority (Stage 2, 2026-09-13).

PURPOSE
-------
Every defensive ladder generator (``side_lanes``, the TES census ladder, any
future lane builder) must derive its geometry from an EXPLICIT span contract.
Widthless lane generation is no longer a supported architecture: a caller that
cannot supply a contract fails immediately with ``SpanContractError`` rather
than silently reconstructing width from defender count or gap spacing.

WHY THIS EXISTS
---------------
Stage 1 (``verify_span_authority.py``) measured five authority levels per case
and found that the TES drive chamber built its census ladder from
``side_lanes(...)`` with NO ``side_width``, so each side fell back to the flat
``tuners.defender_gap_m`` (4.0 m) spacing.  The realised TES span was therefore
INVARIANT to the commanded width - a count-only function.  The Stage 1 report
named this source ``TES_WIDTH_FREE_LADDER``; it was the dominant divergence in
426 of 735 TES cases and the whole of the worst case (-47.00 m).

THE CONTRACT
------------
``SpanContract`` carries the per-side SPAN in metres - the winger-to-winger
distance for that side - plus the source label that produced it.  Ladder
generators consume ``span.left_m`` / ``span.right_m`` (or, by side,
``span.span_m(side)``).

``publish_span_contract`` is the ONE accessor.  It delegates to the existing
``side_widths()`` (proportional short/open split, available-room cap), so the
governed 3-point width tuner interpolation stays the single source and no
second width formula is introduced here.

FAIL-FAST
---------
``require_span`` is the guard every lane builder calls.  It rejects ``None``,
non-finite, and non-positive values.  ``side_lanes`` / ``lane_gap_m`` take the
span as a MANDATORY argument, so omitting it is a ``TypeError`` at the call -
construction-time failure, not a silent 4.0 m ladder.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "SpanContractError",
    "SpanContract",
    "require_span",
    "publish_span_contract",
    "SPAN_SOURCE_PUBLISHED",
    "SPAN_SOURCE_EXPLICIT",
]

# Source labels.  Recorded on every contract so a divergence can be traced to
# the authority that produced the span.
SPAN_SOURCE_PUBLISHED: str = "published"   # from side_widths() - governed chain
SPAN_SOURCE_EXPLICIT: str = "explicit"     # supplied directly by a caller


class SpanContractError(AssertionError):
    """Raised when a ladder generator is handed no usable span contract.

    Subclasses ``AssertionError`` so existing ``assert``-style call sites and
    verifier harnesses catch it, while the name stays greppable for the
    widthless-generation audit.
    """


@dataclass(frozen=True)
class SpanContract:
    """The published per-side span for one frame.

    ``left_m`` / ``right_m`` are the SPAN (full winger-to-winger width) of the
    left and right halves respectively.  A ladder generator spaces ``n_side``
    defenders across this span; it never invents the span.
    """

    left_m: float
    right_m: float
    source: str = SPAN_SOURCE_PUBLISHED

    def __post_init__(self) -> None:
        object.__setattr__(self, "left_m", float(self.left_m))
        object.__setattr__(self, "right_m", float(self.right_m))
        if not (math.isfinite(self.left_m) and math.isfinite(self.right_m)):
            raise SpanContractError(
                "SpanContract requires finite spans, got "
                f"left={self.left_m!r} right={self.right_m!r}"
            )

    # -- per-side access -------------------------------------------------
    def span_m(self, side: int) -> float:
        """Return the full span (m) for *side* (+1 RIGHT / -1 LEFT)."""
        return self.right_m if int(side) > 0 else self.left_m

    def half_m(self, side: int) -> float:
        """Return the half-span (m) for *side* (+1 RIGHT / -1 LEFT)."""
        return self.span_m(side) * 0.5

    @property
    def total_m(self) -> float:
        """Sum of both side spans (m).  Diagnostic only, never a target."""
        return self.left_m + self.right_m

    def as_tuple(self) -> tuple:
        """Return ``(left_m, right_m)`` - the legacy ``side_widths`` shape."""
        return self.left_m, self.right_m


def require_span(value, *, caller: str) -> float:
    """Return *value* as a positive finite float, or raise SpanContractError.

    This is the fail-fast gate.  A production ladder generator that cannot
    supply an explicit span contract raises here rather than falling back to
    ``defender_gap_m``.
    """
    if value is None:
        raise SpanContractError(
            f"{caller}: no span contract supplied. Widthless lane generation "
            "is not supported - pass an explicit SpanContract / side span."
        )
    try:
        span = float(value)
    except (TypeError, ValueError) as exc:
        raise SpanContractError(
            f"{caller}: span contract is not numeric ({value!r})."
        ) from exc
    if not math.isfinite(span):
        raise SpanContractError(
            f"{caller}: span contract is not finite ({span!r})."
        )
    if span <= 0.0:
        raise SpanContractError(
            f"{caller}: span contract must be positive (got {span!r})."
        )
    return span


def publish_span_contract(world, tuners, ruck_y: float | None = None) -> SpanContract:
    """Return the published :class:`SpanContract` for the live frame.

    The single accessor every defensive ladder generator reads.  Delegates to
    ``defensive_split_spacing.side_widths`` - the governed 3-point width tuner
    interpolation (short/open split + available-room cap).  No second width
    formula exists here; this module only NAMES and GUARDS the contract.

    ``side_widths`` is imported lazily: ``defensive_split_spacing`` imports
    from ``defensive_width`` at module scope, so a top-level import here would
    close a cycle.  Mirrors the lazy-import pattern already used by
    ``defensive_width.role_anchor_y_side``.
    """
    if world is None:
        raise SpanContractError(
            "publish_span_contract: world is None - cannot publish a span."
        )
    # ── STAGE 6 SINGLE WIDTH AUTHORITY (2026-09-13) ──
    # The span is no longer derived here (nor by ``side_widths``).  The
    # ``formation_width_authority`` computes and publishes the ONE width fact
    # for the frame — including this contract — so every ladder generator
    # (TES census, side_lanes callers) reads exactly the spans the spacing
    # pipeline did.  ``side_widths`` is retained as a compatibility shim, but
    # this accessor now returns the PUBLISHED contract object itself.
    #
    # The fail-fast ``require_span`` guard is preserved: a published span that
    # is missing / non-finite / non-positive still raises ``SpanContractError``
    # here, so the widthless-ladder audit keeps a single choke point.
    from game.defence.defence_lvl_1.formation_width_authority import (
        published_span_contract,
    )
    contract = published_span_contract(world, tuners, ruck_y=ruck_y)
    require_span(contract.left_m, caller="publish_span_contract(left)")
    require_span(contract.right_m, caller="publish_span_contract(right)")
    return contract
