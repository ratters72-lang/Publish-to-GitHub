"""FB continuity/geometry telemetry runner for CI.

Runs the fullback role/geometry suite and emits a single machine-readable
JSON line to stdout containing, per verifier:

    { name, result, active_fb_frames, multi_active_frame,
      sideline_fb_frame, wrong_target_source_frame }

Exits non-zero if any verifier reports a non-PASS result, so CI fails the
job when the single-FB invariant or the fullback-geometry authority breaks.
"""
import json
import subprocess
import sys
import tempfile
import os

VERIFIERS = [
    "verify_fullback_continuity.py",
    "verify_fullback_geometry.py",
    "verify_fullback_role.py",
    "verify_fullback_priority.py",
    "verify_kickoff_fullback.py",
    "verify_fullback_absorb.py",
    "verify_fullback_invariant_audit.py",
    # 2026-08-28: live-kickoff post-catch absorption-grace regression probe —
    # gates the fix that keeps the kickoff FB from being dissolved into the
    # line right after reception when the post-catch anchor coincides with
    # its flat-line position.
    "verify_post_catch_fb_absorb_grace.py",
    # 2026-09-16: the controlled emergency-defender path (B2b).  Gates the
    # law that TACKLE ELIGIBILITY and LINE MEMBERSHIP are separate: the
    # tagged fullback may enter lane candidacy / the pre-tackle pool /
    # the corridor / TES arming on a GENUINE line break while never being
    # absorbed into the line, and the tag is never revoked by arming.
    "verify_fullback_emergency_defender.py",
    # 2026-09-21: HIGH-VELOCITY RECOVERY LOCOMOTION.  Gates the three-part
    # fix for "fullback structural immobility": the RECOVER movement state
    # (tables + transitions + dampener bypass), the engine-armed recovery
    # predicate and approach-cap exemption, the explicit kickoff-flight mode
    # of the geometry authority and its two KickoffEngine call sites, and the
    # role-persistence law (the deleted CHASE/SHOOT tag demotion).
    "verify_fullback_recover_contract.py",
    # 2026-09-21: LIVE end-to-end proof for the same feature.  The contract
    # verifier uses stubs; this one boots the real engine, runs the seeded
    # rollout and asserts RECOVER survives to the END of the frame (no later
    # writer clobbers it), that the run really did owe > FB_RECOVER_DEBT_M of
    # ground, that RECOVER ran above the sweeper approach cap, and that
    # RLM_FULLBACK_RECOVER=0 zeroes it out (in-process A/B).
    "verify_fullback_recover_live.py",
    # 2026-09-22: LATERAL TRACKING.  Gates the negative result (the 0.25
    # LATERAL_PRIORITY_BY_STATE[RECOVER] entry is NOT on the sweeper's path)
    # together with the contract that replaced the three real Y-axis
    # limiters: the 2D-distance speed helpers, the open-play lateral
    # mirroring of the carrier's lane, and the 2D break/step-out gate.
    "verify_fullback_lateral_priority_exclusion.py",
    # 2026-09-22: the dedicated open-play lateral contract — the lever and its
    # constants, the anchor mirror, the lane destination, the 2D gate, the
    # source wiring of the 2D metric, and a bounded LIVE A/B proving the
    # shipped default leaves a ZERO destination gap while the
    # RLM_FB_LATERAL_OPEN_PLAY=0 rollback still gaps.
    "verify_fullback_lateral_open_play_contract.py",
    # 2026-09-23: THE X-AXIS DEADLOCK (frame-1814).  The tagged fullback held
    # static at x = 63.80 while the carrier streamed 106.35 -> 111.49: the
    # emergency-defender yield left the saturated sweeper anchor standing as
    # the command, and the dispatch's line_break_static_guard hard-exempts the
    # tagged fullback, so nothing recovered it.  Gates the pursuit clearance
    # (target_x must advance downfield on every authority path), its A/B
    # rollback lever, and the untouched sweeper / lateral-break contracts.
    "verify_fullback_line_break_chase.py",
    # 2026-09-23: THE PURE-DATA LEAF's structural contract.  Gates the
    # 21-field record / 20-field body row / seven-field carrier snapshot (both
    # widened by Stage 3.1's two skill-trait placeholders, appended last and
    # NEUTRAL at 1.0) / six-member closed vocabulary,
    # the STRING type annotations and their resolution by get_type_hints (a
    # shape test that compared types to strings could never fail), the
    # derived views that dataclasses.fields() cannot see, and a mocked
    # three-role proximity frame in which the in-radius row the decider's
    # short-circuit never reached survives as IN_RADIUS_NOT_EVALUATED.
    # Emits a literal "RESULT: PASS", which this harness's parser requires.
    "verify_contest_record_schema_contract.py",
    # 2026-09-23: THE MULTI-BODY CONSUMPTION.  Stage 1B declared the contest,
    # Stage 2A preserved the complete declaration, Stage 2B made it
    # transportable — and nothing consumed the multi-body part, so a two-body
    # and a six-body tackle resolved identically.  Gates the leaf's membership
    # law, the collaborative wrap, the arm-grab break window, the offload
    # window, the no-op/bounds law, the A/B lever, the REAL TES entry point
    # attaching the model, the four consumer functions, the engine's own
    # break-threshold consumption, and the leaf's reachability.
    "verify_tackle_exchange_multi_body_contract.py",
    # 2026-09-24: THE BUMP CLEARANCE PATH (dynamic results pipeline, step 2).
    # Step 1 derived the collision-state token; nothing read it.  This stage
    # makes it act: a declaration that resolved BUMP_OFF tears the wrap down
    # instead of grinding the carrier through a pre-baked tackle.  Gates the
    # A/B lever, the predicate (BUMP_OFF and nothing else), the carrier's
    # momentum-preserving burst window, the cleared TES commit locks, the REAL
    # teardown on a real engine, the unchanged plain-path control arm, the
    # intercept ORDER in all three TES phase handlers, and the battery wiring.
    "verify_bump_clearance_contract.py",
    # 2026-09-27: BEATEN-DEFENDER OWNERSHIP LOSS.  Lane ownership was granted
    # on "a genuine break exists" and released on "the break is gone" --
    # neither test says anything about the FULLBACK, so a body the carrier
    # had carried PAST kept a lane it could no longer intercept (measured:
    # FB.x 46.1, BC.x 55.4 -- holding PRIMARY 9.3 m behind the carrier).  The
    # law is the negative-side twin of the lane system's one-sided
    # `d.x <= bc_x + ahead_engage_m` band.  Gates the geometry predicate (and
    # a bad-band fallback), the ownership gate, BOTH admission sites (the
    # lane gate and the banded `selectable` pool, with a lever-OFF control
    # proving that fixture reaches the re-grant path), the release contract
    # (the whole stamp package: lane role + commit + stance shims), the
    # end-to-end re-ownership by the geometry authority's chase branch, the
    # commit-flag handover ordering, no lock-out on re-entry, and the source
    # wiring of all three sites.
    "verify_fullback_beaten_ownership_loss.py",
    # 2026-09-28: THE EMERGENCY-CHASE TARGET AXIS.  Every emergency-chase
    # writer aimed the last defender at the carrier's LIVE lane offset by a
    # fixed X-ONLY trail, so the Y axis was a measurement and never a
    # prediction -- the body crossed the carrier's lane and the lateral error
    # GREW as the X error closed.  Gates the pure classifier the audit
    # measured with (labels, epsilon band, heading-relative ahead tests,
    # best-fit lead recovery, the crossing counter's coincidence band, the
    # summary means), the intercept law (degenerate equivalence at rest, the
    # near-range taper, the taper ramp, the LEAD_MAX projection cap, the
    # ahead/trailing bounds, the field bound, the anchor floor, totality,
    # purity), and the RLM_FB_CHASE_INTERCEPT=0 rollback BYTE-FOR-BYTE.
    "verify_fb_chase_target_axis.py",
    # 2026-09-28: THE ANCHOR-RELOCK BREAK LATCH.  The authority-timeline
    # probe (seed 20260827, window 16) captured the FIRST live
    # fullback_geometry CHASE -> DRIFT transition on frames 382/383 with the
    # CARRIER UNMOVED at 26.52: the tackle cycle re-locked ruck_x_m from 8.25
    # onto the carrier at the CONTACT -> DRIVE entry, so the line anchor
    # jumped +18.27 m GOAL-SIDE of a live break, G10a DEPTH fell through and
    # the LINE-INTACT LEVEL exclusion (G10b) returned FB_BREAK_NONE -- the
    # sweeper anchor + DRIFT were then written under the same writer with no
    # engine event (the probe's MODE_OVERWRITE).  Gates the law that a
    # FORWARD anchor relock may not retroactively un-classify a live chase:
    # the single reader that floors the live anchor with the latched one, the
    # latched DEPTH twin, every classify consumer (geometry writer, live-break
    # establishment, breakaway/lateral/depth/candidacy predicates, MMDL), the
    # explicit cycle-end sweep at both engine teardown sites, the tag-loss
    # clear, the lever-gated explicit relock handover backstop, and the
    # write-free RLM_FB_BREAK_LATCH=0 rollback.  The lever-OFF arm reproduces
    # the recorded f=383 target (48.44) and mode (DRIFT) VERBATIM.
    "verify_fullback_break_latch_contract.py",
    # 2026-09-29: THE DEFENSIVE-LINE OCCUPANCY GATE.  A kickoff reception
    # seeds ``ruck_x_m = bc.x``, so the post-catch "line" is manufactured
    # 10 m in front of the catch with the whole kicking team still
    # goal-side of it; the carrier then only has to run ~11 m to trip the
    # DEPTH leg.  MEASURED (probe_fb_kick_return_admission.py, seed
    # 20260827): the break first fires at f=309 with the carrier 11.03 m
    # past the reception and ALL TWELVE other defenders still AHEAD of the
    # anchor -- 0 behind it, 0 on it -- and the fullback ran the carrier
    # down; six of eight swept seeds reproduced it, every one through the
    # DEPTH leg (the LATERAL leg fired in no seed).  Gates the law that a
    # line the defence has not reached cannot be broken: the occupancy
    # predicate, its band and self-exclusion, the scoped empty-world
    # passthrough that keeps every stub harness byte-identical, the
    # conjunct on every classify consumer, the REAL geometry writer on
    # both fixtures, and the RLM_FB_LINE_OCCUPIED=0 rollback.
    "verify_fullback_line_occupied_contract.py",
    # 2026-09-30: THE EMERGENCY-CHASE ADMISSION LEDGER.  The gate that admits
    # the last defender answered a bare ``False`` on every refusal, so WHICH
    # conjunct refused, how wide the line was standing, how far the carrier
    # was from the line and how close the nearest defender was all had to be
    # re-derived afterwards by a bespoke probe.  This gates the ledger that
    # logs every rejected line-break evaluation ONCE with its own leg, its
    # own reason and those three diagnostics: the closed verdict vocabulary
    # and its identity / line-break partition, the three measurements (and
    # their honest ``None`` -- never a fabricated zero), the bounded and
    # deduplicated ledger (repeat_count + a ceiling that reports its own
    # drops), the armed / disarmed verdict IDENTITY on every fixture, every
    # reason the gate can produce actually produced through the REAL gate,
    # the ADMITTED row measuring the BREAK TEST anchor its verdict used (so a
    # latched chase cannot report a negative gap) rather than the relocked
    # live line, the lateral label agreeing with the classifier's own
    # constants over a geometry sweep, the source wiring of all three sites
    # (including the contracts the other FB verifiers pin), the CSV export,
    # and the degenerate-world tolerance that keeps the ledger from ever
    # raising inside a movement predicate.
    "verify_fb_admission_audit_contract.py",
    # 2026-09-29: THE INTERCEPT-TRACKING CONSUMPTION.  The chase POINT the
    # tagged fullback is handed on a line break is already an intercept
    # (velocity projection + goal-side margin), but the CONSUMPTION steered
    # the body with the shared X-dominant CHASE blend
    # (LATERAL_PRIORITY_BY_STATE[CHASE] = 0.3) and then damped what little
    # lateral command it produced: BC_LATERAL_DAMP 0.60 on any carrier whose
    # own |vy| exceeded 1.0 m/s (i.e. every diagonal break), the band weight,
    # and the item-5 spacing-Y freeze inside 1.5 m -- which re-points
    # ``target_y`` at the body's OWN lane and zeroes the lateral command
    # outright.  Measured result: the X error collapsed while the lateral
    # error GREW, the body crossed the carrier's lane and re-acquired it late.
    # Gates the fullback-scoped bearing steering, the two alignment
    # exemptions, the untouched shared CHASE row / acceleration / speed table,
    # the untouched DRIFT / RECOVER paths, the surviving collapse-commit and
    # arrival authority, a 150-frame END-TO-END chase (real
    # ``fullback_chase_point`` feeding the real integrator) in which the
    # interceptor ends closer to the carrier and reaches the lateral contact
    # band where the historical leg does not, and the
    # RLM_FB_CHASE_INTERCEPT_TRACK=0 rollback.
    "verify_fb_chase_intercept_tracking.py",
    # 2026-09-29: THE PRE-TACKLE SLOW PACING OF A CHASING FULLBACK ("once the
    # carrier passes the fullback the pursuing body stalls or dramatically
    # reduces pursuit instead of turning and continuing the chase").
    # ``pre_tackle_authority.is_pre_tackle_eligible`` DOCUMENTS that the
    # dynamic fullback is excluded ("geometry-driven only") but never
    # implemented the check, and the fullback keeps DEF_LINE_READY by design,
    # so it PASSED for the last defender.  The consequence is a split
    # locomotion contract: ``fullback_role`` owns the chase target and the
    # movement state while the pre-tackle authority owns the PACE.  MEASURED
    # (probe_fb_chase_write_sequence.py, seed 20260827, frames 800-812): the
    # chase runs at move_speed 9.50 / v=(-7.68, 3.16) and collapses the
    # moment the body closes inside the ~2.0 m promote radius --
    # move_speed 1.50 -> 2.10 -> 2.34 -> 2.50, i.e. exactly
    # ``_slow_step``'s min(pretackle_slow_speed_mps, dist * 3.0) with the
    # 2.5 cap -- turning the pursuer +X, AWAY from the carrier, 28 frames
    # BEFORE the crossing the report was filed against.  It LATCHES: 2.5 m/s
    # cannot close a carrier at ~7 m/s and the carrier is closing on the
    # body, so the separation (and the SLOW ownership) persists.  Gates the
    # exemption predicate and its scope (live chase only, latch OR CHASE
    # state), the eligibility refusal, the blast radius on every body it
    # must NOT touch, the OPEN_PLAY publisher and the per-frame application
    # on the REAL code, the measured ramp fingerprint, and the
    # RLM_FB_PRETACKLE_CHASE_EXEMPT=0 rollback.
    "verify_fb_pretackle_chase_exempt.py",
    # 2026-09-30: STAGE 1 — INTENT OWNERSHIP.  The last of the fullback
    # split-brains is the DECISION itself: ``fullback_role`` decided the
    # sweeper anchor / the breakaway chase / the owed-ground recovery while
    # ``movement_mode_decision`` independently policy-decided DRIFT-or-CHASE
    # for the SAME body, and ``kickoff_engine`` wrote a third, ad-hoc stamp.
    # No layer owned the intent, so on a live break two of them could
    # disagree about what the last defender was doing.  This stage gives the
    # vocabulary (UNOWNED / SWEEP / CHASE / RECOVER / FLIGHT) a single owner
    # and ONE write path.  Gates the vocabulary and its total intent ->
    # MovementState law, the identical-singleton write (behaviour neutrality
    # BY CONSTRUCTION), the write-nothing UNOWNED yield, the
    # bookkeeping-only RLM_FB_INTENT_CONTROLLER lever, the transition law,
    # the source-level ownership boundary across fullback_role /
    # kickoff_engine / movement_mode_decision (7 routed producers and ZERO
    # bare writes in the geometry authority), the controller's freedom from
    # an import cycle, and a LIVE seeded rollout that asserts a tagged
    # fullback is never unstamped and reports (never asserts) the residual
    # intent/state split-brain that Stage 2 State Ownership removes.
    "verify_fullback_intent_ownership_contract.py",
    # 2026-09-30: STAGE 2 — STATE OWNERSHIP.  Stage 1 gave the last defender
    # ONE intent vocabulary and ONE write path, but the STATE itself was still
    # named by the shared defender policy: the MMDL's fullback branch decided
    # DRIFT-or-CHASE for the same body, and the sweeper pin in
    # ``defensive_behaviour_unified`` re-pinned ``MovementState.DRIFT`` +
    # ``target_x = anchor_x`` whenever ``fullback_breakaway_live`` was False.
    # That predicate admits a DEPTH latch but NOT a LATERAL one, so on a
    # lateral channel breach the pin overwrote the CHASE the geometry
    # authority had just ordered -- ``_fb_intent = CHASE`` against a live
    # state of DRIFT, the measured split-brain.  This stage makes the
    # controller the SOLE authority for a tagged fullback's movement state
    # while its BREAK EMERGENCY is live (the per-actor break latch is armed),
    # and hands the frame back to the shared policy the moment the latch
    # clears.  Gates the ownership truth table (tag + latch + lever), the
    # DEPTH/LATERAL equivalence the old gate lacked, the sole-authority write
    # path and its write-nothing yield, the MMDL's DEFERENCE to the
    # controller's verdict (proved by monkeypatching it), the release
    # contract, the pin's A/B on a lateral break (CHASE survives vs CHASE
    # clobbered to DRIFT), the unchanged depth / no-break / untagged
    # controls, the source wiring of all three writers, and the write-free
    # RLM_FB_STATE_CONTROLLER=0 rollback.
    "verify_fullback_state_ownership_contract.py",
    # 2026-09-30: STAGE 3 — RECOVERY OWNERSHIP.  Stages 1 and 2 gave the last
    # defender ONE intent and ONE state authority; its RECOVERY was still
    # governed by four shared pathways that each restated the same exemption
    # inline (``getattr(d, "fullback_role_active", False)`` in the shared
    # recovery predicate, the static guard's write, the freeze-expiry
    # hand-back and the post-miss entitlement).  The behaviour was right and
    # the OWNERSHIP was not: one law restated four times, un-A/B-able and
    # un-auditable.  This stage makes the controller the single owner of the
    # fullback's recovery contract -- the tagged fullback is removed from the
    # shared recovery, freeze-expiry, post-miss pursuit and static-guard
    # pathways, and its misses, owed-ground recoveries and break re-engagement
    # become fullback behaviours resolved by ``recovery_intent`` and declared
    # through ``declare_fullback_recovery``.  Gates the lever, the ownership
    # truth table and its exact negation, the four pathways denying the tagged
    # body on a live break (each against an untagged control), the DEFERENCE
    # proved by monkeypatching the controller's predicate (inert with the
    # lever off), the single recovery decision (CHASE / RECOVER / SWEEP /
    # UNOWNED) and its write-free rollback, the miss contract surviving the
    # shared lever, the source wiring of all four readers with no import cycle
    # and the Stage-1 write-path counts intact, and the degenerate inputs.
    "verify_fullback_recovery_ownership_contract.py",
    # 2026-09-30: STAGE 4 — LOCOMOTION OWNERSHIP.  Stages 1-3 gave the last
    # defender ONE intent, ONE state authority and ONE recovery contract, but
    # its STEERING was still the shared defender model: on a live break the
    # emergency pursuit / interception ran on the shared CHASE row (X-dominant,
    # lateral-damped, spacing-Y frozen), and on the frame a MISS released the
    # break latch the body fell straight back onto DRIFT / SLIDE /
    # line-defence priorities MID-RECOVERY -- a hole through the Stage-3
    # recovery contract.  This stage gives the actor a DEDICATED locomotion
    # contract resolved from its INTENT, independent of the shared tables.
    # Gates the lever and its write-free rollback, the leaf's two profiles and
    # their shared-equivalence (the default is behaviour-neutral BY
    # CONSTRUCTION), the ownership truth table with its exact negation, DOOR A
    # (the Stage-2 break latch), DOOR B (the LIVE Stage-3 recovery predicate
    # evaluated at the COMMANDED anchor -- proved to be re-evaluated per frame
    # and NOT a persisted stamp), the ONE profile resolver (None for the
    # untagged / sweeper / kickoff-flight frames), the declaration stamps, the
    # CONSUMPTION handing ``params_override`` to ``apply_movement_state`` with
    # the alignment-damping switch honoured, a four-fixture byte-identical
    # lever ON/OFF A/B, the no-import-cycle wiring, and an INDEPENDENCE proof:
    # re-tuning the shared table moves the shared body and does NOT move the
    # owned fullback's.
    "verify_fullback_locomotion_ownership_contract.py",
    # 2026-10-01: THE LATCH-BACKED CHASE HOLD.  The anchor-relock latch
    # (2026-09-28) floors the ANCHOR the break classifier reads but does NOT
    # guarantee the VERDICT: ``apply_fullback_geometry``'s breakaway branch is
    # guarded by the classifier AND the defensive-line occupancy gate, so a
    # frame can still fall through to the SWEEPER branch (CHASE -> DRIFT) under
    # a live chase -- the authority-timeline probe's MODE_OVERWRITE class, no
    # engine event.  Gates the law that a LIVE LATCH IS AUTHORITY FOR PURSUIT
    # OWNERSHIP: the hold predicate and its release rule (N1
    # ``carrier_behind_latched_line``, plus every existing lifecycle clear),
    # the occupancy reversal (N2) held onto the LATCHED kind through the
    # EXISTING pursuit command path byte-for-byte, the N1 release, the
    # no-latch / no-carrier controls, the classifier left un-sticky, and the
    # write-free RLM_FB_BREAK_LATCH=0 rollback.
    "verify_fullback_chase_hold_contract.py",
]

METRICS = (
    "active_fb_frames",
    "multi_active_frame",
    "sideline_fb_frame",
    "wrong_target_source_frame",
)


def _parse(log_text):
    """Return (result, metrics).

    Handles the two result-reporting styles used by the FB verifiers:
      - "RESULT: PASS" / "RESULT: ALL CHECKS PASSED" / "RESULT: FAIL"
      - "PASS: fullback geometry authority ..." / "FAIL: ..."  (geometry)
    A FAIL token anywhere wins over PASS, so a partial failure never
    reports green.
    """
    result = None
    metrics = {}
    saw_pass = False
    saw_fail = False
    for line in log_text.splitlines():
        l = line.strip()
        up = l.upper()
        # RESULT: token
        if "RESULT:" in up:
            chunk = l.split("RESULT:", 1)[1].strip()
            token = chunk.split()[0].upper() if chunk.split() else "UNKNOWN"
            if token == "PASS":
                result = "PASS"
                saw_pass = True
            elif token == "ALL":
                result = "PASS"
                saw_pass = True
            else:
                result = token
                saw_fail = True
        # "PASS:" / "FAIL:" prefix (geometry verifier's summary)
        if up.startswith("PASS:"):
            saw_pass = True
            if result is None:
                result = "PASS"
        if up.startswith("FAIL:"):
            saw_fail = True
            result = "FAIL"
        if "ALL CHECKS PASSED" in up:
            saw_pass = True
            if result in (None, "UNKNOWN"):
                result = "PASS"
        for key in METRICS:
            if f"{key}=" in line:
                val = line.split(f"{key}=", 1)[1].split()[0].strip()
                metrics[key] = val
    if saw_fail:
        return "FAIL", metrics
    if result is None:
        return ("PASS" if saw_pass else "UNKNOWN"), metrics
    return result, metrics


def main():
    out = {}
    failed = False
    for name in VERIFIERS:
        if not os.path.exists(name):
            out[name] = {"result": "SKIP", "reason": "not found"}
            continue
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False, encoding="utf-8"
        ) as tf:
            log_path = tf.name
        try:
            with open(log_path, "w", encoding="utf-8") as lf:
                subprocess.run(
                    [sys.executable, name],
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            with open(log_path, encoding="utf-8", errors="replace") as lf:
                text = lf.read()
            result, metrics = _parse(text)
            out[name] = {"result": result or "UNKNOWN", **metrics}
            if result != "PASS":
                failed = True
        finally:
            try:
                os.remove(log_path)
            except OSError:
                pass

    print("FULLBACK_TELEMETRY_JSON: " + json.dumps(out, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
