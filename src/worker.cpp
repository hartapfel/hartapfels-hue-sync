#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <thread>
#ifdef __linux__
#include <sys/prctl.h>
#include <sys/ioctl.h>
#include <unistd.h>
#elif defined(_WIN32)
#define NOMINMAX
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#endif
extern "C" {
#include "capture.h"
#include "blackbars.h"
#include "video.h"
}

#ifdef C9_WORKER_TEST
void fixture_tick(std::chrono::steady_clock::time_point now);
#endif

namespace {
using Clock = std::chrono::steady_clock;
using Targets = std::array<c9_rgb, C9_VIDEO_AREA_COUNT>;
std::atomic<bool> stopping{false};
static_assert(ATOMIC_BOOL_LOCK_FREE == 2, "Signal handler requires a lock-free stop flag");
void stop(int) { stopping.store(true, std::memory_order_relaxed); }
int cancelled(void *) { return stopping.load(std::memory_order_relaxed); }

struct Session {
    capture_settings capture{};
    uint8_t piccap{};
    char host[64]{}, area[37]{}, identity[129]{};
    uint8_t key[16]{}, ids[256]{};
    uint16_t port{}, count{};
    uint32_t mask[256]{}, used{};
    uint8_t brightness = 100, intensity = 1;
    bool live_controls = false;
};

struct Preset { float lightness, chroma; unsigned threshold; };
// AppMode.Video.Presets in Hue Sync Desktop 1.13.1.83.
constexpr Preset presets[] = {{.005f,.15f,5}, {.025f,.30f,2}, {.07f,.5f,1}, {.5f,.5f,0}};

bool read_controls(uint8_t& brightness, uint8_t& intensity) {
    uint8_t pair[2];
    if (std::fread(pair, 1, 2, stdin) != 2 || pair[0] > 100 || pair[1] >= 4) return false;
    brightness = pair[0];
    intensity = pair[1];
    return true;
}

bool controls_available() {
#ifdef _WIN32
    DWORD bytes = 0;
    return PeekNamedPipe(reinterpret_cast<HANDLE>(_get_osfhandle(_fileno(stdin))), nullptr, 0,
                         nullptr, &bytes, nullptr) && bytes >= 2;
#else
    int bytes = 0;
    return ioctl(STDIN_FILENO, FIONREAD, &bytes) == 0 && bytes >= 2;
#endif
}

bool read_bytes(void *out, size_t size) { return std::fread(out, 1, size, stdin) == size; }
unsigned u16(const uint8_t *p) { return p[0] | (unsigned(p[1]) << 8); }

bool read_session(Session& session) {
    // Local, bounded binary descriptor from the service's private stdin pipe.
    // All supported hosts and the LG C9 use little-endian IEEE binary64.
    uint8_t h[17];
    if (!read_bytes(h, sizeof(h)) || std::memcmp(h, "C9S2", 4) ||
        h[11] > YUV_BT2020_NCL || h[12] > YUV_FULL ||
        !read_bytes(&session.piccap, 1) || session.piccap > 1) return false;
    session.port = u16(h + 4);
    session.capture = {u16(h + 6), u16(h + 8), h[10], yuv_matrix(h[11]), yuv_range(h[12])};
    session.count = u16(h + 13);
    if (!session.port || !session.capture.width || session.capture.width > 16384 ||
        !session.capture.height || session.capture.height > 16384 || !h[10] || h[10] > 60 ||
        !session.count || session.count > 256 ||
        !h[15] || h[15] >= sizeof(session.host) || !h[16] || h[16] >= sizeof(session.identity))
        return false;
    if (!read_bytes(session.host, h[15]) || !read_bytes(session.identity, h[16]) ||
        !read_bytes(session.area, 36) || !read_bytes(session.key, 16) ||
        std::strlen(session.host) != h[15] || std::strlen(session.identity) != h[16]) return false;
    bool seen[256]{};
    for (unsigned i = 0; i < session.count; ++i) {
        double pos[3];
        if (!read_bytes(&session.ids[i], 1) || seen[session.ids[i]] || !read_bytes(pos, sizeof(pos))) return false;
        for (double v : pos) if (!(v >= -1. && v <= 1.)) return false;
        seen[session.ids[i]] = true;
        session.mask[i] = c9_video_area_mask(pos[0], pos[1], pos[2]);
        session.used |= session.mask[i];
    }
    return true;
}

struct Video {
    explicit Video(const Session& config) : session(config), intensity(config.intensity) { c9_bars_init(&bars); }
    const Session& session;
    std::atomic<unsigned> intensity;
    video_region_state regions[C9_VIDEO_AREA_COUNT]{}; // capture thread only
    c9_bar_detector bars;
    std::mutex mutex;
    std::condition_variable changed;
    Targets targets{};
    Clock::time_point last_capture{};
    bool done = false;
    int capture_result = 0;
};

int ingest(const capture_bitmap *frame, void *context) {
    auto& video = *static_cast<Video *>(context);
    Targets next{};
    const auto *areas = c9_video_areas();
    bitmap_region picture;
    if (!c9_active_picture(&video.bars, frame, &picture)) return -1;
    unsigned threshold = presets[video.intensity.load()].threshold;
    for (unsigned i = 0; i < next.size(); ++i) {
        if (!(video.session.used & (1u << i))) continue;
        unsigned samples;
        if (!c9_video_ingest_preset(&video.regions[i], frame, picture, areas[i].screen, 5000, &samples, threshold)) return -1;
        next[i] = video.regions[i].trimean;
    }
    // Publish only a complete frame. The DILE frame stays on the capture thread.
    {
        std::lock_guard<std::mutex> lock(video.mutex);
        video.targets = next;
        video.last_capture = Clock::now();
    }
    video.changed.notify_one();
    return 0;
}

bool await_bridge(Video& video) {
    {
        std::unique_lock<std::mutex> lock(video.mutex);
        const auto deadline = Clock::now() + std::chrono::seconds(10);
        while (video.last_capture == Clock::time_point{} && !video.done && !cancelled(nullptr)) {
            if (Clock::now() >= deadline) {
                std::fputs(video.session.piccap ? "No PicCap input on localhost port 19401\n" :
                           "No video frame received during startup\n", stderr);
                return false;
            }
            video.changed.wait_for(lock, std::chrono::milliseconds(100));
        }
        if (video.done || cancelled(nullptr)) return false;
    }
    // No capture frame or mutex is held while the service activates the area.
    std::puts("video-ready");
    std::fflush(stdout);
    if (std::fgetc(stdin) != 1 || cancelled(nullptr)) return false;
    std::lock_guard<std::mutex> lock(video.mutex);
    if (video.done || Clock::now() - video.last_capture >= std::chrono::seconds(5)) {
        std::fputs("No video frame available after bridge activation\n", stderr);
        return false;
    }
    return true;
}

int stream_video(Video& video, hue_stream *stream) {
    const auto tick = std::chrono::nanoseconds(1000000000 / 60);
    const auto send = std::chrono::milliseconds(20);
    const auto timeout = std::chrono::seconds(5);
    auto started = Clock::now(), next_tick = started, next_send = started;
    Clock::time_point previous_tick{};
    c9_lab history[C9_VIDEO_AREA_COUNT]{};
    c9_area_color layers[C9_VIDEO_AREA_COUNT]{};
    hue_channel_rgb channels[256]{};
    bool ready = false;
    uint8_t brightness = video.session.brightness, intensity = video.session.intensity;
    while (!cancelled(nullptr)) {
        Targets targets;
        {
            std::unique_lock<std::mutex> lock(video.mutex);
            auto now = Clock::now();
            if (video.done) return video.capture_result;
            if (video.last_capture == Clock::time_point{}) {
                if (now - started >= timeout) break;
                video.changed.wait_for(lock, std::chrono::milliseconds(100));
                next_tick = next_send = Clock::now();
                continue;
            }
            if (now - video.last_capture >= timeout) break;
            auto deadline = std::min(next_tick, next_send);
            if (now < deadline) {
                video.changed.wait_until(lock, deadline, [&] { return video.done || cancelled(nullptr); });
                continue;
            }
            targets = video.targets;
        }
        auto now = Clock::now();
        if (now >= next_tick) {
            float fps = 60.f;
            if (previous_tick != Clock::time_point{}) {
                double elapsed = std::chrono::duration<double>(now - previous_tick).count();
                fps = static_cast<float>(1.0 / elapsed);
            }
            previous_tick = now;
            while (video.session.live_controls && controls_available()) {
                if (!read_controls(brightness, intensity)) return 1;
                video.intensity.store(intensity);
            }
            const auto& preset = presets[intensity];
            auto alpha = c9_video_smoothing(fps, preset.lightness, preset.chroma);
#ifdef C9_WORKER_TEST
            fixture_tick(now);
#endif
            for (unsigned i = 0; i < targets.size(); ++i) {
                if (!(video.session.used & (1u << i))) continue;
                c9_rgb rgb = c9_video_moderate_apply(targets[i], &history[i], brightness / 100.f, alpha, 1);
                if (!brightness) rgb = {};
                layers[i] = {rgb.r, rgb.g, rgb.b, 1.};
            }
            next_tick += tick;
            if (next_tick <= now) next_tick = now + tick;
        }
        if (now >= next_send) {
            for (unsigned i = 0; i < video.session.count; ++i)
                channels[i] = c9_mix_video_channel(video.session.ids[i], video.session.mask[i], layers);
            int err = hue_stream_write(stream, channels, video.session.count);
            if (err) {
                std::fprintf(stderr, "Hue stream write failed (%d)\n", err);
                return 1;
            }
            if (!ready) {
                std::puts("streaming");
                std::fflush(stdout);
                ready = true;
            }
            next_send += send;
            if (next_send <= now) next_send = now + send;
        }
    }
    if (cancelled(nullptr)) return 0;
    std::fputs(video.session.piccap ? "No PicCap input on localhost port 19401\n" :
               "No video frame received for five seconds\n", stderr);
    return 1;
}
}

int measure_hdr_chart(bool direct, capture_cancelled interrupted);

int main(int argc, char **argv) {
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
#ifdef __linux__
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0 || getppid() == 1) return 1;
#elif defined(_WIN32)
    _setmode(_fileno(stdin), _O_BINARY);
#endif
    if (argc >= 2 && std::strcmp(argv[1], "--measure-hdr-chart") == 0) {
        if (argc == 2) return measure_hdr_chart(false, cancelled);
        if (argc == 3 && std::strcmp(argv[2], "direct") == 0) return measure_hdr_chart(true, cancelled);
        return 2;
    }
    bool wait_for_bridge = argc == 2 && std::strcmp(argv[1], "--wait-for-bridge") == 0;
    Session session;
    session.live_controls = argc == 2 && std::strcmp(argv[1], "--live-controls") == 0;
    if (argc != 1 && !wait_for_bridge && !session.live_controls) return 2;
    if (session.live_controls) {
        // Control updates share stdin with the initial descriptor and grant.
        // Keep prefetched control bytes out of the C library's input buffer.
        std::setvbuf(stdin, nullptr, _IONBF, 0);
        if (!read_controls(session.brightness, session.intensity)) return 2;
        wait_for_bridge = true;
    }
    if (!read_session(session)) {
        std::fputs("Invalid screen sync session descriptor\n", stderr);
        return 2;
    }
    Video video(session);
    std::thread capture([&] {
        auto capture_source = session.piccap ? c9_receive_piccap : c9_capture_video;
        int result = capture_source(&session.capture, ingest, &video, cancelled);
        if (result) std::fprintf(stderr, "Video capture failed (%d)\n", result);
        std::lock_guard<std::mutex> lock(video.mutex);
        video.capture_result = result;
        video.done = true;
        video.changed.notify_one();
    });
    int result = 1;
    if (!wait_for_bridge || await_bridge(video)) {
        hue_stream *stream = nullptr;
        int handshake = hue_stream_open(&stream, session.host, session.port, session.area, session.identity, session.key);
        std::memset(session.key, 0, sizeof(session.key));
        std::memset(session.identity, 0, sizeof(session.identity));
        if (handshake) std::fprintf(stderr, "Hue stream handshake failed (%d)\n", handshake);
        else {
            result = stream_video(video, stream);
            hue_stream_close(stream);
        }
    }
    stop(0);
    capture.join();
    return result == 0 && video.capture_result == 0 ? 0 : 1;
}
