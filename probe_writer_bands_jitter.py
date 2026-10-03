"""probe_writer_bands_jitter.py — live on/off jitter measurement for the
writer-priority-band arbiter.

Boots the real engine headless (same recipe as ``headless_ruck_probe.py``),
drives it into OPEN_PLAY, and measures per-frame writer-conflict + target
jitter with the band arbiter ON and OFF.  Emits one ``RESULT_JSON`` line so
the two runs can be diffed mechanically.

Usage:
    python probe_writer_bands_jitter.py on  > tmp_wpb_on.txt  2>&1
    python probe_writer_bands_jitter.py off > tmp_wpb_off.txt 2>&1

Metrics (all counted only on frames where OPEN_PLAY defence is live):
    open_play_frames      frames the defence manager actually ran
    conflict_frames       player-frames where movement_writer is a >1 list
                          (freeze_audit's DUAL_WRITER condition)
    writer_switch_frames  player-frames where the writer NAME changed from the
                          previous frame (frame-order conflict churn)
    x_flips / y_flips     player-frames where the target delta reversed sign
                          (target seesaw)
    slow_x_flips / slow_y_flips
                          the same, restricted to near-stationary bodies
                          (< 0.10 m/s) — pure writer-conflict jitter, no
                          legitimate movement to explain it
    x_churn / y_churn     sum of |target delta| in metres (how much the
                          target thrashes)
    suppressed_total      writer proposals dropped by the band arbiter
    resolved_frames       frames the arbiter wrote a winning signal
"""

from __future__ import annotations

import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import json
import math
import random
import sys

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

import pygame

pygame.init()
pygame.display.set_mode((1, 1))  # .convert() needs a video mode

from ball.ball import Ball
from game.kickoff_engine import KickoffEngine
from game.physics.collision_system import CollisionSystem
from kick_off.kickoff_formations import (
    apply_kicking_team_formation,
    apply_receiving_kickoff_formation,
)
from world.catch_system import check_for_catch
from world.field import Field
from world.open_play_engine import OpenPlayEngine
from world.world_state import GamePhase, WorldState

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
FPS = 60
SLOW_SPEED_MPS = 0.10

# Fixed seed so the ON and OFF runs play the SAME match.  Without this the
# kickoff RNG diverges (different catcher / landing point) and the on/off
# delta measures two different scenarios instead of the band arbiter.
SEED = int(os.environ.get("RLM_WPB_SEED", "20260909"))


def _seed_rngs() -> None:
    """Pin every RNG the engine might use so both runs are identical."""
    random.seed(SEED)
    try:
        import numpy as np
    except Exception:  # numpy absent -> engine is pure-stdlib
        return
    np.random.seed(SEED)


class _PlayerJitter:
    """Per-player target-jitter accumulator."""

    def __init__(self) -> None:
        self.prev_tx: float | None = None
        self.prev_ty: float | None = None
        self.prev_dx: float = 0.0
        self.prev_dy: float = 0.0
        self.prev_writer: str | None = None
        self.has_prev: bool = False

    @staticmethod
    def _flipped(prev_delta: float, delta: float) -> bool:
        if abs(delta) < 1e-9 or abs(prev_delta) < 1e-9:
            return False
        return (prev_delta > 0.0) != (delta > 0.0)

    def push(self, p, writer, slow: bool, acc: dict) -> None:
        tx = float(getattr(p, "target_x", 0.0) or 0.0)
        ty = float(getattr(p, "target_y", 0.0) or 0.0)
        if self.has_prev and self.prev_tx is not None:
            dx = tx - self.prev_tx
            dy = ty - self.prev_ty
            acc["x_churn"] += abs(dx)
            acc["y_churn"] += abs(dy)
            if self._flipped(self.prev_dx, dx):
                acc["x_flips"] += 1
                if slow:
                    acc["slow_x_flips"] += 1
            if self._flipped(self.prev_dy, dy):
                acc["y_flips"] += 1
                if slow:
                    acc["slow_y_flips"] += 1
            if abs(dx) > 1e-9:
                self.prev_dx = dx
            if abs(dy) > 1e-9:
                self.prev_dy = dy
        self.prev_tx, self.prev_ty = tx, ty
        self.has_prev = True

        name = str(writer) if writer is not None else None
        if self.prev_writer is not None and name != self.prev_writer:
            acc["writer_switch_frames"] += 1
        self.prev_writer = name


def _blank_acc() -> dict:
    return {
        "open_play_frames": 0,
        "conflict_frames": 0,
        "writer_switch_frames": 0,
        "x_flips": 0,
        "y_flips": 0,
        "slow_x_flips": 0,
        "slow_y_flips": 0,
        "x_churn": 0.0,
        "y_churn": 0.0,
        "suppressed_total": 0,
        "resolved_frames": 0,
        "defender_frames": 0,
    }


def run(mode: str, max_frames: int) -> dict:
    _seed_rngs()
    field = Field(SCREEN_WIDTH, SCREEN_HEIGHT)
    world = WorldState()
    ball = Ball(field)
    world.ball = ball

    open_play = OpenPlayEngine(world)
    world.open_play = open_play
    world.open_play_engine = open_play

    kickoff_engine = KickoffEngine(world, ball, field)
    collision_system = CollisionSystem()

    # Documented override: world beats tuner (see writer_priority_bands
    # .writer_priority_bands_enabled).
    world.writer_priority_bands_enabled = (mode == "on")

    kickoff_engine.reset_for_kickoff()
    apply_receiving_kickoff_formation(world, field)
    apply_kicking_team_formation(world, field)

    world.ball_owner = "NONE"
    world.ball_carrier = None
    world.ball.possessed_by = None
    kickoff_engine.kickoff()

    dt = 1.0 / FPS
    acc = _blank_acc()
    traces: dict[int, _PlayerJitter] = {}
    conflict_players: set[int] = set()
    open_play_reached = False
    phase_hist: dict[str, int] = {}

    for _ in range(max_frames):
        ball.update(dt)

        if world.phase == GamePhase.KICKOFF:
            kickoff_engine.update(dt)
            check_for_catch(world, ball, dt)
        elif world.phase == GamePhase.CATCH:
            if world.ball_carrier is not None:
                ope = world.open_play
                if ope is not None:
                    ope.start(world.ball_carrier)
            world.phase = GamePhase.OPEN_PLAY
        elif world.phase == GamePhase.OPEN_PLAY:
            open_play.update(dt)
            if not world.tes_active:
                collision_system.resolve_all(world.players, world=world)

            label = str(getattr(world, "_ope_phase_label", "") or "open_play")
            phase_hist[label] = phase_hist.get(label, 0) + 1
            open_play_reached = True
            acc["open_play_frames"] += 1

            defenders = list(getattr(world, "defending_team", []) or [])
            for p in defenders:
                acc["defender_frames"] += 1
                writer = getattr(p, "movement_writer", None)
                if isinstance(writer, list) and len(writer) > 1:
                    acc["conflict_frames"] += 1
                    conflict_players.add(getattr(p, "player_id", -1))
                speed = math.hypot(
                    float(getattr(p, "vx", 0.0) or 0.0),
                    float(getattr(p, "vy", 0.0) or 0.0),
                )
                trace = traces.setdefault(
                    getattr(p, "player_id", -1), _PlayerJitter()
                )
                trace.push(p, writer, speed < SLOW_SPEED_MPS, acc)

            band_state = getattr(world, "_writer_priority_bands", None) or {}
            acc["suppressed_total"] += len(band_state.get("suppressed") or [])
            if band_state.get("winners"):
                acc["resolved_frames"] += 1

    acc["x_churn"] = round(acc["x_churn"], 3)
    acc["y_churn"] = round(acc["y_churn"], 3)
    acc["mode"] = mode
    acc["open_play_reached"] = open_play_reached
    acc["conflict_players"] = sorted(conflict_players)
    acc["phase_hist"] = phase_hist
    pygame.quit()
    return acc


def main() -> int:
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "on"
    max_frames = int(sys.argv[2]) if len(sys.argv) > 2 else 1800
    result = run(mode, max_frames)
    print("RESULT_JSON " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
