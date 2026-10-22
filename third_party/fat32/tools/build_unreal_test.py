"""Сборка отдельного Unreal с исправлением состояния SD после CMD24.

Исходный эмулятор не меняется. Удалены его события сборки, копирующие и
удаляющие файлы поставки: всё необходимое готовит prepare_unreal.py.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True,
                        help='Папка Unreal с Unreal2017.vcxproj и sdcard.cpp')
    parser.add_argument('--resume', action='store_true', help='Повторить сборку существующей тестовой копии')
    parser.add_argument('--uart-trace', action='store_true', help='Добавить журнал CPU/IRQ для диагностики полного ZiFi')
    parser.add_argument('--host-poke-sync', action='store_true',
                        help='Сбрасывать теги кэша TS-Conf в начале кадра, чтобы Z80 видел записи стенда')
    parser.add_argument('--zf-trace', action='store_true',
                        help='Счётчики моста ZiFi на реальный COM-порт')
    parser.add_argument('--screenshot', action='store_true',
                        help='Сохранять кадр в BMP по FAT32_SCRSHOT=<номер кадра>')
    parser.add_argument('--port-trace', action='store_true',
                        help='Журнал записей в видеорегистры TS-Conf с cpu.t')
    parser.add_argument('--hires-border', action='store_true',
                        help='Бордюр текстового режима как в FPGA: {GPAL, BORDER[3:0]}')
    args = parser.parse_args()
    target = ROOT / 'build/unreal-source'
    if target.exists() and not args.resume:
        raise SystemExit('Каталог сборки уже существует: исходники не перезаписаны.')
    if not args.resume:
        shutil.copytree(args.source, target,
                        ignore=shutil.ignore_patterns('tmp', 'bin', '.git', '*.user'))
    sd = target / 'sdcard.cpp'
    original = (args.source/'sdcard.cpp').read_bytes()
    before = b'if ((Val & 0xC0) != 0x40) // start=0, transm=1\r\n               break;'
    if original.count(before) != 1:
        raise SystemExit('Версия sdcard.cpp отличается: исправление не применено.')
    patched = original.replace(before, before.replace(b'break;', b'return;'))
    sd.write_bytes(patched)
    trace='''   static unsigned uart_trace_frames = 0;
   if (getenv("FAT32_UART_TRACE") && !(uart_trace_frames++ % 50))
   {
      printf("CPU pc=%04X sp=%04X iff=%u halt=%u frame=%u\\n", cpu.pc, cpu.sp, cpu.iff1, cpu.halted, comp.frame_counter);
      printf("TS mask=%u vs=%u hs=%u pos=%u last=%u pend=%u len=%u\\n", comp.ts.intmask, comp.ts.vsint, comp.ts.hsint, comp.ts.intctrl.frame_t, comp.ts.intctrl.last_cput, comp.ts.intctrl.pend, comp.ts.intctrl.frame_len);
      printf("IRQ im=%u i=%02X gate=%u vdos=%u/%u eipos=%u\\n", cpu.im, cpu.i, cpu.int_gate, comp.ts.vdos, comp.ts.vdos_m1, cpu.eipos);
      fflush(stdout);
   }
'''
    # Модель кэша TS-Conf в Unreal отдаёт чтения Z80 из cpu.tscache_data и
    # обновляет теги только при записи самим Z80. Запись в RAM эмулятора из
    # стенда остаётся невидимой для Z80, пока строка не вытеснена: так один и
    # тот же байт load_sw читался нулём, а стенд видел свою единицу. Сброс
    # тегов в начале кадра делает запись стенда видимой и лишь добавляет
    # промахи кэша; исполнение Z80 при этом не меняется.
    cache_sync='''   if (getenv("FAT32_CACHE_SYNC"))
      memset(cpu.tscache_addr, 0xFF, sizeof(cpu.tscache_addr));
'''
    # Кадр сохраняется один раз, на заданном кадре: BMP пишет штатная функция
    # Unreal, формат и каталог берутся из INI (ScrShot=BMP, ScrShotDir).
    screenshot = """   {
      extern void main_scrshot();
      const char *want = getenv("FAT32_SCRSHOT");
      if (want && comp.frame_counter == (unsigned)atoi(want))
         main_scrshot();
   }
"""
    snippets=([trace] if args.uart_trace else [])+([cache_sync] if args.host_poke_sync else [])
    snippets+=([screenshot] if args.screenshot else [])
    if snippets:
        mainloop=(args.source/'mainloop.cpp').read_text(encoding='utf-8')
        anchor='void spectrum_frame()'+chr(10)+'{'+chr(10)
        assert mainloop.count(anchor)==1
        (target/'mainloop.cpp').write_text(mainloop.replace(anchor,anchor+''.join(snippets)),encoding='utf-8')
    if args.zf_trace:
        # zf232.cpp хранит русские комментарии в кодировке системы, и один байт
        # там не разбирается ни cp1251, ни utf-8. Поэтому работаем побайтно:
        # исходник не перекодируем, а свои вставки кодируем cp1251.
        eol = (chr(13) + chr(10)).encode('ascii')
        zf = (args.source / 'zf232.cpp').read_bytes()
        anchor = eol.join([b' //- - -',
                           b' if (zf_hPort && zf_hPort != INVALID_HANDLE_VALUE)',
                           b' {', b''])
        assert zf.count(anchor) == 1, 'версия zf232.cpp отличается'
        probe = eol.join([
            b'  if (getenv("FAT32_ZF_TRACE"))',
            b'  {',
            b'   static unsigned zf_trace_calls = 0;',
            b'   if (!(zf_trace_calls++ % 50))',
            b'   {',
            b'    printf("ZF api=%u sel=%u w=%u/%u r=%u/%u res=%02X%c",',
            b'           selected_api_layer, select_zf, zf_whead, zf_wtail,',
            b'           zf_rhead, zf_rtail, result_code, 10);',
            b'    fflush(stdout);',
            b'   }',
            b'  }', b''])
        zf = zf.replace(anchor, anchor + probe)
        read_call = b'     ReadFile(zf_hPort, temprd, canread, &readed, &zf_OvR);'
        assert zf.count(read_call) == 1, 'вызов ReadFile моста ZiFi не найден'
        note = [
            '      // Синхронный отказ ReadFile (на перенаправленном COM-порту это',
            '      // ERROR_HANDLE_EOF) оставляет событие overlapped несигнальным.',
            '      // Мост проверяет его через WaitForSingleObject и после первого',
            '      // же отказа больше никогда не запрашивает чтение: приём умирает',
            '      // навсегда. Взводим событие сами, чтобы попытка повторилась.',
        ]
        read_probe = eol.join([
            b'     {',
            b'      BOOL zf_ok = ReadFile(zf_hPort, temprd, canread, &readed, &zf_OvR);',
            b'      DWORD zf_err = GetLastError();',
            b'      static unsigned zf_read_calls = 0;',
            b'      if (getenv("FAT32_ZF_TRACE") && !(zf_read_calls++ % 50))',
            b'      {',
            b'       printf("ZFread ok=%d err=%lu canread=%d readed=%lu%c",',
            b'              (int)zf_ok, (unsigned long)zf_err, canread,',
            b'              (unsigned long)readed, 10);',
            b'       fflush(stdout);',
            b'      }']
            + [s.encode('cp1251') for s in note]
            + [b'      if (!zf_ok && zf_err != ERROR_IO_PENDING)',
               b'       SetEvent(zf_OvR.hEvent);',
               b'     }'])
        zf = zf.replace(read_call, read_probe)
        (target / 'zf232.cpp').write_bytes(zf)
    if args.port_trace:
        # Журнал пишется только на двух кадрах начиная с FAT32_PORT_TRACE=<кадр>,
        # чтобы не тормозить эмуляцию. Регистры: VCONFIG 00, VPAGE 01, GYOFFS 04/05,
        # TSCONFIG 06, PALSEL 07, BORDER 0F, HSINT 22, VSINT 23/24.
        eol = (chr(13) + chr(10)).encode('ascii')
        io = (args.source / 'io.cpp').read_bytes()
        anchor_io = b'void ts_ext_port_wr(u8 port, u8 val)' + eol + b'{' + eol
        assert io.count(anchor_io) == 1, 'версия io.cpp отличается'
        probe = eol.join([
            b'  {',
            b'    const char *pt = getenv("FAT32_PORT_TRACE");',
            b'    if (pt)',
            b'    {',
            b'      unsigned first = (unsigned)atoi(pt);',
            b'      if (comp.frame_counter >= first && comp.frame_counter < first + 2)',
            b'        switch (port)',
            b'        {',
            b'          case 0x00: case 0x01: case 0x04: case 0x05: case 0x06:',
            b'          case 0x07: case 0x0F: case 0x22: case 0x23: case 0x24:',
            b'            printf("PORT frame=%u t=%u reg=%02X val=%02X%c",',
            b'                   comp.frame_counter, cpu.t, port, val, 10);',
            b'            fflush(stdout);',
            b'            break;',
            b'        }',
            b'    }',
            b'  }', b''])
        (target / 'io.cpp').write_bytes(io.replace(anchor_io, anchor_io + probe))
    if args.hires_border:
        # Unreal рисует бордюр полным индексом vid.clut[border] в любом режиме.
        # В FPGA текстовый режим — единственный hires: video_render.v кладёт в
        # plex оба полубайта как video[3:0], а video_out.v выводит
        # {palsel[3:0], полубайт}. Поэтому в текстовой полосе бордюр равен
        # CRAM[{GPAL, BORDER[3:0]}]. Без этого стенд не показывает цвет, который
        # получает бордюр строки, где запись BORDER опередила смену режима.
        eol = (chr(13) + chr(10)).encode('ascii')
        drawers = (args.source / 'drawers.cpp').read_bytes()
        anchor_br = b'    else p = vid.clut[comp.ts.border];' + eol
        assert drawers.count(anchor_br) == 1, 'версия drawers.cpp отличается'
        hires = (b'    else if (vid.mode == M_TSTX) p = vid.clut[(comp.ts.gpal << 4) | '
                 b'(comp.ts.border & 0x0F)];' + eol)
        (target / 'drawers.cpp').write_bytes(drawers.replace(anchor_br, hires + anchor_br))
    project = target / 'Unreal2017.vcxproj'
    tree = ET.parse(project)
    ET.register_namespace('', 'http://schemas.microsoft.com/developer/msbuild/2003')
    for group in tree.getroot():
        for child in list(group):
            if child.tag.rsplit('}', 1)[-1] == 'PostBuildEvent':
                group.remove(child)
    tree.write(project, encoding='utf-8', xml_declaration=True)
    installer = Path(os.environ['ProgramFiles(x86)']) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    vs = Path(subprocess.check_output([str(installer), '-latest', '-products', '*',
                                      '-property', 'installationPath'], text=True).strip())
    msbuild = vs / 'MSBuild/Current/Bin/MSBuild.exe'
    env = {k:v for k,v in os.environ.items() if k.lower() != 'path'}
    env['Path'] = os.pathsep.join([r'C:\Windows\System32', r'C:\Windows', str(msbuild.parent)])
    with (ROOT/'build/unreal-build.log').open('wb') as log:
        result = subprocess.run([str(msbuild), str(project), '/p:Configuration=Release',
                                 '/p:Platform=x64', '/p:PlatformToolset=v143', '/m', '/v:minimal'],
                                cwd=target, env=env, stdout=log, stderr=subprocess.STDOUT)
    evidence = dict(source=str(args.source.resolve()),
                    sdcard_original_sha256=hashlib.sha256(original).hexdigest(),
                    sdcard_patched_sha256=hashlib.sha256(patched).hexdigest(),
                    mainloop_sha256=hashlib.sha256((target/'mainloop.cpp').read_bytes()).hexdigest(),
                    uart_trace='FAT32_UART_TRACE' in (target/'mainloop.cpp').read_text(encoding='utf-8'),
                    host_poke_sync='FAT32_CACHE_SYNC' in (target/'mainloop.cpp').read_text(encoding='utf-8'),
                    exit_code=result.returncode)
    exe = target/'bin/x64/Release/Unreal.exe'
    if result.returncode == 0:
        evidence['executable'] = str(exe)
        evidence['executable_sha256'] = hashlib.sha256(exe.read_bytes()).hexdigest()
    (ROOT/'build/unreal-build.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, indent=2))
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
