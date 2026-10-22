/* Экран загрузки DOOM VDAC4: лист FT812 одного кадра. Чистая функция (без ESP и LINK) —
 * собирается и на ПК (tests/loader_preview.c: кадр рисует модель растеризатора Tang).
 *
 * 1024×768: картинка 1024×768 RGB565 из RAM_G (VD_LDR_PIC, её распаковал BL616) один к одному,
 * внизу (тёмная пятая часть картинки) — строка этапа, проценты, полоса загрузки и строка WAD.
 * Полоса: рамка и жёлоб — RECTS со скруглением (LINE_WIDTH), заливка — тёмно-красная с
 * оранжевым светом сверху, по ней бежит блик (ножницы — по залитой части), у края — свечение
 * (POINTS), оно дышит. Текст — шрифтами ПЗУ FT812 (26, 27, 29; CELL + VERTEX2F), с тенью.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include <stddef.h>
#include <stdint.h>
#include "vd_loader.h"

#define DL_DISPLAY()              0x00000000u
#define DL_BITMAP_SOURCE(a)       (0x01000000u | ((a) & 0x3FFFFFu))
#define DL_CLEAR_COLOR_RGB(r,g,b) (0x02000000u | ((uint32_t)(r) << 16) | ((uint32_t)(g) << 8) | (uint32_t)(b))
#define DL_COLOR_RGB(r,g,b)       (0x04000000u | ((uint32_t)(r) << 16) | ((uint32_t)(g) << 8) | (uint32_t)(b))
#define DL_BITMAP_HANDLE(h)       (0x05000000u | (h))
#define DL_CELL(c)                (0x06000000u | ((c) & 127u))
#define DL_BITMAP_LAYOUT(f,s,h)   (0x07000000u | ((uint32_t)(f) << 19) | (((s) & 1023u) << 9) | ((h) & 511u))
#define DL_BITMAP_SIZE(w,h)       (0x08000000u | (((w) & 511u) << 9) | ((h) & 511u))   /* NEAREST, BORDER */
#define DL_POINT_SIZE(s)          (0x0D000000u | ((s) & 0x1FFFu))
#define DL_LINE_WIDTH(w)          (0x0E000000u | ((w) & 0xFFFu))
#define DL_COLOR_A(a)             (0x10000000u | (uint32_t)(a))
#define DL_SCISSOR_XY(x,y)        (0x1B000000u | (((x) & 2047u) << 11) | ((y) & 2047u))
#define DL_SCISSOR_SIZE(w,h)      (0x1C000000u | (((w) & 4095u) << 12) | ((h) & 4095u))
#define DL_BEGIN(p)               (0x1F000000u | (p))
#define DL_END()                  0x21000000u
#define DL_CLEAR(c,s,t)           (0x26000000u | ((c) << 2) | ((s) << 1) | (t))
#define DL_VERTEX_FORMAT(f)       (0x27000000u | (f))
#define DL_BITMAP_LAYOUT_H(s,h)   (0x28000000u | ((((s) >> 10) & 3u) << 2) | (((h) >> 9) & 3u))
#define DL_BITMAP_SIZE_H(w,h)     (0x29000000u | ((((w) >> 9) & 3u) << 2) | (((h) >> 9) & 3u))
#define DL_VERTEX2F(x,y)          (0x40000000u | (((uint32_t)(x) & 0x7FFFu) << 15) | ((uint32_t)(y) & 0x7FFFu))
#define PRIM_BITMAPS 1u
#define PRIM_POINTS  2u
#define PRIM_RECTS   9u
#define FMT_RGB565   7u

/* полоса и строки (точки экрана) */
#define BAR_X0 128
#define BAR_X1 896
#define BAR_Y0 684
#define BAR_Y1 706
#define TEXT_Y 646
#define FOOT_Y 718

typedef struct {
    uint32_t *dl;
    unsigned n, max;
} out_t;

static void put(out_t *o, uint32_t w)
{
    if (o->n < o->max)
        o->dl[o->n++] = w;
}

static void rect(out_t *o, int x0, int y0, int x1, int y1)   /* точки, 1/16 — VERTEX_FORMAT 4 */
{
    put(o, DL_VERTEX2F(x0 * 16, y0 * 16));
    put(o, DL_VERTEX2F(x1 * 16, y1 * 16));
}

static int text_width(const vd_ldr_fonts_t *f, int font, const char *s)
{
    int w = 0;
    if (!f->w[font] || !s)
        return 0;
    for (; *s; s++)
        w += f->w[font][(uint8_t)*s & 127u];
    return w;
}

/* строка шрифтом ПЗУ (16 + 10 = 26 и т. д.): BITMAPS — уже начат; символ — CELL и VERTEX2F */
static void text(out_t *o, const vd_ldr_fonts_t *f, int font, int x, int y, const char *s)
{
    static const uint8_t handle[3] = {26, 27, 29};
    if (!f->w[font] || !s)
        return;
    put(o, DL_BITMAP_HANDLE(handle[font]));
    for (; *s; s++) {
        uint8_t c = (uint8_t)*s & 127u;
        if (c > 32 && x < 1024) {
            put(o, DL_CELL(c));
            put(o, DL_VERTEX2F(x * 16, y * 16));
        }
        x += f->w[font][c];
    }
}

/* строка с тенью (на 2 точки вправо и вниз) */
static void text_sh(out_t *o, const vd_ldr_fonts_t *f, int font, int x, int y, const char *s,
                    uint32_t rgb)
{
    put(o, DL_COLOR_RGB(0, 0, 0));
    put(o, DL_COLOR_A(200));
    text(o, f, font, x + 2, y + 2, s);
    put(o, 0x04000000u | rgb);
    put(o, DL_COLOR_A(255));
    text(o, f, font, x, y, s);
}

/* треугольная волна 0…255 с периодом p мс */
static unsigned tri(uint32_t t, uint32_t p)
{
    uint32_t k = t % p * 512u / p;
    return k < 256u ? k : 511u - k;
}

unsigned vd_loader_dl(uint32_t *dl, unsigned max, const vd_ldr_view_t *v, const vd_ldr_fonts_t *f)
{
    out_t o = {dl, 0, max};
    put(&o, DL_CLEAR_COLOR_RGB(0, 0, 0));
    put(&o, DL_CLEAR(1, 1, 1));
    put(&o, DL_VERTEX_FORMAT(4));
    if (v->pic) {
        put(&o, DL_COLOR_RGB(v->pic, v->pic, v->pic));
        put(&o, DL_BITMAP_HANDLE(0));
        put(&o, DL_BITMAP_SOURCE(VD_LDR_PIC));
        put(&o, DL_BITMAP_LAYOUT(FMT_RGB565, 2048u, 768u));
        put(&o, DL_BITMAP_LAYOUT_H(2048u, 768u));
        put(&o, DL_BITMAP_SIZE(1024u, 768u));
        put(&o, DL_BITMAP_SIZE_H(1024u, 768u));
        put(&o, DL_BEGIN(PRIM_BITMAPS));
        put(&o, DL_VERTEX2F(0, 0));
        put(&o, DL_END());
    }

    /* полоса: рамка, жёлоб */
    unsigned pm = v->permille > 1000u ? 1000u : v->permille;
    int fx0 = BAR_X0 + 3, fx1 = BAR_X0 + 3 + (int)((BAR_X1 - BAR_X0 - 6) * pm / 1000u);
    put(&o, DL_BEGIN(PRIM_RECTS));
    put(&o, DL_LINE_WIDTH(4 * 16));
    put(&o, DL_COLOR_RGB(120, 26, 8));
    put(&o, DL_COLOR_A(230));
    rect(&o, BAR_X0, BAR_Y0, BAR_X1, BAR_Y1);
    put(&o, DL_LINE_WIDTH(2 * 16));
    put(&o, DL_COLOR_RGB(14, 6, 5));
    put(&o, DL_COLOR_A(255));
    rect(&o, BAR_X0 + 3, BAR_Y0 + 3, BAR_X1 - 3, BAR_Y1 - 3);
    if (fx1 > fx0 + 1) {
        /* заливка: тёмно-красная, сверху — оранжевый свет и тонкий блеск */
        put(&o, DL_COLOR_RGB(176, 22, 6));
        rect(&o, fx0, BAR_Y0 + 3, fx1, BAR_Y1 - 3);
        put(&o, DL_LINE_WIDTH(1 * 16));
        put(&o, DL_COLOR_RGB(255, 92, 20));
        put(&o, DL_COLOR_A(150));
        rect(&o, fx0 + 1, BAR_Y0 + 4, fx1 - 1, (BAR_Y0 + BAR_Y1) / 2);
        put(&o, DL_COLOR_RGB(255, 200, 120));
        put(&o, DL_COLOR_A(170));
        rect(&o, fx0 + 2, BAR_Y0 + 5, fx1 - 2, BAR_Y0 + 6);
        /* блик бежит по залитой части (ножницы), 1,6 с на проход */
        int span = fx1 - fx0, bw = 90;
        int bx = fx0 - bw + (int)((uint32_t)(span + bw) * (v->t_ms % 1600u) / 1600u);
        put(&o, DL_SCISSOR_XY((uint32_t)fx0, (uint32_t)(BAR_Y0 + 3)));
        put(&o, DL_SCISSOR_SIZE((uint32_t)span, (uint32_t)(BAR_Y1 - BAR_Y0 - 6)));
        put(&o, DL_LINE_WIDTH(6 * 16));
        put(&o, DL_COLOR_RGB(255, 180, 90));
        put(&o, DL_COLOR_A(70));
        rect(&o, bx + 6, BAR_Y0 + 9, bx + bw - 6, BAR_Y1 - 9);
        put(&o, DL_SCISSOR_XY(0, 0));
        put(&o, DL_SCISSOR_SIZE(2048u, 2048u));
    }
    put(&o, DL_END());
    if (fx1 > fx0 + 1 && pm < 1000u) {
        /* свечение у края заливки — дышит */
        unsigned a = 60u + tri(v->t_ms, 1200u) * 70u / 255u;
        put(&o, DL_BEGIN(PRIM_POINTS));
        put(&o, DL_COLOR_RGB(255, 140, 40));
        put(&o, DL_COLOR_A(a));
        put(&o, DL_POINT_SIZE(16 * 16));
        put(&o, DL_VERTEX2F(fx1 * 16, (BAR_Y0 + BAR_Y1) * 8));
        put(&o, DL_COLOR_A(a + 60u));
        put(&o, DL_POINT_SIZE(7 * 16));
        put(&o, DL_VERTEX2F(fx1 * 16, (BAR_Y0 + BAR_Y1) * 8));
        put(&o, DL_END());
    }

    /* строки: этап (слева), проценты (справа), WAD (под полосой) */
    put(&o, DL_BEGIN(PRIM_BITMAPS));
    if (v->status)
        text_sh(&o, f, VD_LDR_FM, BAR_X0, TEXT_Y, v->status, v->error ? 0xFF4030u : 0xE0D2C0u);
    if (!v->error) {
        char pc[8];
        unsigned p = pm / 10u, k = 0;
        if (p >= 100u)
            pc[k++] = '1';
        if (p >= 10u)
            pc[k++] = (char)('0' + p / 10u % 10u);
        pc[k++] = (char)('0' + p % 10u);
        pc[k++] = '%';
        pc[k] = 0;
        int pw = text_width(f, VD_LDR_FL, pc);
        text_sh(&o, f, VD_LDR_FL, BAR_X1 - pw, TEXT_Y - 6, pc, 0xFF9A3Cu);
    }
    if (v->footer)
        text_sh(&o, f, VD_LDR_FS, BAR_X0, FOOT_Y, v->footer, 0x8C8278u);
    put(&o, DL_END());
    put(&o, DL_DISPLAY());
    return o.n;
}
