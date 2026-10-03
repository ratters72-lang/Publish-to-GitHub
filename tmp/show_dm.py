"""tmp/show_dm.py -- dump DefenceManager.reassert_fullback."""

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

from game.managers.defence_manager import DefenceManager

_loud()

out = []
for name in ("reassert_fullback",):
    fn = getattr(DefenceManager, name, None)
    out.append("=" * 78)
    out.append("DefenceManager.%s" % name)
    out.append("=" * 78)
    if fn is None:
        out.append("  (missing)")
        continue
    try:
        out.append(inspect.getsource(fn))
    except (OSError, TypeError) as exc:
        out.append("  (unavailable: %s)" % exc)

text = "\n".join(out) + "\n"
with open("tmp/dm_reassert.txt", "w", encoding="utf-8") as fh:
    fh.write(text)
_REAL.write("wrote tmp/dm_reassert.txt (%d chars)\n" % len(text))
