/* Run the actual ELF app sources and engine against a mock VDAC4 ABI.
 * Physical QSPI/I2S are replaced; WAD reads use the exact 70/71/E4 protocol. */
#define boolean win_boolean
#include <windows.h>
#undef boolean
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdarg.h>
#include <math.h>
#include "vd_port.h"
#include "vd_gpu.h"
#include "doomstat.h"
#include "g_game.h"
#include "p_saveg.h"
#include "i_video.h"
#include "ft_copro.h"
#include "r_local.h"
int vd_app_main(int,char**);
static const zm_app_t *app;
static uint8_t *wad_data;
static size_t wad_length,allocated,peak;
static CRITICAL_SECTION heap_lock;
typedef struct allocation {struct allocation *next; size_t size;} allocation;
static allocation *allocations;
static HANDLE threads[4];
static int thread_count,task_attempt,fail_task;
static unsigned stack_bytes;
static int old_bl616;
static uint8_t ram_g[0x400000];
static ftc_t copro;
static uint32_t dl[128],frame,source,palette_address;
static unsigned verified_frames;
static unsigned verified_video,fb_bytes,pal_bytes,last_fb_bytes,last_pal_bytes;
static uint64_t total_fb_bytes,total_pal_bytes;
static FILE *gpu_stats;
static unsigned gpu_writes,gpu_reads,cmd_bytes,col_bytes,flat_bytes,bl_texture_bytes;
static unsigned defer_gpu,poll_count;
static int fail_ft;
static uint8_t reported_status[6+VD_ERROR_MAX+1];
static unsigned reported_length;
static void comparison_snapshot(const char *name,const uint8_t *pixels);
static void bus_rd(void *ctx,uint32_t a,uint32_t *p,uint32_t n){
    if(a>=0x40000 && a+n*4<=0xf0000)bl_texture_bytes+=n*4;
    (void)ctx;if((a&3)||a+n*4>sizeof(ram_g))abort();memcpy(p,ram_g+a,n*4);
    /* Exercise the copro's real unknown-command fault/reset path, as v7
     * does not recognize the Doom probe. Leave all other commands intact. */
    if(old_bl616 && a>=FTC_RAM_CMD && a+n*4<=FTC_RAM_CMD+4096)
        for(unsigned i=0;i<n;i++)if(p[i]==FTDOOM_INFO)p[i]=0xfffffffe;
}
static void bus_wr(void *ctx,uint32_t a,const uint32_t *p,uint32_t n){
    (void)ctx;if((a&3)||a+n*4>sizeof(ram_g))abort();memcpy(ram_g+a,p,n*4);
#ifdef VD_GPU_VERIFY
    if(copro.cmd==FTDOOM_END && a+n*4==copro.p[0]+64000) {
        for(unsigned i=0;i<64000;i++)if(ram_g[copro.p[0]+i]!=I_VideoBuffer[i]) {
            comparison_snapshot("build/host/mismatch-reference.ppm",I_VideoBuffer);
            comparison_snapshot("build/host/mismatch-bl616.ppm",ram_g+copro.p[0]);
            fprintf(stderr,"GPU mismatch frame %u x %u y %u: %u expected %u\n",frame,i%320,i/320,ram_g[copro.p[0]+i],I_VideoBuffer[i]);abort();
        }
        verified_frames++;
        if(frame==300 || frame==340) {
            comparison_snapshot("build/host/renderer-software.ppm",I_VideoBuffer);
            comparison_snapshot("build/host/renderer-bl616.ppm",ram_g+copro.p[0]);
        }
    }
#endif
}
static void bus_status(void *ctx,ftc_status_t *s){
    (void)ctx;memset(s,0,sizeof(*s));
    memcpy(&s->cmd_write,ram_g+FTC_REG_CMD_WRITE,4);memcpy(&s->cmd_read,ram_g+FTC_REG_CMD_READ,4);
    memcpy(&s->cpureset,ram_g+FTC_REG_CPURESET,4);
}
void vd_gpu_host_check(unsigned at,unsigned n,unsigned stride,unsigned width) {
    for(unsigned i=0;i<n;i++,at+=stride)for(unsigned j=0;j<width;j++)if(copro.doom.pixels[at+j]!=I_VideoBuffer[at+j]) {
        fprintf(stderr,"PRIMITIVE x=%u y=%u GPU=%u CPU=%u column x=%d yl=%d yh=%d fracmid=%08x step=%08x\n",at%320+j,at/320,copro.doom.pixels[at+j],I_VideoBuffer[at+j],dc_x,dc_yl,dc_yh,dc_texturemid,dc_iscale);
        fprintf(stderr,"PARAMS %08x %08x %08x %08x %08x %08x %08x\n",copro.p[0],copro.p[1],copro.p[2],copro.p[3],copro.p[4],copro.p[5],copro.p[6]);abort();
    }
}
static unsigned requests,read_bytes,sound_samples;
/* Режим demo: чтения WAD посреди уровня демо (через 1 с после начала) — кадр стоит, пока они идут */
static unsigned demo_level_bytes,demo_level_reads;
static int test_mode,save_requested,save_done,load_requested,load_seen,loaded_ammo;
static int config_written,config_attempts,config_fail_op,config_fail_limit;
static int legacy_launcher;
static unsigned event_max,file_pulls,wire_checks,wake_checks;
static uint8_t f6_data[965],fail_reply;
static uint16_t f6_length;
static int f1_ok,f4_ok;
static _Thread_local int wire_command_active,wire_ack_delivered;
static uint32_t hotkeys_start;
extern int screenblocks,detailLevel,showMessages;
extern int novert;
void host_config_change(int kind);
extern int sfxVolume,musicVolume;
int host_music_volume(void);
int host_sfx_gain(int slider);
/* SD Evo на стенде; у режимов config своя — настройки из них не попадают в другие режимы */
static const char *card="build/host/card";
extern int mouseSensitivity,vdac4_mouse_scale,vdac4_bilinear,key_fire,key_use,key_strafeleft,key_straferight;
extern float mouse_acceleration;
extern char *chat_macros[10];
static int32_t loaded_x,loaded_y;
static double sound_energy;
static float sound_peak;
static FILE *pcm_file;
static FILE *wad_trace;
static void *host_alloc(size_t n,uint32_t caps) {
    (void)caps; EnterCriticalSection(&heap_lock);
    /* Leave 1.5 MiB of the board's 8 MiB for firmware + ELF text/BSS. */
    if(n>6500*1024 || allocated>6500*1024-n) {LeaveCriticalSection(&heap_lock);return NULL;}
    allocation *p=malloc(sizeof(*p)+n); if(!p) {LeaveCriticalSection(&heap_lock);return NULL;}
    p->size=n;p->next=allocations;allocations=p;allocated+=n;
    if(allocated>peak)peak=allocated;
    LeaveCriticalSection(&heap_lock);return p+1;
}
static void host_free(void *data) {
    if(!data)return;allocation *p=(allocation*)data-1;
    EnterCriticalSection(&heap_lock);allocation **link=&allocations;
    while(*link && *link!=p)link=&(*link)->next;
    if(!*link){fprintf(stderr,"untracked free\n");abort();}
    *link=p->next;allocated-=p->size;free(p);LeaveCriticalSection(&heap_lock);
}
static void *host_realloc(void *p,size_t size) {
    if(!p)return host_alloc(size,0); if(!size){host_free(p);return NULL;}
    void *q=host_alloc(size,0); if(q){size_t old=((allocation*)p-1)->size;memcpy(q,p,old<size?old:size);host_free(p);}return q;
}
static void logline(const char *fmt,...) {va_list ap;va_start(ap,fmt);vprintf(fmt,ap);va_end(ap);putchar('\n');fflush(stdout);}
static uint32_t millis(void){return GetTickCount();}
static void delay(uint32_t ms){if(defer_gpu && ms<=1)SwitchToThread();else Sleep(ms?ms:1);}
typedef struct{zm_task_fn fn;void *arg;} task;
static DWORD WINAPI thread_fn(void *p){task t=*(task*)p;free(p);t.fn(t.arg);return 0;}
static int task_start(zm_task_fn f,void *a,uint32_t stack,uint8_t priority,int8_t cpu){
    (void)priority;(void)cpu;
    if(++task_attempt==fail_task || stack>24*1024-stack_bytes)return ZM_NO_MEMORY;
    if(thread_count>=4)return -1;task *t=malloc(sizeof(*t));*t=(task){f,a};
    HANDLE h=CreateThread(NULL,0,thread_fn,t,0,NULL);if(!h){free(t);return -1;}threads[thread_count++]=h;stack_bytes+=stack;return 0;
}
static void *mutex_create(void){CRITICAL_SECTION *m=host_alloc(sizeof(*m),0);InitializeCriticalSection(m);return m;}
static void lock(void *m){EnterCriticalSection(m);}static void unlock(void *m){LeaveCriticalSection(m);}
static void *signal_create(void){return CreateEvent(NULL,FALSE,FALSE,NULL);}
static void signal_give(void *s){
    if(wire_command_active==0x71 || wire_command_active==0x74){if(!wire_ack_delivered)abort();wake_checks++;}
    SetEvent(s);
}static int signal_wait(void *s,uint32_t ms){return WaitForSingleObject(s,ms)==WAIT_OBJECT_0?0:-5;}
static int reply(uint8_t c,const void *p,uint16_t n){
    /* Кадр к Z80 — не больше 256 байт: аппаратная очередь ZiFi (NET_CHUNK ZiFi SPG). DOOM 0.1.8
     * отдавал данные записи одним F6 на 965 байт — на плате он терялся целиком. */
    if(n>256){printf("REPLY TOO LONG code=%02X bytes=%u limit=256\n",c,n);fflush(stdout);abort();}
    if(c==0xF1){if(n!=1)abort();f1_ok=*(const uint8_t*)p;}
    if(c==0xF4){if(n!=1)abort();f4_ok=*(const uint8_t*)p;}
    if(c==0xF6){if(n>sizeof(f6_data))abort();memcpy(f6_data,p,n);f6_length=n;}
    if(c==0xF3){if(n>sizeof(reported_status))abort();memcpy(reported_status,p,n);reported_length=n;}
    if(c==fail_reply){fail_reply=0;return ZM_ERROR;}
    if((c==0xF1 || c==0xF4) && n==1 && *(const uint8_t*)p)wire_ack_delivered=1;
    return 0;
}
/* Проверяем настоящий command(): чужой/повторный seq, преждевременный 74 и потерю ответа.
 * Эти вызовы не подменяют сериализацию движка и не модифицируют передаваемые данные. */
static void wire_ack(uint8_t cmd,const uint8_t *data,unsigned n,int expected){
    wire_command_active=cmd;wire_ack_delivered=0;f1_ok=f4_ok=-1;
    if(app->command(cmd,data,n)!=ZM_OK || (cmd==0x71?f1_ok:f4_ok)!=expected)abort();
    wire_command_active=0;wire_checks++;
}
/* 76 [seq, смещение] (n байт запроса) -> F6 [seq, ok, смещение, число, данные]: ok — expected,
 * число — length. Порция ложится в block+at. */
static void wire_fetch(const uint8_t *seq,unsigned at,unsigned n,int expected,unsigned length,uint8_t *block){
    uint8_t req[4]={seq[0],seq[1],(uint8_t)at,(uint8_t)(at>>8)};
    f6_length=0;
    if(app->command(0x76,req,n)!=ZM_OK || f6_length!=7+(expected?length:0) || f6_data[2]!=expected ||
       (f6_data[3]|f6_data[4]<<8)!=(expected?at:0) || (f6_data[5]|f6_data[6]<<8)!=(expected?length:0))abort();
    if(n>=2 && memcmp(f6_data,seq,2))abort();
    if(expected && block)memcpy(block+at,f6_data+7,length);
    wire_checks++;
}
static int event(uint8_t c,const void *p,uint16_t n,uint32_t timeout){
    (void)timeout;const uint8_t *b=p;
    /* Как Application::sendEvent: слишком длинное событие отвергается без ожидания. */
    if(n>63){printf("EVENT REJECT code=%02X bytes=%u limit=63\n",c,n);return ZM_BUSY;}
    if(n>event_max)event_max=n;
    if(c==0xE5) {
        if(n!=35 || b[15] || b[28])abort();
        char path[128],other[128];unsigned op=b[2],len=b[33]|b[34]<<8;
        uint32_t at=b[29]|b[30]<<8|b[31]<<16|b[32]<<24,size=0;
        snprintf(path,sizeof(path),"%s/%s",card,b+3);snprintf(other,sizeof(other),"%s/%s",card,b+16);
        int cfg=strlen((const char*)b+3)>4 && !strcmp((const char*)b+3+strlen((const char*)b+3)-4,".CFG");
        if(strchr((const char*)b+3,'/') || strchr((const char*)b+3,'\\') || len>960)abort();
        uint8_t answer[969]={b[0],b[1],0};unsigned count=0;FILE *f=NULL;int status=0;
        uint8_t block[960],foreign[3]={b[0]^1,b[1],0};
        wire_fetch(foreign,0,4,0,0,NULL);
        wire_fetch(b,0,1,0,0,NULL);
        wire_fetch(b,0,2,0,0,NULL);      /* запрос 0.1.8 без смещения */
        if(op==3 && !legacy_launcher){
            wire_ack(0x74,answer,9,0);  /* успех до получения данных запрещён */
            unsigned first=len<248?len:248;
            fail_reply=0xF6;wire_fetch(b,0,4,1,first,NULL);
            wire_ack(0x74,answer,9,0);  /* неотправленный F6 также не разрешает успех */
            if(len>248)wire_fetch(b,248,4,0,0,NULL);   /* порция за неотданными байтами */
            for(unsigned at=0;at<len;at+=248)wire_fetch(b,at,4,1,len-at<248?len-at:248,block);
            if(len)wire_fetch(b,0,4,1,first,block);    /* повтор порции (Z80 переспросил) */
            wire_fetch(b,len,4,0,0,NULL);              /* за концом блока */
            file_pulls++;
        } else if(op!=3)wire_fetch(b,0,4,0,0,NULL);
        switch(op) {
        case 0: f=fopen(path,"rb");if(!f){status=0x19;break;}fseek(f,0,SEEK_END);size=ftell(f);break;
        case 1: f=fopen(path,"rb");if(!f){status=0x19;break;}fseek(f,at,SEEK_SET);count=fread(answer+9,1,len,f);if(count!=len)status=1;break;
        case 2: if(cfg){config_attempts++;printf("CONFIG ATTEMPT %d ms=%u\n",config_attempts,millis()-hotkeys_start);}
            f=fopen(path,"wb");if(!f)status=0x21;break;
        /* config-fail: отказ SD на записи TEMP.CFG — как fail для сохранений */
        case 3: if(legacy_launcher){status=0x10;break;}
            if(test_mode==3 || (test_mode==8 && cfg) || (cfg && config_fail_op==3 && config_attempts<=config_fail_limit)){status=0x21;break;}f=fopen(path,"r+b");if(!f){status=0x21;break;}fseek(f,at,SEEK_SET);if(fwrite(block,1,len,f)!=len)status=0x21;break;
        case 4: if(cfg && config_fail_op==4 && config_attempts<=config_fail_limit){status=0x21;break;}
            if(!MoveFileExA(path,other,MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH))status=0x21;
            else if(cfg){config_written++;printf("CONFIG WRITTEN %s -> %s\n",b+3,b+16);}
            else {save_done++;printf("SAVED ammo=%d x=%d y=%d time=%d\n",players[0].ammo[am_clip],players[0].mo->x,players[0].mo->y,leveltime);}break;
        case 5: if(remove(path))status=0x19;break;
        case 6: break;
        default:abort();
        }
        if(f && fclose(f))status=0x21;
        answer[2]=status;answer[3]=count;answer[4]=count>>8;memcpy(answer+5,&size,4);
        answer[0]^=1;wire_ack(0x74,answer,count+9,0);answer[0]^=1;
        fail_reply=0xF4;wire_ack(0x74,answer,count+9,1);
        wire_ack(0x74,answer,count+9,1);
        wire_ack(0x74,answer,count+9,0);
        wire_fetch(b,0,4,0,0,NULL);
        return ZM_OK;
    }
    if(c!=0xE4 || n!=8)abort();
    uint32_t at=b[2]|b[3]<<8|b[4]<<16|b[5]<<24;unsigned len=b[6]|b[7]<<8;
    if(wad_trace)fwrite(b+2,6,1,wad_trace);
    if(at>wad_length || len>wad_length-at || len>1016)abort();
    uint8_t answer[1020];answer[0]=b[0];answer[1]=b[1];answer[2]=len;answer[3]=len>>8;memcpy(answer+4,wad_data+at,len);
    requests++;read_bytes+=len;
    if(gamestate==GS_LEVEL && demoplayback && leveltime>35){demo_level_bytes+=len;demo_level_reads++;}
    answer[0]^=1;wire_ack(0x71,answer,len+4,0);answer[0]^=1;
    fail_reply=0xF1;wire_ack(0x71,answer,len+4,1);
    wire_ack(0x71,answer,len+4,1);
    wire_ack(0x71,answer,len+4,0);
    return ZM_OK;
}
static int pcm_open(const zm_pcm_format_t *f){if(f->rate!=22050 || f->channels!=2)abort();return 0;}
static int pcm_write(const float *p,uint32_t n){
    for(unsigned i=0;i<n*2;i++) {
        if(!isfinite(p[i]) || fabsf(p[i])>1.001f)abort();
        sound_energy+=p[i]*p[i];if(fabsf(p[i])>sound_peak)sound_peak=fabsf(p[i]);
        int16_t s=(int16_t)(p[i]*32767);fwrite(&s,2,1,pcm_file);
    }
    sound_samples+=n*2;Sleep(n*1000/22050);return n;
}
/* pcm_write выше ждёт реальное время, очереди нет: ядро VDAC4 показало бы здесь только DMA */
static uint32_t pcm_queued_ms(void){return 0;}
static void nothing(void){}
static int ft_open(uint32_t timeout){(void)timeout;return fail_ft?ZM_NOT_FOUND:ZM_OK;}
static void snapshot(const char *name){
    FILE *f=fopen(name,"wb");fprintf(f,"P6\n320 200\n255\n");
    for(unsigned i=0;i<64000;i++) {
        unsigned index=ram_g[source+i];const uint8_t *p=ram_g+palette_address+index*2;
        unsigned c=p[0]|p[1]<<8;uint8_t rgb[3]={((c>>11)&31)*255/31,((c>>5)&63)*255/63,(c&31)*255/31};fwrite(rgb,1,3,f);
    }fclose(f);
}
static void comparison_snapshot(const char *name,const uint8_t *pixels){
    FILE *f=fopen(name,"wb");fprintf(f,"P6\n320 200\n255\n");
    for(unsigned i=0;i<64000;i++) {
        unsigned index=pixels[i];const uint8_t *p=ram_g+palette_address+index*2;
        unsigned c=p[0]|p[1]<<8;uint8_t rgb[3]={((c>>11)&31)*255/31,((c>>5)&63)*255/63,(c&31)*255/31};fwrite(rgb,1,3,f);
    }fclose(f);
}
static int ft_write(uint32_t address,const uint32_t *p,uint32_t n){
    gpu_writes++;
    /* буферы программного кадра #0, #10000, #30000 (третий — тройной буфер, без рендера BL616) */
    if((address<0x20000 && n*4<=0x20000-address) ||
       (!vd_gpu_enabled && address>=0x30000 && address+n*4<=0x3FA00))fb_bytes+=n*4;
    if(address>=0x20000 && address+n*4<=0x20600)pal_bytes+=n*4;
    if(address>=FTC_RAM_CMD && address<FTC_RAM_CMD+4096) {
        cmd_bytes+=n*4;
        uint32_t rp,wp;memcpy(&rp,ram_g+FTC_REG_CMD_READ,4);memcpy(&wp,ram_g+FTC_REG_CMD_WRITE,4);
        if(rp!=0xfff)for(unsigned i=0;i<n;i++) {
            unsigned at=address-FTC_RAM_CMD+i*4;
            if(((at-rp)&4095)<((wp-rp)&4095)) {
                fprintf(stderr,"Overwrite unread command: rp=%u wp=%u at=%u\n",rp,wp,at);abort();
            }
        }
    }
    if(address>=0x40000 && address<0xc0000)col_bytes+=n*4;
    if(address>=0xc0000 && address<0xf0000)flat_bytes+=n*4;
    if(address<0x100000 && n*4<=0x100000-address)memcpy(ram_g+address,p,n*4);
    else if(address>=0x300000 && address+n*4<=0x300000+sizeof(dl))memcpy((uint8_t*)dl+address-0x300000,p,n*4);
    else if(address==0x302054 && n==1){
        for(unsigned i=0;i<128;i++){if((dl[i]>>24)==1)source=dl[i]&0x3fffff;if((dl[i]>>24)==0x2a)palette_address=dl[i]&0x3fffff;}
        if(!vd_gpu_enabled && I_VideoBuffer) {
            if(memcmp(ram_g+source,I_VideoBuffer,64000)) {
                fprintf(stderr,"Software framebuffer mismatch at frame %u\n",frame);abort();
            }
            verified_video++;
        }
        last_fb_bytes=fb_bytes;last_pal_bytes=pal_bytes;
        total_fb_bytes+=fb_bytes;total_pal_bytes+=pal_bytes;fb_bytes=pal_bytes=0;
        /* после счётчиков выше: video-transfer ждёт смену листа из другого потока */
        __atomic_add_fetch(&frame,1,__ATOMIC_RELEASE);
        if(gpu_stats)fprintf(gpu_stats,"%u,%d,%u,%u,%u,%u,%u,%u\n",frame,gamestate,gpu_writes,gpu_reads,cmd_bytes,col_bytes,flat_bytes,bl_texture_bytes);
        gpu_writes=gpu_reads=cmd_bytes=col_bytes=flat_bytes=bl_texture_bytes=0;
        if(frame==1)snapshot("build/host/title.ppm");
        if(frame==50 && getenv("VD_TEST_LOW"))R_SetViewSize(8,1);
        if(test_mode==2 && frame==40) { G_LoadGame(P_SaveGameFile(0));load_requested=1; }
        if(test_mode==2 && load_requested && !load_seen && gamestate==GS_LEVEL) {
            load_seen=1;loaded_ammo=players[0].ammo[am_clip];loaded_x=players[0].mo->x;loaded_y=players[0].mo->y;
            printf("LOADED ammo=%d x=%d y=%d time=%d\n",loaded_ammo,loaded_x,loaded_y,leveltime);
        }
        if((test_mode==1 || test_mode==3) && !save_requested && gamestate==GS_LEVEL && leveltime>100) {
            G_SaveGame(0,"VDAC4 TEST");save_requested=1;
        }
        if(frame==340)snapshot("build/host/game.ppm");
        Sleep(15);
    } else if(address<sizeof(ram_g) && n*4<=sizeof(ram_g)-address){
        memcpy(ram_g+address,p,n*4);if(!defer_gpu)ftc_poll(&copro);
    } else abort();return 0;
}
static int ft_read(uint32_t a,uint32_t *p){gpu_reads++;if(a==0x302054){*p=0;return 0;}if(!defer_gpu || ++poll_count%4==0)ftc_poll(&copro);bus_rd(NULL,a,p,1);return 0;}
/* Клавиатура стенда — как у загрузчика 0.1.7: байты очереди PS/2 (набор 2) в пакете 75. За опрос —
 * набор клавиш «нажато сейчас» (код, E0); смена набора даёт нажатия и отпускания (F0). */
typedef struct {uint8_t code,ext;} ps2_key;
static ps2_key ps2_now[8],ps2_was[8];static unsigned ps2_now_n,ps2_was_n;
static void hold(uint8_t code,uint8_t ext){if(ps2_now_n<8){ps2_now[ps2_now_n].code=code;ps2_now[ps2_now_n].ext=ext;ps2_now_n++;}}
static int ps2_in(const ps2_key *set,unsigned n,ps2_key k){for(unsigned i=0;i<n;i++)if(set[i].code==k.code&&set[i].ext==k.ext)return 1;return 0;}
static unsigned ps2_bytes(uint8_t *out){
    unsigned n=0;
    for(unsigned i=0;i<ps2_was_n;i++)if(!ps2_in(ps2_now,ps2_now_n,ps2_was[i])){if(ps2_was[i].ext)out[n++]=0xE0;out[n++]=0xF0;out[n++]=ps2_was[i].code;}
    for(unsigned i=0;i<ps2_now_n;i++)if(!ps2_in(ps2_was,ps2_was_n,ps2_now[i])){if(ps2_now[i].ext)out[n++]=0xE0;out[n++]=ps2_now[i].code;}
    memcpy(ps2_was,ps2_now,sizeof(ps2_now));ps2_was_n=ps2_now_n;ps2_now_n=0;return n;
}
int main(int argc,char **argv){
    if(argc!=3){fprintf(stderr,"usage: host DOOM1.WAD save|load|fail|menu|mouse|config|config-fail|config-options|demo|startfail-game|startfail-audio\n");return 2;}
    fail_task=!strcmp(argv[2],"startfail-game")?1:!strcmp(argv[2],"startfail-audio")?2:0;
    fail_ft=!strcmp(argv[2],"ft-unavailable");
    old_bl616=getenv("VD_TEST_OLD_BL616")!=NULL || !strcmp(argv[2],"video-transfer");
    defer_gpu=getenv("VD_TEST_GPU_DEFER")!=NULL;
    test_mode=!strcmp(argv[2],"load")?2:!strcmp(argv[2],"fail")?3:!strcmp(argv[2],"menu")?4:!strcmp(argv[2],"mouse")?6:
              !strcmp(argv[2],"config")?7:!strcmp(argv[2],"config-fail")?8:!strcmp(argv[2],"config-options")?9:!strcmp(argv[2],"demo")?10:!strncmp(argv[2],"sound-",6)?11:(!strncmp(argv[2],"hotkeys-",8) || !strncmp(argv[2],"defaults-",9))?12:1;
    legacy_launcher=!strncmp(argv[2],"wire-old-spg-",13);
    if(legacy_launcher)test_mode=!strcmp(argv[2],"wire-old-spg-save")?3:9;
    if(test_mode>=7 && test_mode<=9)card="build/host/card-config";
    if(test_mode==11)card="build/host/card-sound";
    if(test_mode==12)card="build/host/card-hotkeys";
    if(!strncmp(argv[2],"defaults-",9))card="build/host/card-types";
    config_fail_op=!strcmp(argv[2],"hotkeys-fail-rename")?4:
                   (!strcmp(argv[2],"hotkeys-fail-write") || !strcmp(argv[2],"hotkeys-recover"))?3:0;
    config_fail_limit=!strcmp(argv[2],"hotkeys-recover")?2:1000000;
    CreateDirectoryA(card,NULL);
    FILE *f=fopen(argv[1],"rb");if(!f)return 2;fseek(f,0,SEEK_END);wad_length=ftell(f);rewind(f);wad_data=malloc(wad_length);fread(wad_data,1,wad_length,f);fclose(f);
    pcm_file=fopen("build/host/audio.s16le","wb");if(!pcm_file)return 2;
    if(getenv("VD_TEST_GPU_STATS")) {
        gpu_stats=fopen("build/host/gpu-stats.csv","w");
        fprintf(gpu_stats,"frame,gamestate,writes,reads,commands,column_upload,flat_upload,bl_texture_read\n");
    }
    if(getenv("VD_TEST_TRACE"))wad_trace=fopen("build/host/wad-requests.bin","wb");
    InitializeCriticalSection(&heap_lock);
    ftc_bus_t bus={0};bus.rd=bus_rd;bus.wr=bus_wr;bus.status=bus_status;ftc_init(&copro,&bus);
    zm_core_t core={0};core.size=sizeof(core);core.abi_major=1;core.abi_minor=3;core.alloc=host_alloc;core.realloc=host_realloc;core.free=host_free;core.log=logline;core.millis=millis;core.delay=delay;
    core.task_start=task_start;core.mutex_create=mutex_create;core.mutex_lock=lock;core.mutex_unlock=unlock;core.signal_create=signal_create;core.signal_give=signal_give;core.signal_wait=signal_wait;
    core.reply=reply;core.event=event;core.pcm_open=pcm_open;core.pcm_write=pcm_write;core.pcm_queued_ms=pcm_queued_ms;core.pcm_flush=nothing;core.pcm_close=nothing;core.ft_open=ft_open;core.ft_write=ft_write;core.ft_read=ft_read;core.ft_close=nothing;
    if(!strcmp(argv[2],"video-transfer")) {
        test_mode=5;vd_core=&core;vd_running=1;I_VideoBuffer=calloc(1,64000);
        uint32_t pal[128];for(unsigned i=0;i<128;i++)pal[i]=i*0x1010101u;
        vd_video_init();
        const uint32_t frame0=frame;
        for(unsigned i=0;i<16;i++) {
            if(i==4)I_VideoBuffer[0]=17;
            if(i==6)I_VideoBuffer[63999]=29;
            if(i==8)memset(I_VideoBuffer,123,64000);
            if(i==10)pal[127]^=0xffffffffu;
            if(i==13){I_VideoBuffer[63]=45;I_VideoBuffer[64]=67;}
            vd_video_frame(I_VideoBuffer,pal);
            /* выгрузка кадра — своя задача (video.c, up_async): проверять, когда кадр дошёл до
               смены листа (bus_wr REG_DLSWAP считает frame); раньше проверка шла сразу и
               успевала только по случайности тайминга */
            for(unsigned t=0;frame<frame0+i+1 && t<5000;t++)Sleep(1);
            if(frame<frame0+i+1)return 9;
            if(memcmp(ram_g+palette_address,pal,512))return 5;
            /* тройной буфер: кадр i пишется в буфер i % 3, где лежит кадр i − 3, — нужны
               изменения кадров i − 2 … i; первые три кадра — целиком */
            if(source!=(const uint32_t[]){0,0x10000,0x30000}[i%3])return 10;
            if((i==3 || i==11 || i==12) && last_fb_bytes)return 6;
            if((i<3 || (i>=4 && i<=10) || i>=13) && !last_fb_bytes)return 11;
            if(((i>=3 && i<=9) || i>=13) && last_pal_bytes)return 7;
            if((i<3 || (i>=10 && i<=12)) && !last_pal_bytes)return 12;
        }
        vd_running=0;                        /* задаче выгрузки — выйти до конца процесса */
        for(unsigned t=0;!vd_video_stopped() && t<2000;t++)Sleep(1);
        fclose(pcm_file);
        printf("VIDEO verified=%u pixel_bytes=%llu palette_bytes=%llu full_frame_baseline=%u\n",verified_video,
            (unsigned long long)total_fb_bytes,(unsigned long long)total_pal_bytes,frame*64512);
        return verified_video==16 && total_fb_bytes+total_pal_bytes<frame*64512/2?0:8;
    }
    char *args[]={"doom",(char*)&core,(char*)&app};if(vd_app_main(3,args)!=1)return 3;
    int started=app->start(&core);
    if(fail_task) {
        uint32_t before=millis();app->stop();unsigned elapsed=millis()-before;
        int joined=!thread_count || WaitForMultipleObjects(thread_count,threads,TRUE,1000)==WAIT_OBJECT_0;
        fclose(pcm_file);
        printf("STARTFAIL result=%d attempt=%d threads=%d joined=%d stop_ms=%u wad_reads=%u\n",started,task_attempt,thread_count,joined,elapsed,requests);
        return started==ZM_NO_MEMORY && task_attempt==fail_task && joined && elapsed<1000 && !requests?0:1;
    }
    if(started)return 3;
    uint8_t config[14];memcpy(config,&wad_length,4);memcpy(config+4,"DOOM1.WAD",10);app->command(0x70,config,14);
    if(fail_ft) {
        uint32_t start=millis();
        while(__atomic_load_n(&vd_state,__ATOMIC_ACQUIRE)!=255 && millis()-start<10000)Sleep(10);
        app->command(0x73,NULL,0);
        int good=reported_length==6+sizeof("FT812 OPEN FAILED: CODE -3") && reported_status[0]==255 &&
                 !strcmp((const char*)reported_status+6,"FT812 OPEN FAILED: CODE -3") && !frame && !gpu_writes;
        app->stop();WaitForMultipleObjects(thread_count,threads,TRUE,20000);fclose(pcm_file);
        printf("FTFAIL status=%u bytes=%u message=%s frames=%u gpu_writes=%u\n",reported_status[0],reported_length,reported_status+6,frame,gpu_writes);
        return good?0:1;
    }
    uint32_t start=millis(),ready=0,menu_start=0;
    unsigned target=test_mode==4?520:test_mode==6?5000:test_mode==10?1000000:test_mode>=7?1000000:350;
    /* Режимы config: DEFAULT.CFG/DOOM.CFG прочитаны до первого кадра игры (vd_state 2). Меню на
     * демо: Esc, вниз (Options), Enter, 4 раза вниз (Mouse sensitivity), вправо (+1), Esc, Esc,
     * 2 раза вверх (Quit Game), Enter, Y — I_Quit пишет настройки. Клавиша [скан-код PS/2, E0]
     * держится 150 мс, шаг 400 мс; стрелки — с E0. Режим config-options — до первого Esc (выход
     * из Options), без Quit Game: настройки пишет уже выход из Options. */
    static const uint8_t config_keys[][2]={{0x76,0},{0x72,1},{0x5A,0},{0x72,1},{0x72,1},{0x72,1},{0x72,1},{0x74,1},
                                           {0x76,0},{0x76,0},{0x75,1},{0x75,1},{0x5A,0},{0x35,0}};
    const unsigned config_count=test_mode==9?9:sizeof(config_keys)/2;
    int config_seen=0;uint32_t config_start=0;
    /* Sound Volume через PS/2: Options -> вверх (последний пункт) -> Enter.
     * Backspace возвращает в Options; Esc закрывает меню; F4 открывает звук напрямую. */
    uint8_t sound_keys[][2]={{0x76,0},{0x72,1},{0x5A,0},{0x75,1},{0x5A,0},
                           {0x74,1},{0x72,1},{0x74,1},{0x66,0},{0x66,0}};
    int sound_f4=!strcmp(argv[2],"sound-f4"),sound_back=!strcmp(argv[2],"sound-back");
    int sound_burst=!strcmp(argv[2],"sound-burst"),sound_burst_sent=0;
    int sound_unchanged=!strcmp(argv[2],"sound-unchanged");
    int sound_delta=!strcmp(argv[2],"sound-max")?-1:1;
    if(sound_delta<0)sound_keys[5][0]=sound_keys[7][0]=0x6B;
    if(sound_unchanged)sound_keys[5][0]=sound_keys[7][0]=0;
    if(!strcmp(argv[2],"sound-esc") || sound_f4)sound_keys[8][0]=0x76;
    if(sound_f4)sound_keys[4][0]=0x0C;  /* F4 */
    if(sound_burst){sound_keys[1][0]=0x76;sound_keys[1][1]=0;}
    unsigned sound_count=(!strcmp(argv[2],"sound-back-close") || sound_unchanged)?10:9;
    int sound_sfx=0,sound_music=0,sound_before=0,sound_after=0,sound_start_ok=0,sound_picture=0;
    /* Горячие клавиши проходят через очередь PS/2. Три нажатия с шагом 400 мс:
     * запись допустима лишь после секунды покоя от последнего изменения.
     * При отказе SD вторую серию начинаем через 4 с; между ними повторов быть не должно. */
    int hot_read=!strcmp(argv[2],"hotkeys-read"),hot_noop=!strcmp(argv[2],"hotkeys-noop");
    int typed=!strncmp(argv[2],"defaults-",9),typed_sent=0;
    int typed_kind=!strcmp(argv[2],"defaults-int")?1:!strcmp(argv[2],"defaults-float")?2:
                   !strcmp(argv[2],"defaults-string")?3:!strcmp(argv[2],"defaults-key")?4:0;
    int hot_revert=!strcmp(argv[2],"hotkeys-revert");
    uint8_t hot_keys[]={0x4E,0x4E,0x4E};  /* - - - */
    if(!strcmp(argv[2],"hotkeys-plus") || hot_noop)memset(hot_keys,0x55,sizeof(hot_keys));
    if(!strcmp(argv[2],"hotkeys-gamma")){hot_keys[0]=0x78;hot_keys[1]=hot_keys[2]=0;}     /* F11 */
    if(!strcmp(argv[2],"hotkeys-detail")){hot_keys[0]=0x03;hot_keys[1]=hot_keys[2]=0;}    /* F5 */
    if(!strcmp(argv[2],"hotkeys-messages")){hot_keys[0]=0x0A;hot_keys[1]=hot_keys[2]=0;}  /* F8 */
    if(hot_revert){hot_keys[1]=0x55;hot_keys[2]=0;}
    /* Режим mouse: пакеты 72 по 12 байт — кнопки, X, Y мыши Kempston. В уровне: 0,6 с мышь
     * вперёд (Y растёт вверх), 0,6 с вправо, 1 с левая кнопка. Замеры — по mobj игрока. */
    uint8_t mouse_b=0xFF,mouse_x=0x10,mouse_y=0x20;
    uint32_t mouse_start=0;int mouse_done=0,mouse_moved=0,mouse_ammo=0;
    fixed_t mouse_x0=0,mouse_y0=0,mouse_x1=0,mouse_y1=0;angle_t mouse_a0=0,mouse_a1=0,mouse_a2=0;
    while(frame<target && vd_state!=255 && vd_state!=3 && millis()-start<60000 && !mouse_done){
        uint8_t keys[4+64];
        if(vd_state==2){
            if(!ready)ready=millis();uint32_t t=millis()-ready;
            if(test_mode==10 && gamestate==GS_LEVEL && demoplayback && leveltime>=35*12)break;  /* 12 с демо */
            if(test_mode==12) {
                if(!config_seen) {
                    config_seen=1;
                    printf("HOTKEYS READ screenblocks=%d detaillevel=%d show_messages=%d usegamma=%d\n",
                           screenblocks,detailLevel,showMessages,usegamma);
                    if(typed)printf("DEFAULTS READ novert=%d mouse_acceleration=%.3f key_fire=%d chatmacro0=%s\n",
                                    novert,mouse_acceleration,key_fire,chat_macros[0]);
                }
                if(!hotkeys_start && gamestate==GS_LEVEL && leveltime>35)hotkeys_start=millis();
                uint32_t u=hotkeys_start?millis()-hotkeys_start:0;
                unsigned k=u<500?~0u:(u-500)/400;
                if(!hot_read && !typed && k<3 && (u-500)%400<150 && hot_keys[k])hold(hot_keys[k],0);
                if(typed_kind && !typed_sent && u>=500){host_config_change(typed_kind);typed_sent=1;}
                if(config_fail_op && u>=4500 && u<4650)hold(0x55,0);  /* следующее изменение */
                if(u>(config_fail_op?8000u:4500u))break;
            }
            if(test_mode==11) {
                if(!config_seen) {
                    config_seen=1;sound_sfx=sfxVolume;sound_music=musicVolume;
                    sound_start_ok=host_music_volume()==sound_music*8;
                    printf("SOUND READ sfx=%d music=%d mixer_music=%d\n",sound_sfx,sound_music,host_music_volume());
                }
                if(!config_start && gamestate==GS_LEVEL && leveltime>35)hotkeys_start=config_start=millis();
                uint32_t u=config_start?millis()-config_start:0;
                unsigned k=u<500?~0u:(u-500)/400;
                if(k!=~0u && k<5 && host_sfx_gain(sound_sfx))sound_before=1;
                if(k!=~0u && k>=4 && host_sfx_gain(sound_sfx+(sound_unchanged?0:sound_delta)))sound_after=1;
                if(k<sound_count && (u-500)%400<150 && (!sound_f4 || k>=4) && (!sound_burst || k<2) && sound_keys[k][0])
                    hold(sound_keys[k][0],sound_keys[k][1]);
                if(sound_burst && k==4 && !sound_burst_sent) {
                    /* Один пакет 75 (21 байт PS/2): F4, SFX +1, вниз, Music +1, Esc.
                     * Все события попадут в M_Responder до следующего M_Ticker. */
                    const uint8_t burst[]={0,255,16,32,0x0C,0xF0,0x0C,
                        0xE0,0x74,0xE0,0xF0,0x74,0xE0,0x72,0xE0,0xF0,0x72,
                        0xE0,0x74,0xE0,0xF0,0x74,0x76,0xF0,0x76};
                    app->command(0x75,burst,sizeof(burst));sound_burst_sent=1;
                }
                if(k==5 && !sound_picture){snapshot("build/host/sound-menu.ppm");sound_picture=1;}
                if(k!=~0u && k>=sound_count+4)break;
            }
            if(test_mode>=7 && test_mode<=9) {
                if(!config_seen) {
                    config_seen=1;
                    printf("CONFIG READ mouse_sensitivity=%d vdac4_mouse_scale=%d vdac4_bilinear=%d mouse_acceleration=%.3f key_fire=%d key_use=%d key_strafeleft=%d key_straferight=%d sfx=%d music=%d mixer_music=%d chatmacro0=%s\n",
                        mouseSensitivity,vdac4_mouse_scale,vdac4_bilinear,mouse_acceleration,key_fire,key_use,key_strafeleft,key_straferight,sfxVolume,musicVolume,host_music_volume(),chat_macros[0]);
                }
                /* через секунду демо: смена экрана (wipe) не опрашивает клавиши и стоит leveltime —
                 * нажатие на ней пропало бы */
                if(!config_start && gamestate==GS_LEVEL && leveltime>35)config_start=millis();
                uint32_t u=config_start?millis()-config_start:0;
                unsigned k=u<500?~0u:(u-500)/400;
                if(k<config_count && (u-500)%400<150)hold(config_keys[k][0],config_keys[k][1]);
                if(test_mode==9 && k!=~0u && k>=config_count+4)break;     /* 1,6 с после выхода из Options */
            }
            if(test_mode!=2 && test_mode<7 && ((t>=500&&t<650)||(t>=1000&&t<1150)||(t>=1500&&t<1650)||(t>=2000&&t<2150)))hold(0x5A,0);  /* Enter */
            if(test_mode==4 && gamestate==GS_LEVEL && leveltime>100 && !menu_start)menu_start=millis();
            if(test_mode==6 && gamestate==GS_LEVEL && leveltime>70) {
                mobj_t *mo=players[0].mo;
                if(!mouse_start){mouse_start=millis();mouse_x0=mo->x;mouse_y0=mo->y;mouse_a0=mo->angle;mouse_ammo=players[0].ammo[am_clip];}
                uint32_t u=millis()-mouse_start;
                if(u<600)mouse_y+=2;
                else if(u<700){if(!mouse_moved){mouse_moved=1;mouse_x1=mo->x;mouse_y1=mo->y;mouse_a1=mo->angle;}}
                else if(u<1300)mouse_x+=2;
                else if(u<1400)mouse_a2=mo->angle;
                else if(u<2400)mouse_b=0xFE;
                else if(u<2600)mouse_b=0xFF;
                else mouse_done=1;
            }
            if(test_mode!=2 && test_mode!=6 && test_mode<7 && !save_done && !menu_start && t>2800){hold(0x1D,0);hold(0x2B,0);}  /* W — вперёд, F — огонь */
            if(menu_start && !save_done) {
                uint32_t u=millis()-menu_start;
                if(u<150)hold(0x06,0);                          /* F2 — меню сохранения */
                if(u>=500&&u<650)hold(0x5A,0);                  /* Enter — слот */
                if(u>=1000&&u<1150)hold(0x23,0);                /* «d» — имя (data2), не стрелка */
                if(u>=1500&&u<1650)hold(0x5A,0);
            }
        }
        keys[0]=0;keys[1]=mouse_b;keys[2]=mouse_x;keys[3]=mouse_y;
        app->command(0x75,keys,(uint16_t)(4+ps2_bytes(keys+4)));Sleep(20);
    }
    int state=vd_state;app->stop();WaitForMultipleObjects(thread_count,threads,TRUE,20000);fclose(pcm_file);if(wad_trace)fclose(wad_trace);if(gpu_stats)fclose(gpu_stats);
    printf("WIRE max_event=%u pulls=%u checks=%u wake_after_reply=%u legacy=%d\n",event_max,file_pulls,wire_checks,wake_checks,legacy_launcher);
    printf("BL616 pixel-identical frames=%u\n",verified_frames);
    printf("VIDEO verified=%u pixel_bytes=%llu palette_bytes=%llu full_frame_baseline=%u\n",verified_video,
        (unsigned long long)total_fb_bytes,(unsigned long long)total_pal_bytes,frame*64512);
    if(old_bl616 && (vd_gpu_enabled || copro.fault || copro.rp || copro.wp))return 4;
    printf("RESULT state=%d level=%d:%d gamestate=%d tics=%d ammo=%d frames=%u reads=%u bytes=%u peak_heap=%zu sound_peak=%.5f sound_rms=%.5f\n",state,gameepisode,gamemap,gamestate,leveltime,players[0].ammo[am_clip],frame,requests,read_bytes,peak,sound_peak,sqrt(sound_energy/(sound_samples?sound_samples:1)));
    if(test_mode==3)return state==255 && save_requested && !save_done?0:1;
    /* выход из меню — штатный (3) и при отказе SD на записи настроек (config-fail) */
    if(test_mode==10) {
        printf("DEMO level=%d:%d tics=%d level_reads=%u level_bytes=%u\n",gameepisode,gamemap,leveltime,demo_level_reads,demo_level_bytes);
        return state==2 && demoplayback && gamestate==GS_LEVEL && leveltime>=35*12?0:1;
    }
    if(test_mode==11) {
        int delta=sound_unchanged?0:sound_delta;
        printf("SOUND END state=%d sfx=%d music=%d mixer_music=%d gain_before=%d gain_after=%d writes=%d menu=%d\n",
               state,sfxVolume,musicVolume,host_music_volume(),sound_before,sound_after,config_written,menuactive);
        return state==2 && sound_start_ok && sound_before && sound_after &&
               sfxVolume==sound_sfx+delta && musicVolume==sound_music+delta &&
               host_music_volume()==musicVolume*8 && config_written==(sound_unchanged?0:2) &&
               menuactive==(sound_back || !strcmp(argv[2],"sound-max") || !strcmp(argv[2],"sound-back-close") || sound_unchanged)?0:1;
    }
    if(test_mode==12) {
        if(typed)printf("DEFAULTS END novert=%d mouse_acceleration=%.3f key_fire=%d chatmacro0=%s\n",
                        novert,mouse_acceleration,key_fire,chat_macros[0]);
        printf("HOTKEYS END state=%d screenblocks=%d detaillevel=%d show_messages=%d usegamma=%d attempts=%d writes=%d menu=%d\n",
               state,screenblocks,detailLevel,showMessages,usegamma,config_attempts,config_written,menuactive);
        return state==2 && config_seen && !menuactive?0:1;
    }
    if(test_mode==9) {
        printf("CONFIG OPTIONS state=%d mouse_sensitivity=%d menu=%d\n",state,mouseSensitivity,menuactive);
        return state==2 && config_seen && !menuactive?0:1;
    }
    if(test_mode>=7) {
        printf("CONFIG QUIT state=%d mouse_sensitivity=%d\n",state,mouseSensitivity);
        return state==3 && config_seen?0:1;
    }
    if(test_mode==6) {
        double a=mouse_a0*(2*3.14159265358979/4294967296.0);
        double along=((mouse_x1-mouse_x0)*cos(a)+(mouse_y1-mouse_y0)*sin(a))/65536.0;
        double turn=(int32_t)(mouse_a1-mouse_a2)*(360.0/4294967296.0);
        printf("MOUSE forward=%.1f units; right turn=%.1f deg; ammo %d -> %d\n",along,turn,mouse_ammo,players[0].ammo[am_clip]);
        return state==2 && mouse_done && gamestate==GS_LEVEL && along>8 && turn>30 && turn<150 &&
               players[0].ammo[am_clip]<mouse_ammo?0:1;
    }
    return state==2 && frame>=350 && gamestate==GS_LEVEL && leveltime>70 && players[0].ammo[am_clip]<50 && sound_peak>0.01f && (test_mode==2?load_seen:save_done)?0:1;
}
