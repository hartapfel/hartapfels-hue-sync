#include <chrono>
#include <cstdio>
#include <vector>
#include <mbedtls/net_sockets.h>
#include <mbedtls/ssl.h>
#include "hyperion_request_generated.h"
#include "hyperion_reply_generated.h"
extern "C" {
#include "capture.h"
}

namespace {
struct Socket {
    mbedtls_net_context net;
    Socket() { mbedtls_net_init(&net); }
    ~Socket() { mbedtls_net_free(&net); }
    Socket(const Socket&) = delete;
    Socket& operator=(const Socket&) = delete;
};

using Clock = std::chrono::steady_clock;
constexpr uint32_t max_payload = 32u * 1024 * 1024;

struct Connection {
    Socket peer;
    capture_cancelled cancelled;
    void *context;

    bool transfer(uint8_t *bytes, size_t size, bool send) {
        auto deadline = Clock::now() + std::chrono::seconds(3);
        while (size && !cancelled(context) && Clock::now() < deadline) {
            int ready = mbedtls_net_poll(&peer.net, send ? MBEDTLS_NET_POLL_WRITE : MBEDTLS_NET_POLL_READ, 100);
            if (ready < 0) break;
            if (!ready) continue;
            int n = send ? mbedtls_net_send(&peer.net, bytes, size) : mbedtls_net_recv(&peer.net, bytes, size);
            if (n == MBEDTLS_ERR_SSL_WANT_READ || n == MBEDTLS_ERR_SSL_WANT_WRITE) continue;
            if (n <= 0) break;
            bytes += n;
            size -= size_t(n);
        }
        return size == 0;
    }
};

bool image_view(const hyperionnet::Image *image, const capture_settings& settings, capture_bitmap& frame) {
    frame.matrix = settings.matrix;
    frame.range = settings.range;
    if (!image) return false;
    if (const auto *raw = image->data_as_RawImage()) {
        if (!raw->data() || raw->width() <= 0 || raw->width() > 16384 ||
            raw->height() <= 0 || raw->height() > 16384) return false;
        frame.width = unsigned(raw->width());
        frame.height = unsigned(raw->height());
        frame.layout = RGB24;
        frame.pixels = {raw->data()->Data(), raw->data()->size(), 3u * frame.width};
        return frame.pixels.available == frame.pixels.stride * frame.height;
    }
    const auto *nv = image->data_as_NV12Image();
    if (!nv || !nv->data_y() || !nv->data_uv() || nv->width() <= 0 || nv->width() > 16384 ||
        nv->height() <= 0 || nv->height() > 16384 || nv->stride_y() < 0 || nv->stride_uv() < 0) return false;
    frame.width = unsigned(nv->width());
    frame.height = unsigned(nv->height());
    frame.layout = NV12;
    unsigned chroma_width = 2 * ((frame.width + 1) / 2);
    frame.pixels = {nv->data_y()->Data(), nv->data_y()->size(), nv->stride_y() ? unsigned(nv->stride_y()) : frame.width};
    frame.chroma = {nv->data_uv()->Data(), nv->data_uv()->size(), nv->stride_uv() ? unsigned(nv->stride_uv()) : chroma_width};
    return frame.pixels.stride >= frame.width && frame.pixels.available / frame.pixels.stride >= frame.height &&
           frame.chroma.stride >= chroma_width && frame.chroma.available / frame.chroma.stride >= (frame.height + 1) / 2;
}

// PicCap reads a four-byte network-order length before each FlatBuffers reply.
std::vector<uint8_t> reply(int priority) {
    flatbuffers::FlatBufferBuilder builder(64);
    hyperionnet::FinishReplyBuffer(builder, hyperionnet::CreateReply(builder, 0, -1, priority));
    uint32_t size = builder.GetSize();
    std::vector<uint8_t> packet{uint8_t(size >> 24), uint8_t(size >> 16), uint8_t(size >> 8), uint8_t(size)};
    packet.insert(packet.end(), builder.GetBufferPointer(), builder.GetBufferPointer() + size);
    return packet;
}
}

int c9_receive_piccap(const capture_settings *settings, capture_callback consume,
                      void *context, capture_cancelled cancelled) {
    Socket listener;
    if (mbedtls_net_bind(&listener.net, "127.0.0.1", "19401", MBEDTLS_NET_PROTO_TCP) != 0 ||
        mbedtls_net_set_nonblock(&listener.net) != 0) {
        std::fputs("PicCap input could not open local port 19401\n", stderr);
        return -1;
    }
    std::vector<uint8_t> payload;
    auto image_reply = reply(-1);
    while (!cancelled(context)) {
        int ready = mbedtls_net_poll(&listener.net, MBEDTLS_NET_POLL_READ, 100);
        if (ready < 0) return -1;
        if (!ready) continue;
        Connection connection;
        connection.cancelled = cancelled;
        connection.context = context;
        if (mbedtls_net_accept(&listener.net, &connection.peer.net, nullptr, 0, nullptr) != 0) continue;
        if (mbedtls_net_set_nonblock(&connection.peer.net) != 0) continue;
        bool registered = false, delivered = false;
        while (!cancelled(context)) {
            uint8_t header[4];
            if (!connection.transfer(header, sizeof(header), false)) break;
            uint32_t size = uint32_t(header[0]) << 24 | uint32_t(header[1]) << 16 | uint32_t(header[2]) << 8 | header[3];
            if (!size || size > max_payload) break;
            payload.resize(size);
            if (!connection.transfer(payload.data(), size, false)) break;
            flatbuffers::Verifier verifier(payload.data(), size, 8, 16);
            if (!hyperionnet::VerifyRequestBuffer(verifier)) break;
            const auto *request = hyperionnet::GetRequest(payload.data());
            if (const auto *reg = request->command_as_Register()) {
                if (reg->priority() < 0 || reg->priority() > 255) break;
                auto ack = reply(reg->priority());
                if (!connection.transfer(ack.data(), ack.size(), true)) break;
                registered = true;
                continue;
            }
            capture_bitmap frame{};
            if (!registered || !image_view(request->command_as_Image(), *settings, frame)) break;
            int result = consume(&frame, context);
            if (result != 0) return result < 0 ? -1 : 0;
            delivered = true;
            if (!connection.transfer(image_reply.data(), image_reply.size(), true)) break;
        }
        if (delivered && !cancelled(context)) {
            std::fputs("PicCap input disconnected\n", stderr);
            return -1;
        }
    }
    return 0;
}
