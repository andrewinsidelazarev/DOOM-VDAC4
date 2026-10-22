; Выбор игры, если в каталоге игры несколько IWAD: меню на экране загрузки FT812 (ftscreen.asm,
; FtPutMenu) вместо строки этапа. Пункты — в порядке таблицы MenuWads: DOOM.WAD «DOOM»,
; DOOM1.WAD «DOOM SHAREWARE», DOOM2.WAD «DOOM II»; есть только найденные, номера 1…n.
; Клавиатура — очередь PS/2 AVR (Ps2Open, как SendKeys): цифра — сразу этот пункт; стрелки
; вверх/вниз (и влево/вправо), W/S — выбор; Enter, пробел — запуск. Джойстик Kempston: вверх/
; вниз — выбор, огонь — запуск. Без FT812 меню не видно — тогда первый пункт (игра без FT812
; всё равно не запустится: DOOM.ELF рисует в FT812).
; SPDX-License-Identifier: GPL-2.0-or-later

WAD_ITEMS       equ 3

; Выход: NZ — WadConfig/WadFind заполнены выбранным IWAD (FindWad); Z — IWAD нет.
ChooseWad:
        xor a
        ld (MenuCount),a
        ld (MenuProbe),a
.probe:
        ld a,(MenuProbe)
        call MenuEntry
        ld e,(hl)
        inc hl
        ld d,(hl)
        ex de,hl                        ; HL — имя для FAT32_FIND
        call FAT32_FIND
        jp c,FileFindError
        jr z,.next                      ; такого файла нет
        ld a,(MenuCount)
        ld e,a
        ld d,0
        ld hl,MenuList
        add hl,de
        ld a,(MenuProbe)
        ld (hl),a                       ; пункт меню → номер в таблице
        ld a,e
        inc a
        ld (MenuCount),a
.next:
        ld a,(MenuProbe)
        inc a
        ld (MenuProbe),a
        cp WAD_ITEMS
        jr c,.probe
        ld a,(MenuCount)
        or a
        ret z                           ; IWAD нет
        cp 2
        jr c,.first                     ; один — без меню
        ld a,(FtOk)
        or a
        jr z,.first                     ; экрана FT812 нет — меню не видно
        call WadMenu
        jr .take
.first:
        xor a
.take:                                  ; A — пункт меню
        ld e,a
        ld d,0
        ld hl,MenuList
        add hl,de
        ld a,(hl)
        call MenuEntry
        ld e,(hl)
        inc hl
        ld d,(hl)
        ex de,hl
        jp FindWad                      ; NZ — найден

; HL ← запись таблицы MenuWads номер A (имя, подпись).
MenuEntry:
        add a,a
        add a,a
        ld e,a
        ld d,0
        ld hl,MenuWads
        add hl,de
        ret

; Меню: кадры рисует FtTick (FtMenu — лист с пунктами), клавиши — MenuKey.
; Выход: A — выбранный пункт.
WadMenu:
        xor a
        ld (MenuSel),a
        ld (Ps2Break),a
        ld a,#1C
        ld (MenuJoy),a                  ; уже нажатое на джойстике не срабатывает
        call Ps2Drain                   ; нажатое до меню — не выбор
        ld a,1
        ld (FtMenu),a
        ld hl,(FtLastDraw)              ; клавиши — когда меню уже на экране
        ld (MenuFrame),hl
        ld bc,0                         ; не дольше 65536 опросов (FT812 замолчал — без ожидания)
.shown:
        push bc
        call FtTick
        pop bc
        ld hl,(FtLastDraw)
        ld de,(MenuFrame)
        or a
        sbc hl,de
        jr nz,.loop
        dec bc
        ld a,b
        or c
        jr nz,.shown
.loop:
        call FtTick
        call MenuKey
        or a
        jr z,.loop
        cp 1
        jr z,.up
        cp 2
        jr z,.down
        cp 3
        jr z,.go
        and #0F                         ; цифра: пункт A
        ld hl,MenuCount
        cp (hl)
        jr nc,.loop                     ; такого пункта нет
        ld (MenuSel),a
        jr .go
.up:
        ld a,(MenuSel)
        or a
        jr nz,.prev
        ld a,(MenuCount)
.prev:
        dec a
        ld (MenuSel),a
        jr .loop
.down:
        ld a,(MenuSel)
        inc a
        ld hl,MenuCount
        cp (hl)
        jr c,.set
        xor a
.set:
        ld (MenuSel),a
        jr .loop
.go:
        xor a
        ld (FtMenu),a
        call Ps2Drain                   ; отпускания и прочее — не в DOOM
        ld a,(MenuSel)
        ret

; Действие с клавиатуры или джойстика: 0 — нет, 1 — вверх, 2 — вниз, 3 — запуск, #10+n — пункт n.
MenuKey:
        call Ps2Open                    ; BC = #BFF7
.byte:
        in a,(c)
        or a
        jr z,.joy                       ; очередь пуста
        cp #E0
        jr z,.byte                      ; расширенная: стрелки = цифровому блоку, Enter тот же
        cp #F0
        jr nz,.code
        ld a,1
        ld (Ps2Break),a
        jr .byte
.code:
        ld e,a
        ld hl,Ps2Break
        ld a,(hl)
        ld (hl),0
        or a
        jr nz,.byte                     ; отпускание
        ld hl,MenuKeys
.find:
        ld a,(hl)
        or a
        jr z,.byte                      ; клавиша не меню
        inc hl
        cp e
        ld a,(hl)
        inc hl
        jr nz,.find
        ret
.joy:                                   ; Kempston: бит 2 вниз, 3 вверх, 4 огонь; #FF — нет джойстика
        in a,(#1F)
        cp #FF
        jr nz,.kempston
        xor a
.kempston:
        and #1C
        ld d,a
        ld hl,MenuJoy
        ld a,(hl)
        ld (hl),d
        cpl
        and d                           ; только что нажатое
        bit 4,a
        ld e,3
        jr nz,.joy_act
        bit 3,a
        ld e,1
        jr nz,.joy_act
        bit 2,a
        ld e,2
        jr nz,.joy_act
        xor a
        ret
.joy_act:
        ld a,e
        ret

; Очередь PS/2 — до пустой.
Ps2Drain:
        call Ps2Open
.byte:
        in a,(c)
        or a
        jr nz,.byte
        ld (Ps2Break),a
        ret

; Скан-код (набор 2) → действие MenuKey.
MenuKeys:
        db #16,#10, #1E,#11, #26,#12   ; 1, 2, 3
        db #75,1, #6B,1, #1D,1          ; вверх, влево, W
        db #72,2, #74,2, #1B,2          ; вниз, вправо, S
        db #5A,3, #29,3                 ; Enter, пробел
        db 0

; имя для FAT32_FIND (doom.asm), подпись пункта
MenuWads:
        dw WadFull, MenuDoom
        dw WadShare, MenuShare
        dw Wad2, MenuDoom2
MenuDoom:  db "DOOM",0
MenuShare: db "DOOM SHAREWARE",0
MenuDoom2: db "DOOM II",0

MenuCount: db 0                         ; пунктов
MenuProbe: db 0
MenuSel:   db 0                         ; выделенный пункт
MenuJoy:   db 0                         ; прошлое состояние джойстика
MenuFrame: dw 0                         ; FtLastDraw до меню
Ps2Break:  db 0                         ; был префикс F0
MenuList:  ds WAD_ITEMS                 ; пункт → номер в MenuWads
