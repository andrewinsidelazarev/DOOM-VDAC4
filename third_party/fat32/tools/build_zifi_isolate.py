# -*- coding: utf-8 -*-
"""Сборки для изоляции записи, гасящей спрайт на строке переключения полос.

В каждой отключена ровно одна запись видеорегистра. Картинка получается кривая
намеренно: смотреть надо только на то, пропала ли тонкая полоса по курсору.
Рабочая сборка восстанавливается в конце.
"""
from pathlib import Path
import hashlib
import os
import shutil
import subprocess

SPG = Path(r'C:/Users/Администратор/Desktop/WC/ZiFi ESP32-S3 Zero/ZiFi SPG')
FAT32 = Path(__file__).resolve().parents[1]
CASES = [('SKIP_GYOFFS', 'no_gyoffs'), ('SKIP_PALSEL', 'no_palsel'),
         ('SKIP_VPAGE', 'no_vpage')]


def assemble(defines, sym):
    assembler = os.environ.get('SJASMPLUS', r'C:\z80\zuma\sjasmplus.exe')
    result = subprocess.run(
        [assembler, '--nologo'] + defines +
        ['--inc=' + str(SPG.parent / 'shared/z80'),
         '--inc=' + str(FAT32 / 'include'),
         '--sym=' + str(SPG / 'build' / sym), 'zifi.asm'],
        cwd=SPG, capture_output=True)
    if result.returncode:
        print((result.stdout + result.stderr).decode('utf-8', errors='replace'))
        raise SystemExit(result.returncode)


def pack(bin_name, out_name, desc):
    spg = SPG / '_spg'
    ini = (spg / 'spgbld.ini').read_text(encoding='utf-8')
    ini = ini.replace('../build/zifi.bin', '../build/' + bin_name)
    ini = ini.replace('Desc = Zifi', 'Desc = ' + desc)
    tmp = spg / 'spgbld_iso.ini'
    tmp.write_text(ini, encoding='utf-8')
    env = dict(os.environ)
    env['PATH'] = str(spg) + os.pathsep + env.get('PATH', '')
    out = SPG / 'build' / out_name
    result = subprocess.run([str(spg / 'spgbld.exe'), '-b', tmp.name, str(out)],
                            cwd=spg, env=env, capture_output=True)
    tmp.unlink()
    if result.returncode:
        print(result.stdout.decode('cp866', errors='replace'))
        raise SystemExit(result.returncode)
    data = out.read_bytes()
    print('%-24s %6d %s' % (out.name, len(data), hashlib.sha256(data).hexdigest()))


def main():
    build = SPG / 'build'
    for define, tag in CASES:
        assemble(['-D' + define + '=1'], 'zifi_%s.sym' % tag)
        shutil.move(build / 'zifi.bin', build / ('zifi_%s.bin' % tag))
        pack('zifi_%s.bin' % tag, 'zifi_%s.spg' % tag, 'Zifi ' + tag)
    assemble([], 'zifi.sym')
    assert (build / 'zifi.bin').is_file(), 'рабочий zifi.bin не восстановлен'
    print('рабочая сборка восстановлена')


if __name__ == '__main__':
    main()
