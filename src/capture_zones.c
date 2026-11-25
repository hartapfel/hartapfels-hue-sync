#define _POSIX_C_SOURCE 200809L
#include "blackbars.h"
#include "capture.h"
#include "video.h"
#include <errno.h>
#include <signal.h>
#include <sys/prctl.h>
#include <unistd.h>
#include <string.h>
#include "music.h"

static int gaming;

static volatile sig_atomic_t stopping;
static void stop(int signal_number) { (void)signal_number; stopping = 1; }
static int cancelled(void *unused) { (void)unused; return stopping; }

static int sample(const struct capture_bitmap *frame, void *context)
{
    struct bitmap_region picture;
    if (!c9_active_picture(context, frame, &picture)) return -1;
    uint8_t zones[C9_ZONE_BYTES];
    for (unsigned i = 0; i < C9_VIDEO_AREA_COUNT; ++i) {
        struct bitmap_result result;
        if (!c9_video_sample(frame, picture, c9_sample_rect(i, gaming), 5000, &result)) return -1;
        for (unsigned k = 0; k < 3; ++k) {
            zones[6*i + k] = (uint8_t)result.average[k];
            zones[6*i + 3 + k] = (uint8_t)result.trimean[k];
        }
    }
    size_t sent = 0;
    while (!stopping && sent < sizeof(zones)) {
        ssize_t bytes = write(3, zones + sent, sizeof(zones) - sent);
        if (bytes < 0 && errno == EINTR) continue;
        if (bytes <= 0) return -1;
        sent += (size_t)bytes;
    }
    return stopping ? 1 : 0;
}

int main(int argc, char **argv)
{
    gaming = argc == 2 && strcmp(argv[1], "gaming") == 0;
    int music = argc == 2 && strcmp(argv[1], "music") == 0;
    if (argc != 1 && !gaming && !music) return 2;
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    signal(SIGPIPE, SIG_IGN);
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) || getppid() == 1) return 1;
    if (music) return c9_capture_music(&stopping);
    struct c9_bar_detector bars;
    c9_bars_init(&bars);
    const struct capture_settings settings = {320, 180, 30, YUV_BT709, YUV_LIMITED};
    return c9_capture_video(&settings, sample, &bars, cancelled) == 0 ? 0 : 1;
}
