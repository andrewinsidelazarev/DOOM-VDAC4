"""Доступ только к RAM отдельного тестового Unreal по проверенному PID/пути."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
from verify_unreal import ROOT
from harness import symbols


class Memory:
    def __init__(self,directory=None):
        self.directory=directory or ROOT/'build/unreal-zifi'
        self.pid=int((self.directory/'process-id.txt').read_text(encoding='utf-8-sig'))
        self.k=c.WinDLL('kernel32',use_last_error=True)
        k=self.k
        k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]; k.OpenProcess.restype=w.HANDLE
        k.ReadProcessMemory.argtypes=[w.HANDLE,c.c_void_p,c.c_void_p,c.c_size_t,c.POINTER(c.c_size_t)]
        k.WriteProcessMemory.argtypes=k.ReadProcessMemory.argtypes
        k.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD)]
        k.CloseHandle.argtypes=[w.HANDLE]
        self.handle=k.OpenProcess(0x438,False,self.pid)
        if not self.handle: raise c.WinError(c.get_last_error())
        path=c.create_unicode_buffer(32768); length=w.DWORD(len(path))
        assert k.QueryFullProcessImageNameW(self.handle,0,path,c.byref(length))
        assert Path(path.value).resolve()==(self.directory/'Unreal.exe').resolve()
        self.sym=symbols(ROOT.parent/'ZiFi ESP32-S3 Zero/ZiFi SPG/build/zifi.sym')
        self.base=self.locate()

    def read(self,address,size):
        data=c.create_string_buffer(size); count=c.c_size_t()
        if not self.k.ReadProcessMemory(self.handle,address,data,size,c.byref(count)):
            raise c.WinError(c.get_last_error())
        return data.raw[:count.value]

    def locate(self):
        class MBI(c.Structure):
            _fields_=[('base',c.c_void_p),('allocation',c.c_void_p),('ap',w.DWORD),
                      ('partition',w.WORD),('size',c.c_size_t),('state',w.DWORD),
                      ('protect',w.DWORD),('type',w.DWORD)]
        self.k.VirtualQueryEx.argtypes=[w.HANDLE,c.c_void_p,c.POINTER(MBI),c.c_size_t]
        pattern=(ROOT.parent/'ZiFi ESP32-S3 Zero/ZiFi SPG/build/zifi.bin').read_bytes()[:24]
        address=0
        while address<0x7FFFFFFFFFFF:
            m=MBI()
            if not self.k.VirtualQueryEx(self.handle,address,c.byref(m),c.sizeof(m)): break
            if m.state==0x1000 and m.protect&0xEE and not m.protect&0x100 and 4*1024*1024<=m.size<64*1024*1024:
                data=self.read(m.base,m.size)
                pos=data.find(pattern)
                while pos>=0:
                    begin=pos-32768
                    if begin>=0 and len(data)>=begin+4*1024*1024 and data[begin+14*16384]==0xC3:
                        return m.base+begin
                    pos=data.find(pattern,pos+1)
            address=(m.base or 0)+m.size
        raise RuntimeError('RAM ZiFi ещё не найдена')

    def get(self,name,size=1,offset=0):
        return self.read(self.base+self.sym[name]+offset,size)

    def put(self,name,data,offset=0):
        assert 0x8000<=self.sym[name]+offset<0xBE00
        count=c.c_size_t()
        assert self.k.WriteProcessMemory(self.handle,self.base+self.sym[name]+offset,data,len(data),c.byref(count))
        assert count.value==len(data)

    def close(self):
        self.k.CloseHandle(self.handle)


if __name__=='__main__':
    m=Memory()
    print('RAM',hex(m.base))
    for name,size in [('DIR_date',12),('load_sw',2),('fat_active',1),('FILE_NAME',48),
                      ('read_threads',12),('ProtoErrText',48),('NetHttpCode',2)]:
        print(name,m.get(name,size))
    (m.directory/'runtime-ram.bin').write_bytes(m.read(m.base,4*1024*1024))
    m.close()
