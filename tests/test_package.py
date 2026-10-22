"""Check the SPG's actual raw blocks against the assembled source artifacts."""
from pathlib import Path
import struct,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import make_launcher_pic
# код Z80, драйвер FAT32 и страницы картинки экрана загрузки (с #40)
BLOCKS=[(2,ROOT/'build/doom_z80.bin'),(14,ROOT/'third_party/fat32/build/fat32.bin'),(15,ROOT/'third_party/fat32/build/fat32-work.bin')]
BLOCKS+=make_launcher_pic.blocks()
p=(ROOT/'dist/DOOM/_DOOM.SPG').read_bytes()
assert p[32:44]==b'SpectrumProg' and p[44]==0x10
assert struct.unpack_from('<HH',p,48)==(0x8000,0xBFF0)
assert p[58]==len(BLOCKS)
offset=1024
for i,(page,path) in enumerate(BLOCKS):
    flags,packed,target=p[256+3*i:259+3*i]
    assert target==page and flags==(128 if i==len(BLOCKS)-1 else 0) and packed<32
    n=(packed+1)*512
    data=path.read_bytes()
    assert p[offset:offset+n]==data.ljust(n,b'\0')
    offset+=n
assert offset==len(p)
print(f'PASS: SPG header and {len(BLOCKS)} raw blocks, {len(p)} bytes')
