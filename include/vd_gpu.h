/* SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include <stdint.h>
void vd_gpu_init(void);
void vd_gpu_report(void);
void vd_gpu_begin(void);
void vd_gpu_flat_reset(void);
int vd_gpu_column(int low,int translated,int fuzz);
int vd_gpu_span(int low);
void vd_gpu_patch(int x,int y,const void *patch,int flipped);
void vd_gpu_copy(unsigned offset,unsigned length);
int vd_gpu_present(uint32_t address);
void vd_gpu_readscreen(uint8_t *dest);
void vd_gpu_set_target(uint32_t address);
uint32_t vd_video_back(void);
void vd_video_wait(void);
void vd_video_report(void);
void vd_ft_write(uint32_t address,const uint32_t *words,unsigned count);
extern int vd_gpu_enabled;
#ifdef VD_GPU_VERIFY
void vd_gpu_verify(int low,int span);
#endif
