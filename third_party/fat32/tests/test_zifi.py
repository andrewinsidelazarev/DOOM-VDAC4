"""Собранный ZiFi, драйвер и SD-адаптер: проверка записи через страничную память."""
from pathlib import Path
import struct
import unittest
import z80
from harness import ROOT,Disk,Driver,symbols
from test_sdzc import Card,ZController

PROJECT=ROOT.parent/'ZiFi ESP32-S3 Zero'/'ZiFi SPG'
STOP,STACK=0xBE10,0xBFD0


class Zifi:
    def __init__(self, disk=None):
        self.disk=disk or Disk(spc=8)
        self.card=Card(self.disk)
        self.bus=ZController(self.card)
        self.cpu=z80.Z80Machine()
        self.ram=self.cpu.memory
        self.sym=symbols(PROJECT/'build/zifi.sym')
        self.pages=[bytearray(16384) for _ in range(256)]
        self.mapping=[0,31,2,0]
        for page,path in [(2,PROJECT/'build/zifi.bin'),(14,ROOT/'build/fat32.bin'),
                          (15,ROOT/'build/fat32-work.bin')]:
            data=path.read_bytes()
            self.pages[page][:len(data)]=data
        for window,page in enumerate(self.mapping):
            self.cpu.set_memory_block(window*16384,self.pages[page])
        for window in (0,1,3):
            self.ram[self.sym[f'restore_page{window}']+1]=self.mapping[window]
        self.cpu.set_output_callback(self.output)
        self.cpu.set_input_callback(self.bus.input)
        self.logs=[]
        self.irq_trigger=None
        self.music_ticks=0
        # Подменена только отрисовка интерфейса, которой нужны видеопрерывания.
        self.ui={self.sym[x]:x for x in ('set_ports','zifi_log')}
        for addr in [STOP,*self.ui]:
            self.cpu.set_breakpoint(addr)
        assert self.call('sd_init')==(0,True,False)
        assert self.mapping==[0,31,2,0]

    def output(self,port,value):
        if port in (0x10AF,0x11AF,0x13AF):
            window=(port>>8)-16
            start=window*16384
            self.pages[self.mapping[window]][:]=self.ram[start:start+16384]
            self.mapping[window]=value
            self.cpu.set_memory_block(start,self.pages[value])
        elif port&255 in (0x57,0x77):
            self.bus.output(port,value)
        elif port&255 not in (0xFE,0xAF):
            raise AssertionError(hex(port))

    def call(self,label,stop_at=None,**regs):
        stop=self.sym[stop_at] if stop_at else STOP
        if stop_at:
            self.cpu.set_breakpoint(stop)
        self.cpu.pc,self.cpu.sp=self.sym[label],STACK
        self.ram[STACK:STACK+2]=STOP.to_bytes(2,'little')
        for key,value in regs.items():
            setattr(self.cpu,key,value)
        for _ in range(10000):
            self.cpu.ticks_to_stop=1_000_000
            event=self.cpu.run()
            if not event&2:
                continue
            if self.cpu.pc in (STOP,stop):
                if stop_at:
                    self.cpu.clear_breakpoint(stop)
                return self.cpu.a,bool(self.cpu.f&64),bool(self.cpu.f&1)
            if self.cpu.pc==self.irq_trigger:
                # Настоящий обработчик ZiFi сохраняет регистры и возвращает банки.
                self.cpu.clear_breakpoint(self.irq_trigger)
                self.cpu.sp-=2
                self.ram[self.cpu.sp:self.cpu.sp+2]=self.cpu.pc.to_bytes(2,'little')
                self.cpu.pc=self.sym['int_text_off']
                self.irq_trigger=None
                continue
            if self.cpu.pc not in self.ui:
                continue
            if self.ui[self.cpu.pc]=='music_player_play':
                self.music_ticks+=1
            if self.ui.get(self.cpu.pc)=='zifi_log':
                self.logs.append(bytes(self.ram[self.cpu.hl:self.cpu.hl+80]).split(b'\0')[0])
            self.cpu.pc=int.from_bytes(self.ram[self.cpu.sp:self.cpu.sp+2],'little')
            self.cpu.sp+=2
        raise AssertionError(('ZiFi did not return',label,hex(self.cpu.pc)))

    def save(self,name,data,offset=0,page=32):
        payload=bytes(offset)+data
        for pos in range(0,len(payload),16384):
            self.pages[page+pos//16384][:len(payload[pos:pos+16384])]=payload[pos:pos+16384]
        thread=self.sym['read_threads']
        # zifi_get завершает приём адресом get_buffer (#4000), а не смещением 0.
        struct.pack_into('<H',self.ram,thread+self.sym['thread.adress'],self.sym['get_buffer']+offset)
        self.ram[thread+self.sym['thread.page']]=page
        self.ram[thread+self.sym['thread.full_len']:thread+self.sym['thread.full_len']+3]=len(data).to_bytes(3,'little')
        self.cpu.set_memory_block(self.sym['FILE_NAME'],name.encode()+b'\0')
        return self.call('save_downloaded_file')


class ZifiTests(unittest.TestCase):
    def test_music_buffer_uses_its_two_pages_only(self):
        for size,offset in ((1,0),(32768,0),(32764,4)):
            with self.subTest(size=size,offset=offset):
                h=Zifi()
                data=bytes((i*17+3)%251 for i in range(size))
                self.assertEqual(h.save('track.pt3',data,offset,page=29),(0,True,False))
                self.assertEqual(h.disk.contents('track.pt3'),data)
        for page,size,offset in ((29,32769,0),(29,32768,1),(31,1,0),(28,1,0),(72,1,0)):
            h=Zifi()
            before=dict(h.disk.blocks)
            self.assertEqual(h.save('invalid.bin',bytes(size),offset,page=page),(0xF2,False,True))
            self.assertEqual(h.disk.blocks,before)

    def test_load_ini_through_driver_and_restore_banks(self):
        for spc in (1,8):
            for size in (37,511,512,3000):
                with self.subTest(spc=spc,size=size):
                    disk=Disk(spc=spc)
                    seed=Driver(disk)
                    self.assertEqual(seed.mkdir('zifi'),(0,True,False))
                    seed.find('zifi',16)
                    seed.call(31)
                    seed.create('zifi.ini')
                    data=(b'SSID:test\r\nPASSWORD:test\r\ntime:none\r\n'+b';'*size)[:size]
                    seed.append(data)
                    h=Zifi(disk)
                    h.call('load_ini',stop_at='parse_ini')
                    self.assertEqual(h.cpu.pc,h.sym['parse_ini'])
                    length=min(size,511)
                    self.assertEqual(h.pages[32][:length+1],data[:length]+b'\0')
                    self.assertEqual(h.mapping,[0,31,2,0])
                    self.assertEqual(h.ram[h.sym['fat_active']],0)

    def test_invalid_download_range_does_not_create_file(self):
        for size,offset in ((640*1024+1,0),(640*1024,1)):
            with self.subTest(size=size,offset=offset):
                h=Zifi()
                before=dict(h.disk.blocks)
                self.assertEqual(h.save('invalid.bin',bytes(size),offset),(0xF2,False,True))
                self.assertEqual(h.disk.blocks,before)
                self.assertEqual(h.mapping,[0,31,2,0])

    def test_sd_session_restores_previous_interrupt_ui_mode(self):
        h=Zifi()
        h.ram[h.sym['save_mode']+1]=0
        self.assertEqual(h.save('empty.bin',b''),(0,True,False))
        self.assertEqual(h.ram[h.sym['save_mode']+1],0)
        self.assertEqual(h.disk.contents('empty.bin'),b'')

    def test_failed_replace_keeps_previous_file(self):
        """Отказ при перезаписи не должен уничтожить уже скачанный файл."""
        h=Zifi()
        first=bytes(i%251 for i in range(20000))
        self.assertEqual(h.save('keep.bin',first),(0,True,False))
        self.assertEqual(h.disk.contents('keep.bin'),first)
        # Роняем запись на втором APPEND уже во временный файл.
        second=bytes((i*7)%251 for i in range(33000))
        written=[]
        original=h.card.fail_write
        h.card.fail_write=lambda lba,block: (written.append(lba),len(written)>40)[1]
        self.assertNotEqual(h.save('keep.bin',second)[0],0)
        h.card.fail_write=original
        # Старый файл цел и содержит прежние данные, имя за ним.
        self.assertEqual(h.disk.contents('keep.bin'),first)
        self.assertEqual(h.ram[h.sym['fat_active']],0)
        h.disk.assert_mirrors()

    def test_write_failure_preserves_committed_prefix_and_allows_next_save(self):
        h=Zifi()
        data=bytes(i%251 for i in range(33000))
        # Корень занимает кластер 2; второй APPEND начинает кластер 7.
        rejected_lba=h.disk.data_start+5*h.disk.spc
        h.card.fail_write=lambda lba,block: lba==rejected_lba
        self.assertEqual(h.save('partial.bin',data),(0xE2,False,True))
        self.assertEqual(h.disk.contents('partial.bin'),data[:16384])
        self.assertEqual(h.mapping,[0,31,2,0])
        self.assertFalse(h.card.selected)
        self.assertEqual(h.ram[h.sym['fat_active']],0)
        self.assertEqual(len(h.logs),1)
        h.card.fail_write=None
        self.assertEqual(h.save('next.bin',b'next save'),(0,True,False))
        self.assertEqual(h.disk.contents('next.bin'),b'next save')
        h.disk.assert_mirrors()

    def test_music_interrupt_during_sd_write_restores_driver_banks(self):
        h=Zifi()
        h.ui[h.sym['music_player_play']]='music_player_play'
        h.ui[h.sym['set_256c_mode']]='set_256c_mode'
        for addr in h.ui:
            h.cpu.set_breakpoint(addr)
        h.ram[h.sym['music_sw']+1]=1
        h.ram[h.sym['load_sw']+1]=0
        # Проигрыватель закончил трек: при записи автозагрузку откладываем.
        h.pages[3][h.sym['music_setup_vars']&0x3FFF]=0x80
        h.irq_trigger=symbols(ROOT/'build/port_sdzc.sym')['SD_WRITE']
        h.cpu.set_breakpoint(h.irq_trigger)
        data=bytes(i%251 for i in range(17000))
        self.assertEqual(h.save('music.bin',data),(0,True,False))
        self.assertEqual(h.music_ticks,1)
        self.assertEqual(h.ram[h.sym['load_sw']+1],0)
        self.assertEqual(h.mapping,[0,31,2,0])
        self.assertEqual(h.disk.contents('music.bin'),data)
        h.disk.assert_mirrors()

    def test_paged_save_maximum_and_replace(self):
        """Повторная загрузка того же имени перезаписывает файл через временный."""
        h=Zifi()
        for size,offset in [(1,0),(16385,37),(640*1024,0)]:
            data=bytes((i*31+size)%251 for i in range(size))
            name=f'data{size}.bin'
            self.assertEqual(h.save(name,data,offset),(0,True,False))
            self.assertEqual(h.disk.contents(name),data)
            self.assertEqual(h.mapping,[0,31,2,0])
            self.assertEqual(h.save(name,b'replace'),(0,True,False))
            self.assertEqual(h.disk.contents(name),b'replace')
            self.assertEqual(h.mapping,[0,31,2,0])
            # Временный файл не остаётся на томе после успешной подмены.
            self.assertNotIn('ZIFITMP.$$$',[e['name'] for e in h.disk.entries()])
        self.assertFalse(h.logs)
        h.disk.assert_mirrors()

    def test_create_download_directory_twice(self):
        h=Zifi()
        h.cpu.set_memory_block(h.sym['DIR_date']+1,b'2026-09-08\0')
        for _ in range(2):
            self.assertEqual(h.call('set_download_dir'),(0,True,False))
            self.assertEqual(h.mapping,[0,31,2,0])
        self.assertEqual(h.save('test.bin',b'in directory'),(0,True,False))
        zifi=h.disk.get('zifi')['cluster']
        downloads=h.disk.get('downloads',zifi)['cluster']
        date=h.disk.get('2026-09-08',downloads)['cluster']
        self.assertEqual(h.disk.contents('test.bin',date),b'in directory')
        h.disk.assert_mirrors()


if __name__=='__main__':
    unittest.main()
