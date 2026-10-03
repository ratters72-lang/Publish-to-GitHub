"""tmp/probe_csm_event.py — print the mocked frame's contact-space MISS event."""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

import verify_contest_record_schema_contract as v

frame = v._run_mocked_frame()
events = getattr(frame.decision, "contact_space_miss_events", ()) or ()
print("event count: %d" % len(events))
for index, ev in enumerate(events):
    print("")
    print("event[%d]:" % index)
    for name in (
        "path", "applied", "separation_m", "combined_radius_m",
        "normal_x", "normal_y", "closing_mps",
        "defender_travel_m", "carrier_travel_m", "carrier_speed_loss",
        "carrier_dx", "carrier_dy", "defender_dx", "defender_dy",
        "defender_stagger_s",
    ):
        print("  %-20s %r" % (name, getattr(ev, name, "<missing>")))
print("")
print("bc: x=%r y=%r vx=%r vy=%r" % (frame.bc.x, frame.bc.y,
                                     frame.bc.vx, frame.bc.vy))
print("arcs: bc=%r" % (getattr(frame.bc, "_csm_rebound", None),))
for body in frame.line:
    print("  body %s arc=%r" % (body.player_id,
                                getattr(body, "_csm_rebound", None)))
