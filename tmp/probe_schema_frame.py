"""tmp/probe_schema_frame.py — inspect the mocked schema frame under CSM.

Prints the live carrier, the published record's carrier, and, per row, the
recorded dist next to the body's position and the declared mock position.
"""

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import verify_contest_record_schema_contract as V

frame = V._run_mocked_frame()
bc = frame.bc
print("declared mock line:", V._MOCK_LINE)
print("bc live: x=%.6f y=%.6f vx=%.6f vy=%.6f" % (bc.x, bc.y, bc.vx, bc.vy))
print("record.carrier: %s" % (frame.record.carrier,))
print("decision events: %s"
      % (getattr(frame.decision, "contact_space_miss_events", None),))
print("decision bc: x=%s y=%s vx=%s"
      % (frame.decision.bc_x, frame.decision.bc_y, frame.decision.bc_vx))
print("")
by_id = {b.player_id: b for b in frame.line}
for row in frame.record.candidates:
    body = by_id[row.player_id]
    print("pid=%-3d row.dist=%.6f  body=(%.4f, %.4f)  declared=(%.2f, %.2f)"
          % (row.player_id, row.dist, body.x, body.y,
             next(x for p, x, _y, _v in V._MOCK_LINE if p == row.player_id),
             next(y for p, _x, y, _v in V._MOCK_LINE if p == row.player_id)))
