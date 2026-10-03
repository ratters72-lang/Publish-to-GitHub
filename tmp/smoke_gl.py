import io
import os
import sys
import contextlib

sys.path.insert(0, os.getcwd())
import probe_goal_line_outcomes as p

KEYS = ("band", "label", "channel", "booted", "line_placed", "tackled",
        "contact_x", "broke", "try", "metres", "closest_m")

for band, ruck_x in p.BANDS:
    for label, c10, ctry in (("neutral", 0.95, 1.0),
                             ("tryline_squeeze", 0.95, 0.70)):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = p.run_one(label, c10, ctry, 11, 0.0,
                          ruck_x=ruck_x, band=band)
        print({k: r.get(k) for k in KEYS})
