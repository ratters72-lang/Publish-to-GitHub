"""tmp/show_reassert.py -- dump _reassert_fullback + return-window helpers."""

import inspect
import io
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, ".")

_REAL = sys.stdout


def _quiet():
    sys.stdout = io.TextIOWrapper(
        open(os.devnull, "wb"), encoding="utf-8", errors="replace"
    )


def _loud():
    sys.stdout = _REAL


_quiet()
import pygame

pygame.display.init()
pygame.display.set_mode((1, 1), pygame.NOFRAME)

import world.open_play_engine as ope_mod

ope_mod.DEBUG_SDEA_PHYSICS = False
ope_mod.DEBUG_OPE_TRACE = False

from world.open_play_engine import OpenPlayEngine

_loud()

out = []

fn = getattr(OpenPlayEngine, "_reassert_fullback", None)
out.append("=" * 78)
out.append("METHOD _reassert_fullback")
out.append("=" * 78)
if fn is None:
    out.append("  (missing)")
else:
    try:
        out.append(inspect.getsource(fn))
    except (OSError, TypeError) as exc:
        out.append("  (unavailable: %s)" % exc)

for name in ("_fb_arm_post_tackle_return", "_fb_reset_engagement",
             "_fb_clear_chase_state"):
    obj = getattr(ope_mod, name, None)
    out.append("")
    out.append("=" * 78)
    out.append("FUNCTION %s" % name)
    out.append("=" * 78)
    if obj is None:
        out.append("  (missing)")
        continue
    try:
        out.append(inspect.getsource(obj))
    except (OSError, TypeError) as exc:
        out.append("  (unavailable: %s)" % exc)

text = "\n".join(out) + "\n"
with open("tmp/reassert.txt", "w", encoding="utf-8") as fh:
    fh.write(text)
_REAL.write("wrote tmp/reassert.txt (%d chars)\n" % len(text))
