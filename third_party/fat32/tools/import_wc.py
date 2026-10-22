"""Однократное воспроизводимое извлечение зафиксированных исходников FAT32 из WC.

После извлечения файлы src/ развиваются самостоятельно. Скрипт намеренно
исключён из обычной сборки: новые правки WC не должны молча заменять
исправления самостоятельного проекта.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / 'upstream' / name).read_text(encoding='utf-8-sig')


def write(name, text):
    # Старые комментарии о компоновке описывают WC, а не извлечённую реализацию.
    # Оставляем комментарии при командах; новую архитектуру документируем отдельно.
    text = '\n'.join(line for line in text.splitlines()
                     if not line.lstrip().startswith(';')) + '\n'
    text = re.sub(r'\n{3,}', '\n\n', text)
    (ROOT / name).write_text(text, encoding='utf-8')


core = read('CORE32.ASM')
core = core[core.index('        MACRO CALL_WDOS_EXTENSION'):]
core = core[:core.index('\nEND\n')]
core = re.sub(r'^\s*ASSERT.*$', '', core, flags=re.M)
core = re.sub(r'^\s*DS #[0-9A-Fa-f]+-\$,0.*$', '', core, flags=re.M)
start, end = core.index('\nDOS_SWP\n'), core.index('\nDEL128\n')
core = core[:start] + '\nDOS_SWP JP @DRIVER_BIND\n' + core[end:]
start, end = core.index('        LD A,I', core.index('EXTENSION_GATE:')), core.index('\nSAFE_IO_DISPATCH:')
core = core[:start] + '        JP WDOS_EXT.GATE_CONTINUE\n' + core[end:]
start, end = core.index('\nDR1 '), core.index('\nSTREAM_RESTORE_READ_HANDLER:')
core = core[:start] + '''
EXTENSION_GATE_AFTER:
        EX AF,AF'
        EXX
        PUSH AF
        POP HL
        DUP 9
        POP BC
        EDUP
        PUSH HL
        POP AF
        EXX
        EX AF,AF'
        RET
''' + core[end:]
# WC менял только младший байт вызова: оба обработчика попадали в одну страницу
# 256 байт. При свободной компоновке необходимо менять полный 16-битный адрес.
core = core.replace('SAVE512 LD A,low SAFE_SDDSE\n        LD (NW0+1),A',
                    'SAVE512 PUSH HL\n        LD HL,SAFE_SDDSE\n        LD (NW0+1),HL\n        POP HL')
core = core.replace('        LD HL,NW0+1\n        LD (HL),low SAFE_RDDSE',
                    '        LD HL,SAFE_RDDSE\n        LD (NW0+1),HL')
write('src/core32.asm', '; Основа — WC 1.10i; происхождение: upstream/manifest.json.\n' + core)

kernel = read('KERNEL.ASM')
kernel = kernel[kernel.index('GENBU   EQU'):kernel.index('\nSTART   JP')]
write('src/state.inc', kernel)

extension = read('CORE32_EXT.ASM')
extension = extension.replace('"CORE32_EXT_API.ASM"', '"extension_ids.inc"')
start = extension.index('\nUNPACK_DRIVER:')
end = extension.index('\n; Боевой CORE32', start)
extension = extension[:start] + '\nUNPACK_DRIVER:\n        XOR A\n        RET\n' + extension[end:]
write('src/extension.asm', extension)
write('src/extension_ids.inc', read('CORE32_EXT_API.ASM'))
write('include/filex.inc', read('CORE32_FILEX_API.ASM'))
write('src/filex.asm', read('FILEX_RUNTIME.ASM'))

print('Extracted standalone filesystem sources; no WC build dependency.')
