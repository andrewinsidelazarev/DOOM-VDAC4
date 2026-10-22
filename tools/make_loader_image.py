"""Картинка экрана загрузки DOOM VDAC4 → assets/loader.jpg.

    python tools/make_loader_image.py [assets/loader.png] [--quality Q]

Картинка (4:3; другая — обрезается по бокам или сверху и снизу до 4:3) уменьшается до 1024×768 —
экран FT812 один к одному — и сжимается в JPEG baseline 4:2:0 (его понимает CMD_LOADIMAGE
сопроцессора BL616, ft_image.c). С 0.1.5 JPEG живёт в страницах DOOM.SPG (tools/make_launcher_pic.py):
его отдаёт сопроцессору загрузчик Z80 (launcher/ftscreen.asm), в DOOM.ELF картинки больше нет.
"""
import io
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
args = [a for a in sys.argv[1:] if not a.startswith("--")]
quality = int(sys.argv[sys.argv.index("--quality") + 1]) if "--quality" in sys.argv else 88
if "--quality" in sys.argv:
    args.remove(str(quality))
src = Path(args[0]) if args else ROOT / "assets" / "loader.png"
W, H = 1024, 768

img = Image.open(src).convert("RGB")
w, h = img.size
if w * 3 > h * 4:                                   # шире 4:3 — по бокам
    cw = h * 4 // 3
    img = img.crop(((w - cw) // 2, 0, (w - cw) // 2 + cw, h))
elif w * 3 < h * 4:                                 # выше — сверху и снизу
    ch = w * 3 // 4
    img = img.crop((0, (h - ch) // 2, w, (h - ch) // 2 + ch))
img = img.resize((W, H), Image.LANCZOS)
buf = io.BytesIO()
img.save(buf, "JPEG", quality=quality, subsampling=2, optimize=False, progressive=False)
jpg = buf.getvalue()
(ROOT / "assets" / "loader.jpg").write_bytes(jpg)

print(f"assets/loader.jpg: JPEG {W}×{H}, {len(jpg)} байтов (качество {quality})")
