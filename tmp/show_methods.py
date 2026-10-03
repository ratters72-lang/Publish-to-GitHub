"""tmp/show_methods.py -- dump the source of the RELEASE/PTB phase writers."""

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

TARGETS = (
    "_update_release",
    "_update_ptb_prep",
    "_update_ptb_exec",
    "_update_ptb",
    "_prepare_release",
)

out = []
for name in TARGETS:
    fn = getattr(OpenPlayEngine, name, None)
    out.append("=" * 78)
    out.append("METHOD %s" % name)
    out.append("=" * 78)
    if fn is None:
        out.append("  (missing)")
        continue
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError) as exc:
        out.append("  (source unavailable: %s)" % exc)
        continue
    out.append(src)

text = "\n".join(out) + "\n"
with open("tmp/phase_writers.txt", "w", encoding="utf-8") as fh:
    fh.write(text)
_REAL.write("wrote tmp/phase_writers.txt (%d chars)\n" % len(text))
