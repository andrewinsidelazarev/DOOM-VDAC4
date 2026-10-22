from pathlib import Path
import re,subprocess,os
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'build/host'
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    vc=r'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat'
    clang=os.environ.get('CLANG_CL',r'E:\zx\toolchain\llvm\bin\clang-cl.exe')
    cmake=(ROOT/'main/CMakeLists.txt').read_text()
    names=re.search(r'set\(NAMES (.*?)\)',cmake,re.S)[1].split()
    sources=[ROOT/'third_party/doomgeneric/doomgeneric'/f'{n}.c' for n in names]+list((ROOT/'main').glob('*.c'))+[ROOT/'tests/host.c']
    sources=[ROOT/'tests/audio_probe.c' if p==ROOT/'main/sound.c' else p for p in sources]
    config_source=ROOT/'third_party/doomgeneric/doomgeneric/m_config.c'
    sources=[ROOT/'tests/config_probe.c' if p==config_source else p for p in sources]
    sources += [ROOT/'firmware/bl616/copro'/f'{n}.c' for n in ['ft_copro','ft_inflate','ft_3d','ft_doom']]
    lines=['@echo off',f'call "{vc}" >nul 2>nul || exit /b 1']
    objs=[]
    for source in sources:
        obj=OUT/(('engine_' if 'third_party' in str(source) else '')+source.stem+'.obj');objs.append(obj)
        opts='/DVDAC4 '
        if os.environ.get('VD_GPU_VERIFY'):opts+=' /DVD_GPU_VERIFY '
        if 'third_party' in str(source) or source.name=='config_probe.c':opts+=f'/FI"{ROOT}/include/compat.h"'
        if source.name=='app.c':opts+=' /Dmain=vd_app_main'
        lines.append(f'"{clang}" --target=i686-pc-windows-msvc /nologo /c /O2 /std:c11 /D_CRT_SECURE_NO_WARNINGS /DNORMALUNIX /DLINUX /I"{ROOT}/tests/stubs" /I"{ROOT}/include" /I"{ROOT}/third_party/doomgeneric/doomgeneric" /I"{ROOT}/firmware/bl616/copro" /clang:-Wno-incompatible-pointer-types /clang:-Wno-int-conversion /clang:-Wno-pointer-sign /clang:-fwrapv {opts} "{source}" /Fo"{obj}" || exit /b 1')
    lines.append(f'"{clang}" --target=i686-pc-windows-msvc '+ ' '.join(f'"{p}"' for p in objs)+f' /Fe"{OUT}/host.exe" /link advapi32.lib shell32.lib user32.lib || exit /b 1')
    script=OUT/'build.bat';script.write_text('\n'.join(lines)+'\n')
    with (OUT/'build.log').open('w') as log:
        result=subprocess.run(['cmd','/c',str(script)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:print((OUT/'build.log').read_text(errors='replace')[-8000:])
    raise SystemExit(result.returncode)
if __name__=='__main__':main()
