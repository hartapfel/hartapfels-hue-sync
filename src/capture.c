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

int c9_capture_video(const struct capture_settings *settings, capture_callback consume,
                     void *context, capture_cancelled cancelled)
{
    if (!settings || !consume || !cancelled || !settings->width || settings->width > 16384 ||
        !settings->height || settings->height > 16384 || !settings->fps || settings->fps > 60 ||
        (unsigned)settings->matrix > YUV_BT2020_NCL || (unsigned)settings->range > YUV_FULL)
        return -1;
    // The capture backend is installed beside the worker in its own service.
    void *library = dlopen("$ORIGIN/libdile_vt_backend.so", RTLD_NOW | RTLD_LOCAL);
    if (!library) {
        fprintf(stderr, "C9 video backend: %s\n", dlerror());
        return -1;
    }
    capture_backend_t api = {0};
    api.init = (capture_init_t)dlsym(library, "capture_init");
    api.start = (capture_start_t)dlsym(library, "capture_start");
    api.wait = (capture_wait_t)dlsym(library, "capture_wait");
    api.acquire_frame = (capture_acquire_frame_t)dlsym(library, "capture_acquire_frame");
    api.release_frame = (capture_release_frame_t)dlsym(library, "capture_release_frame");
    api.terminate = (capture_terminate_t)dlsym(library, "capture_terminate");
    api.cleanup = (capture_cleanup_t)dlsym(library, "capture_cleanup");
    int status = -1;
    if (!api.init || !api.start || !api.wait || !api.acquire_frame ||
        !api.release_frame || !api.terminate || !api.cleanup) {
        fputs("Incomplete video backend ABI\n", stderr);
        goto close_library;
    }
    cap_backend_config_t config = {0};
    config.resolution_width = (int)settings->width;
    config.resolution_height = (int)settings->height;
    config.fps = (int)settings->fps;
    config.no_gui = true;
    config.quirks = QUIRK_DILE_VT_CREATE_EX;
    int init_result = api.init(&config, &api.state);
    if (init_result != 0) {
        fprintf(stderr, "DILE capture initialization failed (%d)\n", init_result);
        if (access("/dev/mem", R_OK | W_OK) != 0 && (errno == EACCES || errno == EPERM))
            fputs("DILE capture device access denied\n", stderr);
        goto close_library;
    }
    if (api.start(api.state) != 0) {
        fputs("DILE capture start failed\n", stderr);
        goto cleanup;
    }
    status = 0;
    const uint64_t interval_us = (1000000u + settings->fps - 1) / settings->fps;
    uint64_t next_sample_us = 0;
    while (!cancelled(context)) {
        frame_info_t source = {0};
        if (api.wait(api.state) != 0) { status = -1; break; }
        // C9 firmware can signal every display refresh despite the DILE divider.
        // Wait before borrowing the current frame so capture never holds a
        // frozen buffer during this delay. A slower input needs no extra wait.
        uint64_t now = getticks_us();
        while (now < next_sample_us && !cancelled(context)) {
            uint64_t remaining = next_sample_us - now;
            struct timespec delay = {(time_t)(remaining / 1000000u),
                                     (long)(remaining % 1000000u) * 1000};
            if (nanosleep(&delay, NULL) != 0 && errno != EINTR) { status = -1; break; }
            now = getticks_us();
        }
        if (status != 0) break;
        if (cancelled(context)) break;
        next_sample_us = now + interval_us;
        if (api.acquire_frame(api.state, &source) != 0) { status = -1; break; }
        struct capture_bitmap frame = {0};
        int next = borrow_bitmap(&source, settings, &frame) ? consume(&frame, context) : -1;
        if (api.release_frame(api.state, &source) != 0 || next < 0) { status = -1; break; }
        if (next > 0) break;
    }
    if (api.terminate(api.state) != 0) status = -1;
cleanup:
    if (api.cleanup(api.state) != 0) status = -1;
close_library:
    dlclose(library);
    return status;
}
