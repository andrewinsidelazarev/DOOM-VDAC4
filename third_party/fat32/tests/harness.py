"""Исполнение собранного драйвера Z80; заменён только физический секторный обмен."""
from pathlib import Path
import re
import struct
import z80

ROOT = Path(__file__).resolve().parents[1]
STOP, STACK, PARAM, NAME, DATA = 0x8100, 0xBFF0, 0x8400, 0x8600, 0xC000
INIT, READ, WRITE, SYNC = 0x8110, 0x8120, 0x8130, 0x8140


def symbols(path):
    return {m[1]: int(m[2], 16) for line in Path(path).read_text().splitlines()
            if (m := re.match(r'^([^:]+):\s+EQU\s+(0x[0-9A-Fa-f]+)$', line))}


def u32(data, offset=0):
    return struct.unpack_from('<I', data, offset)[0]


class Disk:
    """Независимый разбор FAT и тестовый том FAT32 с настоящими MBR/BPB/FSInfo."""
    def __init__(self, spc=1, partition=0, clusters=65536, extended=0, extended_skip=0,
                 fats=2, ext_flags=0):
        self.spc, self.start, self.clusters = spc, partition, clusters
        self.fats = fats
        # BPB_ExtFlags: бит 7 отключает зеркалирование, младшая тетрада —
        # номер активной копии FAT. Стенд обязан читать ту же копию.
        self.active = (ext_flags & 0x0F) if ext_flags & 0x80 else 0
        self.step = extended + 16
        self.ebr = partition - extended - self.step*extended_skip if extended else 0
        self.reserved = 32
        self.fat_sectors = (clusters + 2 + 127) // 128
        self.total = self.reserved + fats*self.fat_sectors + clusters*spc
        self.data_start = partition + self.reserved + fats*self.fat_sectors
        # Храним только записанные секторы: большие и полные тома требуют мало ОЗУ.
        self.blocks = {}
        bpb = bytearray(512)
        bpb[:11] = b'\xEB\x58\x90F32TEST '
        struct.pack_into('<HBHBHHBHHHII', bpb, 11,
                         512, spc, self.reserved, fats, 0, 0, 0xF8, 0, 63, 255,
                         partition, self.total)
        struct.pack_into('<IHHIHH', bpb, 36, self.fat_sectors, ext_flags, 0, 2, 1, 6)
        bpb[64], bpb[66] = 0x80, 0x29
        struct.pack_into('<I', bpb, 67, 0x76543210)
        bpb[71:82], bpb[82:90], bpb[510:512] = b'TEST VOLUME', b'FAT32   ', b'\x55\xAA'
        self.write(partition, bpb)
        self.write(partition+6, bpb)
        info = bytearray(512)
        struct.pack_into('<I', info, 0, 0x41615252)
        struct.pack_into('<III', info, 484, 0x61417272, clusters-1, 3)
        info[510:512] = b'\x55\xAA'
        self.write(partition+1, info)
        self.write(partition+7, info)
        for cluster, value in [(0, 0x0FFFFFF8), (1, 0x0FFFFFFF), (2, 0x0FFFFFFF)]:
            self.set_fat(cluster, value)
        if partition:
            mbr = bytearray(512)
            mbr[510:512] = b'\x55\xAA'
            if extended:
                # Цепочка EBR: extended — отступ от каждого EBR до его тома,
                # extended_skip — сколько логических дисков идёт перед нужным.
                # У «пустышек» нет годного BPB, поэтому драйвер обязан пройти
                # по ссылке дальше, а не остановиться на первом же томе.
                assert self.ebr > 0, 'цепочка EBR не помещается перед томом'
                mbr[450] = 0x0F
                struct.pack_into('<II', mbr, 454, self.ebr,
                                 self.total + partition - self.ebr)
                for n in range(extended_skip + 1):
                    last = n == extended_skip
                    rec = bytearray(512)
                    rec[510:512] = b'\x55\xAA'
                    rec[450] = 0x0C
                    struct.pack_into('<II', rec, 454, extended,
                                     self.total if last else 16)
                    if not last:
                        # Ссылка на следующий EBR — от начала первого EBR.
                        rec[450+16] = 0x05
                        struct.pack_into('<II', rec, 454+16,
                                         self.step*(n+1), self.step + 16)
                    self.write(self.ebr + self.step*n, rec)
            else:
                mbr[450] = 0x0C
                struct.pack_into('<II', mbr, 454, partition, self.total)
            self.write(0, mbr)

    def read(self, lba):
        assert 0 <= lba < self.start + self.total, ('LBA outside media', lba)
        return self.blocks.get(lba, bytes(512))

    def write(self, lba, data):
        assert len(data) == 512
        assert 0 <= lba < self.start + self.total
        self.blocks[lba] = bytes(data)

    def set_fat(self, cluster, value):
        for copy in range(self.fats):
            lba = self.start + self.reserved + copy*self.fat_sectors + cluster//128
            sector = bytearray(self.read(lba))
            struct.pack_into('<I', sector, (cluster % 128)*4, value)
            self.write(lba, sector)

    def fat(self, cluster, copy=None):
        copy = self.active if copy is None else copy
        sector = self.read(self.start+self.reserved+copy*self.fat_sectors+cluster//128)
        return u32(sector, cluster%128*4) & 0x0FFFFFFF

    def chain(self, first):
        found = []
        while first:
            assert 2 <= first < self.clusters+2, ('invalid cluster', first)
            assert first not in found, ('FAT cycle', first)
            found.append(first)
            first = self.fat(first)
            if first >= 0x0FFFFFF8:
                return found
        assert not found, 'allocated chain ends in FREE'
        return []

    def cluster_data(self, cluster):
        lba = self.data_start + (cluster-2)*self.spc
        return b''.join(self.read(lba+i) for i in range(self.spc))

    def entries(self, directory=2):
        raw = b''.join(self.cluster_data(c) for c in self.chain(directory))
        pending, result = {}, []
        positions = (1,3,5,7,9,14,16,18,20,22,24,28,30)
        for i in range(0, len(raw), 32):
            e = raw[i:i+32]
            if e[0] == 0:
                break
            if e[0] == 0xE5:
                pending = {}
                continue
            if e[11] == 15:
                pending[e[0] & 31] = e
                continue
            if pending:
                name = b''.join(pending[n][p:p+2] for n in sorted(pending)
                                for p in positions).decode('utf-16-le').split('\0')[0].rstrip('\uffff')
                checksum = 0
                for x in e[:11]:
                    checksum = (((checksum & 1) << 7) + (checksum >> 1) + x) & 255
                assert all(record[13] == checksum for record in pending.values())
            else:
                stem, ext = e[:8].decode('cp866').rstrip(), e[8:11].decode('cp866').rstrip()
                name = stem + ('.'+ext if ext else '')
            pending = {}
            result.append(dict(name=name, short=e[:11], attr=e[11], size=u32(e,28),
                               cluster=struct.unpack_from('<H',e,26)[0] | struct.unpack_from('<H',e,20)[0]<<16,
                               raw=e))
        return result

    def get(self, name, directory=2):
        return next(e for e in self.entries(directory) if e['name'].casefold() == name.casefold())

    def contents(self, name, directory=2):
        e = self.get(name, directory)
        data = b''.join(self.cluster_data(c) for c in self.chain(e['cluster']))
        assert len(data) >= e['size']
        return data[:e['size']]

    def assert_mirrors(self):
        assert not self.active, 'том без зеркалирования: копии FAT равны быть не должны'
        for copy in range(1, self.fats):
            for i in range(self.fat_sectors):
                assert self.read(self.start+self.reserved+i) ==                     self.read(self.start+self.reserved+copy*self.fat_sectors+i)


class Driver:
    def __init__(self, disk=None, mount=True, build=None, native=None):
        self.disk = disk or Disk()
        build = Path(build or ROOT/'build')
        self.sym = symbols(build/'fat32.sym')
        self.cpu = z80.Z80Machine()
        self.ram = self.cpu.memory
        self.cpu.set_memory_block(0x4000, (build/'fat32.bin').read_bytes())
        # Рабочая страница: в ней код проверок safety.asm (и таблица порта SD-ZC).
        self.cpu.set_memory_block(0x0000, (build/'fat32-work.bin').read_bytes())
        self.operations, self.fail, self.syncs = [], None, 0
        for address in (STOP,INIT,READ,WRITE,SYNC):
            self.cpu.set_breakpoint(address)
        table=PARAM
        if native:
            self.cpu.set_memory_block(0x3900,(build/'port_sdzc.bin').read_bytes())
            self.cpu.set_input_callback(native.input)
            self.cpu.set_output_callback(native.output)
            table=0x3900
        else:
            self.cpu.set_memory_block(PARAM, struct.pack('<4H',INIT,READ,WRITE,SYNC))
        assert self.call(0, hl=table) == (0,True,False)
        assert self.call(1) == (0,True,False)
        if mount:
            assert self.call(3) == (0,True,False), self.result()

    def result(self):
        return self.cpu.a, bool(self.cpu.f&64), bool(self.cpu.f&1)

    def call(self, slot, **regs):
        self.cpu.sp, self.cpu.pc = STACK, 0x4000+3*slot if isinstance(slot,int) else self.sym[slot]
        self.ram[STACK:STACK+2] = STOP.to_bytes(2,'little')
        for name, value in regs.items():
            setattr(self.cpu,name,value)
        for _ in range(getattr(self, 'max_events', 40000)):
            self.cpu.ticks_to_stop = 1_000_000
            event = self.cpu.run()
            if not event & 2:
                continue
            if self.cpu.pc == STOP:
                return self.result()
            self.callback()
        raise AssertionError(('Z80 did not return', hex(self.cpu.pc), slot, self.operations[-5:]))

    def callback(self):
        pc, lba, buffer = self.cpu.pc, self.cpu.de<<16 | self.cpu.hl, self.cpu.bc
        status = 0
        if pc in (READ,WRITE):
            op = 'read' if pc == READ else 'write'
            self.operations.append((op,lba))
            if self.fail and self.fail(op,lba):
                status = 0xE1
            elif pc == READ:
                assert buffer+512 <= 65536
                self.cpu.set_memory_block(buffer,self.disk.read(lba))
            else:
                assert buffer+512 <= 65536
                self.disk.write(lba,bytes(self.ram[buffer:buffer+512]))
        elif pc == SYNC:
            self.syncs += 1
        elif pc != INIT:
            raise AssertionError(hex(pc))
        # Намеренно портим основные регистры обработчика для проверки контракта.
        self.cpu.bc, self.cpu.de, self.cpu.hl = 0xA55A,0x1234,0x5678
        self.cpu.a,self.cpu.f = status,64 if status == 0 else 0
        self.cpu.pc = int.from_bytes(self.ram[self.cpu.sp:self.cpu.sp+2],'little')
        self.cpu.sp += 2

    def name(self, name, prefix=b''):
        self.cpu.set_memory_block(NAME,prefix+name.encode('cp866')+b'\0')
        return NAME

    def create(self, name, size=0):
        return self.call(72,hl=self.name(name,b'\x20'+struct.pack('<I',size)))

    def find(self, name, attr=0):
        return self.call(59,hl=self.name(name,bytes([attr])))

    def mkdir(self, name):
        return self.call(73,hl=self.name(name))

    def append(self, data, buffer=DATA):
        self.cpu.set_memory_block(buffer,data)
        return self.call(76,hl=buffer,bc=len(data))

    def filex(self, operation, offset=0, buffer=DATA, length=0, flags=0, **fields):
        b = bytearray(32)
        b[0:4] = bytes([32,1,operation,flags])
        struct.pack_into('<IHH',b,4,offset,buffer,length)
        for k,v in fields.items():
            struct.pack_into('<'+('H' if k in ('aux','aux_length') else 'I'),b,
                             dict(aux=12,aux_length=14,source_dir=16,dest_dir=20)[k],v)
        self.cpu.set_memory_block(PARAM,b)
        result = self.call(77,hl=PARAM)
        return result,u32(self.ram,PARAM+24)
