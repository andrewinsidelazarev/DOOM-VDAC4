/* Экран загрузки DOOM VDAC4 (main/loader.c, лист — main/loader_dl.c).
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include <stdint.h>

/* --- лист кадра (чистая функция: собирается и на ПК, tests/loader_preview.c) ------------- */
#define VD_LDR_PIC   0x080000u          /* картинка 1024×768 RGB565 в RAM_G (CMD_LOADIMAGE) */
enum { VD_LDR_FS = 0, VD_LDR_FM = 1, VD_LDR_FL = 2 };   /* шрифты ПЗУ 26, 27, 29 */
typedef struct {
    const uint8_t *w[3];                /* ширины символов 0…127 (метрики ПЗУ); NULL — нет */
    uint8_t h[3];                       /* высота строки шрифта */
} vd_ldr_fonts_t;
typedef struct {
    uint32_t t_ms;                      /* время экрана — для анимации */
    uint8_t pic;                        /* яркость картинки 0…255 (0 — её нет) */
    uint16_t permille;                  /* полоса, 0…1000 */
    const char *status;                 /* этап (строка DOOM) */
    const char *footer;                 /* WAD и прочитанное */
    uint8_t error;                      /* status — ошибка: красным */
} vd_ldr_view_t;
/* Слова листа в dl (не больше max); возвращает их число. */
unsigned vd_loader_dl(uint32_t *dl, unsigned max, const vd_ldr_view_t *v, const vd_ldr_fonts_t *f);

/* --- задача экрана (ESP) ---------------------------------------------------------------- */
void vd_loader_start(void);             /* после ft_open: картинка в BL616, экран — своя задача */
void vd_loader_end(void);               /* перед первым кадром игры: 100 %, задача выходит */
int vd_loader_stopped(void);
void vd_loader_text(const char *s);     /* строка printf DOOM: этап */
extern volatile uint32_t vd_load_bytes; /* прочитано из WAD через Evo */
extern uint32_t vd_load_est;            /* оценка того, что прочитает загрузка (0 — неизвестно) */
