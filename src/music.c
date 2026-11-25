#define _POSIX_C_SOURCE 200809L
#include "music.h"
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <string.h>
#include <unistd.h>

// Stereo 48 kHz playback. Split at 160 Hz and 2.2 kHz before measuring energy.
void c9_music_zones(struct music_filter *f, const int16_t *samples, unsigned frames,
                    uint8_t zones[C9_ZONE_BYTES])
{
    float energy[2][3] = {{0}};
    for (unsigned i = 0; i < frames; ++i) {
        for (unsigned ch = 0; ch < 2; ++ch) {
            float x = samples[2*i+ch] / 32768.f;
            f->bass[ch] += .020726f * (x - f->bass[ch]);
            f->low_mid[ch] += .250223f * (x - f->low_mid[ch]);
            float mid = f->low_mid[ch] - f->bass[ch], high = x - f->low_mid[ch];
            energy[ch][0] += f->bass[ch] * f->bass[ch];
            energy[ch][1] += mid * mid;
            energy[ch][2] += high * high;
        }
    }
    const struct video_area *areas = c9_video_areas();
    for (unsigned zone = 0; zone < C9_VIDEO_AREA_COUNT; ++zone) {
        float right = (areas[zone].screen.left + areas[zone].screen.right) * .5f;
        for (unsigned band = 0; band < 3; ++band) {
            float e = energy[0][band] * (1.f - right) + energy[1][band] * right;
            float rms = frames ? sqrtf(e / frames) : 0.f;
            float level = fmaxf(0.f, rms - .0005f) * 8.f;
            uint8_t code = (uint8_t)(255.f * level / (1.f + level));
            zones[6*zone+band] = zones[6*zone+3+band] = code;
        }
    }
}

static int frames_pipe[2];
static struct music_filter filter;
static int on_pcm(const void *pcm, int bytes)
{
    if (pcm && bytes >= 4 && bytes <= 262144 && bytes % 4 == 0) {
        uint8_t zones[C9_ZONE_BYTES];
        c9_music_zones(&filter, pcm, (unsigned)bytes/4, zones);
        // Atomic nonblocking pipe writes drop a frame if the consumer is busy.
        ssize_t sent = write(frames_pipe[1], zones, sizeof(zones));
        (void)sent;
    }
    return 0;
}

int c9_capture_music(volatile sig_atomic_t *stopping)
{
    void *lib = dlopen("libdile_audio_pcm.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!lib) return 1;
    int (*start)(int) = dlsym(lib, "DILE_AUDIO_PCM_StartUpload");
    int (*stop)(int) = dlsym(lib, "DILE_AUDIO_PCM_StopUpload");
    int (*listen)(int,int (*)(const void *,int)) = dlsym(lib, "DILE_AUDIO_PCM_RegSendPCMCallback");
    if (!start || !stop || !listen || pipe(frames_pipe) < 0 ||
        fcntl(frames_pipe[1], F_SETFL, O_NONBLOCK) < 0 || start(0) != 0) return 1;
    int rc = listen(0, on_pcm);
    struct pollfd input = {frames_pipe[0], POLLIN, 0};
    while (rc == 0 && !*stopping) {
        int ready = poll(&input, 1, 200);
        if (ready < 0 && errno == EINTR) continue;
        if (!ready) continue;
        uint8_t frame[C9_ZONE_BYTES];
        if (ready < 0 || read(input.fd, frame, sizeof(frame)) != sizeof(frame)) { rc = 1; break; }
        size_t offset = 0;
        while (offset < sizeof(frame) && !*stopping) {
            ssize_t n = write(3, frame + offset, sizeof(frame) - offset);
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) { rc = 1; break; }
            offset += (size_t)n;
        }
    }
    // Stop the vendor callback before exiting the capture process.
    if (stop(0) != 0) rc = 1;
    return rc != 0;
}
