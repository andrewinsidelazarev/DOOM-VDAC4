"""Read DOOM's fixed display list and RAM_G over the VDAC4 FTREAD service.

The game keeps rendering; this is explicitly a non-atomic memory capture.
Unlike a general copro display-list capture, DOOM's direct 22-word list does
not require waiting for REG_DLSWAP (its pending interval is shorter than TCP).
"""
from pathlib import Path
import argparse,json,socket,sys

p=argparse.ArgumentParser()
p.add_argument('host')
p.add_argument('out',type=Path)
p.add_argument('--tools',default=r'E:\zx\Tang Nano 20k VDAC\VDAC4\ESP32 S3 Zero N8R8\Firmware\tools')
a=p.parse_args()
sys.path.insert(0,a.tools)
from tang_tool import PORT,receive_line,receive_exact

with socket.create_connection((a.host,PORT),10) as s:
    s.settimeout(15)
    banner=receive_line(s)
    assert banner.startswith('ZIFI-TANG/1 '),banner
    s.sendall(b'FTREAD\n')
    assert receive_line(s)=='READY'
    def read(addr,words):
        s.sendall(f'R {addr:X} {words}\n'.encode())
        reply=receive_line(s)
        assert reply==f'DATA {words*4}',reply
        return receive_exact(s,words*4)
    def link(reg):
        s.sendall(f'LINK {reg:X}\n'.encode())
        reply=receive_line(s).split()
        assert len(reply)==2 and reply[0]=='VALUE',reply
        return reply[1]
    info={'cp_info':link(0x30),'ft_rtime':link(0x2c),'ft_rstat':link(0x28),
          'atomic':False,'capture':'RAM_G read while DOOM continues rendering'}
    dl=read(0x300000,22)
    assert int.from_bytes(dl[16:20],'little')==0x05000000,'Not the expected DOOM display list'
    regs=read(0x302000,0x180)
    ram=b''.join(read(at,min(8192,(0x20400-at)//4)) for at in range(0,0x20400,32768))
    s.sendall(b'END\n')
a.out.mkdir(parents=True,exist_ok=True)
(a.out/'dl.bin').write_bytes(dl.ljust(8192,b'\0'))
(a.out/'regs.bin').write_bytes(regs)
(a.out/'ramg.bin').write_bytes(ram)
(a.out/'info.json').write_text(json.dumps(info,indent=2))
print(json.dumps(info))
