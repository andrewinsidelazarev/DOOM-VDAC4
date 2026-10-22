; Проверочный снимок ServeFile до 0.1.8: данные записи внутри события E5.
; E5 [seq16,op,name13,other13,offset32,count16,data] ->
; 74 [seq16,status8,count16,size32,data]. All names are relative to GameDir.
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
        jp nc,Fail
        ld a,(ProtoBuf+2)
        ld (FileOp),a
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
        jr z,.truncate
        cp 1
        jr z,.read
        cp 3
        jp nz,.invalid
        ld hl,(ProtoRxLen)
        ld de,35
        or a
        sbc hl,de
        ld de,(FileCount)
        sbc hl,de
        jp nz,.invalid
        ld hl,ProtoBuf+35
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
