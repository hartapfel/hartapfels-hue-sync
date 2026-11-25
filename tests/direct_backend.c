/* Capture fixture loaded only in the ARM test guest. Use the same capture ABI
   as the release module and fail if the worker misorders frame ownership. */
#include <stdio.h>
#include "unicapture.h"

enum { WIDTH = 96, HEIGHT = 64, STRIDE = 102 };
static unsigned char planes[2][HEIGHT * STRIDE];
static enum { INITIALIZED, STARTED, WAITED, ACQUIRED, STOPPED } phase;
static unsigned releases;

int capture_init(cap_backend_config_t *config, void **state)
{
    if (config->resolution_width != 256 || config->resolution_height != 144 ||
        config->fps != 30 || !config->no_gui) return -1;
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
