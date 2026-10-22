/* Native ESP32 PCM mixer and compact MUS FM synthesizer (no external ELF/bank).
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "compat.h"
#include <math.h>
#include "i_sound.h"
#include "w_wad.h"
#include "z_zone.h"
#include "m_config.h"
#include "doomdef.h"
#include "sounds.h"
extern gamestate_t gamestate;           /* doomstat.h — нет: его paused совпадает с паузой музыки */
#define RATE 22050
/* Сколько звука держать в очереди ядра VDAC4 вперёд. pcm_queued_ms считает и DMA I2S (8 × 480
 * кадров — ~174 мс при 22 050 Гц), порог — выше него, как у BOING 1.1 (проверено на плате). До
 * 0.1.5 порога не было: микшер забивал всё кольцо PCM ядра (256 КБ — ~3 с при 22 050 Гц стерео),
 * и звук отставал от картинки на секунды. */
#define AUDIO_AHEAD_MS 220
#define SFX 16
#define VOICES 24
int snd_samplerate=RATE,snd_cachesize=512*1024,snd_maxslicetime_ms=12;
int snd_musicdevice=SNDDEVICE_SB,snd_sfxdevice=SNDDEVICE_SB;
char *snd_musiccmd="";
typedef struct { const uint8_t *data; uint32_t length,step; uint64_t pos; float left,right; } effect_t;
typedef struct { unsigned channel,note,age; uint32_t phase,mod,step; float amp,env; int active,release; } voice_t;
static effect_t effects[SFX];
static voice_t voices[VOICES];
static float sine_table[1024],note_steps[128];
/* ELF data lives in PSRAM. Keep this single audio task's output buffer out
 * of the scarce internal-RAM task stack. */
static float pcm[256*2];
static uint8_t program[16],velocity[16],volume[16],pan[16],sustain[16];
static float bend[16];
static void *audio_lock;
static const uint8_t *song,*song_end,*score_start,*cursor;
static unsigned music_delay,music_clock,age,noise=0x1234567;
static int playing,looping,paused,music_volume=90;
static uint16_t le16(const uint8_t *p) { return p[0]|((uint16_t)p[1]<<8); }
static uint32_t le32(const uint8_t *p) { return le16(p)|((uint32_t)le16(p+2)<<16); }
int vd_audio_init(void) {
    audio_lock=vd_core->mutex_create(); if(!audio_lock) return ZM_NO_MEMORY;
    for(unsigned i=0;i<1024;i++) sine_table[i]=sinf(i*6.28318530718f/1024.0f);
    for(unsigned i=0;i<128;i++) note_steps[i]=440.0f*powf(2.0f,((int)i-69)/12.0f)*(4294967296.0f/RATE);
    return ZM_OK;
}
void vd_audio_reset(void) {
    if(!audio_lock) return;
    vd_core->mutex_lock(audio_lock); memset(effects,0,sizeof(effects)); memset(voices,0,sizeof(voices));
    playing=0; song=NULL; vd_core->mutex_unlock(audio_lock);
}
static void reset_channels(void) {
    memset(voices,0,sizeof(voices)); memset(program,0,sizeof(program)); memset(sustain,0,sizeof(sustain));
    for(unsigned i=0;i<16;i++) { velocity[i]=100; volume[i]=100; pan[i]=64; bend[i]=1.0f; }
}
static void note_on(unsigned ch,unsigned note,unsigned vel) {
    if(note>127) return;
    unsigned pick=0,oldest=UINT32_MAX;
    for(unsigned i=0;i<VOICES;i++) {
        if(!voices[i].active) {pick=i;break;}
        if(voices[i].age<oldest) {oldest=voices[i].age;pick=i;}
    }
    voices[pick]=(voice_t){.channel=ch,.note=note,.age=++age,.step=(uint32_t)(note_steps[note]*bend[ch]),.amp=vel/127.0f,.env=1.0f,.active=1};
}
static void notes_off(unsigned ch,int immediate) {
    for(unsigned i=0;i<VOICES;i++) if(voices[i].channel==ch) { if(immediate) voices[i].active=0; else voices[i].release=1; }
}
static int get_byte(void) { if(cursor>=song_end) {playing=0;return -1;} return *cursor++; }
static void music_tick(void) {
    if(music_delay) {music_delay--;return;}
    /* A bounded event budget also handles a malformed zero-time loop. */
    for(unsigned events=0;events<256 && playing;events++) {
        int ev=get_byte(); if(ev<0) return;
        unsigned type=(ev>>4)&7,ch=ev&15; int a,b;
        switch(type) {
        case 0:
            a=get_byte(); if(a<0) return;
            for(unsigned i=0;i<VOICES;i++) if(voices[i].active && voices[i].channel==ch && voices[i].note==(a&127)) voices[i].release=sustain[ch]?2:1;
            break;
        case 1:
            a=get_byte(); if(a<0) return;
            if(a&128) {b=get_byte();if(b<0)return;velocity[ch]=b&127;}
            note_on(ch,a&127,velocity[ch]); break;
        case 2:
            a=get_byte(); if(a<0) return;
            bend[ch]=powf(2.0f,((float)a-128.0f)/768.0f);
            for(unsigned i=0;i<VOICES;i++) if(voices[i].channel==ch) voices[i].step=(uint32_t)(note_steps[voices[i].note]*bend[ch]);
            break;
        case 3:
            a=get_byte(); if(a<0) return;
            if(a==10 || a==11) notes_off(ch,a==10);
            if(a==14) {volume[ch]=100;pan[ch]=64;sustain[ch]=0;bend[ch]=1.0f;}
            break;
        case 4:
            a=get_byte(); b=get_byte(); if(a<0 || b<0) return;
            if(a==0) program[ch]=b&127;
            if(a==3) volume[ch]=b&127;
            if(a==4) pan[ch]=b&127;
            if(a==8) {
                sustain[ch]=b>=64;
                if(!sustain[ch]) for(unsigned i=0;i<VOICES;i++) if(voices[i].channel==ch && voices[i].release==2) voices[i].release=1;
            }
            break;
        case 6:
            if(looping) {cursor=score_start;reset_channels();music_delay=1;return;}
            playing=0; memset(voices,0,sizeof(voices)); return;
        default: playing=0; return;
        }
        if(ev&128) {
            unsigned delay=0;
            for(unsigned i=0;i<4;i++) {
                a=get_byte();if(a<0)return;
                delay=(delay<<7)|(a&127);
                if(!(a&128)) {music_delay=delay?delay-1:0;return;}
            }
            playing=0; return;
        }
    }
    playing=0;
}
void vd_audio_render(float *out,unsigned count) {
    vd_core->mutex_lock(audio_lock);
    for(unsigned f=0;f<count;f++) {
        float left=0,right=0;
        for(unsigned i=0;i<SFX;i++) {
            effect_t *e=&effects[i]; if(!e->data) continue;
            unsigned pos=(unsigned)(e->pos>>16);
            if(pos>=e->length) {e->data=NULL;continue;}
            float v=((int)e->data[pos]-128)/128.0f;
            left+=v*e->left; right+=v*e->right; e->pos+=e->step;
        }
        if(playing && !paused) {
            music_clock+=140;
            if(music_clock>=RATE) { music_clock-=RATE; music_tick(); }
            for(unsigned i=0;i<VOICES;i++) {
                voice_t *v=&voices[i]; if(!v->active) continue;
                unsigned ch=v->channel; float wave;
                v->phase+=v->step;
                if(ch==15) {
                    noise^=noise<<13;noise^=noise>>17;noise^=noise<<5;
                    wave=((int)(noise&65535)-32768)/32768.0f;
                    if(v->note<38) wave=sine_table[v->phase>>22];
                    v->env*=v->note<44?0.9988f:0.996f;
                } else {
                    unsigned family=program[ch]/8;
                    v->mod+=v->step*(family==4?1:family==3?3:2);
                    float modulation=sine_table[v->mod>>22]*(family==4?140.0f:family==10?220.0f:65.0f)*v->env;
                    unsigned phase=(v->phase>>22)+(int)modulation;
                    wave=sine_table[phase&1023];
                    if(v->release==1) v->env*=0.9975f;
                    else if(family==0 || family==1 || family==3) v->env*=0.99994f;
                }
                if(v->env<0.002f) {v->active=0;continue;}
                float gain=v->amp*v->env*(volume[ch]/127.0f)*(music_volume/127.0f)*0.055f;
                left+=wave*gain*(127-pan[ch])/127.0f; right+=wave*gain*pan[ch]/127.0f;
            }
        }
        out[f*2]=left>1?1:left< -1?-1:left; out[f*2+1]=right>1?1:right< -1?-1:right;
    }
    vd_core->mutex_unlock(audio_lock);
}
void vd_audio_task(void *arg) {
    (void)arg; zm_pcm_format_t format={RATE,2};
    if(vd_core->pcm_open(&format)!=ZM_OK) {vd_core->log("doom: PCM5102 output failed");vd_state=255;vd_running=0;return;}
    while(vd_running) {
        if(vd_core->pcm_queued_ms && vd_core->pcm_queued_ms()>AUDIO_AHEAD_MS) {vd_core->delay(2);continue;}
        vd_audio_render(pcm,256);
        unsigned sent=0;
        while(sent<256 && vd_running) {
            int n=vd_core->pcm_write(pcm+sent*2,256-sent);
            if(n<0) {vd_core->log("doom: PCM write failed");vd_state=255;vd_running=0;break;}
            if(n==0) vd_core->delay(1); else sent+=(unsigned)n;
        }
    }
    vd_core->pcm_flush(); vd_core->pcm_close();
}
/* Звуки — заранее, на статичных экранах (заставка, межуровневый счёт, финал): по одному-два за
 * кадр, не дольше 20 мс подряд, пока не загрузятся все (DOOM1 — 55 звуков, 535 КБ, за титул до
 * первого демо). Иначе звук читается с SD Evo при первом проигрывании — посреди уровня кадр
 * стоит. В уровне подгрузки нет; не успели — как раньше, при первом звуке. */
static int preload_next;
void I_InitSound(boolean prefix) { (void)prefix; preload_next=1; }
void I_ShutdownSound(void) { vd_audio_reset(); }
int I_GetSfxLumpNum(sfxinfo_t *s) { char name[12]; snprintf(name,sizeof(name),"ds%s",s->link?s->link->name:s->name); return W_GetNumForName(name); }
void I_UpdateSound(void) {
    if(preload_next<=0 || preload_next>=NUMSFX || gamestate==GS_LEVEL) return;
    uint32_t start=vd_core->millis();
    while(preload_next<NUMSFX && (uint32_t)(vd_core->millis()-start)<20) {
        sfxinfo_t *s=&S_sfx[preload_next++];
        char name[12]; snprintf(name,sizeof(name),"ds%s",s->link?s->link->name:s->name);
        int lump=W_CheckNumForName(name);   /* звуков DOOM2 в DOOM1.WAD нет */
        if(lump<0 || W_LumpLength(lump)<40) continue;
        s->lumpnum=lump;
        W_CacheLumpNum(lump,PU_STATIC);     /* как I_StartSound: звук остаётся в памяти */
    }
}
void I_UpdateSoundParams(int ch,int vol,int sep) {
    if(ch<0 || ch>=SFX)return;
    if(vol<0)vol=0;if(vol>127)vol=127;if(sep<0)sep=0;if(sep>254)sep=254;
    vd_core->mutex_lock(audio_lock);
    effects[ch].left=(vol/127.0f)*(254-sep)/254.0f*0.3f;
    effects[ch].right=(vol/127.0f)*sep/254.0f*0.3f;
    vd_core->mutex_unlock(audio_lock);
}
int I_StartSound(sfxinfo_t *s,int ch,int vol,int sep) {
    if(ch<0 || ch>=SFX)return -1;
    int lump=s->lumpnum>=0?s->lumpnum:I_GetSfxLumpNum(s), length=W_LumpLength(lump);
    if(length<40)return -1;
    const uint8_t *p=W_CacheLumpNum(lump,PU_STATIC);
    unsigned rate=le16(p+2), samples=le32(p+4);
    if(le16(p)!=3 || rate<1000 || rate>96000 || samples<32 || samples>(unsigned)length-8) return -1;
    vd_core->mutex_lock(audio_lock);
    effects[ch]=(effect_t){.data=p+24,.length=samples-32,.step=(uint32_t)(((uint64_t)rate<<16)/RATE)};
    vd_core->mutex_unlock(audio_lock);
    I_UpdateSoundParams(ch,vol,sep); return ch;
}
void I_StopSound(int ch) { if(ch>=0 && ch<SFX) {vd_core->mutex_lock(audio_lock);effects[ch].data=NULL;vd_core->mutex_unlock(audio_lock);} }
boolean I_SoundIsPlaying(int ch) { if(ch<0 || ch>=SFX)return false; vd_core->mutex_lock(audio_lock);int yes=effects[ch].data!=NULL;vd_core->mutex_unlock(audio_lock);return yes; }
void I_PrecacheSounds(sfxinfo_t *s,int n) { (void)s;(void)n; }
void I_InitMusic(void) {}
void I_ShutdownMusic(void) { I_StopSong(); }
void I_SetMusicVolume(int vol) { vd_core->mutex_lock(audio_lock);music_volume=vol<0?0:vol>127?127:vol;vd_core->mutex_unlock(audio_lock); }
void I_PauseSong(void) { vd_core->mutex_lock(audio_lock);paused=1;vd_core->mutex_unlock(audio_lock); }
void I_ResumeSong(void) { vd_core->mutex_lock(audio_lock);paused=0;vd_core->mutex_unlock(audio_lock); }
void *I_RegisterSong(void *data,int len) {
    uint8_t *p=data;
    if(len<16 || memcmp(p,"MUS\x1a",4)) return NULL;
    unsigned start=le16(p+6),length=le16(p+4);
    if(start<16 || start>(unsigned)len || !length || length>(unsigned)len-start) return NULL;
    vd_core->mutex_lock(audio_lock);song=p;score_start=p+start;song_end=score_start+length;vd_core->mutex_unlock(audio_lock);return p;
}
void I_UnRegisterSong(void *handle) { (void)handle; I_StopSong(); }
void I_PlaySong(void *handle,boolean loop) {
    vd_core->mutex_lock(audio_lock);
    if(handle && handle==song) {reset_channels();cursor=score_start;looping=loop;paused=0;music_clock=RATE-140;music_delay=0;playing=1;}
    vd_core->mutex_unlock(audio_lock);
}
void I_StopSong(void) {vd_core->mutex_lock(audio_lock);playing=0;memset(voices,0,sizeof(voices));vd_core->mutex_unlock(audio_lock);}
boolean I_MusicIsPlaying(void) { vd_core->mutex_lock(audio_lock);int yes=playing;vd_core->mutex_unlock(audio_lock);return yes; }
void I_BindSoundVariables(void) {M_BindVariable("snd_musicdevice",&snd_musicdevice);M_BindVariable("snd_sfxdevice",&snd_sfxdevice);}
