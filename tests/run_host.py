"""Real engine integration: save, fresh-process load, failed write, save menu, settings files."""
from pathlib import Path
import hashlib,os,re,shutil,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
# Настройки как у пользователя: CRLF, незнакомая и пустая строки, строка в кавычках.
DEFAULT_CFG=b'mouse_sensitivity             3\r\nsfx_volume 3\r\nmusic_volume 11\r\nchatmacro0                    "Hi there"\r\nunknown_variable 5\r\nlonely_key\r\n\r\n'
DOOM_CFG=b'vdac4_mouse_scale 7\nvdac4_bilinear 0\nmouse_acceleration 1.500000\n'
def config_read(log):
    m=re.search(r'^CONFIG READ (.*?) chatmacro0=(.*?)\r?$',log,re.M);assert m,'settings were not reported'
    return dict(x.split('=') for x in m[1].split()),m[2]

def wire_read(log):
    assert 'EVENT REJECT' not in log,log[-2500:]
    m=re.search(r'WIRE max_event=(\d+) pulls=(\d+) checks=(\d+) wake_after_reply=(\d+) legacy=(\d+)',log)
    assert m and int(m[1])<=63 and int(m[3])>0 and int(m[4])>0,log[-2500:]
    return tuple(map(int,m.groups()))
def run_hotkeys(out,wad):
    card=out/'card-hotkeys';card.mkdir(exist_ok=True)
    names=('screenblocks','detaillevel','show_messages','usegamma')
    initial=(10,0,1,0)
    cases=[('minus',initial,(7,0,1,0),2,2),('read',None,(7,0,1,0),0,0),
           ('plus',None,initial,2,2),('read',None,initial,0,0),
           ('gamma',None,(10,0,1,1),2,2),('read',None,(10,0,1,1),0,0),
           ('detail',None,(10,1,1,1),2,2),('read',None,(10,1,1,1),0,0),
           ('messages',None,(10,1,0,1),2,2),('read',None,(10,1,0,1),0,0),
           ('noop',(11,0,1,0),(11,0,1,0),0,0),('revert',initial,initial,0,0),
           ('fail-write',initial,(8,0,1,0),4,0),('fail-rename',initial,(8,0,1,0),4,0),
           ('recover',initial,(8,0,1,0),4,2),('read',None,(8,0,1,0),0,0)]
    for index,(mode,reset,expected,attempts,writes) in enumerate(cases,1):
        if reset:
            (card/'DEFAULT.CFG').write_bytes(DEFAULT_CFG+''.join(f'{k} {v}\n' for k,v in zip(names,reset)).encode())
            (card/'DOOM.CFG').write_bytes(DOOM_CFG)
        before=[(card/n).read_bytes() for n in ('DEFAULT.CFG','DOOM.CFG')]
        values=tuple(int(re.search(rf'^{key} +(\d+)\r?$',before[0].decode(),re.M)[1]) for key in names)
        result=subprocess.run([str(out/'host.exe'),str(wad),'hotkeys-'+mode],cwd=ROOT,capture_output=True,timeout=70)
        log=result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
        (out/f'hotkeys-{index:02}-{mode}.log').write_text(log)
        assert result.returncode==0,log[-2500:]
        wire_read(log)
        assert 'HOTKEYS READ '+' '.join(f'{k}={v}' for k,v in zip(names,values)) in log,log[-2500:]
        assert 'HOTKEYS END state=2 '+' '.join(f'{k}={v}' for k,v in zip(names,expected))+f' attempts={attempts} writes={writes} menu=0' in log,log[-2500:]
        times=[int(t) for t in re.findall(r'CONFIG ATTEMPT \d+ ms=(\d+)',log)]
        assert len(times)==attempts,(mode,times)
        if attempts:
            # Первое изменение в 500 мс, последнее в 1300 мс. Проверяем и отсутствие
            # ранней записи, и завершение без Quit. Два TEMP.CFG — одна запись двух коллекций.
            earliest=1450 if mode in ('gamma','detail','messages') else 2250
            assert all(earliest<=t<4000 for t in times[:2]),(mode,times)
            if attempts==4:assert all(5450<=t<7500 for t in times[2:]),(mode,times)
        if writes:
            cfg=(card/'DEFAULT.CFG').read_text()
            for key,value in zip(names,expected):assert re.search(rf'^{key} +{value}$',cfg,re.M),(mode,cfg)
            assert not (card/'TEMP.CFG').exists()
        else:
            assert [(card/n).read_bytes() for n in ('DEFAULT.CFG','DOOM.CFG')]==before,(mode,'changed files')
        print(f'hotkeys-{index:02}-{mode}: '+log.strip().splitlines()[-1],flush=True)
    return len(cases)

def run_config_types(out,wad):
    card=out/'card-types';card.mkdir(exist_ok=True)
    before_values='novert=1 mouse_acceleration=1.500 key_fire=163 chatmacro0=Hi there'
    cases=[('int','novert=0 mouse_acceleration=1.500 key_fire=163 chatmacro0=Hi there','DOOM.CFG','novert','0'),
           ('float','novert=1 mouse_acceleration=2.500 key_fire=163 chatmacro0=Hi there','DOOM.CFG','mouse_acceleration','2.500000'),
           ('string','novert=1 mouse_acceleration=1.500 key_fire=163 chatmacro0=Bi there','DEFAULT.CFG','chatmacro0','"Bi there"'),
           ('key','novert=1 mouse_acceleration=1.500 key_fire=102 chatmacro0=Hi there','DEFAULT.CFG','key_fire','33')]
    for mode,expected,filename,key,value in cases:
        (card/'DEFAULT.CFG').write_bytes(DEFAULT_CFG)
        (card/'DOOM.CFG').write_bytes(DOOM_CFG+b'novert 1\n')
        for reload in (False,True):
            arg='defaults-read' if reload else 'defaults-'+mode
            before=[(card/n).read_bytes() for n in ('DEFAULT.CFG','DOOM.CFG')]
            result=subprocess.run([str(out/'host.exe'),str(wad),arg],cwd=ROOT,capture_output=True,timeout=70)
            log=result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
            (out/f'defaults-{mode}{"-read" if reload else ""}.log').write_text(log)
            assert result.returncode==0,log[-2500:]
            wire_read(log)
            assert 'DEFAULTS READ '+(expected if reload else before_values) in log,log[-2500:]
            assert 'DEFAULTS END '+expected in log,log[-2500:]
            assert f'attempts={0 if reload else 2} writes={0 if reload else 2} menu=0' in log,log[-2500:]
            if reload:
                assert [(card/n).read_bytes() for n in ('DEFAULT.CFG','DOOM.CFG')]==before
            else:
                times=[int(t) for t in re.findall(r'CONFIG ATTEMPT \d+ ms=(\d+)',log)]
                assert len(times)==2 and all(1450<=t<3000 for t in times),(mode,times)
            assert re.search(rf'^{key} +{re.escape(value)}$',(card/filename).read_text(),re.M)
            assert not (card/'TEMP.CFG').exists()
            print(f'defaults-{mode}{"-read" if reload else ""}: '+log.strip().splitlines()[-1],flush=True)
    return len(cases)*2

def main():
    wad=Path(sys.argv[1] if len(sys.argv)>1 else ROOT/'tests/data/DOOM1.WAD')
    out=ROOT/'build/host';slot=out/'card/D1SAVE0.DSG';card=out/'card-config'
    # Existing test card files are preserved: the game explicitly replaces slot 0.
    # Настройки — на своей карте card-config, каждый прогон run_host начинает её заново.
    logs={}
    for mode in ('startfail-game','startfail-audio','ft-unavailable','video-transfer','old-bl616','save','load','fail','menu','mouse',
                 'config','config-again','config-fail','config-options','demo'):
        before=hashlib.sha256(slot.read_bytes()).hexdigest() if mode=='fail' else None
        if mode=='config':
            shutil.rmtree(card,ignore_errors=True);card.mkdir()
            (card/'DEFAULT.CFG').write_bytes(DEFAULT_CFG);(card/'DOOM.CFG').write_bytes(DOOM_CFG)
        if mode=='config-fail':settings=[(card/n).read_bytes() for n in ('DEFAULT.CFG','DOOM.CFG')]
        env=os.environ.copy()
        if mode=='old-bl616':env['VD_TEST_OLD_BL616']='1'
        arg={'old-bl616':'save','config-again':'config'}.get(mode,mode)
        result=subprocess.run([str(out/'host.exe'),str(wad),arg],cwd=ROOT,env=env,capture_output=True,timeout=70)
        log=result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
        (out/f'{mode}.log').write_text(log);logs[mode]=log
        if result.returncode:raise AssertionError(f'{mode}: {log[-2500:]}')
        if mode not in ('startfail-game','startfail-audio','ft-unavailable','video-transfer'):wire_read(log)
        if mode in ('save','config','config-again','config-options'):assert wire_read(log)[1]>=2
        if mode=='fail':assert hashlib.sha256(slot.read_bytes()).hexdigest()==before,'write failure changed previous save'
        if mode=='menu':assert slot.read_bytes().split(b'\0')[0].endswith(b'D'),'keyboard save-name text did not arrive'
        if mode=='old-bl616':assert 'software renderer on ESP' in log
        if mode=='mouse':assert 'MOUSE forward=' in log
        # 12 с демо 1 (E1M5): посреди уровня ни одного чтения WAD — уровень с предзагрузкой, звуки
        # загружены на заставке (до 0.1.7 — 450 чтений, 360 КБ: рывки демо)
        if mode=='demo':assert 'DEMO level=1:5' in log and 'level_reads=0 level_bytes=0' in log,log[-1500:]
        if mode.startswith('config'):
            # Прочитано при старте: ползунок меню 3, 4, 5, 5 — значение, записанное прошлым выходом
            # (config-fail ничего не записал).
            fields,macro=config_read(log)
            sensitivity={'config':3,'config-again':4,'config-fail':5,'config-options':5}[mode]
            assert fields['mouse_sensitivity']==str(sensitivity),f'{mode}: {fields}'
            assert (fields['sfx'],fields['music'],fields['mixer_music'])==('3','11','88'),fields
            assert (fields['vdac4_mouse_scale'],fields['vdac4_bilinear'],fields['mouse_acceleration'])==('7','0','1.500'),f'{mode}: {fields}'
            # KEY_FIRE, KEY_USE, KEY_STRAFE_L/R doomgeneric без скан-кода: 163, 162, 160, 161 переживают запись
            assert (fields['key_fire'],fields['key_use'],fields['key_strafeleft'],fields['key_straferight'])==('163','162','160','161'),f'{mode}: {fields}'
            assert macro=='Hi there',macro
            if mode=='config-options':
                assert 'CONFIG OPTIONS state=2 mouse_sensitivity=%d menu=0'%(sensitivity+1) in log,'options exit did not finish'
            else:
                assert 'CONFIG QUIT state=3 mouse_sensitivity=%d'%(sensitivity+1) in log,'menu quit did not finish'
        if mode in ('config','config-again','config-options'):
            # Одна запись при уходе с Options; Quit без новых изменений ничего не пишет.
            writes=1
            assert log.count('CONFIG WRITTEN TEMP.CFG -> DEFAULT.CFG')==writes and log.count('CONFIG WRITTEN TEMP.CFG -> DOOM.CFG')==writes,log[-2500:]
            assert not (card/'TEMP.CFG').exists()
            main_cfg=(card/'DEFAULT.CFG').read_text();extra_cfg=(card/'DOOM.CFG').read_text()
            assert re.search(r'^mouse_sensitivity +%d$'%(sensitivity+1),main_cfg,re.M),main_cfg
            assert re.search(r'^sfx_volume +3$',main_cfg,re.M) and re.search(r'^music_volume +11$',main_cfg,re.M),main_cfg
            assert re.search(r'^key_fire +163$',main_cfg,re.M) and '"Hi there"' in main_cfg and 'unknown_variable' not in main_cfg
            assert re.search(r'^vdac4_mouse_scale +7$',extra_cfg,re.M) and re.search(r'^mouse_acceleration +1\.500000$',extra_cfg,re.M),extra_cfg
        if mode=='config-fail':
            assert [(card/n).read_bytes() for n in ('DEFAULT.CFG','DOOM.CFG')]==settings,'failed SD write changed settings'
            assert 'TEMP.CFG not written' in log and 'CONFIG WRITTEN' not in log
            assert log.count('CONFIG ATTEMPT ')==2,'failed settings retried without changes'
        print(mode+': '+log.strip().splitlines()[-1])
    assert 'LOADED ammo=' in logs['load']
    sound_card=out/'card-sound';sound_card.mkdir(exist_ok=True)
    # Реальные меню и микшер; sound-esc читает файлы, записанные sound-back в другом процессе.
    for mode,sfx,music,delta in [('sound-back',3,11,1),('sound-esc',4,12,1),
                               ('sound-f4',0,0,1),('sound-max',15,15,-1),
                               ('sound-back-close',7,6,1),('sound-burst',2,9,1),
                               ('sound-unchanged',6,10,0)]:
        if mode!='sound-esc':
            (sound_card/'DEFAULT.CFG').write_text(f'mouse_sensitivity 3\nsfx_volume {sfx}\nmusic_volume {music}\n')
            (sound_card/'DOOM.CFG').write_bytes(DOOM_CFG)
        before=[(sound_card/n).read_bytes() for n in ('DEFAULT.CFG','DOOM.CFG')]
        result=subprocess.run([str(out/'host.exe'),str(wad),mode],cwd=ROOT,capture_output=True,timeout=70)
        log=result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
        (out/f'{mode}.log').write_text(log)
        assert result.returncode==0,f'{mode}: {log[-2500:]}'
        wire_read(log)
        assert f'SOUND READ sfx={sfx} music={music} mixer_music={music*8}' in log,log[-2500:]
        attempts=[int(t) for t in re.findall(r'CONFIG ATTEMPT \d+ ms=(\d+)',log)]
        assert len(attempts)==(2 if delta else 0),(mode,attempts)
        # Уход с экрана сохраняет сразу: в 3700 мс (последняя правка в 3300 мс),
        # а пакет sound-burst — в 2100 мс. Отложенная на секунду запись этот тест не пройдёт.
        exit_ms=2100 if mode=='sound-burst' else 3700
        assert all(exit_ms-50<=t<exit_ms+350 for t in attempts),(mode,attempts)
        cfg=(sound_card/'DEFAULT.CFG').read_text()
        for key,value in [('sfx_volume',sfx+delta),('music_volume',music+delta)]:
            assert re.search(rf'^{key} +{value}$',cfg,re.M),(mode,cfg)
        assert not (sound_card/'TEMP.CFG').exists()
        if not delta:assert [(sound_card/n).read_bytes() for n in ('DEFAULT.CFG','DOOM.CFG')]==before
        print(mode+': '+log.strip().splitlines()[-1])
    hotkeys_count=run_hotkeys(out,wad)
    types_count=run_config_types(out,wad)
    # Новый ELF со старым SPG: старый ServeFile отвергает write без данных статусом 10.
    # Проверяем настоящий ELF и неизменность предыдущих целевых файлов.
    for mode,files in [('wire-old-spg-save',[slot]),
                       ('wire-old-spg-config',[card/'DEFAULT.CFG',card/'DOOM.CFG'])]:
        before=[p.read_bytes() for p in files]
        result=subprocess.run([str(out/'host.exe'),str(wad),mode],cwd=ROOT,capture_output=True,timeout=70)
        log=result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
        (out/f'{mode}.log').write_text(log)
        assert result.returncode==0,log[-2500:]
        wire=wire_read(log);assert wire[1]==0 and wire[4]==1,wire
        assert 'not written (status 16)' in log and 'CONFIG WRITTEN' not in log,log[-2500:]
        assert [p.read_bytes() for p in files]==before,'old SPG changed previous files'
        print(mode+': '+log.strip().splitlines()[-1],flush=True)
    # Export only images produced by the actual game, not synthetic artwork.
    from PIL import Image
    for name in ('title','game','sound-menu'):Image.open(out/f'{name}.ppm').save(out/f'{name}.png')
    print('PASS: startup failure cleanup, 24 KiB stack budget, old BL616 fallback, video transfer, game loop, sound, persistent save/load, failed SD write, keyboard save menu, Kempston mouse, DEFAULT.CFG/DOOM.CFG read, write on Options exit and menu quit, failed settings write, demo without mid-level WAD reads')
    print(f'PASS: {24+hotkeys_count+types_count} processes; Music/SFX startup mixer levels, PS/2 Sound Volume, Backspace/Esc/F4/batched keys, restart, 0/15, unchanged menu without writes; PS/2 hotkeys, debounce, reset/reload, no-op/revert, SD write/rename failure and recovery; bound int/float/string/key changes in both collections; events <=63, write data via 76/F6, wrong/repeated seq, premature 74, reply failures/order, old SPG refusal')
if __name__=='__main__':main()
