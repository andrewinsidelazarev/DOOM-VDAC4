/* На стенде компилируется настоящий микшер; добавлены только чтения его состояния.
 * В ELF эти функции не входят. Проверяем применённые коэффициенты, не копии настроек. */
#include "../main/sound.c"

int host_music_volume(void) {
    vd_core->mutex_lock(audio_lock);
    int value=music_volume;
    vd_core->mutex_unlock(audio_lock);
    return value;
}

int host_sfx_gain(int slider) {
    int found=0;
    float gain=slider*8/127.0f*0.3f;
    vd_core->mutex_lock(audio_lock);
    for(unsigned i=0;i<SFX;i++)
        if(effects[i].data && fabsf(effects[i].left+effects[i].right-gain)<0.000001f)
            found=1;
    vd_core->mutex_unlock(audio_lock);
    return found;
}
