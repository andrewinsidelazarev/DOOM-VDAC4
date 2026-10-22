/* Только стенд ПК: меняем привязанные значения в потоке игры перед обычным опросом.
 * Проверяем типы, для которых нет горячих клавиш; код сохранения остаётся настоящим.
 * Основные сценарии hotkeys-* подают только PS/2 и этим каналом не пользуются. */
#include "m_config.h"
#define M_UpdateDefaults Engine_UpdateDefaults
#include "../third_party/doomgeneric/doomgeneric/m_config.c"
#undef M_UpdateDefaults

static int requested_change;

void host_config_change(int kind)
{
    __atomic_store_n(&requested_change, kind, __ATOMIC_RELEASE);
}

void M_UpdateDefaults(boolean editing)
{
    switch (__atomic_exchange_n(&requested_change, 0, __ATOMIC_ACQUIRE))
    {
        case 1: M_SetVariable("novert", "0"); break;
        case 2: M_SetVariable("mouse_acceleration", "2.5"); break;
        // Меняем содержимое строки, сохраняя её указатель.
        case 3: ((char *) M_GetStrVariable("chatmacro0"))[0] = 'B'; break;
        case 4: M_SetVariable("key_fire", "33"); break;
    }
    Engine_UpdateDefaults(editing);
}
