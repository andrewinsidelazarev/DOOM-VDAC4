"""Картинка экрана загрузки для загрузчика Z80 (launcher/ftscreen.asm): assets/loader.jpg →
страницы SPG по 16 КБ (build/ldrpic_NN.bin) и build/ldrpic.inc (страница первой, число страниц,
байтов в последней). JPEG дополняется нулями до слова: Z80 пишет его в кольцо RAM_CMD словами.

    python tools/make_launcher_pic.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE0 = 0x40          # страницы ОЗУ Evo под картинку: выше драйвера FAT32 (#0E, #0F) и буфера #20
PAGE = 16384


def main():
    jpg = (ROOT / "assets" / "loader.jpg").read_bytes()
    data = jpg + bytes(-len(jpg) % 4)
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    pages = (len(data) + PAGE - 1) // PAGE
    for k in range(pages):
        (build / f"ldrpic_{k:02d}.bin").write_bytes(data[k * PAGE:(k + 1) * PAGE])
    last = len(data) - (pages - 1) * PAGE
    (build / "ldrpic.inc").write_text(
        "; tools/make_launcher_pic.py: картинка экрана загрузки (assets/loader.jpg)\n"
        f"LDRPIC_PAGE0 EQU #{PAGE0:02X}\n"
        f"LDRPIC_PAGES EQU {pages}\n"
        f"LDRPIC_LAST EQU {last}\n"
        f"LDRPIC_BYTES EQU {len(jpg)}\n", encoding="utf-8")
    print(f"ldrpic: {len(jpg)} байт JPEG, страниц {pages} с #{PAGE0:02X}, в последней {last}")


def blocks():
    """Страницы SPG картинки: [(страница, файл)] — для tools/package.py."""
    build = ROOT / "build"
    return [(PAGE0 + k, p) for k, p in enumerate(sorted(build.glob("ldrpic_*.bin")))]


if __name__ == "__main__":
    main()
