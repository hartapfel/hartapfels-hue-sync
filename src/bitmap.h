#pragma once
#include <stddef.h>
#include <stdint.h>

enum pixel_layout { RGBX8, BGRX8, RGB10A2 };
struct bitmap_result {
    uint32_t average[3];
    uint32_t trimean[3];
    uint32_t samples;
};

// Pixels start at the top left of a selected region. Stride is in bytes and
// available includes any inter-row padding. All three layouts are little endian.
int analyze_bitmap(const uint8_t *pixels, size_t available, unsigned width,
                   unsigned height, size_t stride, enum pixel_layout layout,
                   unsigned budget, struct bitmap_result *result);
