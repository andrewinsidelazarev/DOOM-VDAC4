"""Package only the Z80 launcher and FAT32 driver into SPG; ELF/WAD stay files."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import make_launcher_pic
def main():
    build=ROOT/'build'; out=ROOT/'dist'/'DOOM';out.mkdir(parents=True,exist_ok=True)
    ini=build/'doom_spg.ini'
    blocks=[(2,build/'doom_z80.bin'),(14,ROOT/'third_party/fat32/build/fat32.bin'),(15,ROOT/'third_party/fat32/build/fat32-work.bin')]
    # картинка экрана загрузки — страницы с #40 (launcher/ftscreen.asm, tools/make_launcher_pic.py)
    blocks+=make_launcher_pic.blocks()
    ini.write_text('Desc = DOOM VDAC4\nStart = #8000\nStack = #bff0\nResident = #5b00\nPage3 = 0\nClock = 2\nINT = 0\nPager = 0\n'+
        ''.join(f'Block = #c000, {page}, {path.relative_to(ROOT).as_posix()}\n' for page,path in blocks),encoding='ascii')
    tool=os.environ.get('SPGBLD',r'E:\zx\z80\zuma\spgbld.exe')
    # Несжатые блоки не требуют старого упаковщика mhmt.
    # _DOOM.SPG — первым в списке файлов папки; прежнее имя DOOM.SPG в пакет не попадает
    (out/'DOOM.SPG').unlink(missing_ok=True)
    subprocess.run([tool,'-b',str(ini),str(out/'_DOOM.SPG'),'-c','0'],cwd=ROOT,check=True)
    (out/'README.TXT').write_text(
        'DOOM VDAC4 1.1\n'
        'https://github.com/andrewinsidelazarev/DOOM-VDAC4\n'
        '\n'
        'УСТАНОВКА\n'
        '_DOOM.SPG и DOOM.ELF положить в одну папку на SD-карте Evo (FAT32),\n'
        'туда же — свой WAD: DOOM1.WAD (shareware), DOOM.WAD или DOOM2.WAD.\n'
        'Запустить _DOOM.SPG. Папку игры загрузчик находит сам и запоминает\n'
        'в ESP. В корень карты ничего класть не нужно.\n'
        '_DOOM.SPG и DOOM.ELF обновлять вместе, из одного выпуска.\n'
        '\n'
        'ВЫБОР ИГРЫ\n'
        'Если в папке несколько WAD, на экране загрузки появится меню:\n'
        '1, 2, 3 — сразу эта игра; стрелки или W/S — выбор; Enter или\n'
        'пробел — запуск. Джойстик Kempston: вверх/вниз — выбор, огонь — запуск.\n'
        '\n'
        'УПРАВЛЕНИЕ (клавиатура PS/2)\n'
        'Стрелки, W/A/S/D — ход и поворот     Q/E, запятая/точка — шаг вбок\n'
        'Ctrl или F — огонь                   Пробел — открыть, нажать\n'
        'Shift — бег                          Alt — стрейф\n'
        'Esc — меню                           Backspace — назад, Enter — выбрать\n'
        'F2/F3 — сохранить/загрузить          F6/F9 — быстро\n'
        'F4 — громкость                       Tab или M — карта\n'
        'P — пауза                            1..7 — оружие\n'
        'Мышь Kempston: поворот и ход; левая — огонь, правая — стрейф,\n'
        'средняя — вперёд. Джойстик Kempston: ход, поворот, огонь.\n'
        'Выход — Quit Game в меню или Reset.\n'
        '\n'
        'СОХРАНЕНИЯ И НАСТРОЙКИ\n'
        'Всё лежит в папке игры: D1SAVEn.DSG (DOOM1.WAD), DUSAVEn.DSG\n'
        '(DOOM.WAD), D2SAVEn.DSG (DOOM2.WAD); настройки — DEFAULT.CFG\n'
        'и DOOM.CFG. Настройки записываются сами: из Options и Sound Volume -\n'
        'при выходе с экрана, горячие клавиши — через секунду. Перед Reset\n'
        'подождите пару секунд после изменения настроек.\n'
        'DOOM.CFG: vdac4_bilinear 1 — сглаживание, 0 — чёткие пиксели.\n'
        '\n'
        'НУЖНО\n'
        'ZX Evolution TS-Conf, SD-карта в SD-ZC, клавиатура PS/2.\n'
        'VDAC4: ESP32-S3 N8R8 с прошивкой 0.1.83 или новее, Tang Nano 20K\n'
        'с ядром FT812 в Slot1. WAD в пакет не входит.\n'
        '\n'
        'Движок — GPL-2.0-or-later (COPYING.TXT), прочие лицензии — LICENSES.TXT.\n',encoding='utf-8')
    manifest={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(out.glob('*')) if p.suffix in ('.SPG','.ELF')}
    (ROOT/'dist'/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'COPYING.TXT').write_bytes((ROOT/'LICENSE').read_bytes())
    (out/'LICENSES.TXT').write_text('FAT32 Driver ZX Evolution\n'+(ROOT/'third_party/fat32/LICENSE').read_text()+
        '\nVDAC4 ABI and SPI transport\n'+(ROOT/'licenses/VDAC4-MIT.txt').read_text()+
        '\nR-Type VDAC2+ FAT directory search\n'+(ROOT/'licenses/R-Type-MIT.txt').read_text(),encoding='utf-8')
    with zipfile.ZipFile(ROOT/'dist'/'DOOM-VDAC4-1.1.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.iterdir()):
            if p.is_file():z.write(p,'DOOM/'+p.name)
if __name__=='__main__':main()
