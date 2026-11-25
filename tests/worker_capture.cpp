#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>
extern "C" {
#include "capture.h"
}

void fixture_tick(std::chrono::steady_clock::time_point now) {
    static unsigned ticks = 0;
    auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(now.time_since_epoch()).count();
    std::fprintf(stderr, "tick-ns %lld\n", static_cast<long long>(ns));
    if (++ticks == 12 && std::getenv("C9_TEST_SLOW_TICK"))
        std::this_thread::sleep_for(std::chrono::milliseconds(85));
}

// Substitute only the hardware frame producer in the host integration build.
int c9_capture_video(const capture_settings *settings, capture_callback consume, void *context,
                     capture_cancelled cancelled) {
    if (std::getenv("C9_TEST_CAPTURE_FAIL")) return -1;
    std::vector<uint8_t> pixels(settings->width * settings->height * 3);
    for (unsigned y = 0; y < settings->height; ++y) {
        for (unsigned x = 0; x < settings->width; ++x)
            pixels[(y * settings->width + x) * 3 + (x < settings->width / 2 ? 0 : 2)] = 255;
    }
    capture_bitmap frame{settings->width, settings->height, RGB24,
        {pixels.data(), pixels.size(), settings->width * 3}, {}, settings->matrix, settings->range};
    for (unsigned i = 0; i < settings->fps && !cancelled(context); ++i) {
        if (consume(&frame, context) < 0) return -1;
        std::this_thread::sleep_for(std::chrono::milliseconds(1000 / settings->fps));
    }
    return 0;
}
