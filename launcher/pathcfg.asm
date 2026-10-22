; Папка игры — в path.cfg во flash ESP (PATH_GET/PATH_SET, прошивка VDAC4 0.1.83+), без WC и
; без файлов на карте. Ключ — имя «DOOM» и серийный номер тома FAT32 (BPB +67): у каждой карты
; своя запись. В найденной так папке проверяется сигнатура игры — DOOM.ELF и IWAD; нет её
; (папку удалили, карта другая) или записи нет — полный поиск по карте (LocateGame), и путь
; обновляется. Прошивка без этих команд отвечает ошибкой — тогда поиск, как раньше, и путь
; не пишется. Папку можно переносить по карте: кластер каталога тот же.

CMD_PATH_GET    equ #2A                 ; [серийный LE32][имя, 0] -> AA [1 есть / 0 нет][кластер LE32]
RESP_PATH_GET   equ #AA
CMD_PATH_SET    equ #2B                 ; [серийный LE32][кластер LE32][имя, 0] -> AB [1 / 0]
RESP_PATH_SET   equ #AB

; Папка из ESP: NZ — подошла, GameDir и текущий каталог драйвера — она; Z — искать.
PathCfgLoad:
        xor a
        ld (PathEsp),a
        call ScanGeometry               ; BPB тома — в ScanBuffer
        jp nz,.no
        ld hl,ScanBuffer+67
        ld de,PathGetMsg
        ld bc,4
        ldir
        ld hl,ScanBuffer+67
        ld de,PathSetMsg
        ld bc,4
        ldir
        ld hl,PathGetMsg
        ld bc,PathGetEnd-PathGetMsg
        ld a,CMD_PATH_GET
        call Proto_Send
        ld a,RESP_PATH_GET
        call PathWait
        jp c,.no                        ; прошивка без path.cfg
        ld a,1
        ld (PathEsp),a                  ; ESP знает команды: найденный поиском путь — записать
        ld a,(ProtoBuf)
        or a
        jp z,.no
        ld hl,ProtoBuf+1
        ld de,PathDir
        call ScanCopy32
        ; кластер внутри тома (2.., сектор до конца тома) — как у каталогов поиска
        ld hl,PathDir
        ld de,ScanCluster
        call ScanCopy32
        xor a
        ld (ScanSector),a
        call ScanClusterLba
        jr c,.no
        ld hl,PathDir
        call FAT32_SET_DIR_CLUSTER
        ld hl,ElfSpec
        call FAT32_FIND
        jr c,.no
        jr z,.no
        ld hl,Wad2
        call .wad
        jr nz,.yes
        ld hl,WadFull
        call .wad
        jr nz,.yes
        ld hl,WadShare
        call .wad
        jr z,.no
.yes:
        ld hl,PathDir
        ld de,GameDir
        call ScanCopy32
        ld a,1
        or a
        ret
.no:
        xor a
        ret
.wad:                                   ; NZ — IWAD есть
        call FAT32_FIND
        jr c,.none
        ret
.none:
        xor a
        ret

; После поиска: путь GameDir — в ESP. Отказ записи игре не мешает: следующий запуск снова ищет.
PathCfgSave:
        ld a,(PathEsp)
        or a
        ret z
        ld hl,GameDir
        ld de,PathSetMsg+4
        call ScanCopy32
        ld hl,PathSetMsg
        ld bc,PathSetEnd-PathSetMsg
        ld a,CMD_PATH_SET
        call Proto_Send
        ld a,RESP_PATH_SET
        jp PathWait

; Ответ A от ESP: CF=0 — пришёл (тело в ProtoBuf); CF=1 — ESP ответил ошибкой (команды не
; знает) или молчал. Экран загрузки живёт и тут.
PathWait:
        ld (PathWant),a
        ld de,#FFFF
.loop:
        push de
        call Proto_Poll
        pop de
        jr nc,.idle
        ld hl,PathWant
        cp (hl)
        ret z                           ; CF=0
        cp RESP_ERROR
        scf
        ret z
.idle:
        ld a,e
        and 63
        jr nz,.count
        push de
        call FtTick
        pop de
.count:
        dec de
        ld a,d
        or e
        jr nz,.loop
        scf
        ret

PathGetMsg: ds 4
        db "DOOM",0
PathGetEnd:
PathSetMsg: ds 8
        db "DOOM",0
PathSetEnd:
PathDir: dd 0
PathEsp: db 0
PathWant: db 0
