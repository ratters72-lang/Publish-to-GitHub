"""verify_mmdl_fullback_exclusion.py — Fullback MMDL movement-state contract.

B2b LAW (2026-09-16): the dynamic fullback is a dedicated GEOMETRY actor whose
movement_state contract at the MMDL layer is DRIFT-or-CHASE:

  * DRIFT — the default.  The actor glides to its backfield anchor.  It must
            NEVER be routed into RETREAT / HOLD / SLIDE / SHOOT, no matter how
            hostile the inputs are: bc is None, TES active + tackler, marker
            role, a non-compliant retreat position, a bogus PRIMARY /
            SECONDARY depth_class, an active decision lock, or the
            deep-passive HOLD branch.
  * CHASE — the intended breakaway response.  Once a GENUINE LINE BREAK
            exists (carrier past the defensive anchor) OR the carrier comes
            inside the tackle radius, the last defender must detect and
            pursue the carrier: "the fullback behaves like any other defender
            during a break".  CHASE is therefore REQUIRED in those two
            regimes, not forbidden.

The prohibitions (RETREAT / HOLD / SLIDE / SHOOT) hold in BOTH regimes, and
the guard stays fullback-specific: an identical non-fullback defender still
receives its normal MMDL state.

HISTORY: this verifier previously asserted "the fullback is ALWAYS DRIFT".
Every scenario it built put the carrier past the line, so under the
DRIFT-or-CHASE contract it was asserting the opposite of the intended
behaviour.  It now exercises BOTH regimes explicitly.
"""
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from game.defence.defence_lvl_1.movement_mode_decision import decide_movement_state
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
from player.movement_states import MovementState

# ── GEOMETRY FOR THE TWO REGIMES ──────────────────────────────────────
# NO BREAK : ruck_x = 100 -> line anchor 110; the carrier at 105 is behind it.
# BREAK    : ruck_x = 30  -> line anchor 40;  the carrier at 55 is past 41.
RUCK_NO_BREAK = 100.0
RUCK_BREAK = 30.0
BC_NO_BREAK_X = 105.0
BC_BREAK_X = 55.0

# The states that are NEVER legal for the fullback, in either regime.
FORBIDDEN = (
    MovementState.RETREAT,
    MovementState.HOLD,
    MovementState.SLIDE,
    MovementState.SHOOT,
)

failures = []
TUNERS = DefenceTuners()


def check(name, actual, expected):
    status = "PASS" if actual == expected else "FAIL"
    print(f"  [{status}] {name}" + ("" if actual == expected
                                    else f" — expected {expected!r}, got {actual!r}"))
    if actual != expected:
        failures.append(f"{name}: expected {expected!r}, got {actual!r}")


def check_not_forbidden(name, actual):
    status = "PASS" if actual not in FORBIDDEN else "FAIL"
    print(f"  [{status}] {name}" + ("" if actual not in FORBIDDEN
                                    else f" — got forbidden {actual!r}"))
    if actual in FORBIDDEN:
        failures.append(f"{name}: got forbidden {actual!r}")


def make_defender(x, y, depth_class="PASSIVE", player_id=1, role=None,
                  fullback=False, is_tackler=False, movement_state=None):
    """Mirror the tmp/verify_mmdl.py helper, plus fullback/tackler flags."""
    return NS(
        x=x, y=y, depth_class=depth_class, player_id=player_id,
        defender_role=role, movement_state=movement_state,
        is_tackler=is_tackler, fullback_role_active=fullback,
    )


def make_bc(x, y):
    return NS(x=x, y=y)


def make_world(ruck_x=None, tes_active=False):
    return NS(ruck_x_m=ruck_x, tes_active=tes_active)


def seed_line_body(d):
    """Make *d* a HEALTHY line body for the MMDL.

    ``seed_hold.is_seed_stale`` force-holds any line body whose spacing-Y /
    depth-role seed is stale, and a bare stub counts as stale: it needs a
    lane ``depth_role``, a ``depth_side``, ``_depth_seeded`` and both
    ``role_depth_target_*`` fields.  Without this the CONTROL defenders would
    all resolve HOLD and the controls would prove nothing.

    (The fullback stubs need no seeding: ``is_seed_stale`` returns False
    immediately for a ``fullback_role_active`` body.)
    """
    d.depth_role = "A"
    d.depth_side = 1
    d._depth_seeded = True
    d.role_depth_target_x = float(d.x)
    d.role_depth_target_y = float(d.y)
    d.frozen = False
    d.freeze_timer = 0.0
    d.tackle_state_active = False
    return d


def seal_line_seed(world, d):
    """Stamp *world* + *d* so the SPACING half of ``is_seed_stale`` is healthy.

    The spacing half requires, in order:
      * ``frame_geometry_is_fresh(world)`` -> ``frame_geometry_ready`` is True
        AND ``frame_geometry_frame == frame_count``;
      * a non-empty ``world.frame_spacing_targets`` dict containing ``id(d)``;
      * a non-None ``d.frame_spacing_y``.

    Without all four a seeded line body would STILL be force-held, which is
    exactly why the controls must seal the frame as well as the depth seed.
    """
    world.frame_count = getattr(world, "frame_count", 0)
    world.frame_geometry_ready = True
    world.frame_geometry_frame = world.frame_count
    world.frame_spacing_targets = {id(d): float(getattr(d, "y", 35.0))}
    d.frame_spacing_y = float(getattr(d, "y", 35.0))
    return world


def both_regimes(label, make_fb, expect_no_break, expect_break,
                 fb_x=60.0, fb_y=35.0):
    """Run one hostile input in BOTH regimes and assert the state for each.

    *make_fb* returns a fresh fullback stub so the two runs stay independent.
    """
    fb_nb = make_fb(fb_x, fb_y)
    actual_nb = decide_movement_state(
        make_world(ruck_x=RUCK_NO_BREAK), fb_nb,
        make_bc(BC_NO_BREAK_X, 35.0), TUNERS,
    )
    check(f"{label} [no break]", actual_nb, expect_no_break)
    check_not_forbidden(f"{label} [no break] never RETREAT/HOLD/SLIDE/SHOOT",
                        actual_nb)

    fb_b = make_fb(fb_x, fb_y)
    actual_b = decide_movement_state(
        make_world(ruck_x=RUCK_BREAK), fb_b,
        make_bc(BC_BREAK_X, 35.0), TUNERS,
    )
    check(f"{label} [genuine break]", actual_b, expect_break)
    check_not_forbidden(f"{label} [genuine break] never RETREAT/HOLD/SLIDE/SHOOT",
                        actual_b)


print("=" * 72)
print("MMDL FULLBACK MOVEMENT-STATE CONTRACT (DRIFT-or-CHASE)")
print("=" * 72)

# ── 1. Plain PASSIVE fullback, outside the tackle radius ──────────────
# The actor must glide (DRIFT) normally and pursue (CHASE) on a break.
print("\n[1] Plain PASSIVE fullback")
both_regimes(
    "plain passive fullback",
    lambda x, y: make_defender(x, y, "PASSIVE", player_id=1, fullback=True),
    MovementState.DRIFT, MovementState.CHASE,
)

# ── 2. Tackle-radius proximity with NO break -> CHASE is REQUIRED ─────
print("\n[2] Tackle-radius proximity (no break) still closes on the carrier")
fb_radius = make_defender(104.0, 35.0, "PASSIVE", player_id=1, fullback=True)
# ruck 100 -> anchor 110, carrier at 105: no break, and 1.0 m away (< radius).
check("fullback inside the tackle radius without a break",
      decide_movement_state(make_world(ruck_x=RUCK_NO_BREAK), fb_radius,
                            make_bc(BC_NO_BREAK_X, 35.0), TUNERS),
      MovementState.CHASE)

# ── 3. bc is None -> the fullback always DRIFTs (no carrier to read) ──
print("\n[3] No ball carrier")
fb_nobc = make_defender(60.0, 35.0, "PASSIVE", player_id=1, fullback=True)
check("fb_no_bc (break-regime world, no carrier)",
      decide_movement_state(make_world(ruck_x=RUCK_BREAK), fb_nobc, None, TUNERS),
      MovementState.DRIFT)
fb_nobc2 = make_defender(60.0, 35.0, "PASSIVE", player_id=1, fullback=True)
check("fb_no_bc (no-break-regime world, no carrier)",
      decide_movement_state(make_world(ruck_x=RUCK_NO_BREAK), fb_nobc2, None, TUNERS),
      MovementState.DRIFT)

# ── 4. TES active + is_tackler -> the FB branch precedes the TES branch
print("\n[4] TES active + registered tackler")
both_regimes(
    "tes tackler (stale CHASE movement_state)",
    lambda x, y: make_defender(x, y, "PRIMARY", player_id=1, fullback=True,
                               is_tackler=True, movement_state=MovementState.CHASE),
    MovementState.DRIFT, MovementState.CHASE,
)

# ── 5. Marker role -> the FB must never HOLD tight to the ruck ───────
print("\n[5] Marker role")
both_regimes(
    "marker-role fullback",
    lambda x, y: make_defender(x, y, "MARKER", player_id=1, role="MARKER_A",
                               fullback=True),
    MovementState.DRIFT, MovementState.CHASE,
)

# ── 6. Non-compliant retreat position -> the FB must never RETREAT ───
print("\n[6] Non-compliant retreat position")
both_regimes(
    "non-compliant retreat-band fullback",
    lambda x, y: make_defender(x, y, "PASSIVE", player_id=1, fullback=True),
    MovementState.DRIFT, MovementState.CHASE,
    fb_x=35.0, fb_y=35.0,
)

# ── 7. Bogus PRIMARY depth_class, outside the tackle radius ──────────
print("\n[7] Bogus PRIMARY depth_class (outside the tackle radius)")
both_regimes(
    "bogus PRIMARY fullback",
    lambda x, y: make_defender(x, y, "PRIMARY", player_id=1, fullback=True),
    MovementState.DRIFT, MovementState.CHASE,
    fb_x=60.0, fb_y=33.0,
)

# ── 8. Bogus SECONDARY depth_class, far from the carrier ────────────
print("\n[8] Bogus SECONDARY depth_class")
both_regimes(
    "bogus SECONDARY fullback",
    lambda x, y: make_defender(x, y, "SECONDARY", player_id=1, fullback=True),
    MovementState.DRIFT, MovementState.CHASE,
    fb_x=10.0, fb_y=10.0,
)

# ── 9. Decision lock -> a locked state must not survive on the FB ───
print("\n[9] Active decision lock")
fb_lock_nb = make_defender(60.0, 35.0, "PRIMARY", player_id=1, fullback=True,
                           movement_state=MovementState.CHASE)
fb_lock_nb._mmdl_state_until_frame = 9999
check("locked fullback [no break] still DRIFTs",
      decide_movement_state(make_world(ruck_x=RUCK_NO_BREAK), fb_lock_nb,
                            make_bc(BC_NO_BREAK_X, 35.0), TUNERS),
      MovementState.DRIFT)
fb_lock_b = make_defender(60.0, 35.0, "PRIMARY", player_id=1, fullback=True,
                          movement_state=MovementState.CHASE)
fb_lock_b._mmdl_state_until_frame = 9999
check("locked fullback [genuine break] CHASEs",
      decide_movement_state(make_world(ruck_x=RUCK_BREAK), fb_lock_b,
                            make_bc(BC_BREAK_X, 35.0), TUNERS),
      MovementState.CHASE)

# ── 10. Deep-passive HOLD branch -> the FB must never HOLD ──────────
print("\n[10] Deep-passive HOLD branch")
both_regimes(
    "deep-passive fullback",
    lambda x, y: make_defender(x, y, "PASSIVE", player_id=1, fullback=True),
    MovementState.DRIFT, MovementState.CHASE,
    fb_x=20.0, fb_y=10.0,
)

# ── 11. Tuner-less safety -> the guard must fire before tuner reads ─
print("\n[11] Tuner-less safety (tuners=None)")
fb_notuner = make_defender(60.0, 35.0, "PASSIVE", player_id=1, fullback=True)
check("fb_no_tuners [no break]",
      decide_movement_state(make_world(ruck_x=RUCK_NO_BREAK), fb_notuner,
                            make_bc(BC_NO_BREAK_X, 35.0), None),
      MovementState.DRIFT)

# ── 12. CONTROLS — non-fullback bodies keep their normal MMDL state ─
# These prove the guard is fullback-specific and changed no line behaviour.
print("\n[12] Controls — identical non-fullback defenders are unaffected")
bc_sprint = make_bc(BC_BREAK_X, 35.0)

# 12a. THE SEED-STALE LOCKDOWN ITSELF.  An UNSEEDED non-fullback line stub is
# force-held by seed_hold, while the tagged fullback in identical geometry is
# exempt and returns DRIFT/CHASE.  This is the sharpest single proof that the
# guard is fullback-specific, and it is why every control below is seeded.
ctrl_bare = make_defender(60.0, 35.0, "PASSIVE", player_id=3)
check("unseeded non-fullback stub resolves HOLD (seed-stale lockdown)",
      decide_movement_state(make_world(ruck_x=RUCK_NO_BREAK), ctrl_bare,
                            make_bc(BC_NO_BREAK_X, 35.0), TUNERS),
      MovementState.HOLD)
fb_bare = make_defender(60.0, 35.0, "PASSIVE", player_id=4, fullback=True)
check("tagged fullback in IDENTICAL geometry does NOT hold",
      decide_movement_state(make_world(ruck_x=RUCK_NO_BREAK), fb_bare,
                            make_bc(BC_NO_BREAK_X, 35.0), TUNERS),
      MovementState.DRIFT)

# 12b. Line-retreat branch — a non-compliant PASSIVE body behind the line.
ctrl_retreat = seed_line_body(
    make_defender(35.0, 35.0, "PASSIVE", player_id=2))
check("ctrl_line_retreat_still_active",
      decide_movement_state(seal_line_seed(make_world(ruck_x=RUCK_BREAK), ctrl_retreat),
                            ctrl_retreat, bc_sprint, TUNERS),
      MovementState.RETREAT)

# 12c. No-carrier RETREAT branch.
ctrl_nobc = seed_line_body(
    make_defender(60.0, 35.0, "PASSIVE", player_id=2))
check("ctrl_no_bc_still_retreat",
      decide_movement_state(seal_line_seed(make_world(), ctrl_nobc),
                            ctrl_nobc, None, TUNERS),
      MovementState.RETREAT)

# 12d. TES tackler keeps its current state (markers/tacklers are non-line
# roles, so they need no seed at all).
ctrl_tes = seed_line_body(
    make_defender(60.0, 35.0, "PRIMARY", player_id=2, is_tackler=True,
                  movement_state=MovementState.CHASE))
check("ctrl_tes_tackler_keeps_state",
      decide_movement_state(seal_line_seed(make_world(tes_active=True), ctrl_tes),
                            ctrl_tes, bc_sprint, TUNERS),
      MovementState.CHASE)

# 12e. Marker role still HOLDs tight to the ruck.
ctrl_marker = make_defender(60.0, 35.0, "MARKER", player_id=2, role="MARKER_A")
check("ctrl_marker_still_hold",
      decide_movement_state(make_world(ruck_x=RUCK_BREAK), ctrl_marker,
                            bc_sprint, TUNERS),
      MovementState.HOLD)

# 12f. The same non-compliant position still RETREATs for a normal body.
ctrl_noncompliant = seed_line_body(
    make_defender(35.0, 35.0, "PASSIVE", player_id=2))
check("ctrl_non_compliant_still_retreat",
      decide_movement_state(
          seal_line_seed(make_world(ruck_x=RUCK_BREAK), ctrl_noncompliant),
          ctrl_noncompliant, bc_sprint, TUNERS),
      MovementState.RETREAT)

# 12g. A close PRIMARY still COMMITS to the carrier.  The expected state is
# CHASE, not SHOOT: the tackle-radius chase switch (2026-09-05) routes a
# genuine line body inside the commit radius into forward closing.  What the
# control proves is that the fullback guard did not suppress engagement.
ctrl_primary = seed_line_body(
    make_defender(BC_BREAK_X - 6.0, 33.0, "PRIMARY", player_id=2))
check("ctrl_primary_still_commits (CHASE)",
      decide_movement_state(
          seal_line_seed(make_world(ruck_x=RUCK_BREAK), ctrl_primary),
          ctrl_primary, bc_sprint, TUNERS),
      MovementState.CHASE)

# 12h. A distant SECONDARY still CHASEs.
ctrl_secondary = seed_line_body(
    make_defender(10.0, 10.0, "SECONDARY", player_id=2))
check("ctrl_secondary_still_chase",
      decide_movement_state(seal_line_seed(make_world(ruck_x=5.0), ctrl_secondary),
                            ctrl_secondary, make_bc(40.0, 10.0), TUNERS),
      MovementState.CHASE)

# ── REPORT ──────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print(f"scenario: NO BREAK ruck_x={RUCK_NO_BREAK} (anchor "
      f"{RUCK_NO_BREAK + 10}) carrier x={BC_NO_BREAK_X}  |  "
      f"BREAK ruck_x={RUCK_BREAK} (anchor {RUCK_BREAK + 10}) "
      f"carrier x={BC_BREAK_X}")
if failures:
    print(f"RESULT: {len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
sys.exit(0)
