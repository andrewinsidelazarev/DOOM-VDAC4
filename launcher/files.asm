; E5 [seq16,op,name13,other13,offset32,count16] — только заголовок, 35 байт.
; Запись: 76 [seq16,offset16] -> F6 [seq16,ok8,offset16,count16,data] — порции до 248 байт (кадр к Z80
; не больше 256 байт), всего до 960 байт данных.
; Результат: 74 [seq16,status8,count16,size32,data]. Имена — относительно GameDir.
; 0 stat, 1 read, 2 create/truncate, 3 write, 4 replace/rename, 5 delete, 6 sync.
ServeFile:
        xor a
        ld (WadSelected),a
        ld hl,(ProtoRxLen)
        ld de,35
        or a
        sbc hl,de
        jp c,Fail
        ld hl,PutBuf
        ld de,PutBuf+1
        ld bc,8
        ld (hl),0
        ldir
        ld hl,(ProtoBuf)
        ld (PutBuf),hl
        ; Старый ELF присылал данные в E5. Такой запрос отвергается до операций с файлом.
        ld hl,(ProtoRxLen)
        ld de,35
        or a
        sbc hl,de
        jp nz,.invalid
        ld hl,ProtoBuf+3
        ld de,FileName+1
        ld bc,13
        ldir
        ld hl,ProtoBuf+16
        ld de,OtherName+1
        ld bc,13
        ldir
        ld hl,FileFx
        ld de,FileFx+1
        ld bc,31
        ld (hl),0
        ldir
        ld a,32
        ld (FileFx),a
        ld a,1
        ld (FileFx+1),a
        ld hl,ProtoBuf+29
        ld de,FileFx+4
        ld bc,6
        ldir                   ; offset copied; count put separately below
        ld hl,(ProtoBuf+33)
        ld (FileFx+10),hl
        ld (FileCount),hl
        ld de,961
        or a
        sbc hl,de
        jp nc,.invalid
        ld a,(ProtoBuf+2)
        ld (FileOp),a
        cp 7
        jp nc,.invalid
        cp 6
        jp z,.sync
        cp 5
        jp z,.delete
        cp 4
        jp z,.rename
        ld hl,FileName
        call FAT32_FIND
        jp c,.error
        jr nz,.exists
        ld a,(FileOp)
        cp 2
        jr z,.create
        ld a,#19
        jp .error
.create:
        ld hl,FileName+1
        ld de,CreateName+5
        ld bc,13
        ldir
        ld hl,CreateName
        call FAT32_CREATE
        jp nz,.error
        jp .sync
.exists:
        ld (PutBuf+5),hl
        ld (PutBuf+7),de
        ld a,(FileOp)
        or a
        jp z,.ok
        cp 2
        jp z,.truncate
        cp 1
        jp z,.read
        cp 3
        jp nz,.invalid
        ; Данные записи — ответами F6 на команду 76: очередь событий ESP вмещает лишь 63 байта,
        ; а кадр к Z80 — не больше 256 байт (аппаратная очередь ZiFi; кадр ~1 КБ на плате терялся
        ; целиком — 0.1.8). Порции до PIECE байт, как NET_CHUNK у ZiFi SPG, собираются в PutBuf+9;
        ; WRITE_AT — только когда собран и проверен весь блок, ошибка или чужой seq файл не меняют.
        ld hl,0
        ld (PullAt),hl
.pull:
        ld hl,(PullAt)
        ld de,(FileCount)
        or a
        sbc hl,de
        jp nc,.pulled                   ; собрано всё
        ld a,PULL_TRIES
        ld (PullTries),a
.pull_try:
        ld hl,(PutBuf)                  ; 76 [seq, смещение]
        ld (PullReq),hl
        ld hl,(PullAt)
        ld (PullReq+2),hl
        ld hl,PullReq
        ld bc,4
        ld a,#76
        call Proto_Send
.pull_wait:
        ld a,#F6
        ld b,1
        call WaitLong
        jr nc,.pull_got
        ld hl,PullTries                 ; кадр не дошёл — переспросить ту же порцию
        dec (hl)
        jr nz,.pull_try
        jp .invalid
.pull_stale:
        ld hl,PullTries                 ; запоздавший ответ на прежний запрос — ждать свой
        dec (hl)
        jr nz,.pull_wait
        jp .invalid
.pull_got:
        ld hl,(ProtoBuf)                ; F6 [seq, 1, смещение, число, данные]
        ld de,(PutBuf)
        or a
        sbc hl,de
        jr nz,.pull_stale               ; чужой seq — ответ прежней операции
        ld a,(ProtoBuf+2)
        cp 1
        jp nz,.invalid
        ld hl,(ProtoBuf+3)
        ld de,(PullAt)
        or a
        sbc hl,de
        jr c,.pull_stale                ; дубликат уже собранной порции
        jp nz,.invalid
        ld hl,(FileCount)               ; число — min(PIECE, сколько осталось)
        ld de,(PullAt)
        or a
        sbc hl,de
        ld de,PIECE
        or a
        sbc hl,de
        jr c,.pull_last
        ld hl,PIECE
        jr .pull_want
.pull_last:
        add hl,de
.pull_want:
        ld (PullLen),hl
        ld de,(ProtoBuf+5)
        or a
        sbc hl,de
        jp nz,.invalid
        ld hl,(ProtoRxLen)
        ld de,7
        or a
        sbc hl,de
        ld de,(PullLen)
        or a
        sbc hl,de
        jp nz,.invalid
        ld hl,PutBuf+9
        ld de,(PullAt)
        add hl,de
        ex de,hl
        ld hl,ProtoBuf+7
        ld bc,(PullLen)
        ldir
        ld hl,(PullAt)
        ld de,(PullLen)
        add hl,de
        ld (PullAt),hl
        jp .pull
.pulled:
        ld hl,PutBuf+9
        ld (FileFx+8),hl
        ld a,2
        jr .transfer
.read:
        ld hl,PutBuf+9
        ld (FileFx+8),hl
        ld a,1
.transfer:
        ld (FileFx+2),a
        ld hl,FileFx
        call FAT32_FILEX
        jp nz,.error
        ld hl,(FileFx+24)
        ld de,(FileCount)
        or a
        sbc hl,de
        jr nz,.invalid
        ld a,(FileOp)
        cp 1
        jr nz,.sync
        ld (PutBuf+3),de
        jr .ok
.truncate:
        ld a,3
        ld (FileFx+2),a
        ld hl,FileFx
        call FAT32_FILEX
        jr nz,.error
        jr .sync
.rename:
        ld a,5
        ld (FileFx+2),a
        ld a,FILEX_FLAG_REPLACE+FILEX_FLAG_CURRENT_DIR
        ld (FileFx+3),a
        ld hl,FileName
        ld (FileFx+8),hl
        ld hl,OtherName
        ld (FileFx+12),hl
        ld hl,14
        ld (FileFx+10),hl
        ld (FileFx+14),hl
        ld hl,FileFx
        call FAT32_FILEX
        jr nz,.error
        jr .sync
.delete:
        ld hl,FileName
        call FAT32_DELETE
        jr nz,.error
.sync:
        call FAT32_SYNC
        jr nz,.error
.ok:
        xor a
        jr .answer
.invalid:
        ld a,#10
.error:
        or a
        jr nz,.answer
        inc a
.answer:
        ld (PutBuf+2),a
        ld bc,(PutBuf+3)
        ld hl,9
        add hl,bc
        ld b,h
        ld c,l
        ld hl,PutBuf
        ld a,#74
        call Proto_Send
        xor a
        ret
PIECE equ 248                           ; данных в одном F6 (кадр 255 байт)
PULL_TRIES equ 3                        ; попыток на порцию
PullAt: dw 0                            ; сколько байт блока уже собрано
PullLen: dw 0
PullTries: db 0
PullReq: ds 4
FileOp: db 0
FileCount: dw 0
FileName: db 0
        ds 13
OtherName: db 0
        ds 13
CreateName: db #20
        dd 0
        ds 13
FileFx: ds 32
