"""Подготовка отдельной копии Unreal и IMG для проверки через эмулятор SD."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--emulator',type=Path,default=Path.home()/'Desktop/Unreal')
    parser.add_argument('--image',type=Path)
    args=parser.parse_args()
    image=args.image or args.emulator/'nemo.img'
    target=ROOT/'build/unreal'
    target.mkdir(exist_ok=True)
    for file in args.emulator.iterdir():
        if file.is_file() and (file.suffix.lower() in ('.dll','.ini') or file.name in ('Unreal.exe','CMOS','NVRAM')):
            shutil.copy2(file,target/file.name)
    shutil.copytree(args.emulator/'rom',target/'rom',dirs_exist_ok=True)
    shutil.copy2(image,target/'disk.img')
    for name in ('fat32.bin','fat32-work.bin'):
        shutil.copy2(ROOT/'build'/name,target/name)
    config=(target/'unreal.ini').read_bytes().decode('cp1251')
    config=re.sub(r'(?m)^SDCARD=.*','SDCARD=disk.img',config)
    config=re.sub(r'(?m)^Image[01]=.*','Image0=',config,count=1)
    config=re.sub(r'(?m)^Image1=.*','Image1=',config)
    config=re.sub(r'(?m)^diskA=.*','diskA=',config)
    config=re.sub(r'(?m)^;?snapshot=.*','snapshot=smoke.spg',config)
    config=re.sub(r'(?m)^ConfirmExit=.*','ConfirmExit=0',config)
    config=re.sub(r'(?m)^SPGMemInit=.*','SPGMemInit=2',config)
    (target/'unreal.ini').write_bytes(config.encode('cp1251'))
    assembler=os.environ.get('SJASMPLUS',r'C:\z80\zuma\sjasmplus.exe')
    subprocess.run([assembler,'--nologo','--inc='+str(ROOT/'include'),str(ROOT/'tests/unreal_smoke.asm')],cwd=target,check=True)
    spg=ROOT.parent/'ZiFi ESP32-S3 Zero/ZiFi SPG/_spg'
    for name in ('spgbld.exe','mhmt.exe'):
        shutil.copy2(spg/name,target/name)
    (target/'smoke.ini').write_text('''Desc=FAT32 test
Start=#8000
Stack=#BFFF
Resident=#5B00
Page3=#20
Clock=2
INT=0
Pager=0
Block=#c000,2,smoke.bin
Block=#c000,#0e,fat32.bin
Block=#c000,#0f,fat32-work.bin
''',encoding='utf-8')
    # spgbld запускает mhmt.exe по PATH, а не из своего каталога: у ZiFi это
    # делает build.bat. Без этого упаковка молча падает и SPG не создаётся.
    env=dict(os.environ)
    env['PATH']=str(target)+os.pathsep+env.get('PATH','')
    subprocess.run([str(target/'spgbld.exe'),'-b','smoke.ini','smoke.spg'],
                   cwd=target,env=env,check=True)
    (target/'source.json').write_text(json.dumps({'image':str(image),'sha256':hashlib.sha256(image.read_bytes()).hexdigest()},indent=2),encoding='utf-8')
    print(target)


if __name__=='__main__':
    main()
