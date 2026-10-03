"""Generic finite state machine with transition guards and callbacks."""

from typing import Any, Callable


class Transition:
    """Represents a permissible transition from one state to another."""

    def __init__(
        self,
        source: str,
        target: str,
        event: str,
        guard: Callable[[Any], bool] | None = None,
        on_transition: Callable[[Any, Any], None] | None = None,
    ):
        self.source = source
        self.target = target
        self.event = event
        self.guard = guard
        self.on_transition = on_transition


class StateMachine:
    """Deterministic finite state machine."""

    def __init__(self, initial: str, states: set[str]):
        self._state = initial
        self._states = states
        self._transitions: dict[tuple[str, str], Transition] = {}

    def add_transition(self, t: Transition) -> None:
        """Register a transition keyed by (source, event)."""
        assert t.source in self._states, f"Unknown source state: {t.source}"
        assert t.target in self._states, f"Unknown target state: {t.target}"
        self._transitions[(t.source, t.event)] = t

    def dispatch(self, event: str, ctx: Any = None) -> bool:
        """Attempt a transition. Returns True if the transition fired."""
        key = (self._state, event)
        t = self._transitions.get(key)
        if t is None:
            return False

        if t.guard is not None and not t.guard(ctx):
            return False

        previous = self._state
        self._state = t.target
        if t.on_transition:
            t.on_transition(ctx, previous)
        return True

    def force_state(self, state: str) -> None:
        """Forcefully set state without firing transition callbacks.
        
        Use ONLY for hard resets (e.g. absorbing TES completion when
        TES has already progressed past TACKLE_COMPLETE internally).
        Normal transitions should always go through dispatch().
        """
        assert state in self._states, f"Unknown state: {state}"
        self._state = state

    @property
    def state(self) -> str:
        return self._state
