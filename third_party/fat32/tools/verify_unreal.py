"""Независимая проверка файлов и зеркал FAT в IMG после smoke.spg."""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from harness import Disk


class ImageDisk(Disk):
    def __init__(self,path):
        self.file=path.open('rb')
        mbr=self.file.read(512)
        self.start=struct.unpack_from('<I',mbr,454)[0]
        self.file.seek(self.start*512)
        bpb=self.file.read(512)
        assert bpb[510:512]==b'\x55\xAA'
        assert struct.unpack_from('<H',bpb,11)[0]==512
        self.fats=bpb[16]
        assert self.fats==2
        self.spc=bpb[13]
        self.reserved=struct.unpack_from('<H',bpb,14)[0]
        self.total=struct.unpack_from('<I',bpb,32)[0]
        self.fat_sectors=struct.unpack_from('<I',bpb,36)[0]
        # BPB_ExtFlags: бит 7 отключает зеркалирование, младшая тетрада — номер
        # активной копии FAT. Разбор наследуется от Disk и обязан читать ту же.
        ext=struct.unpack_from('<H',bpb,40)[0]
        self.active=(ext&0x0F) if ext&0x80 else 0
        assert not self.active
        assert struct.unpack_from('<I',bpb,44)[0]==2
        self.data_start=self.start+self.reserved+self.fats*self.fat_sectors
        self.clusters=(self.total-self.reserved-self.fats*self.fat_sectors)//self.spc

    def read(self,lba):
        assert 0<=lba<self.start+self.total
        self.file.seek(lba*512)
        data=self.file.read(512)
        assert len(data)==512
        return data


def close_test_emulator(directory):
    import ctypes as c
    from ctypes import wintypes as w
    pid=int((directory/'process-id.txt').read_text(encoding='utf-8-sig'))
    kernel=c.WinDLL('kernel32',use_last_error=True)
    user=c.WinDLL('user32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]
    kernel.OpenProcess.restype=w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)]
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.OpenProcess(0x101000,False,pid)
    if not handle:
        if c.get_last_error()==87:
            return
        raise c.WinError(c.get_last_error())
    try:
        name=c.create_unicode_buffer(32768)
        length=w.DWORD(len(name))
        if not kernel.QueryFullProcessImageNameW(handle,0,name,c.byref(length)):
            raise c.WinError(c.get_last_error())
        assert Path(name.value).resolve()==(directory/'Unreal.exe').resolve(), 'PID принадлежит другому процессу'
        user.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)]
        user.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,c.c_int]
        user.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
        callback=c.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
        @callback
        def close_window(hwnd,param):
            owner=w.DWORD()
            user.GetWindowThreadProcessId(hwnd,c.byref(owner))
            if owner.value==pid:
                klass=c.create_unicode_buffer(128)
                user.GetClassNameW(hwnd,klass,128)
                if klass.value=='EMUL_WND':
                    # Unreal игнорирует WM_CLOSE; SC_CLOSE вызывает correct_exit.
                    user.PostMessageW(hwnd,0x112,0xF060,0)
            return True
        user.EnumWindows(close_window,0)
        if kernel.WaitForSingleObject(handle,10000)!=0:
            raise RuntimeError('Тестовый Unreal ещё не завершился; IMG не проверен')
    finally:
        kernel.CloseHandle(handle)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,default=ROOT/'build/unreal')
    parser.add_argument('--close',action='store_true',help='Штатно завершить только тестовый Unreal перед проверкой IMG')
    args=parser.parse_args()
    if args.close:
        close_test_emulator(args.directory)
    disk=ImageDisk(args.directory/'disk.img')
    expected=bytearray(bytes(range(256))*64+bytes(17000-16384))
    expected[511:517]=b'FAT32!'
    actual=disk.contents('FATDRVT1.BIN')
    assert actual==expected, 'Содержимое FATDRVT1.BIN не совпало'
    marker=disk.contents('FATDRVOK.TXT')
    assert marker==b'FAT32 standalone: APPEND, FILEX, sync OK\r\n'
    disk.assert_mirrors()
    disk.file.close()
    source=json.loads((args.directory/'source.json').read_text(encoding='utf-8'))
    source_hash=hashlib.sha256(Path(source['image']).read_bytes()).hexdigest()
    assert source_hash==source['sha256'], 'Исходный IMG изменился'
    result=dict(status='PASS',layer='Unreal with isolated SD model correction',
                file_size=len(actual),file_sha256=hashlib.sha256(actual).hexdigest(),
                marker=marker.decode('ascii').strip(),fat_mirrors_equal=True,
                original_image_unchanged=True,
                executable_sha256=hashlib.sha256((args.directory/'Unreal.exe').read_bytes()).hexdigest(),
                spg_sha256=hashlib.sha256((args.directory/'smoke.spg').read_bytes()).hexdigest(),
                driver_sha256=hashlib.sha256((args.directory/'fat32.bin').read_bytes()).hexdigest(),
                image_sha256=hashlib.sha256((args.directory/'disk.img').read_bytes()).hexdigest())
    (args.directory/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
