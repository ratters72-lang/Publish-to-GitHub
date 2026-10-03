import sys
sys.path.insert(0, ".")
p = "game/defence/defence_lvl_1/fullback_controller.py"
t = open(p, encoding="utf-8").read()
print("LEN", len(t))
for name in (
    "def fullback_locomotion_profile(",
    "def declared_fullback_locomotion(",
    "def declared_fullback_locomotion_source(",
    "def declared_fullback_locomotion_frame(",
    "def declare_fullback_locomotion(",
    "def locomotion_intent(",
    "def locomotion_contract_owned(",
    "def shared_locomotion_allowed(",
    "def break_locomotion_owned(",
    "def recovery_locomotion_owned(",
    "def recovery_owed(",
    "def _locomotion_anchor_pair(",
    "def _fullback_locomotion_api(",
):
    print("DEF", name, name in t)
for entry in (
    '"FB_LOCOMOTION_CONTROLLER"',
    '"FB_LOCOMOTION_CONTROLLER_ENV_VAR"',
    '"fullback_locomotion_profile"',
    '"locomotion_contract_owned"',
    '"shared_locomotion_allowed"',
    '"declare_fullback_locomotion"',
    '"declared_fullback_locomotion"',
    '"recovery_owed"',
    '"SOURCE_LOCOMOTION_BREAK"',
    '"LOCOMOTION_INTENT_ATTR"',
):
    print("ALL", entry, t.count(entry))
for m in ("def owns_locomotion(", "def locomotion_profile(", "def declare_locomotion(",
          "def declared_locomotion(", "def recovery_owed(", "def locomotion_intent("):
    print("FACADE", m, m in t)
print("TAIL:")
print(t[-1600:])
