#include <math.h>
#include <stdint.h>

// Synthetic vendor callback for the isolated ARM guest. Never reads an audio device.
int DILE_AUDIO_PCM_StartUpload(int channel) { return channel != 0; }
int DILE_AUDIO_PCM_StopUpload(int channel) { return channel != 0; }
int DILE_AUDIO_PCM_RegSendPCMCallback(int channel, int (*callback)(const void *, int))
{
    if (channel || !callback) return -1;
    const int frequency[] = {0, 60, 900, 9000};
    int16_t stereo[2 * 4800] = {0};
    for (unsigned band = 0; band < 4; ++band) {
        for (unsigned i = 0; i < 4800; ++i) {
            stereo[i*2] = (int16_t)(9000 * sin(6.283185307179586 * frequency[band] * i / 48000));
            stereo[i*2+1] = 0;
        }
        callback(stereo, sizeof(stereo));
    }
    return 0;
}
