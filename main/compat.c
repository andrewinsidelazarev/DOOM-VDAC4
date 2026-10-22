/* SPDX-License-Identifier: GPL-2.0-or-later */
#define VD_COMPAT_IMPL
#include "compat.h"
#include <limits.h>
#include "vd_loader.h"
typedef struct { char name[96]; uint8_t *data; size_t size, capacity; } node_t;
/* err — запись в память не удалась (нет места), такой файл на SD не идёт */
typedef struct { node_t *node; size_t pos; int write, wad, err; } file_t;
static node_t nodes[12];
static file_t files[8];
int vd_errno;
int vd_toupper(int c) { return c>='a' && c<='z'?c-32:c; }
int vd_tolower(int c) { return c>='A' && c<='Z'?c+32:c; }
int vd_isprint(int c) { return c>=32 && c<127; }
void *vd_malloc(size_t n) { return vd_core->alloc(n ? n : 1, ZM_MEM_PSRAM); }
void *vd_calloc(size_t n, size_t s) {
    if (s && n > SIZE_MAX / s) return NULL;
    void *p = vd_malloc(n*s); if (p) memset(p, 0, n*s); return p;
}
void *vd_realloc(void *p, size_t n) { return vd_core->realloc(p, n); }
void vd_free(void *p) { if (p) vd_core->free(p); }
char *vd_strdup(const char *s) { size_t n=strlen(s)+1; char *p=vd_malloc(n); if(p) memcpy(p,s,n); return p; }
char *vd_getenv(const char *name) { (void)name; return NULL; }
int vd_mkdir(const char *name, ...) { (void)name; return 0; }
/* The engine may prepend .savegame/ or a temporary directory. Physical files
 * always use a flat 8.3 name in the installation directory selected by Z80. */
static int flat_name(const char *path,char out[13]) {
    const char *name=path;
    for(const char *p=path;*p;p++) if(*p=='/' || *p=='\\') name=p+1;
    size_t n=strlen(name);
    if(n>12 && n>=4 && !strcasecmp(name+n-4,".cfg")) name="DOOM.CFG",n=8;
    if(!n || n>12) return -1;
    unsigned dots=0;
    for(size_t i=0;i<n;i++) {
        int c=vd_toupper((unsigned char)name[i]);
        if(c=='.') { if(++dots!=1 || !i || i>8 || n-i>4) return -1; }
        else if(!((c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_')) return -1;
        out[i]=c;
    }
    out[n]=0;
    if(n<4 || (strcmp(out+n-4,".DSG") && strcmp(out+n-4,".CFG"))) return -1;
    /* Separate slots when two IWADs share the installation directory. */
    if(n==12 && !strncmp(out,"DOOMSAV",7)) {
        char slot=out[7];
        snprintf(out,13,"%sSAVE%c.DSG",!strcmp(vd_wad_name,"DOOM2.WAD")?"D2":!strcmp(vd_wad_name,"DOOM.WAD")?"DU":"D1",slot);
    }
    return 0;
}
FILE *vd_fopen(const char *name, const char *mode) {
    file_t *f=NULL; node_t *node=NULL; int writing=mode[0]=='w';
    for(unsigned i=0;i<8;i++) if(!files[i].node && !files[i].wad) { f=&files[i]; break; }
    if(!f) return NULL;
    if(!strcasecmp(name,vd_wad_name) && !writing) { *f=(file_t){.wad=1}; return (FILE*)f; }
    char flat[13];
    if(flat_name(name,flat)) { vd_errno=ENOENT; return NULL; }
    uint32_t size=0;
    if(!writing && vd_fs_call(0,flat,NULL,0,NULL,0,&size)) { vd_errno=ENOENT; return NULL; }
    if(size>256*1024) return NULL;
    for(unsigned i=0;i<12;i++) if(!nodes[i].name[0]) {
        node=&nodes[i]; strcpy(node->name,flat); break;
    }
    if(!node) return NULL;
    if(!writing && size) {
        node->data=vd_malloc(size);
        if(!node->data) { memset(node,0,sizeof(*node)); return NULL; }
        node->capacity=node->size=size;
        for(uint32_t at=0;at<size;) {
            unsigned n=size-at; if(n>960)n=960;
            if(vd_fs_call(1,flat,NULL,at,node->data+at,n,NULL)) {
                vd_free(node->data); memset(node,0,sizeof(*node)); vd_errno=EIO; return NULL;
            }
            at+=n;
        }
    }
    *f=(file_t){.node=node,.write=writing}; return (FILE*)f;
}
int vd_fclose(FILE *stream) {
    if((uintptr_t)stream<=1) return 0;
    file_t *f=(file_t*)stream; node_t *node=f->node; int result=0;
    if(node && f->write) {
        result=f->err?-1:vd_fs_call(2,node->name,NULL,0,NULL,0,NULL);
        for(uint32_t at=0;!result && at<node->size;) {
            unsigned n=node->size-at; if(n>960)n=960;
            result=vd_fs_call(3,node->name,NULL,at,node->data+at,n,NULL); at+=n;
        }
        if(!result) result=vd_fs_call(6,node->name,NULL,0,NULL,0,NULL);
        if(result) vd_core->log("doom: %s not written (status %d)",node->name,result);
    }
    if(node) { vd_free(node->data); memset(node,0,sizeof(*node)); }
    memset(f,0,sizeof(*f)); return result?EOF:0;
}
size_t vd_fread(void *out,size_t s,size_t n,FILE *stream) {
    file_t *f=(file_t*)stream; if(!s || !n || n>SIZE_MAX/s || !f) return 0;
    size_t size=f->wad?vd_wad_size:f->node->size;
    if(f->pos>=size) return 0;
    size_t bytes=s*n; if(bytes>size-f->pos) bytes=size-f->pos;
    bytes-=bytes%s;
    if(f->wad) bytes=vd_wad_read((uint32_t)f->pos,out,bytes);
    else memcpy(out,f->node->data+f->pos,bytes);
    f->pos+=bytes; return bytes/s;
}
size_t vd_fwrite(const void *in,size_t s,size_t n,FILE *stream) {
    file_t *f=(file_t*)stream; if(!s || !n || n>SIZE_MAX/s || !f || !f->write) return 0;
    size_t bytes=s*n; node_t *node=f->node;
    if(bytes>256*1024 || f->pos>256*1024-bytes) { f->err=1; return 0; }
    size_t end=f->pos+bytes;
    if(end>node->capacity) {
        size_t capacity=(end+4095)&~(size_t)4095;
        void *p=vd_realloc(node->data,capacity); if(!p) { f->err=1; return 0; }
        node->data=p; node->capacity=capacity;
    }
    if(f->pos>node->size) memset(node->data+node->size,0,f->pos-node->size);
    memcpy(node->data+f->pos,in,bytes); f->pos=end;
    if(end>node->size) node->size=end;
    return n;
}
int vd_fseek(FILE *stream,long offset,int origin) {
    file_t *f=(file_t*)stream;
    if(!f || (origin!=SEEK_SET && origin!=SEEK_CUR && origin!=SEEK_END)) return -1;
    int64_t base=origin==SEEK_SET?0:origin==SEEK_CUR?(int64_t)f->pos:(int64_t)(f->wad?vd_wad_size:f->node->size);
    int64_t at=base+offset; if(at<0 || at>INT32_MAX) return -1; f->pos=(size_t)at; return 0;
}
long vd_ftell(FILE *f) { return (long)((file_t*)f)->pos; }
int vd_vfprintf(FILE *f,const char *fmt,va_list ap) {
    char buf[512]; int n=vsnprintf(buf,sizeof(buf),fmt,ap); if(n<0) return n;
    size_t len=(size_t)n<sizeof(buf)?(size_t)n:sizeof(buf)-1;
    if((uintptr_t)f<=1) { vd_loader_text(buf); vd_core->log("%s",buf); }   /* этап — на экран загрузки */
    else if(vd_fwrite(buf,1,len,f)!=len) return -1;
    return (int)len;
}
int vd_fprintf(FILE *f,const char *fmt,...) { va_list ap; va_start(ap,fmt); int n=vd_vfprintf(f,fmt,ap); va_end(ap); return n; }
int vd_printf(const char *fmt,...) { va_list ap; va_start(ap,fmt); int n=vd_vfprintf(NULL,fmt,ap); va_end(ap); return n; }
int vd_fputc(int c,FILE *f) { uint8_t b=c; return vd_fwrite(&b,1,1,f)==1?c:EOF; }
int vd_fflush(FILE *f) { (void)f; return 0; }
/* Конец — когда позиция дошла до размера (LoadDefaultCollection: while(!feof) fscanf) */
int vd_feof(FILE *stream) {
    if((uintptr_t)stream<=1) return 0;
    file_t *f=(file_t*)stream;
    return f->pos>=(f->wad?vd_wad_size:f->node->size);
}
/* atof прошивка VDAC4 не отдаёт ELF, strtod — отдаёт */
double vd_atof(const char *s) { return strtod(s,NULL); }
/* Only the two-column config grammar used by m_config.c is needed. */
int vd_fscanf(FILE *stream,const char *fmt,...) {
    if(strcmp(fmt,"%79s %99[^\n]\n")) return EOF;
    file_t *f=(file_t*)stream;
    if(!f || !f->node) return EOF;
    va_list ap;va_start(ap,fmt);char *key=va_arg(ap,char*),*value=va_arg(ap,char*);va_end(ap);
    const uint8_t *p=f->node->data; size_t n=f->node->size;unsigned k=0,v=0;
    while(f->pos<n && p[f->pos]<=32) f->pos++;
    if(f->pos==n)return EOF;
    while(f->pos<n && p[f->pos]>32) { if(k<79)key[k++]=p[f->pos];f->pos++; } key[k]=0;
    while(f->pos<n && (p[f->pos]==' '||p[f->pos]=='\t'))f->pos++;
    while(f->pos<n && p[f->pos]!='\n') { if(v<99)value[v++]=p[f->pos];f->pos++; }
    while(v && value[v-1]=='\r')v--;value[v]=0;
    return v?2:1;
}
int vd_sscanf(const char *s,const char *fmt,...) {
    while(*fmt==' ')fmt++;
    while(*s==' '||*s=='\t')s++;
    while(*fmt && *fmt!='%') { if(*fmt++!=*s++)return 0; }
    const char *p=fmt;if(*p!='%')return 0;
    int base=p[1]=='x'?16:p[1]=='o'?8:p[1]=='i'?0:10;
    char *end; long v=strtol(s,&end,base); if(end==s) return 0;
    va_list ap; va_start(ap,fmt); *va_arg(ap,int*)=(int)v; va_end(ap); return 1;
}
int vd_remove(const char *name) {
    char flat[13];if(flat_name(name,flat))return -1;
    return vd_fs_call(5,flat,NULL,0,NULL,0,NULL)?-1:0;
}
int vd_rename(const char *from,const char *to) {
    char a[13],b[13];if(flat_name(from,a)||flat_name(to,b))return -1;
    int result=vd_fs_call(4,a,b,0,NULL,0,NULL);
    if(result) vd_core->log("doom: %s -> %s not replaced (status %d)",a,b,result);
    return result?-1:0;
}
void vd_terminate(int code) { vd_state=code?255:3; longjmp(vd_exit,1); }
void vd_abort(void) { vd_fail("Doom abort"); for(;;) {} }
