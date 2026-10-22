"""Предпросмотр экрана загрузки: собрать tests/loader_preview.c (clang), получить листы
состояний, положить картинку (assets/loader.jpg, распакована libjpeg — эталон декодера BL616,
RGB565) в RAM_G по VD_LDR_PIC и нарисовать кадры моделью растеризатора Tang.

    python tests/loader_preview.py OUT_DIR      (FT812_ROM — образ ПЗУ FT812; CC — clang;
                                                 MODEL — fpga/vdac2/scripts/ft_render_model.py)
"""
import os
import struct
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
rom = os.environ.get("FT812_ROM", r"E:/zx/ft812_rom/ft812_rom_1E0000.bin")
cc = os.environ.get("CC", r"E:/zx/llvm/bin/clang")
model = Path(os.environ.get("MODEL", r"E:/zx/Tang Nano 20k VDAC/fpga/vdac2/scripts/ft_render_model.py"))
exe = out / "loader_preview.exe"
subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-D_CRT_SECURE_NO_WARNINGS",
                f"-I{ROOT / 'include'}", str(ROOT / "tests" / "loader_preview.c"),
                str(ROOT / "main" / "loader_dl.c"), "-o", str(exe)], check=True)
subprocess.run([str(exe), rom, str(out)], check=True)

PIC = 0x080000
ram = bytearray(0x200000)
img = Image.open(ROOT / "assets" / "loader.jpg").convert("RGB")
px = img.load()
for y in range(768):
    row = bytearray(2048)
    for x in range(1024):
        r, g, b = px[x, y]
        struct.pack_into("<H", row, 2 * x, ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
    ram[PIC + 2048 * y:PIC + 2048 * (y + 1)] = row
(out / "ramg.bin").write_bytes(bytes(ram))
env = dict(os.environ, FT812_ROM=rom)
for dl in sorted(out.glob("ldr*.dl")):
    png = dl.with_suffix(".png")
    subprocess.run([sys.executable, "-X", "utf8", str(model), str(out / "ramg.bin"), str(dl), str(png)],
                   check=True, env=env)
    print(png)
