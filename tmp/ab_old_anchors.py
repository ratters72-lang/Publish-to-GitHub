"""A/B runner: executes a verifier with the PRE-reconciliation role anchors
patched back onto DefenceTuners, to classify a failure as pre-existing vs
regression from the even-lane reconciliation.

Usage:
    python tmp/ab_old_anchors.py <verifier.py>
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"

import sys
import runpy
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import pygame
pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

_OLD_ANCHORS = {
    "A": 0.12,
    "B": 0.34,
    "C": 0.56,
    "D": 0.72,
    "CENTRE": 0.80,
    "WINGER": 1.00,
}

_orig_init = DefenceTuners.__init__


def _patched_init(self):
    _orig_init(self)
    self.role_width_anchor.update(_OLD_ANCHORS)
    self.ROLE_WIDTH_OFFSETS.update(
        {r: a * self.width_curve_factor.get(r, 1.0)
         for r, a in _OLD_ANCHORS.items()}
    )


DefenceTuners.__init__ = _patched_init

target = sys.argv[1] if len(sys.argv) > 1 else None
if target is None:
    print("usage: python tmp/ab_old_anchors.py <verifier.py>")
    sys.exit(2)

try:
    runpy.run_path(target, run_name="__main__")
except SystemExit as exc:
    print("[ab_old_anchors] %s exited with code %s" % (target, exc.code))
    sys.exit(exc.code if isinstance(exc.code, int) else 1)
