"""tmp_pt_sig2.py -- print the REAL lifecycle signatures and arm/clear bodies."""
import inspect
import pathlib
import sys

sys.path.insert(0, ".")

from game.defence.defence_lvl_1 import fullback_role as fb

NAMES = [
    "fullback_post_tackle_return_enabled",
    "is_fullback_post_tackle_return_active",
    "assigned_fullback_return_target",
    "record_fullback_return_target",
    "arm_fullback_post_tackle_return",
    "fullback_post_tackle_return_live",
    "fullback_post_tackle_return_release_reason",
    "clear_fullback_post_tackle_return",
    "clear_fullback_post_tackle_return_for",
]

out = pathlib.Path("tmp")
lines = []
for n in NAMES:
    fn = getattr(fb, n, None)
    lines.append("%s%s" % (n, inspect.signature(fn) if fn else " MISSING"))

lines.append("")
lines.append("CONSTS:")
for n in (
    "FB_POST_TACKLE_RETURN_ENV_VAR",
    "FB_POST_TACKLE_RETURN_ATTR",
    "FB_POST_TACKLE_RETURN_TARGET_ATTR",
    "FB_POST_TACKLE_RETURN_SOURCE_ATTR",
    "FB_POST_TACKLE_RETURN_RELEASE_M",
    "FB_POST_TACKLE_RETURN",
):
    lines.append("  %s = %r" % (n, getattr(fb, n, "<missing>")))

for n in ("arm_fullback_post_tackle_return",
          "clear_fullback_post_tackle_return",
          "clear_fullback_post_tackle_return_for"):
    lines.append("")
    lines.append("=" * 70)
    lines.append(n)
    lines.append("=" * 70)
    try:
        lines.append(inspect.getsource(getattr(fb, n)))
    except OSError:
        lines.append("<source unavailable>")

(out / "pt_sig2.txt").write_text("\n".join(lines), encoding="utf-8")
print("written tmp/pt_sig2.txt")
