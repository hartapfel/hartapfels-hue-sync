#include <algorithm>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstdlib>
extern "C" {
#include "capture.h"
}

namespace {
using Clock = std::chrono::steady_clock;
using Pixel = std::array<int, 3>;
using Chart = std::array<Pixel, 24>;

struct Probe {
    capture_cancelled interrupted;
    Clock::time_point deadline;
    Chart anchor{}, sum{};
    unsigned width{}, height{}, count{};
    pixel_layout layout{};
    bool complete{};
};

int finished(void *context) {
    auto& p = *static_cast<Probe *>(context);
    return p.interrupted(nullptr) || Clock::now() >= p.deadline;
}

Pixel raw_pixel(const capture_bitmap& frame, unsigned x, unsigned y) {
    const auto *line = frame.pixels.data + frame.pixels.stride * y;
    if (frame.layout == RGB24) return {line[3*x], line[3*x+1], line[3*x+2]};
    unsigned chroma_y = frame.layout == NV12 ? y/2 : y;
    const auto *uv = frame.chroma.data + chroma_y * frame.chroma.stride + (x/2) * 2;
    return {line[x], uv[0], uv[1]};
}

bool close_codes(const Pixel& a, const Pixel& b, int tolerance) {
    for (unsigned c = 0; c < a.size(); ++c)
        if (std::abs(a[c] - b[c]) > tolerance) return false;
    return true;
}

bool sample(const capture_bitmap& frame, Chart& values) {
    if ((frame.layout != RGB24 && frame.layout != NV12 && frame.layout != NV16) ||
        frame.width < 48 || frame.height < 32)
        return false;
    for (unsigned i = 0; i < values.size(); ++i) {
        unsigned x = (2 * (i % 6) + 1) * frame.width / 12;
        unsigned y = (2 * (i / 6) + 1) * frame.height / 8;
        unsigned dx = frame.width / 48, dy = frame.height / 32;
        auto center = raw_pixel(frame, x, y);
        for (unsigned sy : {y-dy, y+dy})
            for (unsigned sx : {x-dx, x+dx})
                if (!close_codes(raw_pixel(frame, sx, sy), center, 3)) return false;
        values[i] = center;
    }
    // The chart starts with an asymmetric neutral marker. Recognize relative
    // contrast without assuming the TV's transfer function or quantization.
    if (!close_codes(values[0], values[2], 3) || !close_codes(values[0], values[5], 3) ||
        !close_codes(values[1], values[3], 3) || !close_codes(values[1], values[4], 3) ||
        !close_codes(values[0], values[6], 3) || !close_codes(values[1], values[15], 3))
        return false;
    return values[1][0] - values[0][0] >= 20;
}

int observe(const capture_bitmap *frame, void *context) {
    auto& p = *static_cast<Probe *>(context);
    Chart current{};
    if (!sample(*frame, current)) {
        p.count = 0;
        return 0;
    }
    bool stable = p.count && frame->width == p.width && frame->height == p.height && frame->layout == p.layout;
    for (unsigned i = 0; stable && i < current.size(); ++i)
        stable = close_codes(p.anchor[i], current[i], 3);
    if (!stable) {
        p.count = 0;
        p.sum = {};
        p.anchor = current;
        p.width = frame->width;
        p.height = frame->height;
        p.layout = frame->layout;
    }
    for (unsigned i = 0; i < current.size(); ++i)
        for (unsigned c = 0; c < 3; ++c) p.sum[i][c] += current[i][c];
    if (++p.count < 8) return 0;
    std::printf("{\"chart\":\"c9-hdr10-v1\",\"width\":%u,\"height\":%u,\"format\":\"%s\","
                "\"frames\":%u,\"patches\":[", p.width, p.height,
                p.layout == RGB24 ? "RGB24" : (p.layout == NV16 ? "NV16" : "NV12"), p.count);
    for (unsigned i = 0; i < p.sum.size(); ++i) {
        const auto& s = p.sum[i];
        std::printf("%s[%.3f,%.3f,%.3f]", i ? "," : "", s[0]/8., s[1]/8., s[2]/8.);
    }
    std::puts("]}");
    p.complete = true;
    return 1;
}
}

int measure_hdr_chart(bool direct, capture_cancelled interrupted) {
    Probe probe{};
    probe.interrupted = interrupted;
    probe.deadline = Clock::now() + std::chrono::seconds(90);
    std::fprintf(stderr, "Waiting for the HDR10 chart from %s\n",
                 direct ? "direct TV capture" : "PicCap on localhost:19401");
    capture_settings settings{320, 180, 30, YUV_BT709, YUV_LIMITED};
    auto source = direct ? c9_capture_video : c9_receive_piccap;
    int result = source(&settings, observe, &probe, finished);
    if (!probe.complete) std::fputs("No stable HDR10 chart measurement received\n", stderr);
    return result == 0 && probe.complete ? 0 : 1;
}
