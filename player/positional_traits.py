"""player/positional_traits.py — THE POSITIONAL ATTRIBUTE MAPPER (Stage 4).

WHAT THIS FILE IS
-----------------
The PURE MAP from a player's positional JERSEY NUMBER to the three physics
traits the contact model reads off a live body.  It declares:

  * ``PositionalTraits`` — ONE position's profile: a weight, a Fend
    multiplier and a Tackle-skill multiplier;
  * the two DECLARED profiles — backs/halves and forwards/props;
  * ``traits_for_jersey`` — the total, pure, raising-nothing lookup;
  * ``apply_positional_traits`` — THE ONE WRITE, which stamps a profile onto a
    live body so the extractors that already exist can find it.

WHY IT EXISTS
-------------
Stages 3.1-3.3 built the entire trait pipeline and then left it NEUTRAL.
Every consumer is already a ``getattr`` read of a slot no live body fills:

    CarrierSnapshot.of(bc)                          -> getattr(bc, "fend_trait", 1.0)
    contest_preservation._neutral_trait(body, ...)   -> getattr(body, "tackle_skill_trait", 1.0)
    tes_multi_body._mass_of(body)                    -> getattr(body, "mass_kg", 90.0)
    tes._make_ctx(p)                                 -> getattr(p, "mass_kg", 90.0)

so every live body reports the fallback 1.0 / 1.0 / 90.0 and
``resolve_collision_state`` sees one undifferentiated physical band.  This
module is the missing INITIALISATION step: it reads a body's positional jersey
and stamps the non-neutral profile onto it.

NOTHING DOWNSTREAM CHANGES.  The mapper does not touch the extractors, the
wrap model, the discriminator or any consumer — it only fills the three slots
those readers already look for.  A body the mapper does not stamp (an
unstamped ``Player``, jersey 0, a jersey outside every declared band) keeps
absence, and absence IS the neutral reading, so such a body remains
bit-identical to the pre-Stage-4 engine.

THE TWO PROFILES
----------------
    Forwards  (8-13, plus the bench props 14-17)
        weight 105.0 kg   fend 1.15   tackle-skill 1.30
        A heavier, lower-fend body that WRAPS hard.

    Backs/halves  (1-7)
        weight 88.0 kg    fend 1.35   tackle-skill 0.85
        A lighter, higher-fend body that CARRIES hard and wraps soft.

Those numbers are deliberately close on the CARRIER side and far apart on the
WRAP side:

    carrier momentum multiplier  = weight x fend
        back     88 x 1.35 = 118.80
        forward 105 x 1.15 = 120.75      (within 2% of each other)

    per-body wrap resistance     = weight x tackle-skill
        back     88 x 0.85 =  74.80
        forward 105 x 1.30 = 136.50      (1.8x the back)

which is what makes the GRADIENT positional rather than uniform: a
single-forward wrap can be cleared by a running carrier while a CLUSTER of
forwards COMPOUNDS, because each forward contributes 1.8x the resistance of a
back to the same collaborative sum.

WEIGHT IS STAMPED AS ``mass_kg``
--------------------------------
``weight`` is written to the ``mass_kg`` slot, because that is the field every
TES and wrap-model consumer already reads.  It is NOT written to
``Player.mass``: that attribute is the collision SOLVER's normalised inertia
(``CollisionBody.mass``, 1.0; the kickoff freeze raises it to 999999), and
re-scaling it would change the collision solver itself rather than the contact
model the profiles describe.  One field, one meaning.

WHAT IS DELIBERATELY NOT STAMPED
--------------------------------
``core_strength_trait`` and ``tackle_break_trait`` are the two remaining
Stage 3.1 slots.  Neither is consumed by any consumer today, so the mapper
leaves both ABSENT — filling a slot nothing reads would manufacture a
measurement that no physics uses, and would silently change what the record
reports about a body's declared skill.

THE A/B LEVER
-------------
``RLM_POSITIONAL_TRAITS=0`` disarms the mapper, so the initialisation step
writes nothing and every body falls back to its neutral reading.  That is how
the gradient shift is MEASURED rather than asserted: the fixture can run the
same rollout with the lever on and off and show the bands collapsing back to a
single band.  Unset is ON, which is the feature.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    "POSITIONAL_TRAITS_ENV_VAR",
    "WEIGHT_KG_ATTR",
    "FEND_TRAIT_ATTR",
    "TACKLE_SKILL_TRAIT_ATTR",
    "NEUTRAL_TRAIT",
    "POSITION_BACK",
    "POSITION_FORWARD",
    "BACK_JERSEYS",
    "FORWARD_JERSEYS",
    "BENCH_PROP_JERSEYS",
    "BACK_PROFILE",
    "FORWARD_PROFILE",
    "PositionalTraits",
    "positional_traits_enabled",
    "traits_for_jersey",
    "is_back_jersey",
    "is_forward_jersey",
    "apply_positional_traits",
    "declared_profiles",
    "declared_content",
    "declared_gates",
]


# ─────────────────────────────────────────────────────────────────────
# THE A/B LEVER (opt-OUT, default ON)
# ─────────────────────────────────────────────────────────────────────
# ON is the feature: a live body carries the physics traits its position
# implies.  ``RLM_POSITIONAL_TRAITS=0`` restores the all-neutral engine so the
# gradient can be MEASURED against it.  It is read in exactly one place
# (``positional_traits_enabled``) and, with it off, ``apply_positional_traits``
# writes nothing at all — so every downstream reader takes its pre-Stage-4
# fallback and the engine is bit-identical to the stage before.
POSITIONAL_TRAITS_ENV_VAR: str = "RLM_POSITIONAL_TRAITS"


# ─────────────────────────────────────────────────────────────────────
# THE THREE LIVE SLOTS THE MAPPER FILLS
# ─────────────────────────────────────────────────────────────────────
# The attribute NAMES are declared here rather than written inline at the call
# site, so the mapper and any verifier naming the slots read one source of
# truth.  Each is the exact name the consumer's ``getattr`` already asks for.
WEIGHT_KG_ATTR: str = "mass_kg"
FEND_TRAIT_ATTR: str = "fend_trait"
TACKLE_SKILL_TRAIT_ATTR: str = "tackle_skill_trait"

# The identity of a percentage multiplier.  Named so the module states what
# "neutral" means rather than repeating a bare 1.0, and so a verifier can
# reference it.
NEUTRAL_TRAIT: float = 1.0


# ─────────────────────────────────────────────────────────────────────
# THE POSITION LABELS
# ─────────────────────────────────────────────────────────────────────
POSITION_BACK: str = "back"
POSITION_FORWARD: str = "forward"


# ─────────────────────────────────────────────────────────────────────
# THE DECLARED JERSEY BANDS
# ─────────────────────────────────────────────────────────────────────
# BACKS / HALVES: 1-7.  The spine — fullback, wingers, centres, five-eighth,
# halfback and hooker.
BACK_JERSEYS: frozenset = frozenset({1, 2, 3, 4, 5, 6, 7})

# FRONTS: 8-13.  Props, hooker-as-forward, back row and lock.
FORWARD_JERSEYS: frozenset = frozenset({8, 9, 10, 11, 12, 13})

# BENCH PROPS: the interchange.  In a 13-a-side team these jerseys do not
# exist, and this engine's team generator stamps 1..13 only — so this band is
# INERT today and declared for the extended squad a later stage may field.  A
# bench forward is a forward, so it takes the forward profile.
BENCH_PROP_JERSEYS: frozenset = frozenset({14, 15, 16, 17})


# ─────────────────────────────────────────────────────────────────────
# ONE POSITION'S PHYSICS PROFILE
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PositionalTraits:
    """The three physics traits ONE position contributes to a contact.

    Frozen: a profile is a DECLARATION about a position, not per-body state,
    so no caller may edit the profile it looked up.

    Fields are named after what they MEAN physically rather than after the
    live attribute each is written to, so the declaration reads as physics and
    the wiring (``apply_positional_traits``) is the only place that knows where
    each one lands on a body.
    """

    position: str
    """``POSITION_BACK`` / ``POSITION_FORWARD`` — the band this profile is for."""

    jerseys: tuple
    """The declared jersey numbers this profile covers (ordered, for reporting)."""

    weight_kg: float
    """Body mass in kg.  Written to ``mass_kg`` — the field TES and the wrap
    model already read — and to nothing else."""

    fend_trait: float
    """The carrier's ESCAPE multiplier.  Written to ``fend_trait``, where
    ``CarrierSnapshot.of`` reads it into the fend momentum curve."""

    tackle_skill_trait: float
    """The defender's RESISTANCE multiplier.  Written to
    ``tackle_skill_trait``, where the preservation layer's row builder reads it
    into the defensive skill resistance curve."""


# ─────────────────────────────────────────────────────────────────────
# THE TWO DECLARED PROFILES
# ─────────────────────────────────────────────────────────────────────
# Declared as module constants so a verifier reads the SAME numbers the mapper
# applies — a test that re-typed 105.0 could agree with a drifted mapper.
BACK_PROFILE: PositionalTraits = PositionalTraits(
    position=POSITION_BACK,
    jerseys=tuple(sorted(BACK_JERSEYS)),
    weight_kg=88.0,
    fend_trait=1.35,
    tackle_skill_trait=0.85,
)

FORWARD_PROFILE: PositionalTraits = PositionalTraits(
    position=POSITION_FORWARD,
    jerseys=tuple(sorted(FORWARD_JERSEYS | BENCH_PROP_JERSEYS)),
    weight_kg=105.0,
    fend_trait=1.15,
    tackle_skill_trait=1.30,
)


# ─────────────────────────────────────────────────────────────────────
# THE LEVER
# ─────────────────────────────────────────────────────────────────────

def positional_traits_enabled() -> bool:
    """True unless the positional mapper is explicitly disarmed.

    ON by default: giving a live body the physics its position implies IS the
    feature.  ``RLM_POSITIONAL_TRAITS=0`` restores the all-neutral engine so
    the gradient can be measured against it.  This is the ONLY read of the
    lever in the module.
    """
    return os.environ.get(POSITIONAL_TRAITS_ENV_VAR, "1").strip().lower() not in (
        "0", "false", "off", "no",
    )


# ─────────────────────────────────────────────────────────────────────
# THE LOOKUP (total, pure, never raises)
# ─────────────────────────────────────────────────────────────────────

def traits_for_jersey(jersey_number) -> "PositionalTraits | None":
    """The declared profile for *jersey_number*, or ``None`` when unmapped.

    TOTAL AND PURE.  A jersey outside every declared band — the unstamped
    ``0`` every bare ``Player`` carries, an unrecognised squad number, a
    non-integer, ``None`` — reports ``None``, which is the honest "this body
    has no declared position" rather than an invented profile.  A caller that
    receives ``None`` must leave the body alone, so absence stays the neutral
    reading every consumer already falls back to.
    """
    try:
        jersey = int(jersey_number)
    except (TypeError, ValueError):
        return None
    if jersey in BACK_JERSEYS:
        return BACK_PROFILE
    if jersey in FORWARD_JERSEYS or jersey in BENCH_PROP_JERSEYS:
        return FORWARD_PROFILE
    return None


def is_back_jersey(jersey_number) -> bool:
    """True when *jersey_number* is a declared backs/halves number."""
    return traits_for_jersey(jersey_number) is BACK_PROFILE


def is_forward_jersey(jersey_number) -> bool:
    """True when *jersey_number* is a declared forwards/props number."""
    return traits_for_jersey(jersey_number) is FORWARD_PROFILE


# ─────────────────────────────────────────────────────────────────────
# THE ONE WRITE
# ─────────────────────────────────────────────────────────────────────

def apply_positional_traits(player) -> bool:
    """Stamp *player*'s positional physics traits onto it.  THE one write.

    Reads the body's positional jersey, looks up its declared profile and
    stores the three traits on the three slots the contact model already reads:

        player.mass_kg            <- profile.weight_kg
        player.fend_trait         <- profile.fend_trait
        player.tackle_skill_trait <- profile.tackle_skill_trait

    Returns True when a profile was applied and False when nothing was written.
    False covers three distinct cases, and all three are correct:

      * the lever is disarmed (``RLM_POSITIONAL_TRAITS=0``);
      * the body carries no positional jersey (the unstamped ``0`` every bare
        ``Player`` has, and every verification stub);
      * the body's jersey is outside every declared band.

    In every False case NOTHING is written, so the body keeps absence — and
    absence is precisely the neutral reading ``CarrierSnapshot.of`` and the
    preservation layer's row builder fall back to.  That is what keeps a frame
    that never ran the team generator bit-identical to the engine before this
    stage, and it is deliberate: the strong form of "this stage adds nothing to
    an unmapped body".

    Never raises.  A body whose jersey is unreadable, or that refuses an
    attribute store, simply reports False.
    """
    if not positional_traits_enabled():
        return False
    profile = traits_for_jersey(getattr(player, "jersey_number", None))
    if profile is None:
        return False
    try:
        setattr(player, WEIGHT_KG_ATTR, float(profile.weight_kg))
        setattr(player, FEND_TRAIT_ATTR, float(profile.fend_trait))
        setattr(
            player,
            TACKLE_SKILL_TRAIT_ATTR,
            float(profile.tackle_skill_trait),
        )
    except Exception:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────
# REPORTING / DECLARED CONTENT
# ─────────────────────────────────────────────────────────────────────

def declared_profiles() -> tuple:
    """The two declared profiles in declaration order (backs, then forwards)."""
    return (BACK_PROFILE, FORWARD_PROFILE)


def declared_content() -> tuple:
    """The DECLARED content contract: what the mapper fills and where.

    Declarative only — nothing reads this at run time.  Each entry is
    ``(meaning, live_attribute, source_field)``, so the mapping from physics to
    the live slot is stated in code as well as in the document.
    """
    return (
        ("weight", WEIGHT_KG_ATTR, "weight_kg"),
        ("fend", FEND_TRAIT_ATTR, "fend_trait"),
        ("tackle_skill", TACKLE_SKILL_TRAIT_ATTR, "tackle_skill_trait"),
    )


def declared_gates() -> tuple:
    """The DECLARED gates the mapper must satisfy.  Declarative only.

    Each entry is ``(name, description)``; a verifier asserts them against the
    live mapper rather than trusting this list.
    """
    return (
        ("G1_jersey_bands_are_disjoint",
         "no jersey number belongs to both the back and the forward band"),
        ("G2_backs_are_light_high_fend_soft_wrap",
         "jersey 1-7 read weight 88.0, fend 1.35, tackle-skill 0.85"),
        ("G3_forwards_are_heavy_low_fend_hard_wrap",
         "jersey 8-13 and the bench props 14-17 read weight 105.0, "
         "fend 1.15, tackle-skill 1.30"),
        ("G4_unmapped_is_absent_not_neutral",
         "an unmapped jersey (0, negative, 18+, None, non-integer) applies "
         "nothing at all, so the body keeps the extractors' own neutral "
         "fallback"),
        ("G5_the_lever_disarms_the_write",
         "RLM_POSITIONAL_TRAITS=0 makes apply_positional_traits a no-op on a "
         "mapped body"),
        ("G6_only_the_three_declared_slots_are_written",
         "apply_positional_traits stores mass_kg / fend_trait / "
         "tackle_skill_trait and no other attribute, leaving "
         "core_strength_trait and tackle_break_trait absent"),
        ("G7_the_write_is_total",
         "apply_positional_traits never raises, on any body"),
    )
