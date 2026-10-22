; Read-only ELF/WAD extents, following R-Type VDAC2+'s rtype_loader.asm
; (Andrew Lazarev, MIT; licenses/R-Type-MIT.txt).
; FIND validates/selects the file once. Build its sector -> LBA map once,
; then read through the mounted driver's read512 entry (#3902).
; Saves still use FILEX. Oversized/highly fragmented files use FILEX too.
; Directory traversal is finished before its parent-frame RAM is reused.
FastExtents equ #C400
FastMaxExtents equ (#10000-FastExtents)/8

FastOpen:
        xor a
        ld (FastEnabled),a
        ld (FastCacheValid),a
        ld (ScanFatValid),a
        ld (ScanSector),a
        ld hl,0
        ld (FastCount),hl
        ld (FastSector),hl
        ld hl,(#3868+28)
        ld de,(#3868+30)
        ld (FastSize),hl
        ld (FastSize+2),de
        ; ceil(size/512), with a 16-bit sector index (<32 MiB).
        ld a,d
        cp 2
        jr nc,.fallback
        ld bc,511
        add hl,bc
        jr nc,.rounded
        inc de
.rounded:
        ld a,d
        cp 2
        jr nc,.fallback
        ld l,h
        ld h,e
        srl d
        rr h
        rr l
        ld a,h
        or l
        jr z,.fallback
        ld (FastSectors),hl
        ld hl,(#3868+26)
        ld (ScanCluster),hl
        ld hl,(#3868+20)
        ld a,h
        and #0F
        ld h,a
        ld (ScanCluster+2),hl
.cluster:
        call ScanClusterLba
        jp c,FastBad
        ; Last extent may end partway through the final cluster.
        ld hl,(FastSectors)
        ld de,(FastSector)
        or a
        sbc hl,de
        ld a,(ScanSectorsPerCluster)
        ld e,a
        ld d,0
        push hl
        or a
        sbc hl,de
        pop hl
        jr c,.part
        ex de,hl
.part:
        ld (FastPart),hl
        call FastAppend
        jr c,.fallback
        ld hl,(FastSector)
        ld de,(FastPart)
        add hl,de
        ld (FastSector),hl
        ld de,(FastSectors)
        or a
        sbc hl,de
        jr z,.ready
        call ScanNextCluster
        jp c,FastError
        jp z,FastBad
        jr .cluster
.ready:
        ld a,1
        ld (FastEnabled),a
.fallback:
        xor a
        ret

; Add/merge (file sector u16, length u16, SD LBA u32).
FastAppend:
        ld hl,(FastCount)
        ld a,h
        or l
        jr z,.new
        dec hl
        call FastAddress
        ld l,(ix+4)
        ld h,(ix+5)
        ld e,(ix+2)
        ld d,(ix+3)
        add hl,de
        ld e,(ix+6)
        ld d,(ix+7)
        jr nc,.end_lba
        inc de
.end_lba:
        ld bc,(ScanLba)
        or a
        sbc hl,bc
        jr nz,.new
        ex de,hl
        ld bc,(ScanLba+2)
        or a
        sbc hl,bc
        jr nz,.new
        ld l,(ix+2)
        ld h,(ix+3)
        ld de,(FastPart)
        add hl,de
        ld (ix+2),l
        ld (ix+3),h
        or a
        ret
.new:
        ld hl,(FastCount)
        ld de,FastMaxExtents
        or a
        sbc hl,de
        jr nc,.full
        ld hl,(FastCount)
        call FastAddress
        inc hl
        ld (FastCount),hl
        ld hl,(FastSector)
        ld (ix+0),l
        ld (ix+1),h
        ld hl,(FastPart)
        ld (ix+2),l
        ld (ix+3),h
        ld hl,(ScanLba)
        ld (ix+4),l
        ld (ix+5),h
        ld hl,(ScanLba+2)
        ld (ix+6),l
        ld (ix+7),h
        or a
        ret
.full:
        scf
        ret
FastAddress:
        push hl
        add hl,hl
        add hl,hl
        add hl,hl
        ld de,FastExtents
        add hl,de
        push hl
        pop ix
        pop hl
        ret

; Same Fx block / exact-length contract as ReadAt. Does not touch FILEX state.
FastReadAt:
        ld hl,(Fx+10)
        ld (FastRemaining),hl
        ld a,h
        or l
        ret z
        ld de,(Fx+4)
        add hl,de
        ld (FastEnd),hl
        ld hl,(Fx+6)
        ld de,0
        adc hl,de
        jp c,FastBad
        ld de,(FastSize+2)
        or a
        sbc hl,de
        jr c,.inside
        jp nz,FastBad
        ld hl,(FastEnd)
        ld de,(FastSize)
        or a
        sbc hl,de
        jp c,.inside
        jp nz,FastBad
.inside:
        ld hl,(Fx+8)
        ld (FastDest),hl
        ld hl,(Fx+4)
        ld a,h
        and 1
        ld h,a
        ld (FastByte),hl
        ld hl,(Fx+4)
        ld de,(Fx+6)
        ld l,h
        ld h,e
        srl d
        rr h
        rr l
        ld (FastSector),hl
.loop:
        call FastReadSector
        ret nz
        ld hl,512
        ld de,(FastByte)
        or a
        sbc hl,de
        ld de,(FastRemaining)
        push hl
        or a
        sbc hl,de
        pop hl
        jr c,.chunk
        ex de,hl
.chunk:
        ld (FastPart),hl
        ld b,h
        ld c,l
        ld hl,ScanBuffer
        ld de,(FastByte)
        add hl,de
        ld de,(FastDest)
        ldir
        ld (FastDest),de
        ld hl,(FastRemaining)
        ld de,(FastPart)
        or a
        sbc hl,de
        ld (FastRemaining),hl
        ld a,h
        or l
        ret z
        ld hl,0
        ld (FastByte),hl
        ld hl,(FastSector)
        inc hl
        ld (FastSector),hl
        jr .loop

; R-Type's ReadFileSector lookup, plus a last-sector cache for unaligned
; 1016-byte protocol chunks that overlap a physical sector.
FastReadSector:
        ld a,(FastCacheValid)
        or a
        jr z,.lookup
        ld hl,(FastSector)
        ld de,(FastCached)
        or a
        sbc hl,de
        ret z
.lookup:
        xor a
        ld (FastCacheValid),a
        ld hl,0
.find:
        ld de,(FastCount)
        push hl
        or a
        sbc hl,de
        pop hl
        jp nc,FastBad
        call FastAddress
        push hl
        ld hl,(FastSector)
        ld e,(ix+0)
        ld d,(ix+1)
        or a
        sbc hl,de
        jr c,.next
        ld e,(ix+2)
        ld d,(ix+3)
        push hl
        or a
        sbc hl,de
        pop hl
        jr nc,.next
        ld e,(ix+4)
        ld d,(ix+5)
        add hl,de
        ld (ScanLba),hl
        ld l,(ix+6)
        ld h,(ix+7)
        jr nc,.lba
        inc hl
.lba:
        ld (ScanLba+2),hl
        pop hl
        ld bc,ScanBuffer
        call ScanReadLba
        ret nz
        ld hl,(FastSector)
        ld (FastCached),hl
        ld a,1
        ld (FastCacheValid),a
        xor a
        ret
.next:
        pop hl
        inc hl
        jr .find
FastBad:
        ld a,#FE
FastError:
        or a
        ret
FastEnabled: db 0
FastCacheValid: db 0
FastCount: dw 0
FastSector: dw 0
FastSectors: dw 0
FastPart: dw 0
FastRemaining: dw 0
FastDest: dw 0
FastByte: dw 0
FastCached: dw 0
FastEnd: dw 0
FastSize: dd 0
