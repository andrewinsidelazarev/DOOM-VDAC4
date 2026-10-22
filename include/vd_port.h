/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <setjmp.h>
#include "zm_app.h"
extern const zm_core_t *vd_core;
extern volatile int vd_running, vd_state;
extern uint32_t vd_wad_size, vd_frames;
extern uint32_t vd_render_ms,vd_render_count,vd_qspi_ms,vd_swap_ms;
extern uint32_t vd_upload_wait_ms;
extern uint32_t vd_pace_ms,vd_late_frames;  /* ожидание темпа; кадры, державшиеся больше 2 кадров FT812 */
extern char vd_wad_name[13];
#define VD_ERROR_MAX 90
extern char vd_error[VD_ERROR_MAX+1];
extern jmp_buf vd_exit;
void vd_checkpoint(void);
size_t vd_wad_read(uint32_t offset, void *buffer, size_t length);
/* E5/74: stat, read, truncate/create, write, replace/rename, delete, sync. */
int vd_fs_call(unsigned op,const char *name,const char *other,uint32_t offset,void *data,unsigned length,uint32_t *size);
size_t vd_keys_take(uint8_t *out,size_t max,int *joy,int *stale);   /* байты PS/2 AVR Evo, джойстик */
int vd_mouse_take(int *dx,int *dy);          /* кнопки мыши Kempston; dx, dy — с прошлого вызова */
void vd_audio_task(void *arg);
void vd_audio_render(float *out, unsigned count);
void vd_audio_reset(void);
void vd_video_frame(const uint8_t *pixels, const uint32_t *palette);
void vd_video_init(void);
int vd_video_stopped(void);
void *vd_malloc(size_t n);
void *vd_calloc(size_t n, size_t size);
void *vd_realloc(void *p, size_t n);
void vd_free(void *p);
void vd_fail(const char *message);
