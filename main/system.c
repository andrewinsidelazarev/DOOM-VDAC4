/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "compat.h"
#include "i_system.h"
#include "doomgeneric.h"
#include "m_config.h"
void vd_checkpoint(void) { if(!vd_running) longjmp(vd_exit,1); }
char vd_error[VD_ERROR_MAX+1];
void vd_fail(const char *s) {
    vd_core->log("doom: ERROR %s",s);
    unsigned i=0;
    for(;i<VD_ERROR_MAX && s[i];i++)
        vd_error[i]=(unsigned char)s[i]>=32 && (unsigned char)s[i]<=126?s[i]:' ';
    vd_error[i]=0;
    /* Publish the message before the command task observes the failure. */
    __atomic_store_n(&vd_state,255,__ATOMIC_RELEASE);
    longjmp(vd_exit,1);
}
void I_Error(char *fmt,...) { char buf[256]; va_list ap; va_start(ap,fmt); vsnprintf(buf,sizeof(buf),fmt,ap); va_end(ap); vd_fail(buf); }
byte *I_ZoneBase(int *size) {
    for(int kb=4096;kb>=2048;kb-=256) {
        byte *p=vd_malloc(kb*1024); if(p) { *size=kb*1024; vd_core->log("doom: zone %d KiB",kb); return p; }
    }
    I_Error("Not enough PSRAM for Doom zone"); return NULL;
}
/* Выход из меню: DEFAULT.CFG и DOOM.CFG пишутся через TEMP.CFG (m_config.c). Отказ SD
 * (статус в ответе 74) только оставляет прежние файлы — выход штатный, состояние 3. */
void I_Quit(void) { M_SaveDefaults(); vd_state=3; longjmp(vd_exit,1); }
void I_AtExit(atexit_func_t f,boolean error) { (void)f; (void)error; /* core owns all allocations */ }
boolean I_ConsoleStdout(void) { return false; }
void I_Tactile(int a,int b,int c) { (void)a;(void)b;(void)c; }
void I_PrintBanner(char *s) { vd_core->log("%s",s); }
void I_PrintDivider(void) {}
void I_PrintStartupBanner(char *s) { I_PrintBanner(s); }
boolean I_GetMemoryValue(unsigned offset,void *value,int size) { (void)offset; memset(value,0,size); return true; }
void DG_SleepMs(uint32_t ms) { vd_checkpoint(); vd_core->delay(ms); }
uint32_t DG_GetTicksMs(void) { vd_checkpoint(); return vd_core->millis(); }
void DG_SetWindowTitle(const char *s) { vd_core->log("doom: %s",s); }
