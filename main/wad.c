/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "compat.h"
#include "w_file.h"
#include "i_system.h"
static wad_file_t wad;
extern wad_file_class_t stdc_wad_file;
static wad_file_t *open_wad(char *path) {
    if(strcasecmp(path,vd_wad_name)) return NULL;
    wad.file_class=&stdc_wad_file; wad.length=vd_wad_size; wad.mapped=NULL; return &wad;
}
static void close_wad(wad_file_t *w) { (void)w; }
static size_t read_wad(wad_file_t *w,unsigned offset,void *out,size_t len) { (void)w; return vd_wad_read(offset,out,len); }
wad_file_class_t stdc_wad_file={open_wad,close_wad,read_wad};
