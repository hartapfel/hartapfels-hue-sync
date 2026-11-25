#define _POSIX_C_SOURCE 200809L
#include "capture.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static volatile sig_atomic_t stop;
static void request_stop(int sig) { (void)sig; stop = 1; }

struct measurement {
    unsigned frames, limit;
    uint64_t samples, processing_ns;
    struct bitmap_result last;
};

static uint64_t nanoseconds(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000 + (unsigned)t.tv_nsec;
}

static int measure(const struct capture_bitmap *frame, void *context)
{
    struct measurement *m = context;
    if (!m->frames)
        printf("Frame %ux%u, format %d, strides %zu/%zu\n", frame->width, frame->height,
               frame->layout, frame->pixels.stride, frame->chroma.stride);
    struct bitmap_region whole = {0, 0, frame->width, frame->height};
    uint64_t start = nanoseconds();
    if (!analyze_region(frame, whole, 1000, &m->last)) return -1;
    m->processing_ns += nanoseconds() - start;
    m->samples += m->last.samples;
    return ++m->frames == m->limit;
}

static unsigned positive(const char *str, unsigned limit)
{
    char *end;
    long n = strtol(str, &end, 10);
    return n > 0 && (unsigned long)n <= limit && !*end ? (unsigned)n : 0;
}

int main(int argc, char **argv)
{
    if (argc != 7) {
        fputs("Usage: c9-capture-check WIDTH HEIGHT FPS FRAMES 601|709|2020 limited|full\n", stderr);
        return 2;
    }
    struct capture_settings settings = {positive(argv[1], 16384), positive(argv[2], 16384),
                                        positive(argv[3], 60)};
    struct measurement m = {0};
    m.limit = positive(argv[4], 1000000);
    if (!m.limit || !settings.width || !settings.height || !settings.fps) return 2;
    if (!strcmp(argv[5], "601")) settings.matrix = YUV_BT601;
    else if (!strcmp(argv[5], "709")) settings.matrix = YUV_BT709;
    else if (!strcmp(argv[5], "2020")) settings.matrix = YUV_BT2020_NCL;
    else return 2;
    if (!strcmp(argv[6], "limited")) settings.range = YUV_LIMITED;
    else if (!strcmp(argv[6], "full")) settings.range = YUV_FULL;
    else return 2;
    signal(SIGTERM, request_stop);
    signal(SIGINT, request_stop);
    int ret = c9_capture_video(&settings, measure, &m, &stop);
    if (m.frames)
        printf("%u frames, %llu sampled pixels, %.1f us/frame for analysis, last trimean %u %u %u\n",
               m.frames, (unsigned long long)m.samples, m.processing_ns / (1000.0 * m.frames),
               m.last.trimean[0], m.last.trimean[1], m.last.trimean[2]);
    return ret == 0 && m.frames == m.limit ? 0 : 1;
}
