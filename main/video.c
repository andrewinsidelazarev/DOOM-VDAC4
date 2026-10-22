/* Software-rendered 320x200 -> QSPI LINK -> FT812 rasterizer.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "compat.h"
#include "i_video.h"
#include "i_system.h"
#include "v_video.h"
#include "d_event.h"
#include "doomkeys.h"
#include "z_zone.h"
#include "doomgeneric.h"
#include "tables.h"
#include "vd_gpu.h"
#include "m_config.h"
#include "vd_loader.h"
byte *I_VideoBuffer;
boolean screenvisible=true, screensaver_mode=false;
int usegamma, vanilla_keyboard_mapping=0;
/* Мышь Kempston — как I_ReadMouse Chocolate Doom: ev_mouse раз в тик, ускорение
 * mouse_acceleration выше mouse_threshold, novert убирает ход мыши вперёд-назад. */
int usemouse=1, novert=0;
float mouse_acceleration=2.0f;
int mouse_threshold=10;
/* Множитель отсчётов мыши перед ускорением. AVR Evo кладёт в счётчики Kempston отсчёты PS/2
 * как есть, по умолчанию 1 отсчёт/мм (обе кнопки мыши + «+»/«−»/«*» цифрового блока ставят
 * 1, 2, 4 или 8): без множителя полный поворот — 4–8 м хода мыши. 16 при 1 отсчёте/мм —
 * полный поворот за 26–30 см хода при чувствительности 5 (стенд ПК: 60 отсчётов — 72°);
 * ползунок меню 0…9 даёт множитель 0,5…1,4. */
int vdac4_mouse_scale=16;
static int mouse_old;
/* AccelerateMouse Chocolate Doom */
static int accelerate_mouse(int v) {
    if(v<0) return -accelerate_mouse(-v);
    if(v>mouse_threshold) return (int)((v-mouse_threshold)*mouse_acceleration+mouse_threshold);
    return v;
}
static uint32_t palette[128];
static uint8_t rgb[768];
/* Буферы кадра в RAM_G. Программный кадр — тройной буфер: кадр пишется в буфер, которого нет
 * ни на экране, ни в ждущей смене листа, поэтому выгрузка не ждёт смену (раньше ждала до 13 мс
 * из 33,8 — демо дёргалось). Рендер BL616 — двойной: его области с #21000 (gpu.c). */
#define NBUF_MAX 3
static const uint32_t buf_addr[NBUF_MAX]={0,0x10000,0x30000};
#define PAL_ADDR(b) (0x20000u+0x200u*(b))
static unsigned back,nbuf=2;
/* Дельта: одна история — последний выгруженный программный кадр — и карты изменённых блоков
 * по 64 байта за последние nbuf кадров (слот — номер буфера). Буфер b держит кадр k − nbuf:
 * ему нужны блоки, изменённые в любом из nbuf последних кадров. */
static uint32_t *previous_pixels;
static int history_valid;
static uint8_t dirty[NBUF_MAX][125];
static uint32_t previous_palette[NBUF_MAX][128];
static uint8_t pixels_valid[NBUF_MAX],palette_valid[NBUF_MAX];
static uint32_t video_bytes,video_writes,video_frames;
#define RAM_DL 0x300000u
#define REG_FRAMES 0x302004u
#define REG_DLSWAP 0x302054u
/* BILINEAR при растяжении 320×200 → 960×720 (DOOM.CFG, vdac4_bilinear; 0 — NEAREST).
 * Растеризатору ядра FT812 1.26 — ~22–24 мс на кадр (2 прохода на точку), в темп 2 кадров
 * FT812 (33,8 мс) укладывается; у 1.25 — ~40 мс (4 прохода, темп падает до ~20 кадров/с),
 * до 1.25 BILINEAR рисуется как NEAREST. */
int vdac4_bilinear=1;
/* Запись в FT812 блоками по 1024 слова (4 КБ): на блок ядро VDAC4 тратит установку адреса
 * и проверку последнего слова чтением, крупный блок их окупает. Без longjmp — годится и для
 * задачи выгрузки; 0 — ошибка LINK или приложение останавливают. */
static int ft_put(uint32_t addr,const uint32_t *p,unsigned words) {
    while(words) {
        if(!__atomic_load_n(&vd_running,__ATOMIC_ACQUIRE)) return 0;
        size_t n=words>1024?1024:words;
        if(vd_core->ft_write(addr,p,n)!=ZM_OK) return 0;
        video_bytes+=n*4;video_writes++;
        addr+=n*4; p+=n; words-=n;
    }
    return 1;
}
void vd_ft_write(uint32_t addr,const uint32_t *p,unsigned words) {
    vd_checkpoint();
    if(!ft_put(addr,p,words)) { vd_checkpoint(); vd_fail("QSPI framebuffer write"); }
}

/* Выгрузка кадра — в своей задаче на ядре 0: пока кадр N идёт по LINK, игра на ядре 1
 * рисует кадр N+1 (раньше рисование и передача шли друг за другом). Игра копирует готовый
 * кадр и палитру в up_pixels/up_palette и отдаёт их задаче; следующий кадр ждёт, пока
 * задача освободится. Задаче выгрузки longjmp нельзя (vd_exit — задачи игры): ошибку она
 * кладёт в up_error, а vd_fail зовёт игра. Только программный режим (без BL616-рендера). */
static uint8_t *up_pixels;
static uint32_t up_palette[128];
static void *up_go,*up_idle;
static int up_async;
static volatile int up_busy;                 /* кадр отдан задаче, up_pixels ещё нужны ей */
static int up_released;                      /* задача отпустила up_pixels до смены листа */
static volatile int up_stopped=1;            /* задачи выгрузки нет (не запускалась или вышла) */
static const char *volatile up_error;
uint32_t vd_upload_wait_ms;                  /* игра ждала задачу выгрузки, мс */

static const char *swap_wait(void) {
    uint32_t swap=1, start=vd_core->millis();
    for(;;) {
        if(!__atomic_load_n(&vd_running,__ATOMIC_ACQUIRE)) return "stopped";
        if(vd_core->ft_read(REG_DLSWAP,&swap)!=ZM_OK) return "QSPI DLSWAP read";
        if(!swap) break;
        if((uint32_t)(vd_core->millis()-start)>2000) return "Tang frame swap timeout";
        vd_core->delay(1);
    }
    vd_swap_ms+=vd_core->millis()-start;
    return NULL;
}
/* Ровный темп: лист меняется раз в 2 кадра FT812 (59,08 / 2 = 29,54 кадра/с), а не на
 * первом же кадре, как успел, — иначе кадры держались то 1, то 2 кадра FT812. DLSWAP = 2
 * срабатывает в конце кадра FT812, в котором записан; номер этого кадра — swap_frame
 * (REG_FRAMES, прочитанный после записи: если граница кадра прошла между записью и чтением,
 * берётся поздний — кадр выйдет на 3 кадра FT812, но никогда не на 1). Следующую смену можно
 * писать с кадра swap_frame + 2; позже — кадр держится дольше (vd_late_frames). */
#define PACE 2
static uint32_t swap_frame;
static int pace_state;                       /* 0 — первый кадр, 1 — с привязкой, 2 — счёт стоит */
uint32_t vd_pace_ms,vd_late_frames;
static const char *pace_wait(void) {
    if(pace_state!=1) return NULL;
    uint32_t now=0, start=vd_core->millis();
    for(;;) {
        if(!__atomic_load_n(&vd_running,__ATOMIC_ACQUIRE)) return "stopped";
        if(vd_core->ft_read(REG_FRAMES,&now)!=ZM_OK) return "QSPI FRAMES read";
        int32_t d=(int32_t)(now-swap_frame);
        if(d>=PACE) { if(d>PACE) vd_late_frames++; break; }
        if((uint32_t)(vd_core->millis()-start)>100) {
            pace_state=2;                    /* развёртки FT812 нет: без привязки */
            vd_core->log("doom: FT812 frame counter stands; frames are not paced");
            break;
        }
        vd_core->delay(1);
    }
    vd_pace_ms+=vd_core->millis()-start;
    return NULL;
}
void vd_video_init(void) {
    int result=vd_core->ft_open(15000);
    if(result!=ZM_OK) {
        char message[64];
        snprintf(message,sizeof(message),"FT812 OPEN FAILED: CODE %d",result);
        vd_fail(message);
    }
    vd_checkpoint();vd_gpu_init();
    back=0; nbuf=vd_gpu_enabled?2:3; history_valid=0;
    memset(pixels_valid,0,sizeof(pixels_valid));
    memset(palette_valid,0,sizeof(palette_valid));
    previous_pixels=vd_gpu_enabled?NULL:vd_malloc(64000);
    if(!vd_gpu_enabled && !previous_pixels)
        vd_core->log("doom: video history allocation failed; full frame uploads");
    video_bytes=video_writes=video_frames=0;
    up_async=0; up_busy=0; up_error=NULL;
    pace_state=0; vd_pace_ms=vd_late_frames=0;
    if(!vd_gpu_enabled) {
        /* как previous_pixels — на каждый запуск: память и объекты приложения ядро снимает */
        up_pixels=vd_malloc(64000);
        up_go=vd_core->signal_create();
        up_idle=vd_core->signal_create();
        extern void vd_upload_task(void *arg);
        if(up_pixels && up_go && up_idle) {
            __atomic_store_n(&up_stopped,0,__ATOMIC_RELEASE);
            /* стек 6 КБ: на нём же идёт код ядра VDAC4 (ft_write, журнал при повторе записи) */
            if(vd_core->task_start(vd_upload_task,NULL,6144,3,0)==ZM_OK) up_async=1;
            else __atomic_store_n(&up_stopped,1,__ATOMIC_RELEASE);
        }
        vd_core->log("doom: frame upload %s",up_async?"in its own task (core 0)":"in the game task");
    }
}
int vd_video_stopped(void) { return __atomic_load_n(&up_stopped,__ATOMIC_ACQUIRE); }
/* Буфер назначения отстаёт от кадра на nbuf кадров: сравнивать не с прошлым кадром на экране,
 * а писать блоки, изменённые за эти nbuf кадров. Целые блоки по 64 байта — чтобы одиночные
 * точки не давали сотни мелких передач LINK. Первая запись в буфер — весь кадр. */
static int block_dirty(unsigned b) {
    uint8_t any=0;
    for(unsigned s=0;s<nbuf;s++) any|=dirty[s][b>>3];
    return any>>(b&7)&1;
}
static int upload_pixels(uint32_t address,const uint8_t *pixels) {
    const uint32_t *source=(const uint32_t*)pixels;
    if(!previous_pixels) return ft_put(address,source,16000);
    uint8_t *d=dirty[back];
    if(!history_valid) { memset(d,0xff,125); memcpy(previous_pixels,source,64000); history_valid=1; }
    else {
        memset(d,0,125);
        for(unsigned b=0;b<1000;b++)
            if(memcmp(source+b*16,previous_pixels+b*16,64)) {
                d[b>>3]|=1u<<(b&7); memcpy(previous_pixels+b*16,source+b*16,64);
            }
    }
    if(!pixels_valid[back]) {
        if(!ft_put(address,source,16000)) return 0;
        pixels_valid[back]=1;
        return 1;
    }
    unsigned at=0;
    while(at<1000) {
        if(!block_dirty(at)){at++;continue;}
        unsigned first=at;
        do at++; while(at<1000 && block_dirty(at));
        if(!ft_put(address+first*64,source+first*16,(at-first)*16)) { pixels_valid[back]=0; return 0; }
    }
    return 1;
}
void vd_video_report(void) {
    if(video_frames)vd_core->log("doom video: %u B/frame; %u writes/frame",
        (unsigned)(video_bytes/video_frames),(unsigned)(video_writes/video_frames));
    video_bytes=video_writes=video_frames=0;
}
uint32_t vd_video_back(void) {return buf_addr[back];}
void vd_video_wait(void) {
    vd_checkpoint();
    vd_loader_end();                         /* рендер BL616: первый кадр — экран загрузки уходит */
    const char *error=swap_wait();
    if(error) { vd_checkpoint(); vd_fail(error); }
}
/* Кадр в RAM_G (дельтой к буферу назначения), палитра, список и смена листа; NULL — готово.
 * release — задача выгрузки: кадр и палитра ушли, up_pixels игре можно отдать раньше смены. */
static void up_release(void) {
    up_released=1;
    __atomic_store_n(&up_busy,0,__ATOMIC_RELEASE);
    vd_core->signal_give(up_idle);
}
static const char *frame_upload(const uint8_t *pixels,const uint32_t *pal,int release) {
    const char *error;
    /* двойной буфер: буфер назначения на экране, пока смена листа не прошла */
    if(nbuf==2 && (error=swap_wait())!=NULL) return error;
    uint32_t upload_start=vd_core->millis(),swap;
    uint32_t addr=buf_addr[back], paladdr=PAL_ADDR(back);
    if(!vd_gpu_present(addr)) { if(!upload_pixels(addr,pixels)) return "QSPI framebuffer write"; }
    else { pixels_valid[back]=0; memset(dirty[back],0,125); }   /* история — прежняя */
    if(!palette_valid[back] || memcmp(previous_palette[back],pal,512)) {
        palette_valid[back]=0;
        if(!ft_put(paladdr,pal,128)) return "QSPI palette write";
        memcpy(previous_palette[back],pal,512);palette_valid[back]=1;
    }
    vd_qspi_ms+=vd_core->millis()-upload_start;
    if(release) up_release();
    /* тройной: смены ждёт только список — RAM_DL прошлой смены ещё может быть в деле */
    if(nbuf==3 && (error=swap_wait())!=NULL) return error;
    upload_start=vd_core->millis();
    /* 960x720 centered at x=32, y=24. 320x200 -> 4:3 with pixel-aspect
     * correction. A=1/3 and E=1/3.6 in FT81x 8.8 format. */
    const uint32_t dl[]={
        0x02000000,0x26000007,0x04ffffff,0x100000ff,
        0x05000000,0x01000000|addr,0x2a000000|paladdr,
        0x07000000|(14u<<19)|(320u<<9)|200u,
        0x28000000,
        0x08000000|(vdac4_bilinear?1u<<20:0)|((960u&511)<<9)|(720u&511),
        0x29000000|((960u>>9)<<2)|(720u>>9),
        0x15000055,0x16000000,0x17000000,
        0x18000000,0x19000047,0x1a000000,
        0x27000000,0x1f000001,0x40000000|(32u<<15)|24u,
        0x21000000,0
    };
    if(!ft_put(RAM_DL,dl,sizeof(dl)/4)) return "QSPI display list write";
    vd_qspi_ms+=vd_core->millis()-upload_start;
    if((error=pace_wait())!=NULL) return error;
    swap=2;
    if(!ft_put(REG_DLSWAP,&swap,1)) return "QSPI DLSWAP write";
    if(vd_core->ft_read(REG_FRAMES,&swap_frame)!=ZM_OK) return "QSPI FRAMES read";
    if(!pace_state) pace_state=1;            /* с первой смены — с привязкой */
    back=(back+1)%nbuf; vd_frames++;video_frames++;
    return NULL;
}
void vd_upload_task(void *arg) {
    (void)arg;
    while(__atomic_load_n(&vd_running,__ATOMIC_ACQUIRE)) {
        if(!__atomic_load_n(&up_busy,__ATOMIC_ACQUIRE)) { vd_core->signal_wait(up_go,50); continue; }
        up_released=0;
        const char *error=up_error?NULL:frame_upload(up_pixels,up_palette,1);
        if(error && !up_error) up_error=error;
        /* отпущен раньше — up_busy уже может быть следующим кадром игры */
        if(!up_released) up_release();
    }
    __atomic_store_n(&up_stopped,1,__ATOMIC_RELEASE);
}
void vd_video_frame(const uint8_t *pixels,const uint32_t *pal) {
    vd_loader_end();                         /* первый кадр игры: экран загрузки уходит (100 %) */
    if(!up_async) {
        vd_checkpoint();
        const char *error=frame_upload(pixels,pal,0);
        if(error) { vd_checkpoint(); vd_fail(error); }
        return;
    }
    uint32_t start=vd_core->millis();
    while(__atomic_load_n(&up_busy,__ATOMIC_ACQUIRE)) {
        vd_checkpoint();
        vd_core->signal_wait(up_idle,20);
        if((uint32_t)(vd_core->millis()-start)>5000) vd_fail("Frame upload task timeout");
    }
    if(up_error) { vd_checkpoint(); vd_fail(up_error); }
    vd_upload_wait_ms+=vd_core->millis()-start;
    memcpy(up_pixels,pixels,64000);
    memcpy(up_palette,pal,512);
    __atomic_store_n(&up_busy,1,__ATOMIC_RELEASE);
    vd_core->signal_give(up_go);
}
void I_InitGraphics(void) { I_VideoBuffer=Z_Malloc(320*200,PU_STATIC,NULL); memset(I_VideoBuffer,0,64000); screenvisible=true; }
void I_ShutdownGraphics(void) {}
void I_StartFrame(void) { vd_checkpoint(); }
/* Клавиатура — скан-коды PS/2 (набор 2) из очереди контроллера AVR Evo, как в R-Type и WC: загрузчик
 * Z80 читает регистр Gluk #F0 и шлёт байты как есть (пакет 75, app.c); матрица ZX не читается. E0 —
 * расширенная клавиша, F0 — отпускание, #FF — очередь переполнилась (отпускания могли пропасть): все
 * клавиши отпускаются. Клавиатура молчит дольше 500 мс — тоже. data1 — клавиша DOOM, data2 — знак
 * ASCII (имя сохранения, чит-коды: vanilla_keyboard_mapping 0). Как в оригинале: стрелки, Ctrl —
 * огонь, пробел — «использовать», Alt — стрейф, Shift — бег, «,» и «.» — шаг вбок, Esc — меню,
 * F1…F12, Tab — карта; и привычные с 0.1.0 W/A/S/D — ход и поворот, Q/E — шаг вбок, F — огонь,
 * M — карта, P — пауза (Pause очередь AVR не пишет). */
static const uint8_t ps2_keys[0x84][2]={
    [0x01]={KEY_F9},[0x03]={KEY_F5},[0x04]={KEY_F3},[0x05]={KEY_F1},[0x06]={KEY_F2},[0x07]={KEY_F12},
    [0x09]={KEY_F10},[0x0A]={KEY_F8},[0x0B]={KEY_F6},[0x0C]={KEY_F4},[0x0D]={KEY_TAB,9},[0x0E]={'`','`'},
    [0x11]={KEY_RALT},[0x12]={KEY_RSHIFT},[0x14]={KEY_FIRE},[0x15]={KEY_STRAFE_L,'q'},[0x16]={'1','1'},
    [0x1A]={'z','z'},[0x1B]={KEY_DOWNARROW,'s'},[0x1C]={KEY_LEFTARROW,'a'},[0x1D]={KEY_UPARROW,'w'},
    [0x1E]={'2','2'},[0x21]={'c','c'},[0x22]={'x','x'},[0x23]={KEY_RIGHTARROW,'d'},[0x24]={KEY_STRAFE_R,'e'},
    [0x25]={'4','4'},[0x26]={'3','3'},[0x29]={KEY_USE,' '},[0x2A]={'v','v'},[0x2B]={KEY_FIRE,'f'},
    [0x2C]={'t','t'},[0x2D]={'r','r'},[0x2E]={'5','5'},[0x31]={'n','n'},[0x32]={'b','b'},[0x33]={'h','h'},
    [0x34]={'g','g'},[0x35]={'y','y'},[0x36]={'6','6'},[0x3A]={KEY_TAB,'m'},[0x3B]={'j','j'},[0x3C]={'u','u'},
    [0x3D]={'7','7'},[0x3E]={'8','8'},[0x41]={KEY_STRAFE_L,','},[0x42]={'k','k'},[0x43]={'i','i'},
    [0x44]={'o','o'},[0x45]={'0','0'},[0x46]={'9','9'},[0x49]={KEY_STRAFE_R,'.'},[0x4A]={'/','/'},
    [0x4B]={'l','l'},[0x4C]={';',';'},[0x4D]={KEY_PAUSE,'p'},[0x4E]={KEY_MINUS,'-'},[0x52]={'\'','\''},
    [0x54]={'[','['},[0x55]={KEY_EQUALS,'='},[0x58]={KEY_CAPSLOCK},[0x59]={KEY_RSHIFT},[0x5A]={KEY_ENTER,13},
    [0x5B]={']',']'},[0x5D]={'\\','\\'},[0x66]={KEY_BACKSPACE},[0x69]={KEY_END},[0x6B]={KEY_LEFTARROW},
    [0x6C]={KEY_HOME},[0x72]={KEY_DOWNARROW},[0x73]={'5','5'},[0x74]={KEY_RIGHTARROW},[0x75]={KEY_UPARROW},
    [0x76]={KEY_ESCAPE,27},[0x77]={KEY_NUMLOCK},[0x78]={KEY_F11},[0x79]={KEY_EQUALS,'+'},[0x7A]={KEY_PGDN},
    [0x7B]={KEY_MINUS,'-'},[0x7C]={'*','*'},[0x7D]={KEY_PGUP},[0x7E]={KEY_SCRLCK},[0x83]={KEY_F7},
};
/* После E0. «E0 12», «E0 59» — ложные Shift клавиатуры при NumLock — мимо (0). */
static int ps2_extended(uint8_t code,int *ch) {
    *ch=0;
    switch(code) {
    case 0x11: return KEY_RALT;
    case 0x14: return KEY_FIRE;
    case 0x4A: *ch='/'; return '/';
    case 0x5A: *ch=13; return KEY_ENTER;
    case 0x69: return KEY_END;
    case 0x6B: return KEY_LEFTARROW;
    case 0x6C: return KEY_HOME;
    case 0x70: return KEY_INS;
    case 0x71: return KEY_DEL;
    case 0x72: return KEY_DOWNARROW;
    case 0x74: return KEY_RIGHTARROW;
    case 0x75: return KEY_UPARROW;
    case 0x7A: return KEY_PGDN;
    case 0x7C: return KEY_PRTSCR;
    case 0x7D: return KEY_PGUP;
    default: return 0;
    }
}
static int ps2_break,ps2_ext,joy_old;
static uint8_t ps2_held[2][0x84];             /* клавиша DOOM, нажатая этим кодом (0 — отпущен) */
static void post_key(int down,int code,int ch) {
    event_t ev={0};ev.type=down?ev_keydown:ev_keyup;ev.data1=code;ev.data2=ch;D_PostEvent(&ev);
}
static void release_keys(void) {
    for(unsigned e=0;e<2;e++) for(unsigned c=0;c<0x84;c++)
        if(ps2_held[e][c]) { post_key(0,ps2_held[e][c],0); ps2_held[e][c]=0; }
    ps2_break=ps2_ext=0;
}
static void ps2_byte(uint8_t code) {
    if(code==0xFF) { release_keys(); return; }
    if(code==0xE0) { ps2_ext=1; return; }    /* E0 приходит перед F0 */
    if(code==0xF0) { ps2_break=1; return; }
    int e=ps2_ext,up=ps2_break,ch=0,key=0;
    ps2_ext=ps2_break=0;                     /* код клавиши съедает префиксы */
    if(code>=0x84) return;
    if(e) key=ps2_extended(code,&ch); else { key=ps2_keys[code][0]; ch=ps2_keys[code][1]; }
    if(!key) return;
    if(up) {
        if(ps2_held[e][code]) { post_key(0,ps2_held[e][code],ch); ps2_held[e][code]=0; }
        return;
    }
    ps2_held[e][code]=(uint8_t)key;          /* автоповтор PS/2 — снова нажатие, как у DOS */
    post_key(1,key,ch);
}
void I_StartTic(void) {
    uint8_t bytes[64];
    int joy,stale;
    size_t n;
    do {
        n=vd_keys_take(bytes,sizeof(bytes),&joy,&stale);
        for(size_t i=0;i<n;i++) ps2_byte(bytes[i]);
    } while(n==sizeof(bytes));
    if(stale) release_keys();
    /* Kempston-джойстик: биты 0 вправо, 1 влево, 2 вниз, 3 вверх, 4 огонь */
    static const uint8_t joy_keys[5]={KEY_RIGHTARROW,KEY_LEFTARROW,KEY_DOWNARROW,KEY_UPARROW,KEY_FIRE};
    for(unsigned i=0;i<5;i++) if((joy^joy_old)&(1<<i)) post_key((joy>>i)&1,joy_keys[i],0);
    joy_old=joy;
    /* Одно ev_mouse за тик: G_Responder присваивает mousex/mousey, а не прибавляет. data1 —
     * кнопки (0 левая — огонь, 1 правая — стрейф, 2 средняя — вперёд), data2 — поворот,
     * data3 — вперёд (Y Kempston растёт вверх, знак уже как в DOOM). Выключенная мышь
     * копит приращения впустую и отпускает кнопки. */
    int dx,dy,buttons=vd_mouse_take(&dx,&dy);
    if(!usemouse) buttons=dx=dy=0;
    if(dx || dy || buttons!=mouse_old) {
        event_t ev={0};ev.type=ev_mouse;ev.data1=buttons;
        ev.data2=accelerate_mouse(dx*vdac4_mouse_scale);
        ev.data3=novert?0:accelerate_mouse(dy*vdac4_mouse_scale);
        D_PostEvent(&ev);mouse_old=buttons;
    }
}
void I_UpdateNoBlit(void) {}
void I_FinishUpdate(void) { vd_video_frame(I_VideoBuffer,palette); }
void I_ReadScreen(byte *dest) { vd_gpu_readscreen(dest); }
void I_SetPalette(byte *p) {
    for(unsigned i=0;i<256;i++) {
        unsigned r=gammatable[usegamma][p[i*3]],g=gammatable[usegamma][p[i*3+1]],b=gammatable[usegamma][p[i*3+2]];
        rgb[i*3]=r; rgb[i*3+1]=g; rgb[i*3+2]=b;
        unsigned color=((r>>3)<<11)|((g>>2)<<5)|(b>>3);
        if(i&1) palette[i/2]|=color<<16; else palette[i/2]=color;
    }
}
int I_GetPaletteIndex(int r,int g,int b) {
    int best=0,diff=INT32_MAX;
    for(int i=0;i<256;i++) { int dr=r-rgb[i*3],dg=g-rgb[i*3+1],db=b-rgb[i*3+2],d=dr*dr+dg*dg+db*db; if(d<diff) {diff=d;best=i;} }
    return best;
}
void I_BeginRead(void) {} void I_EndRead(void) {}
void I_SetWindowTitle(char *s) { DG_SetWindowTitle(s); }
void I_GraphicsCheckCommandLine(void) {}
void I_SetGrabMouseCallback(grabmouse_callback_t f) { (void)f; }
void I_EnableLoadingDisk(void) {}
void I_BindVideoVariables(void) {
    /* Гамма F11 входит в DEFAULT.CFG; без привязки она не читалась и не записывалась. */
    M_BindVariable("usegamma",&usegamma);
    M_BindVariable("vdac4_bilinear",&vdac4_bilinear);
    M_BindVariable("use_mouse",&usemouse);
    M_BindVariable("novert",&novert);
    M_BindVariable("mouse_acceleration",&mouse_acceleration);
    M_BindVariable("mouse_threshold",&mouse_threshold);
    M_BindVariable("vdac4_mouse_scale",&vdac4_mouse_scale);
}
void I_DisplayFPSDots(boolean b) { (void)b; }
void I_CheckIsScreensaver(void) {}
