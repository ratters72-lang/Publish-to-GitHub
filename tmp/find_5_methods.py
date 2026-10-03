with open('world/open_play_engine.py','r',encoding='utf-8') as f:
    lines = f.readlines()
targets = ['_prepare_release','_update_release','_update_ptb_prep','_update_ptb_exec','_update_ptb']
for i,line in enumerate(lines,1):
    for t in targets:
        if f'def {t}(' in line:
            content = ''.join(lines[i:i+12])
            print(f'{t}: line {i}')
            print(f'---opening---\n{content}')
            break
