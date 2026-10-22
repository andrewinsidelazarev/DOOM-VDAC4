"""Однократная замена старого встроенного ядра ZiFi самостоятельным драйвером."""
from pathlib import Path

driver=Path(__file__).resolve().parents[1]
project=driver.parent/'ZiFi ESP32-S3 Zero'/'ZiFi SPG'
p=project/'zifi.asm'
text=p.read_text(encoding='utf-8-sig')
if 'INCLUDE "fat32.inc"' in text:
    raise SystemExit('Адаптер уже подключён; повторный перенос не требуется.')
text=text.replace('sd_driver_page\t\tequ #0f',
                  'sd_driver_page\t\tequ #0f\nfat32_code_page\t\tequ #0e\n\t\tINCLUDE "fat32.inc"')
start=text.index('\nsd_init\n')
end=text.index('\non_int_dma',start)
text=text[:start]+text[end:]
start=text.index('\nsave_downloaded_file\t')
end=text.index('\nDEL512 ',start)
text=text[:start]+'\n\t\tINCLUDE "fat32_adapter.asm"\n'+text[end:]
start=text.index('\nset_download_dir\n')
end=text.index('\nwrite_rtc\n',start)
text=text[:start]+text[end:]
start=text.index('\nCORE    EQU #2002')
end=text.index('\n\tstruct thread',start)
text=text[:start]+'''
; Имена прежних вызовов, сохранившиеся в чтении настроек.
FENTRY  EQU FAT32_FIND
SETDIR  EQU FAT32_SET_DIR
SETROOT EQU FAT32_SET_ROOT
'''+text[end:]
text=text.replace('''\t\tCALL FENTRY
\t\tCALL SETDIR
\t\tLD HL,FILE_INI''','''\t\tCALL FENTRY
\t\tJP C,ini_not_found
\t\tJP Z,ini_not_found
\t\tCALL SETDIR
\t\tJP C,ini_not_found
\t\tLD HL,FILE_INI''',1)
text=text.replace('''\t\tCALL FENTRY
\t\tJP Z,ini_not_found''','''\t\tCALL FENTRY
\t\tJP C,ini_not_found
\t\tJP Z,ini_not_found''',1)
old='''\t\tLD C,download_page\t; page ini
\t\tLD HL,#0000
\t\tLD B,#32
\t\tCALL LOAD512'''
new='''\t\tld a,h
\t\tor l
\t\tjp z,ini_not_found
\t\tld a,download_page
\t\tcall set_page3
\t\tLD HL,#c000
\t\tLD B,1
\t\tCALL FAT32_READ
\t\tJP C,ini_not_found
\t\tld hl,(ini_length)
\t\tld de,#c000
\t\tadd hl,de
\t\tld (hl),0'''
assert old in text
text=text.replace(old,new,1)
text=text.replace('\nini_not_found\n','\nini_not_found\n\t\tcall sd_exit\n',1)
old='''\t\tcall save_downloaded_file
\t\tpop af'''
new='''\t\tcall save_downloaded_file
\t\tjr nc,save_action_ready
\t\tpop af
\t\tjp main_ex
save_action_ready:
\t\tpop af'''
assert old in text
text=text.replace(old,new,1)
old='''\t\tcall music_player_play

show_now_play_link'''
new='''\t\tcall music_player_play
\t\t; Во время FAT32 нельзя менять сохранённые банки из автоперехода музыки.
\t\tld a,(fat_active)
\t\tor a
\t\tjp nz,pt_play_ex

show_now_play_link'''
assert old in text
text=text.replace(old,new,1)
text=text.replace('\nend\n','\nend\n\t\tASSERT end <= #be00, код пересёк область векторов прерываний\n',1)
p.write_text(text,encoding='utf-8',newline='\r\n')
print('Адаптер FAT32 подключён к исходнику ZiFi.')
