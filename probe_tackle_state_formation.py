"""probe_tackle_state_formation.py — STAGE 2 FORMATION PROBE.

A PROBE, not a gate: it prints what the tackle-formation module actually
does on constructed inputs so a human can read the behaviour, and it reports
five declared claims as observations:

  [A] DETERMINISM     — the same declaration forms the same contest.
  [B] THE PARTIES     — the carrier and the primary defender are latched.
  [C] INITIAL DATA    — every declared initial condition is captured.
  [D] NO MUTATION     — forming writes nothing to either body.
  [E] NO RESOLUTION   — forming resolves nothing: no referee, no PTB.

Run it with no arguments.  Nothing here asserts a contract; the contract
gate is verify_tackle_formation_contract.py.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from game.tes import tackle_state as TS

print(__file__)
print("=" * 74)
print("STAGE 2 TACKLE FORMATION PROBE")
print("=" * 74)


class Body:
    """A minimal body: only the fields the formation module declares."""

    def __init__(self, player_id, x, y, vx=0.0, vy=0.0, facing=0.0,
                 defender_role=None, tackle_role=None, mass_kg=90.0,
                 is_defender=False, label=""):
        self.player_id = player_id
        self.x, self.y = float(x), float(y)
        self.vx, self.vy = float(vx), float(vy)
        self.facing = float(facing)
        self.defender_role = defender_role
        self.tackle_role = tackle_role
        self.mass_kg = float(mass_kg)
        self.is_defender = bool(is_defender)
        self.label = label

    def __repr__(self):
        return "#%d%s(%.2f,%.2f v=%.2f,%.2f)" % (
            self.player_id, ("/" + self.label) if self.label else "",
            self.x, self.y, self.vx, self.vy)


class World:
    """A minimal world: only the fields the formation module reads."""

    def __init__(self, frame_count=120, sim_time_s=3.5, attack_dir=1):
        self.frame_count = frame_count
        self.sim_time_s = sim_time_s
        self.attacking_direction = attack_dir

    def __repr__(self):
        return "World(frame=%d t=%.2f dir=%d)" % (
            self.frame_count, self.sim_time_s, self.attacking_direction)


def fresh(frame_count=120, sim_time_s=3.5, attack_dir=1):
    world = World(frame_count, sim_time_s, attack_dir)
    TS.reset_tackle_states(world)
    return world


def line():
    """A carrier driving at +5 m/s into a defender closing at -4 m/s."""
    return (
        Body(7, 60.0, 35.0, 5.0, 0.0, facing=0.0, label="carrier"),
        Body(8, 60.4, 35.0, -4.0, 0.0, facing=math.pi,
             defender_role="TACKLER_1", tackle_role=TS.ROLE_PRIMARY,
             is_defender=True, label="primary"),
    )


def snapshot(body):
    keys = ("x", "y", "vx", "vy", "facing", "player_id", "mass_kg",
            "defender_role", "tackle_role", "is_defender", "label")
    return {k: getattr(body, k) for k in keys if hasattr(body, k)}


INVENTED = ("frozen", "tes_locked", "in_contact", "is_tackler",
            "anim_state", "attack_role", "target_x", "target_y",
            "vx_target", "vy_target", "movement_mode")


# ═════════════════════════════════════════════════════════════════════
# [A] DETERMINISM
# ═════════════════════════════════════════════════════════════════════
print()
print("[A] DETERMINISM - the same declaration forms the same contest")
print("-" * 74)

CONTRAST = ("tackle_id", "frame", "seq", "carrier_id",
            "primary_defender_id", "separation_m", "contact_angle_deg",
            "contact_angle_to_attack_deg", "lateral_offset_m",
            "closing_speed_mps", "contact_quality", "contact_type",
            "carrier_speed_mps", "defender_speed_mps",
            "carrier_momentum_kgmps", "defender_momentum_kgmps",
            "relative_speed_mps", "relative_momentum_kgmps",
            "relative_vx", "relative_vy", "forward_origin_x",
            "forward_origin_y", "attack_dir", "owner")

world1 = fresh()
bc1, df1 = line()
s1 = TS.form_tackle_state(world1, bc1, df1, frame=120, seq=7,
                          phase="OPEN_PLAY", channel="manager_trigger")

world2 = fresh()
bc2, df2 = line()
s2 = TS.form_tackle_state(world2, bc2, df2, frame=120, seq=7,
                          phase="OPEN_PLAY", channel="manager_trigger")

print("  run 1:", TS.format_tackle_state_report(world1))
print("  run 2:", TS.format_tackle_state_report(world2))

same = all(getattr(s1, f) == getattr(s2, f) for f in CONTRAST)
print("  identical across %d declared fields: %s" % (len(CONTRAST), same))
print("  identity:", s1.tackle_id, "declaration key:", s1.declaration_key)

again = TS.form_tackle_state(world1, bc1, df1, frame=120, seq=7)
print("  re-forming the SAME declaration returns the SAME object:",
      again is s1)
print("  and it does not close the contest:", s1.closed is False)
print("  records for this declaration:",
      len([s for s in TS.recent_tackle_states()
           if s.tackle_id == s1.tackle_id]))

bc3, df3 = line()
s3 = TS.form_tackle_state(world1, bc3, df3, frame=121, seq=8)
print("  a DIFFERENT declaration on the SAME world supersedes it:",
      s1.closed, "reason:", s1.closed_reason)
print("  new identity:", s3.tackle_id, "owner:", s3.owner)
print("  exactly ONE contest is live in this world:",
      TS.active_tackle_state(world1) is s3, "open:", s3.open)
print("  and the OTHER world's contest was untouched by it:",
      s2.closed is False and TS.published_tackle_state(world2) is s2)


# ═════════════════════════════════════════════════════════════════════
# [B] THE PARTIES
# ═════════════════════════════════════════════════════════════════════
print()
print("[B] THE PARTIES - who the contest is between")
print("-" * 74)

world = fresh()
bc, df = line()
joiner = Body(9, 60.9, 35.35, -3.0, 0.2, facing=math.pi,
              defender_role="TACKLER_2", tackle_role="SECONDARY",
              is_defender=True, label="joiner")
state = TS.form_tackle_state(world, bc, df, frame=130, seq=1)

print("  carrier            :", repr(bc))
print("  primary defender   :", repr(df))
print("  third body (joiner):", repr(joiner))
print("  state.carrier is the carrier object        :",
      state.carrier is bc)
print("  state.primary_defender is the defender obj :",
      state.primary_defender is df)
print("  carrier_id=%r primary_defender_id=%r"
      % (state.carrier_id, state.primary_defender_id))
print("  the third body is NOT the primary          :",
      state.primary_defender_id != joiner.player_id)
print("  contact_set (defenders in contact)         :",
      state.contact_set_ids)
print("  attachments beyond the primary at formation:",
      state.attachment_ids, "(count %d)" % state.attachment_count)
print("  has_attachment(primary) =", state.has_attachment(8))
print("  contact kinematics are SNAPSHOTS (not the live bodies):",
      type(state.contact_carrier).__name__,
      state.contact_carrier is not bc)


# ═════════════════════════════════════════════════════════════════════
# [C] THE INITIAL DATA
# ═════════════════════════════════════════════════════════════════════
print()
print("[C] INITIAL DATA - every declared initial condition")
print("-" * 74)

world = fresh(frame_count=140, sim_time_s=5.25, attack_dir=1)
bc = Body(7, 60.0, 35.0, 5.0, 0.0, facing=0.0, mass_kg=92.0)
df = Body(8, 60.4, 35.0, -4.0, 0.0, facing=math.pi, mass_kg=88.0,
          defender_role="TACKLER_1", tackle_role=TS.ROLE_PRIMARY,
          is_defender=True)
state = TS.form_tackle_state(world, bc, df, frame=140, seq=2,
                             phase="OPEN_PLAY", channel="engage",
                             admitted_by_decider=False)

print("  declaration : frame=%s seq=%s phase=%r channel=%r"
      % (state.frame, state.seq, state.phase, state.channel))
print("  timestamp   : contact_frame=%s contact_time_s=%s"
      % (state.contact_frame, state.contact_time_s))
print("  geometry    : separation=%.4f m angle=%.3f deg "
      "angle_to_attack=%.3f deg lateral=%.4f m closing=%.4f m/s"
      % (state.separation_m, state.contact_angle_deg,
         state.contact_angle_to_attack_deg, state.lateral_offset_m,
         state.closing_speed_mps))
print("  quality/type: %s / %s"
      % (state.contact_quality, state.contact_type))
print("  carrier kin : x=%.3f y=%.3f v=(%.3f, %.3f) facing=%.3f"
      % (state.contact_carrier.x, state.contact_carrier.y,
         state.contact_carrier.vx, state.contact_carrier.vy,
         state.contact_carrier.facing))
print("  defender kin: x=%.3f y=%.3f v=(%.3f, %.3f) facing=%.3f"
      % (state.contact_defender.x, state.contact_defender.y,
         state.contact_defender.vx, state.contact_defender.vy,
         state.contact_defender.facing))
print("  momentum    : carrier=%.2f defender=%.2f kg.m/s"
      % (state.carrier_momentum_kgmps, state.defender_momentum_kgmps))
print("  relative    : v=(%.3f, %.3f) |v|=%.3f |p|=%.3f"
      % (state.relative_vx, state.relative_vy,
         state.relative_speed_mps, state.relative_momentum_kgmps))
print("  origin      : (%.3f, %.3f) attack_dir=%s"
      % (state.forward_origin_x, state.forward_origin_y,
         state.attack_dir))
print("  forward progress from the origin: x=62 -> %.4f m, x=58 -> %.4f m"
      % (state.forward_progress_m(62.0), state.forward_progress_m(58.0)))
print("  attachment list at formation: %s (count %d)"
      % (state.attachments, state.attachment_count))

missing = []
for name, fields in TS.declared_content():
    for f in fields:
        if getattr(state, f, None) is None:
            missing.append("%s.%s" % (name, f))
print("  declared items: %d; declared fields left unset: %d %s"
      % (len(TS.declared_content()),
         len(missing), ("-> " + str(missing)) if missing else ""))


# ═════════════════════════════════════════════════════════════════════
# [D] NO MUTATION
# ═════════════════════════════════════════════════════════════════════
print()
print("[D] NO MUTATION - forming writes nothing to a body")
print("-" * 74)

world = fresh(frame_count=150, sim_time_s=6.0)
bc = Body(7, 60.0, 35.0, 5.0, 0.0, facing=0.25, mass_kg=92.0)
df = Body(8, 60.4, 35.0, -4.0, 0.0, facing=math.pi, mass_kg=88.0,
          defender_role="TACKLER_1", tackle_role=TS.ROLE_PRIMARY,
          is_defender=True)

before_carrier = snapshot(bc)
before_defender = snapshot(df)
before_world = (world.frame_count, world.sim_time_s,
                world.attacking_direction)

state = TS.form_tackle_state(world, bc, df, frame=150, seq=1)

print("  carrier  before:", before_carrier)
print("  carrier  after :", snapshot(bc))
print("  identical      :", snapshot(bc) == before_carrier)
print("  defender before:", before_defender)
print("  defender after :", snapshot(df))
print("  identical      :", snapshot(df) == before_defender)

invented = sorted({f for body in (bc, df) for f in INVENTED
                   if hasattr(body, f)})
print("  ownership/movement flags invented on a body: %s" % invented)

after_world = (world.frame_count, world.sim_time_s,
               world.attacking_direction)
print("  world clock/direction untouched:",
      after_world == before_world, after_world)

print("  re-forming is equally inert:",
      (lambda: (TS.form_tackle_state(world, bc, df, frame=150, seq=1),
                snapshot(bc) == before_carrier
                and snapshot(df) == before_defender)[1])())


# ═════════════════════════════════════════════════════════════════════
# [E] NO RESOLUTION
# ═════════════════════════════════════════════════════════════════════
print()
print("[E] NO RESOLUTION - Stage 2 captures, it does not decide")
print("-" * 74)

world = fresh(frame_count=160, sim_time_s=7.0)
bc, df = line()
state = TS.form_tackle_state(world, bc, df, frame=160, seq=1)

print("  owned by               :", state.owner)
print("  owned_by_tes           :", state.owned_by_tes)
print("  closed                 :", state.closed)
print("  closed_reason          :", state.closed_reason)
print("  result / outcome / winner attributes present:",
      [f for f in ("result", "outcome", "winner", "grounded",
                   "pushback", "ptb_ready")
       if hasattr(state, f)] or "none")

print()
print("  the handover, then the contest's ownership:")
TS.mark_tes_ownership(world, bc, df, (2.5, 0.0))
print("    owner=%r owned_by_tes=%r handover_frame=%s handoff_v=(%.2f, %.2f)"
      % (state.owner, state.owned_by_tes, state.handover_frame,
         state.handoff_defender_vx, state.handoff_defender_vy))
print("    handed velocity differed from the contact velocity:",
      state.handoff_differs_from_contact)
print("    identity/initial conditions untouched by the handover:",
      state.tackle_id == "tackle-160-1"
      and state.forward_origin_x == 60.0)
print("    the contest is still unresolved:", state.closed is False)

print()
print("  growth, then the declared lifecycle boundaries:")
joiner = Body(9, 60.95, 35.4, -3.0, 0.3, facing=math.pi,
              defender_role="TACKLER_2", tackle_role="SECONDARY",
              is_defender=True, label="joiner")
TS.append_attachment(world, joiner, "SECONDARY")
TS.mark_attached(world, joiner)
print("    attached=%s attached_frame=%s"
      % (state.attached_ids,
         state.attachment_for(9).attached_frame))
print("    primary as an attachment row:",
      TS.append_attachment(world, df, TS.ROLE_PRIMARY))
print("    forward progress still measured from the declared origin: %.2f m"
      % state.forward_progress_m(62.0))

TS.clear_tackle_state(world)
print("    closed=%s reason=%r published=%r active=%r"
      % (state.closed, state.closed_reason,
         TS.published_tackle_state(world),
         TS.active_tackle_state(world)))

world = fresh(frame_count=170, sim_time_s=8.0)
bc, df = line()
state = TS.form_tackle_state(world, bc, df, frame=170, seq=1)
TS.clear_tackle_state(world, TS.CLOSE_KICKOFF)
print("    a kickoff closes it   : %s / %r"
      % (state.closed, state.closed_reason))
TS.reset_tackle_states(world)
print("    a kickoff reset drops the ledger: ring=%d published=%r"
      % (len(TS.recent_tackle_states()),
         TS.published_tackle_state(world)))

print()
print("=" * 74)
print("PROBE COMPLETE - nothing above asserts a contract; the gate is")
print("verify_tackle_formation_contract.py")
print("=" * 74)
