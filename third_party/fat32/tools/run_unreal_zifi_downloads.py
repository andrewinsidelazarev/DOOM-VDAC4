"""HTTP -> MicroPython UART -> исходный ZiFi Z80 -> FAT32 -> тестовый IMG.

В RAM задаются только URL/действие, обычно выбранные мышью в меню.
Буфер загрузки, thread.adress, длина, код и вызовы FAT32 не подменяются.
"""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import argparse
import hashlib
import json
import threading
import time

from unreal_zifi_memory import Memory
from verify_unreal import ImageDisk,close_test_emulator


def main():
    # Настоящая плата живёт в локальной сети и до 127.0.0.1 не дотянется:
    # с --host сервер слушает все интерфейсы, а Z80 получает этот адрес.
    parser=argparse.ArgumentParser()
    parser.add_argument('--timeout',type=float,default=180.0,
                        help='предел ожидания одного файла; настоящая плата по UART медленнее')
    parser.add_argument('--host',default='127.0.0.1',
                        help='адрес этой машины, видимый плате, например 192.168.1.50')
    args=parser.parse_args()
    cases=[('uart513.bin',513,0,32),('uartoffset.bin',33001,4,32),
           ('uartmusic.pt3',32764,4,29),('uart640k.bin',655360,0,32)]
    payloads={name:bytes((i*31+i//16384+size)%251 for i in range(size))
              for name,size,offset,page in cases}
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name=self.path.lstrip('/')
            entry=next((case for case in cases if case[0]==name),None)
            if entry is None:
                self.send_error(404); return
            data=payloads[name]
            if entry[2]: data=b'.'+name.rsplit('.',1)[1].encode()+data
            requests.append({'name':name,'bytes':len(data)})
            self.send_response(200)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Connection','close')
            self.end_headers()
            self.wfile.write(data)
        def log_message(self,*args): pass

    bind='127.0.0.1' if args.host=='127.0.0.1' else ''
    server=ThreadingHTTPServer((bind,0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    m=Memory()
    directory=m.directory
    try:
        assert m.get('fat_active')==b'\0' and m.get('load_sw',offset=1)==b'\0'
        date=m.get('DIR_date',12)[1:].split(b'\0')[0].decode()
        assert date[:2]=='20',date
        report={'layer':'Unreal Z80 + local MicroPython UART + local HTTP + SD IMG',
                'date':date,'files':[],'requests':requests,'physical_esp':False}
        for name,size,offset,page in cases:
            m.put('cmd_conn2site_adr',args.host.encode()+bytes(1))
            m.put('request_port',server.server_port.to_bytes(2,'little'))
            m.put('request_path',b'/'+name.encode()+b'\0')
            m.put('load_ram_page',bytes([page]),offset=1)
            m.put('do_after_load',b'\3',offset=1)
            m.put('fat_last_error',b'\0')
            m.put('load_sw',b'\1',offset=1)
            began=time.monotonic(); last=began; ready=None
            print('Downloading',name,size,'page',hex(page),flush=True)
            while time.monotonic()-began<args.timeout:
                time.sleep(.05)
                length=int.from_bytes(m.get('readed_len_low',2,offset=1),'little')
                length+=m.get('readed_len_high',offset=1)[0]<<16
                if time.monotonic()-last>10:
                    print('Received',length,'of',size+offset,flush=True);last=time.monotonic()
                filename=m.get('FILE_NAME',48).split(b'\0')[0].decode('ascii',errors='replace')
                done=(filename==name and m.get('load_sw',offset=1)==b'\0'
                      and m.get('fat_active')==b'\0' and m.get('fat_remaining',3)==b'\0'*3)
                if done:
                    ready=ready or time.monotonic()
                    if time.monotonic()-ready>.3: break
                else: ready=None
                if m.get('fat_last_error')!=b'\0':
                    raise AssertionError(('save failed',name,m.get('fat_last_error').hex()))
            else:
                # Стенд задаёт запрос записью в RAM эмулятора. Модель кэша TS-Conf
                # отдаёт чтения Z80 из своей копии, поэтому без сброса тегов Z80
                # не увидит взведённый load_sw и загрузка просто не начнётся.
                if length==0 and m.get('load_sw',offset=1)!=bytes(1):
                    raise AssertionError(('Z80 не увидел запрос стенда: запустите Unreal с '
                                          'FAT32_CACHE_SYNC=1 и EXE, собранным с --host-poke-sync',name))
                raise AssertionError(('download timeout',name,length,m.get('ProtoErrText',48)))
            thread=m.get('read_threads',12)
            address=int.from_bytes(thread[m.sym['thread.adress']:m.sym['thread.adress']+2],'little')
            assert address==0x4000+offset,(name,hex(address))
            assert thread[m.sym['thread.page']]==page
            report['files'].append({'name':name,'size':size,'source_address':hex(address),
                                    'source_page':hex(page),'sha256':hashlib.sha256(payloads[name]).hexdigest()})
            print('Saved',name,'source',hex(address),flush=True)
        (directory/'runtime-ram.bin').write_bytes(m.read(m.base,4*1024*1024))
    finally:
        m.close()
        server.shutdown()
        close_test_emulator(directory)
    disk=ImageDisk(directory/'disk.img')
    cluster=2
    for component in ('zifi','downloads',date): cluster=disk.get(component,cluster)['cluster']
    for result in report['files']:
        actual=disk.contents(result['name'],cluster)
        assert actual==payloads[result['name']],('IMG bytes mismatch',result['name'])
    disk.assert_mirrors();disk.file.close()
    report['fat_mirrors_equal']=True
    source=json.loads((directory/'source.json').read_text(encoding='utf-8'))
    from pathlib import Path
    assert hashlib.sha256(Path(source['source_image']).read_bytes()).hexdigest()==source['source_image_sha256']
    report['source_image_unchanged']=True
    report['spg_sha256']=source['spg_sha256']
    report['status']='PASS'
    (directory/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__': main()
