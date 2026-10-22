/* Предпросмотр экрана загрузки на ПК: лист main/loader_dl.c для нескольких состояний; метрики
 * шрифтов ПЗУ — из образа ПЗУ FT812 (FT812_ROM, файл 0x1E0000…0x2FFFFF). Картинку в RAM_G и
 * кадр рисует tests/loader_preview.py (модель растеризатора Tang).
 *
 *   loader_preview ROM.bin OUT_DIR      → OUT_DIR/ldrN.dl
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "vd_loader.h"

#define ROM_BASE 0x1E0000u

int main(int argc, char **argv)
{
    if (argc != 3)
        return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f)
        return 1;
    static uint8_t rom[0x120000];
    size_t n = fread(rom, 1, sizeof rom, f);
    fclose(f);
    if (n != sizeof rom)
        return 1;
    uint32_t root;
    memcpy(&root, rom + (0x2FFFFCu - ROM_BASE), 4);
    static const uint8_t id[3] = {26, 27, 29};
    static uint32_t m[3][37];
    vd_ldr_fonts_t fonts;
    for (int k = 0; k < 3; k++) {
        memcpy(m[k], rom + (root - ROM_BASE) + 148u * (id[k] - 16u), 148);
        fonts.w[k] = (const uint8_t *)m[k];
        fonts.h[k] = (uint8_t)m[k][35];
    }
    struct {
        uint32_t t;
        uint8_t pic;
        uint16_t pm;
        const char *status, *footer;
        uint8_t err;
    } cases[] = {
        {100, 0, 0, "Starting DOOM", "DOOM.WAD", 0},
        {2400, 180, 120, "W_Init: Init WADfiles", "DOOM.WAD   0.3 of 2.6 MB", 0},
        {9000, 255, 470, "R_Init: Init DOOM refresh daemon", "DOOM.WAD   1.2 of 2.6 MB", 0},
        {30000, 255, 1000, "ST_Init: Init status bar", "DOOM.WAD   2.6 of 2.6 MB", 0},
        {12000, 255, 520, "Error: Evo WAD read timeout", "DOOM.WAD   1.3 of 2.6 MB", 1},
    };
    static uint32_t dl[2048];
    for (unsigned c = 0; c < sizeof cases / sizeof cases[0]; c++) {
        vd_ldr_view_t v = {cases[c].t, cases[c].pic, cases[c].pm, cases[c].status, cases[c].footer, cases[c].err};
        unsigned w = vd_loader_dl(dl, 2048, &v, &fonts);
        char path[512];
        snprintf(path, sizeof path, "%s/ldr%u.dl", argv[2], c);
        FILE *o = fopen(path, "wb");
        if (!o)
            return 1;
        fwrite(dl, 4, w, o);
        fclose(o);
        printf("%s: %u слов\n", path, w);
    }
    return 0;
}
