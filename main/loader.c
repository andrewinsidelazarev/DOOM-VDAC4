/* Экран загрузки DOOM VDAC4: картинка «DOOM ZX Evolution» и полоса загрузки на FT812, пока
 * DOOM читает WAD через Evo (десятки секунд: R_Init — текстуры и спрайты).
 *
 * С 0.1.5 картинку и полосу с первого шага показывает загрузчик Z80 (DOOM.SPG,
 * launcher/ftscreen.asm): JPEG — в страницах SPG, его распаковал сопроцессор BL616 в RAM_G
 * (VD_LDR_PIC) ещё до запуска DOOM.ELF. Своя задача на ядре 0 (там же потом — выгрузка
 * кадров) берёт метку загрузчика (from_launcher) — картинку и полосу — и ~30 раз в секунду
 * пишет лист кадра (main/loader_dl.c) и DLSWAP; полоса идёт дальше с полосы загрузчика. Игра тем временем грузится в своей задаче: прочитанные байты WAD
 * (vd_load_bytes) против оценки по каталогу WAD (vd_load_est, app.c) — полоса, строки printf
 * DOOM («R_Init: …») — этап. Первый кадр игры (vd_video_frame) зовёт vd_loader_end: полоса — 100 %,
 * задача выходит, дальше лист пишет игра.
 * Без сопроцессора (BL616 не отвечает) — без картинки, полоса на чёрном.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "vd_port.h"
#include "vd_loader.h"
#include <stdio.h>
#include <string.h>

#define RAM_DL        0x300000u
#define REG_DLSWAP    0x302054u
#define ROM_FONTROOT  0x2FFFFCu
#define FRAME_MS      33u
#define LDR_MARK      0x07FFF0u           /* метка загрузчика Z80 (FT_MARK в ftscreen.asm) */
#define LDR_MAGIC     0x504C4456u         /* «VDLP» */
#define LDR_FILL_W    762u                /* полоса загрузчика: точек (FILL_W в ftscreen.asm) */
#define CHIPID        0x0C0000u           /* сюда ядро FT812 после CORERST кладёт CHIPID */

volatile uint32_t vd_load_bytes;
uint32_t vd_load_est;

static volatile int ldr_run, ldr_stop, ldr_stopped = 1;
static char ldr_status[64] = "Starting DOOM";
static void *ldr_mutex;

/* --- строки DOOM: этап ------------------------------------------------------------------- */
/* Строка printf: «X_Init: текст» — новый этап (без хвоста « - », точек и перевода строки);
 * точки, скобки и пробелы прогресса R_Init — не этап. */
void vd_loader_text(const char *s)
{
    if (!ldr_mutex || !s || __atomic_load_n(&ldr_stopped, __ATOMIC_ACQUIRE))
        return;                                   /* экрана нет: и мьютекс мог остаться от прошлого запуска */
    while (*s == '\n' || *s == ' ')
        s++;
    const char *colon = strstr(s, "_Init:");
    if (!colon || colon - s > 4)
        return;
    char t[64];
    unsigned n = 0;
    for (; *s && *s != '\n' && n < sizeof t - 1; s++)
        t[n++] = (*s >= 32 && *s < 127) ? *s : ' ';
    while (n && (t[n - 1] == ' ' || t[n - 1] == '-' || t[n - 1] == '.' || t[n - 1] == '['))
        n--;
    t[n] = 0;
    vd_core->mutex_lock(ldr_mutex);
    memcpy(ldr_status, t, n + 1);
    vd_core->mutex_unlock(ldr_mutex);
}

/* --- картинка: её уже распаковал загрузчик Z80 ------------------------------------------- */
/* DOOM.SPG (launcher/ftscreen.asm) с первого шага показывает картинку и полосу сам и перед
 * запуском DOOM.ELF оставляет метку LDR_MARK: «VDLP», полоса (точек из LDR_FILL_W) и слово
 * картинки по CHIPID — CORERST у ft_open положил туда CHIPID, слово надо вернуть. Метка
 * снимается: запуск без неё (старый DOOM.SPG) — полоса на чёрном. Возвращает полосу, ‰,
 * или −1 — метки нет. */
static int from_launcher(void)
{
    uint32_t m[4];
    for (unsigned i = 0; i < 4; i++)
        if (vd_core->ft_read(LDR_MARK + 4u * i, &m[i]) != ZM_OK)
            return -1;
    if (m[0] != LDR_MAGIC)
        return -1;
    static const uint32_t zero[4] = {0, 0, 0, 0};
    if (vd_core->ft_write(CHIPID, &m[2], 1) != ZM_OK || vd_core->ft_write(LDR_MARK, zero, 4) != ZM_OK)
        return -1;
    uint32_t px = m[1] & 0xFFFFu;
    if (px > LDR_FILL_W)
        px = LDR_FILL_W;
    return (int)(px * 1000u / LDR_FILL_W);
}

/* --- метрики шрифтов ПЗУ (26, 27, 29) ---------------------------------------------------- */
static uint32_t font_m[3][37];
static vd_ldr_fonts_t fonts;

static int load_fonts(void)
{
    static const uint8_t id[3] = {26, 27, 29};
    uint32_t root = 0;
    if (vd_core->ft_read(ROM_FONTROOT, &root) != ZM_OK || root < 0x200000u || (root & 3u) ||
        root + 148u * 16u > ROM_FONTROOT)
        return 0;                                 /* образа ПЗУ ещё нет */
    for (unsigned k = 0; k < 3; k++) {
        for (unsigned i = 0; i < 37; i++)
            if (vd_core->ft_read(root + 148u * (id[k] - 16u) + 4u * i, &font_m[k][i]) != ZM_OK)
                return 0;
        uint32_t h = font_m[k][35];
        if (!h || h > 64u)
            return 0;
    }
    for (unsigned k = 0; k < 3; k++) {
        fonts.w[k] = (const uint8_t *)font_m[k];  /* ширины — младшим байтом вперёд */
        fonts.h[k] = (uint8_t)font_m[k][35];
    }
    return 1;
}

/* --- задача ------------------------------------------------------------------------------- */
static void swap_wait(uint32_t ms)
{
    uint32_t t0 = vd_core->millis(), v = 1;
    while (vd_core->ft_read(REG_DLSWAP, &v) == ZM_OK && (v & 3u) && (uint32_t)(vd_core->millis() - t0) < ms)
        vd_core->delay(1);
}

static void loader_task(void *arg)
{
    (void)arg;
    static uint32_t dl[1024];
    char status[64], footer[64];
    uint32_t t0 = vd_core->millis(), shown;
    int fonts_ok = 0, final = 0;
    const int base = from_launcher();             /* ‰ полосы загрузчика Z80; −1 — картинки нет */
    const uint32_t from = base > 0 ? (uint32_t)base : 0u;
    shown = from;
    if (base < 0)
        vd_core->log("doom: no loader picture from DOOM.SPG; plain screen");
    while (__atomic_load_n(&vd_running, __ATOMIC_ACQUIRE) && ldr_run) {
        uint32_t now = vd_core->millis();
        if (!fonts_ok)
            fonts_ok = load_fonts();
        /* полоса: прочитанное против оценки, до конца — не больше 99 %; к цели — плавно; начало —
           полоса загрузчика Z80 (from) */
        uint32_t est = vd_load_est, got = vd_load_bytes, target;
        if (__atomic_load_n(&ldr_stop, __ATOMIC_ACQUIRE))
            target = 1000u;
        else if (!est)
            target = got > 200000u ? 30u : got / 7000u;              /* каталог ещё не прочитан */
        else
            target = got >= est ? 990u : (uint32_t)((uint64_t)got * 990u / est);
        if (target < 1000u)
            target = from + target * (990u - from) / 990u;
        if (target > shown)
            shown += (target - shown + 7u) / 8u;
        if (__atomic_load_n(&ldr_stop, __ATOMIC_ACQUIRE) && shown >= 1000u)
            final++;
        vd_core->mutex_lock(ldr_mutex);
        memcpy(status, ldr_status, sizeof status);
        vd_core->mutex_unlock(ldr_mutex);
        int err = __atomic_load_n(&vd_state, __ATOMIC_ACQUIRE) == 255;
        if (err) {
            memcpy(status, "Error: ", 7);
            strncpy(status + 7, vd_error, sizeof status - 8);
            status[sizeof status - 1] = 0;
        }
        snprintf(footer, sizeof footer, "%s   %u.%u of %u.%u MB", vd_wad_name,
                 (unsigned)(got / 1048576u), (unsigned)(got % 1048576u * 10u / 1048576u),
                 (unsigned)(est / 1048576u), (unsigned)(est % 1048576u * 10u / 1048576u));
        vd_ldr_view_t v = {now - t0, base >= 0 ? 255u : 0u, (uint16_t)(shown > 1000u ? 1000u : shown),
                           status, est ? footer : vd_wad_name, (uint8_t)err};
        unsigned n = vd_loader_dl(dl, sizeof dl / 4, &v, fonts_ok ? &fonts : &(vd_ldr_fonts_t){0});
        swap_wait(100);
        uint32_t two = 2;
        if (vd_core->ft_write(RAM_DL, dl, n) != ZM_OK || vd_core->ft_write(REG_DLSWAP, &two, 1) != ZM_OK)
            break;
        if (final >= 4)                           /* 100 % постояли ~0,1 с */
            break;
        uint32_t spent = vd_core->millis() - now;
        vd_core->delay(spent < FRAME_MS ? FRAME_MS - spent : 1);
    }
    swap_wait(100);
    __atomic_store_n(&ldr_stopped, 1, __ATOMIC_RELEASE);
}

void vd_loader_start(void)
{
    /* мьютекс — на каждый запуск: объекты приложения ядро снимает при его остановке */
    ldr_mutex = vd_core->mutex_create();
    if (!ldr_mutex)
        return;
    strcpy(ldr_status, "Starting DOOM");
    vd_load_est = 0;
    vd_load_bytes = 0;
    ldr_stop = 0;
    ldr_run = 1;
    __atomic_store_n(&ldr_stopped, 0, __ATOMIC_RELEASE);
    /* стек 6 КБ, как у выгрузки кадров: лист — статический, на стеке — код ядра VDAC4
       (ft_write, журнал при повторе записи) и snprintf */
    if (vd_core->task_start(loader_task, NULL, 6144, 3, 0) != ZM_OK) {
        ldr_run = 0;
        __atomic_store_n(&ldr_stopped, 1, __ATOMIC_RELEASE);
        vd_core->log("doom: loader task failed; no loading screen");
    }
}

void vd_loader_end(void)
{
    if (__atomic_load_n(&ldr_stopped, __ATOMIC_ACQUIRE))
        return;
    __atomic_store_n(&ldr_stop, 1, __ATOMIC_RELEASE);
    uint32_t t0 = vd_core->millis();
    while (!__atomic_load_n(&ldr_stopped, __ATOMIC_ACQUIRE) && (uint32_t)(vd_core->millis() - t0) < 10000u) {
        vd_checkpoint();
        vd_core->delay(5);
    }
    ldr_run = 0;
}

int vd_loader_stopped(void)
{
    return __atomic_load_n(&ldr_stopped, __ATOMIC_ACQUIRE);
}
