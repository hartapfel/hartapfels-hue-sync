#pragma once
#include <stddef.h>
#include <stdint.h>

struct hue_channel_rgb {
    uint8_t id;
    double red, green, blue;
};

struct hue_stream;

// Open after activating the selected entertainment_configuration via HTTPS.
// identity is the paired application key, and key is the 16-byte client key.
// The caller owns the returned stream and uses it on one thread at a time.
int hue_stream_open(struct hue_stream **out, const char *host, uint16_t port,
                    const char *area_uuid, const char *identity, const uint8_t key[16]);
// Components are normalized, post-mixer RGB values as received by the serializer.
int hue_stream_write(struct hue_stream *stream, const struct hue_channel_rgb *channels,
                     size_t count);
void hue_stream_close(struct hue_stream *stream);
