/* Capture fixture loaded only in the ARM test guest. Use the same capture ABI
   as the release module and fail if the worker misorders frame ownership. */
#include <stdio.h>
#include "unicapture.h"
#include "quirks.h"
#include "color.h"

enum { WIDTH = 96, HEIGHT = 64, STRIDE = 102 };
static unsigned char planes[2][HEIGHT * STRIDE];
static enum { INITIALIZED, STARTED, WAITED, ACQUIRED, STOPPED } phase;
static unsigned releases;

/* In the numerical regression run these functions resolve to the exported
   symbols in the packaged worker. Preserve filter history until the reference
   starts a new sequence, including across changes in the tick interval. */
static int compare_desktop_ticks(void)
{
    FILE *file = fopen("/fixture/desktop-ticks", "rb");
    if (!file) return 0;
    struct tick {
        struct c9_rgb input;
        float gain, fps;
        uint32_t threshold, reset;
        float expected[3];
    } tick;
    _Static_assert(sizeof(tick) == 40, "Expected the desktop vector file layout");
    struct c9_lab history = {0};
    unsigned count = 0;
    float worst = 0.f;
    size_t bytes;
    while ((bytes = fread(&tick, 1, sizeof(tick), file)) == sizeof(tick)) {
        if (tick.reset) history = (struct c9_lab){0};
        struct c9_rgb output = c9_video_moderate_tick(tick.input, &history, tick.gain, tick.fps, tick.threshold);
        float rgb[] = {output.r, output.g, output.b};
        for (unsigned i = 0; i < 3; ++i) {
            float difference = rgb[i] - tick.expected[i];
            if (difference < 0.f) difference = -difference;
            if (!(difference >= 0.f)) { fclose(file); return -1; }
            if (difference > worst) worst = difference;
        }
        ++count;
    }
    int complete = bytes == 0 && feof(file) && count > 0;
    fclose(file);
    fprintf(stderr, "Desktop ticks %u max RGB error %.9g\n", count, (double)worst);
    return complete && worst <= 1e-5f ? 0 : -1;
}

int capture_init(cap_backend_config_t *config, void **state)
{
    if (config->quirks != QUIRK_DILE_VT_CREATE_EX || config->resolution_width != 320 || config->resolution_height != 180 ||
        config->fps != 30 || !config->no_gui) return -1;
    if (compare_desktop_ticks() != 0) return -1;
    FILE *file = fopen("/fixture/direct.nv16", "rb");
    if (!file) return -1;
    size_t bytes = fread(planes, 1, sizeof(planes), file);
    fclose(file);
    if (bytes != sizeof(planes)) return -1;
    *state = &phase;
    return 0;
}

int capture_start(void *state)
{
    if (state != &phase || phase != INITIALIZED) return -1;
    phase = STARTED;
    return 0;
}

int capture_wait(void *state)
{
    if (state != &phase || phase != STARTED || releases >= 8) return -1;
    phase = WAITED;
    return 0;
}

int capture_acquire_frame(void *state, frame_info_t *frame)
{
    if (state != &phase || phase != WAITED) return -1;
    frame->width = WIDTH;
    frame->height = HEIGHT;
    frame->pixel_format = PIXFMT_YUV422_SEMI_PLANAR;
    for (int i = 0; i < 2; ++i) {
        frame->planes[i].buffer = planes[i];
        frame->planes[i].stride = STRIDE;
    }
    phase = ACQUIRED;
    return 0;
}

int capture_release_frame(void *state, frame_info_t *frame)
{
    if (state != &phase || phase != ACQUIRED || frame->planes[1].buffer != planes[1]) return -1;
    phase = STARTED;
    ++releases;
    return 0;
}

int capture_terminate(void *state)
{
    if (state != &phase || phase != STARTED) return -1;
    phase = STOPPED;
    return 0;
}

int capture_cleanup(void *state)
{
    return state == &phase && phase == STOPPED && releases == 8 ? 0 : -1;
}
