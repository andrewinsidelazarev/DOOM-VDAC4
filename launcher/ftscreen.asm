; Экран загрузки DOOM с первого шага, как у R-Type: хост FT812 здесь — сам Z80, по SPI Evo
; (#77 — выбор, #57 — данные; чтение — как у Z-Controller: IN отдаёт байт прошлого обмена).
; Экран — тот же, что у DOOM.ELF (main/loader.c, лист — main/loader_dl.c): картинка (JPEG
; страниц SPG, его распаковывает сопроцессор BL616) с проявлением, полоса с бликом и дышащим
; свечением, строка этапа, проценты и строка WAD — шрифтами ПЗУ FT812, с тенью.
;
; FtScreen_Start — сразу после остановки прежнего приложения ESP (тогда хост FT812 — снова
; Evo): M_SEL, команды хоста как у ESP (ft_host::boot1024x768) — в слоте TS-Config ACTIVE
; перезагружает Tang в ядро FT812, — ждём REG_ID, 1024×768@59, чёрный лист. Затем — ждём
; шрифты ПЗУ: ESP заливает их окном FT сразу после загрузки ядра (~1,4 с), и распаковка картинки
; в это время отнимала бы порт FT812 у окна ESP (у порта сопроцессор — первым): на плате с 0.1.5
; заливка падала («ROM fonts … FAILED»), и строк не было и у DOOM.ELF. Потом — JPEG картинки
; (LDRPIC_PAGE0…, tools/make_launcher_pic.py) сопроцессору командой CMD_LOADIMAGE (OPT_NODL)
; в RAM_G FT_PIC, и когда она распакована — сразу весь экран: картинка на полной яркости,
; полоса и строки (до того экран чёрный, промежуточных кадров нет).
; FtTick — кадр экрана, не чаще раза в 2 кадра FT812 (REG_FRAMES — часы анимации): его зовут
; ожидания загрузчика (Proto_WaitCmd, кольцо сопроцессора). FtProgress — цель полосы (шаги
; загрузчика, передача ELF); полоса идёт к ней плавно, как у DOOM.ELF. FtHandover — перед
; запуском DOOM.ELF: картинка распакована и проявилась, полоса дошла; метка FT_MARK в RAM_G —
; картинка уже там, полоса — FtShown точек. DOOM.ELF показывает ту же картинку без распаковки и
; ведёт полосу дальше. FT812 не ответил (нет ядра FT812) — экрана нет, загрузка идёт как раньше.
; SPDX-License-Identifier: GPL-2.0-or-later
        INCLUDE "ldrpic.inc"

FT_REG_HI       EQU #30                 ; регистры #3020xx, RAM_DL #30xxxx, RAM_CMD #308xxx
REG_ID_LO       EQU #2000
REG_FRAMES_LO   EQU #2004
REG_DLSWAP_LO   EQU #2054
REG_CMDB_SPACE_LO EQU #2574
REG_CMDB_WRITE_LO EQU #2578
FT_PIC          EQU #080000             ; картинка 1024×768 RGB565 (VD_LDR_PIC у DOOM.ELF)
FT_SPIN         EQU 60000               ; опросов сопроцессора без места в кольце (~4 с)
FONT_WAIT       EQU 300                 ; ×~10 мс: ждать шрифты ПЗУ не дольше ~3 с

; как в main/loader_dl.c (точки экрана)
BAR_X0          EQU 128
BAR_X1          EQU 896
BAR_Y0          EQU 684
BAR_Y1          EQU 706
TEXT_Y          EQU 646
FOOT_Y          EQU 718
FILL_X0         EQU BAR_X0 + 3
FILL_W          EQU BAR_X1 - BAR_X0 - 6 ; 762: полная полоса
FADE_FR         EQU 41                  ; проявление картинки: 700 мс в кадрах FT812 (59 Гц)
HL_FR           EQU 94                  ; блик: проход 1,6 с
GL_FR           EQU 71                  ; свечение: период 1,2 с

; доли полосы у загрузчика (точек из FILL_W); дальше её ведёт DOOM.ELF
FT_AT_SCREEN    EQU 6                   ; FT812 и картинка в пути
FT_AT_MOUNT     EQU 12
FT_AT_GAME      EQU 20                  ; каталог игры найден
FT_AT_QUERY     EQU 28                  ; ESP ответил, нужен ли ELF
FT_ELF_W        EQU 200                 ; передача DOOM.ELF
FT_AT_COMMIT    EQU FT_AT_QUERY + FT_ELF_W + 4
FT_AT_START     EQU 6                   ; + к полосе перед запуском DOOM.ELF

        MACRO DL_V x, y                 ; VERTEX2F в точках (VERTEX_FORMAT 4)
        dd #40000000 | ((((x) * 16) & #7FFF) << 15) | (((y) * 16) & #7FFF)
        ENDM

; --- SPI FT812 ----------------------------------------------------------------------------
FtOn:
        ld a,#02                        ; как BootTrace: шина — Evo, затем выбор FT812
        out (#77),a
        ld a,#07
        out (#77),a
        ret
FtOff:
        ld a,#03
        out (#77),a
        ret

; Команда хоста: B — команда, C — параметр.
FtCmd:
        call FtOn
        ld a,b
        out (#57),a
        ld a,c
        out (#57),a
        xor a
        out (#57),a
        jr FtOff

; Запись: A — разряды 21…16 адреса, DE — 15…0, HL — откуда, BC — байтов (не 0).
; Выход: HL — за записанным. Портит AF, BC, DE.
FtWrite:
        push af
        call FtOn
        pop af
        or #80
        out (#57),a
        ld a,d
        out (#57),a
        ld a,e
        out (#57),a
        ld a,c
        ld d,b
        ld c,#57
        or a
        jr z,.blocks
        ld b,a
        otir                            ; остаток (BC mod 256)
.blocks:
        ld a,d
        or a
        jr z,.done
.block:
        ld b,0
        otir                            ; по 256
        dec d
        jr nz,.block
.done:
        jr FtOff

; Чтение: A — разряды 21…16, DE — 15…0, HL — куда, B — байтов (1…255).
FtRead:
        push bc
        push af
        call FtOn
        pop af
        and #3F
        out (#57),a
        ld a,d
        out (#57),a
        ld a,e
        out (#57),a
        xor a
        out (#57),a                     ; пустой байт после адреса
        in a,(#57)                      ; холостое чтение: начинает обмен первого байта
        pop bc
        ld c,#57
        inir
        jr FtOff

; Слово по адресу A:DE → FtBuf.
FtRd32:
        ld hl,FtBuf
        ld b,4
        jr FtRead

; Регистр #30:DE ← HL (32 бита, старшие — 0).
FtWrReg:
        ld (FtBuf),hl
        ld hl,0
        ld (FtBuf+2),hl
        ld a,FT_REG_HI
        ld hl,FtBuf
        ld bc,4
        jr FtWrite

; ~26 тактов на BC.
FtDelay:
        dec bc
        ld a,b
        or c
        jr nz,FtDelay
        ret

; --- арифметика ----------------------------------------------------------------------------
; HL ← DE · A.
FtMul8:
        ld hl,0
        ld b,8
.bit:
        add hl,hl
        rla
        jr nc,.next
        add hl,de
.next:
        djnz .bit
        ret

; HL ← HL / DE, DE ← остаток (DE ≠ 0).
FtDiv:
        ld b,16
        ld a,h
        ld c,l
        ld hl,0
.bit:
        sla c
        rla
        adc hl,hl
        sbc hl,de
        jr nc,.one
        add hl,de
        jr .next
.one:
        inc c
.next:
        djnz .bit
        ex de,hl                        ; DE — остаток
        ld h,a
        ld l,c
        ret

; --- запуск экрана --------------------------------------------------------------------------
FtScreen_Start:
        xor a
        ld (FtOk),a
        ld (FtLive),a
        ld (FtPic),a
        ld (FtPicSent),a
        ld (FtFontOk),a
        ld hl,0
        ld (FtFill),hl
        ld (FtShown),hl
        ld (FtLastDraw),hl
        ld (FtFooter),hl
        ; кадр — от FT812 (M_SEL): ядро FT812 при M_SEL = 0 вернулось бы в слот TS-Config
        ld bc,#00AF
        ld a,4
        out (c),a
        ld bc,#6800                     ; CORERST
        call FtCmd
        ld bc,#4400                     ; CLKEXT
        call FtCmd
        ld bc,#4100                     ; SLEEP
        call FtCmd
        ld bc,#61C8                     ; CLKSEL ×8: 64 МГц
        call FtCmd
        ld bc,#0000                     ; ACTIVE
        call FtCmd
        ; REG_ID = #7C: ядро FT812 отвечает (переход в слот FT812 — ~0,2 с); ждать до ~3 с
        ld hl,300
.id:
        push hl
        ld bc,5000                      ; ~10 мс
        call FtDelay
        ld a,FT_REG_HI
        ld de,REG_ID_LO
        call FtRd32
        pop hl
        ld a,(FtBuf)
        cp #7C
        jr z,.found
        dec hl
        ld a,h
        or l
        jr nz,.id
        ret                             ; FT812 нет — без экрана
.found:
        ld hl,FtModeTab
.mode:
        ld e,(hl)
        inc hl
        ld d,(hl)
        inc hl
        ld a,d
        or e
        jr z,.mode_end
        ld c,(hl)
        inc hl
        ld b,(hl)
        inc hl
        push hl
        ld h,b
        ld l,c
        call FtWrReg
        pop hl
        jr .mode
.mode_end:
        ; пока экран не готов — чёрный лист (иначе на экране мусор из RAM_DL, как у R-Type)
        ld hl,FtDl
        ld (FtDlPtr),hl
        ld hl,FtTplHead
        ld b,3
        call FtPutN
        ld hl,FtTplTail + 4             ; DISPLAY
        ld b,1
        call FtPutN
        call FtSendDl
        ld a,1
        ld (FtOk),a
        ; шрифты ПЗУ — до картинки (см. начало файла), не дольше ~3 с
        ld hl,FONT_WAIT
.fonts:
        ld a,(FtFontOk)
        or a
        jr nz,.pic
        push hl
        call FtTick
        ld bc,5000
        call FtDelay
        pop hl
        dec hl
        ld a,h
        or l
        jr nz,.fonts
.pic:
        call FtPicLoad
        ; распаковка — до конца (~1 с, ~5 с самое большее): первый видимый кадр — сразу весь
        ; экран: картинка (без проявления), полоса, строки
        ld hl,500
.unpack:
        ld a,(FtPicSent)
        or a
        jr z,.live                      ; картинка не ушла — полоса на чёрном
        ld a,(FtPic)
        or a
        jr nz,.live
        push hl
        call FtTick
        ld bc,5000
        call FtDelay
        pop hl
        dec hl
        ld a,h
        or l
        jr nz,.unpack
.live:
        ld hl,FT_AT_SCREEN
        ld (FtFill),hl
        ld (FtShown),hl
        call FtGetNow
        ld de,FADE_FR
        or a
        sbc hl,de
        ld (FtPicF0),hl                 ; яркость — сразу полная
        ld a,1
        ld (FtLive),a
        jp FtDraw

; 1024×768@59, как у ESP (ft_host::boot1024x768): регистр (младшие 16 бит), значение
FtModeTab:
        dw #2070,0                      ; REG_PCLK
        dw #2038,24                     ; REG_HSYNC0
        dw #203C,24+136                 ; REG_HSYNC1
        dw #2030,24+136+160             ; REG_HOFFSET
        dw #2034,1024                   ; REG_HSIZE
        dw #202C,1344                   ; REG_HCYCLE
        dw #204C,2                      ; REG_VSYNC0
        dw #2050,8                      ; REG_VSYNC1
        dw #2044,37                     ; REG_VOFFSET
        dw #2048,768                    ; REG_VSIZE
        dw #2040,806                    ; REG_VCYCLE
        dw #206C,0                      ; REG_PCLK_POL
        dw #2068,0                      ; REG_CSPREAD
        dw #2070,1                      ; REG_PCLK
        dw 0

; --- шрифты ПЗУ: ширины символов 26, 27, 29 (как load_fonts у DOOM.ELF) ---------------------
FtLoadFonts:
        ld a,#2F                        ; ROM_FONTROOT #2FFFFC
        ld de,#FFFC
        call FtRd32
        ; корень: #200000 ≤ корень, кратен 4, корень + 148·16 ≤ #2FFFFC
        ld a,(FtBuf+3)
        or a
        ret nz
        ld a,(FtBuf)
        and 3
        ret nz
        ld a,(FtBuf+2)
        cp #20
        ret c
        cp #30
        ret nc
        cp #2F
        jr nz,.root_ok
        ld hl,(FtBuf)
        ld de,#F6BD                     ; #2FFFFC − 148·16 + 1
        or a
        sbc hl,de
        ret nc
.root_ok:
        ld hl,(FtBuf)
        ld (FtRoot),hl
        ld a,(FtBuf+2)
        ld (FtRoot+2),a
        ld ix,FtFontTab
        ld hl,FtW
        ld b,3
.font:
        push bc
        push hl
        ld l,(ix+0)                     ; 148·(шрифт − 16)
        ld h,(ix+1)
        ld de,(FtRoot)
        add hl,de
        ld a,(FtRoot+2)
        adc a,0
        ex de,hl                        ; A:DE — блок шрифта
        pop hl
        push hl
        push af
        push de
        ld b,128                        ; ширины 0…127
        call FtRead
        pop de
        pop af
        ld hl,140                       ; слово 35 — высота строки
        add hl,de
        adc a,0
        ex de,hl
        call FtRd32
        pop hl
        pop bc
        ld a,(FtBuf+1)
        or a
        ret nz
        ld a,(FtBuf+2)
        or a
        ret nz
        ld a,(FtBuf+3)
        or a
        ret nz
        ld a,(FtBuf)
        or a
        ret z
        cp 65
        ret nc
        ld de,128
        add hl,de
        inc ix
        inc ix
        djnz .font
        ld a,1
        ld (FtFontOk),a
        ret
FtFontTab:
        dw 148 * (26 - 16), 148 * (27 - 16), 148 * (29 - 16)

; --- картинка: CMD_LOADIMAGE через REG_CMDB_WRITE --------------------------------------------
; Поток — в REG_CMDB_WRITE (ядро кладёт каждые 4 байта словом в кольцо RAM_CMD и двигает
; REG_CMD_WRITE сразу на 4), место — REG_CMDB_SPACE. Не REG_CMD_WRITE напрямую: его хост пишет
; по байту, и сопроцессор видел бы промежуточный указатель (младший байт уже новый, старший ещё
; старый) — уходил вперёд и брал старые байты кольца за JPEG (на плате 0.1.5: картинка
; «покоцанная» — блоки сдвинуты; у DOOM.ELF указатель ESP пишет словом через окно).
FtPicLoad:
        ld hl,FtPicHead
        ld de,12
        call FtCmdbPut
        ret c
        ld a,LDRPIC_PAGE0
        ld (FtPage),a
        ld a,LDRPIC_PAGES
        ld (FtPages),a
.page:
        ld a,(FtPage)
        ld bc,#13AF
        out (c),a
        ld hl,#C000
        ld de,16384
        ld a,(FtPages)
        dec a
        jr nz,.full
        ld de,LDRPIC_LAST
.full:
        call FtCmdbPut
        jr c,.out
        ld hl,FtPage
        inc (hl)
        ld hl,FtPages
        dec (hl)
        jr nz,.page
        ld a,1
        ld (FtPicSent),a
.out:
        ld bc,#13AF                     ; окно #C000 — снова буфер загрузчика
        ld a,#20
        out (c),a
        ret

; HL ← REG_CMDB_SPACE (свободно байтов в кольце, кратно 4; #FFC — кольцо пусто).
FtSpace:
        ld a,FT_REG_HI
        ld de,REG_CMDB_SPACE_LO
        call FtRd32
        ld hl,(FtBuf)
        ld a,l
        and #FC
        ld l,a
        ld a,h
        and #0F
        ld h,a
        ret

; В кольцо сопроцессора через REG_CMDB_WRITE: HL — откуда, DE — байтов (кратно 4). Ждёт места
; (кадры экрана идут). CF — сопроцессор не берёт (~4 с без места).
FtCmdbPut:
        ld (FtSrc),hl
        ld (FtLeft),de
        ld hl,FT_SPIN
        ld (FtSpin),hl
.loop:
        ld hl,(FtLeft)
        ld a,h
        or l
        ret z
        call FtSpace
        ld a,h
        or l
        jr nz,.room
        call FtTick
        ld hl,(FtSpin)
        dec hl
        ld (FtSpin),hl
        ld a,h
        or l
        jr nz,.loop
        scf
        ret
.room:
        ld b,h
        ld c,l
        ld hl,(FtLeft)
        call .min
        push bc
        ld hl,(FtSrc)
        ld a,FT_REG_HI
        ld de,REG_CMDB_WRITE_LO
        call FtWrite
        ld (FtSrc),hl
        pop bc
        ld hl,(FtLeft)
        or a
        sbc hl,bc
        ld (FtLeft),hl
        ld hl,FT_SPIN
        ld (FtSpin),hl
        call FtTick
        jp .loop
.min:                                   ; BC ← min(BC, HL)
        push hl
        or a
        sbc hl,bc
        pop hl
        ret nc
        ld b,h
        ld c,l
        ret

; --- полоса и кадры экрана -------------------------------------------------------------------
; HL — цель полосы, точек (до FILL_W); меньше прежней — не трогает.
FtProgress:
        ld de,FILL_W + 1
        or a
        sbc hl,de
        add hl,de
        jr c,.ok
        ld hl,FILL_W
.ok:
        ld de,(FtFill)
        or a
        sbc hl,de
        add hl,de
        ret c
        ld (FtFill),hl
        jr FtTick

; FtNow ← REG_FRAMES (младшие 16 бит).
FtGetNow:
        ld a,FT_REG_HI
        ld de,REG_FRAMES_LO
        call FtRd32
        ld hl,(FtBuf)
        ld (FtNow),hl
        ret

; Кадр экрана, если с прошлого прошло FtGap кадров FT812 и больше. Портит всё.
FtTick:
        ld a,(FtOk)
        or a
        ret z
        call FtGetNow
        ld de,(FtLastDraw)
        or a
        sbc hl,de
        ld a,h
        or a
        jr nz,FtDraw
        ld a,(FtGap)
        ld b,a
        ld a,l
        cp b
        ret c
        ; FtDraw

; Кадр экрана на FtNow: шрифты, картинка распакована ли, полоса к цели, лист и DLSWAP.
FtDraw:
        ld a,(FtFontOk)
        or a
        call z,FtLoadFonts
        ld a,(FtPic)
        or a
        jr nz,.smooth
        ld a,(FtPicSent)
        or a
        jr z,.smooth
        call FtSpace                    ; кольцо пусто — JPEG разобран
        ld de,#0FFC
        or a
        sbc hl,de
        jr nz,.smooth
        ld a,1
        ld (FtPic),a
        ld hl,(FtNow)
        ld (FtPicF0),hl
.smooth:                                ; к цели — на (разница + 7) / 8, как у DOOM.ELF
        ld a,(FtLive)
        or a
        ret z                           ; экран ещё не готов — лист не трогать
        ld hl,(FtFill)
        ld de,(FtShown)
        or a
        sbc hl,de
        jr c,.dl
        jr z,.dl
        ld de,7
        add hl,de
        srl h
        rr l
        srl h
        rr l
        srl h
        rr l
        ld de,(FtShown)
        add hl,de
        ld (FtShown),hl
.dl:
        ld a,FT_REG_HI                  ; смена листа ещё ждёт кадра — этот пропустить
        ld de,REG_DLSWAP_LO
        call FtRd32
        ld a,(FtBuf)
        and 3
        ret nz
        ld hl,(FtNow)
        ld (FtLastDraw),hl
        call FtBuildDl
        ; FtSendDl

; Лист из FtDl (до FtDlPtr) — в RAM_DL, REG_DLSWAP ← FRAME.
FtSendDl:
        ld hl,(FtDlPtr)
        ld de,FtDl
        or a
        sbc hl,de
        ld b,h
        ld c,l
        ld hl,FtDl
        ld a,FT_REG_HI                  ; RAM_DL #300000
        ld de,#0000
        call FtWrite
        ld hl,2                         ; REG_DLSWAP ← FRAME
        ld de,REG_DLSWAP_LO
        call FtWrReg
        ; FT_SEEN (LINK #50) снова — след этапа загрузчика, а не этот лист: ESP видит этап
        jp BootTrace

; Лист кадра — как vd_loader_dl (main/loader_dl.c): FtShown — заливка, FtNow — анимация.
FtBuildDl:
        ld hl,FtDl
        ld (FtDlPtr),hl
        ld hl,FtTplHead
        ld b,3
        call FtPutN
        ld a,(FtPic)
        or a
        jr z,.bar
        ; яркость картинки: проявление за FADE_FR кадров
        ld hl,(FtNow)
        ld de,(FtPicF0)
        or a
        sbc hl,de
        ld de,FADE_FR
        or a
        sbc hl,de
        add hl,de
        ld a,255
        jr nc,.bright
        ex de,hl
        ld a,255
        call FtMul8
        ld de,FADE_FR
        call FtDiv
        ld a,l
.bright:
        ld hl,(FtDlPtr)                 ; COLOR_RGB(b, b, b)
        ld (hl),a
        inc hl
        ld (hl),a
        inc hl
        ld (hl),a
        inc hl
        ld (hl),#04
        inc hl
        ld (FtDlPtr),hl
        ld hl,FtTplPic
        ld b,9
        call FtPutN
.bar:
        ld hl,FtTplBar
        ld b,11
        call FtPutN
        ld hl,(FtShown)
        ld a,h
        or a
        jr nz,.fill
        ld a,l
        cp 2
        jr c,.rects_end
.fill:
        call FtPutFill
        call FtPutGleam
.rects_end:
        ld hl,FtTplEnd
        ld b,1
        call FtPutN
        ld hl,(FtShown)                 ; свечение — у неполной заливки
        ld a,h
        or a
        jr nz,.glow
        ld a,l
        cp 2
        jr c,.texts
.glow:
        ld de,FILL_W
        or a
        sbc hl,de
        call c,FtPutGlow
.texts:
        ld hl,FtTplBmp
        ld b,1
        call FtPutN
        ld a,(FtFontOk)
        or a
        jr z,.tail
        ld a,(FtMenu)
        or a
        jr nz,.menu
        call FtPutTexts
        jr .tail
.menu:
        call FtPutMenu
.tail:
        ld hl,FtTplTail
        ld b,2
        jp FtPutN

; B слов листа из HL — в FtDlPtr.
FtPutN:
        push bc
        ld a,b
        add a,a
        add a,a
        ld c,a
        ld b,0
        ld de,(FtDlPtr)
        ldir
        ld (FtDlPtr),de
        pop bc
        ret

; Слово листа: DE — старшие 16 бит, HL — младшие.
FtPutW:
        push hl
        ld hl,(FtDlPtr)
        pop bc
        ld (hl),c
        inc hl
        ld (hl),b
        inc hl
        ld (hl),e
        inc hl
        ld (hl),d
        inc hl
        ld (FtDlPtr),hl
        ret

; VERTEX2F: HL — x, DE — y (точки).
FtPutV:
        push hl
        ex de,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl                       ; HL = y·16 — младшие 16 бит слова
        pop de
        ex de,hl
        add hl,hl
        add hl,hl
        add hl,hl
        ld a,h
        or #40
        ld h,a                          ; #4000 | x·8 — старшие (x·16 << 15)
        ex de,hl
        jr FtPutW

; Заливка: тёмно-красная, сверху — оранжевый свет и тонкий блеск.
FtPutFill:
        ld hl,(FtShown)
        ld de,FILL_X0
        add hl,de
        ld (FtX1),hl
        ld hl,FtTplFillA
        ld b,1
        call FtPutN
        ld hl,FILL_X0
        ld de,BAR_Y0 + 3
        call FtPutV
        ld hl,(FtX1)
        ld de,BAR_Y1 - 3
        call FtPutV
        ld hl,FtTplFillB
        ld b,3
        call FtPutN
        ld hl,FILL_X0 + 1
        ld de,BAR_Y0 + 4
        call FtPutV
        ld hl,(FtX1)
        dec hl
        ld de,(BAR_Y0 + BAR_Y1) / 2
        call FtPutV
        ld hl,FtTplFillC
        ld b,2
        call FtPutN
        ld hl,FILL_X0 + 2
        ld de,BAR_Y0 + 5
        call FtPutV
        ld hl,(FtX1)
        dec hl
        dec hl
        ld de,BAR_Y0 + 6
        jp FtPutV

; Блик бежит по залитой части (ножницы), проход — HL_FR кадров:
; bx = FILL_X0 − 90 + (заливка + 90) · фаза / HL_FR.
FtPutGleam:
        ld hl,(FtNow)
        ld de,HL_FR
        call FtDiv
        ld a,e                          ; фаза 0…HL_FR−1
        push af
        ld hl,(FtShown)
        ld de,90
        add hl,de
        srl h
        rr l                            ; (заливка + 90) / 2
        ex de,hl
        pop af
        call FtMul8
        ld de,HL_FR / 2
        call FtDiv
        ld de,FILL_X0 - 90
        add hl,de
        ld (FtBx),hl
        ld hl,FtTplGleamA
        ld b,1
        call FtPutN                     ; SCISSOR_XY
        ld hl,(FtShown)                 ; SCISSOR_SIZE(заливка, 16)
        ld a,l
        and #0F
        rlca
        rlca
        rlca
        rlca
        ld b,a
        srl h
        rr l
        srl h
        rr l
        srl h
        rr l
        srl h
        rr l
        ld a,h
        or #1C
        ld d,a
        ld e,l
        ld h,b
        ld l,BAR_Y1 - BAR_Y0 - 6
        call FtPutW
        ld hl,FtTplGleamB
        ld b,3
        call FtPutN
        ld hl,(FtBx)
        ld de,6
        add hl,de
        ld de,BAR_Y0 + 9
        call FtPutV
        ld hl,(FtBx)
        ld de,90 - 6
        add hl,de
        ld de,BAR_Y1 - 9
        call FtPutV
        ld hl,FtTplGleamC
        ld b,2
        jp FtPutN

; Свечение у края заливки — дышит: a = 60 + tri · 70 / 255.
FtPutGlow:
        ld hl,(FtNow)
        ld de,GL_FR
        call FtDiv
        ex de,hl                        ; HL — фаза 0…GL_FR−1
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl                       ; ·512
        ld de,GL_FR
        call FtDiv                      ; k = 0…511
        ld a,h
        or a
        jr z,.up
        ex de,hl
        ld hl,511
        or a
        sbc hl,de
.up:
        ex de,hl                        ; DE — tri
        ld a,70
        call FtMul8
        ld de,255
        call FtDiv
        ld a,l
        add a,60
        ld (FtGlowA),a
        ld hl,FtTplGlowA
        ld b,2
        call FtPutN                     ; BEGIN POINTS, COLOR_RGB 255, 140, 40
        ld a,(FtGlowA)
        call .alpha
        ld hl,FtTplGlowB
        ld b,1
        call FtPutN                     ; POINT_SIZE 16
        call .vertex
        ld a,(FtGlowA)
        add a,60
        call .alpha
        ld hl,FtTplGlowC
        ld b,1
        call FtPutN                     ; POINT_SIZE 7
        call .vertex
        ld hl,FtTplEnd
        ld b,1
        jp FtPutN
.alpha:                                 ; COLOR_A(A)
        ld l,a
        ld h,0
        ld de,#1000
        jp FtPutW
.vertex:
        ld hl,(FtShown)
        ld de,FILL_X0
        add hl,de
        ld de,(BAR_Y0 + BAR_Y1) / 2
        jp FtPutV

; Меню выбора игры (wadmenu.asm) вместо строк этапа и процентов: пункты «n НАЗВАНИЕ» в строку
; над полосой шрифтом 27, выделенный — цветом процентов; под полосой — подсказка шрифтом 26.
MENU_GAP        EQU 40                  ; точек между пунктами
FtPutMenu:
        ld hl,BAR_X0
        ld (FtMenuX),hl
        xor a
        ld (FtMenuI),a
.item:
        ld a,(FtMenuI)
        ld hl,MenuCount
        cp (hl)
        jr nc,.hint
        ld e,a
        ld d,0
        ld hl,MenuList
        add hl,de
        ld a,(hl)
        call MenuEntry
        inc hl
        inc hl
        ld e,(hl)
        inc hl
        ld d,(hl)                       ; DE — подпись
        ld hl,FtMenuStr
        ld a,(FtMenuI)
        add a,'1'
        ld (hl),a
        inc hl
        ld (hl),' '
        inc hl
        ex de,hl
.copy:
        ld a,(hl)
        ld (de),a
        inc hl
        inc de
        or a
        jr nz,.copy
        ld hl,FtMenuStr
        ld (FtTStr),hl
        ld bc,FtRgbStatus
        ld a,(FtMenuI)
        ld hl,MenuSel
        cp (hl)
        jr nz,.color
        ld bc,FtRgbPct
.color:
        ld a,1
        ld hl,(FtMenuX)
        ld de,TEXT_Y
        call FtTextSh
        ld a,1
        call FtTextW
        ld de,(FtMenuX)
        add hl,de
        ld de,MENU_GAP
        add hl,de
        ld (FtMenuX),hl
        ld hl,FtMenuI
        inc (hl)
        jr .item
.hint:
        ld hl,FtMenuHint
        ld (FtTStr),hl
        xor a
        ld hl,BAR_X0
        ld de,FOOT_Y
        ld bc,FtRgbFoot
        jp FtTextSh
FtMenuHint:
        db "Choose the game: number or arrows, Enter to start",0

; Строки: этап (слева), проценты (справа), WAD (под полосой) — с тенью.
FtPutTexts:
        ld hl,FtStatus
        ld (FtTStr),hl
        ld a,1                          ; шрифт 27
        ld hl,BAR_X0
        ld de,TEXT_Y
        ld bc,FtRgbStatus
        call FtTextSh
        ; проценты: p = заливка · 100 / FILL_W (как pm / 10 у DOOM.ELF)
        ld de,(FtShown)
        ld a,50
        call FtMul8
        ld de,FILL_W / 2
        call FtDiv
        ld a,l
        ld hl,FtPct
        cp 100
        jr c,.tens
        ld (hl),'1'
        inc hl
        sub 100
        ld b,a
        ld a,'0'
        jr .tens_digit
.tens:
        cp 10
        jr c,.units
        ld b,a
.tens_digit:
        ld c,0
.div10:
        ld a,b
        cp 10
        jr c,.d10
        sub 10
        ld b,a
        inc c
        jr .div10
.d10:
        ld a,c
        add a,'0'
        ld (hl),a
        inc hl
        ld a,b
.units:
        add a,'0'
        ld (hl),a
        inc hl
        ld (hl),'%'
        inc hl
        ld (hl),0
        ld hl,FtPct
        ld (FtTStr),hl
        ld a,2                          ; ширина строки шрифтом 29
        call FtTextW
        ld de,BAR_X1
        ex de,hl
        or a
        sbc hl,de
        ld a,2
        ld de,TEXT_Y - 6
        ld bc,FtRgbPct
        call FtTextSh
        ld hl,(FtFooter)
        ld a,h
        or l
        ret z
        ld (FtTStr),hl
        xor a                           ; шрифт 26
        ld hl,BAR_X0
        ld de,FOOT_Y
        ld bc,FtRgbFoot
        ; FtTextSh

; Строка с тенью (на 2 точки вправо и вниз): A — шрифт 0…2, HL — x, DE — y, BC — цвет
; (3 байта: B, G, R), FtTStr — строка.
FtTextSh:
        ld (FtTFont),a
        ld (FtTX),hl
        ld (FtTY),de
        ld (FtTRgb),bc
        ld hl,FtTplShadow
        ld b,2
        call FtPutN                     ; COLOR_RGB 0, 0, 0; COLOR_A 200
        ld hl,(FtTX)
        inc hl
        inc hl
        ld de,(FtTY)
        inc de
        inc de
        call FtText
        ld hl,(FtTRgb)                  ; COLOR_RGB цвета
        ld de,(FtDlPtr)
        ld bc,3
        ldir
        ld a,#04
        ld (de),a
        inc de
        ld (FtDlPtr),de
        ld hl,FtTplOpaque
        ld b,1
        call FtPutN                     ; COLOR_A 255
        ld hl,(FtTX)
        ld de,(FtTY)
        ; FtText

; Строка FtTStr шрифтом FtTFont с точки HL, DE: BITMAP_HANDLE, на символ — CELL и VERTEX2F.
FtText:
        ld (FtTCx),hl
        ld (FtTCy),de
        ld a,(FtTFont)
        ld e,a
        ld d,0
        ld hl,FtHandles
        add hl,de
        ld l,(hl)
        ld h,0
        ld de,#0500                     ; BITMAP_HANDLE
        call FtPutW
        ld hl,(FtTStr)
.char:
        ld a,(hl)
        and 127
        ret z
        inc hl
        push hl
        ld (FtTC),a
        cp 33
        jr c,.advance
        ld hl,(FtTCx)
        ld de,1024
        or a
        sbc hl,de
        jr nc,.advance
        ld a,(FtTC)
        ld l,a
        ld h,0
        ld de,#0600                     ; CELL
        call FtPutW
        ld hl,(FtTCx)
        ld de,(FtTCy)
        call FtPutV
.advance:
        call FtWidthOf
        ld hl,(FtTCx)
        add hl,de
        ld (FtTCx),hl
        pop hl
        jr .char

; DE ← ширина символа FtTC шрифтом FtTFont.
FtWidthOf:
        ld a,(FtTFont)
        ld h,a
        ld l,0
        srl h
        rr l                            ; шрифт · 128
        ld de,FtW
        add hl,de
        ld a,(FtTC)
        ld e,a
        ld d,0
        add hl,de
        ld e,(hl)
        ret

; HL ← ширина строки FtTStr шрифтом A.
FtTextW:
        ld (FtTFont),a
        ld hl,0
        ld (FtTCx),hl
        ld hl,(FtTStr)
.char:
        ld a,(hl)
        and 127
        jr z,.end
        ld (FtTC),a
        inc hl
        push hl
        call FtWidthOf
        ld hl,(FtTCx)
        add hl,de
        ld (FtTCx),hl
        pop hl
        jr .char
.end:
        ld hl,(FtTCx)
        ret

; Перед запуском DOOM.ELF: картинка распакована и проявилась, полоса дошла (~5 с); метка.
FtHandover:
        ld a,(FtOk)
        or a
        ret z
        ld hl,WadConfig + 4             ; WAD найден — строка под полосой, как у DOOM.ELF
        ld (FtFooter),hl
        ld hl,(FtFill)
        ld de,FT_AT_START
        add hl,de
        call FtProgress
        ld hl,500
.wait:
        push hl
        call FtTick
        ld bc,5000
        call FtDelay
        pop hl
        ld a,(FtPicSent)
        or a
        jr z,.none                      ; картинка не ушла — DOOM.ELF без метки
        ld a,(FtPic)
        or a
        jr z,.next
        push hl
        ld hl,(FtNow)
        ld de,(FtPicF0)
        or a
        sbc hl,de
        ld de,FADE_FR
        or a
        sbc hl,de
        jr c,.wait_more
        ld hl,(FtShown)
        ld de,(FtFill)
        or a
        sbc hl,de
        pop hl
        jr z,.mark
        jr .next
.wait_more:
        pop hl
.next:
        dec hl
        ld a,h
        or l
        jr nz,.wait
        ld a,(FtPic)
        or a
        jr nz,.mark
.none:
        xor a
        ld (FtOk),a
        ret
.mark:
        ld a,#0C                        ; слово картинки, где CORERST оставит CHIPID
        ld de,#0000
        ld hl,FtMark + 8
        ld b,4
        call FtRead
        ld hl,(FtShown)
        ld (FtMark + 4),hl
        ld a,#07                        ; FT_MARK = #07FFF0
        ld de,#FFF0
        ld hl,FtMark
        ld bc,16
        call FtWrite
        xor a
        ld (FtOk),a                     ; дальше экран ведёт DOOM.ELF
        ret

; Полоса за передачей DOOM.ELF: FT_AT_QUERY + FT_ELF_W · Offset / ElfSize (по 4 КБ).
FtUpload:
        ld a,(FtOk)
        or a
        ret z
        ld hl,ElfSize
        call .kb4
        ld a,d
        or e
        ret z
        push de
        ld hl,Offset
        call .kb4                       ; DE = Offset / 4096
        ld a,FT_ELF_W
        call FtMul8
        pop de
        call FtDiv
        ld de,FT_AT_QUERY
        add hl,de
        jp FtProgress
.kb4:                                   ; DE ← (HL)… (32 бита) >> 12, 16 бит
        inc hl
        ld a,(hl)                       ; байт 1
        inc hl
        ld e,(hl)                       ; байт 2
        inc hl
        ld d,(hl)                       ; байт 3
        rept 4
        srl d
        rr e
        rra
        endr
        ld d,e
        ld e,a
        ret

; --- слова листа -----------------------------------------------------------------------------
FtTplHead:
        dd #02000000                    ; CLEAR_COLOR_RGB 0, 0, 0
        dd #26000007                    ; CLEAR 1, 1, 1
        dd #27000004                    ; VERTEX_FORMAT 4
FtTplPic:                               ; (перед ним — COLOR_RGB яркости)
        dd #05000000                    ; BITMAP_HANDLE 0
        dd #01000000 | FT_PIC           ; BITMAP_SOURCE
        dd #07380100                    ; BITMAP_LAYOUT RGB565, 2048, 768 (младшие разряды)
        dd #28000009                    ; BITMAP_LAYOUT_H
        dd #08000100                    ; BITMAP_SIZE 1024, 768 (NEAREST, BORDER)
        dd #29000009                    ; BITMAP_SIZE_H
        dd #1F000001                    ; BEGIN BITMAPS
        DL_V 0, 0
        dd #21000000                    ; END
FtTplBar:
        dd #1F000009                    ; BEGIN RECTS
        dd #0E000040                    ; LINE_WIDTH 4
        dd #04781A08                    ; COLOR_RGB 120, 26, 8
        dd #100000E6                    ; COLOR_A 230
        DL_V BAR_X0, BAR_Y0
        DL_V BAR_X1, BAR_Y1
        dd #0E000020                    ; LINE_WIDTH 2
        dd #040E0605                    ; COLOR_RGB 14, 6, 5
        dd #100000FF                    ; COLOR_A 255
        DL_V BAR_X0 + 3, BAR_Y0 + 3
        DL_V BAR_X1 - 3, BAR_Y1 - 3
FtTplFillA:
        dd #04B01606                    ; COLOR_RGB 176, 22, 6
FtTplFillB:
        dd #0E000010                    ; LINE_WIDTH 1
        dd #04FF5C14                    ; COLOR_RGB 255, 92, 20
        dd #10000096                    ; COLOR_A 150
FtTplFillC:
        dd #04FFC878                    ; COLOR_RGB 255, 200, 120
        dd #100000AA                    ; COLOR_A 170
FtTplGleamA:
        dd #1B000000 | (FILL_X0 << 11) | (BAR_Y0 + 3)   ; SCISSOR_XY
FtTplGleamB:
        dd #0E000060                    ; LINE_WIDTH 6
        dd #04FFB45A                    ; COLOR_RGB 255, 180, 90
        dd #10000046                    ; COLOR_A 70
FtTplGleamC:
        dd #1B000000                    ; SCISSOR_XY 0, 0
        dd #1C800800                    ; SCISSOR_SIZE 2048, 2048
FtTplGlowA:
        dd #1F000002                    ; BEGIN POINTS
        dd #04FF8C28                    ; COLOR_RGB 255, 140, 40
FtTplGlowB:
        dd #0D000100                    ; POINT_SIZE 16
FtTplGlowC:
        dd #0D000070                    ; POINT_SIZE 7
FtTplBmp:
        dd #1F000001                    ; BEGIN BITMAPS
FtTplShadow:
        dd #04000000                    ; COLOR_RGB 0, 0, 0
        dd #100000C8                    ; COLOR_A 200
FtTplOpaque:
        dd #100000FF                    ; COLOR_A 255
FtTplTail:
FtTplEnd:
        dd #21000000                    ; END
        dd #00000000                    ; DISPLAY
FtPicHead:
        dd #FFFFFF24                    ; CMD_LOADIMAGE
        dd FT_PIC
        dd 2                            ; OPT_NODL
FtHandles:
        db 26, 27, 29
FtRgbStatus:
        db #C0, #D2, #E0                ; #E0D2C0
FtRgbPct:
        db #3C, #9A, #FF                ; #FF9A3C
FtRgbFoot:
        db #78, #82, #8C                ; #8C8278
FtStatus:
        db "Starting DOOM",0            ; первая строка этапа DOOM.ELF
; метка FT_MARK: «VDLP», точек полосы, 0, слово картинки по #0C0000, 0
FtMark:
        db "VDLP"
        dw 0,0
        ds 4
        dd 0

FtOk:      db 0                         ; FT812 ответил, экран — у загрузчика
FtLive:    db 0                         ; экран готов (картинка, шрифты): кадры рисуются
FtPic:     db 0                         ; картинка распакована
FtPicSent: db 0                         ; JPEG весь в кольце
FtFontOk:  db 0                         ; ширины шрифтов ПЗУ прочитаны
FtPage:    db 0
FtPages:   db 0
FtGlowA:   db 0
FtTFont:   db 0
FtTC:      db 0
FtFill:    dw 0                         ; цель полосы, точек
FtShown:   dw 0                         ; на экране, точек
FtNow:     dw 0                         ; REG_FRAMES
FtLastDraw: dw 0
FtGap:     db 2                         ; кадров FT812 между кадрами экрана; поиск по карте — 4
FtPicF0:   dw 0                         ; кадр, когда картинка распаковалась
FtFooter:  dw 0                         ; строка WAD (0 — нет)
FtMenu:    db 0                         ; меню выбора игры вместо строк этапа (wadmenu.asm)
FtMenuI:   db 0
FtMenuX:   dw 0
FtMenuStr: ds 20                        ; «n НАЗВАНИЕ»
FtX1:      dw 0
FtBx:      dw 0
FtSrc:     dw 0
FtLeft:    dw 0
FtSpin:    dw 0
FtDlPtr:   dw 0
FtTX:      dw 0
FtTY:      dw 0
FtTCx:     dw 0
FtTCy:     dw 0
FtTStr:    dw 0
FtTRgb:    dw 0
FtRoot:    ds 3
FtBuf:     ds 4
FtPct:     ds 6
FtW:       ds 3 * 128                   ; ширины символов шрифтов 26, 27, 29
FtDl:      ds 2048                      ; с меню выбора игры лист длиннее 256 слов
