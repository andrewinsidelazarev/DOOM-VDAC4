# Исходники

| Компонент | Источник / ревизия | Лицензия |
|---|---|---|
| doomgeneric | https://github.com/ozkl/doomgeneric — `e257a09527062bfb986b6e5e0780839b6cbd5b71` | GPL-2.0-or-later, `third_party/doomgeneric/LICENSE` |
| FAT32 Driver ZX Evolution | https://github.com/andrewinsidelazarev/FAT32-Driver-ZX-Evolution — `e72839070fcdc8eb0b6dbae2b9f6ff52b0a2a22d` | MIT, `third_party/fat32/LICENSE` |
| ABI VDAC4 и Z80 SPI/protocol | https://github.com/andrewinsidelazarev/VDAC4, снимок 19.10.2026 | MIT, `licenses/VDAC4-MIT.txt` |
| Посекторный поиск каталога FAT32 | `E:\zx\R-Type VDAC2+\Source\ASM\rtype_loader.asm`, снимок 19.10.2026; адаптирован в `launcher/directory.asm` | MIT, `licenses/R-Type-MIT.txt` |
| Картинка экрана загрузки «DOOM ZX Evolution» | `assets/loader.png` — нарисована Codex (генерация изображений) 20.10.2026 по просьбе автора порта, им же 20.10.2026 опущена надпись «ZX EVOLUTION» над полосой; `assets/loader.jpg`, `main/loader_image.c` — из неё | — |

В doomgeneric изменены `g_game.c` (проверка ошибок сохранения и замена через
FILEX без предварительного удаления слота; демо грузит уровень с предзагрузкой),
`m_config.c/.h` (чтение/запись CFG и отслеживание всех привязанных значений с отложенной записью),
`m_menu.c` (опрос изменений каждый тик, немедленная запись при уходе с Options и Sound Volume,
включая Backspace и F4), `r_main.c` (начало кадра/замер),
`r_draw.c` (колонки и строки BL616), `v_video.c` (наложение UI на кадр BL616).
Остальные платформенные адаптации лежат в `main/` и `include/compat.h`.
FAT32-драйвер используется без изменений. Копии ABI и SPI имеют исходные
пути `Firmware/modules/sdk/include/zm_app.h` и `Network/shared/z80/`.

Исходный движок: id Software / Chocolate Doom / doomgeneric; авторские сведения
сохранены в файлах. Новый код порта распространяется на условиях GPL-2.0-or-later.
Внешний загрузчик ELF из проекта VDAC4 используется как инструмент сборки и
часть уже установленной прошивки; он не копируется в исходники порта.

Игровые WAD не входят в лицензию движка и не включены в пакет программ.
Для локального интеграционного прогона использован shareware DOOM1.WAD 1.9,
SHA-256 `1d7d43be501e67d927e415e0b8f3e29c3bf33075e859721816f652a526cac771`.
