/* Small, bounded file system for Doom's configuration and in-session saves.
 * The WAD backend is separate: random reads are serviced by Evo FAT32.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
#include <errno.h>
#include <ctype.h>
#include <sys/stat.h>
#include <unistd.h>
#ifdef _WIN32
#include <direct.h>
#endif
#include "vd_port.h"
#include "vd_gpu.h"
FILE *vd_fopen(const char *, const char *);
int vd_fclose(FILE *);
size_t vd_fread(void *, size_t, size_t, FILE *);
size_t vd_fwrite(const void *, size_t, size_t, FILE *);
int vd_fseek(FILE *, long, int);
long vd_ftell(FILE *);
int vd_fprintf(FILE *, const char *, ...);
int vd_vfprintf(FILE *, const char *, va_list);
int vd_fscanf(FILE *, const char *, ...);
int vd_sscanf(const char *, const char *, ...);
int vd_fputc(int, FILE *);
int vd_fflush(FILE *);
int vd_feof(FILE *);
double vd_atof(const char *);
int vd_remove(const char *);
int vd_rename(const char *, const char *);
int vd_mkdir(const char *, ...);
char *vd_getenv(const char *);
char *vd_strdup(const char *);
int vd_printf(const char *, ...);
void vd_abort(void) __attribute__((noreturn));
void vd_terminate(int) __attribute__((noreturn));
extern int vd_errno;
int vd_toupper(int);
int vd_tolower(int);
int vd_isprint(int);
#ifndef VD_COMPAT_IMPL
#undef errno
#define errno vd_errno
/* ctype — свои: у newlib они смотрят в таблицу _ctype_b, а её ядро VDAC4 приложению не даёт */
#undef toupper
#undef tolower
#undef isprint
#define toupper vd_toupper
#define tolower vd_tolower
#define isprint vd_isprint
#define malloc vd_malloc
#define calloc vd_calloc
#define realloc vd_realloc
#define free vd_free
#define strdup vd_strdup
#define fopen vd_fopen
#define fclose vd_fclose
#define fread vd_fread
#define fwrite vd_fwrite
#define fseek vd_fseek
#define ftell vd_ftell
#define fprintf vd_fprintf
#define vfprintf vd_vfprintf
#define fscanf vd_fscanf
#define sscanf vd_sscanf
#define fputc vd_fputc
#define fflush vd_fflush
#undef feof
#define feof vd_feof
#define atof vd_atof
#define remove vd_remove
#define rename vd_rename
#define mkdir vd_mkdir
#define getenv vd_getenv
#define printf vd_printf
#define exit vd_terminate
#define abort vd_abort
#undef stderr
#undef stdout
#define stderr ((FILE *)(uintptr_t)1)
#define stdout ((FILE *)(uintptr_t)1)
#endif
