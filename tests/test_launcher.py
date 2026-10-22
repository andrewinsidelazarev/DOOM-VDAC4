"""Execute the assembled Z80 launcher + real FAT32 driver; mock only SD sectors
and ESP wire protocol. Also checks durable saves by rebooting the Z80 machine.
Run with the Python environment containing the `z80` package.
"""
from pathlib import Path
import itertools, os, re, struct, sys, unittest, subprocess
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'third_party/fat32/tests'))
from harness import Disk, symbols
from test_sdzc import Card,ZController
BOARD=Path(os.environ.get('VDAC4_ROOT',r'E:\zx\Tang Nano 20k VDAC\VDAC4'))
sys.path.insert(0,str(BOARD/'ESP32 S3 Zero N8R8/Network/tests'))
from test_spi_link_z80 import SpiLinkMixin, frame as wire_frame
def frame(c,p):
    # Кадр к Z80 — не больше 256 байт payload: аппаратная очередь ZiFi (NET_CHUNK у ZiFi SPG).
    # DOOM 0.1.8 отдавал данные записи одним F6 на 965 байт — на плате он терялся целиком.
    assert len(p)<=256,(hex(c),len(p))
    return wire_frame(c,p)
PIECE=248                               # данных в одном F6 (files.asm, app.c)
def pulled_blocks(esp):
    # Сколько байт забрано за каждую операцию записи (по seq), в порядке операций.
    totals={}
    for seq,at,n in esp.pulls:totals[seq]=max(totals.get(seq,0),at+n)
    return list(totals.values())
import z80

ELF=bytes((i*79+11)&255 for i in range(6511))
WAD=bytes((i*31+3)&255 for i in range(9500))
SAVE=bytes((i*29+5)&255 for i in range(1873))
INIT,READ,WRITE,SYNC=0xC100,0xC110,0xC120,0xC130

def assemble_launcher(legacy=False):
    # Собираем только Z80 из проверяемых исходников. ELF/SPG и dist здесь не создаются.
    name='doom_z80_legacy' if legacy else 'doom_z80'
    source=ROOT/'launcher/doom.asm'
    if legacy:
        text=source.read_text(encoding='utf-8').replace('INCLUDE "files.asm"',
              'INCLUDE "'+(ROOT/'tests/data/files-inline-0.1.7.asm').as_posix()+'"')
        text=text.replace('"build/doom_z80.bin"','"build/doom_z80_legacy.bin"')
        source=ROOT/'build/doom_z80_legacy.asm';source.write_text(text,encoding='utf-8')
    result=subprocess.run([os.environ.get('SJASMPLUS',r'E:\zx\z80\zuma\sjasmplus.exe'),'--nologo',
                           '--inc=launcher','--inc=build','--inc=third_party/fat32/include',
                           f'--sym=build/{name}.sym',f'--lst=build/{name}.lst',str(source)],
                          cwd=ROOT,capture_output=True)
    log=result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace')
    (ROOT/f'build/{name}-assemble.log').write_text(log,encoding='utf-8')
    assert result.returncode==0,log
    return name

def setUpModule():
    assemble_launcher()

def entry(name,cluster,size=0,directory=False):
    a,_,b=name.partition('.'); e=bytearray(32)
    e[:11]=(a.ljust(8)+b.ljust(3)).encode('ascii');e[11]=0x10 if directory else 0x20
    struct.pack_into('<H',e,20,cluster>>16);struct.pack_into('<HHHI',e,22,0x55AA,0x5B05,cluster&65535,size)
    return e

def disk_image(nested=True,wad=True,wad_stride=3,**geometry):
    disk=Disk(**({'partition':2048}|geometry))
    for c in (3,7):disk.set_fat(c,0x0FFFFFFF)
    if nested:
        disk.write(disk.data_start,entry('GAMES',3,directory=True)+bytes(480))
        disk.write(disk.data_start+disk.spc,entry('ANYNAME',7,directory=True)+bytes(480))
        folder=7
    else:folder=2
    def file_data(data,first,stride=3):
        size=512*disk.spc
        clusters=[first+stride*i for i in range((len(data)+size-1)//size)]
        for i,c in enumerate(clusters):
            disk.set_fat(c,clusters[i+1] if i+1<len(clusters) else 0x0FFFFFFF)
            for s in range(disk.spc):
                at=i*size+s*512
                disk.write(disk.data_start+(c-2)*disk.spc+s,data[at:at+512].ljust(512,b'\0'))
        return clusters[0]
    records=entry('DOOM.ELF',file_data(ELF,100),len(ELF))
    if wad:records+=entry('DOOM1.WAD',file_data(WAD,201,wad_stride),len(WAD))
    disk.write(disk.data_start+(folder-2)*disk.spc,records.ljust(512,b'\0'))
    return disk,folder

def fs(op,name='TEMP.DSG',other='',offset=0,data=b'',length=0):
    # Описание для плана модели; next_request отделяет данные от заголовка события.
    return bytes([op])+name.encode().ljust(13,b'\0')+other.encode().ljust(13,b'\0')+struct.pack('<IH',offset,len(data) if op==3 else length)+data

class Esp:
    def __init__(self,cached=False,reboot=False,fail=False,paths=None,path_set_ok=True):
        self.cached=cached;self.data=bytearray();self.committed=False;self.started=False;self.done=False
        self.requests=[];self.answers=[];self.seq=0;self.keys=0;self.fail=fail
        self.write_data=None;self.write_sent=0;self.pulls=[];self.event_lengths=[]
        # path.cfg ESP (PATH_GET/PATH_SET): словарь (имя, серийный тома) -> кластер — общий у запусков
        # одного теста, как файл во flash; None — прошивка до 0.1.83, на команды отвечает ошибкой
        self.paths=paths;self.path_set_ok=path_set_ok;self.path_gets=[];self.path_sets=[]
        self.ps2=bytearray()
        if reboot:
            self.plan=[(0xE5,fs(0,'D1SAVE0.DSG'),('stat',len(SAVE))),
                       (0xE5,fs(1,'D1SAVE0.DSG',length=960),('data',SAVE[:960])),
                       (0xE5,fs(1,'D1SAVE0.DSG',offset=960,length=len(SAVE)-960),('data',SAVE[960:]))]
        else:
            self.plan=[(0xE4,struct.pack('<IH',4093,1000),('wad',WAD[4093:5093])),
                       (0xE5,fs(0,'D1SAVE0.DSG'),('missing',None)),
                       (0xE5,fs(2),('ok',None)),
                       (0xE5,fs(3,data=SAVE[:960]),('ok',None)),
                       (0xE5,fs(3,offset=960,data=SAVE[960:]),('ok',None)),
                       (0xE5,fs(4,other='D1SAVE0.DSG'),('ok',None)),
                       (0xE4,struct.pack('<IH',512,1016),('wad',WAD[512:1528])),
                       # Replace an existing slot with the completed temp file.
                       (0xE5,fs(2),('ok',None)),
                       (0xE5,fs(3,data=SAVE[:960]),('ok',None)),
                       (0xE5,fs(3,offset=960,data=SAVE[960:]),('ok',None)),
                       (0xE5,fs(4,other='D1SAVE0.DSG'),('ok',None)),
                       (0xE5,fs(6,'D1SAVE0.DSG'),('ok',None))]
    def next_request(self):
        if not self.plan:self.done=True;return b''
        c,p,self.expected=self.plan.pop(0);self.seq+=1;self.requests.append(c)
        self.write_data=p[33:] if c==0xE5 and p[0]==3 else None
        self.write_sent=0
        if self.write_data is not None:p=p[:33]
        payload=struct.pack('<H',self.seq)+p
        assert len(payload)<=63,(hex(c),len(payload))
        self.event_lengths.append(len(payload))
        self.last_payload=payload
        return frame(c,payload)
    def __call__(self,c,p):
        if c==0x6D:return frame(0xED,b'\1')
        if c in (0x2A,0x2B):
            if self.paths is None:return frame(0xEE,b'unsupported:%02X\0'%c)
            serial=struct.unpack('<I',p[:4])[0]
            if c==0x2A:
                name=p[4:].split(b'\0')[0].decode().upper();self.path_gets.append((name,serial))
                cluster=self.paths.get((name,serial))
                return frame(0xAA,bytes([cluster is not None])+struct.pack('<I',cluster or 0))
            cluster=struct.unpack('<I',p[4:8])[0];name=p[8:].split(b'\0')[0].decode().upper()
            self.path_sets.append((name,serial,cluster))
            if self.path_set_ok:self.paths[(name,serial)]=cluster
            return frame(0xAB,bytes([self.path_set_ok]))
        if c==0x69:
            self.query=(p[9:].rstrip(b'\0'),*struct.unpack('<II',p[1:9]));return frame(0xE9,bytes([0 if self.cached else 1])+struct.pack('<I',2000000))
        if c==0x6A:
            assert struct.unpack('<I',p[:4])[0]==len(self.data)
            self.data+=p[4:];return frame(0xEA,b'\1'+struct.pack('<I',len(self.data)))
        if c==0x6B:self.committed=True;return frame(0xEB,b'\1')
        if c==0x6C:assert p==b'DOOM.ELF\0';self.started=True;return frame(0xEC,b'\0')
        if c==0x70:
            assert p==struct.pack('<I',len(WAD))+b'DOOM1.WAD\0'
            return frame(0xF0,b'\1')+self.next_request()
        if c==0x76:
            # 76 [seq, смещение] -> F6 [seq, 1, смещение, число, данные], порция до PIECE байт
            seq=p[:2] if len(p)>=2 else bytes(2)
            reject=frame(0xF6,seq+bytes(5))
            if len(p)!=4 or struct.unpack('<H',p[:2])[0]!=self.seq or self.write_data is None:return reject
            at=struct.unpack('<H',p[2:4])[0]
            if at>=len(self.write_data) or at>self.write_sent:return reject
            assert len(self.write_data)<=960
            piece=self.write_data[at:at+PIECE];self.pulls.append((self.seq,at,len(piece)))
            self.write_sent=max(self.write_sent,at+len(piece))
            return frame(0xF6,seq+b'\1'+struct.pack('<HH',at,len(piece))+piece)
        if c in (0x71,0x74):
            assert struct.unpack('<H',p[:2])[0]==self.seq
            kind,value=self.expected
            if c==0x71:assert p[2:]==struct.pack('<H',len(value))+value
            else:
                status=p[2];n=struct.unpack('<H',p[3:5])[0];size=struct.unpack('<I',p[5:9])[0]
                assert len(p)==n+9
                if kind=='missing':assert status==0x19
                elif kind=='error':assert status!=0
                elif kind=='status':assert status==value,(status,value)
                else:
                    assert status==0,(kind,status,self.seq)
                    if self.write_data is not None:assert self.write_sent==len(self.write_data),'write acknowledged without fetching data'
                    if kind=='stat':assert size==value
                    if kind=='data':assert p[9:]==value
            self.answers.append(p)
            self.write_data=None              # 74 принят: как ELF (fs_awaiting=0), данные операции больше не отдаются
            return frame(c|0x80,b'\1')+self.next_request()
        # 72: матрица, джойстик и три байта мыши Kempston (без мыши порты дают #FF).
        # 75: джойстик Kempston, кнопки и X, Y мыши (без мыши — #FF), байты очереди PS/2
        if c==0x75:
            self.keys+=1;assert 4<=len(p)<=4+24 and p[0]==0 and p[1:4]==b'\xff'*3,p.hex()
            self.ps2+=p[4:];return frame(0xF5,b'\1')
        if c==0x73:return frame(0xF3,b'\2\1'+bytes(4))
        raise AssertionError(hex(c))

class Ft812:
    """FT812 за SPI Evo (выбор 7 на #77) для экрана загрузки (launcher/ftscreen.asm): команды хоста
    (3 байта; 4F — след BootTrace, он — в boot_trace), память байтами, чтение — по адресу после
    пустого байта. Сопроцессор забирает кольцо RAM_CMD сразу (REG_CMD_READ = REG_CMD_WRITE),
    смена листа — тоже сразу: снимок листа — в dls. REG_FRAMES растёт на чтение (часы анимации);
    шрифты ПЗУ (корень #2FFFFC, ширины 26, 27, 29) появляются после fonts_after чтений корня —
    как заливка ESP после загрузки ядра; ширины — FONT_W, высота — 20."""
    FONT_ROOT=0x2F0000;FONT_W=11
    def __init__(self,machine,answer=True,fonts_after=40):
        self.m=machine;self.answer=answer;self.mem={};self.cmds=[];self.cmd_stream=bytearray()
        self.dls=[];self.cmd_write=0;self.tx=bytearray();self.marked=None;self.first_reads=None
        self.frames=0;self.root_reads=0;self.fonts_after=fonts_after;self.cmd_after_fonts=None
        self.cmd_write_direct=0
    def fonts_ready(self):return self.root_reads>self.fonts_after
    def font_byte(self,a):
        for fid in (26,27,29):
            base=self.FONT_ROOT+148*(fid-16)
            if base<=a<base+128:return self.FONT_W if a-base>=32 else 0
            if base+140<=a<base+144:return 20 if a==base+140 else 0
        return None
    def begin(self):self.tx=bytearray()
    def read(self,a):
        if a==0x302000:return 0x7C if self.answer else 0
        if 0x302004<=a<0x302008:
            if a==0x302004:self.frames+=1
            return self.frames>>8*(a-0x302004)&255
        if 0x2FFFFC<=a<0x300000:
            if a==0x2FFFFC:self.root_reads+=1
            return self.FONT_ROOT>>8*(a-0x2FFFFC)&255 if self.fonts_ready() else 0
        if self.fonts_ready() and self.font_byte(a) is not None:return self.font_byte(a)
        if 0x3020F8<=a<0x302100:return self.cmd_write>>8*(a&3)&255
        if 0x302574<=a<0x302578:return 0xFFC>>8*(a&3)&255          # REG_CMDB_SPACE: кольцо пусто
        if 0x302054<=a<0x302058:return 0
        return self.mem.get(a,0)
    def exchange(self,v):
        i=len(self.tx);self.tx.append(v)
        if i>=4 and self.tx[0]&0xC0==0:
            return self.read(((self.tx[0]&0x3F)<<16|self.tx[1]<<8|self.tx[2])+i-4)
        return 0
    def end(self):
        t=bytes(self.tx)
        if len(t)==3:
            if t[0]==0x4F:self.m.boot_trace.append(bytearray(t));return
            if self.first_reads is None:self.first_reads=self.m.reads
            self.cmds.append((t[0],t[1]));return
        if len(t)<4 or t[0]&0xC0!=0x80:return
        a=(t[0]&0x3F)<<16|t[1]<<8|t[2];data=t[3:]
        for k,x in enumerate(data):self.mem[a+k]=x
        if a==0x302578:                                              # REG_CMDB_WRITE: адрес не растёт
            if self.cmd_after_fonts is None:self.cmd_after_fonts=self.fonts_ready()
            assert len(data)%4==0,len(data)
            self.cmd_stream+=data;return
        if a==0x3020FC:
            self.cmd_write_direct+=1
            if self.cmd_after_fonts is None:self.cmd_after_fonts=self.fonts_ready()
            w=int.from_bytes(data[:4],'little')&0xFFF
            while self.cmd_write!=w:
                self.cmd_stream.append(self.mem.get(0x308000+self.cmd_write,0));self.cmd_write=(self.cmd_write+1)&0xFFF
        elif a==0x302054 and data[0]:
            dl=[]
            while len(dl)<2048:
                w=int.from_bytes(bytes(self.mem.get(0x300000+4*len(dl)+k,0) for k in range(4)),'little')
                dl.append(w)
                if w==0:break
            self.dls.append(dl)
        elif a==0x07FFF0:self.marked=(data,self.m.app.started)

def dl_fill(dl):
    """Правый край заливки полосы (точки) или None: вершина после COLOR_RGB 176, 22, 6."""
    if 0x04B01606 not in dl:return None
    return (dl[dl.index(0x04B01606)+2]>>15&0x7FFF)//16

def dl_text(dl,handle):
    """Символы строк шрифтом handle (CELL после BITMAP_HANDLE handle), по строкам."""
    lines=[];cur=None
    for w in dl:
        if w>>24==5:cur=[] if (w&31)==handle else None;lines.append(cur) if cur is not None else None
        elif w>>24==6 and cur is not None:cur.append(chr(w&127))
    return [''.join(x) for x in lines]

class Machine(SpiLinkMixin):
    PLUGIN_PAGE=None
    def __init__(self,disk,app,launcher='doom_z80'):
        self.disk=disk;self.machine=z80.Z80Machine();self.memory=self.machine.memory
        self.symbols=symbols(ROOT/f'build/{launcher}.sym')
        for addr,path in [(0x8000,f'build/{launcher}.bin'),(0x4000,'third_party/fat32/build/fat32.bin'),(0,'third_party/fat32/build/fat32-work.bin')]:
            self.machine.set_memory_block(addr,(ROOT/path).read_bytes())
        self.machine.set_memory_block(0x3900,struct.pack('<4H',INIT,READ,WRITE,SYNC))
        for pc in (INIT,READ,WRITE,SYNC,self.symbols['Halt']):self.machine.set_breakpoint(pc)
        self.machine.set_input_callback(self.port_in);self.machine.set_output_callback(self.port_out)
        self.rx=bytearray();self.tx=bytearray();self.rx_polls=0;self.esp=None;self.vconfig=[];self.border=[];self.writes=0
        self.attach_link();self.app=app;self.fail_writes=False
        self.spi_select=3;self.boot_trace=[];self.reads=0;self.read_lbas=set()
        self.path_writes=0                       # записи на карту на этапе 5 (поиск): должно быть 0
        # Клавиатура — очередь скан-кодов PS/2 AVR Evo через регистры Gluk: #EFF7 = #80 открывает их,
        # #DFF7 — номер, #BFF7 — данные; #0C бит 0 — сброс очереди, #F0 = 2 — приём, чтение #F0 —
        # байт очереди (0 — пусто). Матрица ZX (#xxFE) читаться не должна: fe_reads.
        self.gluk_open=0;self.gluk_reg=0;self.ps2_mode=0;self.ps2_queue=bytearray();self.gluk_writes=[];self.fe_reads=0
        self.ft=Ft812(self);self.ft_miso=0
        # окно #C000: страницы ОЗУ Evo (порт #13AF); картинка экрана загрузки — страницы SPG
        self.page3=0x20;self.pages={0x20:bytes(16384)}
        sys.path.insert(0,str(ROOT/'tools'));import make_launcher_pic
        for page,path in make_launcher_pic.blocks():self.pages[page]=path.read_bytes().ljust(16384,bytes(1))
    def map3(self,page):
        self.pages[self.page3]=bytes(self.memory[0xC000:0x10000])
        self.page3=page;self.machine.set_memory_block(0xC000,self.pages.get(page,bytes(16384)))
    def port_out(self,port,value):
        try:
            if port&255==0x77:
                if self.spi_select==7 and value!=7:self.ft.end()
                self.spi_select=value
                if value==7:
                    self.ft.begin()
                    super().port_out(port,3);return
            if port&255==0x57 and self.spi_select==7:
                self.ft_miso=self.ft.exchange(value);return
            if port==0x13AF:self.map3(value)
            elif port==0x00AF:self.vconfig.append(value)
            elif port==0x27AF and value==0x84:pass # diagnostic RAM -> CRAM palette
            elif port&255==0xFE:self.border.append(value&7)
            elif port==0xEFF7:self.gluk_open=value
            elif port==0xDFF7:self.gluk_reg=value
            elif port==0xBFF7:
                assert self.gluk_open==0x80,'регистры Gluk закрыты'
                self.gluk_writes.append((self.gluk_reg,value))
                if self.gluk_reg==0x0C and value&1:self.ps2_queue.clear()
                if self.gluk_reg==0xF0:self.ps2_mode=value
            else:super().port_out(port,value)
        except Exception as e:self.error=e;self.app.done=True
    def port_in(self,port):
        if port&255==0x57 and self.spi_select==7:
            v=self.ft_miso;self.ft_miso=self.ft.exchange(0xFF);return v
        # Мышь Kempston: #FADF кнопки, #FBDF X, #FFDF Y; None — мыши нет (#FF).
        mouse=getattr(self,'mouse',None)
        if mouse and port in (0xFADF,0xFBDF,0xFFDF):return mouse[(0xFADF,0xFBDF,0xFFDF).index(port)]
        if port&255==0xFE:self.fe_reads+=1
        if port==0xBFF7:
            if self.gluk_open!=0x80 or self.gluk_reg!=0xF0 or self.ps2_mode!=2 or not self.ps2_queue:return 0
            return self.ps2_queue.pop(0)
        try:return super().port_in(port)
        except Exception as e:self.error=e;self.app.done=True;return 255
    def run(self):
        m=self.machine;m.pc=self.symbols['Start']
        for _ in range(getattr(self,'step_limit',100000)):
            m.ticks_to_stop=1000000;events=m.run()
            if hasattr(self,'error'):raise self.error
            if self.app.done:return
            if not events&2:continue
            if m.pc==self.symbols['Halt']:return
            status=0
            if m.pc in (READ,WRITE):
                lba=m.de<<16|m.hl;buf=m.bc
                if m.pc==READ:
                    self.reads+=1;self.read_lbas.add(lba)
                    if (lba==getattr(self,'read_error_lba',None) and
                        self.memory[self.symbols['BootStage']]==getattr(self,'read_error_stage',self.memory[self.symbols['BootStage']])):status=0xE1
                    else:m.set_memory_block(buf,self.disk.read(lba))
                elif self.fail_writes and self.app.expected[0]=='error':status=0xE1
                elif getattr(self,'fail_path_writes',False) and self.memory[self.symbols['BootStage']]==5:status=0xE1
                else:
                    self.disk.write(lba,bytes(self.memory[buf:buf+512]))
                    if self.memory[self.symbols['BootStage']]==5:self.path_writes+=1
                    else:self.writes+=1
            elif m.pc not in (INIT,SYNC):raise AssertionError(hex(m.pc))
            m.bc,m.de,m.hl=0xA55A,0x1234,0x5678;m.a,m.f=status,64 if not status else 0
            m.pc=int.from_bytes(self.memory[m.sp:m.sp+2],'little');m.sp+=2
        raise AssertionError(('timeout',hex(m.pc),self.rx_polls))

class LauncherTest(unittest.TestCase):
    def seed_file(self,disk,name,data):
        esp=Esp(cached=True)
        temp='TEMP.CFG' if name.endswith('.CFG') else 'TEMP.DSG'
        esp.plan=([(0xE5,fs(2,temp),('ok',None))]+
                  [(0xE5,fs(3,temp,offset=at,data=data[at:at+960]),('ok',None)) for at in range(0,len(data),960)]+
                  [(0xE5,fs(6,temp),('ok',None)),(0xE5,fs(4,temp,other=name),('ok',None))])
        Machine(disk,esp).run();self.assertTrue(esp.done)

    def test_write_response_rejected_before_disk_write(self):
        # Проверяем реальный Z80: повреждённый F6 не должен вызвать WRITE_AT.
        for bad in ('seq','count','short','extra','denied','truncated','offset'):
            with self.subTest(reply=bad):
                class BadEsp(Esp):
                    def __call__(self,c,p):
                        if c!=0x76:return super().__call__(c,p)
                        at=struct.unpack('<H',p[2:4])[0];piece=self.write_data[at:at+PIECE]
                        seq=self.seq ^ (1 if bad=='seq' else 0)
                        count=len(piece)+(1 if bad=='count' else 0)
                        data=piece[:-1] if bad=='short' else piece+b'x' if bad=='extra' else piece
                        off=at+(PIECE if bad=='offset' else 0)
                        body=struct.pack('<HBHH',seq,0 if bad=='denied' else 1,off,count)+data
                        return frame(0xF6,body[:6] if bad=='truncated' else body)
                disk,folder=disk_image();old=b'A'*1373
                self.seed_file(disk,'DEFAULT.CFG',old)
                esp=BadEsp(cached=True)
                esp.plan=[(0xE5,fs(3,'DEFAULT.CFG',data=b'B'*960),('status',0x10)),
                          (0xE4,struct.pack('<IH',0,512),('wad',WAD[:512]))]
                m=Machine(disk,esp);m.run();self.assertTrue(esp.done)
                self.assertEqual(m.writes,0);self.assertEqual(disk.contents('DEFAULT.CFG',folder),old)

    def test_write_piece_lost_then_repeated(self):
        # F6 первой порции «потерялся» (ESP не ответила): Z80 после ожидания переспрашивает ту же
        # порцию, а запоздавший дубликат уже собранной порции пропускает — данные не сдвигаются.
        class LossyEsp(Esp):
            dropped=False;duplicated=False
            def __call__(self,c,p):
                if c==0x76 and not self.dropped:
                    self.dropped=True;return b''
                reply=super().__call__(c,p)
                if c==0x76 and not self.duplicated and struct.unpack('<H',p[2:4])[0]==PIECE:
                    self.duplicated=True
                    stale=self.write_data[:PIECE]
                    return frame(0xF6,p[:2]+b'\1'+struct.pack('<HH',0,len(stale))+stale)+reply
                return reply
        disk,folder=disk_image();old=b'A'*1373;self.seed_file(disk,'DEFAULT.CFG',old)
        data=bytes((i*7+1)&255 for i in range(960))
        esp=LossyEsp(cached=True);esp.plan=[(0xE5,fs(3,'DEFAULT.CFG',data=data),('ok',None))]
        m=Machine(disk,esp);m.run();self.assertTrue(esp.done)
        self.assertTrue(esp.dropped and esp.duplicated)
        self.assertEqual(disk.contents('DEFAULT.CFG',folder),data+old[960:])

    def test_repeated_write_event_refused(self):
        class ReplayedEsp(Esp):
            replay=False
            def next_request(self):
                if self.replay:
                    self.replay=False;self.expected=('status',0x10)
                    return frame(0xE5,self.last_payload)
                return super().next_request()
            def __call__(self,c,p):
                if c==0x74 and self.expected[0]=='ok':
                    self.replay=True;self.before_repeat=self.machine.writes
                return super().__call__(c,p)
        disk,folder=disk_image();old=b'A'*1373;self.seed_file(disk,'DEFAULT.CFG',old)
        esp=ReplayedEsp(cached=True);esp.plan=[(0xE5,fs(3,'DEFAULT.CFG',data=b'B'*960),('ok',None))]
        m=Machine(disk,esp);esp.machine=m;m.run()
        self.assertTrue(esp.done);self.assertEqual(pulled_blocks(esp),[960])
        self.assertEqual(m.writes,esp.before_repeat)
        self.assertEqual(disk.contents('DEFAULT.CFG',folder),b'B'*960+old[960:])

    def test_old_elf_inline_write_refused(self):
        class OldEsp(Esp):
            def next_request(self):
                if not self.plan:self.done=True;return b''
                c,p,self.expected=self.plan.pop(0);self.seq+=1
                payload=struct.pack('<H',self.seq)+p
                assert len(payload)<=63
                return frame(c,payload)
            def __call__(self,c,p):
                if c==0x76:raise AssertionError('new SPG requested data from old ELF')
                return super().__call__(c,p)
        for name in ('DEFAULT.CFG','DOOM.CFG','D1SAVE0.DSG'):
            with self.subTest(name=name):
                disk,folder=disk_image();old=b'A'*1373;self.seed_file(disk,name,old)
                esp=OldEsp(cached=True);esp.plan=[(0xE5,fs(3,name,data=b'old inline data'),('status',0x10))]
                m=Machine(disk,esp);m.run();self.assertTrue(esp.done)
                self.assertEqual(m.writes,0);self.assertEqual(disk.contents(name,folder),old)

    def test_old_spg_header_only_write_refused(self):
        legacy=assemble_launcher(legacy=True)
        for name in ('DEFAULT.CFG','DOOM.CFG','D1SAVE0.DSG'):
            with self.subTest(name=name):
                disk,folder=disk_image();old=b'A'*1373;self.seed_file(disk,name,old)
                esp=Esp(cached=True);esp.plan=[(0xE5,fs(3,name,data=b'B'*960),('status',0x10))]
                m=Machine(disk,esp,launcher=legacy);m.run();self.assertTrue(esp.done)
                self.assertEqual(esp.pulls,[]);self.assertEqual(m.writes,0)
                self.assertEqual(disk.contents(name,folder),old)

    def test_cfg_failed_write_keeps_previous_file(self):
        for name in ('DEFAULT.CFG','DOOM.CFG'):
            with self.subTest(name=name):
                disk,folder=disk_image();old=b'A'*1373;self.seed_file(disk,name,old)
                esp=Esp(cached=True)
                esp.plan=[(0xE5,fs(2,'TEMP.CFG'),('ok',None)),
                          (0xE5,fs(3,'TEMP.CFG',data=b'B'*960),('error',None))]
                m=Machine(disk,esp);m.fail_writes=True;m.run();self.assertTrue(esp.done)
                self.assertEqual(pulled_blocks(esp),[960]);self.assertEqual(disk.contents(name,folder),old)

    def test_config_roundtrip(self):
        # Настоящие ServeFile и FAT32; ESP задаёт лишь пакеты, как vd_fclose/vd_fopen.
        # Размеры по обе стороны сектора и пакета, остаток TEMP.CFG после отказа/Reset.
        for cached_path in (False,True):
            for size in (67,511,512,513,1373,2975):
                with self.subTest(path_cfg=cached_path,size=size):
                    disk,folder=disk_image()
                    paths={('DOOM',self.SERIAL):folder} if cached_path else {}
                    parents=[disk.cluster_data(c) for c in (2,3)]
                    cfg=(b'mouse_sensitivity 7\nsfx_volume 3\nmusic_volume 11\n'+b'#'*size)[:size]
                    old_cfg=b'mouse_sensitivity 1\nsfx_volume 0\nmusic_volume 0\n'+b'\n'*(size+700)
                    extra=b'vdac4_mouse_scale 9\nnovert 1\n'+b'#'*1201
                    def write(name,data):
                        return ([(0xE5,fs(2,'TEMP.CFG'),('ok',None))]+
                                [(0xE5,fs(3,'TEMP.CFG',offset=at,data=data[at:at+960]),('ok',None))
                                 for at in range(0,len(data),960)]+
                                [(0xE5,fs(6,'TEMP.CFG'),('ok',None))]+
                                ([(0xE5,fs(4,'TEMP.CFG',other=name),('ok',None))] if name!='TEMP.CFG' else []))
                    def read(name,data):
                        return ([(0xE5,fs(0,name),('stat',len(data)))]+
                                [(0xE5,fs(1,name,offset=at,length=len(data[at:at+960])),('data',data[at:at+960]))
                                 for at in range(0,len(data),960)])
                    wad=(0xE4,struct.pack('<IH',4093,1000),('wad',WAD[4093:5093]))
                    esp=Esp(paths=paths)
                    esp.plan=[wad,(0xE5,fs(0,'DEFAULT.CFG'),('missing',None))]
                    # Содержимое и длина старого файла отличаются: фиктивный успех REPLACE не пройдёт.
                    esp.plan+=write('DEFAULT.CFG',old_cfg)+read('DEFAULT.CFG',old_cfg)+[wad]
                    # Создать незавершённый временный файл, затем усечь до нуля.
                    esp.plan+=write('TEMP.CFG',b'x'*1801)
                    esp.plan+=write('DEFAULT.CFG',cfg)+write('DOOM.CFG',extra)+[wad]
                    m=Machine(disk,esp);m.run();self.assertTrue(esp.done)
                    self.assertEqual(max(esp.event_lengths),35)
                    self.assertEqual(max(pulled_blocks(esp)),960)
                    self.assertEqual(esp.path_sets,[] if cached_path else [('DOOM',self.SERIAL,folder)])
                    self.assertEqual(m.path_writes,0)
                    for name,data in [('DEFAULT.CFG',cfg),('DOOM.CFG',extra)]:
                        self.assertEqual(disk.contents(name,folder),data)
                        self.assertEqual(disk.get(name,folder)['size'],len(data))
                    self.assertNotIn('TEMP.CFG',[e['name'] for e in disk.entries(folder)])
                    self.assertEqual([disk.cluster_data(c) for c in (2,3)],parents)
                    # Новый Z80 и перемонтирование: stat/read тем же E5, что при старте движка.
                    esp=Esp(cached=True,paths=paths)
                    esp.plan=read('DEFAULT.CFG',cfg)+[wad]+read('DOOM.CFG',extra)
                    m=Machine(disk,esp);m.run();self.assertTrue(esp.done)
                    self.assertEqual(m.writes+m.path_writes,0)
                    self.assertEqual(esp.path_sets,[])

    def test_config_events_between_key_and_status_responses(self):
        # E5 приходит до/после F5 и F3, при состоянии ESP 1 (загрузка) и 2 (игра).
        # SendKeys не должен съедать событие в ожидании подтверждения.
        class TrafficEsp(Esp):
            def next_request(self):
                if not self.plan:self.done=True
                return b''
            def __call__(self,c,p):
                if c in (0x75,0x73):
                    ack=super().__call__(c,p) if c==0x75 else frame(0xF3,bytes([1+(self.seq&1),1])+bytes(4))
                    if not self.pending and ((self.seq&1)==0)==(c==0x75):
                        self.pending=True
                        req=Esp.next_request(self)
                        return req+ack if self.seq&1 else ack+req
                    return ack
                if c==0x74:self.pending=False
                result=super().__call__(c,p)
                if c in (0x70,0x74):
                    for name in ('KeyDivider','StatusDivider'):
                        self.machine.memory[self.machine.symbols[name]]=1
                return result
        disk,folder=disk_image();cfg=b'sfx_volume 2\nmusic_volume 13\n'
        esp=TrafficEsp(cached=True,paths={('DOOM',self.SERIAL):folder});esp.pending=False
        esp.plan=[(0xE5,fs(2,'TEMP.CFG'),('ok',None)),
                  (0xE5,fs(3,'TEMP.CFG',data=cfg),('ok',None)),
                  (0xE5,fs(6,'TEMP.CFG'),('ok',None)),
                  (0xE5,fs(4,'TEMP.CFG',other='DEFAULT.CFG'),('ok',None)),
                  (0xE5,fs(0,'DEFAULT.CFG'),('stat',len(cfg))),
                  (0xE5,fs(1,'DEFAULT.CFG',length=len(cfg)),('data',cfg))]
        m=Machine(disk,esp);esp.machine=m;m.run()
        self.assertTrue(esp.done);self.assertEqual(len(esp.answers),6)
        self.assertEqual(disk.contents('DEFAULT.CFG',folder),cfg)
        self.assertEqual(m.fe_reads,0)

    def test_runtime_error_survives_stop_and_wraps(self):
        for text in (b'',b'FT812 OPEN FAILED: CODE -3',b'Lowercase message\nwith a second line',b'X'*110):
            with self.subTest(text=text):
                class FailedEsp(Esp):
                    def __call__(self,c,p):
                        if c==0x70:
                            for name in ('KeyDivider','StatusDivider'):
                                self.machine.memory[self.machine.symbols[name]]=1
                            return frame(0xF0,b'\1')
                        if c==0x73:return frame(0xF3,b'\xff\1'+bytes(4)+text)
                        return super().__call__(c,p)
                disk,_=disk_image();esp=FailedEsp(cached=True);m=Machine(disk,esp);esp.machine=m;m.run()
                self.assertTrue(esp.started);self.assertEqual(m.writes,0)
                self.assertEqual(m.boot_trace[-1],b'\x4f\x0c\xff')
                pointer=int.from_bytes(m.memory[m.symbols['BootMessage']:m.symbols['BootMessage']+2],'little')
                if text:
                    self.assertEqual(pointer,m.symbols['AppErrorText'])
                    expected=bytes(x if 32<=x<=126 else 32 for x in text[:90])
                    self.assertEqual(bytes(m.memory[pointer:pointer+len(expected)+1]),expected+b'\0')
                    if text.startswith(b'FT812'):
                        (ROOT/'build/ft-open-error.scr').write_bytes(bytes(m.memory[0xC000:0xDB00]))
                    if len(text)>90:
                        for row in (0xC842,0xC862,0xC882):
                            self.assertNotEqual(bytes(m.memory[row:row+30]),bytes(30))
                else:self.assertEqual(pointer,0)

    def test_elf_lookup_io_error_is_not_file_missing(self):
        disk,folder=disk_image();esp=Esp(cached=True);m=Machine(disk,esp)
        m.read_error_lba=disk.data_start+(folder-2)*disk.spc;m.read_error_stage=6;m.run()
        self.assertFalse(esp.started);self.assertEqual(m.writes,0)
        # The driver's STREAM_IO_ERROR normalizes device failures to FF.
        self.assertEqual(m.boot_trace[-1],b'\x4f\x06\xff')
        pointer=int.from_bytes(m.memory[m.symbols['BootMessage']:m.symbols['BootMessage']+2],'little')
        self.assertEqual(pointer,m.symbols['MsgFileRead'])

    def test_fast_reads_near_end_and_backwards(self):
        data=bytes((i*17+i//512)&255 for i in range(4*1024*1024+20))
        for spc,stride in ((1,1),(8,3)):
            with self.subTest(spc=spc,stride=stride),patch(__name__+'.WAD',data):
                disk,_=disk_image(spc=spc,wad_stride=stride);esp=Esp(cached=True)
                positions=[len(data)-1016,17,3*1024*1024+511,511,65535,2*1024*1024+3]
                esp.plan=[(0xE4,struct.pack('<IH',at,1016),('wad',data[at:at+1016])) for at in positions]
                m=Machine(disk,esp);m.run()
                self.assertTrue(esp.done);self.assertEqual(m.writes,0)
                self.assertEqual(m.memory[m.symbols['FastEnabled']],1)
                count=int.from_bytes(m.memory[m.symbols['FastCount']:m.symbols['FastCount']+2],'little')
                self.assertEqual(count,1 if stride==1 else (len(data)+512*spc-1)//(512*spc))

    def test_fast_map_overflow_uses_driver(self):
        data=bytes(range(256))*4096
        with patch(__name__+'.WAD',data):
            disk,_=disk_image();esp=Esp(cached=True)
            at=len(data)-1016
            esp.plan=[(0xE4,struct.pack('<IH',at,1016),('wad',data[at:]))]
            m=Machine(disk,esp);m.run()
            self.assertTrue(esp.done);self.assertEqual(m.memory[m.symbols['FastEnabled']],0)
            self.assertEqual(m.writes,0)

    def test_fast_read_bounds_and_media_error(self):
        for at,n in ((len(WAD)-1,2),(0xffffffff,2),(len(WAD),1)):
            with self.subTest(offset=at):
                disk,_=disk_image();esp=Esp(cached=True)
                esp.plan=[(0xE4,struct.pack('<IH',at,n),('wad',b''))]
                m=Machine(disk,esp);m.run()
                self.assertTrue(esp.done);self.assertEqual(m.writes,0)
        disk,_=disk_image();esp=Esp(cached=True)
        esp.plan=[(0xE4,struct.pack('<IH',0,512),('wad',b''))]
        m=Machine(disk,esp);m.read_error_lba=disk.data_start+(201-2)*disk.spc;m.run()
        self.assertTrue(esp.done);self.assertEqual(m.writes,0)

    def test_esp_start_no_memory_is_not_file_missing(self):
        class NoMemoryEsp(Esp):
            def __call__(self,c,p):
                if c==0x6C:
                    assert p==b'DOOM.ELF\0'
                    return frame(0xEC,b'\xfe')
                return super().__call__(c,p)
        disk,_=disk_image();esp=NoMemoryEsp();m=Machine(disk,esp);m.run()
        self.assertEqual(bytes(esp.data),ELF);self.assertTrue(esp.committed)
        self.assertEqual(m.boot_trace[-1],b'\x4f\x0a\xfe')
        self.assertEqual(m.border[-1],2);self.assertEqual(m.writes,0)
        pointer=int.from_bytes(m.memory[m.symbols['BootMessage']:m.symbols['BootMessage']+2],'little')
        self.assertEqual(pointer,m.symbols['MsgEspMemory'])
        (ROOT/'build/esp-no-memory.scr').write_bytes(bytes(m.memory[0xC000:0xDB00]))

    def test_wad_read_cost_after_first_open(self):
        # Measure actual SD sector reads, not calls to the implementation's
        # FIND routine. Replay identical traffic with and without retention.
        class MeasuredEsp(Esp):
            def next_request(self):
                if self.reopen:
                    self.machine.memory[self.machine.symbols['WadSelected']]=0
                return super().next_request()
            def __call__(self,c,p):
                if c==0x71:self.samples.append(self.machine.reads)
                return super().__call__(c,p)
        counts=[]
        with patch(__name__+'.WAD',bytes(range(256))*4096):
            for reopen in (True,False):
                disk,_=disk_image();esp=MeasuredEsp(cached=True)
                esp.reopen=reopen;esp.samples=[]
                esp.plan=[(0xE4,struct.pack('<IH',i*1000,1000),('wad',WAD[i*1000:(i+1)*1000])) for i in range(12)]
                m=Machine(disk,esp);esp.machine=m;m.run()
                self.assertTrue(esp.done);self.assertEqual(m.writes,0)
                counts.append(esp.samples[-1]-esp.samples[0])
        self.assertLess(counts[1]*3,counts[0],counts)
        (ROOT/'build/wad-read-cost.txt').write_text(
            f'11 reads after first open, 1 MiB fragmented WAD: reopen={counts[0]}, retained={counts[1]} SD sectors\n')
    def assert_boot_and_save(self,disk,folder):
        esp=Esp();m=Machine(disk,esp);m.run()
        self.assertTrue(esp.done,('boot failure',bytes(m.boot_trace[-1]).hex()))
        self.assertEqual(bytes(esp.data),ELF)
        self.assertEqual(disk.contents('D1SAVE0.DSG',folder),SAVE)
        return m
    def test_sector_search_thousands_of_siblings(self):
        disk,folder=disk_image()
        # More siblings than even a 16-KiB queue can hold. All must be visited
        # without skipping a directory; the game is the last root entry.
        records=bytearray()
        for i in range(4500):
            disk.set_fat(10000+i,0x0FFFFFFF)
            records+=entry(f'D{i:07}',10000+i,directory=True)
        records+=entry('GAMES',3,directory=True)
        records+=bytes(512-len(records)%512)
        chain=[2]+[1000+2*i for i in range(len(records)//512-1)]
        for i,c in enumerate(chain):
            disk.set_fat(c,chain[i+1] if i+1<len(chain) else 0x0FFFFFFF)
            disk.write(disk.data_start+c-2,records[i*512:(i+1)*512])
        self.assert_boot_and_save(disk,folder)
    def test_sector_search_geometry_and_active_fat(self):
        for spc,part,flags in [(1,0,0),(4,2048,0),(64,4096,0x81)]:
            with self.subTest(spc=spc,partition=part,flags=flags):
                disk,folder=disk_image(spc=spc,partition=part,ext_flags=flags)
                # Root and game entries cross cluster boundaries. FAT copy
                # zero is stale when BPB selects copy one.
                oldroot=disk.read(disk.data_start)
                oldgame=disk.read(disk.data_start+(folder-2)*spc)
                deleted=bytearray(512)
                for at in range(0,512,32):deleted[at]=0xE5
                for start,nextc,records in [(2,1500,oldroot),(folder,1700,oldgame)]:
                    disk.set_fat(start,nextc);disk.set_fat(nextc,0x0FFFFFFF)
                    for s in range(spc):disk.write(disk.data_start+(start-2)*spc+s,deleted)
                    disk.write(disk.data_start+(nextc-2)*spc,records)
                if flags:
                    first=disk.start+disk.reserved
                    for i in range(disk.fat_sectors):disk.write(first+i,bytes(512))
                self.assert_boot_and_save(disk,folder)
    def test_sector_search_high_cluster_and_parent_cursor(self):
        disk,folder=disk_image(clusters=150000)
        records=disk.read(disk.data_start+folder-2)
        disk.set_fat(70000,0x0FFFFFFF);disk.set_fat(600,0x0FFFFFFF)
        disk.write(disk.data_start+1,entry('ANYNAME',70000,directory=True)+bytes(480))
        # An ELF match before descending must survive return from the child.
        dot=entry('DOT',70000,directory=True);dot[:11]=b'.          '
        parent=entry('DOTDOT',3,directory=True);parent[:11]=b'..         '
        game=dot+parent+records[:32]+entry('EMPTY',600,directory=True)+records[32:64]
        disk.write(disk.data_start+70000-2,game.ljust(512,b'\0'))
        self.assert_boot_and_save(disk,70000)
    def test_sector_search_ancestor_cycle(self):
        disk,_=disk_image()
        disk.write(disk.data_start+1,entry('BACK',2,directory=True)+bytes(480))
        esp=Esp();m=Machine(disk,esp);m.run()
        self.assertFalse(esp.started);self.assertEqual(m.writes+m.path_writes,0)
        self.assertEqual(m.boot_trace[-1],b'\x4f\x05\xfe')
    def test_sector_search_read_error_is_not_file_missing(self):
        # Inject a device error at the raw reader, without changing the
        # driver's mount result or returning plausible zero-filled sectors.
        disk,_=disk_image();esp=Esp();m=Machine(disk,esp)
        m.read_error_lba=disk.data_start+1;m.run()
        self.assertFalse(esp.started);self.assertEqual(m.writes+m.path_writes,0)
        self.assertEqual(m.boot_trace[-1],b'\x4f\x05\xe1')
    def test_sector_search_invalid_directory_cluster(self):
        for cluster in [0,1,65538,0x0FFFFFF7]:
            with self.subTest(cluster=cluster):
                disk,_=disk_image()
                disk.write(disk.data_start,entry('BAD',cluster,directory=True)+bytes(480))
                esp=Esp();m=Machine(disk,esp);m.run()
                self.assertFalse(esp.started);self.assertEqual(m.writes+m.path_writes,0)
                self.assertEqual(m.boot_trace[-1],b'\x4f\x05\xfe')
    def test_sector_search_invalid_fat_link(self):
        for link,code in [(0,0xFE),(1,0xFE),(0x0FFFFFF7,0xFE),(3,0xF6)]:
            with self.subTest(link=link):
                disk,_=disk_image()
                deleted=bytearray(512)
                for at in range(0,512,32):deleted[at]=0xE5
                disk.write(disk.data_start+1,deleted);disk.set_fat(3,link)
                esp=Esp();m=Machine(disk,esp);m.run()
                self.assertFalse(esp.started);self.assertEqual(m.writes+m.path_writes,0)
                self.assertEqual(m.boot_trace[-1],bytes([0x4F,5,code]))
    def test_over_128_siblings_with_shallow_game_folder(self):
        disk,folder=disk_image()
        records=bytearray()
        for i in range(300):
            disk.set_fat(1000+i,0x0FFFFFFF)
            records+=entry(f'OTHER{i}',1000+i,directory=True)
        records+=entry('GAMES',3,directory=True)
        records+=bytes(512-len(records)%512)
        clusters=[2]+list(range(900,900+len(records)//512-1))
        for i,cluster in enumerate(clusters):
            disk.set_fat(cluster,clusters[i+1] if i+1<len(clusters) else 0x0FFFFFFF)
            disk.write(disk.data_start+cluster-2,records[i*512:(i+1)*512])
        esp=Esp(cached=True);m=Machine(disk,esp);m.run()
        self.assertTrue(esp.done,('boot failure',bytes(m.boot_trace[-1]).hex()))
        self.assertEqual(disk.contents('D1SAVE0.DSG',folder),SAVE)
    def test_many_sibling_directories(self):
        disk,folder=disk_image()
        records=bytearray()
        for i in range(15):
            disk.set_fat(600+i,0x0FFFFFFF)
            records+=entry(f'OTHER{i}',600+i,directory=True)
        records+=entry('GAMES',3,directory=True)
        disk.write(disk.data_start,records)
        # Same nesting and long directory name as the hardware installation.
        disk.set_fat(8,0x0FFFFFFF)
        disk.write(disk.data_start+1,entry('VDAC4',8,directory=True)+bytes(480))
        short=entry('DOOMVD~1',7,directory=True)
        checksum=0
        for value in short[:11]:
            checksum=(((checksum&1)<<7)+(checksum>>1)+value)&255
        lfn=bytearray(32);lfn[0]=0x41;lfn[11]=15;lfn[13]=checksum
        chars=('DOOM VDAC4\0'+'\uffff'*2).encode('utf-16-le')
        for i,at in enumerate((1,3,5,7,9,14,16,18,20,22,24,28,30)):
            lfn[at:at+2]=chars[2*i:2*i+2]
        disk.write(disk.data_start+6,lfn+short+bytes(448))
        esp=Esp(cached=True);m=Machine(disk,esp);m.run()
        self.assertTrue(esp.done,('boot failure',bytes(m.boot_trace[-1]).hex()))
        self.assertEqual(disk.contents('D1SAVE0.DSG',folder),SAVE)
    def test_real_sdzc_and_esp_share_spi_ports(self):
        class NativeMachine(Machine):
            def __init__(self,disk,app):
                super().__init__(disk,app)
                self.card=Card(disk);self.sd=ZController(self.card);self.select=3
                self.machine.set_memory_block(0x3900,(ROOT/'third_party/fat32/build/port_sdzc.bin').read_bytes())
                for pc in (INIT,READ,WRITE,SYNC):self.machine.clear_breakpoint(pc)
            def port_out(self,port,value):
                if port&255==0x77:
                    self.select=value;self.sd.output(port,value)
                    if value in (1,11):
                        self.ports.write(port,3);return
                if port&255==0x57 and self.select not in (0x13,7):
                    self.sd.output(port,value);return
                super().port_out(port,value)
            def port_in(self,port):
                if port&255==0x57 and self.select not in (0x13,7):return self.sd.input(port)
                return super().port_in(port)
        disk,folder=disk_image();paths={};esp=Esp(paths=paths)
        cfg=b'mouse_sensitivity 6\nsfx_volume 3\nmusic_volume 11\n'
        # Дополнительно к сохранениям: настройки через настоящий порт SD-ZC, без перехвата read512.
        esp.plan += [(0xE5,fs(2,'TEMP.CFG'),('ok',None)),
                     (0xE5,fs(3,'TEMP.CFG',data=cfg),('ok',None)),
                     (0xE5,fs(6,'TEMP.CFG'),('ok',None)),
                     (0xE5,fs(4,'TEMP.CFG',other='DEFAULT.CFG'),('ok',None))]
        m=NativeMachine(disk,esp);m.run()
        self.assertTrue(esp.done,(m.memory[m.symbols['BootStage']],m.memory[m.symbols['BootError']]))
        self.assertEqual(bytes(esp.data),ELF)
        self.assertEqual(disk.contents('D1SAVE0.DSG',folder),SAVE)
        self.assertEqual(disk.contents('DEFAULT.CFG',folder),cfg)
        esp=Esp(cached=True,paths=paths)
        esp.plan=[(0xE5,fs(0,'DEFAULT.CFG'),('stat',len(cfg))),
                  (0xE5,fs(1,'DEFAULT.CFG',length=len(cfg)),('data',cfg))]
        NativeMachine(disk,esp).run();self.assertTrue(esp.done)
        self.assertEqual(esp.path_sets,[])
    def test_nested_folder_elf_wad_save_replace_and_reboot(self):
        disk,folder=disk_image();esp=Esp();m=Machine(disk,esp);m.run()
        self.assertTrue(esp.done);self.assertEqual(bytes(esp.data),ELF);self.assertTrue(esp.committed)
        self.assertEqual(pulled_blocks(esp),[960,913,960,913])
        self.assertEqual(max(esp.event_lengths),35)
        self.assertEqual(esp.query,(b'DOOM.ELF',len(ELF),0x5B0555AA))
        self.assertEqual(disk.contents('D1SAVE0.DSG',folder),SAVE)
        self.assertNotIn('D1SAVE0.DSG',[e['name'] for e in disk.entries(2)])
        self.assertEqual(m.vconfig,[4,4]);self.assertGreater(m.writes,0)
        disk.assert_mirrors()
        second=Esp(cached=True,reboot=True);m2=Machine(disk,second);m2.run()
        self.assertTrue(second.done);self.assertEqual(second.data,b'');self.assertEqual(m2.writes,0)
    def check_loading_screen(self,m,esp,final):
        ft=m.ft
        # первым делом, до первого сектора SD: команды хоста, как у ESP; ACTIVE — последней
        self.assertEqual(ft.cmds[:5],[(0x68,0),(0x44,0),(0x41,0),(0x61,0xC8),(0,0)])
        self.assertEqual(ft.first_reads,0)
        self.assertEqual(m.vconfig[0],4)
        jpg=(ROOT/'assets/loader.jpg').read_bytes()
        self.assertEqual(bytes(ft.cmd_stream),struct.pack('<III',0xFFFFFF24,0x080000,2)+jpg+bytes(-len(jpg)%4))
        self.assertGreater(len(ft.dls),2)
        # до готовности — только чёрный лист; первый видимый кадр — сразу весь экран: картинка на
        # полной яркости, полоса, строки; кадров без картинки или без строк нет
        self.assertEqual(ft.dls[0],[0x02000000,0x26000007,0x27000004,0])
        for dl in ft.dls[1:]:
            self.assertIn(0x01080000,dl);self.assertEqual(dl[dl.index(0x05000000)-1],0x04FFFFFF)
            self.assertIn(0x1F000009,dl);self.assertEqual(dl_text(dl,27),['StartingDOOM']*2)
        self.assertTrue(ft.cmd_after_fonts)                            # JPEG — после шрифтов ПЗУ
        self.assertEqual(ft.cmd_write_direct,0)                        # только REG_CMDB_WRITE
        last=ft.dls[-1]
        self.assertEqual(dl_text(last,27),['StartingDOOM']*2)          # этап с тенью, как у DOOM.ELF
        self.assertEqual(dl_text(last,26),['DOOM1.WAD']*2)             # WAD под полосой
        pct=dl_text(last,29)[0]
        self.assertTrue(pct.endswith('%'));self.assertEqual(int(pct[:-1]),(fills_last:=dl_fill(last)-131)*100//762)
        self.assertIn(0x1F000002,last)                                 # свечение (POINTS)
        self.assertIn(0x0E000060,last)                                 # блик (LINE_WIDTH 6)
        fills=[x for x in map(dl_fill,ft.dls) if x is not None]
        self.assertEqual(fills,sorted(fills))
        data,started=ft.marked
        self.assertFalse(started)                                      # метка — до запуска DOOM.ELF
        self.assertEqual(data[:4],b'VDLP')
        self.assertEqual(struct.unpack('<H',data[4:6])[0],final)
        self.assertEqual(fills[-1],131+final)
        self.assertEqual(data[8:12],bytes(ft.mem.get(0x0C0000+k,0) for k in range(4)))
    def test_loading_screen_first(self):
        disk,_=disk_image();esp=Esp();m=Machine(disk,esp);m.run()
        self.assertTrue(esp.done);self.assertEqual(bytes(esp.data),ELF)
        self.check_loading_screen(m,esp,28+200+4+6)                     # ELF передан: FT_AT_COMMIT + FT_AT_START
        disk,_=disk_image();esp=Esp(cached=True);m=Machine(disk,esp);m.run()
        self.assertTrue(esp.done);self.check_loading_screen(m,esp,28+6)  # ELF в кэше ESP
    def test_no_ft812_still_boots(self):
        disk,folder=disk_image();esp=Esp(cached=True);m=Machine(disk,esp);m.ft.answer=False;m.run()
        self.assertTrue(esp.done);self.assertEqual(disk.contents('D1SAVE0.DSG',folder),SAVE)
        self.assertEqual(m.ft.dls,[]);self.assertIsNone(m.ft.marked);self.assertEqual(bytes(m.ft.cmd_stream),b'')
    def test_root_installation(self):
        disk,folder=disk_image(nested=False);esp=Esp(cached=True);m=Machine(disk,esp);m.run()
        self.assertTrue(esp.done);self.assertEqual(disk.contents('D1SAVE0.DSG',folder),SAVE)
    SERIAL=0x76543210                                  # BS_VolID тома в модели карты (harness.Disk)
    def test_path_from_esp(self):
        # первый запуск ищет по карте и отдаёт папку ESP; второй берёт её из ESP — без поиска.
        # На карту на этапе 5 не пишется ничего.
        for nested in (True,False):
            with self.subTest(nested=nested):
                disk,folder=disk_image(nested=nested);paths={}
                esp=Esp(cached=True,paths=paths);m=Machine(disk,esp);m.run()
                self.assertTrue(esp.done,('boot failure',bytes(m.boot_trace[-1]).hex()))
                self.assertEqual(esp.path_gets,[('DOOM',self.SERIAL)])
                self.assertEqual(esp.path_sets,[('DOOM',self.SERIAL,folder)]);self.assertEqual(m.path_writes,0)
                self.assertNotIn('DOOMPATH.CFG',[e['name'] for e in disk.entries()])
                esp=Esp(cached=True,reboot=True,paths=paths);m=Machine(disk,esp);m.run()   # сохранение читается
                self.assertTrue(esp.done,('boot failure',bytes(m.boot_trace[-1]).hex()))
                self.assertEqual(esp.path_sets,[]);self.assertEqual(m.writes+m.path_writes,0)
                if nested:self.assertNotIn(disk.data_start+(3-2)*disk.spc,m.read_lbas)   # GAMES не читался
                self.assertEqual(disk.contents('D1SAVE0.DSG',folder),SAVE)
    def test_path_without_signature_is_updated(self):
        # по пути из ESP нет DOOM.ELF и IWAD (не та папка, вне тома, другая карта) — поиск и новый путь
        S=self.SERIAL
        for key,cluster in [(('DOOM',S),3),(('DOOM',S),2),(('DOOM',S),0x0FFFFFFF),
                            (('DOOM',S),1),(('DOOM',S),0),(('DOOM',S^1),7),(('RTYPE',S),7)]:
            with self.subTest(key=key,cluster=cluster):
                disk,folder=disk_image();paths={key:cluster}
                esp=Esp(cached=True,paths=paths);m=Machine(disk,esp);m.run()
                self.assertTrue(esp.done,('boot failure',bytes(m.boot_trace[-1]).hex()))
                self.assertIn(disk.data_start+(3-2)*disk.spc,m.read_lbas)                 # искал
                self.assertEqual(esp.path_sets,[('DOOM',self.SERIAL,folder)]);self.assertEqual(m.path_writes,0)
                self.assertEqual(paths[('DOOM',self.SERIAL)],folder)
                if key!=('DOOM',self.SERIAL):self.assertEqual(paths[key],cluster)          # чужие записи целы
                self.assertEqual(disk.contents('D1SAVE0.DSG',folder),SAVE)
    def test_path_moved_folder(self):
        # папку перенесли по карте: кластер каталога тот же — поиска нет
        disk,folder=disk_image();disk.write(disk.data_start,entry('MOVED',folder,directory=True)+bytes(480))
        esp=Esp(cached=True,paths={('DOOM',self.SERIAL):folder});m=Machine(disk,esp);m.run()
        self.assertTrue(esp.done,('boot failure',bytes(m.boot_trace[-1]).hex()))
        self.assertEqual(esp.path_sets,[]);self.assertEqual(m.path_writes,0)
        self.assertEqual(disk.contents('D1SAVE0.DSG',folder),SAVE)
    def test_path_old_firmware_and_failed_set(self):
        # прошивка без PATH_GET (ошибка ESP) — поиск, путь не пишется; отказ записи пути — игра идёт
        disk,folder=disk_image();esp=Esp(cached=True);m=Machine(disk,esp);m.run()
        self.assertTrue(esp.done,('boot failure',bytes(m.boot_trace[-1]).hex()))
        self.assertEqual(esp.path_sets,[]);self.assertEqual(m.path_writes,0)
        disk,folder=disk_image();paths={}
        esp=Esp(cached=True,paths=paths,path_set_ok=False);m=Machine(disk,esp);m.run()
        self.assertTrue(esp.done,('boot failure',bytes(m.boot_trace[-1]).hex()))
        self.assertEqual(len(esp.path_sets),1);self.assertEqual(paths,{})
    def test_failed_write_keeps_previous_slot(self):
        disk,folder=disk_image();first=Esp(cached=True);Machine(disk,first).run()
        esp=Esp(cached=True)
        esp.plan=[(0xE5,fs(2),('ok',None)),(0xE5,fs(3,data=b'broken save'),('error',None)),
                  (0xE4,struct.pack('<IH',0,512),('wad',WAD[:512]))]
        m=Machine(disk,esp);m.fail_writes=True;m.run()
        self.assertTrue(esp.done);self.assertEqual(disk.contents('D1SAVE0.DSG',folder),SAVE)
    def test_missing_wad_does_not_start_or_write(self):
        disk,_=disk_image(wad=False);esp=Esp(cached=True);m=Machine(disk,esp);m.run()
        self.assertFalse(esp.started);self.assertEqual(m.border[-1],2);self.assertEqual(m.writes+m.path_writes,0)
        self.assertEqual(m.boot_trace[-1],bytes([0x4F,5,m.memory[m.symbols['BootError']]]))
        # кадры экрана загрузки повторяют след этапа (FT_SEEN) — повторы подряд не в счёт
        self.assertEqual([k for k,_ in itertools.groupby(x[1] for x in m.boot_trace[:-1])],list(range(1,6)))
        pointer=int.from_bytes(m.memory[m.symbols['BootMessage']:m.symbols['BootMessage']+2],'little')
        self.assertEqual(pointer,m.symbols['MsgMissingWad'])
        (ROOT/'build/missing-wad.scr').write_bytes(bytes(m.memory[0xC000:0xDB00]))
    def test_missing_elf_message(self):
        disk=Disk(partition=2048);esp=Esp(cached=True);m=Machine(disk,esp);m.run()
        self.assertFalse(esp.started);self.assertEqual(m.writes+m.path_writes,0)
        pointer=int.from_bytes(m.memory[m.symbols['BootMessage']:m.symbols['BootMessage']+2],'little')
        self.assertEqual(pointer,m.symbols['MsgMissingElf'])
    def run_keys(self,queue=b'',refill=None):
        """Пакеты 75 после запуска: 8 штук, мышь между ними меняется; затем ESP просит стоп."""
        class KeysEsp(Esp):
            def __call__(self,c,p):
                m=self.machine
                if c==0x70:
                    m.memory[m.symbols['KeyDivider']]=1
                    return frame(0xF0,b'\1')
                if c==0x75:
                    self.packets.append((bytes(p),m.mouse));self.ps2+=p[4:]
                    n=len(self.packets)
                    m.mouse=(0xFF^(n&7),(0x10+n*45)&255,(0x20-n*30)&255)
                    if refill and n in refill:m.ps2_queue+=refill[n]
                    if n<8:m.memory[m.symbols['KeyDivider']]=1
                    else:m.memory[m.symbols['StatusDivider']]=1
                    return frame(0xF5,b'\1')
                if c==0x73:return frame(0xF3,bytes([3 if len(self.packets)>=8 else 2,1])+bytes(4))
                return super().__call__(c,p)
        disk,_=disk_image();esp=KeysEsp(cached=True);esp.packets=[]
        m=Machine(disk,esp);esp.machine=m;m.mouse=(0xFE,0x10,0x20);m.ps2_queue+=queue;m.run()
        self.assertTrue(esp.started);self.assertFalse(esp.done);self.assertEqual(len(esp.packets),8)
        self.assertEqual(m.fe_reads,0,'матрица ZX не читается')
        return esp,m
    def test_keys_carry_kempston_mouse(self):
        esp,m=self.run_keys()
        for p,mouse in esp.packets:
            self.assertEqual(p[0],0);self.assertEqual(p[1:4],bytes(mouse))
    def test_keys_from_ps2_queue(self):
        # Esc, стрелка вверх (E0), отпускания (F0), переполнение #FF и длинная очередь: за опрос не
        # больше 24 байт, порядок тот же, очередь включается один раз (#0C = 1, #F0 = 2)
        first=bytes([0x76,0xF0,0x76,0xE0,0x75,0xE0,0xF0,0x75,0xFF])+bytes(range(1,31))
        # очередь до включения (#0C = 1 — сброс) пропадает, как у AVR: байты — после первого опроса
        esp,m=self.run_keys(b')',{1:first,4:bytes([0x1D,0xF0,0x1D])})
        self.assertEqual(bytes(esp.ps2),first+bytes([0x1D,0xF0,0x1D]))
        self.assertTrue(all(len(p)<=4+24 for p,_ in esp.packets))
        self.assertEqual([len(p)-4 for p,_ in esp.packets][:4],[0,24,len(first)-24,0])
        self.assertEqual(m.gluk_writes[:2],[(0x0C,1),(0xF0,2)])
        self.assertEqual([w for w in m.gluk_writes if w[0]==0x0C],[(0x0C,1)])
if __name__=='__main__':unittest.main(verbosity=2)
