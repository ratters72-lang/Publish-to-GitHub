"""verify_fb_emergency_initiation.py — the chaser must be able to START a tackle.

THE DEFECT THIS PINS DOWN (2026-09-30)
--------------------------------------
``contact_authority.evaluate_contact`` admits a tagged fullback through
CONJUNCT 1 (``fullback_role_active`` + ``can_acquire_tackle``) and then
rejected it at CONJUNCT 2, which accepts ONLY the lane roles
``{PRIMARY, SECONDARY}``.

``intercept_tacklers.select_lane_tacklers`` grants PRIMARY to the smallest
``|d.x - bc.x|`` candidate per side and demotes every other candidate on that
side to ``LINE`` -- so a sweeper the carrier has already run PAST, chasing from
behind, always lost the slot.  ``LINE`` can never start a tackle sequence
(``intercept_tacklers`` spec §4), so the pursuer closed all the way to contact
and then merely travelled alongside the carrier: contact with no contest, so
``tes.py``'s ``bc.frozen = True`` never ran and the pair slid to the tryline.

WHAT IS ASSERTED
----------------
  [1]  a tagged fullback on a genuine break whose ``tackle_role`` is unset --
       the exact live state (None / PASSIVE / LINE) -- now FORMS the contest;

  [2]  a NON-fullback body in the identical state still does NOT form one, so
       the admission is scoped to the emergency fullback and is not a blanket
       unlock of the tackle gate;

  [3]  a tagged fullback with NO genuine break still does NOT form one, so the
       new branch cannot draft the sweeper into a contest outside a break.

The conjunct logic is ISOLATED: this verifier replaces the module's
publication and recorder reaches so it measures the changed conjunct and
nothing else.  End-to-end behaviour is covered by the engine-level probe
(``tmp/fb_close.py``).

Exit code 0 = all sections pass.
"""

import sys
from types import SimpleNamespace

import game.defence.defence_lvl_1.contact_authority as ca

RADIUS_M = 1.5


def _body(player_id, x, y, *, fullback=False, can_tackle=True,
          tackle_role=None):
    """A minimal body carrying exactly the attributes the trigger reads."""
    return SimpleNamespace(
        player_id=player_id,
        x=float(x),
        y=float(y),
        fullback_role_active=bool(fullback),
        can_acquire_tackle=bool(can_tackle),
        tackle_role=tackle_role,
        defender_role=SimpleNamespace(name="DEF_LINE_READY"),
        frozen=False,
        freeze_timer=0.0,
        tackle_state_active=False,
    )


def _world():
    return SimpleNamespace(frame_count=1, _phase="OPEN_PLAY")


def _ope(verdict="CONTACT"):
    return SimpleNamespace(
        _phase="OPEN_PLAY",
        _check_pre_tackle=lambda bc, p: verdict,
    )


def _install_isolating_stubs(emergency_active):
    """Replace every side-effect reach so only the conjunct logic runs."""
    ca.contact_radius_m = lambda: RADIUS_M
    ca._trace_enabled = lambda: False
    ca.last_decision = lambda world: None
    ca._publish = lambda world, decision, memo=True: decision
    ca._preserve = lambda world, bc, defenders, decision, contest=None: decision
    ca._publish_contest = lambda world, contest: None
    ca._form_tackle_state = lambda *a, **k: None
    ca._assemble_contest = lambda *a, **k: SimpleNamespace()

    # THE ADMISSION AUTHORITY UNDER TEST.  ``fullback_role.fullback_emergency_
    # defender_active`` is a pure read of live engine truth and needs a real
    # break; the branch's DECISION LOGIC is what is measured here, so the
    # authority is driven directly and the real predicate is asserted
    # separately by the engine probe.
    ca._fb_emergency_authority_active = (
        lambda world, defender, bc: bool(emergency_active(defender))
    )


def _evaluate(fullback, emergency_active, verdict="CONTACT"):
    _install_isolating_stubs(emergency_active)
    bc = _body(0, 0.0, 0.0)
    return ca.evaluate_contact(_world(), _ope(verdict), [fullback], bc)


def _run():
    failures = []
    total = 0

    # ── [1] THE FIX: the chasing fullback, tackle_role unset, FORMS ──────
    total += 1
    fb = _body(9, 0.4, 0.0, fullback=True, can_tackle=True, tackle_role=None)
    d = _evaluate(fb, lambda body: True)
    ok = (d.outcome is ca.ContactOutcome.CONTACT_FORMED and d.winner is fb)
    if not ok:
        failures.append(
            "[1] chasing fullback (tackle_role=None) failed to form a "
            f"contest: outcome={d.outcome} reason={d.reason} "
            f"winner={getattr(d.winner, 'player_id', None)}"
        )

    # ── [1b] same for the live LINE / PASSIVE demotions ──────────────────
    for role in ("LINE", "PASSIVE"):
        total += 1
        fb = _body(9, 0.4, 0.0, fullback=True, can_tackle=True, tackle_role=role)
        d = _evaluate(fb, lambda body: True)
        if not (d.outcome is ca.ContactOutcome.CONTACT_FORMED and d.winner is fb):
            failures.append(
                f"[1b] fullback with tackle_role={role!r} failed to form: "
                f"outcome={d.outcome} reason={d.reason}"
            )

    # ── [2] SCOPED: a non-fullback in the same state still cannot form ───
    total += 1
    plain = _body(12, 0.4, 0.0, fullback=False, can_tackle=True,
                  tackle_role=None)
    d = _evaluate(plain, lambda body: True)
    if d.outcome is not ca.ContactOutcome.CONTACT_FORMED:
        pass
    else:
        failures.append(
            "[2] a NON-fullback with tackle_role=None formed a contest -- "
            "the admission leaked beyond the emergency fullback"
        )

    # ── [3] SCOPED: a tagged fullback with NO genuine break still cannot ──
    total += 1
    fb = _body(9, 0.4, 0.0, fullback=True, can_tackle=True, tackle_role=None)
    d = _evaluate(fb, lambda body: False)
    if d.outcome is ca.ContactOutcome.CONTACT_FORMED:
        failures.append(
            "[3] a tagged fullback with no genuine break formed a contest -- "
            "the sweeper can be drafted in outside a break"
        )

    # ── [4] CONJUNCT 1 still short-circuits a fullback with no authority ──
    total += 1
    fb = _body(9, 0.4, 0.0, fullback=True, can_tackle=False, tackle_role=None)
    d = _evaluate(fb, lambda body: True)
    if d.outcome is ca.ContactOutcome.CONTACT_FORMED:
        failures.append(
            "[4] a tagged fullback WITHOUT can_acquire_tackle formed a "
            "contest -- CONJUNCT 1 no longer guards"
        )

    # ── [5] the ordinary PRIMARY lane tackler is untouched ───────────────
    total += 1
    lane = _body(7, 0.4, 0.0, fullback=False, can_tackle=True,
                 tackle_role="PRIMARY")
    d = _evaluate(lane, lambda body: True)
    if not (d.outcome is ca.ContactOutcome.CONTACT_FORMED and d.winner is lane):
        failures.append(
            "[5] an ordinary PRIMARY lane tackler stopped forming contests"
        )

    print(f"sections run : {total}")
    if failures:
        print(f"FAILURES     : {len(failures)}")
        for line in failures:
            print(f"  !! {line}")
        return 1
    print("FAILURES     : 0")
    print("VERDICT      : PASS")
    return 0


if __name__ == "__main__":
    sys.exit(_run())
