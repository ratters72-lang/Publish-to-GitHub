import py_compile, sys
files = [
    'game/defence/defence_lvl_1/defensive_role_spacing.py',
    'game/defence/defence_lvl_1/defensive_behaviour_lvl1.py',
    'game/defence/defence_lvl_2/defensive_behaviour_lvl2.py',
    'game/defence/defence_lvl_1/defensive_geometry.py',
    'game/defence/defence_lvl_1/defensive_timing.py',
]
all_ok = True
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f'OK: {f}')
    except py_compile.PyCompileError as e:
        print(f'ERROR: {f}: {e}')
        all_ok = False
if all_ok:
    print('ALL CHECKS PASSED')
    sys.exit(0)
else:
    print('SOME CHECKS FAILED')
    sys.exit(1)
