/* SPDX-License-Identifier: GPL-2.0-or-later
 * ESP32 command producer. BSP/visibility stays on ESP32; no world pixel loop
 * executes there when this backend is active. BL616 owns texture sampling,
 * COLORMAP, translation, fuzz and the world framebuffer. */
#include "compat.h"
#include "vd_gpu.h"
#include "r_local.h"
#include "i_video.h"
#include "i_swap.h"
#include "w_wad.h"
#include "ft_doom.h"
#define BASE 0x21000u
#define MAPS 0x31000u
#define COL_BASE 0x40000u
#define FLAT_BASE 0xc0000u
#define NCOL 2048u
#define NFLAT 48u
#define CMD_RAM 0x308000u
#define CMD_READ 0x3020f8u
#define CMD_WRITE 0x3020fcu
typedef struct { uint32_t hash,tag,used; const uint8_t *source; } entry;
static entry cols[NCOL],flats[NFLAT];
static uint8_t *shadow_cols,*shadow_flats;
static uint32_t queue[240],queued,wp,serial,target,shown,clock;
static const uint8_t *last_flat;
static uint32_t last_flat_address,last_flat_tag;
static uint32_t stats_frames,stats_upload,stats_commands,stats_wait;
static int pending,stale,maps_loaded;
int vd_gpu_enabled;
extern int fuzzpos;
static uint32_t read32(uint32_t a) {
    uint32_t v;if(vd_core->ft_read(a,&v)!=ZM_OK)vd_fail("BL616 QSPI read");return v;
}
static int wait_done(int probe) {
    uint32_t start=vd_core->millis();
    for(;;) {
        uint32_t rp=read32(CMD_READ);
        if(rp==wp){stats_wait+=vd_core->millis()-start;return 1;}
        if(rp==0xfff || vd_core->millis()-start>5000) {
            if(probe)return 0;
            vd_fail(rp==0xfff?"BL616 rejected Doom command":"BL616 command timeout");
        }
        vd_checkpoint();vd_core->delay(1);
    }
}
static void flush(void) {
    if(!queued)return;
    /* Keep the consumer running while the next batch is produced. Reuse
     * only acknowledged ring space, always leaving one word empty. */
    uint32_t start=vd_core->millis();
    for(;;) {
        uint32_t rp=read32(CMD_READ);
        if(rp==0xfff)vd_fail("BL616 rejected Doom command");
        if(((rp-wp-4)&4095)>=queued*4)break;
        if(vd_core->millis()-start>5000)vd_fail("BL616 command timeout");
        vd_checkpoint();vd_core->delay(1);
    }
    stats_wait+=vd_core->millis()-start;stats_commands+=queued*4;
    unsigned first=(4096-wp)/4;if(first>queued)first=queued;
    vd_ft_write(CMD_RAM+wp,queue,first);
    if(first<queued)vd_ft_write(CMD_RAM,queue+first,queued-first);
    wp=(wp+queued*4)&4095;vd_ft_write(CMD_WRITE,&wp,1);queued=0;
}
static void sync_gpu(void) {flush();wait_done(0);}
static void emit(uint32_t op,const uint32_t *p,unsigned n) {
    if(queued+n+1>240)flush();
    queue[queued++]=op;
    for(unsigned i=0;i<n;i++)queue[queued++]=p[i];
}
void vd_gpu_init(void) {
    wp=read32(CMD_WRITE)&4095;
    if(read32(CMD_READ)!=wp)vd_fail("BL616 command FIFO busy at startup");
    uint32_t at=wp,probe[]={FTDOOM_INFO,0,0};
    for(unsigned i=0;i<3;i++){vd_ft_write(CMD_RAM+wp,probe+i,1);wp=(wp+4)&4095;}
    vd_ft_write(CMD_WRITE,&wp,1);
    if(!wait_done(1) || read32(CMD_RAM+((at+4)&4095))!=FTDOOM_MAGIC) {
        uint32_t one=1,zero=0;
        vd_ft_write(0x302020,&one,1);vd_core->delay(20);
        vd_ft_write(CMD_READ,&zero,1);vd_ft_write(CMD_WRITE,&zero,1);
        vd_ft_write(0x302020,&zero,1);wp=0;
        vd_core->log("BL616 Doom commands unavailable; software renderer on ESP");return;
    }
    shadow_cols=vd_malloc(NCOL*256);shadow_flats=vd_malloc(NFLAT*4096);
    if(!shadow_cols || !shadow_flats)vd_fail("Doom texture cache allocation");
    vd_gpu_enabled=1;vd_core->log("BL616 Doom textured renderer ABI 1 enabled");
}
static uint32_t asset(const uint8_t *src,int flat,uint32_t *tag) {
    unsigned bytes=flat?4096:256,n=flat?NFLAT:NCOL;
    entry *entries=flat?flats:cols;
    uint8_t *shadow=flat?shadow_flats:shadow_cols;
    if(flat && src==last_flat){*tag=last_flat_tag;return last_flat_address;}
    uint32_t hash=2166136261u;
    for(unsigned i=0;i<bytes;i++)hash=(hash^src[i])*16777619u;
    unsigned slot=hash%n;
    entry *e=entries+slot;uint32_t address=(flat?FLAT_BASE:COL_BASE)+slot*bytes;
    if(!e->tag || e->hash!=hash || memcmp(shadow+slot*bytes,src,bytes)) {
        /* Completion before replacing a RAM_G slot avoids use-after-reuse by
         * older queued commands. Generation invalidates BL616's local cache. */
        if(e->tag)sync_gpu();
        memcpy(shadow+slot*bytes,src,bytes);
        vd_ft_write(address,(const uint32_t *)(shadow+slot*bytes),bytes/4);
        stats_upload+=bytes;
        if(!++serial)vd_fail("Texture generation exhausted");
        *e=(entry){.hash=hash,.tag=serial};
    }
    if(flat){last_flat=src;last_flat_address=address;last_flat_tag=e->tag;}
    *tag=e->tag;return address;
}
static uint32_t column_asset(const uint8_t *src,unsigned lo,unsigned hi,uint32_t *tag) {
    /* A sprite post can be shorter than 128/256 bytes. Compare and copy only
     * the sampled interval, retaining the rest of this column in the cache.
     * Camera clipping must not turn the same source into a new texture.
     * Comparing live bytes also handles zone allocations reusing an address. */
    uint32_t hash=(uint32_t)(uintptr_t)src;
    hash^=hash>>16;hash*=0x7feb352du;hash^=hash>>15;
    unsigned base=(hash%(NCOL/4))*4,slot=base;
    for(unsigned i=base;i<base+4;i++) {
        if(cols[i].tag && cols[i].source==src){slot=i;break;}
        if(!cols[i].tag || cols[i].used<cols[slot].used)slot=i;
    }
    entry *e=cols+slot;
    uint8_t *data=shadow_cols+slot*256;
    uint32_t address=COL_BASE+slot*256;
    int same=e->tag && e->source==src;
    if(!same || memcmp(data+lo,src+lo,hi-lo+1)) {
        if(e->tag)sync_gpu();
        if(!same)memset(data,0,256);
        memcpy(data+lo,src+lo,hi-lo+1);
        vd_ft_write(address,(const uint32_t *)data,64);stats_upload+=256;
        if(!++serial)vd_fail("Texture generation exhausted");
        e->source=src;e->tag=serial;
    }
    e->used=++clock;*tag=e->tag;return address;
}
void vd_gpu_set_target(uint32_t address) {target=address;}
void vd_gpu_flat_reset(void) {last_flat=NULL;}
void vd_gpu_begin(void) {
    if(!vd_gpu_enabled)return;
    last_flat=NULL;stats_frames++;
    vd_video_wait();target=vd_video_back();
    if(!maps_loaded) {
        unsigned len=W_LumpLength(W_GetNumForName("COLORMAP"));
        if(len<34*256)vd_fail("Doom COLORMAP needs 34 tables");
        vd_ft_write(MAPS,(const uint32_t *)colormaps,34*64);
        vd_ft_write(MAPS+34*256,(const uint32_t *)translationtables,3*64);
        uint32_t p[]={MAPS,37};emit(FTDOOM_MAPS,p,2);maps_loaded=1;
    }
    /* Only the border/status bar travels ESP -> BL. The world stays entirely
     * in the BL616 framebuffer. View edges are word-aligned in vanilla Doom. */
    if((viewwindowx|scaledviewwidth)&3)vd_fail("Unaligned Doom viewport");
    for(unsigned y=0;y<200;y++) {
        unsigned at=y*320;
        if(y<(unsigned)viewwindowy || y>=(unsigned)(viewwindowy+viewheight))
            vd_ft_write(BASE+at,(const uint32_t *)(I_VideoBuffer+at),80);
        else {
            if(viewwindowx)vd_ft_write(BASE+at,(const uint32_t *)(I_VideoBuffer+at),viewwindowx/4);
            unsigned end=viewwindowx+scaledviewwidth;
            if(end<320)vd_ft_write(BASE+at+end,(const uint32_t *)(I_VideoBuffer+at+end),(320-end)/4);
        }
    }
    uint32_t p[]={BASE,target,(uint32_t)viewwindowx|((uint32_t)viewwindowy<<16),
        (uint32_t)scaledviewwidth|((uint32_t)viewheight<<16)};
    emit(FTDOOM_BEGIN,p,4);pending=1;
}
static unsigned cmap(const uint8_t *p) {
    intptr_t off=p-colormaps;
    if(off<0 || off>=34*256 || (off&255))vd_fail("Unknown Doom light table");
    return (unsigned)off/256;
}
int vd_gpu_column(int low,int translated,int fuzz) {
    if(!vd_gpu_enabled)return 0;
    if(!pending)vd_fail("Doom column outside frame");
    if(fuzz) {if(!dc_yl)dc_yl=1;if(dc_yh==viewheight-1)dc_yh=viewheight-2;}
    if(dc_yh<dc_yl)return 1;
    unsigned n=dc_yh-dc_yl+1,width=low?2:1;
    uint32_t at=(dc_yl+viewwindowy)*320+viewwindowx+dc_x*width;
    uint32_t p[]={at,n|(width<<16)|(fuzz?1u<<24:0),0,0,0,dc_iscale,0};
    if(fuzz) {p[4]=fuzzpos;p[6]=6;
#ifndef VD_GPU_VERIFY
        fuzzpos=(fuzzpos+n)%50;
#endif
    }
    else {
        uint32_t frac=(uint32_t)dc_texturemid+(uint32_t)(dc_yl-centery)*(uint32_t)dc_iscale;
        uint32_t end=frac+(n-1)*(uint32_t)dc_iscale;
        unsigned mask=translated?255:127,lo=(frac>>16)&mask,hi=(end>>16)&mask;
        /* A masked sprite post may contain fewer than 128 bytes. Copy only
         * indices that this column will actually sample, never past its post. */
        uint64_t period=(uint64_t)(mask+1)<<16;
        if((uint64_t)(frac&(uint32_t)(period-1))+(uint64_t)(n-1)*(uint32_t)dc_iscale>=period) {lo=0;hi=mask;}
        p[2]=column_asset(dc_source,lo,hi,p+3);p[4]=frac;
        unsigned tr=255;
        if(translated) {
            intptr_t off=dc_translation-translationtables;
            if(off<0 || off>=768 || (off&255))vd_fail("Unknown sprite translation");
            tr=34+off/256;
        }
        p[6]=cmap(dc_colormap)|(tr<<8)|(mask<<16);
    }
    emit(FTDOOM_COLUMN,p,7);return 1;
}
int vd_gpu_span(int low) {
    if(!vd_gpu_enabled)return 0;
    if(!pending)vd_fail("Doom span outside frame");
    if(ds_x2<ds_x1)return 1;
    unsigned width=low?2:1;
    uint32_t p[]={ (uint32_t)((ds_y+viewwindowy)*320+viewwindowx+ds_x1*width),
        (uint32_t)(ds_x2-ds_x1+1)|(width<<16),0,0,
        (((uint32_t)ds_xfrac<<10)&0xffff0000)|((ds_yfrac>>6)&65535),
        (((uint32_t)ds_xstep<<10)&0xffff0000)|((ds_ystep>>6)&65535),cmap(ds_colormap)};
    p[2]=asset(ds_source,1,p+3);emit(FTDOOM_SPAN,p,7);
#ifndef VD_GPU_VERIFY
    if(low){ds_x1<<=1;ds_x2<<=1;}
#endif
    return 1;
}
void vd_gpu_patch(int x,int y,const void *vp,int flipped) {
    if(!pending)return;
    const patch_t *patch=vp;unsigned w=SHORT(patch->width);
    for(unsigned col=0;col<w;col++) {
        const uint8_t *post=(const uint8_t *)patch+LONG(patch->columnofs[flipped?w-1-col:col]);
        for(;post[0]!=255;post+=post[1]+4) {
            if(!post[1])continue;
            uint32_t p[]={ (uint32_t)((y+post[0])*320+x+col),post[1]|(1u<<16),0,0,0,65536,0xffffff};
            p[2]=column_asset(post+3,0,post[1]-1,p+3);emit(FTDOOM_COLUMN,p,7);
        }
    }
}
void vd_gpu_copy(unsigned offset,unsigned length) {
    if(!pending || !length)return;
    if(offset>64000 || length>64000-offset)vd_fail("Overlay outside screen");
    unsigned first=offset&~3u,last=(offset+length+3)&~3u;
    sync_gpu();vd_ft_write(BASE+first,(const uint32_t *)(I_VideoBuffer+first),(last-first)/4);
    uint32_t p[]={BASE+offset,offset,length};emit(FTDOOM_COPY,p,3);
}
int vd_gpu_present(uint32_t address) {
    if(!pending){stale=0;return 0;}
    uint32_t p[]={address};emit(FTDOOM_END,p,1);sync_gpu();
    pending=0;shown=address;stale=1;return 1;
}
void vd_gpu_readscreen(uint8_t *dest) {
    if(pending)vd_gpu_present(target);
    if(stale) {
        for(unsigned i=0;i<16000;i++) ((uint32_t *)I_VideoBuffer)[i]=read32(shown+i*4);
        stale=0;
    }
    memcpy(dest,I_VideoBuffer,64000);
}
void vd_gpu_report(void) {
    if(stats_frames)vd_core->log("doom GPU: textures %u B/view; commands %u B/view; FIFO wait %u ms/view",
        (unsigned)(stats_upload/stats_frames),(unsigned)(stats_commands/stats_frames),(unsigned)(stats_wait/stats_frames));
    stats_frames=stats_upload=stats_commands=stats_wait=0;
}
#ifdef VD_GPU_VERIFY
void vd_gpu_verify(int low,int span) {
    extern void vd_gpu_host_check(unsigned,unsigned,unsigned,unsigned);
    if(!vd_gpu_enabled)return;
    sync_gpu();
    if(span)vd_gpu_host_check((ds_y+viewwindowy)*320+viewwindowx+ds_x1,
        ((ds_x2-ds_x1)>>(low?1:0))+1,low?2:1,low?2:1);
    else if(dc_yh>=dc_yl)vd_gpu_host_check((dc_yl+viewwindowy)*320+viewwindowx+(dc_x<<(low?1:0)),
        dc_yh-dc_yl+1,320,low?2:1);
}
#endif
