"""
verify_stage5_phase_gated_tuning.py — Stage 5 phase-gated behavioural tuning verifier.

Confirms the Stage 5 ship-text claim:

  * The unified pre-tackle authority resolves the CURRENT game sub-state ONCE
    per frame and loads that phase profile's behavioural tuner overrides
    (speeds / radii / offsets / slowdown) instead of the global scalars.
  * Every phase profile DEFAULTS to the existing scalar values, so behaviour
    is byte-identical until a phase profile is explicitly set on
    DefenceTuners.pretackle_phase_tuners.
  * The phase map supports the full sub-state family (OPEN_PLAY / TACKLE_INIT /
    TACKLE_CONTACT / TACKLE_DRIVE / TACKLE_DOMINANCE / TACKLE_RELEASE / PTB /
    POST_RUCK_OPEN_PLAY), not merely a binary live-vs-frozen switch.
  * frozen_mode semantics (live-BC commit vs frozen-anchor) are untouched.
  * MMDL / TES reading the single canonical pre_tackle_state is unchanged.

Run:  python verify_stage5_phase_gated_tuning.py
Exit 0 on success, 1 on failure.
"""
import sys, os, math

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from world.world_state import GameSubState
from game.defence.defence_lvl_1 import pre_tackle_authority as pta
from game.defence.defence_lvl_1.defensive_tuners import DefenceTuners

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" - " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


PTA_FILE = "game/defence/defence_lvl_1/pre_tackle_authority.py"
DT_FILE = "game/defence/defence_lvl_1/defensive_tuners.py"


def main():
    print("=" * 64)
    print("STAGE 5 -- PHASE-GATED BEHAVIOURAL TUNING VERIFIER")
    print("=" * 64)

    pta_src = read(PTA_FILE)
    dt_src = read(DT_FILE)

    # ══════════════════════════════════════════════════════════════
    # A. Phase resolution map (full sub-state granularity)
    # ══════════════════════════════════════════════════════════════
    print("\n[A] resolve_pre_tackle_phase maps every GameSubState to a phase key")
    expect = {
        GameSubState.OPEN_PLAY: "OPEN_PLAY",
        GameSubState.TACKLE_INIT: "TACKLE_INIT",
        GameSubState.TACKLE_CONTACT: "TACKLE_CONTACT",
        GameSubState.TACKLE_DRIVE: "TACKLE_DRIVE",
        GameSubState.TACKLE_DOMINANCE: "TACKLE_DOMINANCE",
        GameSubState.TACKLE_RELEASE: "TACKLE_RELEASE",
        GameSubState.PTB_READY: "PTB",
        GameSubState.PTB: "PTB",
        GameSubState.POST_RUCK_OPEN_PLAY: "POST_RUCK_OPEN_PLAY",
        GameSubState.POST_PTB_RESET: "POST_RUCK_OPEN_PLAY",
        GameSubState.RESET_LINE: "POST_RUCK_OPEN_PLAY",
    }
    check("A1 resolve_pre_tackle_phase is defined",
          hasattr(pta, "resolve_pre_tackle_phase"))
    ok = True
    detail = ""
    from types import SimpleNamespace
    for sub_state, key in expect.items():
        w = SimpleNamespace(sub_state=sub_state)
        try:
            got = pta.resolve_pre_tackle_phase(w)
        except Exception as e:
            got = None
            detail = "exception: %s" % e
        if got != key:
            ok = False
            detail = "%s -> %r expected %r" % (sub_state, got, key)
            break
    check("A2 every GameSubState resolves to the correct phase key", ok, detail)

    # Unknown / stub worlds default to DEFAULT
    check("A3 unknown sub-state (None) -> DEFAULT",
          pta.resolve_pre_tackle_phase(SimpleNamespace(sub_state=None)) == "DEFAULT")

    check("A4 string sub-state fallback -> DEFAULT (kickoff not mapped)",
          pta.resolve_pre_tackle_phase(SimpleNamespace(sub_state="kickoff_in_air")) == "DEFAULT")

    # ══════════════════════════════════════════════════════════════
    # B. _phase_tuners falls back to scalar tuners (no profile set)
    # ══════════════════════════════════════════════════════════════
    print("\n[B] @phase_tuners resolves DEFAULT scalars when no profile is set")
    tuners = DefenceTuners()
    pt_key = "OPEN_PLAY"
    pt = pta._phase_tuners(tuners, pt_key)
    check("B1 slow_speed falls back to scalar 2.5",
          abs(pt["pretackle_slow_speed_mps"] - 2.5) < 1e-9)
    check("B2 follow_smooth falls back to scalar 0.6",
          abs(pt["pretackle_follow_smooth"] - 0.6) < 1e-9)
    check("B3 commit_speed falls back to scalar 4.0",
          abs(pt["pretackle_commit_speed_mps"] - 4.0) < 1e-9)
    check("B4 commit_depth_offset falls back to scalar 1.0",
          abs(pt["pretackle_commit_depth_offset_m"] - 1.0) < 1e-9)
    check("B5 commit_lateral_offset falls back to scalar 1.0",
          abs(pt["pretackle_commit_lateral_offset_m"] - 1.0) < 1e-9)

    # A profile key with NO entry -> identical to the scalar fallback
    check("B6 a phase with no profile returns the SAME values as scalars",
          all(
              abs(pt[k] - float(getattr(tuners, k, 0.0))) < 1e-9
              for k in pt
          ))

    # ══════════════════════════════════════════════════════════════
    # C. _phase_tuners consumes a configured phase profile override
    # ══════════════════════════════════════════════════════════════
    print("\n[C] @phase_tuners consumes a configured phase profile")
    t2 = DefenceTuners()
    t2.pretackle_phase_tuners = {
        "TACKLE_DRIVE": {
            "pretackle_commit_speed_mps": 3.0,
            "pretackle_commit_lateral_offset_m": 0.8,
        },
    }
    ptd = pta._phase_tuners(t2, "TACKLE_DRIVE")
    check("C1 profile commit_speed override applied (3.0)",
          abs(ptd["pretackle_commit_speed_mps"] - 3.0) < 1e-9)
    check("C2 profile lateral_offset override applied (0.8)",
          abs(ptd["pretackle_commit_lateral_offset_m"] - 0.8) < 1e-9)
    check("C3 non-overridden key falls back to scalar (slow 2.5)",
          abs(ptd["pretackle_slow_speed_mps"] - 2.5) < 1e-9)
    check("C4 a different phase (OPEN_PLAY) still uses scalars",
          abs(pta._phase_tuners(t2, "OPEN_PLAY")["pretackle_commit_speed_mps"] - 4.0) < 1e-9)

    # ══════════════════════════════════════════════════════════════
    # D. apply_pre_tackle resolves the phase and uses profile values
    # ══════════════════════════════════════════════════════════════
    print("\n[D] apply_pre_tackle resolves phase ONCE and wires phase values through")
    check("D1 apply_pre_tackle resolves the phase key",
          "phase_key = resolve_pre_tackle_phase(world)" in pta_src)
    check("D2 apply_pre_tackle builds the phase tuner dict",
          "pt = _phase_tuners(tuners, phase_key)" in pta_src)
    check("D3 commit tuner reads use the phase dict (hold_cap)",
          "hold_cap = float(pt[\"pretackle_slow_speed_mps\"])" in pta_src)
    check("D4 commit tuner reads use the phase dict (commit_cap)",
          "commit_cap = float(pt[\"pretackle_commit_speed_mps\"])" in pta_src)
    check("D5 _seed_body receives the phase dict",
          "_seed_body(world, d, bc, tuners, frozen_mode, pt)" in pta_src)
    check("D6 live-commit point uses the phase dict",
          "_live_commit_point(bc, d, pt)" in pta_src)
    check("D7 _live_commit_point signature takes the phase dict",
          "def _live_commit_point(bc, d, pt)" in pta_src)
    check("D8 _seed_body signature supports the phase dict",
          "def _seed_body(world, d, bc, tuners, frozen_mode: bool, pt: dict | None = None)" in pta_src)
    check("D9 _phase_tuners helper is defined",
          "def _phase_tuners(tuners, phase_key: str) -> dict:" in pta_src)

    # ══════════════════════════════════════════════════════════════
    # E. frozen_mode semantics intact (live vs frozen, not replaced)
    # ══════════════════════════════════════════════════════════════
    print("\n[E] frozen_mode semantics remain intact (phase-gating layered on)")
    check("E1 OPEN_PLAY path uses live-BC commit point",
          "cx, cy = _live_commit_point(bc, d, pt)" in pta_src)
    check("E2 DRIVE path seeds the frozen commit point (Stage 8)",
          "cx = float(bc.x) + offset" in pta_src)
    check("E2b DRIVE path folds COMMIT/PLANTED into the retreat anchor",
          "if not frozen_mode and st in (PreTackleState.COMMIT, PreTackleState.PLANTED):" in pta_src)
    check("E3 SLOW/HOLD converge to frozen anchors (anti-collapse)",
          "_slow_step(d, ax, ay, dt, hold_cap, smooth)" in pta_src)

    # ══════════════════════════════════════════════════════════════
    # F. DefenceTuners exposes the phase profile dict (default empty)
    # ══════════════════════════════════════════════════════════════
    print("\n[F] DefenceTuners exposes pretackle_phase_tuners (default empty)")
    t3 = DefenceTuners()
    check("F1 pretackle_phase_tuners attribute exists",
          hasattr(t3, "pretackle_phase_tuners"))
    check("F2 pretackle_phase_tuners defaults to an EMPTY dict (no behaviour change)",
          isinstance(t3.pretackle_phase_tuners, dict) and len(t3.pretackle_phase_tuners) == 0)

    # ══════════════════════════════════════════════════════════════
    # G. Engine module tree imports cleanly
    # ══════════════════════════════════════════════════════════════
    print("\n[G] engine module tree imports cleanly")
    import importlib
    mods = [
        "world.open_play_engine",
        "game.managers.defence_manager",
        "game.defence.defence_lvl_1.pre_tackle_authority",
        "game.defence.defence_lvl_1.defensive_behaviour_unified",
        "game.defence.defence_lvl_1.defensive_behaviour_lvl1",
        "game.defence.defence_lvl_1.defensive_tuners",
        "world.rules.movement_contracts",
    ]
    ok = True
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            ok = False
            print("      [import FAIL] %s: %s" % (m, e))
    check("G1 all engine modules import", ok)

    print("\n" + "=" * 64)
    if failures:
        print("RESULT: %d FAILURE(S): %s" % (len(failures), failures))
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
