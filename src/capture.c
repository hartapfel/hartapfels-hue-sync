#define _POSIX_C_SOURCE 200809L
#include "capture.h"
#include "unicapture.h"
#include "log.h"
#include "quirks.h"
#include <errno.h>
#include <unistd.h>
#include <dlfcn.h>
#include <time.h>

// These two host callbacks are imported by PicCap's DILE video backend.
uint64_t getticks_us(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000000 + now.tv_nsec / 1000;
}

void log_printf(LogLevel level, const char *module, const char *fmt, ...)
{
    if (level > Info) return;
    fprintf(stderr, "%s: ", module);
    va_list args;
    va_start(args, fmt);
    vfprintf(stderr, fmt, args);
    va_end(args);
    fputc('\n', stderr);
}

static int borrow_plane(struct bitmap_plane *dest, const frame_info_t *source,
                        unsigned index, unsigned rows, unsigned row_bytes)
{
    int stride = source->planes[index].stride;
    if (!source->planes[index].buffer || stride <= 0 || (unsigned)stride < row_bytes ||
        rows > SIZE_MAX / (unsigned)stride)
        return 0;
    *dest = (struct bitmap_plane){source->planes[index].buffer, rows * (size_t)stride, (size_t)stride};
    return 1;
}

static int borrow_bitmap(const frame_info_t *source, const struct capture_settings *settings,
                         struct capture_bitmap *frame)
{
    if (source->width <= 0 || source->width > 16384 || source->height <= 0 || source->height > 16384)
        return 0;
    frame->width = (unsigned)source->width;
    frame->height = (unsigned)source->height;
    frame->matrix = settings->matrix;
    frame->range = settings->range;
    switch (source->pixel_format) {
    case PIXFMT_RGB:
        frame->layout = RGB24;
        return borrow_plane(&frame->pixels, source, 0, frame->height, frame->width * 3);
    case PIXFMT_YUV420_SEMI_PLANAR: frame->layout = NV12; break;
    case PIXFMT_YUV422_SEMI_PLANAR: frame->layout = NV16; break;
    default: return 0;
    }
    unsigned rows = frame->layout == NV12 ? (frame->height + 1) / 2 : frame->height;
    return borrow_plane(&frame->pixels, source, 0, frame->height, frame->width) &&
           borrow_plane(&frame->chroma, source, 1, rows, (frame->width + 1) / 2 * 2);
}

struct layer {
    void *library;
    capture_backend_t api;
    int started;
};

static int close_layer(struct layer *layer)
{
    int result = 0;
    if (layer->started && layer->api.terminate(layer->api.state) != 0) result = -1;
    if (layer->api.state && layer->api.cleanup(layer->api.state) != 0) result = -1;
    if (layer->library) dlclose(layer->library);
    *layer = (struct layer){0};
    return result;
}

static int open_layer(struct layer *layer, const char *path, cap_backend_config_t *config)
{
    layer->library = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!layer->library) {
        fprintf(stderr, "C9 %s backend: %s\n", config->no_gui ? "video" : "graphics", dlerror());
        return 0;
    }
    capture_backend_t *a = &layer->api;
    a->init = (capture_init_t)dlsym(layer->library, "capture_init");
    a->start = (capture_start_t)dlsym(layer->library, "capture_start");
    a->wait = (capture_wait_t)dlsym(layer->library, "capture_wait");
    a->acquire_frame = (capture_acquire_frame_t)dlsym(layer->library, "capture_acquire_frame");
    a->release_frame = (capture_release_frame_t)dlsym(layer->library, "capture_release_frame");
    a->terminate = (capture_terminate_t)dlsym(layer->library, "capture_terminate");
    a->cleanup = (capture_cleanup_t)dlsym(layer->library, "capture_cleanup");
    if (a->init && a->start && a->wait && a->acquire_frame && a->release_frame && a->terminate && a->cleanup) {
        void *state = NULL;
        if (a->init(config, &state) == 0) {
            a->state = state;
            if (a->start(state) == 0) {
                layer->started = 1;
                return 1;
            }
        }
    }
    fprintf(stderr, "Could not initialize %s capture\n", path);
    close_layer(layer);
    return 0;
}

static int capture(const struct capture_settings *settings, capture_callback consume,
                   void *context, capture_cancelled cancelled, int display)
{
    if (!settings || !consume || !cancelled || !settings->width || settings->width > 16384 ||
        !settings->height || settings->height > 16384 || !settings->fps || settings->fps > 60 ||
        (unsigned)settings->matrix > YUV_BT2020_NCL || (unsigned)settings->range > YUV_FULL) return -1;
    cap_backend_config_t config = {0};
    config.resolution_width = (int)settings->width;
    config.resolution_height = (int)settings->height;
    config.fps = (int)settings->fps;
    config.quirks = QUIRK_DILE_VT_CREATE_EX;
    struct layer ui = {0}, video = {0};
    if (display) open_layer(&ui, "$ORIGIN/libgm_backend.so", &config);
    config.no_gui = true;
    open_layer(&video, "$ORIGIN/libdile_vt_backend.so", &config);
    int status = 0;
    const uint64_t interval = (1000000u + settings->fps - 1) / settings->fps;
    uint64_t next_sample = 0, retry_video = getticks_us() + 1000000, next_probe = 0;
    int visible = -1;
    while (!cancelled(context)) {
        if (!video.started && !ui.started) { status = -1; break; }
        // DILE vsync may stop on a graphics-only input. GM supplies its own
        // current surface so use the capture clock whenever GM is available.
        if (!ui.started && video.api.wait(video.api.state) != 0) { status = -1; break; }
        uint64_t now = getticks_us();
        while (now < next_sample && !cancelled(context)) {
            uint64_t left = next_sample - now;
            struct timespec delay = {(time_t)(left/1000000), (long)(left%1000000)*1000};
            if (nanosleep(&delay,NULL) != 0 && errno != EINTR) { status = -1; break; }
            now = getticks_us();
        }
        if (status || cancelled(context)) break;
        next_sample = now + interval;
        if (!video.started && now >= retry_video) {
            open_layer(&video, "$ORIGIN/libdile_vt_backend.so", &config);
            retry_video = now + 1000000;
        }
        frame_info_t vf = {0}, gf = {0};
        int have_ui = ui.started && ui.api.acquire_frame(ui.api.state, &gf) == 0;
        int have_video = video.started && video.api.acquire_frame(video.api.state, &vf) == 0;
        struct capture_bitmap base = {0}, graphics = {0};
        const struct capture_bitmap *frame = have_video && borrow_bitmap(&vf,settings,&base) ? &base : NULL;
        if (have_ui && gf.pixel_format == PIXFMT_ARGB && gf.width > 0 && gf.width <= 16384 &&
            gf.height > 0 && gf.height <= 16384 && borrow_plane(&graphics.pixels,&gf,0,gf.height,gf.width*4)) {
            graphics.width = gf.width;
            graphics.height = gf.height;
            graphics.layout = COMPOSITED_BGRA;
            graphics.background = frame;
            frame = &graphics;
            if (now >= next_probe) {
                int found = 0;
                for (unsigned y = 0; y < graphics.height && !found; y += 4)
                    for (unsigned x = 0; x < graphics.width && !found; x += 4)
                        found = graphics.pixels.data[y * graphics.pixels.stride + 4*x + 3] != 0;
                if (found != visible) fprintf(stderr,"graphics-visible=%d\n", found);
                visible = found;
                next_probe = now + 250000;
            }
        }
        int next = frame ? consume(frame,context) : ui.started ? 0 : -1;
        if (have_video && video.api.release_frame(video.api.state,&vf) != 0) next = -1;
        if (have_ui && ui.api.release_frame(ui.api.state,&gf) != 0) next = -1;
        if (next) { status = next < 0 ? -1 : 0; break; }
    }
    if (close_layer(&video) != 0) status = -1;
    if (close_layer(&ui) != 0) status = -1;
    return status;
}

int c9_capture_video(const struct capture_settings *settings, capture_callback consume,
                     void *context, capture_cancelled cancelled)
{
    return capture(settings, consume, context, cancelled, 0);
}

int c9_capture_display(const struct capture_settings *settings, capture_callback consume,
                       void *context, capture_cancelled cancelled)
{
    return capture(settings, consume, context, cancelled, 1);
}
