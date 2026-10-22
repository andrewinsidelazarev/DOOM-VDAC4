import unittest
import struct
from harness import Driver, Disk, DATA, PARAM, NAME, u32


class DriverTests(unittest.TestCase):
    def test_reserved_and_capabilities(self):
        h=Driver(mount=False)
        for slot in range(78,128):
            self.assertEqual(h.ram[0x4000+3*slot],0xC3)
            self.assertEqual(h.call(slot),(0xFE,False,True))
        self.assertEqual(h.filex(0),((0,True,False),255))
        self.assertFalse(h.operations)

    def test_mount_and_empty_file(self):
        for partition in (0,2048):
            h=Driver(Disk(partition=partition))
            self.assertEqual(h.create('Empty.txt'),(0,True,False))
            self.assertEqual(h.disk.contents('Empty.txt'),b'')
            before=dict(h.disk.blocks)
            self.assertEqual(h.call(49,hl=DATA,b=1),(15,False,False))
            self.assertEqual(before,h.disk.blocks)
            self.assertEqual(h.find('empty.TXT'),(1,False,False))
            self.assertEqual(h.cpu.de<<16|h.cpu.hl,0)
            self.assertEqual(h.find('missing'),(0,True,False))
            self.assertEqual(h.append(b'x'),(0xF1,False,True))

    def test_append_exact_bytes_and_64k_end(self):
        h=Driver()
        self.assertEqual(h.create('downloaded long name.bin'),(0,True,False))
        data=b''
        for n in (1,511,513,4096,16384,17):
            chunk=bytes((i*29+n)%256 for i in range(n))
            self.assertEqual(h.append(chunk),(0,True,False),n)
            data+=chunk
            self.assertEqual(h.disk.contents('downloaded long name.bin'),data)
        h.disk.assert_mirrors()
        self.assertEqual(h.call(35),(0,True,False))
        self.assertEqual(h.syncs,1)

    def test_failed_create_cannot_write_previous_file(self):
        h=Driver()
        self.assertEqual(h.create('same.bin'),(0,True,False))
        self.assertEqual(h.append(b'original'),(0,True,False))
        self.assertNotEqual(h.create('same.bin')[0],0)
        before=dict(h.disk.blocks)
        self.assertEqual(h.append(b'bad'),(0xF1,False,True))
        self.assertEqual(h.call(49,hl=DATA,b=1),(0xF1,False,True))
        self.assertEqual(before,h.disk.blocks)

    def test_filex_read_write_resize(self):
        h=Driver()
        self.assertEqual(h.create('file.bin'),(0,True,False))
        data=bytes(i%251 for i in range(17000))
        for i in range(0,len(data),16384):
            self.assertEqual(h.append(data[i:i+16384]),(0,True,False))
        self.assertEqual(h.filex(1,offset=300,buffer=DATA,length=16384),((0,True,False),16384))
        self.assertEqual(bytes(h.ram[DATA:65536]),data[300:16684])
        h.cpu.set_memory_block(DATA,b'replacement')
        self.assertEqual(h.filex(2,offset=507,length=11),((0,True,False),11))
        data=data[:507]+b'replacement'+data[518:]
        self.assertEqual(h.disk.contents('file.bin'),data)
        self.assertEqual(h.filex(3,offset=1000)[0],(0,True,False))
        self.assertEqual(h.disk.contents('file.bin'),data[:1000])
        self.assertEqual(h.filex(3,offset=2000)[0],(0,True,False))
        self.assertEqual(h.disk.contents('file.bin'),data[:1000]+bytes(1000))
        self.assertEqual(h.filex(3,offset=0)[0],(0,True,False))
        self.assertEqual(h.disk.contents('file.bin'),b'')
        h.disk.assert_mirrors()

    def test_directory_names_and_numeric_tails(self):
        h=Driver(Disk(spc=8))
        for n in range(1,105):
            name=f'2026-09-{n:03}'
            self.assertEqual(h.mkdir(name),(0,True,False),name)
            self.assertEqual(h.find(name,16),(1,False,False),name)
        entries=h.disk.entries()
        self.assertEqual(len(entries),104)
        self.assertEqual(len({e['short'] for e in entries}),104)
        for n in (1,9,10,99,100,104):
            tail='~'+str(n)
            self.assertEqual(entries[n-1]['short'],('2026-09-'[:8-len(tail)]+tail+'   ').encode())
        self.assertEqual(h.call(31),(0,True,False))
        self.assertEqual(h.create('inside.txt'),(0,True,False))
        self.assertEqual(h.append(b'payload'),(0,True,False))
        directory=entries[-1]['cluster']
        self.assertEqual(h.disk.contents('inside.txt',directory),b'payload')
        self.assertEqual(h.call(32),(0,True,False))
        self.assertEqual(h.find('inside.txt'),(0,True,False))
        self.assertNotEqual(h.create('2026-09-001')[0],0)
        h.disk.assert_mirrors()

    def test_filex_move_metadata_fsinfo_and_fat(self):
        h=Driver()
        self.assertEqual(h.mkdir('target'),(0,True,False))
        target=h.disk.get('target')['cluster']
        self.assertEqual(h.create('source.txt'),(0,True,False))
        self.assertEqual(h.append(b'original bytes'),(0,True,False))
        h.cpu.set_memory_block(DATA,b'\0source.txt\0')
        h.cpu.set_memory_block(DATA+256,b'\0renamed.txt\0')
        self.assertEqual(h.filex(5,length=12,aux=DATA+256,aux_length=13,
                                 source_dir=0,dest_dir=target)[0],(0,True,False))
        self.assertEqual(h.disk.contents('renamed.txt',target),b'original bytes')
        self.assertEqual(h.find('source.txt'),(0,True,False))
        self.assertEqual(h.find('target',16),(1,False,False))
        self.assertEqual(h.call(31),(0,True,False))
        self.assertEqual(h.find('renamed.txt'),(1,False,False))
        meta=bytearray(16)
        meta[:4]=bytes([16,0x27,0x22,4])
        struct.pack_into('<HH',meta,11,0x1234,0x5678)
        h.cpu.set_memory_block(DATA,meta)
        self.assertEqual(h.filex(6,length=16)[0],(0,True,False))
        entry=h.disk.get('renamed.txt',target)
        self.assertEqual(entry['attr'],0x22)
        self.assertEqual(entry['raw'][22:26],b'\x34\x12\x78\x56')
        self.assertEqual(h.filex(4,length=48)[0],(0,True,False))
        self.assertEqual(u32(h.ram,DATA+8),65536)
        self.assertEqual(h.filex(7,length=512),((0,True,False),512))
        self.assertEqual(bytes(h.ram[DATA:DATA+512]),h.disk.read(h.disk.reserved))
        self.assertEqual(h.call(75,hl=h.name('renamed.txt',b'\0')),(0,True,False))
        self.assertFalse([e for e in h.disk.entries(target) if e['name']=='renamed.txt'])
        h.disk.assert_mirrors()

    def test_sector_stream_and_video_layout(self):
        h=Driver(Disk(spc=8))
        self.assertEqual(h.create('sectors.bin',1024),(0,True,False))
        data=bytes(i%251 for i in range(1024))
        h.cpu.set_memory_block(DATA,data)
        self.assertEqual(h.call(49,hl=DATA,b=2),(0,True,False))
        self.assertEqual(h.disk.contents('sectors.bin'),data)
        self.assertEqual(h.call(33),(0,True,False))
        h.cpu.set_memory_block(DATA,bytes([0xCC])*2048)
        self.assertEqual(h.call(60,hl=DATA,b=2),(0,True,False))
        for row in range(4):
            self.assertEqual(bytes(h.ram[DATA+row*512:DATA+row*512+256]),data[row*256:row*256+256])
            self.assertEqual(bytes(h.ram[DATA+row*512+256:DATA+(row+1)*512]),bytes([0xCC])*256)
        self.assertEqual(h.cpu.hl,DATA+2048)

    def test_media_failures_and_buffer_bounds(self):
        h=Driver()
        self.assertEqual(h.create('good.bin'),(0,True,False))
        self.assertEqual(h.append(b'original'),(0,True,False))
        before=dict(h.disk.blocks)
        for buffer,length in [(0x7FFF,1),(0xC001,16384),(0xFFFF,2)]:
            self.assertNotEqual(h.call(76,hl=buffer,bc=length)[0],0)
            self.assertEqual(h.filex(1,buffer=buffer,length=length)[0][0],0x13)
        self.assertEqual(h.filex(1,buffer=PARAM,length=32)[0][0],0x13)
        self.assertEqual(before,h.disk.blocks)
        h.fail=lambda op,lba: op=='read'
        self.assertTrue(h.find('missing')[2])
        self.assertNotEqual(h.create('bad.bin')[0],0)
        self.assertEqual(before,h.disk.blocks)

    def test_append_write_failure_keeps_previous_size(self):
        h=Driver()
        self.assertEqual(h.create('file.bin'),(0,True,False))
        self.assertEqual(h.append(b'original'),(0,True,False))
        data_lba=h.disk.data_start+(h.disk.get('file.bin')['cluster']-2)
        failed=[]
        def fail_once(op,lba):
            if op=='write' and lba==data_lba and not failed:
                failed.append(lba)
                return True
            return False
        h.fail=fail_once
        self.assertNotEqual(h.append(bytes(2000))[0],0)
        self.assertTrue(failed)
        self.assertEqual(h.disk.contents('file.bin'),b'original')
        h.disk.assert_mirrors()

    def test_delete_media_failure_is_not_reported_as_zero_status(self):
        h=Driver()
        h.create('original.txt')
        h.append(b'preserved contents')
        before=dict(h.disk.blocks)
        h.fail=lambda op,lba: op=='write'
        self.assertEqual(h.call(75,hl=h.name('original.txt',b'\0')),(0xE1,False,True))
        self.assertEqual(h.disk.blocks,before)
        self.assertEqual(h.disk.contents('original.txt'),b'preserved contents')

    def test_absent_or_invalid_fsinfo_does_not_abort_file_transactions(self):
        for offset in (1,0,32,0xFFFF):
            with self.subTest(fsinfo=offset):
                disk=Disk(partition=2048)
                bpb=bytearray(disk.read(disk.start))
                struct.pack_into('<H',bpb,48,offset)
                disk.write(disk.start,bpb)
                if offset==1:
                    disk.write(disk.start+1,bytes(512))
                before=disk.read(disk.start+offset)
                h=Driver(disk)
                self.assertEqual(h.create('without-hint.bin'),(0,True,False))
                self.assertEqual(h.append(bytes(range(256))*3),(0,True,False))
                self.assertEqual(h.filex(3,offset=1000)[0],(0,True,False))
                self.assertEqual(disk.contents('without-hint.bin'),bytes(range(256))*3+bytes(232))
                self.assertEqual(h.call(75,hl=h.name('without-hint.bin',b'\0')),(0,True,False))
                self.assertEqual(disk.read(disk.start+offset),before)
                disk.assert_mirrors()

    def test_valid_fsinfo_write_error_is_propagated(self):
        h=Driver()
        h.create('failed-hint.bin')
        failures=[]
        def fail_once(op,lba):
            if op=='write' and lba==h.disk.start+1 and not failures:
                failures.append(lba)
                return True
            return False
        h.fail=fail_once
        result=h.append(b'data')
        self.assertTrue(failures)
        self.assertEqual(result[0],0xE1)
        self.assertFalse(result[1])
        self.assertEqual(h.disk.contents('failed-hint.bin'),b'')
        h.disk.assert_mirrors()

    def test_cluster_sizes_from_1_to_64_sectors(self):
        """Кластеры 512 Б..32 КиБ: каталог и файл, заведомо длиннее кластера."""
        for spc in (2,4,16,32,64):
            with self.subTest(spc=spc):
                h=Driver(Disk(spc=spc,clusters=600))
                self.assertEqual(h.mkdir('sub'),(0,True,False))
                self.assertEqual(h.find('sub',16),(1,False,False))
                self.assertEqual(h.call(31),(0,True,False))
                self.assertEqual(h.create('over.bin'),(0,True,False))
                size=spc*512+777
                data=bytes((i*37+spc)%251 for i in range(size))
                for i in range(0,size,16384):
                    self.assertEqual(h.append(data[i:i+16384]),(0,True,False))
                inside=h.disk.get('sub')['cluster']
                self.assertEqual(h.disk.contents('over.bin',inside),data)
                chain=h.disk.chain(h.disk.get('over.bin',inside)['cluster'])
                self.assertEqual(len(chain),-(-size//(spc*512)))
                h.disk.assert_mirrors()

    def test_volume_inside_extended_partition_chain(self):
        """Логический том в расширенном разделе, в том числе не первый в цепочке."""
        for skip in (0,1):
            with self.subTest(skip=skip):
                h=Driver(Disk(partition=8192,extended=63,extended_skip=skip))
                self.assertEqual(h.create('logical.bin'),(0,True,False))
                data=bytes(i%251 for i in range(5000))
                self.assertEqual(h.append(data),(0,True,False))
                self.assertEqual(h.disk.contents('logical.bin'),data)
                # Том найден именно за EBR, а не по запасному пути с LBA 0.
                self.assertTrue(any(lba==h.disk.ebr for _,lba in h.operations))
                self.assertTrue(all(lba<h.disk.start+h.disk.total
                                    for _,lba in h.operations))
                h.disk.assert_mirrors()

    def test_volume_above_16m_sectors_uses_32bit_lba(self):
        """Том за границей 2**24 секторов: старшее слово LBA участвует всерьёз."""
        h=Driver(Disk(spc=8,partition=20_000_000,clusters=4096))
        self.assertEqual(h.mkdir('deep'),(0,True,False))
        self.assertEqual(h.create('far.bin'),(0,True,False))
        data=bytes((i*11)%251 for i in range(20000))
        for i in range(0,len(data),16384):
            self.assertEqual(h.append(data[i:i+16384]),(0,True,False))
        self.assertEqual(h.disk.contents('far.bin'),data)
        self.assertTrue(all(lba>=1<<24 for _,lba in h.operations if lba))
        self.assertEqual(h.filex(1,offset=17000,buffer=DATA,length=3000),
                         ((0,True,False),3000))
        self.assertEqual(bytes(h.ram[DATA:DATA+3000]),data[17000:20000])
        h.disk.assert_mirrors()

    def test_sector_size_other_than_512_is_rejected(self):
        """512 байт в секторе — проверяемое требование, а не молчаливое допущение."""
        for value in (256,1024,4096):
            with self.subTest(bytes_per_sector=value):
                disk=Disk(partition=2048)
                boot=bytearray(disk.read(2048))
                struct.pack_into('<H',boot,11,value)
                disk.write(2048,boot)
                self.assertEqual(Driver(disk,mount=False).call(3),(1,False,True))

    def test_single_fat_and_non_mirrored_volume(self):
        """Число копий FAT и BPB_ExtFlags берутся из тома, а не подразумеваются."""
        h=Driver(Disk(fats=1))
        self.assertEqual(h.create('one.bin'),(0,True,False))
        self.assertEqual(h.append(b'single fat volume'),(0,True,False))
        self.assertEqual(h.disk.contents('one.bin'),b'single fat volume')

        h=Driver(Disk(fats=2,ext_flags=0x81))
        self.assertEqual(h.create('act.bin'),(0,True,False))
        self.assertEqual(h.append(b'active fat one'),(0,True,False))
        self.assertEqual(h.disk.contents('act.bin'),b'active fat one')
        cluster=h.disk.get('act.bin')['cluster']
        self.assertEqual(h.disk.fat(cluster,1),0x0FFFFFFF)
        # Зеркалирование выключено: неактивная копия остаётся нетронутой.
        self.assertEqual(h.disk.fat(cluster,0),0)


if __name__=='__main__':
    unittest.main()
