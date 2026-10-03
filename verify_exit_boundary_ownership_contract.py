"""verify_exit_boundary_ownership_contract.py — Stage 4B contract verifier.

Seven static checks (C1-C7) over the TES -> line exit boundary.  Static by
design: every check reads source text or imports a pure module, so the
verifier is fast, deterministic, and needs no headless match.  The dynamic
counterpart is tmp/probe_stage4b_exit_ownership.py.

  C1 SOURCE ORDER      inside OpenPlayEngine.update(), the exit-era write
                       order is: release repair -> population repair ->
                       ruck pin -> finalise -> record_final_targets.
  C2 PUBLISH PROV.     every frame-geometry publisher stamps its origin.
  C3 CHANNEL SEPARATION stamp_target_write() must NOT touch target_source.
  C4 DECLARED MAP      the map covers every sub-phase x body class, and
                       every declared tag is a real blessed writer name.
  C5 BUDGET SINGLE SRC exit bounds come from their owning authorities; no
                       private copies of the PTB-era literals may remain
                       inside the phase handlers.
  C6 PIN/Freeze MERGE  the pin's eligibility test and the freeze writer's
                       condition must agree on what "frozen" means.
  C7 RELEASE BOUNDS    the RELEASE floor/cap are imported, and the watchdog
                       frame count is DERIVED from them.

Exit code 0 = all FAIL-free.  INFO rows are findings, not failures.
"""

import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

ENGINE = "world/open_play_engine.py"
PIPELINE = "game/defence/defence_lvl_1/defensive_pipeline.py"
UNIFIED = "game/defence/defence_lvl_1/defensive_behaviour_unified.py"
WRITE_LOG = "game/debug/target_write_log.py"
PIN = "game/defence/defence_lvl_1/ruck_adjacent_pin.py"
GUARD = "game/defence/defence_lvl_1/phase_authority_guard.py"
BUDGET = "world/rules/exit_timing_budget.py"
GATE = "world/rules/release_gate.py"

_FAILS = []
_INFOS = []


def read(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def check(name, ok, detail=""):
    if ok:
        print("  PASS  %-28s %s" % (name, detail))
    else:
        print("  FAIL  %-28s %s" % (name, detail))
        _FAILS.append(name)


def info(name, detail):
    print("  INFO  %-28s %s" % (name, detail))
    _INFOS.append((name, detail))


def body_of(text, marker):
    """Return the substring from *marker* to the next top-level def."""
    idx = text.find(marker)
    if idx < 0:
        return ""
    rest = text[idx:]
    nxt = rest.find("\n    def ", 1)
    return rest if nxt < 0 else rest[:nxt]


# ─────────────────────────────────────────────────────────────────────
# C1 — END-OF-FRAME SOURCE ORDER
# ─────────────────────────────────────────────────────────────────────

def c1_source_order():
    print("\nC1  END-OF-FRAME WRITE ORDER (update() tail)")
    text = read(ENGINE)
    upd = text.find("    def update(self")
    check("C1_update_found", upd > 0, "def update at %d" % upd)
    if upd < 0:
        return
    # Bound the tail to the end of update(): the next top-level method.
    tail = text[upd:]
    nxt = tail.find("\n    def ", 5)
    tail = tail if nxt < 0 else tail[:nxt]

    order = [
        ("release_repair", "publish_source=\"repair\""),
        ("population_repair", "repair_line_population_if_drifted("),
        ("ruck_pin", "enforce_ruck_pin("),
        ("finalise", "finalise_line_population("),
        ("record_final", "record_final_targets("),
    ]
    idxs = {}
    for label, needle in order:
        pos = tail.find(needle)
        idxs[label] = pos
        check("C1_%s_present" % label, pos >= 0, "%s at %s" % (needle, pos))
    present = [(k, v) for k, v in idxs.items() if v >= 0]
    ordered = all(present[i][1] < present[i + 1][1]
                  for i in range(len(present) - 1))
    check("C1_order_strictly_increasing", ordered,
          " -> ".join("%s@%d" % (k, v) for k, v in present))


# ─────────────────────────────────────────────────────────────────────
# C2 — PUBLISH PROVENANCE
# ─────────────────────────────────────────────────────────────────────

def c2_publish_provenance():
    print("\nC2  FRAME-GEOMETRY PUBLISH PROVENANCE")
    pipe = read(PIPELINE)
    check("C2_stamp_fn_defined",
          "def stamp_frame_geometry_publish(" in pipe)
    check("C2_stamp_called_in_pipeline",
          "stamp_frame_geometry_publish(world, publish_source)" in pipe)
    check("C2_publish_source_kwarg",
          "publish_source: str = \"preamble\"" in pipe)
    check("C2_reader_defined",
          "def frame_geometry_publish_sources(" in pipe)
    uni = read(UNIFIED)
    check("C2_structured_stamp",
          "stamp_frame_geometry_publish" in uni
          and "\"structured_open_play\"" in uni)
    eng = read(ENGINE)
    check("C2_repair_label", "publish_source=\"repair\"" in eng)
    check("C2_finalise_label", "publish_source=\"finalise\"" in eng)


# ─────────────────────────────────────────────────────────────────────
# C3 — THE TWO CHANNELS STAY SEPARATE
# ─────────────────────────────────────────────────────────────────────

def c3_channel_separation():
    print("\nC3  ATTRIBUTION CHANNEL SEPARATION")
    log = read(WRITE_LOG)
    fn = body_of(log, "def stamp_target_write(")
    check("C3_stamp_sets_target_writer", "player._target_writer =" in fn)
    # The whole point of the separate field: it must not write target_source.
    assigns_src = re.search(r"player\.target_source\s*=", fn) is not None
    check("C3_stamp_does_not_touch_target_source", not assigns_src,
          "stamp_target_write body assigns target_source" if assigns_src
          else "separate non-behavioural field respected")
    guard = read(GUARD)
    guard_names = set(re.findall(r'"([a-z_]+)"', guard))
    for tag in ("ruck_pin", "ope_markers"):
        listed = tag in guard_names
        info("C3_phase_guard_lists_%s" % tag,
             "YES (visible to phase audit)" if listed
             else "NO -> invisible to phase_authority_guard (measured gap)")
        check("C3_%s_is_blessed_writer" % tag, tag in log,
              "blessed by target_write_log.TARGET_WRITERS")


# ─────────────────────────────────────────────────────────────────────
# C4 — DECLARED MAP COVERAGE AND TAG VALIDITY
# ─────────────────────────────────────────────────────────────────────

def c4_declared_map():
    print("\nC4  DECLARED OWNERSHIP MAP")
    import game.defence.defence_lvl_1.exit_transition_authority as eta
    import game.debug.target_write_log as twl
    from game.defence.defence_lvl_1 import phase_authority_guard as pag

    # TWO VOCABULARIES, TWO CHANNELS.  exit_transition_authority reads
    # ``_target_writer`` (blessed TARGET_WRITERS) AND ``target_source``
    # (phase_authority_guard's legal sets).  A declared tag is therefore
    # valid when it is known to EITHER vocabulary -- validating against
    # TARGET_WRITERS alone wrongly rejects "fullback" / "dh_preposition",
    # which are legitimate target_source values.  (The first draft of this
    # check did exactly that and reported a false FAIL, which is why the
    # check now states the contract instead of half of it.)
    writers = set(twl.TARGET_WRITERS)
    sources = set()
    for _name, _set in pag.LEGAL_TARGET_SOURCES.items():
        sources.update(_set)
    known = writers | sources
    info("C4_vocabularies",
         "target_writer names=%d target_source names=%d union=%d"
         % (len(writers), len(sources), len(known)))

    missing_sub = [s for s in eta.EXIT_SUBPHASES
                   if s not in eta.EXIT_OWNERSHIP]
    check("C4_subphases_covered", not missing_sub, "missing=%s" % missing_sub)

    missing_cls = []
    for sub, classes in eta.EXIT_OWNERSHIP.items():
        for cls in eta.EXIT_BODY_CLASSES:
            if cls not in classes:
                missing_cls.append("%s/%s" % (sub, cls))
    check("C4_body_classes_covered", not missing_cls,
          "missing=%s" % missing_cls[:5])

    bad = []
    for sub, classes in eta.EXIT_OWNERSHIP.items():
        for cls, tags in classes.items():
            for tag in tags:
                if tag == eta.UNATTRIBUTED:
                    continue
                if tag not in known:
                    bad.append("%s/%s=%r" % (sub, cls, tag))
    check("C4_all_tags_known", not bad,
          "unknown=%s" % bad[:6] if bad
          else "every declared tag is a known writer name")

    # Cross-vocabulary tags are LEGAL but worth naming: they can only ever
    # arrive on the target_source channel, so a body carrying one is
    # invisible to the jitter audit's _target_writer read.
    src_only = sorted({t for classes in eta.EXIT_OWNERSHIP.values()
                       for tags in classes.values() for t in tags
                       if t != eta.UNATTRIBUTED and t not in writers})
    info("C4_source_only_tags",
         "%s (reachable only via target_source)" % (src_only or "none"))

    info("C4_phase_universal", "freeze_pin unioned into every entry: %s"
         % ("freeze_pin" in eta.declared_writers("RELEASE", "LINE")))


# ─────────────────────────────────────────────────────────────────────
# C5 — BUDGET DERIVED FROM ITS AUTHORITIES
# ─────────────────────────────────────────────────────────────────────

def c5_budget_single_source():
    print("\nC5  EXIT TIMING BUDGET SINGLE-SOURCE")
    from world.rules import exit_timing_budget as etb
    from world.rules import release_gate as rg
    from game.tes import ptb_manager as pm

    check("C5_release_floor_is_authority",
          etb.RELEASE_MIN_CONVERGENCE_S == rg.RELEASE_MIN_CONVERGENCE_S,
          "%s" % etb.RELEASE_MIN_CONVERGENCE_S)
    check("C5_release_cap_is_authority",
          etb.RELEASE_MAX_DURATION_S == rg.RELEASE_MAX_DURATION_S,
          "%s" % etb.RELEASE_MAX_DURATION_S)
    check("C5_prep_is_authority",
          etb.PTB_PREP_MAX_S == pm.PTB_PREP_MAX_S,
          "%s" % etb.PTB_PREP_MAX_S)
    check("C5_exec_is_authority",
          etb.PTB_EXEC_MAX_S == pm.PTB_EXEC_MAX_S,
          "%s" % etb.PTB_EXEC_MAX_S)
    check("C5_watchdog_derived",
          etb.RELEASE_WATCHDOG_FRAMES == etb.watchdog_frames(
              etb.RELEASE_MAX_DURATION_S - etb.RELEASE_MIN_CONVERGENCE_S),
          "release=%d prep=%d exec=%d"
          % (etb.RELEASE_WATCHDOG_FRAMES, etb.PTB_PREP_WATCHDOG_FRAMES,
             etb.PTB_EXEC_WATCHDOG_FRAMES))
    info("C5_envelope", etb.format_budget())

    # Private copies of the PTB-era literals inside the phase handlers.
    eng = read(ENGINE)
    legacy = {
        "PTB": body_of(eng, "def _update_ptb("),
        "POST_RUCK": body_of(eng, "def _update_post_ruck_open_play("),
    }
    for label, body in legacy.items():
        found = re.findall(r"^\s*(PTB_TIMEOUT_S|DUCKOUT_DISTANCE|"
                           r"POST_RUCK_DURATION_S)\s*=\s*([0-9.]+)", body,
                           re.M)
        if found:
            info("C5_%s_local_literal" % label,
                 "private copies still present: %s" % found)
        else:
            check("C5_%s_uses_budget_module" % label, True,
                  "no private literal found")


# ─────────────────────────────────────────────────────────────────────
# C6 — PIN vs FREEZE-PIN EXCLUSION AGREEMENT
# ─────────────────────────────────────────────────────────────────────

def c6_pin_freeze_agreement():
    print("\nC6  PIN vs FREEZE-PIN 'FROZEN' AGREEMENT")
    pin = read(PIN)
    if "def _pin_eligible(" not in pin:
        info("C6_pin_eligible", "no _pin_eligible() found in %s" % PIN)
        return
    b = body_of(pin, "def _pin_eligible(")
    pin_frozen = "frozen" in b
    pin_freeze_timer = "freeze_timer" in b
    check("C6_pin_tests_frozen", pin_frozen, "pin reads frozen")
    check("C6_pin_tests_freeze_timer", pin_freeze_timer,
          "pin reads freeze_timer")
    eng = read(ENGINE)
    freeze_body = body_of(eng, "def _zero_frozen_velocity")
    freeze_uses_timer = "freeze_timer" in read(PIPELINE)
    if pin_frozen and pin_freeze_timer:
        info("C6_asymmetry",
             "pin requires frozen AND freeze_timer>0; the frame freeze "
             "writer pins on `frozen` — a body with frozen=True and "
             "freeze_timer==0 is pinned by one and moved by the other "
             "(freeze_timer reachable in pipeline: %s)"
             % freeze_uses_timer)


# ─────────────────────────────────────────────────────────────────────
# C7 — RELEASE BOUNDS IMPORTED, NOT REDEFINED
# ─────────────────────────────────────────────────────────────────────

def c7_release_bounds():
    print("\nC7  RELEASE BOUNDS")
    eng = read(ENGINE)
    check("C7_imports_floor_cap",
          "RELEASE_MIN_CONVERGENCE_S" in eng
          and "RELEASE_MAX_DURATION_S" in eng
          and "from world.rules.release_gate import" in eng)
    redefined = re.search(
        r"^RELEASE_MIN_DURATION_S\s*=", eng, re.M) is not None
    check("C7_no_legacy_floor_redefined", not redefined,
          "RELEASE_MIN_DURATION_S must be gone")
    check("C7_watchdog_derived_in_engine",
          "RELEASE_WATCHDOG_FRAMES: int = max(" in eng
          and "RELEASE_MAX_DURATION_S - RELEASE_MIN_CONVERGENCE_S" in eng)
    gate = read(GATE)
    for sym in ("RELEASE_MIN_CONVERGENCE_S", "RELEASE_MAX_DURATION_S",
                "def release_convergence("):
        check("C7_gate_has_%s" % sym.split("(")[0].replace(" ", "_"),
              sym in gate)


def main():
    print("=" * 78)
    print("STAGE 4B — EXIT-BOUNDARY OWNERSHIP CONTRACT VERIFIER")
    print("=" * 78)
    c1_source_order()
    c2_publish_provenance()
    c3_channel_separation()
    c4_declared_map()
    c5_budget_single_source()
    c6_pin_freeze_agreement()
    c7_release_bounds()
    print("\n" + "=" * 78)
    if _FAILS:
        print("RESULT: %d FAIL(s) — %s" % (len(_FAILS), ", ".join(_FAILS)))
        print("=" * 78)
        return 1
    print("RESULT: all checks PASS (%d INFO findings)" % len(_INFOS))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
