from ai_agents.defence_reactivity_agent import DefenceReactivityAgent
import inspect

src = inspect.getsource(DefenceReactivityAgent.update)
assert "ope._update_defence" not in src, "STILL HAS _update_defence call!"
print("VERIFIED: no _update_defence call in agent")
