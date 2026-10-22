"""Проверки сохранности данных, перенесённые из WC Improved (v1.11i-2026-10-01).

Каждый тест — сценарий, в котором прежний драйвер портил свои или чужие
данные, писал не туда или зависал: испорченная цепочка FAT, отказ носителя
посреди операции, запись, которая легла, хотя драйвер получил отказ.
"""
import random
import struct
import unittest
from harness import Driver, Disk, DATA, NAME, STACK, u32

EOC = 0x0FFFFFFF
ROOT = 2


def lba_of(disk, cluster, sector=0):
    return disk.data_start + (cluster - 2) * disk.spc + sector


def fail_on(h, op, lba, nth=1, land=False):
    """Отказ на nth-м обращении op к сектору lba. land — запись всё же ложится."""
    seen = []

    def hook(kind, where):
        if kind != op or where != lba:
            return False
        seen.append(where)
        if len(seen) != nth:
            return False
        if land:
            h.disk.write(where, bytes(h.ram[h.cpu.bc:h.cpu.bc + 512]))
        return True
    h.fail = hook
    return seen


def fat_snapshot(disk):
    start = disk.start + disk.reserved
    return [disk.read(start + i) for i in range(disk.fats * disk.fat_sectors)]


def sfn_entry(name, attr=0x20, cluster=0, size=0):
    e = bytearray(32)
    e[:11] = name
    e[11] = attr
    struct.pack_into('<HI', e, 26, cluster & 0xFFFF, size)
    struct.pack_into('<H', e, 20, cluster >> 16)
    return bytes(e)


def lfn_checksum(short):
    s = 0
    for x in short:
        s = (((s & 1) << 7) + (s >> 1) + x) & 255
    return s


class StreamTests(unittest.TestCase):
    def file_with_two_clusters(self, h, name='TWO.BIN'):
        self.assertEqual(h.create(name), (0, True, False))
        data = bytes((i * 7) % 251 for i in range(1024))
        self.assertEqual(h.append(data), (0, True, False))
        chain = h.disk.chain(h.disk.get(name)['cluster'])
        self.assertEqual(len(chain), 2)
        return chain, data

    # Порча до выбора файла — FIND отказывает (#FE, проверка цепочки при
    # выборе). Порча после выбора — поток останавливается на переходе к
    # следующему кластеру (STREAM_NEXT_CLUSTER): это второй рубеж.

    def test_free_link_does_not_read_root(self):
        """Ссылка 0 (свободный кластер) прежде уводила поток в корневой каталог."""
        h = Driver()
        chain, data = self.file_with_two_clusters(h)
        h.disk.set_fat(chain[0], 0)
        self.assertEqual(h.find('TWO.BIN'), (0xFE, False, True))
        h.disk.set_fat(chain[0], chain[1])
        self.assertEqual(h.find('TWO.BIN'), (1, False, False))
        h.disk.set_fat(chain[0], 0)
        h.cpu.set_memory_block(DATA, bytes([0xCC]) * 1024)
        result = h.call(48, hl=DATA, b=2)
        self.assertTrue(result[2], result)
        self.assertEqual(bytes(h.ram[DATA:DATA + 512]), data[:512])
        self.assertEqual(bytes(h.ram[DATA + 512:DATA + 1024]), bytes([0xCC]) * 512)
        self.assertNotIn(('read', lba_of(h.disk, ROOT)), h.operations[-3:])

    def test_free_link_does_not_write_root(self):
        h = Driver()
        chain, _ = self.file_with_two_clusters(h)
        root = h.disk.read(lba_of(h.disk, ROOT))
        self.assertEqual(h.find('TWO.BIN'), (1, False, False))
        h.disk.set_fat(chain[0], 0)
        h.cpu.set_memory_block(DATA, bytes([0xEE]) * 1024)
        self.assertTrue(h.call(49, hl=DATA, b=2)[2])
        self.assertEqual(h.disk.read(lba_of(h.disk, ROOT)), root)
        self.assertEqual(h.disk.entries()[0]['name'], 'TWO.BIN')

    def test_link_beyond_data_area_is_rejected(self):
        """Последний сектор FAT держит слоты за концом данных: туда не читать."""
        h = Driver(Disk(clusters=600))
        chain, _ = self.file_with_two_clusters(h)
        h.disk.set_fat(chain[0], 600 + 5)
        self.assertEqual(h.find('TWO.BIN'), (0xFE, False, True))
        h.disk.set_fat(chain[0], chain[1])
        self.assertEqual(h.find('TWO.BIN'), (1, False, False))
        h.disk.set_fat(chain[0], 600 + 5)
        result = h.call(48, hl=DATA, b=2)
        self.assertTrue(result[2], result)
        total = h.disk.start + h.disk.total
        self.assertTrue(all(lba < total for _, lba in h.operations))


class FreeSpaceTests(unittest.TestCase):
    def test_full_volume_does_not_link_garbage(self):
        """Место кончилось посреди выделения: прежде в цепочку шёл мусор."""
        h = Driver(Disk(clusters=64))
        self.assertEqual(h.create('FILL.BIN', 50 * 512), (0, True, False))
        fill = h.disk.chain(h.disk.get('FILL.BIN')['cluster'])
        before = fat_snapshot(h.disk)
        result = h.create('BIG.BIN', 30 * 512)
        self.assertEqual(result, (16, False, True))
        self.assertEqual(h.disk.chain(h.disk.get('FILL.BIN')['cluster']), fill)
        self.assertEqual(fat_snapshot(h.disk), before)
        self.assertNotIn('BIG.BIN', [e['name'] for e in h.disk.entries()])

    def test_slots_beyond_data_area_are_not_allocated(self):
        """Свободные слоты FAT за концом области данных — не место для файла."""
        disk = Disk(clusters=200)
        for c in range(3, 202):
            disk.set_fat(c, EOC)
        h = Driver(disk)
        self.assertEqual(h.create('LAST.BIN', 512), (16, False, True))
        self.assertEqual(disk.fat(202), 0)
        total = disk.start + disk.total
        self.assertTrue(all(lba < total for _, lba in h.operations))

    def test_fragmented_allocation_over_genbu_flushes(self):
        """Цепочка длиннее GENBU (2047 кластеров): ровно нужной длины, без
        занятых кластеров. Прежде каждый сброс GENBU добавлял лишний кластер."""
        disk = Disk(clusters=20000)
        rng = random.Random(1234)
        used = {c for c in range(3, 20002) if rng.random() < 0.45}
        for c in used:
            disk.set_fat(c, EOC)
        h = Driver(disk)
        need = 5000
        self.assertEqual(h.create('FRAG.BIN', need * 512), (0, True, False))
        chain = disk.chain(disk.get('FRAG.BIN')['cluster'])
        self.assertEqual(len(chain), need)
        self.assertEqual(len(set(chain)), need)
        self.assertFalse(used & set(chain))
        disk.assert_mirrors()

    def test_chain_of_more_than_65536_clusters_and_append_walk(self):
        """32-битный счёт кластеров в MKSG и в обходе цепочки APPEND."""
        disk = Disk(clusters=70000)
        h = Driver(disk)
        h.max_events = 400000           # обход APPEND читает FAT на каждый кластер
        need = 65536 + 300
        self.assertEqual(h.create('HUGE.BIN', need * 512), (0, True, False))
        first = disk.get('HUGE.BIN')['cluster']
        self.assertEqual(len(disk.chain(first)), need)
        tail = bytes(range(200)) * 5
        self.assertEqual(h.append(tail), (0, True, False))
        entry = disk.get('HUGE.BIN')
        self.assertEqual(entry['size'], need * 512 + len(tail))
        chain = disk.chain(first)
        self.assertEqual(len(chain), need + 2)
        last = disk.cluster_data(chain[-2]) + disk.cluster_data(chain[-1])
        self.assertEqual(last[:len(tail)], tail)
        disk.assert_mirrors()


class DirectoryTests(unittest.TestCase):
    def test_root_cycle_does_not_hang(self):
        """Замкнутая цепочка каталога: поиск и создание отказывают, ничего не
        записав (прежде CREATE и MKDIR писали в такой каталог)."""
        disk = Disk()
        disk.set_fat(ROOT, ROOT)
        h = Driver(disk)
        before = dict(disk.blocks)
        self.assertTrue(h.find('ANY.BIN')[2])
        self.assertNotEqual(h.create('NEW.BIN')[0], 0)
        self.assertNotEqual(h.mkdir('SUB')[0], 0)
        self.assertEqual(disk.blocks, before)

    def test_subdirectory_chain_into_root_is_rejected(self):
        h = Driver()
        self.assertEqual(h.mkdir('SUB'), (0, True, False))
        sub = h.disk.get('SUB')['cluster']
        self.assertEqual(h.find('SUB', 16), (1, False, False))
        self.assertEqual(h.call(31), (0, True, False))
        h.disk.set_fat(sub, ROOT)
        before = dict(h.disk.blocks)
        self.assertTrue(h.find('ANY.BIN')[2])
        self.assertNotEqual(h.create('NEW.BIN')[0], 0)
        self.assertEqual(h.disk.blocks, before)

    def fill_root(self, h, count):
        # Строчное имя 8.3 — одна короткая запись (заглавные дают ещё и LFN).
        for n in range(count):
            self.assertEqual(h.create(f'f{n:03}.bin'), (0, True, False), n)

    def test_unreadable_directory_sector_is_not_overwritten(self):
        """Отказ чтения сектора каталога при создании записи — отказ носителя
        до любой записи (прежде он выглядел как «имя занято»)."""
        h = Driver()
        self.fill_root(h, 40)
        chain = h.disk.chain(ROOT)
        self.assertGreaterEqual(len(chain), 3)
        names = sorted(e['name'] for e in h.disk.entries())
        fat = fat_snapshot(h.disk)
        h.operations.clear()
        fail_on(h, 'read', lba_of(h.disk, chain[1]), nth=2)
        self.assertEqual(h.create('NEW.BIN'), (0xFF, False, True))
        self.assertFalse([op for op in h.operations if op[0] == 'write'])
        h.fail = None
        self.assertEqual(fat_snapshot(h.disk), fat)
        self.assertEqual(h.disk.chain(ROOT), chain)
        self.assertEqual(sorted(e['name'] for e in h.disk.entries()), names)

    def test_new_directory_cluster_is_zeroed_before_link(self):
        """Отказ обнуления нового кластера каталога: прежде он уже был в
        цепочке, и обрывки старых записей становились живыми."""
        h = Driver()
        self.fill_root(h, 16)
        self.assertEqual(h.disk.chain(ROOT), [ROOT])
        ghost = b''.join(sfn_entry(b'GHOST%03dBIN' % n, cluster=ROOT, size=1)
                         for n in range(16))
        target = 3
        h.disk.write(lba_of(h.disk, target), ghost)
        fail_on(h, 'write', lba_of(h.disk, target))
        self.assertEqual(h.create('NEXT.BIN'), (0xFF, False, True))
        h.fail = None
        self.assertEqual(h.disk.chain(ROOT), [ROOT])
        self.assertFalse([e for e in h.disk.entries() if 'GHOST' in e['name']])
        self.assertEqual(len(h.disk.entries()), 16)

    def test_full_8k_directory_enumeration_ends(self):
        """Каталог ровно 8 КиБ без нулевой записи прежде перечислялся без конца."""
        disk = Disk(spc=16)
        sector = b''.join(sfn_entry(b'E%05d  BIN' % n) for n in range(16))
        for s in range(16):
            disk.write(lba_of(disk, ROOT, s), sector)
        h = Driver(disk)
        self.assertEqual(h.call(32), (0, True, False))
        count = 0
        for _ in range(400):
            a, z, c = h.call(29, de=DATA)
            if z:
                break
            count += 1
        self.assertEqual(count, 256)

    def test_long_name_over_255_chars_stays_in_its_field(self):
        """LFN из 20 записей (260 знаков) прежде писал за поле имени."""
        short = b'LONGNA~1TXT'
        crc = lfn_checksum(short)
        text = ('n' * 260).encode('utf-16-le')
        records = []
        for seq in range(20, 0, -1):
            rec = bytearray(32)
            rec[0] = seq | (0x40 if seq == 20 else 0)
            part = text[(seq - 1) * 26:seq * 26]
            rec[1:11], rec[14:26], rec[28:32] = part[:10], part[10:22], part[22:26]
            rec[11], rec[13] = 0x0F, crc
            records.append(bytes(rec))
        raw = b''.join(records) + sfn_entry(short)
        raw += bytes(1024 - len(raw))
        h = Driver(Disk(spc=2))
        h.disk.write(lba_of(h.disk, ROOT, 0), raw[:512])
        h.disk.write(lba_of(h.disk, ROOT, 1), raw[512:1024])
        self.assertEqual(h.call(32), (0, True, False))
        h.cpu.set_memory_block(DATA, bytes([0xCC]) * 600)
        result = h.call(30, a=0, de=DATA)
        self.assertFalse(result[1], result)
        name = bytes(h.ram[DATA + 1:DATA + 1 + 256])
        self.assertEqual(name[:255], b'n' * 255)
        self.assertEqual(name[255], 0)
        self.assertEqual(bytes(h.ram[DATA + 257:DATA + 600]), bytes([0xCC]) * 343)

    def test_short_name_extension_stops_at_name_end(self):
        """Короткое имя к длинному «Long Readme.md»: расширение прежде было
        «MD»+#00 — недопустимый в DIR_Name байт."""
        h = Driver()
        for name in ('Long Readme.md', 'Picture.c'):
            self.assertEqual(h.create(name), (0, True, False))
        self.assertEqual(h.disk.get('Long Readme.md')['short'][8:11], b'MD ')
        self.assertEqual(h.disk.get('Picture.c')['short'][8:11], b'C  ')


class EntryTests(unittest.TestCase):
    def test_created_entry_that_landed_keeps_its_chain(self):
        """Запись каталога легла, а драйвер получил отказ: прежде цепочка
        освобождалась под живой записью."""
        h = Driver()
        fail_on(h, 'write', lba_of(h.disk, ROOT), land=True)
        self.assertEqual(h.create('DATA.BIN', 1000), (0, True, False))
        h.fail = None
        entry = h.disk.get('DATA.BIN')
        self.assertEqual(len(h.disk.chain(entry['cluster'])), 2)

    def test_created_entry_that_did_not_land_frees_chain(self):
        h = Driver()
        fat = fat_snapshot(h.disk)
        fail_on(h, 'write', lba_of(h.disk, ROOT))
        self.assertTrue(h.create('DATA.BIN', 1000)[2])
        h.fail = None
        self.assertFalse(h.disk.entries())
        self.assertEqual(fat_snapshot(h.disk), fat)

    def test_mkdir_body_failure_publishes_nothing(self):
        """Отказ записи тела каталога: прежде запись в родителе уже стояла,
        а тело оставалось мусором свободного кластера."""
        h = Driver()
        ghost = b''.join(sfn_entry(b'GHOST%03dBIN' % n, cluster=ROOT, size=1)
                         for n in range(16))
        h.disk.write(lba_of(h.disk, 3), ghost)
        fail_on(h, 'write', lba_of(h.disk, 3))
        self.assertEqual(h.mkdir('SUB'), (0xFF, False, True))
        h.fail = None
        self.assertFalse(h.disk.entries())
        self.assertEqual(h.disk.fat(3), 0)

    def test_mkdir_parent_entry_that_landed_is_kept(self):
        h = Driver()
        fail_on(h, 'write', lba_of(h.disk, ROOT), land=True)
        self.assertEqual(h.mkdir('SUB'), (0, True, False))
        h.fail = None
        sub = h.disk.get('SUB')
        self.assertTrue(sub['attr'] & 0x10)
        body = h.disk.cluster_data(sub['cluster'])
        self.assertEqual(body[:11], b'.          ')
        self.assertEqual(body[32:43], b'..         ')
        self.assertEqual(h.disk.chain(sub['cluster']), [sub['cluster']])

    def test_append_entry_that_landed_is_committed(self):
        """Запись каталога при APPEND легла, драйвер получил отказ: прежде
        откат освобождал цепочку под записью с новым размером."""
        h = Driver()
        self.assertEqual(h.create('APP.BIN'), (0, True, False))
        data = bytes((i * 3) % 251 for i in range(1000))
        fail_on(h, 'write', lba_of(h.disk, ROOT), land=True)
        self.assertEqual(h.append(data), (0, True, False))
        h.fail = None
        self.assertEqual(h.disk.contents('APP.BIN'), data)
        more = b'tail'
        self.assertEqual(h.append(more), (0, True, False))
        self.assertEqual(h.disk.contents('APP.BIN'), data + more)
        h.disk.assert_mirrors()

    def test_append_entry_that_did_not_land_rolls_back(self):
        h = Driver()
        self.assertEqual(h.create('APP.BIN'), (0, True, False))
        fat = fat_snapshot(h.disk)
        fail_on(h, 'write', lba_of(h.disk, ROOT))
        self.assertEqual(h.append(bytes(1000)), (0xE1, False, False))
        h.fail = None
        self.assertEqual(h.disk.contents('APP.BIN'), b'')
        self.assertEqual(fat_snapshot(h.disk), fat)

    def rename(self, h, old, new):
        h.cpu.set_memory_block(DATA, new.encode('cp866') + b'\0')
        return h.call(74, hl=h.name(old, b'\0'), de=DATA)

    def rename_setup(self):
        h = Driver(Disk(spc=2))
        self.assertEqual(h.create('OLD.BIN'), (0, True, False))
        self.assertEqual(h.append(b'keep me'), (0, True, False))
        for n in range(15):
            self.assertEqual(h.create(f'F{n:03}.BIN'), (0, True, False))
        cluster = h.disk.get('OLD.BIN')['cluster']
        return h, cluster

    def test_rename_completes_when_old_entry_write_fails_once(self):
        """Прежнюю запись удалить по имени не удалось (запись её сектора не
        легла): переименование доводится — её короткая запись помечается по
        месту (R8-1). Прежде (до 2026-10-06) на цепочку смотрели две записи."""
        h, cluster = self.rename_setup()
        fail_on(h, 'write', lba_of(h.disk, ROOT, 0))
        self.assertEqual(self.rename(h, 'OLD.BIN', 'NEW.BIN'), (0, True, False))
        h.fail = None
        refs = [e['name'] for e in h.disk.entries() if e['cluster'] == cluster]
        self.assertEqual(refs, ['NEW.BIN'])
        self.assertEqual(h.disk.contents('NEW.BIN'), b'keep me')

    def test_rename_rolls_back_when_old_entry_stays(self):
        """Сектор прежней записи не пишется совсем: новая запись удаляется,
        на цепочку смотрит одна прежняя — две записи на одной цепочке удаление
        любой превратило бы в потерю данных другой."""
        h, cluster = self.rename_setup()
        old_sector = lba_of(h.disk, ROOT, 0)
        h.fail = lambda op, lba: op == 'write' and lba == old_sector
        result = self.rename(h, 'OLD.BIN', 'NEW.BIN')
        self.assertTrue(result[2], result)
        h.fail = None
        refs = [e['name'] for e in h.disk.entries() if e['cluster'] == cluster]
        self.assertEqual(refs, ['OLD.BIN'])
        self.assertEqual(h.disk.contents('OLD.BIN'), b'keep me')

    def test_rename_completes_when_old_entry_deletion_landed(self):
        h, cluster = self.rename_setup()
        fail_on(h, 'write', lba_of(h.disk, ROOT, 0), land=True)
        result = self.rename(h, 'OLD.BIN', 'NEW.BIN')
        self.assertEqual(result, (0, True, False))
        h.fail = None
        refs = [e['name'] for e in h.disk.entries() if e['cluster'] == cluster]
        self.assertEqual(refs, ['NEW.BIN'])
        self.assertEqual(h.disk.contents('NEW.BIN'), b'keep me')

    def test_delete_does_not_free_root(self):
        """Испорченная запись файла на кластере корня: прежде удаление
        освобождало корень, и том терял все имена."""
        h = Driver()
        self.assertEqual(h.create('A.BIN'), (0, True, False))
        self.assertEqual(h.append(b'x'), (0, True, False))
        self.assertEqual(h.create('B.BIN'), (0, True, False))
        sector = bytearray(h.disk.read(lba_of(h.disk, ROOT)))
        at = bytes(sector).index(h.disk.get('A.BIN')['short'])
        struct.pack_into('<H', sector, at + 20, 0)
        struct.pack_into('<H', sector, at + 26, ROOT)
        h.disk.write(lba_of(h.disk, ROOT), bytes(sector))
        result = h.call(75, hl=h.name('A.BIN', b'\0'))
        self.assertTrue(result[2], result)
        self.assertEqual(h.disk.fat(ROOT), EOC)
        self.assertIn('B.BIN', [e['name'] for e in h.disk.entries()])



class FilexTests(unittest.TestCase):
    def move(self, h, old, new, source_dir=0, dest_dir=0, flags=0, kind=0):
        src = bytes([kind]) + old.encode('cp866') + b'\0'
        dst = bytes([kind]) + new.encode('cp866') + b'\0'
        h.cpu.set_memory_block(DATA, src)
        h.cpu.set_memory_block(DATA + 256, dst)
        return h.filex(5, length=len(src), aux=DATA + 256, aux_length=len(dst),
                       source_dir=source_dir, dest_dir=dest_dir, flags=flags)[0]

    def refs(self, disk, cluster, directories):
        return [(d, e['name']) for d in directories for e in disk.entries(d)
                if e['cluster'] == cluster]

    def test_shrink_writes_no_file_data(self):
        """Усечение пишет только элемент каталога и FAT: хвост сектора за
        новым концом не обнуляется. Отказ записи элемента — файл прежний
        целиком."""
        for fail in (False, True):
            with self.subTest(fail_entry=fail):
                h = Driver()
                self.assertEqual(h.create('file.bin'), (0, True, False))
                data = bytes((i * 13) % 251 for i in range(1000))
                self.assertEqual(h.append(data), (0, True, False))
                chain = h.disk.chain(h.disk.get('file.bin')['cluster'])
                self.assertEqual(h.find('file.bin'), (1, False, False))
                if fail:
                    fail_on(h, 'write', lba_of(h.disk, ROOT))
                h.operations.clear()
                status = h.filex(3, offset=700)[0][0]
                h.fail = None
                written = {lba for op, lba in h.operations if op == 'write'}
                self.assertFalse(written & {lba_of(h.disk, c) for c in chain})
                if fail:
                    self.assertNotEqual(status, 0)
                    self.assertEqual(h.disk.contents('file.bin'), data)
                else:
                    self.assertEqual(status, 0)
                    self.assertEqual(h.disk.contents('file.bin'), data[:700])

    def test_shrink_completes_when_entry_write_landed(self):
        """Запись ENTRY с новым размером легла, драйвер получил отказ: прежде
        хвост цепочки оставался за коротким размером."""
        h = Driver()
        self.assertEqual(h.create('file.bin'), (0, True, False))
        data = bytes((i * 13) % 251 for i in range(1000))
        self.assertEqual(h.append(data), (0, True, False))
        chain = h.disk.chain(h.disk.get('file.bin')['cluster'])
        self.assertEqual(h.find('file.bin'), (1, False, False))
        fail_on(h, 'write', lba_of(h.disk, ROOT), land=True)
        self.assertEqual(h.filex(3, offset=300)[0], (0, True, False))
        h.fail = None
        self.assertEqual(h.disk.contents('file.bin'), data[:300])
        self.assertEqual(h.disk.chain(chain[0]), chain[:1])
        self.assertEqual(h.disk.fat(chain[1]), 0)
        h.disk.assert_mirrors()

    def test_move_rejects_trailing_space_or_dot(self):
        h = Driver()
        self.assertEqual(h.create('file.bin'), (0, True, False))
        before = dict(h.disk.blocks)
        for name in ('new ', 'new.', '..'):
            self.assertEqual(self.move(h, 'file.bin', name)[0], 0x1C, name)
        self.assertEqual(h.disk.blocks, before)

    def test_move_rolls_back_destination_link_that_landed(self):
        """Ссылка назначения легла, драйвер получил отказ: прежде на цепочку
        оставались две ссылки."""
        h = Driver()
        self.assertEqual(h.mkdir('target'), (0, True, False))
        target = h.disk.get('target')['cluster']
        self.assertEqual(h.create('src.bin'), (0, True, False))
        self.assertEqual(h.append(b'payload'), (0, True, False))
        chain = h.disk.get('src.bin')['cluster']
        fail_on(h, 'write', lba_of(h.disk, target), land=True)
        self.assertNotEqual(self.move(h, 'src.bin', 'dst.bin', dest_dir=target)[0], 0)
        h.fail = None
        self.assertEqual(self.refs(h.disk, chain, (ROOT, target)), [(ROOT, 'SRC.BIN')])
        self.assertEqual(h.disk.contents('src.bin'), b'payload')

    def test_move_completes_when_source_short_entry_write_fails_once(self):
        """Удаление источника: LFN стёрт, сектор SFN не записан. Прежде поиск
        по имени его не находил, и MOVE кончался с двумя ссылками на цепочке.
        Теперь перенос доводится: короткая запись источника — #E5 по месту
        (R8-2), статус COMMITTED_CLEANUP, ссылка одна — в назначении."""
        h = Driver(Disk(spc=2))
        self.assertEqual(h.mkdir('target'), (0, True, False))
        for n in range(13):
            self.assertEqual(h.create(f'f{n:02}.bin'), (0, True, False))
        self.assertEqual(h.create('Source File.txt'), (0, True, False))
        self.assertEqual(h.append(b'source data'), (0, True, False))
        entry = h.disk.get('Source File.txt')
        self.assertIn(entry['short'], h.disk.read(lba_of(h.disk, ROOT, 1))[:32])
        target = h.disk.get('target')['cluster']
        fail_on(h, 'write', lba_of(h.disk, ROOT, 1))
        status = self.move(h, 'Source File.txt', 'moved.txt', dest_dir=target)[0]
        self.assertEqual(status, 0x25)
        h.fail = None
        refs = self.refs(h.disk, entry['cluster'], (ROOT, target))
        self.assertEqual(refs, [(target, 'MOVED.TXT')])
        self.assertEqual(h.disk.contents('moved.txt', target), b'source data')

    def test_move_keeps_source_when_its_short_entry_survives(self):
        """Сектор короткой записи источника не пишется совсем: перенос
        откатывается — ссылка одна, прежняя."""
        h = Driver(Disk(spc=2))
        self.assertEqual(h.mkdir('target'), (0, True, False))
        for n in range(13):
            self.assertEqual(h.create(f'f{n:02}.bin'), (0, True, False))
        self.assertEqual(h.create('Source File.txt'), (0, True, False))
        self.assertEqual(h.append(b'source data'), (0, True, False))
        entry = h.disk.get('Source File.txt')
        target = h.disk.get('target')['cluster']
        source_sector = lba_of(h.disk, ROOT, 1)
        h.fail = lambda op, lba: op == 'write' and lba == source_sector
        status = self.move(h, 'Source File.txt', 'moved.txt', dest_dir=target)[0]
        self.assertNotIn(status, (0, 0x25))
        h.fail = None
        refs = self.refs(h.disk, entry['cluster'], (ROOT, target))
        self.assertEqual(len(refs), 1, refs)
        self.assertEqual(refs[0][0], ROOT)

    def test_replace_restores_destination_when_its_write_landed(self):
        """Перезапись назначения: запись легла, драйвер получил отказ. Прежде
        на цепочку источника смотрели обе записи, цепочка назначения терялась."""
        h = Driver()
        self.assertEqual(h.create('src.bin'), (0, True, False))
        self.assertEqual(h.append(b'AAAA'), (0, True, False))
        self.assertEqual(h.create('dst.bin'), (0, True, False))
        self.assertEqual(h.append(b'BBBB'), (0, True, False))
        fail_on(h, 'write', lba_of(h.disk, ROOT), land=True)
        self.assertNotEqual(self.move(h, 'src.bin', 'dst.bin', flags=1)[0], 0)
        h.fail = None
        self.assertEqual(h.disk.contents('src.bin'), b'AAAA')
        self.assertEqual(h.disk.contents('dst.bin'), b'BBBB')

    def test_directory_move_restores_dotdot_that_landed(self):
        """«..» перенесённого каталога лёг, драйвер получил отказ: прежде откат
        удалял новую ссылку, не вернув «..» прежнему родителю."""
        h = Driver()
        self.assertEqual(h.mkdir('a'), (0, True, False))
        self.assertEqual(h.mkdir('b'), (0, True, False))
        a = h.disk.get('a')['cluster']
        b = h.disk.get('b')['cluster']
        fail_on(h, 'write', lba_of(h.disk, a), land=True)
        self.assertNotEqual(self.move(h, 'a', 'a', dest_dir=b, kind=0x10)[0], 0)
        h.fail = None
        self.assertIn('A', [e['name'] for e in h.disk.entries()])
        self.assertFalse(h.disk.entries(b)[2:] if len(h.disk.entries(b)) > 2 else [])
        dotdot = h.disk.cluster_data(a)[32:64]
        self.assertEqual(dotdot[:2], b'..')
        self.assertEqual(struct.unpack_from('<H', dotdot, 26)[0]
                         | struct.unpack_from('<H', dotdot, 20)[0] << 16, 0)


def dir_sector(*entries):
    return b''.join(entries).ljust(512, b'\0')


def raw_file(disk, cluster=3, size=1024, attr=0x20):
    disk.write(lba_of(disk, ROOT), dir_sector(
        sfn_entry(b'FILE    BIN', attr=attr, cluster=cluster, size=size)))


DELETED = bytes([0xE5]) + bytes(31)
LONG_NAME = 'Source ' + 'x' * 220 + '.txt'


class ReviewTests(unittest.TestCase):
    """Случаи перепроверки 2026-10-06 (R1–R10): на прежнем драйвере и на
    первой версии переноса каждый портил данные, писал не туда или висел."""

    def test_long_name_over_three_sectors_is_found(self):
        """R1: записи LFN в трёх секторах — поиск проходит позапрошлый сектор."""
        h = Driver(Disk(spc=2))
        for n in range(14):
            self.assertEqual(h.create(f'f{n:02}.bin'), (0, True, False))
        self.assertEqual(h.create(LONG_NAME, 900), (0, True, False))
        self.assertEqual(h.find(LONG_NAME), (1, False, False))
        self.assertEqual(h.call(75, hl=h.name(LONG_NAME, b'\0')), (0, True, False))
        self.assertFalse([e for e in h.disk.entries() if e['name'] == LONG_NAME])

    def test_landed_long_name_entry_keeps_its_chain(self):
        """R1: такая запись легла при отказе — цепочку не освобождать."""
        h = Driver(Disk(spc=2))
        for n in range(14):
            self.assertEqual(h.create(f'f{n:02}.bin'), (0, True, False))
        # Кластер 3 — файл, 4 — продление корня: вторая запись в 4 — SFN.
        fail_on(h, 'write', lba_of(h.disk, 4), nth=2, land=True)
        self.assertEqual(h.create(LONG_NAME, 900), (0, True, False))
        h.fail = None
        entry = h.disk.get(LONG_NAME)
        self.assertEqual(h.disk.chain(entry['cluster']), [entry['cluster']])

    def test_delete_does_not_free_root_tail(self):
        """R2: запись файла на втором кластере корня — корень не освобождается."""
        disk = Disk()
        disk.set_fat(ROOT, 3)
        disk.set_fat(3, EOC)
        disk.write(lba_of(disk, ROOT), dir_sector(
            sfn_entry(b'FILE    BIN', cluster=3, size=512), *[DELETED] * 15))
        disk.write(lba_of(disk, 3), dir_sector(sfn_entry(b'OTHER   TXT')))
        h = Driver(disk)
        self.assertTrue(h.call(75, hl=h.name('file.bin', b'\0'))[2])
        self.assertEqual(disk.chain(ROOT), [ROOT, 3])
        self.assertIn('OTHER.TXT', [e['name'] for e in disk.entries()])

    def test_subdirectory_on_root_tail_is_not_written(self):
        """R2: подкаталог на втором кластере корня — запись туда не идёт."""
        disk = Disk()
        disk.set_fat(ROOT, 3)
        disk.set_fat(3, EOC)
        disk.write(lba_of(disk, ROOT), dir_sector(
            sfn_entry(b'SUB        ', attr=0x10, cluster=3), *[DELETED] * 15))
        disk.write(lba_of(disk, 3), dir_sector(sfn_entry(b'OTHER   TXT')))
        h = Driver(disk)
        self.assertEqual(h.find('sub', 16), (1, False, False))
        self.assertEqual(h.call(31), (0, True, False))
        before = dict(disk.blocks)
        self.assertTrue(h.create('alien.bin')[2])
        self.assertTrue(h.find('other.txt')[2])
        self.assertEqual(disk.blocks, before)

    def test_file_on_root_is_not_selected(self):
        """R3: файл на кластере корня (или с хвостом в корне) не выбирается:
        ни чтение корня как данных, ни запись поверх корня."""
        for chain in ([ROOT], [3, ROOT]):
            with self.subTest(chain=chain):
                disk = Disk()
                if chain[0] != ROOT:
                    disk.set_fat(chain[0], ROOT)
                raw_file(disk, chain[0], 1024)
                h = Driver(disk)
                before = dict(disk.blocks)
                self.assertEqual(h.find('file.bin'), (0xFE, False, True))
                h.operations.clear()
                h.cpu.set_memory_block(DATA, b'X' * 1024)
                self.assertTrue(h.call(49, hl=DATA, b=2)[2])
                self.assertTrue(h.call(48, hl=DATA, b=2)[2])
                self.assertFalse(h.operations)
                self.assertEqual(disk.blocks, before)

    def cyclic(self):
        disk = Disk()
        disk.set_fat(3, 4)
        disk.set_fat(4, 3)
        disk.write(lba_of(disk, ROOT), dir_sector(
            sfn_entry(b'FILE    BIN', cluster=3, size=1024),
            sfn_entry(b'GOOD    BIN', cluster=5, size=10)))
        disk.set_fat(5, EOC)
        disk.write(lba_of(disk, 3), b'A' * 512)
        disk.write(lba_of(disk, 4), b'B' * 512)
        return disk

    def test_cyclic_chain_is_not_selected(self):
        """R4, R5: цикл в цепочке — файл не выбирается, ничего не меняется."""
        disk = self.cyclic()
        h = Driver(disk)
        before = dict(disk.blocks)
        self.assertEqual(h.find('file.bin'), (0xFE, False, True))
        self.assertTrue(h.append(b'C' * 50)[0])
        self.assertNotEqual(h.filex(3, offset=512)[0][0], 0)
        self.assertEqual(disk.blocks, before)

    def test_cyclic_chain_from_move_context_is_refused(self):
        """R4, R5: контекст файла после MOVE (без FIND) — та же проверка в
        APPEND и FILEX: дозапись и усечение отказывают, ничего не меняя."""
        disk = self.cyclic()
        h = Driver(disk)
        self.assertEqual(h.find('good.bin'), (1, False, False))
        self.assertEqual(FilexTests().move(h, 'file.bin', 'moved.bin')[0], 0)
        moved = dict(disk.blocks)
        self.assertEqual(h.append(b'C' * 50)[0], 0x25)
        self.assertEqual(h.filex(3, offset=512)[0][0], 0x20)
        self.assertEqual(disk.blocks, moved)
        self.assertEqual(disk.fat(3), 4)

    def test_append_after_unknown_commit_needs_new_find(self):
        """R6: запись каталога легла, перечитать её не удалось — контекст
        снят: повторный APPEND без FIND не пишет поверх легших данных."""
        h = Driver()
        self.assertEqual(h.create('file.bin'), (0, True, False))
        self.assertEqual(h.append(b'A' * 100), (0, True, False))
        root = lba_of(h.disk, ROOT)
        landed = []

        def fault(op, lba):
            if lba != root:
                return False
            if op == 'write' and not landed:
                h.disk.write(lba, bytes(h.ram[h.cpu.bc:h.cpu.bc + 512]))
                landed.append(lba)
                return True
            return op == 'read' and bool(landed)
        h.fail = fault
        self.assertNotEqual(h.append(b'B' * 100)[0], 0)
        h.fail = None
        data = b'A' * 100 + b'B' * 100
        self.assertEqual(h.disk.contents('file.bin'), data)
        # Выбор файла снят целиком: ни APPEND, ни секторная запись (R2-3).
        self.assertEqual(h.append(b'C' * 100)[0], 0xF1)
        h.cpu.set_memory_block(DATA, b'X' * 512)
        self.assertEqual(h.call(49, hl=DATA, b=1), (0xF1, False, True))
        self.assertEqual(h.disk.contents('file.bin'), data)
        self.assertEqual(h.find('file.bin'), (1, False, False))
        self.assertEqual(h.append(b'C' * 100), (0, True, False))
        self.assertEqual(h.disk.contents('file.bin'), data + b'C' * 100)

    def test_replace_into_cyclic_empty_directory_returns(self):
        """R7: проверка пустоты каталога назначения с замкнутой цепочкой."""
        disk = Disk()
        disk.set_fat(3, EOC)
        disk.set_fat(4, 4)
        disk.write(lba_of(disk, ROOT), dir_sector(
            sfn_entry(b'SRC        ', attr=0x10, cluster=3),
            sfn_entry(b'DST        ', attr=0x10, cluster=4)))
        disk.write(lba_of(disk, 4), dir_sector(
            sfn_entry(b'.          ', attr=0x10, cluster=4),
            sfn_entry(b'..         ', attr=0x10), *[DELETED] * 14))
        h = Driver(disk)
        h.max_events = 300
        before = dict(disk.blocks)
        status = FilexTests().move(h, 'src', 'dst', flags=1, kind=0x10)
        self.assertEqual(status[0], 0x20)
        self.assertEqual(disk.blocks, before)

    def test_volume_without_data_area_is_not_mounted(self):
        """R8: BPB с концом тома внутри FAT не монтируется."""
        disk = Disk()
        boot = bytearray(disk.read(0))
        struct.pack_into('<I', boot, 32, 100)
        disk.write(0, boot)
        h = Driver(disk, mount=False)
        self.assertTrue(h.call(3)[2])
        h.operations.clear()
        self.assertTrue(h.create('new.bin')[2])
        self.assertFalse([op for op in h.operations if op[0] == 'write'])

    def test_rejected_cluster_one_does_not_stop_allocation(self):
        """R9: отвергнутый кластер 1 не становится подсказкой поиска места."""
        disk = Disk()
        raw_file(disk, 1, 512)
        h = Driver(disk)
        self.assertTrue(h.call(75, hl=h.name('file.bin', b'\0'))[2])
        self.assertEqual(h.create('new.bin', 512), (0, True, False))
        self.assertEqual(len(disk.chain(disk.get('new.bin')['cluster'])), 1)

    def test_rename_keeps_attributes(self):
        """R10: «только чтение», «скрытый», «системный», «архив» сохраняются."""
        disk = Disk()
        disk.set_fat(3, EOC)
        raw_file(disk, 3, 512, attr=0x27)
        disk.write(lba_of(disk, 3), b'A' * 512)
        h = Driver(disk)
        h.cpu.set_memory_block(DATA, b'new.bin\0')
        self.assertEqual(h.call(74, hl=h.name('file.bin', b'\0'), de=DATA), (0, True, False))
        self.assertEqual(disk.get('new.bin')['attr'], 0x27)
        self.assertEqual(h.find('new.bin'), (1, False, False))
        h.cpu.set_memory_block(DATA, b'Z' * 512)
        self.assertTrue(h.call(49, hl=DATA, b=1)[2])
        self.assertEqual(disk.read(lba_of(disk, 3)), b'A' * 512)


class ReviewTests2(unittest.TestCase):
    """Случаи перепроверки 2026-10-06, второй круг (R2-2…R2-7)."""

    def test_write_after_filex_move_needs_reopen(self):
        """R2-2: после FILEX MOVE поток ядра стоит на каталоге — WRITE без
        нового открытия отказывает, а после открытия пишет в сам файл."""
        h = Driver(Disk(spc=2))
        self.assertEqual(h.create('file.bin', 512), (0, True, False))
        for n in range(15):
            self.assertEqual(h.create(f'f{n:02}.bin'), (0, True, False))
        self.assertEqual(h.create('victim.bin'), (0, True, False))
        self.assertEqual(h.find('file.bin'), (1, False, False))
        self.assertEqual(FilexTests().move(h, 'file.bin', 'new.bin')[0], 0)
        root = b''.join(h.disk.read(lba_of(h.disk, ROOT, s)) for s in range(2))
        h.cpu.set_memory_block(DATA, b'X' * 512)
        self.assertEqual(h.call(49, hl=DATA, b=1), (0xF1, False, True))
        self.assertEqual(b''.join(h.disk.read(lba_of(h.disk, ROOT, s)) for s in range(2)), root)
        self.assertEqual(h.call(33), (0, True, False))
        self.assertEqual(h.call(49, hl=DATA, b=1)[2], False)
        self.assertEqual(h.disk.contents('new.bin'), b'X' * 512)
        self.assertIn('VICTIM.BIN', [e['name'] for e in h.disk.entries()])

    def test_write_after_append_continues_the_file(self):
        """R2-2: WRITE после дозаписи продолжает файл с позиции потока
        (прежде он писал поверх дозаписанных данных); FAT не затрагивается."""
        h = Driver(Disk(spc=4))
        self.assertEqual(h.create('file.bin', 2048), (0, True, False))
        self.assertEqual(h.find('file.bin'), (1, False, False))
        fat = fat_snapshot(h.disk)
        h.cpu.set_memory_block(DATA, b'1' * 512)
        self.assertEqual(h.call(49, hl=DATA, b=1)[2], False)
        self.assertEqual(h.append(b'T' * 100), (0, True, False))
        h.cpu.set_memory_block(DATA, b'2' * 512)
        self.assertEqual(h.call(49, hl=DATA, b=1)[2], False)
        data = h.disk.contents('file.bin')
        self.assertEqual(data[:1024], b'1' * 512 + b'2' * 512)
        self.assertEqual(data[2048:], b'T' * 100)
        first = h.disk.get('file.bin')['cluster']
        self.assertEqual(len(h.disk.chain(first)), 2)
        h.disk.assert_mirrors()
        changed = [i for i, (a, b) in enumerate(zip(fat, fat_snapshot(h.disk))) if a != b]
        self.assertTrue(all(i % h.disk.fat_sectors == 0 for i in changed), changed)

    def test_read_continues_after_other_operations(self):
        """R2-2: чтение по секторам с FILEX между вызовами (READ_AT того же файла
        и сведения о ФС двигают поток ядра) — поток файла продолжается с места."""
        h = Driver(Disk(spc=4))
        data = bytes((i * 7) % 251 for i in range(4096))
        self.assertEqual(h.create('data.bin'), (0, True, False))
        self.assertEqual(h.append(data), (0, True, False))
        self.assertEqual(h.find('data.bin'), (1, False, False))
        got = b''
        for n in range(8):
            self.assertEqual(h.call(48, hl=DATA, b=1)[2], False, n)
            got += bytes(h.ram[DATA:DATA + 512])
            self.assertEqual(h.filex(1, offset=3000, buffer=DATA + 4096, length=100),
                             ((0, True, False), 100))
            self.assertEqual(h.filex(4, buffer=DATA + 4096, length=48)[0], (0, True, False))
        self.assertEqual(got, data)

    def test_rename_publishes_full_attribute_at_once(self):
        """R2-4: новая запись RENAME сразу пишется с полным атрибутом; отдельной
        записи атрибута после удаления прежнего имени нет."""
        disk = Disk()
        disk.set_fat(3, EOC)
        raw_file(disk, 3, 512, attr=0x27)
        disk.write(lba_of(disk, 3), b'A' * 512)
        h = Driver(disk)
        published = []

        def hook(op, lba):
            if op == 'write':
                buf = bytes(h.ram[h.cpu.bc:h.cpu.bc + 512])
                for at in range(0, 512, 32):
                    if buf[at:at + 11] == b'NEW     BIN':
                        published.append(buf[at + 11])
            return False
        h.fail = hook
        h.cpu.set_memory_block(DATA, b'new.bin\0')
        self.assertEqual(h.call(74, hl=h.name('file.bin', b'\0'), de=DATA), (0, True, False))
        self.assertTrue(published)
        self.assertEqual(set(published), {0x27})
        self.assertEqual(disk.get('new.bin')['attr'], 0x27)

    def test_append_refuses_read_only_file(self):
        """R2-5: «только чтение» — дозапись отказывает (#28), том не меняется."""
        disk = Disk()
        disk.set_fat(3, EOC)
        raw_file(disk, 3, 100, attr=0x21)
        h = Driver(disk)
        self.assertEqual(h.find('file.bin'), (1, False, False))
        before = dict(disk.blocks)
        self.assertEqual(h.append(b'B' * 50)[0], 0x28)
        self.assertEqual(disk.blocks, before)

    def mount_writes(self, disk):
        h = Driver(disk, mount=False)
        mounted = h.call(3)
        h.operations.clear()
        created = h.create('new.bin', 512)
        return mounted, created, [lba for op, lba in h.operations if op == 'write']

    def test_volume_with_fat_area_overflow_is_not_mounted(self):
        """R2-6: резерв + FAT×размер с переносом за 2**32 — том не монтируется."""
        disk = Disk()
        boot = bytearray(disk.read(0))
        struct.pack_into('<I', boot, 32, 1000)
        struct.pack_into('<I', boot, 36, 0x80000001)
        disk.write(0, boot)
        mounted, created, writes = self.mount_writes(disk)
        self.assertTrue(mounted[2])
        self.assertFalse(writes)

    def test_volume_past_32bit_lba_is_not_mounted(self):
        """R2-6: начало раздела + длина тома за 2**32 — не монтируется."""
        mounted, created, writes = self.mount_writes(Disk(partition=0xFFFFFC00))
        self.assertTrue(mounted[2])
        self.assertFalse(writes)

    def test_volume_longer_than_its_partition_is_not_mounted(self):
        """R2-6: BPB длиннее записи раздела MBR — не монтируется."""
        disk = Disk(partition=2048)
        mbr = bytearray(disk.read(0))
        struct.pack_into('<I', mbr, 458, 1000)
        disk.write(0, mbr)
        mounted, created, writes = self.mount_writes(disk)
        self.assertTrue(mounted[2])
        self.assertFalse(writes)

    def test_replace_refuses_crosslinked_destination(self):
        """R2-7: у источника и назначения общий хвост — замена отказывает, не
        освобождая хвост живого источника."""
        disk = Disk()
        disk.set_fat(3, 4)
        disk.set_fat(4, EOC)
        disk.write(lba_of(disk, ROOT), dir_sector(
            sfn_entry(b'FILE    BIN', cluster=3, size=1024),
            sfn_entry(b'DEST    BIN', cluster=4, size=512)))
        h = Driver(disk)
        before = dict(disk.blocks)
        self.assertEqual(FilexTests().move(h, 'file.bin', 'dest.bin', flags=1)[0], 0x20)
        self.assertEqual(disk.blocks, before)

    def long_root(self, extra=70, base=100):
        """Корень из 1 + extra кластеров (2, base, base+1, …) без записей."""
        disk = Disk()
        chain = [ROOT] + list(range(base, base + extra))
        for a, b in zip(chain, chain[1:] + [EOC]):
            disk.set_fat(a, b)
        return disk, chain

    def test_long_root_tail_is_protected(self):
        """Корень длиннее таблицы (64 кластера): кластер его хвоста и как файл
        не выбирается, и при удалении не освобождается."""
        disk, chain = self.long_root()
        tail = chain[-1]
        disk.write(lba_of(disk, ROOT), dir_sector(
            sfn_entry(b'FILE    BIN', cluster=tail, size=512),
            sfn_entry(b'GOOD    BIN', cluster=5, size=512)))
        disk.set_fat(5, EOC)
        h = Driver(disk)
        self.assertEqual(h.find('good.bin'), (1, False, False))
        self.assertEqual(h.find('file.bin'), (0xFE, False, True))
        self.assertTrue(h.call(75, hl=h.name('file.bin', b'\0'))[2])
        self.assertEqual(disk.chain(ROOT), chain)

    def test_root_table_is_built_once_per_mount(self):
        """Таблица корня строится один раз: поиск в подкаталоге и проверка
        цепочки файла не читают цепочку корня заново; продление каталога её
        перестраивает. (Поиск в самом корне проходит его цепочку всегда — это
        проверка каталога DIR_CHAIN.)"""
        disk, chain = self.long_root(10, base=300)      # хвост корня — сектор FAT 2
        h = Driver(disk)
        self.assertEqual(h.mkdir('sub'), (0, True, False))
        self.assertEqual(h.find('sub', 16), (1, False, False))
        self.assertEqual(h.call(31), (0, True, False))
        self.assertEqual(h.create('good.bin', 512), (0, True, False))
        tail_fat = disk.start + disk.reserved + chain[-1] // 128
        h.operations.clear()
        for _ in range(3):
            self.assertEqual(h.find('good.bin'), (1, False, False))
        self.assertNotIn(('read', tail_fat), h.operations)
        for n in range(13):                             # подкаталог полон
            self.assertEqual(h.create(f'f{n:02}.bin'), (0, True, False))
        h.operations.clear()
        self.assertEqual(h.create('grow.bin'), (0, True, False))  # растёт
        self.assertEqual(len(disk.chain(disk.get('sub')['cluster'])), 2)
        self.assertIn(('read', tail_fat), h.operations)


def file_volume(size, spc=4, attr=0x20):
    """Том с одним файлом FILE.BIN заданного размера (кластеры с 3), выбранным FIND."""
    disk = Disk(spc=spc)
    count = (size + spc * 512 - 1) // (spc * 512)
    for c in range(3, 3 + count):
        disk.set_fat(c, c + 1 if c < 2 + count else EOC)
        for s in range(spc):
            disk.write(lba_of(disk, c, s), bytes([65 + ((c - 3) * spc + s) % 26]) * 512)
    disk.write(lba_of(disk, ROOT), dir_sector(
        sfn_entry(b'FILE    BIN', attr=attr, cluster=3 if size else 0, size=size)))
    h = Driver(disk)
    assert h.find('file.bin') == (1, False, False)
    return h


class FullLbaDisk(Disk):
    """Носитель на все 2**32 секторов: границы тома задают только MBR и BPB."""
    def read(self, lba):
        assert 0 <= lba < 2**32
        return self.blocks.get(lba, bytes(512))

    def write(self, lba, data):
        assert 0 <= lba < 2**32 and len(data) == 512
        self.blocks[lba] = bytes(data)


class ReviewTests3(unittest.TestCase):
    """Случаи перепроверки 2026-10-06, третий круг (R3-1…R3-7)."""

    def mount_and_create(self, disk):
        h = Driver(disk, mount=False)
        before = dict(disk.blocks)
        mounted = h.call(3)
        created = h.create('new.bin', 512)
        return mounted, created, before

    def test_cluster_numbers_beyond_28_bits_are_not_mounted(self):
        """R3-1: том с кластерами за #0FFFFFF6 не монтируется: прежде выданный
        номер #10000003 после маски становился кластером 3 чужого файла."""
        disk = Disk(clusters=0x10000010)
        disk.set_fat(3, EOC)
        disk.write(lba_of(disk, ROOT), dir_sector(sfn_entry(b'VICTIM  BIN', cluster=3, size=512)))
        disk.write(lba_of(disk, 3), b'V' * 512)
        info = bytearray(disk.read(1))
        struct.pack_into('<I', info, 492, 0x10000003)
        disk.write(1, info)
        mounted, created, before = self.mount_and_create(disk)
        self.assertTrue(mounted[2])
        self.assertEqual(disk.blocks, before)

    def test_largest_fat32_volume_still_mounts(self):
        """R3-1, R4-1: граница #0FFFFFF0 — последний кластер #0FFFFFEF (номера
        #0FFFFFF0…#0FFFFFF6 драйвер считает зарезервированными ссылками)."""
        disk = Disk(clusters=0x0FFFFFEE)
        h = Driver(disk, mount=False)
        self.assertEqual(h.call(3), (0, True, False))
        self.assertEqual(u32(h.ram, h.sym['WDOS_EXT.FAT_DATA_CLUSTER_LIMIT']), 0x0FFFFFF0)

    def test_logical_volume_outside_extended_partition_is_not_mounted(self):
        """R3-2: логический том из EBR выходит за расширенный раздел."""
        disk = Disk(partition=8192, extended=63)
        mbr = bytearray(disk.read(0))
        struct.pack_into('<I', mbr, 458, 64)             # расширенный раздел — 64 сектора
        disk.write(0, mbr)
        mounted, created, before = self.mount_and_create(disk)
        self.assertTrue(mounted[2])
        self.assertEqual(disk.blocks, before)

    def test_wrapped_ebr_address_is_not_mounted(self):
        """R3-2: начало тома в EBR заворачивается за 2**32 на другой том."""
        disk = FullLbaDisk(partition=2048)                # годный BPB на LBA 2048
        mbr = bytearray(disk.read(0))
        mbr[450] = 0x0F
        struct.pack_into('<II', mbr, 454, 0xFFFF0000, 65535)
        disk.write(0, mbr)
        ebr = bytearray(512)
        ebr[510:] = b'\x55\xAA'
        ebr[450] = 0x0C
        struct.pack_into('<II', ebr, 454, 0x10800, disk.total)
        disk.write(0xFFFF0000, ebr)
        mounted, created, before = self.mount_and_create(disk)
        self.assertTrue(mounted[2])
        self.assertEqual(disk.blocks, before)

    def test_load256_buffer_counts_1024_bytes_per_sector(self):
        """R3-3: LOAD256 — две строки по 256 байт с шагом 512 на сектор; буфер,
        чей размах заходит за #FFFF, отвергается, ничего не записав."""
        for slot in (27, 60):
            for hl, count in ((0xFC00, 2), (0xC000, 26), (0x8000, 33)):
                with self.subTest(slot=slot, hl=hex(hl), count=count):
                    h = file_volume(26 * 512, spc=64)
                    low = bytes(h.ram[:0x4000])
                    self.assertEqual(h.call(slot, hl=hl, b=count), (0xF2, False, True))
                    self.assertEqual(bytes(h.ram[:0x4000]), low)
        h = file_volume(26 * 512, spc=64)
        h.cpu.set_memory_block(0xC000, b'\xCC' * 0x4000)
        self.assertFalse(h.call(60, hl=0xC000, b=16)[2])  # размах до #FFFF ровно
        self.assertEqual(bytes(h.ram[0xFE00:0xFF00]), bytes([65 + 15]) * 256)
        self.assertEqual(bytes(h.ram[0xFF00:0x10000]), b'\xCC' * 256)

    def test_landed_metadata_error_closes_the_file(self):
        """R3-4: SET_METADATA лёг (только чтение), драйвер получил отказ:
        выбор файла снят — WRITE не пишет поверх защищённого файла."""
        h = file_volume(1024)
        fail_on(h, 'write', lba_of(h.disk, ROOT), land=True)
        h.cpu.set_memory_block(DATA, bytes([16, 0x27, 0x21, 0]) + bytes(12))
        self.assertNotEqual(h.filex(6, length=16)[0][0], 0)
        h.fail = None
        self.assertEqual(h.disk.get('file.bin')['attr'], 0x21)
        before = h.disk.contents('file.bin')
        h.cpu.set_memory_block(DATA, b'X' * 512)
        self.assertEqual(h.call(49, hl=DATA, b=1), (0xF1, False, True))
        self.assertEqual(h.disk.contents('file.bin'), before)

    def test_stream_resumes_after_append_at_end(self):
        """R3-5: поток, дошедший до конца, после дозаписи читает новые данные:
        и у дочитанного файла, и у пустого при открытии."""
        h = file_volume(2048)
        self.assertFalse(h.call(48, hl=DATA, b=4)[2])
        self.assertEqual(h.append(b'Z' * 512), (0, True, False))
        h.cpu.set_memory_block(DATA, b'Q' * 512)
        self.assertEqual(h.call(48, hl=DATA, b=1)[:1], (0,))
        self.assertEqual(bytes(h.ram[DATA:DATA + 512]), b'Z' * 512)
        h = file_volume(0)
        self.assertEqual(h.append(b'Y' * 512), (0, True, False))
        h.cpu.set_memory_block(DATA, b'Q' * 512)
        self.assertEqual(h.call(48, hl=DATA, b=1)[:1], (0,))
        self.assertEqual(bytes(h.ram[DATA:DATA + 512]), b'Y' * 512)

    def test_stream_resumes_after_filex_growth(self):
        """R3-5: рост через FILEX (SET_EOF, WRITE_AT за концом) — поток видит данные."""
        for op, wanted in ((3, bytes(512)), (2, b'W' * 512)):
            with self.subTest(op=op):
                h = file_volume(0)
                h.cpu.set_memory_block(DATA, b'W' * 512)
                self.assertEqual(h.filex(op, offset=512 if op == 3 else 0, length=512)[0],
                                 (0, True, False))
                h.cpu.set_memory_block(DATA, b'Q' * 512)
                self.assertFalse(h.call(48, hl=DATA, b=1)[2])
                self.assertEqual(bytes(h.ram[DATA:DATA + 512]), wanted)

    def test_reserved_sectors_multiple_of_256_mount(self):
        """R3-6: BPB_RsvdSecCnt — 16-битное поле (256, 512, 8192)."""
        for reserved in (256, 512, 8192):
            with self.subTest(reserved=reserved):
                disk = Disk(partition=2048)
                delta = reserved - disk.reserved
                disk.blocks = {lba + (delta if lba >= disk.start + disk.reserved else 0): data
                               for lba, data in disk.blocks.items()}
                disk.reserved += delta
                disk.total += delta
                disk.data_start += delta
                for lba in (disk.start, disk.start + 6):
                    boot = bytearray(disk.read(lba))
                    struct.pack_into('<H', boot, 14, reserved)
                    struct.pack_into('<I', boot, 32, disk.total)
                    disk.write(lba, boot)
                mbr = bytearray(disk.read(0))
                struct.pack_into('<I', mbr, 458, disk.total)
                disk.write(0, mbr)
                h = Driver(disk, mount=False)
                self.assertEqual(h.call(3), (0, True, False))
                self.assertEqual(h.create('ok.bin', 512), (0, True, False))
                self.assertEqual(len(disk.chain(disk.get('ok.bin')['cluster'])), 1)

    def test_volume_ending_at_last_32bit_sector_mounts(self):
        """R3-6: последний сектор тома ровно #FFFFFFFF."""
        disk = FullLbaDisk(partition=2**32 - Disk().total)
        self.assertEqual(disk.start + disk.total - 1, 0xFFFFFFFF)
        h = Driver(disk, mount=False)
        self.assertEqual(h.call(3), (0, True, False))
        self.assertEqual(h.create('top.bin', 512), (0, True, False))

    def test_filex_move_publishes_full_attribute(self):
        """R3-7: FILEX MOVE пишет новую запись сразу с полным атрибутом."""
        h = file_volume(100, attr=0x27)
        published = []

        def observe(op, lba):
            if op == 'write' and lba == lba_of(h.disk, ROOT):
                buf = bytes(h.ram[h.cpu.bc:h.cpu.bc + 512])
                published.extend(buf[i + 11] for i in range(0, 512, 32)
                                 if buf[i:i + 11] == b'NEW     BIN')
            return False
        h.fail = observe
        self.assertEqual(FilexTests().move(h, 'file.bin', 'new.bin'), (0, True, False))
        self.assertTrue(published)
        self.assertEqual(set(published), {0x27})


class ReviewTests4(unittest.TestCase):
    """Случаи перепроверки 2026-10-06, четвёртый круг (R4-1…R4-8)."""

    def test_failed_replace_selects_nothing(self):
        """R4-7: откат MOVE REPLACE (запись назначения легла с отказом) не
        оставляет контекстом прежнее назначение, которого никто не выбирал:
        прежде следующий FILEX без FIND переписывал или усекал dest.bin."""
        for follow in ('write_at', 'set_eof'):
            with self.subTest(follow=follow):
                h = Driver()
                for name, data in (('source.bin', b'S' * 512), ('dest.bin', b'D' * 512)):
                    self.assertEqual(h.create(name), (0, True, False))
                    self.assertEqual(h.append(data), (0, True, False))
                self.assertEqual(h.find('source.bin'), (1, False, False))
                hits = fail_on(h, 'write', lba_of(h.disk, ROOT), land=True)
                self.assertEqual(FilexTests().move(h, 'source.bin', 'dest.bin', flags=1),
                                 (0x21, False, False))
                h.fail = None
                self.assertTrue(hits)
                h.cpu.set_memory_block(DATA, b'X' * 512)
                result = (h.filex(2, length=512) if follow == 'write_at' else h.filex(3))[0]
                self.assertEqual(result, (0x15, False, False))       # NO_CONTEXT
                self.assertEqual(h.call(62), (0xF1, False, True))
                self.assertEqual(h.disk.contents('source.bin'), b'S' * 512)
                self.assertEqual(h.disk.contents('dest.bin'), b'D' * 512)

    def test_confirmed_shrink_keeps_file_selected(self):
        """R4-2: запись ENTRY усечения легла с отказом и подтверждена
        перечитыванием — файл остаётся выбранным: OPEN и APPEND работают, как
        после обычного усечения."""
        for size in (0, 100, 2048):
            with self.subTest(size=size):
                h = file_volume(3000)
                before = h.disk.contents('file.bin')
                hits = fail_on(h, 'write', lba_of(h.disk, ROOT), land=True)
                self.assertEqual(h.filex(3, offset=size), ((0, True, False), size))
                h.fail = None
                self.assertTrue(hits)
                self.assertEqual(h.call(62), (0, True, False))
                self.assertEqual(h.append(b'X'), (0, True, False))
                self.assertEqual(h.disk.contents('file.bin'), before[:size] + b'X')

    def test_stream_resume_fat_error_returns_carry(self):
        """R4-3: отказ чтения FAT при продолжении потока с конца цепочки —
        CF=1 у всех восьми входов, без обмена; повтор продолжает поток."""
        for slot in (7, 8, 27, 28, 48, 49, 60, 61):
            with self.subTest(slot=slot):
                h = file_volume(2048)
                self.assertEqual(h.call(48, hl=DATA, b=4), (15, False, False))
                self.assertEqual(h.append(b'Z' * 512), (0, True, False))
                seen = fail_on(h, 'read', h.disk.reserved)
                before = dict(h.disk.blocks)
                h.operations.clear()
                result = h.call(slot, hl=DATA, b=1)
                h.fail = None
                self.assertTrue(seen)
                self.assertEqual(result, (0xFF, False, True))
                self.assertEqual(h.operations, [('read', h.disk.reserved)])
                self.assertEqual(h.disk.blocks, before)
                self.assertEqual(h.cpu.sp, STACK + 2)
                if slot in (7, 48):
                    self.assertEqual(h.call(slot, hl=DATA, b=1), (0, True, False))
                    self.assertEqual(bytes(h.ram[DATA:DATA + 512]), b'Z' * 512)

    def test_stream_stopped_by_error_keeps_carry(self):
        """R4-3: поток, остановленный отказом обмена посреди цепочки, и на
        следующем вызове отвечает CF=1 (прежде — A=#0F без CF, как обычный
        конец файла)."""
        h = file_volume(1024, spc=1)
        fail_on(h, 'read', h.disk.reserved)
        self.assertEqual(h.call(48, hl=DATA, b=2), (0xFF, False, True))
        h.fail = None
        self.assertEqual(h.call(48, hl=DATA, b=1), (0xFF, False, True))

    def test_file_link_changed_into_root_after_selection(self):
        """R4-6: FAT изменили после выбора файла — ссылка цепочки ведёт на
        кластер корня. WRITE (посреди потока и с продолжением после конца),
        WRITE_AT и APPEND отказывают, корень цел (прежде затирался)."""
        def root(h):
            return h.disk.read(lba_of(h.disk, ROOT))

        for slot in (8, 49):
            with self.subTest(case='resume', slot=slot):
                h = file_volume(2048)
                h.call(48, hl=DATA, b=4)
                self.assertEqual(h.append(b'Z' * 512), (0, True, False))
                h.disk.set_fat(3, ROOT)
                before = root(h)
                h.cpu.set_memory_block(DATA, b'X' * 512)
                self.assertTrue(h.call(slot, hl=DATA, b=1)[2])
                self.assertEqual(root(h), before)
            with self.subTest(case='stream', slot=slot):
                h = file_volume(1536, spc=1)
                h.disk.set_fat(3, ROOT)
                before = root(h)
                h.cpu.set_memory_block(DATA, b'X' * 1024)
                self.assertTrue(h.call(slot, hl=DATA, b=2)[2])
                self.assertEqual(root(h), before)
        with self.subTest(case='write_at'):
            h = file_volume(1536, spc=1)
            h.disk.set_fat(3, ROOT)
            before = root(h)
            h.cpu.set_memory_block(DATA, b'X' * 512)
            self.assertEqual(h.filex(2, offset=512, length=512)[0], (0x20, False, False))
            self.assertEqual(root(h), before)
        with self.subTest(case='append'):
            h = file_volume(600, spc=1)
            h.disk.set_fat(3, ROOT)
            before = root(h)
            self.assertNotEqual(h.append(b'Z' * 100)[0], 0)
            self.assertEqual(root(h), before)

    def test_wrapped_logical_volume_start_is_not_mounted(self):
        """R4-4: начало тома во втором EBR (EBR + смещение) за 2**32
        заворачивается внутрь расширенного раздела, на другой годный BPB:
        перенос теперь проверяется в самом сложении."""
        d = FullLbaDisk(partition=4096)
        base, second = 2048, 8192
        mbr = bytearray(d.read(0))
        mbr[450] = 0x0F
        struct.pack_into('<II', mbr, 454, base, d.total + 4096 - base)
        d.write(0, mbr)
        first = bytearray(512)
        first[450], first[466], first[510:] = 0x0C, 0x0F, b'\x55\xAA'
        struct.pack_into('<II', first, 454, 63, 16)          # BPB негоден — дальше
        struct.pack_into('<II', first, 470, second - base, d.total)
        d.write(base, first)
        ebr = bytearray(512)
        ebr[450], ebr[510:] = 0x0C, b'\x55\xAA'
        struct.pack_into('<II', ebr, 454, 0xFFFFF000, d.total)  # 8192 + … = 2**32 + 4096
        d.write(second, ebr)
        h = Driver(d, mount=False)
        before = dict(d.blocks)
        self.assertTrue(h.call(3)[2])
        h.create('wrong.bin', 512)
        self.assertEqual(d.blocks, before)

    def test_reserved_cluster_numbers_are_outside_the_volume(self):
        """R4-1: том, чья граница номеров дальше #0FFFFFF0, не монтируется:
        номер #0FFFFFF0 выдавался под файл, а потом сам драйвер отвергал
        ссылку на него — файл не открывался. Последний номер #0FFFFFEF годен."""
        self.assertTrue(Driver(Disk(clusters=0x0FFFFFEF), mount=False).call(3)[2])
        disk = Disk(clusters=0x0FFFFFEE)
        info = bytearray(disk.read(1))
        struct.pack_into('<I', info, 492, 0x0FFFFFEF)
        disk.write(1, info)
        h = Driver(disk, mount=False)
        self.assertEqual(h.call(3), (0, True, False))
        self.assertEqual(h.create('edge.bin', 512), (0, True, False))
        self.assertEqual(disk.get('edge.bin')['cluster'], 0x0FFFFFEF)
        self.assertEqual(h.find('edge.bin'), (1, False, False))

    def test_zero_partition_length_is_not_mounted(self):
        """R4-8: длина раздела 0 в MBR или EBR — пустой раздел, а не «границы
        нет»: прежде том монтировался и CREATE писал в него."""
        for extended in (0, 63):
            with self.subTest(extended=extended):
                disk = Disk(partition=8192, extended=extended)
                table = disk.ebr if extended else 0
                sector = bytearray(disk.read(table))
                struct.pack_into('<I', sector, 458, 0)
                disk.write(table, sector)
                h = Driver(disk, mount=False)
                before = dict(disk.blocks)
                self.assertTrue(h.call(3)[2])
                h.create('zero.bin', 512)
                self.assertEqual(disk.blocks, before)

    def test_fat_shorter_than_cluster_count_is_not_mounted(self):
        """R4-5: одна FAT в 1 сектор (128 записей) на 65536 кластеров — BPB
        испорчен (MOUNT и MOUNT_AT)."""
        for slot in (3, 36):
            with self.subTest(slot=slot):
                disk = Disk(fats=1, clusters=65536)
                bpb = bytearray(disk.read(0))
                struct.pack_into('<I', bpb, 36, 1)
                disk.fat_sectors, disk.data_start = 1, 33
                disk.total = 33 + disk.clusters
                struct.pack_into('<I', bpb, 32, disk.total)
                disk.write(0, bpb)
                h = Driver(disk, mount=False)
                self.assertTrue(h.call(slot, hl=0, de=0)[2])


class ReviewTests5(unittest.TestCase):
    """Случаи перепроверки 2026-10-06, пятый круг (R5-1)."""

    def test_write_at_past_eof_reports_failed_gap(self):
        """R5-1: отказ записи при заполнении нулями промежутка до WRITE_AT за
        концом файла — статус MEDIA, а не OK (прежде FILEX_CLEAR_RESULT
        затирал код отказа); файл прежний."""
        for size, offset in ((0, 512), (600, 1000), (600, 4000)):
            with self.subTest(size=size, offset=offset):
                h = file_volume(size, spc=1)
                before = h.disk.contents('file.bin')
                hits = []

                def fault(op, lba):
                    if op == 'write' and lba > h.disk.data_start and not hits:
                        hits.append(lba)
                        return True
                    return False
                h.fail = fault
                h.cpu.set_memory_block(DATA, b'PAYLOAD')
                self.assertEqual(h.filex(2, offset=offset, length=7), ((0x21, False, False), 0))
                h.fail = None
                self.assertEqual(len(hits), 1)
                self.assertEqual(h.disk.contents('file.bin'), before)

    def test_write_at_past_eof_status_matches_contents(self):
        """R5-1: по одному отказу на каждом обращении WRITE_AT за концом файла
        (запись — и не легла, и легла): OK — только когда файл именно такой,
        как просили; отказ без «легла» и без ROLLBACK — файл прежний."""
        def volume():
            h = file_volume(900, spc=2)
            h.cpu.set_memory_block(DATA, b'P' * 800)
            return h
        original = volume().disk.contents('file.bin')
        wanted = original + bytes(3300 - 900) + b'P' * 800
        base = volume()
        base.operations.clear()
        self.assertEqual(base.filex(2, offset=3300, length=800), ((0, True, False), 800))
        self.assertEqual(base.disk.contents('file.bin'), wanted)
        for nth, (want_op, _) in enumerate(base.operations, 1):
            for land in ((False, True) if want_op == 'write' else (False,)):
                with self.subTest(nth=nth, op=want_op, land=land):
                    h = volume()
                    seen = [0]

                    def fault(op, lba):
                        seen[0] += 1
                        if seen[0] != nth:
                            return False
                        if land:
                            h.disk.write(lba, bytes(h.ram[h.cpu.bc:h.cpu.bc + 512]))
                        return True
                    h.fail = fault
                    status = h.filex(2, offset=3300, length=800)[0][0]
                    h.fail = None
                    contents = h.disk.contents('file.bin')
                    if status == 0:
                        self.assertEqual(contents, wanted)
                    elif not land and status != 0x24:
                        self.assertEqual(contents, original)


class ReviewTests6(unittest.TestCase):
    """Случаи перепроверки 2026-10-06, шестой круг (R6-1…R6-4)."""

    def create(self, h, attr, name='new.bin', size=512):
        return h.call(72, hl=h.name(name, bytes([attr]) + struct.pack('<I', size)))

    def test_create_publishes_requested_attribute(self):
        """R6-1: CREATE пишет запись сразу с атрибутом из запроса (прежде
        оставался 0, и файл «только для чтения» создавался без защиты)."""
        for attr in (0x00, 0x01, 0x02, 0x04, 0x20, 0x27):
            with self.subTest(attr=hex(attr)):
                h = Driver()
                self.assertEqual(self.create(h, attr), (0, True, False))
                self.assertEqual(h.disk.get('new.bin')['attr'], attr)
        h = Driver()
        self.assertEqual(self.create(h, 0x21), (0, True, False))
        before = h.disk.contents('new.bin')
        self.assertEqual(h.append(b'X' * 100)[0], 0x28)
        h.cpu.set_memory_block(DATA, b'W' * 512)
        self.assertTrue(h.call(49, hl=DATA, b=1)[2])
        self.assertEqual(h.filex(2, length=512)[0], (0x18, False, False))
        self.assertEqual(h.disk.contents('new.bin'), before)

    def test_create_refuses_directory_and_label_bits(self):
        """R6-1: бит каталога, метки тома и резервные биты в CREATE —
        неверный аргумент без записи: прежде бит каталога давал «каталог» на
        неочищенных кластерах (его записи — обрывки удалённых файлов)."""
        for attr in (0x08, 0x0F, 0x10, 0x30, 0x40, 0x80):
            with self.subTest(attr=hex(attr)):
                h = Driver()
                before = dict(h.disk.blocks)
                self.assertEqual(self.create(h, attr, size=2048), (0xF3, False, True))
                self.assertEqual(h.disk.blocks, before)

    def test_create_reports_media_error_after_commit(self):
        """R6-2: отказ чтения, когда файл уже создан и драйвер ищет его запись
        для APPEND/FILEX, — отказ носителя (#FE/#FF), а не «неверный аргумент»."""
        base = Driver(Disk(clusters=260))
        base.operations.clear()
        self.assertEqual(base.create('new.bin', 512), (0, True, False))
        ops = base.operations
        last_write = max(i for i, (op, _) in enumerate(ops) if op == 'write')
        reads = [n for n in range(last_write + 2, len(ops) + 1) if ops[n - 1][0] == 'read']
        self.assertTrue(reads)
        for nth in reads:
            with self.subTest(nth=nth):
                h = Driver(Disk(clusters=260))
                seen = [0]

                def fault(op, lba):
                    seen[0] += 1
                    return seen[0] == nth
                h.operations.clear()
                h.fail = fault
                result = h.create('new.bin', 512)
                h.fail = None
                self.assertNotEqual(result[0], 0xF3)
                if result != (0, True, False):
                    self.assertIn(result, ((0xFE, False, True), (0xFF, False, True)))
                self.assertEqual(h.disk.get('new.bin')['size'], 512)

    def fs_info(self, h, flags=0):
        result = h.filex(4, length=48, flags=flags)
        return result, h.ram[DATA + 2] & 4, u32(h.ram, DATA + 12), u32(h.ram, DATA + 36)

    def test_refresh_free_counts_the_fat(self):
        """R6-3: GET_FS_INFO с флагом REFRESH_FREE пересчитывает свободные
        кластеры по FAT (прежде флаг принимался, а возвращалась старая
        подсказка FSInfo либо «неизвестно»)."""
        for clusters in (260, 1000, 1150):
            with self.subTest(clusters=clusters):
                h = Driver(Disk(clusters=clusters))
                self.assertEqual(h.mkdir('a'), (0, True, False))
                self.assertEqual(h.create('f.bin'), (0, True, False))
                self.assertEqual(h.append(b'F' * 900), (0, True, False))
                h.disk.set_fat(clusters + 1, 0x0FFFFFFF)     # последний кластер занят
                actual = sum(h.disk.fat(c) == 0 for c in range(2, clusters + 2))
                result, known, free, _ = self.fs_info(h, flags=1)
                self.assertEqual(result, ((0, True, False), 48))
                self.assertTrue(known)
                self.assertEqual(free, actual)

    def test_fs_info_trusts_only_valid_fsinfo(self):
        """R6-4: GET_FS_INFO берёт FSInfo только из сектора в резервной
        области с полной сигнатурой; подсказка вне кластеров данных —
        «неизвестно» (#FFFFFFFF)."""
        disk = Disk(clusters=260)
        bpb = bytearray(disk.read(0))
        struct.pack_into('<H', bpb, 48, 40)               # FSInfo за резервом (32)
        disk.write(0, bpb)
        disk.write(40, disk.read(1))
        _, known, free, hint = self.fs_info(Driver(disk))
        self.assertEqual((known, free, hint), (0, 0xFFFFFFFF, 0xFFFFFFFF))
        disk = Disk(clusters=260)
        info = bytearray(disk.read(1))
        info[508] = 1                                     # хвост сигнатуры — не 0
        disk.write(1, info)
        _, known, free, hint = self.fs_info(Driver(disk))
        self.assertEqual((known, free, hint), (0, 0xFFFFFFFF, 0xFFFFFFFF))
        for value in (0, 1, 262, 0x0FFFFFF0, 0xFFFFFFFE):
            with self.subTest(hint=hex(value)):
                disk = Disk(clusters=260)
                info = bytearray(disk.read(1))
                struct.pack_into('<I', info, 492, value)
                disk.write(1, info)
                self.assertEqual(self.fs_info(Driver(disk))[3], 0xFFFFFFFF)
        self.assertEqual(self.fs_info(Driver(Disk(clusters=260)))[3], 3)


class ReviewTests9(unittest.TestCase):
    """Случаи перепроверки 2026-10-06, девятый круг (R9-1) и служебные имена."""

    def failed_move(self, landed):
        h = Driver(Disk(spc=2, clusters=260))
        self.assertEqual(h.mkdir('target'), (0, True, False))
        target = h.disk.get('target')['cluster']
        self.assertEqual(h.create('source.bin'), (0, True, False))
        self.assertEqual(h.append(b'A' * 1100), (0, True, False))
        chain = h.disk.chain(h.disk.get('source.bin')['cluster'])
        target_lba = lba_of(h.disk, target)
        faults, armed = [], []

        def fault(op, lba):
            if not armed and op == 'write' and lba == target_lba:
                armed.append(lba)                   # ссылка назначения пишется
                if not landed:
                    return False                    # и ложится без отказа
                h.disk.write(lba, bytes(h.ram[h.cpu.bc:h.cpu.bc + 512]))
                faults.append(op)
                return True                         # легла с отказом
            if armed and len(faults) < 4 and op == 'read':
                faults.append(op)
                return True
            return False
        h.fail = fault
        status = FilexTests().move(h, 'source.bin', 'new.bin', dest_dir=target)[0]
        h.fail = None
        return h, target, chain, status, faults

    def test_failed_move_drops_selection(self):
        """R9-1: ссылка назначения легла (с отказом записи или без него), а
        поиски для отката не прочитали каталог — откат не удался (ROLLBACK),
        на цепочке две записи. Выбор файла снят: прежде следующий SET_EOF без
        FIND по выбранному источнику освобождал цепочку живого назначения."""
        for landed in (True, False):
            with self.subTest(landed=landed):
                h, target, chain, status, faults = self.failed_move(landed)
                self.assertEqual(len(faults), 4)
                self.assertEqual(status, 0x24)
                self.assertEqual(h.disk.contents('new.bin', target), b'A' * 1100)
                self.assertEqual(h.filex(3)[0], (0x15, False, False))
                self.assertEqual(h.call(62), (0xF1, False, True))
                self.assertTrue(all(h.disk.fat(c) for c in chain))
                self.assertEqual(h.disk.contents('source.bin'), b'A' * 1100)

    def test_dot_entries_are_not_deleted_or_renamed(self):
        """«.» и «..» — служебные записи каталога: DELETE и RENAME отвечают
        «неверный аргумент» и ничего не пишут. Прежде DELETE «..» освобождал
        цепочку родительского каталога, RENAME ломал ссылку на него."""
        h = Driver()
        self.assertEqual(h.mkdir('parent'), (0, True, False))
        self.assertEqual(h.find('parent', 0x10), (1, False, False))
        self.assertEqual(h.call(63), (0, True, False))
        self.assertEqual(h.mkdir('child'), (0, True, False))
        self.assertEqual(h.find('child', 0x10), (1, False, False))
        self.assertEqual(h.call(63), (0, True, False))
        h.cpu.set_memory_block(DATA, b'x\0')
        for name in ('.', '..'):
            with self.subTest(name=name):
                before = dict(h.disk.blocks)
                self.assertEqual(h.call(75, hl=h.name(name, b'\x10')), (0xF3, False, True))
                self.assertEqual(h.call(74, hl=h.name(name, b'\x10'), de=DATA),
                                 (0xF3, False, True))
                self.assertEqual(h.disk.blocks, before)
        self.assertNotEqual(h.call(75, hl=h.name('..x', b'\x10'))[0], 0xF3)  # обычное имя


class ReviewTests10(unittest.TestCase):
    """Случаи перепроверки 2026-10-06, десятый круг (R10-1…R10-3); все — без
    отказов носителя."""

    def test_replacing_current_directory_is_refused(self):
        """R10-1: MOVE с заменой каталога, который сейчас текущий, отвергается
        (INVALID_MOVE) без записи: прежде цепочка текущего каталога
        освобождалась, а следующий CREATE писал записи каталога в файл,
        которому достался этот кластер."""
        for spc in (1, 8):
            with self.subTest(spc=spc):
                h = Driver(Disk(spc=spc, clusters=260))
                self.assertEqual(h.mkdir('source'), (0, True, False))
                self.assertEqual(h.mkdir('target'), (0, True, False))
                target = h.disk.get('target')['cluster']
                self.assertEqual(h.find('target', 0x10), (1, False, False))
                self.assertEqual(h.call(63), (0, True, False))
                before = dict(h.disk.blocks)
                self.assertEqual(FilexTests().move(h, 'source', 'target', kind=0x10, flags=1),
                                 (0x1D, False, False))
                self.assertEqual(h.disk.blocks, before)
                self.assertEqual(h.create('inside.bin'), (0, True, False))
                self.assertIn('INSIDE.BIN', [e['name'] for e in h.disk.entries(target)])
                # Замена каталога, который не текущий, по-прежнему работает.
                self.assertEqual(h.call(32), (0, True, False))
                self.assertEqual(h.mkdir('other'), (0, True, False))
                self.assertEqual(FilexTests().move(h, 'source', 'other', kind=0x10, flags=1),
                                 (0, True, False))

    def test_names_outside_supported_cp866_are_refused(self):
        """R10-2: знаки CP866 #B0..#DF (псевдографика) и #F2..#FF в длинное
        имя не переводятся — CREATE, MKDIR, RENAME и MOVE с таким именем
        отказывают без записи. Прежде они становились «ё»: RENAME сообщал
        успех под другим именем, а MOVE оставлял вторую запись на цепочке,
        которую DELETE источника освобождал. Русские буквы, Ё и ё работают."""
        h = Driver(Disk(clusters=260))
        self.assertEqual(h.create('source.bin'), (0, True, False))
        self.assertEqual(h.append(b'S' * 700), (0, True, False))
        for code in (0xB0, 0xC5, 0xDF, 0xF2, 0xFF):
            name = 'long name ' + bytes([code]).decode('cp866') + '.txt'
            with self.subTest(code=hex(code)):
                before = dict(h.disk.blocks)
                self.assertTrue(h.create(name)[2])
                self.assertTrue(h.mkdir(name)[2])
                h.cpu.set_memory_block(NAME + 512, name.encode('cp866') + b'\0')
                self.assertTrue(h.call(74, hl=h.name('source.bin', b'\0'), de=NAME + 512)[2])
                self.assertEqual(FilexTests().move(h, 'source.bin', name)[0], 0x1C)
                self.assertEqual(h.disk.blocks, before)
        for name in ('Ёлка ё.txt', 'Проверка имени.bin', 'юникод Я.dat'):
            with self.subTest(name=name):
                self.assertEqual(h.create(name, 100), (0, True, False))
                self.assertEqual(h.find(name), (1, False, False))
                self.assertIn(name, [e['name'] for e in h.disk.entries()])

    def test_move_without_space_keeps_selection(self):
        """R10-3: MOVE в полный каталог на полном томе — NO_SPACE, том не
        изменён, выбранный файл и позиция его потока прежние."""
        disk = Disk(spc=4, clusters=40)
        for c in range(3, 42):
            disk.set_fat(c, c + 1 if 5 <= c < 41 else EOC)
        root = sfn_entry(b'SOURCE  BIN', cluster=3, size=1024)
        root += sfn_entry(b'FULL       ', attr=0x10, cluster=4)
        root += sfn_entry(b'BALLAST BIN', cluster=5, size=37 * 4 * 512)
        disk.write(lba_of(disk, ROOT), dir_sector(root))
        disk.write(lba_of(disk, 3), b'A' * 512)
        disk.write(lba_of(disk, 3, 1), b'B' * 512)
        body = sfn_entry(b'.          ', attr=0x10, cluster=4) + sfn_entry(b'..         ', attr=0x10)
        body += b''.join(sfn_entry(('F%07d' % n).encode() + b'BIN') for n in range(62))
        for j in range(4):
            disk.write(lba_of(disk, 4, j), body[j * 512:(j + 1) * 512])
        h = Driver(disk)
        self.assertEqual(h.find('source.bin'), (1, False, False))
        self.assertEqual(h.call(48, hl=DATA, b=1), (0, True, False))
        before = dict(disk.blocks)
        self.assertEqual(FilexTests().move(h, 'source.bin', 'new.bin', dest_dir=4),
                         (0x22, False, False))
        self.assertEqual(disk.blocks, before)
        self.assertEqual(h.call(48, hl=DATA, b=1), (0, True, False))
        self.assertEqual(bytes(h.ram[DATA:DATA + 512]), b'B' * 512)

    def test_move_to_invalid_name_keeps_selection(self):
        """MOVE в недопустимое имя — INVALID_NAME (прежде INTERNAL), ничего не
        записано, выбранный файл и позиция его потока прежние."""
        h = Driver(Disk(spc=4, clusters=40))
        self.assertEqual(h.create('source.bin'), (0, True, False))
        self.assertEqual(h.append(b'S' * 1024), (0, True, False))
        self.assertEqual(h.find('source.bin'), (1, False, False))
        self.assertEqual(h.call(48, hl=DATA, b=1), (0, True, False))
        before = dict(h.disk.blocks)
        self.assertEqual(FilexTests().move(h, 'source.bin', 'invalid?.bin'), (0x1C, False, False))
        self.assertEqual(h.disk.blocks, before)
        h.cpu.set_memory_block(DATA, b'X' * 512)
        self.assertEqual(h.call(49, hl=DATA, b=1), (0, True, False))
        self.assertEqual(h.disk.contents('source.bin'), b'S' * 512 + b'X' * 512)


class ReviewTests11(unittest.TestCase):
    """Случаи перепроверки 2026-10-06, одиннадцатый круг (R11-1): сравнение
    длинных имён; все — без отказов носителя."""

    NAMES = (' leading name.bin', 'leading name.bin', '  leading name.bin')

    @staticmethod
    def names(h):
        return sorted(e['name'].casefold() for e in h.disk.entries())

    def test_leading_spaces_are_significant(self):
        """R11-1: начальные пробелы — часть имени. Прежде сравнение LFN
        пропускало их и в запросе, и в имени на диске: DELETE отсутствующего
        «leading name.bin» удалял « leading name.bin», FIND и WRITE_AT
        попадали в соседний файл, RENAME переименовывал не тот, а MOVE
        « x» → «  x» сообщал успех, ничего не сделав."""
        h = Driver(Disk(spc=2, clusters=260))
        self.assertEqual(h.create(self.NAMES[0]), (0, True, False))
        self.assertEqual(h.append(b'A' * 512), (0, True, False))
        before = dict(h.disk.blocks)
        self.assertEqual(h.find(self.NAMES[1]), (0, True, False))
        self.assertNotEqual(h.call(75, hl=h.name(self.NAMES[1], b'\0')), (0, True, False))
        self.assertEqual(h.disk.blocks, before)
        for name, fill in zip(self.NAMES[1:], b'BC'):
            self.assertEqual(h.create(name), (0, True, False))
            self.assertEqual(h.append(bytes([fill]) * 512), (0, True, False))
        self.assertEqual(self.names(h), sorted(self.NAMES))
        # Регистр по-прежнему не важен, пробелы важны.
        self.assertEqual(h.find(self.NAMES[1].upper()), (1, False, False))
        self.assertEqual(h.find('   leading name.bin'), (0, True, False))
        for name, fill in zip(self.NAMES, b'abc'):
            with self.subTest(write=name):
                self.assertEqual(h.find(name), (1, False, False))
                h.cpu.set_memory_block(DATA, bytes([fill]) * 16)
                self.assertEqual(h.filex(2, offset=0, length=16), ((0, True, False), 16))
        for name, fill, old in zip(self.NAMES, b'abc', b'ABC'):
            self.assertEqual(h.disk.contents(name), bytes([fill]) * 16 + bytes([old]) * 496)
        h.cpu.set_memory_block(DATA, b'renamed.bin\0')
        self.assertEqual(h.call(74, hl=h.name(self.NAMES[1], b'\0'), de=DATA), (0, True, False))
        self.assertEqual(h.disk.contents('renamed.bin'), b'b' * 16 + b'B' * 496)
        self.assertEqual(h.call(75, hl=h.name(self.NAMES[0], b'\0')), (0, True, False))
        self.assertEqual(self.names(h), sorted((self.NAMES[2], 'renamed.bin')))
        self.assertEqual(FilexTests().move(h, self.NAMES[2], self.NAMES[0]), (0, True, False))
        self.assertEqual(self.names(h), sorted((self.NAMES[0], 'renamed.bin')))
        self.assertEqual(h.disk.contents(self.NAMES[0]), b'c' * 16 + b'C' * 496)

    def test_query_char_does_not_match_name_end(self):
        """Знак запроса не совпадает с концом длинного имени на диске: прежде
        запрос на #FF длиннее находил имя, конец которого приходится на
        последний знак записи LFN, и DELETE такого запроса удалял его."""
        h = Driver(Disk(spc=2, clusters=260))
        name = 'long name 12'                       # 12 знаков и #0000 — одна запись LFN
        self.assertEqual(h.create(name), (0, True, False))
        self.assertEqual(h.append(b'L' * 512), (0, True, False))
        before = dict(h.disk.blocks)
        self.assertEqual(h.find(name + '\xa0'), (0, True, False))
        self.assertNotEqual(h.call(75, hl=h.name(name + '\xa0', b'\0')), (0, True, False))
        self.assertEqual(h.disk.blocks, before)
        self.assertEqual(h.find(name), (1, False, False))


class ReviewTests12(unittest.TestCase):
    """Случаи перепроверки 2026-10-06, двенадцатый круг (R12-1); без отказов
    носителя."""

    def test_trailing_dot_does_not_find_short_name(self):
        """R12-1: «foo.» — не «foo». Прежде короткая форма 8.3 запроса теряла
        точку в конце: FIND «foo.» выбирал FOO (и WRITE_AT писал в него),
        DELETE освобождал его цепочку, RENAME переименовывал, а «алиас.»
        находил файл с этим коротким алиасом. Служебные «.» и «..»
        по-прежнему находятся."""
        for spc in (1, 8):
            with self.subTest(spc=spc):
                h = Driver(Disk(spc=spc, clusters=260))
                self.assertEqual(h.create('foo'), (0, True, False))
                self.assertEqual(h.append(b'F' * 700), (0, True, False))
                self.assertEqual(h.create(' leading'), (0, True, False))
                self.assertEqual(h.append(b'L' * 700), (0, True, False))
                alias = h.disk.get(' leading')['short'].decode('cp866').rstrip()
                before = dict(h.disk.blocks)
                for query in ('foo.', 'FOO.', alias + '.', ' leading.'):
                    with self.subTest(query=query):
                        self.assertEqual(h.find(query), (0, True, False))
                        self.assertNotEqual(h.call(75, hl=h.name(query, b'\0')), (0, True, False))
                        h.cpu.set_memory_block(DATA, b'bar\0')
                        self.assertNotEqual(h.call(74, hl=h.name(query, b'\0'), de=DATA),
                                            (0, True, False))
                        self.assertEqual(h.disk.blocks, before)
                self.assertEqual(h.find('foo'), (1, False, False))
                self.assertEqual(h.find(alias), (1, False, False))
                self.assertEqual(h.disk.contents(' leading'), b'L' * 700)
                self.assertEqual(h.mkdir('sub'), (0, True, False))
                self.assertEqual(h.find('sub', 0x10), (1, False, False))
                self.assertEqual(h.call(63), (0, True, False))
                self.assertEqual(h.find('.', 0x10), (1, False, False))
                self.assertEqual(h.find('..', 0x10), (1, False, False))


class FaultSweepTests(unittest.TestCase):
    """По одному отказу на каждом обращении к носителю обычных операций (запись
    — и не легла, и легла): посторонний файл цел, у каждой живой записи своя
    цепочка не короче размера, кластеры корня ни у кого не заняты, стек и IX/IY
    на месте — и сразу после отказа, и после следующих вызовов без нового
    FIND."""

    KINDS = ('create', 'create_long', 'mkdir', 'rename', 'rename_long', 'append',
             'shrink', 'grow', 'move', 'move_long', 'replace')

    def setup(self, kind):
        h = Driver(Disk(spc=2))
        assert h.create('victim.bin') == (0, True, False)
        assert h.append(b'V' * 900) == (0, True, False)
        if kind == 'replace':
            # Назначение — не выбранный файл: выбран источник (как у R4-7).
            assert h.create('dest.bin') == (0, True, False)
            assert h.append(b'D' * 900) == (0, True, False)
            self.old_dest = h.disk.get('dest.bin')['cluster']
        if kind not in ('create', 'create_long', 'mkdir'):
            if kind.endswith('_long'):
                for n in range(14):
                    assert h.create(f'f{n:02}.bin') == (0, True, False)
            assert h.create(LONG_NAME if kind.endswith('_long') else 'file.bin') == (0, True, False)
            assert h.append(b'A' * 900) == (0, True, False)
        h.operations.clear()
        h.cpu.ix, h.cpu.iy = 0x1234, 0x5678
        return h

    def follow_up(self, h, kind):
        """Следующие вызовы без нового FIND: FILEX WRITE_AT, APPEND, OPEN и
        WRITE. Пишут только в выбранный файл либо отказывают; прежнее
        назначение замены не выбиралось — его данные целы (R4-7)."""
        h.cpu.set_memory_block(DATA, b'X' * 512)
        h.filex(2, length=512)
        h.append(b'Y' * 100)
        if h.call(62) == (0, True, False):
            h.cpu.set_memory_block(DATA, b'W' * 512)
            h.call(49, hl=DATA, b=1)
        self.check(h)
        if kind == 'replace':
            for e in h.disk.entries():
                if e['cluster'] == self.old_dest:
                    assert h.disk.contents(e['name']) == b'D' * 900, 'прежнее назначение'

    def operation(self, h, kind):
        source = LONG_NAME if kind.endswith('_long') else 'file.bin'
        if kind.startswith('create'):
            return h.create(LONG_NAME if kind == 'create_long' else 'new long file.bin', 1300)
        if kind == 'mkdir':
            return h.mkdir('new directory')
        if kind.startswith('rename'):
            h.cpu.set_memory_block(DATA, b'new long name.bin\0')
            return h.call(74, hl=h.name(source, b'\0'), de=DATA)
        if kind == 'append':
            return h.append(b'B' * 1300)
        if kind == 'shrink':
            return h.filex(3, offset=300)
        if kind == 'grow':
            return h.filex(3, offset=3300)
        if kind.startswith('move'):
            return FilexTests().move(h, source, 'new long name.bin')
        return FilexTests().move(h, 'file.bin', 'dest.bin', flags=1)

    def check(self, h):
        assert h.cpu.sp == STACK + 2, ('стек', h.cpu.sp)
        assert (h.cpu.ix, h.cpu.iy) == (0x1234, 0x5678), 'IX/IY'
        assert h.disk.contents('victim.bin') == b'V' * 900, 'посторонний файл'
        roots = set(h.disk.chain(ROOT))
        owners = {}
        for e in h.disk.entries():
            chain = h.disk.chain(e['cluster'])
            assert len(chain) * h.disk.spc * 512 >= e['size'], ('короткая цепочка', e['name'])
            for c in chain:
                assert c not in owners, ('общий кластер', c, owners.get(c), e['name'])
                assert c not in roots, ('кластер корня', c, e['name'])
                owners[c] = e['name']

    def test_double_fault_sweep(self):
        """Два отказа подряд: первый — на любом обращении
        обычной трассы, второй — на любом обращении после него в трассе с
        первым отказом; запись — и не легла, и легла. Прежде RENAME, MOVE и
        MOVE с заменой оставляли на цепочке две живые записи уже при одном
        отказе."""
        for kind in ('rename', 'rename_long', 'move', 'move_long', 'replace', 'shrink'):
            h = self.setup(kind)
            ram, blocks = bytes(h.ram[:0x10000]), dict(h.disk.blocks)

            def run(faults):
                h.cpu.set_memory_block(0, ram)
                h.disk.blocks = dict(blocks)
                h.operations.clear()
                h.cpu.ix, h.cpu.iy = 0x1234, 0x5678
                seen = [0]

                def fault(op, lba):
                    seen[0] += 1
                    if seen[0] not in faults:
                        return False
                    if faults[seen[0]]:
                        h.disk.write(lba, bytes(h.ram[h.cpu.bc:h.cpu.bc + 512]))
                    return True
                h.fail = fault
                result = self.operation(h, kind)
                h.fail = None
                return result, list(h.operations)

            _, base = run({})
            for first, (op1, _) in enumerate(base, 1):
                for land1 in ((False, True) if op1 == 'write' else (False,)):
                    _, trace = run({first: land1})
                    for second in range(first + 1, len(trace) + 1):
                        op2 = trace[second - 1][0]
                        for land2 in ((False, True) if op2 == 'write' else (False,)):
                            with self.subTest(kind=kind, first=first, land1=land1,
                                              second=second, land2=land2):
                                run({first: land1, second: land2})
                                self.check(h)
                                if kind == 'shrink':
                                    data = h.disk.contents('file.bin')
                                    self.assertEqual(data, b'A' * len(data))
                                self.follow_up(h, kind)

    def test_single_fault_sweep(self):
        for kind in self.KINDS:
            base = self.setup(kind)
            self.operation(base, kind)
            for nth, (want_op, want_lba) in enumerate(base.operations, 1):
                for land in ((False, True) if want_op == 'write' else (False,)):
                    with self.subTest(kind=kind, nth=nth, op=want_op, land=land):
                        h = self.setup(kind)
                        seen = [0]

                        def fault(op, lba):
                            seen[0] += 1
                            if seen[0] != nth:
                                return False
                            if land:
                                h.disk.write(lba, bytes(h.ram[h.cpu.bc:h.cpu.bc + 512]))
                            return True
                        h.fail = fault
                        self.operation(h, kind)
                        h.fail = None
                        self.check(h)
                        self.follow_up(h, kind)


if __name__ == '__main__':
    unittest.main()
