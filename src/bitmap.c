#include "bitmap.h"

#if defined(__clang__) || defined(__GNUC__)
#define grid_sqrt(x) __builtin_sqrtf(x)
#else
#include <math.h>
#define grid_sqrt(x) sqrtf(x)
#endif

static unsigned channel_trimean(const unsigned *bins, unsigned levels, unsigned count)
{
    unsigned ranks[3] = {count / 4, count / 2, (3 * count) / 4};
    unsigned quartiles[3];
    unsigned seen = 0, previous = 0, q = 0;
    for (unsigned value = 0; value < levels && q < 3; ++value) {
        if (bins[value] == 0)
            continue;
        unsigned end = seen + bins[value];
        while (q < 3 && ranks[q] < end) {
            quartiles[q] = (count % 2 == 0 && ranks[q] == seen)
                ? (previous + value) / 2 : value;
            ++q;
        }
        seen = end;
        previous = value;
    }
    return (quartiles[0] + 2 * quartiles[1] + quartiles[2] + 2) / 4;
}

int analyze_bitmap(const uint8_t *pixels, size_t available, unsigned width,
                   unsigned height, size_t stride, enum pixel_layout layout,
                   unsigned budget, struct bitmap_result *result)
{
    if (!pixels || !result || !width || !height || !budget ||
        width > 16384 || height > 16384 || budget > 65536 ||
        (unsigned)layout > RGB10A2 || stride < width * 4u ||
        available < width * 4u || (height > 1 && stride > (available - width * 4u) / (height - 1)))
        return 0;

    unsigned bins[3][1024] = {0};
    struct bitmap_result out = {0};
    const float aspect = (float)width / height;
    const float rows = grid_sqrt((float)budget / aspect);
    const float dy = height / rows;
    const float dx = width / (rows * aspect);
    for (unsigned col = 0; out.samples < budget; ++col) {
        unsigned x = (unsigned)(col * dx + 0.5f);
        if (x >= width)
            break;
        for (unsigned row = 0; out.samples < budget; ++row) {
            unsigned y = (unsigned)(row * dy + 0.5f);
            if (y >= height)
                break;
            const uint8_t *p = pixels + y * stride + 4u * x;
            unsigned rgb[3];
            if (layout == RGB10A2) {
                rgb[0] = p[0] | ((p[1] & 3u) << 8);
                rgb[1] = (p[1] >> 2) | ((p[2] & 15u) << 6);
                rgb[2] = (p[2] >> 4) | ((p[3] & 63u) << 4);
            } else {
                rgb[0] = p[layout == RGBX8 ? 0 : 2];
                rgb[1] = p[1];
                rgb[2] = p[layout == RGBX8 ? 2 : 0];
            }
            for (unsigned c = 0; c < 3; ++c) {
                ++bins[c][rgb[c]];
                out.average[c] += rgb[c];
            }
            ++out.samples;
        }
    }
    for (unsigned c = 0; c < 3; ++c) {
        out.average[c] /= out.samples;
        out.trimean[c] = channel_trimean(bins[c], layout == RGB10A2 ? 1024 : 256, out.samples);
    }
    *result = out;
    return 1;
}
