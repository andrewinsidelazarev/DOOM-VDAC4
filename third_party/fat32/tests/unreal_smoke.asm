; Проверка самостоятельного драйвера в Unreal без загрузки Commander.
        DEVICE ZXSPECTRUM128
        INCLUDE "fat32.inc"
        INCLUDE "filex.inc"
        ORG #8000
START:
        DI
        LD SP,#BFFF
        LD BC,#21AF,A,#0E
        OUT (C),A
        LD BC,#10AF,A,#0F
        OUT (C),A
        INC B
        LD A,#0E
        OUT (C),A
        LD BC,#13AF,A,#20
        OUT (C),A
        LD HL,#3900
        XOR A
        CALL FAT32_BIND
        CALL FAT32_DEVICE_INIT
        JP NZ,FAILED
        CALL FAT32_MOUNT
        JP NZ,FAILED
        LD HL,NAME
        CALL FAT32_CREATE
        JP NZ,FAILED
        LD HL,#C000,BC,#4000
.fill:
        LD (HL),L
        INC HL
        DEC BC
        LD A,B:OR C
        JR NZ,.fill
        LD HL,#C000,BC,#4000
        CALL FAT32_APPEND
        JP NZ,FAILED
        LD HL,PARAM
        CALL FAT32_FILEX
        JP NZ,FAILED
        LD A,(PARAM+FILEX_P_RESULT_COUNT)
        CP #FF
        JP NZ,FAILED
        LD A,FILEX_OP_SET_EOF32
        LD (PARAM+FILEX_P_OPERATION),A
        LD HL,17000
        LD (PARAM+FILEX_P_OFFSET),HL
        LD HL,PARAM
        CALL FAT32_FILEX
        JP NZ,FAILED
        LD A,FILEX_OP_WRITE_AT
        LD (PARAM+FILEX_P_OPERATION),A
        LD HL,511
        LD (PARAM+FILEX_P_OFFSET),HL
        LD HL,REPLACEMENT
        LD (PARAM+FILEX_P_BUFFER),HL
        LD HL,6
        LD (PARAM+FILEX_P_LENGTH),HL
        LD HL,PARAM
        CALL FAT32_FILEX
        JP NZ,FAILED
        LD A,FILEX_OP_READ_AT
        LD (PARAM+FILEX_P_OPERATION),A
        LD HL,509
        LD (PARAM+FILEX_P_OFFSET),HL
        LD HL,#9000
        LD (PARAM+FILEX_P_BUFFER),HL
        LD HL,10
        LD (PARAM+FILEX_P_LENGTH),HL
        LD HL,PARAM
        CALL FAT32_FILEX
        JP NZ,FAILED
        LD HL,#9000,DE,EXPECTED,B,10
.compare:
        LD A,(DE)
        CP (HL)
        JR NZ,FAILED
        INC HL:INC DE
        DJNZ .compare
        CALL FAT32_CLOSE
        JR NZ,FAILED
        LD HL,PASS_NAME
        CALL FAT32_CREATE
        JR NZ,FAILED
        LD HL,PASS_TEXT,BC,PASS_END-PASS_TEXT
        CALL FAT32_APPEND
        JR NZ,FAILED
        CALL FAT32_CLOSE
        JR NZ,FAILED
        LD A,4
        OUT (#FE),A
.halt:
        HALT
        JR .halt
FAILED:
        LD (#9100),A
        LD A,2
        OUT (#FE),A
.halt:
        HALT
        JR .halt
NAME: DB 0,0,0,0,0,"FATDRVT1.BIN",0
PASS_NAME: DB 0,0,0,0,0,"FATDRVOK.TXT",0
PASS_TEXT: DB "FAT32 standalone: APPEND, FILEX, sync OK",13,10
PASS_END:
REPLACEMENT: DB "FAT32!"
EXPECTED: DB #FD,#FE,"FAT32!",5,6
PARAM: DB 32,1,0,0
        DS 28
        SAVEBIN "smoke.bin",START,$-START
