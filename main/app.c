/* DOOM.ELF, VDAC4 ABI 1.3. SPDX-License-Identifier: GPL-2.0-or-later */
#include "vd_port.h"
#include <string.h>
#include "m_argv.h"
#include "doomgeneric.h"
#include "vd_gpu.h"
#include "vd_loader.h"
const zm_core_t *vd_core;
volatile int vd_running, vd_state;
uint32_t vd_wad_size, vd_frames;
uint32_t vd_render_ms,vd_render_count,vd_qspi_ms,vd_swap_ms;
char vd_wad_name[13];
jmp_buf vd_exit;
static void *io_signal, *io_mutex;
static volatile int game_done, audio_done;
static uint16_t request_id, wanted, received;
static int awaiting;
static int fs_awaiting,fs_status,fs_op;
static uint32_t fs_size;
static uint16_t fs_received;
static uint8_t fs_response[960];
/* Данные записи живут до ответа 74; Z80 забирает их командой 76 [seq, смещение] порциями до
 * FS_PIECE байт (ответ F6). Предел события ядра ABI 1.3 — 63 байта; кадр к Z80 — не больше
 * 256 байт: аппаратная очередь ZiFi меньше килобайта, кадр ~1 КБ на плате терялся целиком
 * (0.1.8: Z80 ждал F6, DOOM — «Evo file operation timeout»). Так же порциями по 255 байт
 * тянет данные ZiFi SPG (NET_CHUNK) и режет запись VFS-мост прошивки (по 256). */
#define FS_PIECE 248
static uint8_t fs_write[960],fs_piece[7+FS_PIECE];
static uint16_t fs_write_sent;               /* сколько байт с начала отдано подряд */
static uint8_t response[1016];
/* Клавиатура — байты очереди PS/2 контроллера AVR Evo из пакета 75 как есть (загрузчик Z80 читает
 * регистр Gluk #F0, матрицу ZX не читает); разбор — в video.c. Кольцо на 256 байт: не влезло —
 * ps2_lost, и разбор получит #FF, как при переполнении очереди AVR (клавиши отпускаются).
 * joy_state — биты Kempston-джойстика (0 вправо, 1 влево, 2 вниз, 3 вверх, 4 огонь). */
static uint8_t ps2_ring[256];
static uint8_t ps2_head,ps2_tail,ps2_lost,joy_state;
static uint32_t keys_time;
/* Мышь Kempston из пакета 72: последние счётчики X/Y (по модулю 256), кнопки (бит 0 левая,
 * 1 правая, 2 средняя) и приращения, накопленные до следующего vd_mouse_take. Разность
 * считается на каждом принятом пакете — между пакетами Z80 (единицы мс) счётчик не успевает
 * уйти на 128. mouse_sync=0 — следующий пакет только задаёт отсчёт. */
static uint8_t mouse_x,mouse_y,mouse_buttons;
static int mouse_sync;
static int32_t mouse_dx,mouse_dy;
static uint8_t *cache;
static uint32_t cache_at=UINT32_MAX, cache_length;
/* Чтения WAD через Evo в журнал раз в 5 с: сколько, за сколько и самое долгое — чтение посреди
 * уровня держит кадр (рывки демо 0.1.6 были отсюда). */
static uint32_t wad_bytes,wad_ms,wad_longest;
static uint16_t rd16(const uint8_t *p) { return p[0]|((uint16_t)p[1]<<8); }
static uint32_t rd32(const uint8_t *p) { return rd16(p)|((uint32_t)rd16(p+2)<<16); }
static void wr16(uint8_t *p,uint16_t v) { p[0]=v; p[1]=v>>8; }
static void wr32(uint8_t *p,uint32_t v) { wr16(p,v); wr16(p+2,v>>16); }

static size_t remote_read(uint32_t offset,void *data,size_t size) {
    uint8_t request[8];
    vd_checkpoint();
    vd_core->mutex_lock(io_mutex);
    ++request_id; wanted=(uint16_t)size; received=0; awaiting=1;
    wr16(request,request_id); wr32(request+2,offset); wr16(request+6,wanted);
    vd_core->mutex_unlock(io_mutex);
    if(vd_core->event(0xE4,request,sizeof(request),1000)!=ZM_OK) vd_fail("WAD request queue full");
    uint32_t start=vd_core->millis();
    for(;;) {
        vd_checkpoint();
        vd_core->signal_wait(io_signal,20);
        vd_core->mutex_lock(io_mutex);
        int done=!awaiting;
        if(done) { size=received; memcpy(data,response,size); vd_load_bytes+=size; }
        vd_core->mutex_unlock(io_mutex);
        if(done) {
            uint32_t took=vd_core->millis()-start;
            wad_bytes+=size; wad_ms+=took; if(took>wad_longest) wad_longest=took;
            break;
        }
        if((uint32_t)(vd_core->millis()-start)>10000) vd_fail("Evo WAD read timeout");
    }
    return size;
}
size_t vd_wad_read(uint32_t offset,void *out,size_t length) {
    if(offset>=vd_wad_size) return 0;
    if(length>vd_wad_size-offset) length=vd_wad_size-offset;
    uint8_t *dest=out; size_t done=0;
    while(done<length) {
        vd_checkpoint();
        if(cache_at==UINT32_MAX || offset<cache_at || offset-cache_at>=cache_length) {
            cache_at=offset&~4095u; cache_length=vd_wad_size-cache_at;
            if(cache_length>4096) cache_length=4096;
            for(uint32_t at=0;at<cache_length;) {
                uint32_t n=cache_length-at; if(n>sizeof(response)) n=sizeof(response);
                if(remote_read(cache_at+at,cache+at,n)!=n) vd_fail("FAT32 WAD read failed");
                at+=n;
            }
        }
        size_t n=cache_length-(offset-cache_at); if(n>length-done) n=length-done;
        memcpy(dest+done,cache+offset-cache_at,n); offset+=n; done+=n;
    }
    return done;
}
/* Байты PS/2 с прошлого вызова (не больше max) и джойстик. Пакетов нет дольше 500 мс (загрузчик
 * не опрашивает клавиатуру) — *stale: клавиши и джойстик отпустить. */
size_t vd_keys_take(uint8_t *out,size_t max,int *joy,int *stale) {
    size_t n=0;
    vd_core->mutex_lock(io_mutex);
    *stale=(uint32_t)(vd_core->millis()-keys_time)>500;
    *joy=*stale?0:joy_state;
    if(ps2_lost && n<max) { out[n++]=0xFF; ps2_lost=0; }
    while(n<max && ps2_tail!=ps2_head) out[n++]=ps2_ring[ps2_tail++];
    vd_core->mutex_unlock(io_mutex); return n;
}
/* Кнопки мыши и приращения X/Y (Y растёт вверх) с прошлого вызова. Пакетов нет дольше
 * 500 мс — кнопки отпущены, как клавиши. */
int vd_mouse_take(int *dx,int *dy) {
    vd_core->mutex_lock(io_mutex);
    int buttons=(uint32_t)(vd_core->millis()-keys_time)>500?0:mouse_buttons;
    *dx=mouse_dx; *dy=mouse_dy; mouse_dx=mouse_dy=0;
    vd_core->mutex_unlock(io_mutex); return buttons;
}
/* p — кнопки (#FADF, нажатая — 0), X (#FBDF), Y (#FFDF). Все три #FF — мыши нет или AVR
 * её переинициализирует (счётчики потом начнутся с 0/1): отсчёт заново, без скачка. После
 * паузы в пакетах дольше 500 мс — тоже заново. Вызов под io_mutex. */
static void mouse_packet(const uint8_t *p,int stale) {
    if(p[0]==0xFF && p[1]==0xFF && p[2]==0xFF) { mouse_sync=0; mouse_buttons=0; return; }
    if(mouse_sync && !stale) {
        mouse_dx+=(int8_t)(uint8_t)(p[1]-mouse_x);
        mouse_dy+=(int8_t)(uint8_t)(p[2]-mouse_y);
    }
    mouse_x=p[1]; mouse_y=p[2]; mouse_sync=1;
    mouse_buttons=~p[0]&7;
}
int vd_fs_call(unsigned op,const char *name,const char *other,uint32_t offset,void *data,unsigned length,uint32_t *size) {
    uint8_t request[35]={0};
    if(op>6 || length>960 || (length && !data) || strlen(name)>12 || (other && strlen(other)>12)) return -1;
    vd_checkpoint();
    vd_core->mutex_lock(io_mutex);
    ++request_id; fs_awaiting=1; fs_received=0; wanted=length;fs_op=op;fs_write_sent=0;
    wr16(request,request_id); request[2]=op; strcpy((char*)request+3,name);
    if(other) strcpy((char*)request+16,other);
    wr32(request+29,offset); wr16(request+33,length);
    if(op==3 && length) memcpy(fs_write,data,length);
    vd_core->mutex_unlock(io_mutex);
    if(vd_core->event(0xE5,request,sizeof(request),1000)!=ZM_OK) vd_fail("File request queue full");
    uint32_t start=vd_core->millis();
    for(;;) {
        vd_checkpoint(); vd_core->signal_wait(io_signal,20);
        vd_core->mutex_lock(io_mutex);
        int done=!fs_awaiting,result=fs_status;
        if(done) {
            if(size) *size=fs_size;
            if(op==1 && !result) { if(fs_received!=length) result=-1; else memcpy(data,fs_response,length); }
        }
        vd_core->mutex_unlock(io_mutex);
        if(done) return result;
        if((uint32_t)(vd_core->millis()-start)>10000) vd_fail("Evo file operation timeout");
    }
}
static int command(uint8_t cmd,const uint8_t *p,uint16_t n) {
    uint8_t ok=0;
    if(cmd==0x70) {
        if(vd_state==0 && n>=6 && n<=17 && p[n-1]==0 && rd32(p)>=12) {
            if(!strcmp((const char*)p+4,"DOOM1.WAD") || !strcmp((const char*)p+4,"DOOM.WAD") || !strcmp((const char*)p+4,"DOOM2.WAD")) {
                vd_wad_size=rd32(p); memcpy(vd_wad_name,p+4,n-4); ok=1;
            }
        }
        vd_core->reply(0xF0,&ok,1);
        if(ok) { __atomic_store_n(&vd_state,1,__ATOMIC_RELEASE); vd_core->signal_give(io_signal); }
    } else if(cmd==0x71) {
        vd_core->mutex_lock(io_mutex);
        if(n>=4 && n<=1020 && awaiting && rd16(p)==request_id && rd16(p+2)==n-4 && n-4<=wanted) {
            received=n-4; memcpy(response,p+4,received); ok=1;
        }
        /* Ответ должен уйти до пробуждения игры и следующего запроса. */
        if(vd_core->reply(0xF1,&ok,1)!=ZM_OK)ok=0;
        if(ok) awaiting=0;
        vd_core->mutex_unlock(io_mutex);
        if(ok) vd_core->signal_give(io_signal);
    } else if(cmd==0x74) {
        vd_core->mutex_lock(io_mutex);
        if(n>=9 && n<=969 && fs_awaiting && rd16(p)==request_id && rd16(p+3)==n-9 && n-9<=wanted
           && (fs_op==1 || n==9) && (fs_op!=3 || p[2]!=0 || fs_write_sent==wanted)) {
            fs_status=p[2]; fs_size=rd32(p+5); fs_received=n-9;
            memcpy(fs_response,p+9,fs_received); ok=1;
        }
        if(vd_core->reply(0xF4,&ok,1)!=ZM_OK)ok=0;
        if(ok) fs_awaiting=0;
        vd_core->mutex_unlock(io_mutex);
        if(ok) vd_core->signal_give(io_signal);
    } else if(cmd==0x76) {
        /* 76 [seq, смещение] -> F6 [seq, 1, смещение, число, данные] — порция с этого смещения, не
         * больше FS_PIECE байт (кадр 255). Чужой seq, смещение за отданным или за концом —
         * F6 [seq, 0, 0, 0]. Повтор порции разрешён (Z80 переспрашивает, если кадр не дошёл);
         * смещение в ответе не даёт Z80 принять запоздавший дубликат за следующую порцию. Успешный
         * ответ 74 на запись принимается, только когда все байты отданы подряд. */
        uint8_t rejected[7]={0};
        if(n>=2)memcpy(rejected,p,2);
        vd_core->mutex_lock(io_mutex);
        unsigned at=n==4?rd16(p+2):0;
        if(n==4 && fs_awaiting && fs_op==3 && rd16(p)==request_id && at<wanted && at<=fs_write_sent) {
            unsigned count=wanted-at; if(count>FS_PIECE)count=FS_PIECE;
            memcpy(fs_piece,p,2);fs_piece[2]=1;wr16(fs_piece+3,at);wr16(fs_piece+5,count);
            memcpy(fs_piece+7,fs_write+at,count);
            if(vd_core->reply(0xF6,fs_piece,7+count)==ZM_OK && at+count>fs_write_sent)fs_write_sent=at+count;
        } else vd_core->reply(0xF6,rejected,sizeof(rejected));
        vd_core->mutex_unlock(io_mutex);
    } else if(cmd==0x75) {
        /* [джойстик Kempston, кнопки мыши, X, Y, байты очереди PS/2…] — загрузчик 0.1.7+ */
        if(n>=4) {
            vd_core->mutex_lock(io_mutex);
            uint32_t now=vd_core->millis();
            joy_state=p[0]&31;
            mouse_packet(p+1,(uint32_t)(now-keys_time)>500);
            keys_time=now;
            for(unsigned i=4;i<n;i++) {
                if((uint8_t)(ps2_head+1)==ps2_tail) { ps2_lost=1; break; }
                ps2_ring[ps2_head++]=p[i];
            }
            vd_core->mutex_unlock(io_mutex); ok=1;
        }
        vd_core->reply(0xF5,&ok,1);
    } else if(cmd==0x73) {
        uint8_t status[6+VD_ERROR_MAX+1]={(uint8_t)__atomic_load_n(&vd_state,__ATOMIC_ACQUIRE),1,0,0,0,0};
        unsigned length=6;
        if(status[0]==255) {
            unsigned n=(unsigned)strlen(vd_error)+1;
            memcpy(status+6,vd_error,n);length+=n;
        }
        wr32(status+2,vd_frames); vd_core->reply(0xF3,status,length);
    } else return ZM_ERROR;
    return ZM_OK;
}
static void audio_worker(void *arg) { vd_audio_task(arg); __atomic_store_n(&audio_done,1,__ATOMIC_RELEASE); }
/* Каталог WAD — проверка и оценка для полосы загрузки (vd_load_est): что прочитает загрузка до
 * первого кадра — спрайты (R_InitSpriteLumps читает все), патчи стен (R_GenerateLookup — все
 * патчи текстур), PLAYPAL, COLORMAP, PNAMES, TEXTURE1/2, GENMIDI, графика строки состояния и
 * шрифт HU (ST*), TITLEPIC и музыка заставки, каталог дважды (здесь и W_Init); +5 % — блоки
 * кэша по 4 КБ. Флэты, уровни, звуки и прочая графика читаются уже в игре. */
static int lump_is(const uint8_t *n,const char *s) { return !strncmp((const char*)n,s,8); }
static void validate_wad(void) {
    uint8_t h[16];
    if(vd_wad_read(0,h,12)!=12 || memcmp(h,"IWAD",4))vd_fail("Invalid IWAD header");
    uint32_t count=rd32(h+4),offset=rd32(h+8);
    if(!count || count>262144 || offset>vd_wad_size || count>(vd_wad_size-offset)/16)vd_fail("Invalid IWAD directory");
    uint64_t est=12+2*16*(uint64_t)count;
    int section=0;                              /* 1 — спрайты, 2 — патчи, 3 — флэты */
    for(uint32_t i=0;i<count;i++) {
        if(vd_wad_read(offset+i*16,h,16)!=16)vd_fail("Truncated IWAD directory");
        uint32_t at=rd32(h),size=rd32(h+4);
        if(at>vd_wad_size || size>vd_wad_size-at)vd_fail("IWAD lump outside file");
        const uint8_t *n=h+8;
        if(lump_is(n,"S_START") || lump_is(n,"SS_START")) section=1;
        else if(lump_is(n,"P_START") || lump_is(n,"PP_START")) section=2;
        else if(lump_is(n,"F_START") || lump_is(n,"FF_START")) section=3;
        else if(lump_is(n,"S_END") || lump_is(n,"SS_END") || lump_is(n,"P_END") || lump_is(n,"PP_END") ||
                lump_is(n,"F_END") || lump_is(n,"FF_END")) section=0;
        else if(section==1 || section==2) est+=size;
        else if(!section && (lump_is(n,"PLAYPAL") || lump_is(n,"COLORMAP") || lump_is(n,"PNAMES") ||
                lump_is(n,"TEXTURE1") || lump_is(n,"TEXTURE2") || lump_is(n,"GENMIDI") ||
                lump_is(n,"TITLEPIC") || lump_is(n,"D_INTRO") || lump_is(n,"D_DM2TTL") ||
                (n[0]=='S' && n[1]=='T'))) est+=size;
    }
    vd_load_est=(uint32_t)(est+est/20);
    vd_core->log("doom: loading estimate %u bytes from the WAD",(unsigned)vd_load_est);
}
static void game_worker(void *arg) {
    (void)arg;
    if(!setjmp(vd_exit)) {
        while(__atomic_load_n(&vd_state,__ATOMIC_ACQUIRE)==0) { vd_checkpoint(); vd_core->signal_wait(io_signal,20); }
        /* сначала FT812 (ft_open: Tang — в ядро FT812) и экран загрузки, потом WAD: каталог и
         * загрузка DOOM идут уже под полосой */
        vd_video_init();
        vd_loader_start();
        validate_wad();
        char *args[]={"doom","-iwad",vd_wad_name,"-nogui",NULL};
        myargc=4; myargv=args;
        extern void D_DoomMain(void);
        D_DoomMain();
        __atomic_store_n(&vd_state,2,__ATOMIC_RELEASE);
        uint32_t last=vd_core->millis(),frames=vd_frames;
        while(vd_running) {
            doomgeneric_Tick(); vd_core->delay(1);
            uint32_t now=vd_core->millis(),elapsed=now-last;
            if(elapsed>=5000) {
                uint32_t n=vd_frames-frames,fps10=n*10000/elapsed;
                vd_core->log("doom: %u.%u fps; render %u ms/view; QSPI %u ms/frame; swap wait %u ms/frame; upload wait %u ms/frame; pace wait %u ms/frame; late %u; WAD %u KB in %u ms, longest %u ms",
                    (unsigned)(fps10/10),(unsigned)(fps10%10),
                    (unsigned)(vd_render_count?vd_render_ms/vd_render_count:0),
                    (unsigned)(n?vd_qspi_ms/n:0),(unsigned)(n?vd_swap_ms/n:0),
                    (unsigned)(n?vd_upload_wait_ms/n:0),(unsigned)(n?vd_pace_ms/n:0),
                    (unsigned)vd_late_frames,(unsigned)(wad_bytes/1024),(unsigned)wad_ms,(unsigned)wad_longest);
                wad_bytes=wad_ms=wad_longest=0;
                vd_gpu_report();
                vd_video_report();
                vd_render_ms=vd_render_count=vd_qspi_ms=vd_swap_ms=vd_upload_wait_ms=vd_pace_ms=vd_late_frames=0;last=now;frames=vd_frames;
            }
        }
    }
    vd_audio_reset();
    __atomic_store_n(&game_done,1,__ATOMIC_RELEASE);
}
static int start(const zm_core_t *c) {
    vd_core=c;
    game_done=audio_done=1; vd_running=0; vd_state=0;
    vd_error[0]=0;
    if(c->abi_major!=1 || c->size<offsetof(zm_core_t,ft_close)+sizeof(c->ft_close) || !c->ft_open || !c->ft_write || !c->ft_read) return ZM_ERROR;
    io_signal=c->signal_create(); io_mutex=c->mutex_create();
    cache=vd_malloc(4096);
    if(!io_signal || !io_mutex || !cache) {
        c->log("doom: start failed: IO signal/mutex/cache allocation"); return ZM_NO_MEMORY;
    }
    vd_running=1;
    extern int vd_audio_init(void);
    int result=vd_audio_init();
    if(result!=ZM_OK) {
        vd_running=0; c->log("doom: start failed: audio initialization (%d)",result); return result;
    }
    vd_audio_reset();
    /* Allocate the larger stack first. ABI task stacks consume internal
     * SRAM even though the ELF, zone and PCM buffer live in PSRAM. */
    game_done=0;
    result=c->task_start(game_worker,NULL,16384,3,1);
    if(result!=ZM_OK) {
        game_done=1; vd_running=0;
        c->log("doom: start failed: game task, 16384-byte stack (%d)",result); return result;
    }
    audio_done=0;
    result=c->task_start(audio_worker,NULL,4096,4,0);
    if(result!=ZM_OK) {
        audio_done=1; vd_running=0;
        c->log("doom: start failed: audio task, 4096-byte stack (%d)",result); return result;
    }
    c->log("doom: tasks ready (game stack 16384, audio stack 4096)");
    return ZM_OK;
}
static void stop(void) {
    __atomic_store_n(&vd_running,0,__ATOMIC_RELEASE);
    if(io_signal) vd_core->signal_give(io_signal);
    /* ft_open may take 15 seconds at the first multiboot. Let it unwind before
     * the core deletes app tasks and synchronization objects. */
    for(unsigned i=0;i<1700;i++) {
        /* и задача выгрузки кадра: снятая посреди записи, она оставила бы LINK занятым */
        if(__atomic_load_n(&game_done,__ATOMIC_ACQUIRE) && __atomic_load_n(&audio_done,__ATOMIC_ACQUIRE) &&
           vd_video_stopped() && vd_loader_stopped()) break;
        vd_core->delay(10);
    }
}
int main(int argc,char **argv) {
    static const zm_app_t app={sizeof(zm_app_t),1,3,"doom","1.0",0x70,0x76,start,stop,command,NULL};
    if(argc!=3) return 0;
    *(const zm_app_t**)argv[2]=&app; return 1;
}
