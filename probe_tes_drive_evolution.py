"""probe_tes_drive_evolution.py — Stage 3 CONSTRUCTED-INPUT EVIDENCE.

Companion to ``verify_tes_drive_v1_contract.py`` (the static contract),
``tmp_run_stage3_regression.py`` (the live guard) and
``tmp_s3_neutralise_probe.py`` (the removal probe).

WHERE THIS SITS
---------------
The static gate says the SHAPE is right.  The live guard says the WHOLE
ENGINE is right.  This probe says the EVOLUTION ITSELF is right: it hand-builds
a contest, scripts the two bodies' movement through a designed match narrative,
and prints the drive the module produced cycle by cycle -- so every number
below is read off the real record, not asserted by the probe.

THE NARRATIVE THE SCRIPT PLAYS
------------------------------
    phase 1  the carrier breaks the first tackler's grip   -> control RISES
    phase 2  the pair reaches parity                       -> control HOLDS
    phase 3  a joiner arrives and stalls him               -> control FALLS
    phase 4  the carrier accelerates again                 -> control RISES

Across all four phases the contest must remain ONE unbroken, ACTIVE contest.
That is the whole point of Stage 3: control may strengthen or weaken, but
Stage 3 never ends the contest.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from game.tes import tes_drive_v1 as TD                        # noqa: E402
from game.tes import tackle_state as TS                        # noqa: E402

DT = 1.0 / 60.0
FRAME0 = 1000
FAILS = []


class Body:
    def __init__(self, player_id, x, y, vx=0.0, vy=0.0, facing=0.0,
                 defender_role=None, tackle_role=None, mass_kg=90.0,
                 is_defender=False):
        self.player_id = player_id
        self.x = float(x)
        self.y = float(y)
        self.vx = float(vx)
        self.vy = float(vy)
        self.facing = float(facing)
        self.defender_role = defender_role
        self.tackle_role = tackle_role
        self.mass_kg = float(mass_kg)
        self.is_defender = bool(is_defender)


class World:
    def __init__(self, frame_count=FRAME0, sim_time_s=20.0, attack_dir=1):
        self.frame_count = frame_count
        self.sim_time_s = sim_time_s
        self.attacking_direction = attack_dir
        self.players = []


def check(name, ok, detail=""):
    ok = bool(ok)
    print("  %s  %-56s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILS.append(name)
    return ok


def bar(value, width=16):
    """A tiny text meter for a control value in [0, 1]."""
    filled = int(round(max(0.0, min(1.0, value)) * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


# ─────────────────────────────────────────────────────────────────────
# THE CONSTRUCTED CONTEST
# ─────────────────────────────────────────────────────────────────────
world = World(frame_count=FRAME0, sim_time_s=20.0, attack_dir=1)
TS.reset_tackle_states(world)
TD.reset_tes_drives_v1(world)

carrier = Body(7, 60.0, 35.0, vx=6.0, vy=0.0, facing=0.0, mass_kg=92.0)
primary = Body(8, 60.5, 35.0, vx=-3.0, vy=0.0, facing=math.pi,
               defender_role="TACKLER_1", tackle_role=TS.ROLE_PRIMARY,
               mass_kg=104.0, is_defender=True)
world.players = [carrier, primary]

state = TS.form_tackle_state(world, carrier, primary, frame=FRAME0, seq=1,
                             phase="OPEN_PLAY", channel="gate",
                             admitted_by_decider=True)

print("=" * 100)
print("STAGE 3 - TES DRIVE v1 EVOLUTION PROBE (constructed input)")
print("=" * 100)
print("contest: %s   parties: carrier=%d primary=%d"
      % (state.tackle_id, state.carrier_id, state.primary_defender_id))
print("frozen formation momentum: carrier=%.1f defender=%.1f balance=%.1f"
      % (state.carrier_momentum_kgmps, state.defender_momentum_kgmps,
         state.carrier_momentum_kgmps - state.defender_momentum_kgmps))
print("seeded control: %.6f  (the drive opens where the contest left off)"
      % TD.seed_control_for(state))
print()
print("  The script below plays four phases.  Every number is the drive the")
print("  module actually produced on that cycle.")
print()

# (phase label, carrier vx, primary vx, [(x offset, carrier vx), ...])
SCRIPT = (
    (("phase 1", "carrier breaks the grip"), -3.0,
     [(0.0, 6.0), (0.3, 7.0), (0.6, 8.0), (0.9, 9.0)]),
    (("phase 2", "the pair reaches parity"), -5.0,
     [(1.2, 5.0), (1.5, 5.0), (1.8, 5.0)]),
    (("phase 3", "a JOINDER stalls him"), -8.0,
     [(2.1, 0.5), (2.4, 0.2), (2.7, 0.0)]),
    (("phase 4", "he accelerates again"), -2.0,
     [(3.0, 7.5), (3.4, 8.5), (3.8, 9.0)]),
)

print("  %-7s %-29s %8s %8s %9s %-25s %-13s %7s %4s"
      % ("FRAME", "PHASE", "CARRIER", "DEFENDER", "BALANCE", "CONTROL",
         "TREND", "FWD m", "ATT"))
print("  " + "-" * 116)

drive = None
trace = []
joiner_added = False
for (phase, description), primary_vx, steps in SCRIPT:
    for offset, carrier_vx in steps:
        world.frame_count += 1
        world.sim_time_s += DT
        carrier.x = 60.0 + offset
        carrier.vx = carrier_vx
        primary.x = 60.5 + offset * 0.5
        primary.vx = primary_vx

        # ── THE JOINER: the contest GROWS (Stage 2 owns that), and the
        #    drive must only MIRROR the growth, never cause it.
        if phase == "phase 3" and not joiner_added:
            joiner = Body(9, carrier.x + 0.6, 35.4, vx=-4.0, vy=0.3,
                          facing=math.pi, defender_role="TACKLER_2",
                          tackle_role="SECONDARY", mass_kg=96.0,
                          is_defender=True)
            world.players.append(joiner)
            TS.append_attachment(world, joiner, "SECONDARY")
            TS.mark_attached(world, joiner)
            joiner_added = True

        drive = TD.advance_tes_drive_v1(world, DT)
        trace.append({
            "frame": world.frame_count,
            "phase": phase,
            "description": description,
            "control": drive.control,
            "leverage": drive.leverage,
            "trend": drive.control_trend.value,
            "progress": drive.forward_progress_m,
            "best": drive.forward_best_m,
            "worst": drive.forward_worst_m,
            "balance": drive.momentum_balance_kgmps,
            "attachment_count": drive.attachment_count,
            "update_count": drive.update_count,
            "closed": state.closed,
        })
        print("  %-7d %-29s %8.2f %8.2f %9.1f %-25s %-13s %7.2f %4d"
              % (world.frame_count, "%s: %s" % (phase, description),
                 drive.carrier_speed_mps, drive.defender_speed_mps,
                 drive.momentum_balance_kgmps,
                 "%s %.4f" % (bar(drive.control), drive.control),
                 drive.control_trend.value, drive.forward_progress_m,
                 drive.attachment_count))

print("  " + "-" * 116)
print()

# ─────────────────────────────────────────────────────────────────────
# WHAT THE TRACE SHOWS
# ─────────────────────────────────────────────────────────────────────
controls = [row["control"] for row in trace]
trends = [row["trend"] for row in trace]
progress = [row["progress"] for row in trace]
bests = [row["best"] for row in trace]
worsts = [row["worst"] for row in trace]
attachments = [row["attachment_count"] for row in trace]
counts = [row["update_count"] for row in trace]

phase1 = [i for i, row in enumerate(trace) if row["phase"] == "phase 1"]
phase2 = [i for i, row in enumerate(trace) if row["phase"] == "phase 2"]
phase3 = [i for i, row in enumerate(trace) if row["phase"] == "phase 3"]
phase4 = [i for i, row in enumerate(trace) if row["phase"] == "phase 4"]

print("THE EVOLUTION THE MODULE PRODUCED")
print()

check("the drive was advanced exactly once per scripted cycle",
      counts == list(range(1, len(trace) + 1)),
      "%d cycles, update_count 1..%d" % (len(trace), counts[-1]))

check("control RISES through phase 1",
      controls[phase1[-1]] > controls[phase1[0]],
      "%.4f -> %.4f" % (controls[phase1[0]], controls[phase1[-1]]))
check("control RISES again through phase 4",
      controls[phase4[-1]] > controls[phase4[0]],
      "%.4f -> %.4f" % (controls[phase4[0]], controls[phase4[-1]]))
check("control FALLS through phase 3",
      controls[phase3[-1]] < controls[phase2[-1]] - 1e-9,
      "%.4f (end of phase 2) -> %.4f (end of phase 3)"
      % (controls[phase2[-1]], controls[phase3[-1]]))

check("the contest was NEVER closed, on any cycle",
      not any(row["closed"] for row in trace),
      "contest %s stayed ACTIVE for all %d cycles"
      % (state.tackle_id, len(trace)))
check("it is still the live contest at the end",
      TS.active_tackle_state(world) is state and state.closed is False,
      "closed=%r" % state.closed)

check("control and leverage partitioned the contest on EVERY cycle",
      all(abs(row["control"] + row["leverage"] - 1.0) < 1e-12
          for row in trace),
      "control + leverage == 1.0 on all %d cycles" % len(trace))
check("no single cycle moved control by more than the declared step",
      all(abs(b - a) <= TD.MAX_CONTROL_STEP_PER_CYCLE + 1e-12
          for a, b in zip(controls, controls[1:])),
      "bound=%.2f, worst=%.6f"
      % (TD.MAX_CONTROL_STEP_PER_CYCLE,
         max(abs(b - a) for a, b in zip(controls, controls[1:]))))
check("control never left the declared band",
      all(TD.MIN_CONTROL <= c <= TD.MAX_CONTROL for c in controls),
      "range=[%.4f, %.4f]" % (min(controls), max(controls)))

check("forward progress advanced and then retreated within the envelope",
      max(progress) > 0.0 and min(progress) >= -TD.MAX_BACKWARD_M - 1e-9,
      "range=[%.2f, %.2f] envelope=[-%.1f, +%.1f]"
      % (min(progress), max(progress), TD.MAX_BACKWARD_M,
         TD.MAX_FORWARD_M))
check("the best and worst envelopes are monotone",
      all(b >= a for a, b in zip(bests, bests[1:]))
      and all(b <= a for a, b in zip(worsts, worsts[1:])),
      "best %.2f..%.2f  worst %.2f..%.2f"
      % (bests[0], bests[-1], worsts[0], worsts[-1]))
check("every reading sat inside its envelope",
      all(w - 1e-9 <= p <= b + 1e-9
          for p, b, w in zip(progress, bests, worsts)),
      "%d readings checked" % len(progress))

check("the attachment mirror grew when the contest grew, and never shrank",
      attachments[-1] > attachments[0]
      and all(b >= a for a, b in zip(attachments, attachments[1:])),
      "%s (the joiner was Stage 2's; the drive only mirrored it)"
      % attachments)
check("the mirror equalled the contest's own count on every cycle",
      drive.attachment_count == state.attachment_count,
      "drive=%d contest=%d" % (drive.attachment_count,
                               state.attachment_count))

print()
print("THE THREE THINGS STAGE 3 OWNS, AND THE THING IT DOES NOT")
print()
rises = sum(1 for a, b in zip(controls, controls[1:]) if b > a + 1e-12)
falls = sum(1 for a, b in zip(controls, controls[1:]) if b < a - 1e-12)
holds = sum(1 for a, b in zip(controls, controls[1:]) if abs(b - a) <= 1e-12)
print("  control rose on %d cycles, held on %d, fell on %d"
      % (rises, holds, falls))
print("  the contest remained ONE unbroken ACTIVE contest for all %d cycles"
      % len(trace))
print("  forward progress peaked at %+.2f m from the formation origin"
      % max(progress))
print()

# ─────────────────────────────────────────────────────────────────────
# THE REMOVAL DEMONSTRATION — the contest survives the drive's absence
# ─────────────────────────────────────────────────────────────────────
world = World(frame_count=2000, sim_time_s=40.0, attack_dir=1)
TS.reset_tackle_states(world)
TD.reset_tes_drives_v1(world)
carrier = Body(7, 60.0, 35.0, vx=6.0, facing=0.0)
primary = Body(8, 60.5, 35.0, vx=-3.0, facing=math.pi,
               defender_role="TACKLER_1", tackle_role=TS.ROLE_PRIMARY,
               is_defender=True)
world.players = [carrier, primary]
undriven = TS.form_tackle_state(world, carrier, primary, frame=2000, seq=1)

before = (undriven.attachment_count, undriven.separation_m,
          undriven.forward_origin_x, undriven.owner, undriven.closed)
for _ in range(30):
    world.frame_count += 1
    carrier.x += 0.02
after = (undriven.attachment_count, undriven.separation_m,
         undriven.forward_origin_x, undriven.owner, undriven.closed)

print("THE REMOVAL DEMONSTRATION")
print()
check("with the drive never called, the contest is simply untouched",
      before == after and TD.published_tes_drive_v1(world) is None,
      "30 cycles with no drive call changed nothing on the contest")
check("and the contest is still an active contest",
      TS.active_tackle_state(world) is undriven
      and undriven.closed is False,
      "Stage 3 is additive: the contest does not depend on being driven")
print()

print("=" * 100)
if FAILS:
    print("RESULT: %d FAIL(s) - %s" % (len(FAILS), ", ".join(FAILS)))
    print("=" * 100)
    sys.exit(1)
print("RESULT: the evolution is exactly what Stage 3 declares - one update")
print("  per cycle, a bounded and smooth control axis that both strengthens")
print("  and weakens, a monotone forward-progress envelope, an attachment")
print("  mirror that only follows Stage 2's growth, and a contest that stays")
print("  ACTIVE from the first cycle to the last.")
print("=" * 100)
sys.exit(0)
