"""
population_authority.py — the SINGLE line-population authority (Stage 5,
2026-09-13).

WHY THIS MODULE EXISTS
----------------------
Stage 4 (see ``STAGE4_COUNT_CONTRACT_VS_POPULATION_VERDICT.md``) measured
that four DIFFERENT defender sets were live in the SAME frame:

    count ownership     ``assign_depth_roles.eligible``
    side ownership      bodies carrying ``d.depth_side``
    spacing ownership   ``compute_targets`` eligible (+ ruck-Y pins)
    observers           ``keys(world.frame_spacing_targets)``

and produced a MIXED verdict (BOTH contracts violated):

    D1  1722  published count total != the population it was solved for
    D2  6956  a body carries ``depth_side`` but is NOT in the count pool
    D3C 1735  published per-side counts disagree with its own population
    D4    26  several count solves in one frame on different populations
              (``n_pops = [12, 11, 12]``), last-writer-wins

Three root causes, all removed by this module:

  1. **STALE depth_side MEMBERSHIP** — nothing cleared ``d.depth_side`` when
     a body left the count pool (tackle-state / fullback / excluded role),
     so any observer counting ``depth_side`` saw a superset of the count
     population (the ``n_open=5`` vs 6-bodies signature).
  2. **PUBLISHED-COUNT LAG** — ``decide_side_counts``' hysteresis adopted the
     previous frame's split without re-checking that it summed to
     ``n_total``, so a one-body population change kept the OLD total.
  3. **MULTI-PUBLISHER COUNT OWNERSHIP** — ``assign_split_sides`` ran from
     the per-frame preamble, the dynamic refit (on a collapse-locked
     SUBSET) and the reband, each solving its own count last-writer-wins
     and never keyed by population.

THIS MODULE IS THE ONE PLACE THAT DEFINES AND PUBLISHES THE LINE POPULATION:

    ``line_population(defenders)``           -> the single canonical set
    ``publish_line_population(world, ...)``  -> the single frame publication
    ``published_population(world)``          -> read-back for observers

For EVERY frame the publication holds:

    published_count_left + published_count_right
        == len(line_population)
        == (#depth_side == -1) + (#depth_side == +1)     [side ownership]
        == len(keys(world.frame_spacing_targets))        [spacing ownership]

and every body NOT in the population carries ``depth_side = None``.

SINGLE PER-FRAME PUBLICATION
----------------------------
The publication is keyed by
``(frame_count, population signature, ruck_y, ball_side, ball_side_bias)``.
A second call in the same frame for the same population returns the cached
publication WITHOUT re-solving or re-stamping, so the count is a single
per-frame fact no matter how many subsystems ask.  The ball-side bias is
resolved ONCE by this module (never passed in per caller), so the dynamic
refit and the reband can never publish a differently-split count.

The population is derived from ``world.defending_team`` when present (the
full team), so a caller handing in a SUBSET (e.g. the dynamic refit's
collapse-locked subset) can never shrink the count population.

PURE W.R.T. POSITIONS: only ``depth_side`` / ``frame_side`` / ``depth_role``
/ ``_depth_seeded`` and the ``world.*`` publication fields are written.  This
module never writes x / y / target_* / movement.
"""

from __future__ import annotations

from player.player import DefenderRole

__all__ = [
    "LINE_EXCLUDED_ROLES",
    "line_population",
    "is_line_body",
    "publish_line_population",
    "published_population",
    "published_counts",
    "publish_frame_is_fresh",
]


# ─────────────────────────────────────────────────────────────────────
# THE CANONICAL LINE-EXCLUSION CONTRACT
# ─────────────────────────────────────────────────────────────────────
# Every owner (count / side / spacing / observers) excludes the SAME roles.
# This is the union Stage 4 proved was needed: the old
# ``defensive_depth_roles.EXCLUDED_ROLES`` omitted DEF_CHASE while the
# spacing authority's ``_SPACING_EXCLUDED_ROLES`` included it, so a DEF_CHASE
# body was counted but not spaced (and a tackle-state chain body was spaced
# but not counted).
#
#   TACKLER_1/2/3   engaged in the tackle (never line shape)
#   MARKER_A/B      tight to the ruck
#   DUMMY_HALF      dummy half at the play-the-ball
#   KICK_CHASE      kick-chase body
#   DEF_CHASE       chase body (NOT a line defender)
LINE_EXCLUDED_ROLES = frozenset({
    DefenderRole.TACKLER_1,
    DefenderRole.TACKLER_2,
    DefenderRole.TACKLER_3,
    DefenderRole.MARKER_A,
    DefenderRole.MARKER_B,
    DefenderRole.DUMMY_HALF,
    DefenderRole.KICK_CHASE,
    DefenderRole.DEF_CHASE,
})


def _role_name(role):
    """Return the role's enum name (or the value verbatim, or None)."""
    if role is None:
        return None
    return getattr(role, "name", role)


def is_line_body(d) -> bool:
    """True when *d* belongs to the authoritative defensive LINE.

    The single membership predicate shared by count, side, spacing and every
    observer:

      * not the dynamic fullback actor (lane-agnostic, ball-track geometry)
      * not a TACKLE-STATE holder (invisible to the line systems)
      * ``defender_role`` not in :data:`LINE_EXCLUDED_ROLES`
    """
    if d is None:
        return False
    if getattr(d, "fullback_role_active", False):
        return False
    if getattr(d, "tackle_state_active", False):
        return False
    return getattr(d, "defender_role", None) not in LINE_EXCLUDED_ROLES


def line_population(defenders) -> list:
    """Return the single authoritative line population from *defenders*.

    Input order is preserved; the nearest-first ordering happens later, in
    ``publish_line_population``.
    """
    if not defenders:
        return []
    return [d for d in defenders if is_line_body(d)]


# ─────────────────────────────────────────────────────────────────────
# STALE-STAMP ELIMINATION
# ─────────────────────────────────────────────────────────────────────

def _clear_line_fields(d) -> None:
    """Clear every line-ownership field on a body that left the population."""
    d.depth_side = None
    d.frame_side = None
    d.depth_role = None
    d._depth_seeded = False


def _clear_stale_sides(world, source, population_ids) -> list:
    """Clear line fields on every body NOT in the population.

    Returns the list of cleared players (diagnostics / verifier).  The scan
    covers ``world.defending_team`` when present (the full team), so a body
    the caller's own list filter removed is still reached.
    """
    team = getattr(world, "defending_team", None) or source or []
    cleared = []
    for d in team:
        if d is None or id(d) in population_ids:
            continue
        if (getattr(d, "depth_side", None) in (-1, 1)
                or getattr(d, "frame_side", None) in (-1, 1)
                or getattr(d, "depth_role", None) is not None
                or bool(getattr(d, "_depth_seeded", False))):
            _clear_line_fields(d)
            cleared.append(d)
    return cleared


def _apply_published_sides(world, source, publication) -> list:
    """Re-stamp the published sides on the population; clear stale stamps.

    Called on EVERY publication — including a cache HIT.  A caller later in
    the same frame (the dynamic refit) deliberately clears ``depth_side``
    across a subset before re-running ``assign_depth_roles``; re-applying the
    frame's published sides here keeps the side-ownership invariant true for
    every caller in the frame WITHOUT re-solving the count.
    """
    population = publication.get("population") or []
    side_map = publication.get("side_map") or {}
    pop_ids = publication.get("population_ids") or frozenset()
    for d in population:
        side = side_map.get(id(d))
        if side in (-1, 1):
            d.depth_side = side
    _prune_observer_dicts(world, pop_ids)
    return _clear_stale_sides(world, source, pop_ids)


# The population-keyed observer dicts published by the spacing authority
# (see ``spacing_authority.py``).  ``frame_spacing_targets`` is the Y
# authority named by the Stage 4 ``spacing ownership`` contract, so a member
# that left the line must not survive in it.
#
# ``frame_width_targets`` is DELIBERATELY NOT pruned: the span contract
# (``span_contract.publish_span_contract`` -> ``side_widths``) sizes each
# side's ladder from it, and deleting a key mid-frame collapses a side's
# span to 0.0 (measured: ``SpanContractError: left span must be positive``).
# Width membership is owned by that contract, not by this authority.
_POPULATION_KEYED_DICTS = (
    "frame_spacing_targets",
)


def _prune_observer_dicts(world, population_ids) -> int:
    """Drop entries for bodies that are NOT in the population.

    This is the spacing half of the stale-membership elimination.  The
    spacing authority publishes ``world.frame_spacing_targets`` /
    ``world.frame_width_targets`` for the population it saw when it ran; if
    a body leaves the line LATER in the same frame (the tackle-state gate
    can add a holder mid-frame), those dicts would keep describing a
    defender set that no longer exists — the Stage 4 ``spacing ownership``
    divergence.  Pruning on every publication keeps the keys exactly equal
    to the ONE population.
    """
    pruned = 0
    for name in _POPULATION_KEYED_DICTS:
        dct = getattr(world, name, None)
        if not isinstance(dct, dict) or not dct:
            continue
        stale = [k for k in dct.keys() if k not in population_ids]
        if not stale:
            continue
        for k in stale:
            del dct[k]
        pruned += len(stale)
    return pruned


# ─────────────────────────────────────────────────────────────────────
# THE SINGLE PER-FRAME PUBLICATION
# ─────────────────────────────────────────────────────────────────────

def _source_defenders(world, defenders) -> list:
    """The population SOURCE: the full team when the world carries one.

    Falling back to ``world.defending_team`` is what makes a subset caller
    (the dynamic refit's collapse-locked pool) unable to shrink the count
    population — the defect D4 measured.
    """
    team = getattr(world, "defending_team", None)
    if team:
        return list(team)
    return list(defenders or [])


def _frame_count(world):
    return getattr(world, "frame_count", None)


def publish_frame_is_fresh(world) -> bool:
    """True when ``world.line_population`` was published for THIS frame."""
    pub = getattr(world, "_line_pop_publication", None)
    if not pub:
        return False
    return pub.get("frame") == _frame_count(world)


def publish_line_population(world, defenders, tuners, *,
                            ruck_y=None, force=False) -> dict:
    """Publish THE line population, its side split and its counts — ONCE.

    Args:
        world: WorldState (the source of ``defending_team`` / ``ruck_y_m``).
        defenders: the caller's list.  Used ONLY when the world carries no
            ``defending_team`` (verifier stubs) — the live engine always
            derives the population from the full team.
        tuners: DefenceTuners.
        ruck_y: optional ruck-Y override (verifier determinism).
        force: re-solve even when a fresh publication exists (used by the
            settled-geometry reband after it mutates roles).

    Returns the publication dict::

        {key, frame, population, population_ids, n_left, n_right, total,
         side_map, cleared, solve_index, ball_side, ball_side_bias}

    Side-effects (the ONLY writes this module performs):
        stamps ``d.depth_side`` on exactly the population;
        clears ``depth_side``/``frame_side``/``depth_role``/``_depth_seeded``
        on every non-population body in the team;
        publishes ``world.line_population`` / ``_ids`` / ``_frame``,
        ``world.published_count_left`` / ``_right`` / ``_total``, and the
        single-solve bookkeeping ``world.line_population_publish_count``.
    """
    source = _source_defenders(world, defenders)
    population = line_population(source)
    ruck_y = (float(getattr(world, "ruck_y_m", 35.0))
              if ruck_y is None else float(ruck_y))

    # Lazy imports (import-cycle law): defensive_split_spacing imports THIS
    # module from assign_split_sides, and ball_side_collapse imports the
    # depth-role module.  Resolving at call time keeps module init clean.
    from game.defence.defence_lvl_1.defensive_split_spacing import (
        required_side_counts,
        side_widths,
        side_band_centre,
        LEFT,
        RIGHT,
    )
    from game.defence.defence_lvl_1.ball_side_collapse import (
        resolve_ball_side_bias,
    )

    # ── BALL-SIDE BIAS — resolved ONCE, here, never per caller ──
    # The preamble, the dynamic refit and the reband must not be able to
    # publish a differently-split count for the same frame.  Resolving the
    # bias in the single authority is what makes the count a per-frame fact.
    ball_side, ball_side_bias = resolve_ball_side_bias(world, tuners)

    pop_ids = frozenset(id(d) for d in population)
    key = (_frame_count(world), pop_ids, round(ruck_y, 6),
           ball_side, int(ball_side_bias))

    cached = getattr(world, "_line_pop_publication", None)
    if (not force and cached is not None and cached.get("key") == key):
        # Cache HIT: the count is already this frame's single fact, but a
        # caller (the dynamic refit) may have cleared depth_side across the
        # population since it was published.  Re-apply the published sides so
        # the side-ownership invariant holds for EVERY caller in the frame.
        _apply_published_sides(world, source, cached)
        return cached

    n_left, n_right = required_side_counts(
        world, tuners, len(population), ruck_y=ruck_y,
        ball_side=ball_side, ball_side_bias=ball_side_bias,
    )
    # ── HARD TOTAL INTEGRITY ──
    # The published count may never disagree with the population size.  The
    # hysteresis fix inside decide_side_counts already guarantees this; this
    # re-normalisation is the belt-and-suspenders last word.
    n_left = max(0, min(int(n_left), len(population)))
    n_right = len(population) - n_left

    left_width, right_width = side_widths(world, tuners, ruck_y=ruck_y)
    centre_left = side_band_centre(LEFT, ruck_y, left_width, tuners)
    centre_right = side_band_centre(RIGHT, ruck_y, right_width, tuners)

    ordered = sorted(
        population,
        key=lambda d: (
            round(abs(float(d.y) - centre_left)
                  - abs(float(d.y) - centre_right), 6),
            getattr(d, "player_id", 0),
        ),
    )
    side_map = {}
    for i, d in enumerate(ordered):
        side = LEFT if i < n_left else RIGHT
        d.depth_side = side
        side_map[id(d)] = side

    # ── STALE-MEMBERSHIP ELIMINATION (runs on EVERY fresh publication) ──
    cleared = _clear_stale_sides(world, source, pop_ids)

    frame = _frame_count(world)
    prev_frame = getattr(world, "line_population_frame", None)
    solve_index = (
        int(getattr(world, "line_population_publish_count", 0)) + 1
        if prev_frame == frame else 1
    )

    publication = {
        "key": key,
        "frame": frame,
        "population": population,
        "population_ids": pop_ids,
        "n_left": int(n_left),
        "n_right": int(n_right),
        "total": len(population),
        "side_map": side_map,
        "cleared": cleared,
        "solve_index": solve_index,
        "ball_side": ball_side,
        "ball_side_bias": int(ball_side_bias),
    }

    world._line_pop_publication = publication
    world.line_population = population
    world.line_population_ids = pop_ids
    world.line_population_frame = frame
    world.line_population_publish_count = solve_index
    world.published_count_left = int(n_left)
    world.published_count_right = int(n_right)
    world.published_count_total = len(population)
    return publication


def trim_observer_dicts(world) -> int:
    """Trim the population-keyed observer dicts to the PUBLISHED population.

    Called as the LAST population write of every frame.  Producers inside the
    frame (the TES drive structure writes an entry for its TACKLER_* actors
    during TACKLE_CONTACT) may add keys that are not line bodies; the observer
    contract is that the frame ends with spacing ownership == the ONE
    population, so those keys are dropped at the frame boundary.
    """
    pop_ids = getattr(world, "line_population_ids", None)
    if not pop_ids:
        return 0
    return _prune_observer_dicts(world, pop_ids)


def observer_dicts_missing_members(world) -> bool:
    """True when a POPULATION member has no spacing entry this frame.

    The mirror of :func:`trim_observer_dicts`: a body that (re-)entered the
    line after the spacing producer ran has no entry, so the observer dict is
    short of the population and the frame's geometry must be rebuilt.
    """
    dct = getattr(world, "frame_spacing_targets", None)
    if not isinstance(dct, dict) or not dct:
        return False
    pop_ids = getattr(world, "line_population_ids", None)
    if not pop_ids:
        return False
    return bool(pop_ids - set(dct.keys()))


def published_population(world) -> list:
    """Return the published line population (or ``[]``)."""
    return list(getattr(world, "line_population", None) or [])


def published_counts(world) -> tuple:
    """Return the published ``(n_left, n_right)`` (or ``(None, None)``)."""
    n_left = getattr(world, "published_count_left", None)
    n_right = getattr(world, "published_count_right", None)
    if n_left is None or n_right is None:
        return None, None
    return int(n_left), int(n_right)
