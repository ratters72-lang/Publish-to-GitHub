"""verify_anticipation_exonerated.py — guardrail for the anticipation-teleport investigation.

Parses the probe output (tmp_anticipation_teleport.txt produced by
probe_anticipation_teleport.py) and asserts:

  1. Anticipation max shift is bounded by anticipation_max_gain_m (default 1.5 m),
     structurally incapable of producing a 10-15 m teleport.
  2. The teleport-scale jump delimiter in BOTH (ON and OFF) runs is `pre_tackle`
     / `struct_freeze_timer` — never `anticipation`.
  3. Anticipation ON does NOT produce MORE teleport-scale events than OFF
     (empirically it produces fewer).

Run:  python verify_anticipation_exonerated.py
"""
from __future__ import annotations

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

# Teleport-scale delimiter set: the ONLY target_sources allowed to fire a
# > 2.0 m single-frame defender move.  `anticipation` must NEVER appear.
_ALLOWED_TELEPORT_SOURCES = frozenset({"pre_tackle", "struct_freeze_timer"})

_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "tmp_anticipation_teleport.txt")

# Default anticipation max gain (metres) — apply_anticipation_pre_shift clamps
# every shift to sign(lat) * max_gain * role_gain * proximity, so this is the
# hard ceiling on a single-frame anticipation-induced Y change.
_DEFAULT_ANTICIPATION_MAX_GAIN_M = 1.5


def _parse_output(path: str) -> dict:
    """Parse probe output into {'on_count','off_count','on_max','off_max','sources'}."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Probe output not found: {path}\n"
            "Run `python probe_anticipation_teleport.py > tmp_anticipation_teleport.txt 2>&1` "
            "first."
        )
    # Windows PowerShell `>` redirect writes UTF-16LE; python redirects write
    # UTF-8.  Detect the encoding from the raw bytes and BOM.
    raw = open(path, "rb").read()
    text = None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-16", errors="replace")
    if text is None:
        text = raw.decode("utf-8", errors="replace")

    on_count = off_count = None
    on_max = off_max = None
    sources = set()

    # Comparison lines
    for m in re.finditer(
        r"Teleport-scale events \(> \d+\.\d+ m\):\s+ON=(\d+)\s+OFF=(\d+)", text
    ):
        on_count = int(m.group(1))
        off_count = int(m.group(2))
    for m in re.finditer(
        r"Largest single-frame delta:\s+ON=([\d.]+) m\s+OFF=([\d.]+) m", text
    ):
        on_max = float(m.group(1))
        off_max = float(m.group(2))

    # Every teleport event line carries src=<source>
    for m in re.finditer(r"axis=\S+\s+delta=[\d.]+m\s+src=([^\s]+)", text):
        sources.add(m.group(1))

    return {
        "on_count": on_count,
        "off_count": off_count,
        "on_max": on_max,
        "off_max": off_max,
        "sources": sources,
    }


def _check_anticipation_bound() -> list[str]:
    """Assert the anticipation max-gain tuner default is <= 1.5 m."""
    fails = []
    try:
        from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners
        tuners = DefenceTuners()
        max_gain = float(
            getattr(tuners, "anticipation_max_gain_m", _DEFAULT_ANTICIPATION_MAX_GAIN_M)
        )
        if max_gain > _DEFAULT_ANTICIPATION_MAX_GAIN_M:
            fails.append(
                f"anticipation_max_gain_m={max_gain} exceeds the {_DEFAULT_ANTICIPATION_MAX_GAIN_M} m "
                "bound — anticipation would be able to produce large single-frame shifts."
            )
    except Exception as exc:  # pragma: no cover - defensive
        fails.append(f"Could not import DefenceTuners to check the anticipation bound: {exc}")
    return fails


def main() -> int:
    fails: list[str] = []

    # ── Check 1: anticipation is structurally bounded ──
    for msg in _check_anticipation_bound():
        fails.append(msg)

    # ── Parse probe output ──
    try:
        data = _parse_output(_OUTPUT_PATH)
    except FileNotFoundError as exc:
        print(f"[VERIFY] FAIL: {exc}")
        return 1

    on_count = data["on_count"]
    off_count = data["off_count"]
    on_max = data["on_max"]
    off_max = data["off_max"]
    sources = data["sources"]

    print(f"[VERIFY] parse: ON teleports={on_count}  OFF teleports={off_count}")
    print(f"[VERIFY] parse: ON max delta={on_max} m  OFF max delta={off_max} m")
    print(f"[VERIFY] parse: teleport sources seen = {sorted(sources)}")

    if on_count is None or off_count is None:
        fails.append("Could not parse Teleport-scale ON/OFF counts from probe output.")

    # ── Check 2: anticipation must never be the teleport delimiter ──
    if "anticipation" in sources:
        fails.append(
            "A teleport-scale event was stamped target_source='anticipation' — "
            "anticipation IS causing a >2m single-frame move."
        )
    unexpected = sorted(sources - _ALLOWED_TELEPORT_SOURCES)
    if unexpected:
        fails.append(
            f"Teleport-source(s) outside the known set {sorted(_ALLOWED_TELEPORT_SOURCES)}: "
            f"{unexpected}"
        )

    # ── Check 3: anticipation ON must not worsen the symptom ──
    if on_count is not None and off_count is not None and on_count > off_count:
        fails.append(
            f"Anticipation ON produced MORE teleport-scale events ({on_count}) than OFF "
            f"({off_count}) — matches the hypothesis that anticipation causes it."
        )

    if fails:
        print("\n========= RESULT: FAIL =========")
        for f in fails:
            print(f"  - {f}")
        return 1

    print("\n========= RESULT: ALL CHECKS PASSED =========")
    print("  - Anticipation max shift is bounded (cannot be the teleport engine).")
    print(f"  - Teleport delimiters are {sorted(_ALLOWED_TELEPORT_SOURCES)}; never 'anticipation'.")
    print(f"  - Anticipation ON teleport events ({on_count}) <= OFF ({off_count}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
