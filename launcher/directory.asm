; Sector-by-sector FAT32 search, following R-Type VDAC2+'s rtype_loader.asm
; (Andrew Lazarev, MIT; licenses/R-Type-MIT.txt). No loader state is used.
; Match short names, skip LFN/deleted/volume entries, follow FAT chains.
; Stack frames hold parent cursors, so sibling count cannot overflow it.
; FAT32 driver still mounts the volume and opens/reads/writes game files.
; Scratch: sector #C000, FAT #C200, 960 parent frames #C400..#FFFF.
LocateGame:
        xor a
        ld (SawElf),a
        ld (ScanFatValid),a
        ld hl,ScanFrames
        ld (ScanStack),hl
        ld hl,65535
        ld (DirBudget),hl
        call ScanGeometry
        jp nz,ScanIoError
        ld hl,ScanRoot
        ld de,ScanChild
        call ScanCopy32
.enter:
        ld hl,(DirBudget)
        ld a,h
        or l
        jp z,ScanLimit
        dec hl
        ld (DirBudget),hl
        ld hl,ScanChild
        ld de,ScanDir
        call ScanCopy32
        ld hl,ScanChild
        ld de,ScanCluster
        call ScanCopy32
        xor a
        ld (ScanSector),a
        ld (ScanEntry),a
        ld (ScanFiles),a
        ld hl,1
        ld (ScanSteps),hl
.read:
        call ScanClusterLba
        jp c,ScanCorrupt
        ld bc,ScanBuffer
        call ScanReadLba
        jp nz,ScanIoError
        call FtTick                    ; экран загрузки живёт и во время поиска (портит всё)
.entry:
        ld a,(ScanEntry)
        cp 16
        jp z,.next_sector
        ld l,a
        ld h,0
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        ld de,ScanBuffer
        add hl,de
        push hl
        pop ix
        ld hl,ScanEntry
        inc (hl)                       ; resume after this entry
        ld a,(ix+0)
        or a
        jp z,.done_dir
        cp #E5
        jr z,.entry
        ld a,(ix+11)
        bit 3,a                        ; LFN (#0F) and volume labels
        jr nz,.entry
        bit 4,a
        jr nz,.directory
        ld de,ScanElfName
        call ScanName
        jr nz,.wad
        ld a,1
        ld (SawElf),a
        jr .file
.wad:
        ld de,ScanWad2Name
        call ScanName
        jr z,.iwad
        ld de,ScanWadName
        call ScanName
        jr z,.iwad
        ld de,ScanWad1Name
        call ScanName
        jr nz,.entry
.iwad:
        ld a,2
.file:
        ld hl,ScanFiles
        or (hl)
        ld (hl),a
        cp 3
        jr nz,.entry
        ; API FIND in the caller validates the ELF chain and captures FILEX.
        ld hl,ScanDir
        ld de,GameDir
        call ScanCopy32
        ld hl,GameDir
        call FAT32_SET_DIR_CLUSTER      ; API 10 has no A/Z return status
        or a                           ; success: CF=0
        ret
.directory:
        ; FAT dot entries; a leading dot in an LFN has an ordinary SFN alias.
        ld a,(ix+0)
        cp '.'
        jp z,.entry
        ld l,(ix+26)
        ld h,(ix+27)
        ld (ScanChild),hl
        ld l,(ix+20)
        ld h,(ix+21)
        ld a,h
        and #0F
        ld h,a
        ld (ScanChild+2),hl
        call ScanCheckChild
        jp c,ScanCorrupt
        ld de,(ScanStack)
        ld a,d
        or e
        jp z,ScanDepthLimit
        ld hl,ScanState
        ld bc,16
        ldir
        ld (ScanStack),de
        jp .enter
.next_sector:
        xor a
        ld (ScanEntry),a
        ld a,(ScanSector)
        inc a
        ld hl,ScanSectorsPerCluster
        cp (hl)
        ld (ScanSector),a
        jr c,.advance
        call ScanNextCluster
        jp c,ScanIoError
        jr z,.done_dir
        xor a
        ld (ScanSector),a
.advance:
        ld hl,(ScanSteps)
        inc hl
        ld (ScanSteps),hl
        ld de,4097                     ; allow 4096 sectors, including EOC
        or a
        sbc hl,de
        jp nc,ScanLimit
        jp .read
.done_dir:
        ld hl,(ScanStack)
        ld de,ScanFrames
        or a
        sbc hl,de
        jp z,ScanMissing
        ld hl,(ScanStack)
        ld de,16
        or a
        sbc hl,de
        ld (ScanStack),hl
        ld de,ScanState
        ld bc,16
        ldir
        jp .read                       ; reload parent's sector and cursor

; BPB of the volume already validated/mounted by the FAT32 driver.
ScanGeometry:
        ld hl,(#389E)                  ; WDOS.ADDTOP, pinned work-page ABI
        ld (ScanVolume),hl
        ld (ScanLba),hl
        ld hl,(#38A0)
        ld (ScanVolume+2),hl
        ld (ScanLba+2),hl
        ld bc,ScanBuffer
        call ScanReadLba
        ret nz
        ld a,(ScanBuffer+13)
        ld (ScanSectorsPerCluster),a
        ld hl,ScanBuffer+44
        ld de,ScanRoot
        call ScanCopy32
        ld hl,ScanVolume
        ld de,ScanFatStart
        call ScanCopy32
        ld hl,(ScanBuffer+14)
        ld (ScanTemp),hl
        ld hl,0
        ld (ScanTemp+2),hl
        ld hl,ScanFatStart
        ld de,ScanTemp
        call ScanAdd32
        ld hl,ScanFatStart
        ld de,ScanDataStart
        call ScanCopy32
        ld a,(ScanBuffer+40)
        ld c,0
        bit 7,a
        jr z,.mirrored
        and 15
        ld c,a                         ; active FAT if mirroring disabled
.mirrored:
        ld a,(ScanBuffer+16)
        ld b,a
.fats:
        ld a,c
        or a
        jr z,.data
        push bc
        ld hl,ScanFatStart
        ld de,ScanBuffer+36
        call ScanAdd32
        pop bc
        dec c
.data:
        push bc
        ld hl,ScanDataStart
        ld de,ScanBuffer+36
        call ScanAdd32
        pop bc
        djnz .fats
        ld hl,ScanBuffer+32
        ld de,ScanEnd
        call ScanCopy32
        ld hl,ScanEnd
        ld de,ScanVolume
        call ScanAdd32
        xor a
        ret

; LBA = data + (cluster - 2)*SPC + sector-in-cluster; CF=invalid address.
ScanClusterLba:
        ld hl,(ScanCluster+2)
        ld a,h
        cp #10
        jr nc,.bad
        ld de,(ScanCluster)
        ld a,h
        or l
        jr nz,.valid
        ld a,d
        or a
        jr nz,.valid
        ld a,e
        cp 2
        jr c,.bad
.valid:
        ex de,hl
        ld bc,2
        or a
        sbc hl,bc
        jr nc,.no_borrow
        dec de
.no_borrow:
        ld a,(ScanSectorsPerCluster)
.multiply:
        srl a
        jr c,.multiplied
        add hl,hl
        rl e
        rl d
        jr c,.bad
        jr .multiply
.multiplied:
        ld a,(ScanSector)
        ld c,a
        ld b,0
        add hl,bc
        jr nc,.sector
        inc de
.sector:
        ld (ScanLba),hl
        ld (ScanLba+2),de
        ld hl,ScanLba
        ld de,ScanDataStart
        call ScanAdd32
        jr c,.bad
        ld hl,(ScanLba)
        ld de,(ScanEnd)
        or a
        sbc hl,de
        ld hl,(ScanLba+2)
        ld de,(ScanEnd+2)
        sbc hl,de
        jr nc,.bad
        or a
        ret
.bad:
        scf
        ret

; Next link from the active FAT: CF=error, Z=end, NZ=next cluster.
ScanNextCluster:
        ld hl,(ScanCluster)
        ld de,(ScanCluster+2)
        ld a,l
        and 127
        ld (ScanFatEntry),a
        ld b,7
.shift:
        srl d
        rr e
        rr h
        rr l
        djnz .shift
        ld (ScanLba),hl
        ld (ScanLba+2),de
        ld hl,ScanLba
        ld de,ScanFatStart
        call ScanAdd32
        ld a,(ScanFatValid)
        or a
        jr z,.read
        ld hl,(ScanLba)
        ld de,(ScanFatCached)
        or a
        sbc hl,de
        jr nz,.read
        ld hl,(ScanLba+2)
        ld de,(ScanFatCached+2)
        sbc hl,de
        jr z,.cached
.read:
        ld bc,ScanFatBuffer
        call ScanReadLba
        jr nz,.error
        ld hl,ScanLba
        ld de,ScanFatCached
        call ScanCopy32
        ld a,1
        ld (ScanFatValid),a
.cached:
        ld a,(ScanFatEntry)
        ld l,a
        ld h,0
        add hl,hl
        add hl,hl
        ld de,ScanFatBuffer
        add hl,de
        ld de,ScanCluster
        call ScanCopy32
        ld a,(ScanCluster+3)
        and #0F
        ld (ScanCluster+3),a
        ld hl,(ScanCluster)
        ld de,#FFF8
        or a
        sbc hl,de
        ld hl,(ScanCluster+2)
        ld de,#0FFF
        sbc hl,de
        jr nc,.end
        ld hl,(ScanCluster)
        ld de,#FFF0
        or a
        sbc hl,de
        ld hl,(ScanCluster+2)
        ld de,#0FFF
        sbc hl,de
        jr nc,.bad
        ld hl,(ScanCluster+2)
        ld a,h
        or l
        jr nz,.next
        ld hl,(ScanCluster)
        ld de,2
        or a
        sbc hl,de
        jr c,.bad
.next:
        xor a
        inc a
        ret
.end:
        xor a
        ret
.bad:
        ld a,#FE
.error:
        scf
        ret

; A child cannot point to an ancestor. Normal '.'/'..' are already skipped.
ScanCheckChild:
        ld hl,ScanDir
        call .equal
        jr z,.bad
        ld hl,ScanFrames
.frame:
        ld de,(ScanStack)
        or a
        sbc hl,de
        add hl,de
        jr z,.good
        push hl
        call .equal
        pop hl
        jr z,.bad
        ld de,16
        add hl,de
        jr .frame
.equal:
        ld de,ScanChild
        ld b,4
.byte:
        ld a,(de)
        cp (hl)
        ret nz
        inc hl
        inc de
        djnz .byte
        ret
.bad:
        scf
        ret
.good:
        or a
        ret

ScanName:
        push ix
        pop hl
        ld b,11
.char:
        ld a,(hl)
        cp 'a'
        jr c,.compare
        cp 'z'+1
        jr nc,.compare
        and #DF
.compare:
        ex de,hl
        cp (hl)
        ex de,hl
        ret nz
        inc hl
        inc de
        djnz .char
        ret

; Device read512 call, as in R-Type's ReadLba (BC=buffer, DE:HL=LBA).
ScanReadLba:
        ld hl,(ScanLba)
        ld de,(ScanLba+2)
        push ix
        call .port
        pop ix
        or a
        ret
.port:
        push hl
        ld hl,(#3902)
        ex (sp),hl
        ret
ScanCopy32:
        ld bc,4
        ldir
        ret
ScanAdd32:
        ld b,4
        or a
.byte:
        ld a,(de)
        adc a,(hl)
        ld (hl),a
        inc hl
        inc de
        djnz .byte
        ret

ScanMissing:
        ld hl,MsgMissingElf
        ld a,(SawElf)
        or a
        jr z,.message
        ld hl,MsgMissingWad
.message:
        ld a,#F1
        jr ScanError
ScanDepthLimit:
        ld hl,MsgDepthLimit
        ld a,#F5
        jr ScanError
ScanLimit:
        ld hl,MsgSearchLimit
        ld a,#F6
        jr ScanError
ScanCorrupt:
        ld a,#FE
ScanIoError:
        ld hl,MsgReadError
ScanError:
        ld (BootMessage),hl
        scf
        ret

ScanState:
ScanDir: dd 0
ScanCluster: dd 0
ScanSector: db 0
ScanEntry: db 0
ScanFiles: db 0
        db 0
ScanSteps: dw 0
        dw 0
        ASSERT $-ScanState == 16
ScanStack: dw ScanFrames
DirBudget: dw 0
SawElf: db 0
ScanSectorsPerCluster: db 0
ScanFatValid: db 0
ScanFatEntry: db 0
ScanChild: dd 0
ScanRoot: dd 0
ScanVolume: dd 0
ScanFatStart: dd 0
ScanDataStart: dd 0
ScanEnd: dd 0
ScanLba: dd 0
ScanTemp: dd 0
ScanFatCached: dd 0
ScanElfName: db "DOOM    ELF"
ScanWad2Name: db "DOOM2   WAD"
ScanWadName: db "DOOM    WAD"
ScanWad1Name: db "DOOM1   WAD"
ScanBuffer: equ #C000
ScanFatBuffer: equ #C200
ScanFrames: equ #C400
