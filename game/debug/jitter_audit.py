"""game/debug/jitter_audit.py — per-frame spacing jitter audit (diagnostic).

Captures, per frame, everything the spacing pipeline decided so consecutive
frames can be diffed and each spacing jump attributed to the exact state /
side-population change that caused it:

  per side (LEFT / RIGHT):
      n_side, member_ids (the chain side list), hinge_id, hinge_y, a_y, winger_y
  per defender (all non-fullback bodies passed to compute_targets):
      id, y, target_y, tackle_state_active, defender_role, depth_role,
      depth_side, fullback_role_active

All probes are NO-OPs unless the world carries a ``_jitter_audit`` list or
RLM_JITTER_AUDIT=1 is set, so production behaviour is byte-identical when
the audit is absent.

Wiring:
  - defensive_split_spacing.py  (compute_targets tail) -> record_compute_targets

``diff_records`` is the consecutive-frame differ: it walks the captured
records and, for every defender whose target_y moved more than *threshold*
between two adjacent frames, reports the delta plus the correlated flag flips
(tackle_state_active / defender_role / depth_role / depth_side) and the
side-population / hinge changes on that defender's side.
"""
import os


def audit_on(world) -> bool:
    """True when the audit collector exists (world._jitter_audit) or
    RLM_JITTER_AUDIT=1 (default off)."""
    if world is not None and hasattr(world, "_jitter_audit"):
        return True
    return os.environ.get("RLM_JITTER_AUDIT", "") == "1"


def _get_store(world):
    """Return the world's audit collector, lazily installing it on
    RLM_JITTER_AUDIT=1 so a driver can just set the env var."""
    if world is None:
        return None
    store = getattr(world, "_jitter_audit", None)
    if store is None and os.environ.get("RLM_JITTER_AUDIT", "") == "1":
        store = []
        setattr(world, "_jitter_audit", store)
    return store


def _role_name(o):
    role = getattr(o, "defender_role", None)
    return getattr(role, "name", None) or role


def record_compute_targets(
    world,
    frame,
    defenders,
    targets,
    left_defs,
    right_defs,
    hinge_left,
    hinge_right,
    a_y_left,
    a_y_right,
    winger_y_left,
    winger_y_right,
    fullback_actor=None,
) -> None:
    """Log one compute_targets frame snapshot (NO-OP unless audit_on)."""
    if not audit_on(world):
        return
    store = _get_store(world)
    if store is None:
        return

    def _side_record(side_defs, hinge, a_y, winger_y):
        return {
            "n_side": len(side_defs),
            "member_ids": [getattr(d, "player_id", None) for d in side_defs],
            "hinge_id": getattr(hinge, "player_id", None) if hinge is not None else None,
            "hinge_y": float(hinge.y) if hinge is not None else None,
            "a_y": float(a_y),
            "winger_y": float(winger_y),
        }

    record = {
        "frame": frame,
        "sides": {
            "LEFT": _side_record(left_defs, hinge_left, a_y_left, winger_y_left),
            "RIGHT": _side_record(right_defs, hinge_right, a_y_right, winger_y_right),
        },
        "defenders": {},
        "fullback_id": (
            getattr(fullback_actor, "player_id", None)
            if fullback_actor is not None else None
        ),
    }
    for d in defenders:
        if d is None:
            continue
        ty = targets.get(id(d))
        # Key by player_id (stable across frames) so consecutive-frame diffs
        # match the same defender regardless of object identity; fall back to
        # id() only for defenders without a player_id (synthetic stubs).
        key = getattr(d, "player_id", None)
        if key is None:
            key = id(d)
        record["defenders"][key] = {
            "id": getattr(d, "player_id", None),
            "y": float(getattr(d, "y", 0.0)),
            "target_y": float(ty) if ty is not None else None,
            "tackle_state_active": bool(getattr(d, "tackle_state_active", False)),
            "defender_role": _role_name(d),
            "depth_role": getattr(d, "depth_role", None),
            "depth_side": getattr(d, "depth_side", None),
            "fullback_role_active": bool(getattr(d, "fullback_role_active", False)),
        }
    store.append(record)


def flush_audit(world, path="jitter_audit.csv") -> str:
    """Write the captured records to a CSV (one row per defender per frame)."""
    store = getattr(world, "_jitter_audit", None) or []
    header = ("frame,id,y,target_y,tackle_state_active,defender_role,"
              "depth_role,depth_side,fullback_role_active,side,n_side,"
              "hinge_id,hinge_y,a_y,winger_y")
    lines = [header]
    for rec in store:
        # Determine each defender's side from depth_side, default '-' if absent.
        for did, d in rec["defenders"].items():
            side = ("LEFT" if d["depth_side"] == -1
                    else "RIGHT" if d["depth_side"] == 1 else "-")
            side_info = rec["sides"].get(side, {}) if side in ("LEFT", "RIGHT") else {}
            n_side = side_info.get("n_side", "")
            hinge_id = side_info.get("hinge_id", "")
            hinge_y = side_info.get("hinge_y", "")
            a_y = side_info.get("a_y", "")
            winger_y = side_info.get("winger_y", "")
            lines.append(
                f"{rec['frame']},{d['id']},{d['y']:.4f},"
                f"{'' if d['target_y'] is None else f'{d['target_y']:.4f}'},"
                f"{d['tackle_state_active']},{d['defender_role']},"
                f"{d['depth_role']},{d['depth_side']},"
                f"{d['fullback_role_active']},{side},{n_side},"
                f"{hinge_id},{'' if hinge_y is None else f'{hinge_y:.4f}'},"
                f"{'' if a_y == '' else f'{a_y:.4f}'},"
                f"{'' if winger_y == '' else f'{winger_y:.4f}'}"
            )
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def diff_records(records, threshold=0.1):
    """Diff consecutive records; return rows for every defender whose
    target_y moved > *threshold*, with the correlated state / side change.

    Each row: frame pair, defender id, Δtarget_y, flag flips, side change.
    """
    rows = []
    for i in range(1, len(records)):
        r0, r1 = records[i - 1], records[i]
        f0, f1 = r0["frame"], r1["frame"]

        # Per-side changes (n_side / hinge / side-list membership).
        side_changes = {}
        for side in ("LEFT", "RIGHT"):
            s0, s1 = r0["sides"][side], r1["sides"][side]
            ch = []
            if s0["n_side"] != s1["n_side"]:
                ch.append(f"n_side {s0['n_side']}->{s1['n_side']}")
            if s0["hinge_id"] != s1["hinge_id"]:
                ch.append(f"hinge {s0['hinge_id']}->{s1['hinge_id']}")
            if s0["hinge_y"] != s1["hinge_y"]:
                ch.append(f"hinge_y {s0['hinge_y']}->{s1['hinge_y']}")
            if s0["member_ids"] != s1["member_ids"]:
                removed = [m for m in s0["member_ids"] if m not in s1["member_ids"]]
                added = [m for m in s1["member_ids"] if m not in s0["member_ids"]]
                parts = []
                if removed:
                    parts.append("-" + ",".join(str(m) for m in removed))
                if added:
                    parts.append("+" + ",".join(str(m) for m in added))
                ch.append("members " + " ".join(parts))
            side_changes[side] = "; ".join(ch) if ch else ""

        # Per-defender target_y deltas.
        for did, d1 in r1["defenders"].items():
            d0 = r0["defenders"].get(did)
            if d0 is None:
                continue
            if d0["target_y"] is None or d1["target_y"] is None:
                continue
            dty = d1["target_y"] - d0["target_y"]
            if abs(dty) < threshold:
                continue
            flips = []
            for fname in ("tackle_state_active", "defender_role",
                          "depth_role", "depth_side"):
                if d0[fname] != d1[fname]:
                    flips.append(f"{fname} {d0[fname]}->{d1[fname]}")
            side = ("LEFT" if d1["depth_side"] == -1
                    else "RIGHT" if d1["depth_side"] == 1 else None)
            sc = side_changes.get(side, "") if side else ""
            rows.append({
                "frame": f"{f0}->{f1}",
                "id": d1["id"],
                "dty": dty,
                "flag_changes": "; ".join(flips) if flips else "-",
                "side_changes": sc if sc else "-",
            })
    return rows


def _self_test() -> None:
    """Smoke test: build two records with a genuine side-population change
    and verify diff_records attributes the target_y jump to it."""
    def rec(frame, n_side, members, hinge_id, hinge_y, def_rows):
        return {
            "frame": frame,
            "sides": {
                "LEFT": {"n_side": 0, "member_ids": [], "hinge_id": None,
                         "hinge_y": None, "a_y": 33.0, "winger_y": 53.0},
                "RIGHT": {"n_side": n_side, "member_ids": members,
                          "hinge_id": hinge_id, "hinge_y": hinge_y,
                          "a_y": 37.0, "winger_y": 65.0},
            },
            "defenders": {
                pid: {"id": pid, "y": y, "target_y": ty,
                      "tackle_state_active": tack,
                      "defender_role": role, "depth_role": dr,
                      "depth_side": 1, "fullback_role_active": False}
                for pid, y, ty, tack, role, dr in def_rows
            },
        }

    rows0 = [(21, 62.0, 65.0, False, "LINE", "WINGER"),
             (22, 55.5, 58.0, False, "LINE", "CENTRE"),
             (23, 49.0, 51.0, False, "LINE", "D"),
             (24, 42.5, 44.0, False, "LINE", "C"),
             (25, 37.0, 37.0, False, "LINE", "A")]
    rows1 = [(21, 62.0, 65.0, False, "LINE", "WINGER"),
             (22, 55.5, 55.4, False, "LINE", "CENTRE"),
             (23, 49.0, 45.8, False, "LINE", "D"),
             (24, 42.5, 36.2, False, "LINE", "C"),
             (25, 37.0, 37.0, True, "LINE", "A")]
    r0 = rec(0, 5, [21, 22, 23, 24, 25], 25, 37.0, rows0)
    # Frame 1: id25 left the chain (n_side 5->4), hinge moved to id26.
    r1 = rec(1, 4, [21, 22, 23, 24], 26, 36.2, rows1)
    diff = diff_records([r0, r1], threshold=0.1)
    assert diff, "expected diff rows"
    ids = {row["id"] for row in diff}
    assert {22, 23, 24}.issubset(ids), ids
    r22 = next(row for row in diff if row["id"] == 22)
    assert "n_side 5->4" in r22["side_changes"], r22
    assert "hinge 25->26" in r22["side_changes"], r22
    assert "-25" in r22["side_changes"], r22
    print("JITTER_AUDIT_SELF_TEST_OK")


if __name__ == "__main__":
    _self_test()
