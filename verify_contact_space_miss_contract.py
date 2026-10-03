"""verify_contact_space_miss_contract.py — CONTRACT for the CONTACT_SPACE_MISS path.

WHAT IS ASSERTED
----------------
A1. ``combined_body_radius`` == ``r_carrier + r_defender`` (fallback 0.55).
A2. ``is_contact_space_miss`` is TRUE at ``separation == combined`` (the exact
    ``<='' predicate the task specifies) and FALSE just above it.
A3. ``separation`` measures centre-to-centre distance.

B. NO-OP OUTSIDE CONTACT SPACE (the preservation clause).  For a pair whose
   separation exceeds the combined radius, ``apply_contact_space_miss`` writes
   NOTHING: ``applied`` is False and x/y/vx/vy/freeze_timer are bit-identical
   before and after.

C. THE REACTION FIRES INSIDE CONTACT SPACE and touches BOTH bodies: each is
   given a bounded ballistic ARC (``Rebound``) — the carrier is deflected away
   and pays a capped slice of speed; the defender is thrown the other way and
   staggered.  The two arcs OPPOSE each other, so the pair separates.

C2. THE ARC ITSELF: its per-frame steps sum EXACTLY to its declared total,
   decay monotonically (largest step first), stop after its last frame, and
   never step twice within one frame (the idempotence guard).

D. DETERMINISM: two identical scenarios produce identical results (no RNG).

E. BOUNDS: the declared arc TOTAL is <= its cap (*not* one frame's step), every
   reported velocity delta is <= ``VELOCITY_DELTA_MAX_MPS``, and the module
   itself writes no position at all.

E2. THE ARC IN THE REAL INTEGRATOR: driven through ``Player.update`` itself,
   the arc travels exactly its declared total, stops on its last frame, honours
   the field clamp, and suspends the body's AI seeking for its duration.

F. THE CARRIER PAYS BUT IS NOT BEATEN: the separation never shrinks, the
   carrier's speed falls by at most ``CARRIER_SPEED_LOSS_MAX`` (a contact that
   did not stick still costs the ball carrier), and the defender's arc points
   AWAY from the carrier.

G. THE GATE: ``RLM_CONTACT_SPACE_MISS=0`` disarms the path; the default is
   armed.

H. DECIDER INTEGRATION: with a stubbed ``_check_pre_tackle`` returning MISS,
   ``contact_authority.evaluate_contact`` records ONE ``CONTACT_SPACE_MISS``
   event on the decision when the pair is in contact space, records NONE when
   it is not, and still forms a CONTACT when a defender returns CONTACT
   (formation is unchanged).

I. SOURCE ASSERTIONS (the task's "leave unchanged" clause, machine-checked):
   the ``_check_pre_tackle`` thresholds (0.3 / 0.6 / 0.75), the tackle-role
   initiators {PRIMARY, SECONDARY}, and the ``_check_pre_tackle`` score
   expression are unchanged; the reaction is wired into the MISS branch BEFORE
   the existing miss handling.

Run:  python verify_contact_space_miss_contract.py
"""

import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from player.collision_body import CollisionBody
from game.physics import contact_space_miss as csm
from game.defence.defence_lvl_1 import contact_authority as ca

FAILURES = 0


def check(label, cond):
    global FAILURES
    if cond:
        print("  [PASS] %s" % label)
    else:
        FAILURES += 1
        print("  [FAIL] %s" % label)


def section(title):
    print("")
    print("-" * 78)
    print(title)
    print("-" * 78)


# ── minimal bodies with the attributes the path reads ─────────────────

class _Body:
    def __init__(self, pid, x, y, vx=0.0, vy=0.0, facing=0.0, radius=None):
        self.player_id = int(pid)
        self.x = float(x)
        self.y = float(y)
        self.vx = float(vx)
        self.vy = float(vy)
        self.facing = float(facing)
        self.freeze_timer = 0.0
        self.tackle_role = "PRIMARY"
        self.fullback_role_active = False
        # a REAL CollisionBody so radius resolution follows the live shape
        self.body = CollisionBody(self, radius=radius)

    def snapshot(self):
        return (self.x, self.y, self.vx, self.vy, self.freeze_timer)


class _World:
    def __init__(self, frame=1, ad=1):
        self.frame_count = int(frame)
        self.attacking_direction = int(ad)


# ─────────────────────────────────────────────────────────────────────
# A. PREDICATES
# ─────────────────────────────────────────────────────────────────────
section("A. PREDICATES — contact space is separation <= r_carrier + r_defender")

carrier = _Body(1, 0.0, 0.0)
defender = _Body(2, 0.0, 0.0)

check("A1 combined radius == 2 x 0.55 (both default)",
      abs(csm.combined_body_radius(carrier, defender) - 1.10) < 1e-9)

# place the defender exactly ON the boundary
defender.x = 1.10
check("A2a separation measured centre-to-centre",
      abs(csm.separation(defender, carrier) - 1.10) < 1e-9)
check("A2b is_contact_space_miss TRUE at separation == combined (<=)",
      csm.is_contact_space_miss(defender, carrier) is True)

defender.x = 1.10 + 1e-6
check("A2c is_contact_space_miss FALSE just above the boundary",
      csm.is_contact_space_miss(defender, carrier) is False)

# fallback radius when a body carries no usable radius
bare = _Body(3, 0.0, 0.0)
bare.body = None
check("A3 body_radius falls back to 0.55 with no body",
      abs(csm.body_radius(bare) - csm.DEFAULT_BODY_RADIUS_M) < 1e-9)


# ─────────────────────────────────────────────────────────────────────
# B. NO-OP OUTSIDE CONTACT SPACE
# ─────────────────────────────────────────────────────────────────────
section("B. NO-OP OUTSIDE CONTACT SPACE — an ordinary miss is untouched")

carrier = _Body(1, 0.0, 0.0, vx=5.0, vy=0.0)
defender = _Body(2, 1.40, 0.0, vx=-4.0, vy=0.0)   # 1.40 m > 1.10 m
world = _World()
before_c = carrier.snapshot()
before_d = defender.snapshot()
res = csm.apply_contact_space_miss(world, carrier, defender)
check("B1 applied is False outside contact space", res.applied is False)
check("B2 carrier untouched", carrier.snapshot() == before_c)
check("B3 defender untouched", defender.snapshot() == before_d)
check("B4 result carries the path label + the measured geometry",
      res.path == csm.CONTACT_SPACE_MISS
      and abs(res.separation_m - 1.40) < 1e-9
      and abs(res.combined_radius_m - 1.10) < 1e-9)
check("B5 no arc was attached outside contact space",
      getattr(carrier, "_csm_rebound", None) is None
      and getattr(defender, "_csm_rebound", None) is None)


# ─────────────────────────────────────────────────────────────────────
# C. THE REACTION FIRES INSIDE CONTACT SPACE
# ─────────────────────────────────────────────────────────────────────
section("C. REACTION FIRES INSIDE CONTACT SPACE — both bodies react")

carrier = _Body(1, 50.0, 35.0, vx=6.0, vy=0.0, facing=0.0)
defender = _Body(2, 50.90, 35.0, vx=-5.0, vy=0.0)   # 0.90 m, closing head-on
world = _World()
sep_before = csm.separation(defender, carrier)
res = csm.apply_contact_space_miss(world, carrier, defender)
sep_after = csm.separation(defender, carrier)
check("C1 applied is True in contact space", res.applied is True)
check("C2 the defender's first arc step is non-zero (it will be thrown)",
      (res.defender_dx, res.defender_dy) != (0.0, 0.0))
check("C3 the carrier's first arc step is non-zero (it will be deflected)",
      (res.carrier_dx, res.carrier_dy) != (0.0, 0.0))
check("C4 the defender's reported velocity change is non-zero",
      (res.defender_dvx, res.defender_dvy) != (0.0, 0.0))
check("C5 the defender was staggered (freeze_timer > 0)",
      defender.freeze_timer > 0.0 and res.defender_stagger_s > 0.0)
check("C6 NO TELEPORT — the instantaneous separation is unchanged",
      abs(sep_after - sep_before) < 1e-9)
check("C7 a bounded arc was attached to BOTH bodies",
      isinstance(getattr(carrier, "_csm_rebound", None), csm.Rebound)
      and isinstance(getattr(defender, "_csm_rebound", None), csm.Rebound))
_c_arc = getattr(carrier, "_csm_rebound", None)
_d_arc = getattr(defender, "_csm_rebound", None)
check("C8 the two arcs OPPOSE each other (so the pair separates)",
      _c_arc is not None and _d_arc is not None
      and (_c_arc.dir_x * _d_arc.dir_x + _c_arc.dir_y * _d_arc.dir_y) < 0.0)
check("C9 the arcs will travel a non-zero total distance",
      res.defender_travel_m + res.carrier_travel_m > 0.0)

# ── C2. THE ARC ITSELF ───────────────────────────────────────────────
print("")
print("  -- C2. the arc: exact sum, monotone decay, stop, frame-idempotence")

_arc = csm.Rebound(dir_x=1.0, dir_y=0.0, total_m=1.5, frames=5)
_steps = []
for _f in range(5):
    _dx, _dy = _arc.step(1.0 / 60.0, _f)
    _steps.append(_dx)
check("C2a the five steps sum EXACTLY to the declared total",
      abs(sum(_steps) - 1.5) < 1e-12)
check("C2b the steps decay monotonically (largest first, ease-out)",
      all(_steps[i] > _steps[i + 1] for i in range(len(_steps) - 1)))
check("C2c the arc reports itself done after its last frame",
      _arc.done is True and _arc.step(1.0 / 60.0, 5) == (0.0, 0.0))
check("C2d a sixth frame adds nothing (done is absorbing)",
      abs(_arc.travelled_m - 1.5) < 1e-12)

_arc2 = csm.Rebound(dir_x=0.0, dir_y=-1.0, total_m=0.9, frames=5)
_first = _arc2.step(1.0 / 60.0, 42)
_again = _arc2.step(1.0 / 60.0, 42)     # the SAME frame -> no double step
check("C2e the same frame never steps twice (idempotence guard)",
      _first != (0.0, 0.0) and _again == (0.0, 0.0))
_third = _arc2.step(1.0 / 60.0, 43)     # the next frame DOES step
check("C2f the next frame steps again", _third != (0.0, 0.0))
check("C2g with no frame identity the arc still advances (no stall)",
      csm.Rebound(dir_x=1.0, dir_y=0.0, total_m=0.5, frames=2).step(
          1.0 / 60.0, None) != (0.0, 0.0))
check("C2h the defender's arc is longer than the carrier's (the beaten body flies further)",
      res.defender_travel_m > res.carrier_travel_m)
# ─────────────────────────────────────────────────────────────────────
# D. DETERMINISM
# ─────────────────────────────────────────────────────────────────────
section("D. DETERMINISM — identical inputs give identical outputs")

def _scenario():
    c = _Body(1, 0.0, 0.0, vx=6.0, vy=1.5, facing=0.2)
    d = _Body(2, 0.85, 0.20, vx=-4.5, vy=-1.0)
    w = _World()
    return c, d, w

c1, d1, w1 = _scenario()
r1 = csm.apply_contact_space_miss(w1, c1, d1)
c2, d2, w2 = _scenario()
r2 = csm.apply_contact_space_miss(w2, c2, d2)
check("D1 results identical", r1 == r2)
check("D2 body states identical after the reaction",
      c1.snapshot() == c2.snapshot() and d1.snapshot() == d2.snapshot())


# ─────────────────────────────────────────────────────────────────────
# E. BOUNDS
# ─────────────────────────────────────────────────────────────────────
section("E. BOUNDS — capped arc totals, capped deltas, no position write")

# A full-force head-on contact: 12 m/s closing, so BOTH travel caps bind.
defender = _Body(2, 51.05, 35.0, vx=-6.0, vy=0.0)
carrier = _Body(1, 50.0, 35.0, vx=6.0, vy=0.0)
world = _World()
d_before = defender.snapshot()
c_before = carrier.snapshot()
res = csm.apply_contact_space_miss(world, carrier, defender)
check("E1 the defender's arc TOTAL <= DEFENDER_TRAVEL_MAX_M (1.5 m)",
      res.defender_travel_m <= csm.DEFENDER_TRAVEL_MAX_M + 1e-9)
check("E2 the carrier's arc TOTAL <= CARRIER_TRAVEL_MAX_M (0.9 m)",
      res.carrier_travel_m <= csm.CARRIER_TRAVEL_MAX_M + 1e-9)
check("E3 a full-force contact reaches both caps exactly",
      abs(res.defender_travel_m - csm.DEFENDER_TRAVEL_MAX_M) < 1e-9
      and abs(res.carrier_travel_m - csm.CARRIER_TRAVEL_MAX_M) < 1e-9)
check("E4 the defender's reported velocity delta <= VELOCITY_DELTA_MAX_MPS",
      (res.defender_dvx ** 2 + res.defender_dvy ** 2) ** 0.5
      <= csm.VELOCITY_DELTA_MAX_MPS + 1e-6)
check("E5 the carrier's reported velocity delta <= VELOCITY_DELTA_MAX_MPS",
      (res.carrier_dvx ** 2 + res.carrier_dvy ** 2) ** 0.5
      <= csm.VELOCITY_DELTA_MAX_MPS + 1e-9)
check("E6 the module writes NO POSITION (Player.update integrates the arc)",
      (defender.x, defender.y) == (d_before[0], d_before[1])
      and (carrier.x, carrier.y) == (c_before[0], c_before[1]))
check("E7 one frame's step is far smaller than the cap (no per-frame teleport)",
      (res.defender_dx ** 2 + res.defender_dy ** 2) ** 0.5
      < csm.DEFENDER_TRAVEL_MAX_M * 0.25)


# ─────────────────────────────────────────────────────────────────────
# E2. THE ARC IN THE REAL INTEGRATOR
# ─────────────────────────────────────────────────────────────────────
section("E2. THE REAL INTEGRATOR — arc travels, stops, clamps, suspends AI")

from player.player import Player

DT = csm.DT_FRAME_S


class _LiveWorld:
    """The minimum a real Player reads while a rebound is live."""

    def __init__(self):
        self.frame_count = 0
        self.attacking_direction = 1


def _live_pair(car_x=60.0, def_x=59.5):
    lw = _LiveWorld()
    pcar = Player(car_x, 35.0, player_id=1)
    pdef = Player(def_x, 35.0, player_id=2)
    pcar._world_ref = lw
    pdef._world_ref = lw
    return lw, pcar, pdef


# ── a full-force contact travels its whole declared total ──
lw, pcar, pdef = _live_pair()
pcar.vx, pdef.vx = -6.0, 6.0          # head-on: 12 m/s closing
live = csm.apply_contact_space_miss(lw, pcar, pdef)
d0 = (pdef.x, pdef.y)
c0 = (pcar.x, pcar.y)
# The AI's target is placed far on the other side of the field: while the arc
# is live the body must make NO progress toward it.
pdef.target_x, pdef.target_y = 119.0, 35.0
# Run EXACTLY the arc's frames: the travel is then the arc's alone, with no
# post-arc AI motion mixed in.
for _ in range(csm.REBOUND_FRAMES):
    lw.frame_count += 1
    pcar.update(DT)
    pdef.update(DT)
d_travel = ((pdef.x - d0[0]) ** 2 + (pdef.y - d0[1]) ** 2) ** 0.5
c_travel = ((pcar.x - c0[0]) ** 2 + (pcar.y - c0[1]) ** 2) ** 0.5
check("E2a through Player.update the defender travelled its full arc total",
      abs(d_travel - live.defender_travel_m) < 1e-6)
check("E2b through Player.update the carrier travelled its full arc total",
      abs(c_travel - live.carrier_travel_m) < 1e-6)
check("E2c the defender's REAL travel is within the 1.5 m cap",
      d_travel <= csm.DEFENDER_TRAVEL_MAX_M + 1e-6)
check("E2d the arc cleared itself from both bodies on its last frame",
      getattr(pdef, "_csm_rebound", None) is None
      and getattr(pcar, "_csm_rebound", None) is None)
check("E2e the AI seeking was suspended (nowhere near the 119 m target)",
      pdef.x < 110.0)
# The arc must not over-run: further frames with no arc do not repeat it.
d_arc_end = (pdef.x, pdef.y)
lw.frame_count += 1
pdef.update(DT)
check("E2f the body is a normal AI body again (it moves toward its target)",
      pdef.x > d_arc_end[0] + 1e-9)

# ── the field clamp still bounds the arc ──
lw2, pc2, pd2 = _live_pair(car_x=1.1, def_x=0.4)   # 0.7 m: IN contact space
pd2_start_x = pd2.x
pc2.vx, pd2.vx = -6.0, 6.0
live2 = csm.apply_contact_space_miss(lw2, pc2, pd2)
check("E2g the clamp scenario really IS a contact-space miss (arc is live)",
      live2.applied is True and live2.defender_travel_m > 0.0)
check("E2h this throw really wanted to leave the field (so the clamp binds)",
      pd2_start_x - live2.defender_travel_m < 0.0)
for _ in range(csm.REBOUND_FRAMES):
    lw2.frame_count += 1
    pc2.update(DT)
    pd2.update(DT)
check("E2i the field clamp holds the thrown defender inside the field",
      pd2.x >= 0.0 - 1e-9 and pc2.x >= 0.0 - 1e-9)
check("E2j the clamp only REDUCES travel (never exceeds the declared arc)",
      (pd2_start_x - pd2.x) <= live2.defender_travel_m + 1e-9)


# ─────────────────────────────────────────────────────────────────────
# F. THE CARRIER PAYS BUT IS NOT BEATEN
# ─────────────────────────────────────────────────────────────────────
section("F. THE CARRIER PAYS — capped speed loss, and the pair separates")

defender = _Body(2, 51.05, 35.0, vx=-6.0, vy=0.0)
carrier = _Body(1, 50.0, 35.0, vx=6.0, vy=0.0)
world = _World()
sep_before = csm.separation(defender, carrier)
car_speed_before = (carrier.vx ** 2 + carrier.vy ** 2) ** 0.5
res = csm.apply_contact_space_miss(world, carrier, defender)
sep_after = csm.separation(defender, carrier)
car_speed_after = (carrier.vx ** 2 + carrier.vy ** 2) ** 0.5
nx = res.normal_x
ny = res.normal_y
def_arc = getattr(defender, "_csm_rebound", None)
car_arc = getattr(carrier, "_csm_rebound", None)

check("F1 separation did not shrink", sep_after >= sep_before - 1e-9)
check("F2 the carrier DID lose speed (the contact cost it something)",
      car_speed_after < car_speed_before - 1e-9)
check("F3 the carrier's speed loss is <= CARRIER_SPEED_LOSS_MAX",
      car_speed_after >= car_speed_before
      * (1.0 - csm.CARRIER_SPEED_LOSS_MAX) - 1e-9)
check("F4 the defender's arc points AWAY from the carrier",
      def_arc is not None
      and (def_arc.dir_x * nx + def_arc.dir_y * ny) < 0.0)
check("F5 the carrier's arc points AWAY from the defender",
      car_arc is not None
      and (car_arc.dir_x * nx + car_arc.dir_y * ny) > 0.0)
check("F6 the beaten defender travels further than the carrier",
      res.defender_travel_m > res.carrier_travel_m)


# ─────────────────────────────────────────────────────────────────────
# G. THE GATE
# ─────────────────────────────────────────────────────────────────────
section("G. THE GATE — armed by default, disarmed by RLM_CONTACT_SPACE_MISS=0")

_prev = os.environ.pop(csm.CONTACT_SPACE_MISS_ENV_VAR, None)
try:
    check("G1 default (unset) is ARMED", csm.contact_space_miss_enabled() is True)
    os.environ[csm.CONTACT_SPACE_MISS_ENV_VAR] = "0"
    check("G2 '0' is DISARMED", csm.contact_space_miss_enabled() is False)
    os.environ[csm.CONTACT_SPACE_MISS_ENV_VAR] = "1"
    check("G3 '1' is ARMED", csm.contact_space_miss_enabled() is True)
finally:
    os.environ.pop(csm.CONTACT_SPACE_MISS_ENV_VAR, None)
    if _prev is not None:
        os.environ[csm.CONTACT_SPACE_MISS_ENV_VAR] = _prev


# ─────────────────────────────────────────────────────────────────────
# H. DECIDER INTEGRATION
# ─────────────────────────────────────────────────────────────────────
section("H. DECIDER INTEGRATION — evaluate_contact records the event")

class _Ope:
    def __init__(self, verdict):
        self._phase = "OPEN_PLAY"
        self._verdict = verdict
        self.calls = 0

    def _check_pre_tackle(self, bc, defender):
        self.calls += 1
        return self._verdict


# Neutralise the heavy, out-of-scope reaches of the decider so this section
# tests ONLY the MISS path.  (Documented: these are the recorder/payload/TES
# reaches, none of which is the subject here.)
_orig_preserve = ca._preserve
_orig_assemble = ca._assemble_contest
_orig_publish_contest = ca._publish_contest
_orig_form = ca._form_tackle_state
ca._preserve = lambda world, bc, defenders, decision, contest=None: decision
ca._assemble_contest = lambda *a, **k: None
ca._publish_contest = lambda *a, **k: None
ca._form_tackle_state = lambda *a, **k: None
try:
    # ── H1: MISS with the bodies in contact space records one event ──
    world = _World(frame=101)
    bc = _Body(1, 50.0, 35.0, vx=6.0, vy=0.0)
    d = _Body(2, 50.90, 35.0, vx=-5.0, vy=0.0)
    ope = _Ope(ca.VERDICT_MISS)
    dec = ca.evaluate_contact(world, ope, [bc, d], bc, None)
    check("H1a decision is CONTACT_FAILED (a miss forms nothing)",
          dec.outcome is ca.ContactOutcome.CONTACT_FAILED)
    check("H1b exactly ONE contact_space_miss_event recorded",
          len(dec.contact_space_miss_events) == 1)
    if dec.contact_space_miss_events:
        ev = dec.contact_space_miss_events[0]
        check("H1c the event is an APPLIED CONTACT_SPACE_MISS",
              ev.applied is True and ev.path == csm.CONTACT_SPACE_MISS)
    check("H1d the defender reacted (freeze_timer set)", d.freeze_timer > 0.0)
    check("H1e the decider attached the arc to the defender",
          isinstance(getattr(d, "_csm_rebound", None), csm.Rebound))

    # ── H2: MISS OUTSIDE contact space records NO event, bodies untouched ──
    world2 = _World(frame=102)
    bc2 = _Body(1, 50.0, 35.0, vx=6.0, vy=0.0)
    d2 = _Body(2, 51.40, 35.0, vx=-5.0, vy=0.0)
    before_bc2 = bc2.snapshot()
    before_d2 = d2.snapshot()
    ope2 = _Ope(ca.VERDICT_MISS)
    dec2 = ca.evaluate_contact(world2, ope2, [bc2, d2], bc2, None)
    check("H2a NO contact_space_miss_event outside contact space",
          len(dec2.contact_space_miss_events) == 0)
    check("H2b neither body was written",
          bc2.snapshot() == before_bc2 and d2.snapshot() == before_d2)

    # ── H3: a CONTACT verdict still FORMS, unchanged ──
    world3 = _World(frame=103)
    bc3 = _Body(1, 50.0, 35.0, vx=6.0, vy=0.0)
    d3 = _Body(2, 50.90, 35.0, vx=-1.0, vy=0.0)
    ope3 = _Ope(ca.VERDICT_CONTACT)
    dec3 = ca.evaluate_contact(world3, ope3, [bc3, d3], bc3, None)
    check("H3a CONTACT still forms (winner is the defender)",
          dec3.outcome is ca.ContactOutcome.CONTACT_FORMED
          and dec3.winner is d3)
    check("H3b a formed contest records NO contact_space_miss_event",
          len(dec3.contact_space_miss_events) == 0)

    # ── H4: gate OFF suppresses the event (A/B reversibility) ──
    os.environ[csm.CONTACT_SPACE_MISS_ENV_VAR] = "0"
    try:
        world4 = _World(frame=104)
        bc4 = _Body(1, 50.0, 35.0, vx=6.0, vy=0.0)
        d4 = _Body(2, 50.90, 35.0, vx=-5.0, vy=0.0)
        before_d4 = d4.snapshot()
        ope4 = _Ope(ca.VERDICT_MISS)
        dec4 = ca.evaluate_contact(world4, ope4, [bc4, d4], bc4, None)
        check("H4a gate OFF records NO event",
              len(dec4.contact_space_miss_events) == 0)
        check("H4b gate OFF leaves the defender untouched (pre-change engine)",
              d4.snapshot() == before_d4)
    finally:
        os.environ.pop(csm.CONTACT_SPACE_MISS_ENV_VAR, None)
finally:
    ca._preserve = _orig_preserve
    ca._assemble_contest = _orig_assemble
    ca._publish_contest = _orig_publish_contest
    ca._form_tackle_state = _orig_form


# ─────────────────────────────────────────────────────────────────────
# I. SOURCE ASSERTIONS — the "leave unchanged" clause
# ─────────────────────────────────────────────────────────────────────
section("I. SOURCE ASSERTIONS — thresholds, roles and wiring unchanged")

with open("world/open_play_engine.py", encoding="utf-8") as fh:
    engine_src = fh.read()
with open("game/defence/defence_lvl_1/contact_authority.py",
          encoding="utf-8") as fh:
    ca_src = fh.read()

check("I1 MISS_THRESHOLD still 0.3", "MISS_THRESHOLD = 0.3" in engine_src)
check("I2 ARM_GRAB_THRESHOLD still 0.6",
      "ARM_GRAB_THRESHOLD = 0.6" in engine_src)
check("I3 GLANCING_THRESHOLD still 0.75",
      "GLANCING_THRESHOLD = 0.75" in engine_src)
check("I4 the score expression is unchanged",
      "tackle_score = angle_score * 0.6 + speed_score * 0.4" in engine_src)
check("I5 face-carrier heading convention unchanged (def_spd > 0.1 guard)",
      "if def_spd > 0.1:" in engine_src)
check("I6 tackle-role initiators unchanged",
      'TACKLE_ROLE_INITIATORS: frozenset = frozenset({"PRIMARY", "SECONDARY"})'
      in ca_src)

# The reaction must be wired into the MISS branch BEFORE the existing
# miss handling (the evidence append for SCORE_MISS).
miss_idx = ca_src.find("if pre_result == VERDICT_MISS:")
wired_idx = ca_src.find("_apply_contact_space_miss(world, bc, p, tuners)",
                        miss_idx)
evidence_idx = ca_src.find("ContactFailureReason.SCORE_MISS,", miss_idx)
check("I7 the reaction is called inside the MISS branch",
      miss_idx != -1 and wired_idx != -1)
check("I8 the reaction runs BEFORE the existing miss handling",
      wired_idx != -1 and evidence_idx != -1 and wired_idx < evidence_idx)
check("I9 the path is a function-local reach (no top-level cycle)",
      "from game.physics.contact_space_miss import" in ca_src
      and "import game.physics.contact_space_miss" not in ca_src)
check("I10 the integrator consumes the arc (Player.update reads _csm_rebound)",
      "_csm_rebound" in open("player/player.py", encoding="utf-8").read())


print("")
print("=" * 78)
if FAILURES == 0:
    print("RESULT: PASS — CONTACT_SPACE_MISS contract holds")
else:
    print("RESULT: FAIL — %d check(s) failed" % FAILURES)
print("=" * 78)
sys.exit(1 if FAILURES else 0)
