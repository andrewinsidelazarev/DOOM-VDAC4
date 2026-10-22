# -*- coding: utf-8 -*-
"""Диагностическая сборка zifi_diag.spg: тот же код, но ZiFi не трогается вовсе.

Нужна, чтобы отделить тракт ESP от остальной инициализации машины при поиске
причины пропажи мыши на реальном ZX-Evolution. Рабочий zifi.spg не затрагивается.
"""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess

SPG = Path(r'C:/Users/Администратор/Desktop/WC/ZiFi ESP32-S3 Zero/ZiFi SPG')
FAT32 = Path(__file__).resolve().parents[1]


def main():
    assembler = os.environ.get('SJASMPLUS', r'C:\z80\zuma\sjasmplus.exe')
    build = SPG / 'build'
    # Ассемблируем во временное имя, чтобы не затереть рабочий zifi.bin.
    source = (SPG / 'zifi.asm').read_text(encoding='utf-8')
    assert 'IFNDEF ZIFI_NO_ESP' in source, 'в zifi.asm нет ключа диагностики'
    result = subprocess.run(
        [assembler, '--nologo', '-DZIFI_NO_ESP=1',
         '--inc=' + str(SPG.parent / 'shared/z80'),
         '--inc=' + str(FAT32 / 'include'),
         '--sym=' + str(build / 'zifi_diag.sym'), 'zifi.asm'],
        cwd=SPG, capture_output=True)
    print((result.stdout + result.stderr).decode('utf-8', errors='replace'))
    if result.returncode:
        raise SystemExit(result.returncode)
    # sjasmplus записал build/zifi.bin — переносим и возвращаем рабочий на место.
    shutil.move(build / 'zifi.bin', build / 'zifi_diag.bin')

    spg = SPG / '_spg'
    ini = (spg / 'spgbld.ini').read_text(encoding='utf-8')
    ini = ini.replace('../build/zifi.bin', '../build/zifi_diag.bin')
    ini = ini.replace('Desc = Zifi', 'Desc = Zifi diag')
    (spg / 'spgbld_diag.ini').write_text(ini, encoding='utf-8')
    env = dict(os.environ)
    env['PATH'] = str(spg) + os.pathsep + env.get('PATH', '')
    result = subprocess.run([str(spg / 'spgbld.exe'), '-b', 'spgbld_diag.ini',
                             str(build / 'zifi_diag.spg')], cwd=spg, env=env)
    (spg / 'spgbld_diag.ini').unlink()
    if result.returncode:
        raise SystemExit(result.returncode)
    # Диагностическая трансляция пишет в тот же build/zifi.bin, поэтому рабочую
    # сборку нужно вернуть на место — иначе следующий шаг соберёт SPG из диага.
    restore = subprocess.run(
        [assembler, '--nologo',
         '--inc=' + str(SPG.parent / 'shared/z80'),
         '--inc=' + str(FAT32 / 'include'),
         '--sym=' + str(build / 'zifi.sym'), 'zifi.asm'],
        cwd=SPG, capture_output=True)
    if restore.returncode:
        print((restore.stdout + restore.stderr).decode('utf-8', errors='replace'))
        raise SystemExit(restore.returncode)
    assert (build / 'zifi.bin').is_file(), 'рабочий zifi.bin не восстановлен'
    out = build / 'zifi_diag.spg'
    print(out, out.stat().st_size, hashlib.sha256(out.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
