"""Decode the DOOM indexed framebuffer/palette from a physical FTREAD dump.

RAM_G is sampled while the game runs; this is not an atomic video capture.
"""
from pathlib import Path
import json,struct,sys
from PIL import Image

folder=Path(sys.argv[1])
ram=(folder/'ramg.bin').read_bytes()
words=struct.unpack('<2048I',(folder/'dl.bin').read_bytes())
source=palette=None
for w in words:
    if w>>24==1:source=w&0x3fffff
    elif w>>24==0x2a:palette=w&0x3fffff
    if w==0:break
assert source is not None and palette is not None
assert source+64000<=len(ram) and palette+512<=len(ram)
colors=[]
for w in struct.unpack_from('<256H',ram,palette):
    colors.append(((w>>11)*255//31,((w>>5)&63)*255//63,(w&31)*255//31))
im=Image.new('RGB',(320,200))
im.putdata([colors[i] for i in ram[source:source+64000]])
im.save(folder/'framebuffer.png')
im.resize((640,480),Image.Resampling.NEAREST).save(folder/'preview.png')
print(json.dumps(dict(source=hex(source),palette=hex(palette),colors_used=len(set(im.getdata())),output=str(folder/'preview.png'))))
