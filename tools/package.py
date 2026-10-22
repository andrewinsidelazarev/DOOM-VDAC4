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
        'DOOM VDAC4 1.0\n\n'
        '_DOOM.SPG, DOOM.ELF и IWAD — DOOM.WAD (или DOOM2.WAD) — положить\n'
        'в одну папку на FAT32 SD Evo. В корень карты ничего класть не нужно.\n'
        'Shareware DOOM1.WAD переименуйте в DOOM.WAD (старое имя тоже работает).\n'
        'Запустить _DOOM.SPG. Путь хранится в path.cfg ESP; без записи — поиск.\n'
        'Wild Commander не требуется. На карте — одна полная установка.\n\n'
        'Обновите вместе DOOM.ELF и _DOOM.SPG из этой сборки 1.0. Для записи\n'
        'нужны оба файла: SPG забирает данные командой 76 порциями.\n'
        'Со старым SPG запись получает отказ 0x10; старый ELF с новым SPG\n'
        'тоже не пишет. Прежние CFG/DSG сохраняются, TEMP может остаться.\n'
        'До 0.1.8 запись на плате останавливала DOOM (File request queue\n'
        'full: событие больше 63 байт). В 0.1.8 данные шли одним кадром\n'
        'F6 на 965 байт; на плате он терялся, и DOOM падал с Evo file\n'
        'operation timeout. В 0.1.9 — порции по 248 байт (кадр к Z80 не\n'
        'больше 256 байт, как у ZiFi SPG); новая прошивка ESP не нужна.\n\n'
        'Настройки и сохранения — рядом с ELF и WAD. DEFAULT.CFG содержит\n'
        'mouse_sensitivity, sfx_volume, music_volume (0..15), screenblocks,\n'
        'detaillevel, show_messages, usegamma (0..4) и клавиши.\n'
        'DOOM.CFG содержит vdac4_*, novert, mouse_acceleration.\n'
        'Music и SFX: Options > Sound Volume, либо F4. Уход с Options или\n'
        'Sound Volume (Esc, Backspace) сразу сохраняет изменения. Горячие\n'
        'клавиши -/=, F5, F8, F11 сохраняются после секунды без новых изменений.\n'
        'Quit Game сохраняет оставшиеся изменения; без изменений записи нет.\n'
        'После отказа SD новая попытка будет только при следующем изменении.\n'
        'Сначала TEMP.CFG, затем замена целевого файла. Перед Reset дождитесь\n'
        'паузы и окончания записи; после правки в меню сначала выйдите\n'
        'с экрана настроек и дождитесь возвращения меню/игры.\n\n'
        'Клавиатура PS/2: Esc — меню/закрыть, Backspace — назад, Enter — выбрать.\n'
        'Стрелки или W/A/S/D — движение, Ctrl/F — огонь, Space — использовать,\n'
        'Shift — бег, Alt — стрейф, Q/E или запятая/точка — шаг вбок.\n'
        'F2/F3 — сохранить/загрузить, F6/F9 — быстро, F4 — громкость,\n'
        'Tab/M — карта, P — пауза, 1..7 — оружие. Матрица ZX не используется.\n'
        'Мышь Kempston: поворот/ход, левая — огонь, правая — стрейф,\n'
        'средняя — вперёд. Скорость: Options > Mouse sensitivity.\n\n'
        'Нужны ESP32-S3 N8R8, VDAC4 ABI 1.3 и Tang FT812 Slot1.\n'
        'Игра — приложение ELF, прошивки не заменяет. Музыка — внутренний\n'
        'FM-синтезатор, тембры отличаются от OPL. BL616 v7: рендер на ESP.\n'
        'Проверено на ПК, моделях Z80 и на плате: настройки сохраняются быстро.\n'
        'Исходники и результаты: README.md и VALIDATION.md проекта.\n'
        'Движок GPL-2.0-or-later. Данные IWAD в пакет не входят.\n',encoding='utf-8')
    manifest={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(out.glob('*')) if p.suffix in ('.SPG','.ELF')}
    (ROOT/'dist'/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'COPYING.TXT').write_bytes((ROOT/'LICENSE').read_bytes())
    (out/'LICENSES.TXT').write_text('FAT32 Driver ZX Evolution\n'+(ROOT/'third_party/fat32/LICENSE').read_text()+
        '\nVDAC4 ABI and SPI transport\n'+(ROOT/'licenses/VDAC4-MIT.txt').read_text()+
        '\nR-Type VDAC2+ FAT directory search\n'+(ROOT/'licenses/R-Type-MIT.txt').read_text(),encoding='utf-8')
    with zipfile.ZipFile(ROOT/'dist'/'DOOM-VDAC4-1.0.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.iterdir()):
            if p.is_file():z.write(p,'DOOM/'+p.name)
if __name__=='__main__':main()
