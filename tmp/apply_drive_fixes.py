"""Apply the three TACKLE_DRIVE hardening fixes to world/open_play_engine.py.

Each replacement asserts its OLD text occurs EXACTLY ONCE before applying;
the script aborts without writing if any assertion fails.  Line endings are
preserved exactly (CRLF/LF detected from the source).
"""
import sys

PATH = r"c:/Users/User/Desktop/RLM/world/open_play_engine.py"

with open(PATH, encoding="utf-8", newline="") as f:
    src = f.read()

EOL = "\r\n" if "\r\n" in src else "\n"


def block(lines):
    return EOL.join(lines) + EOL


# ── Fix 3: DRIVE watchdog ──────────────────────────────────────────
OLD_A = block([
    "        # Advance the drive timer",
    "        ctx.drive_elapsed_s += dt",
    "        dur = max(0.1, self.tes_outcome.tackle_duration_s)",
    "        t = max(0.0, min(1.0, ctx.drive_elapsed_s / dur))",
])
NEW_A = block([
    "        # Advance the drive timer",
    "        ctx.drive_elapsed_s += dt",
    "        dur = max(0.1, self.tes_outcome.tackle_duration_s)",
    "        t = max(0.0, min(1.0, ctx.drive_elapsed_s / dur))",
    "",
    "        # -- DRIVE WATCHDOG (2026-08-22) --",
    "        # The t >= 1.0 LERP-completion branch below is the ONLY legal exit",
    "        # from TACKLE_DRIVE into TACKLE_DOMINANCE.  If timer drift ever",
    "        # leaves t short of 1.0 once the drive duration has fully elapsed",
    "        # (+0.5 s margin), force completion so the phase machine can never",
    "        # wedge in TACKLE_DRIVE - a wedged drive never reaches RELEASE, so",
    "        # no new ruck forms and the defensive pipeline runs on stale state.",
    "        if t < 1.0 and self._phase_timer > dur + 0.5:",
    "            t = 1.0",
])

# ── Fix 1: dominance latch at DRIVE entry ──────────────────────────
OLD_B = block([
    "        # Spec 7.1: Check for broken tackle using scoring system (re-evaluated each frame)",
    "        if self.tes_ctx is not None and self.tes_outcome is not None:",
    "            compute_dominance(self.tes_ctx, self.tes_outcome, self.rng)",
    "            if self._check_broken_tackle_scoring(self.tes_ctx, self.tes_outcome):",
    "                self._handle_broken_tackle()",
    "                return",
])
NEW_B = block([
    "        # Spec 7.1: Check for broken tackle using scoring system.",
    "        # -- DOMINANCE LATCH (2026-08-22) --",
    "        # The outcome classification is computed ONCE at DRIVE entry and",
    "        # then FROZEN for the rest of the drive.  Re-running",
    "        # compute_dominance() every frame let a live result flip (e.g.",
    "        # NEUTRAL -> WEAK as engaged-tackler velocities changed) re-arm",
    "        # _check_broken_tackle_scoring() MID-DRIVE against a",
    "        # drive_distance_m that was fixed at entry - a break exit the",
    "        # entry-time classification never authorised.  A mid-drive break",
    "        # also skips the DRIVE-END lock (drive_end_x / ruck_x_m pinning),",
    "        # leaving ruck state stale for the next cycle.  Fresh contexts per",
    "        # tackle make the latch self-resetting (attribute absent -> False).",
    "        if self.tes_ctx is not None and self.tes_outcome is not None:",
    "            if not getattr(self.tes_ctx, \"drive_dominance_latched\", False):",
    "                compute_dominance(self.tes_ctx, self.tes_outcome, self.rng)",
    "                self.tes_ctx.drive_dominance_latched = True",
    "            if self._check_broken_tackle_scoring(self.tes_ctx, self.tes_outcome):",
    "                self._handle_broken_tackle()",
    "                return",
])

# ── Fix 2: raw-gate fallback poison fix ────────────────────────────
OLD_C = block([
    "        total_def_momentum = self.tes_ctx.engagement.total_momentum()",
    "        def_resistance = total_def_momentum if total_def_momentum > 0 else bc_momentum * 0.5",
    "        if bc_momentum > def_resistance * 1.4 or bc_speed > 7.0:",
])
NEW_C = block([
    "        # -- FALLBACK POISON FIX (2026-08-22) --",
    "        # When the engagement registry carries NO measurable defender",
    "        # momentum (e.g. an all-zero velocity snapshot), the old",
    "        # ``bc_momentum * 0.5`` fallback made",
    "        # ``bc_momentum > def_resistance * 1.4`` trivially TRUE - arming",
    "        # the raw break gate on every frame from the momentum side alone",
    "        # (a spurious break exits to OPEN_PLAY with NO ruck formed).  With",
    "        # no measurable resistance the ratio is UNDEFINED: skip the",
    "        # momentum condition entirely and let the explicit speed-burst",
    "        # branch (scripted drive average > 7.0 m/s) remain the only fire",
    "        # path for that case.",
    "        total_def_momentum = self.tes_ctx.engagement.total_momentum()",
    "        momentum_break = (",
    "            bc_momentum > total_def_momentum * 1.4",
    "            if total_def_momentum > 0",
    "            else False",
    "        )",
    "        if momentum_break or bc_speed > 7.0:",
])

edits = [
    ("watchdog", OLD_A, NEW_A),
    ("dominance-latch", OLD_B, NEW_B),
    ("fallback-poison", OLD_C, NEW_C),
]

for name, old, new in edits:
    count = src.count(old)
    if count != 1:
        print("ABORT: %s anchor found %d times (expected 1)" % (name, count))
        sys.exit(1)

for name, old, new in edits:
    src = src.replace(old, new)
    print("applied:", name)

with open(PATH, "w", encoding="utf-8", newline="") as f:
    f.write(src)
print("OK wrote", PATH)
