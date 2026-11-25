#pragma once
#include <stddef.h>
#include <stdint.h>

enum pixel_layout { RGBX8, BGRX8, RGB10A2, RGB24, NV12, NV16 };
enum yuv_matrix { YUV_BT601, YUV_BT709, YUV_BT2020_NCL };
enum yuv_range { YUV_LIMITED, YUV_FULL };

struct bitmap_plane {
    const uint8_t *data;
    size_t available, stride;
};

struct capture_bitmap {
    unsigned width, height;
    enum pixel_layout layout;
    struct bitmap_plane pixels, chroma;
    enum yuv_matrix matrix;
    enum yuv_range range;
};

struct bitmap_region { unsigned x, y, width, height; };
int c9_bar_probe(const struct capture_bitmap *frame, unsigned col, unsigned row,
                 unsigned *left, unsigned *top);
struct bitmap_result {
    uint32_t average[3];
    uint32_t trimean[3];
    uint32_t samples;
};

// Pixels start at the top left of a selected region. Stride is in bytes and
// available includes any inter-row padding. This entry point takes packed RGB.
int analyze_bitmap(const uint8_t *pixels, size_t available, unsigned width,
                   unsigned height, size_t stride, enum pixel_layout layout,
                   unsigned budget, struct bitmap_result *result);

// Uses full-frame coordinates for chroma even when the region starts on an odd
// row or column. YUV is decoded to nonlinear RGB in its source primaries.
int analyze_region(const struct capture_bitmap *frame, struct bitmap_region region,
                   unsigned budget, struct bitmap_result *result);
