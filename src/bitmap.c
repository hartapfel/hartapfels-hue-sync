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

struct yuv_coefficients { int y, rv, gu, gv, bu; };

// Inverse Y'CbCr matrices in Q16, indexed by matrix and quantization range.
static const struct yuv_coefficients coefficients[][2] = {
    {{76309, 104597, -25675, -53279, 132201}, {65536, 91881, -22553, -46802, 116130}},
    {{76309, 117489, -13975, -34925, 138438}, {65536, 103206, -12276, -30679, 121609}},
    {{76309, 110014, -12277, -42626, 140363}, {65536, 96639, -10784, -37444, 123299}}
};

static unsigned channel8(int value)
{
    if (value <= 0) return 0;
    if (value >= 255 * 65536) return 255;
    return (unsigned)(value + 32768) >> 16;
}

static int covers(struct bitmap_plane plane, unsigned rows, unsigned row_bytes)
{
    return plane.data && plane.stride >= row_bytes && plane.available >= row_bytes &&
           (rows == 1 || plane.stride <= (plane.available - row_bytes) / (rows - 1));
}

static int valid_frame(const struct capture_bitmap *frame)
{
    if (!frame || !frame->width || !frame->height || frame->width > 16384 || frame->height > 16384 ||
        (unsigned)frame->layout > NV16)
        return 0;

    const enum pixel_layout layout = frame->layout;
    const int planar = layout == NV12 || layout == NV16;
    const unsigned bytes_per_pixel = planar ? 1 : layout == RGB24 ? 3 : 4;
    if (!covers(frame->pixels, frame->height, frame->width * bytes_per_pixel))
        return 0;
    if (planar) {
        unsigned chroma_rows = layout == NV12 ? (frame->height + 1) / 2 : frame->height;
        if ((unsigned)frame->matrix > YUV_BT2020_NCL || (unsigned)frame->range > YUV_FULL ||
            !covers(frame->chroma, chroma_rows, (frame->width + 1) / 2 * 2))
            return 0;
    }
    return 1;
}

static inline void pixel(const struct capture_bitmap *frame, unsigned x, unsigned y, unsigned rgb[3])
{
    enum pixel_layout layout = frame->layout;
    const uint8_t *row = frame->pixels.data + y * frame->pixels.stride;
    if (layout == NV12 || layout == NV16) {
        const struct yuv_coefficients *matrix = &coefficients[frame->matrix][frame->range];
        unsigned cy = layout == NV12 ? y / 2 : y;
        const uint8_t *uv = frame->chroma.data + cy * frame->chroma.stride + (x / 2) * 2;
        int u = uv[0] - 128, v = uv[1] - 128;
        int luma = ((int)row[x] - (frame->range == YUV_LIMITED ? 16 : 0)) * matrix->y;
        rgb[0] = channel8(luma + v * matrix->rv);
        rgb[1] = channel8(luma + u * matrix->gu + v * matrix->gv);
        rgb[2] = channel8(luma + u * matrix->bu);
    } else if (layout == RGB10A2) {
        const uint8_t *p = row + 4 * x;
        rgb[0] = p[0] | ((p[1] & 3u) << 8);
        rgb[1] = (p[1] >> 2) | ((p[2] & 15u) << 6);
        rgb[2] = (p[2] >> 4) | ((p[3] & 63u) << 4);
    } else {
        const uint8_t *p = row + (layout == RGB24 ? 3 : 4) * x;
        rgb[0] = p[layout == BGRX8 ? 2 : 0];
        rgb[1] = p[1];
        rgb[2] = p[layout == BGRX8 ? 0 : 2];
    }
}

static unsigned rgb_sum(const struct capture_bitmap *frame, unsigned x, unsigned y)
{
    unsigned rgb[3];
    pixel(frame, x, y, rgb);
    return rgb[0] + rgb[1] + rgb[2];
}

int c9_bar_probe(const struct capture_bitmap *frame, unsigned col, unsigned row,
                 unsigned *left, unsigned *top)
{
    if (!valid_frame(frame) || !left || !top || col >= frame->width || row >= frame->height) return 0;
    unsigned rows = (unsigned)(.23f * frame->height), cols = (unsigned)(.23f * frame->width);
    unsigned threshold = frame->layout == RGB10A2 ? 36 : 9;
    unsigned y = 0, x = 0;
    while (y < rows && rgb_sum(frame, col, y) <= threshold) ++y;
    while (x < cols && rgb_sum(frame, x, row) <= threshold) ++x;
    // The desktop retains the exhausted horizontal scan length if the
    // vertical probe found picture. If both miss, it records two zeroes.
    *top = y < rows ? y : 0;
    *left = y < rows || x < cols ? x : 0;
    return 1;
}

int analyze_region(const struct capture_bitmap *frame, struct bitmap_region region,
                   unsigned budget, struct bitmap_result *result)
{
    const unsigned width = region.width, height = region.height;
    if (!valid_frame(frame) || !result || !width || !height || !budget || budget > 65536 ||
        width > frame->width || height > frame->height ||
        region.x > frame->width - width || region.y > frame->height - height)
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
            unsigned px = region.x + x, py = region.y + y;
            unsigned rgb[3];
            pixel(frame, px, py, rgb);
            for (unsigned c = 0; c < 3; ++c) {
                ++bins[c][rgb[c]];
                out.average[c] += rgb[c];
            }
            ++out.samples;
        }
    }
    for (unsigned c = 0; c < 3; ++c) {
        out.average[c] /= out.samples;
        out.trimean[c] = channel_trimean(bins[c], frame->layout == RGB10A2 ? 1024 : 256, out.samples);
    }
    *result = out;
    return 1;
}

int analyze_bitmap(const uint8_t *pixels, size_t available, unsigned width,
                   unsigned height, size_t stride, enum pixel_layout layout,
                   unsigned budget, struct bitmap_result *result)
{
    struct capture_bitmap frame = {width, height, layout, {pixels, available, stride}};
    struct bitmap_region region = {0, 0, width, height};
    return analyze_region(&frame, region, budget, result);
}
