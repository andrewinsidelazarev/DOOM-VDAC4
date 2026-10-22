"""Локальный UART-стенд: исходный SPG, копия IMG и Windows MicroPython.

Настройки, INI и модель прошивки изолированы от установленного Unreal/ESP.
"""
from pathlib import Path
import argparse
import ctypes
import hashlib
import json
import re
import shutil
import sys

from verify_unreal import ImageDisk, ROOT
from harness import Driver

CRLF=bytes([13,10])


def short(path):
    out=ctypes.create_unicode_buffer(32768)
    assert ctypes.windll.kernel32.GetShortPathNameW(str(path),out,len(out))
    return out.value


def main():
    # --com COM4 сажает стенд на настоящую плату вместо локальной модели ESP.
    # Модель говорит на том же протоколе, но это MicroPython-рантайм от ESP-01S,
    # а не текущая Native C++ прошивка S3: её поведение стенд не проверяет.
    parser=argparse.ArgumentParser()
    parser.add_argument('--com',help='имя COM-порта реальной платы, например COM4')
    parser.add_argument('--ini',type=Path,
                        help='рабочий zifi.ini для стенда на настоящей плате')
    args=parser.parse_args()
    if args.com and not args.ini:
        raise SystemExit('С --com обязателен --ini с настоящим zifi.ini: '
                         'иначе стенд отправит на плату выдуманную сеть и '
                         'затрёт её сохранённые настройки Wi-Fi.')
    ini_body=(args.ini.read_bytes() if args.ini
              else b'SSID:UART-TEST'+CRLF+b'password:fixture'+CRLF+b'time:none'+CRLF)
    source=ROOT/'build/unreal'
    target=ROOT/'build/unreal-zifi'
    target.mkdir(exist_ok=True)
    for file in source.iterdir():
        if file.is_file() and (file.suffix.lower() in ('.dll',) or file.name in ('Unreal.exe','CMOS','NVRAM')):
            shutil.copy2(file,target/file.name)
    shutil.copytree(source/'rom',target/'rom',dirs_exist_ok=True)
    shutil.copy2(source/'disk.img',target/'disk.img')
    project=ROOT.parent/'ZiFi ESP32-S3 Zero/ZiFi SPG'
    shutil.copy2(project/'build/zifi.spg',target/'zifi.spg')
    shutil.copytree(ROOT.parent/'ZiFi Micro Python/Firmware/build/uart_runtime',target/'uart_runtime',dirs_exist_ok=True)
    # Фиксированное время отделяет UART/HTTP/SD от доступности внешнего NTP.
    (target/'uart_runtime/ntptime.py').write_text('def settime():\n    pass\n',encoding='utf-8')
    config=(source/'unreal.ini').read_bytes().decode('cp1251')
    config=re.sub(r'(?m)^snapshot=.*','snapshot=zifi.spg',config)
    config=re.sub(r'(?mi)^driver=.*','driver=gdi',config)
    config=re.sub(r'(?mi)^flip=.*','flip=0',config)
    # Снимок экрана стенда: штатный BMP-рендер Unreal.
    config=re.sub(r'(?mi)^ScrShot=.*','ScrShot=BMP',config)
    bridge=[]
    if args.com:
        bridge=['ZiFi='+args.com]
    else:
        bridge=[
            'ZiFi=UART',r'ZiFiUARTExe=C:\mp\ports\windows\build-standard\micropython.exe',
            'ZiFiUARTDir='+short(target/'uart_runtime'),'ZiFiUARTScript=_uart_run.py',
            'ZiFiUARTHeap=1M']
    config=re.sub(r'(?m)^ZiFi=.*',lambda m:chr(10).join(bridge),config)
    (target/'unreal.ini').write_bytes(config.encode('cp1251'))

    class WritableImage(ImageDisk):
        def write(self,lba,data):
            assert len(data)==512 and 0<=lba<self.start+self.total
            self.file.seek(lba*512)
            self.file.write(data)

    disk=WritableImage(target/'disk.img')
    disk.file.close()
    disk.file=(target/'disk.img').open('r+b')
    seed=Driver(disk)
    assert seed.mkdir('zifi')==(0,True,False)
    seed.find('zifi',16)
    seed.call(31)
    assert seed.create('zifi.ini')==(0,True,False)
    # ZiFi при старте отправляет этот файл на плату командой WIFI_INI, а прошивка
    # сохраняет его в LittleFS и поднимает по нему Wi-Fi после reset. Значит на
    # настоящей плате выдуманная сеть затрёт рабочие настройки. Для --com берём
    # только тот ini, который явно указан ключом --ini.
    assert seed.append(ini_body)==(0,True,False)
    disk.file.flush()
    disk.file.close()
    manifest={'spg':str(project/'build/zifi.spg'),
              'spg_sha256':hashlib.sha256((target/'zifi.spg').read_bytes()).hexdigest(),
              'source_image':str(source/'disk.img'),
              'source_image_sha256':hashlib.sha256((source/'disk.img').read_bytes()).hexdigest(),
              'uart':'Windows MicroPython, isolated existing .mpy runtime, 1 MiB heap',
              'physical_esp':False}
    (target/'source.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(target)


if __name__=='__main__':
    main()
