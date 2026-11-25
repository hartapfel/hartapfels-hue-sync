#include "hue_stream.h"
#include <mbedtls/ctr_drbg.h>
#include <mbedtls/entropy.h>
#include <mbedtls/net_sockets.h>
#include <mbedtls/ssl.h>
#include <mbedtls/timing.h>
#include <psa/crypto.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct hue_stream {
    mbedtls_ssl_context ssl;
    mbedtls_ssl_config config;
    mbedtls_net_context socket;
    mbedtls_entropy_context entropy;
    mbedtls_ctr_drbg_context random;
    mbedtls_timing_delay_context timer;
    char area[36];
    uint8_t sequence;
    int connected;
};

static int valid_uuid(const char *id)
{
    if (!id || strlen(id) != 36) return 0;
    for (size_t i = 0; i < 36; ++i) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (id[i] != '-') return 0;
        } else if (!strchr("0123456789abcdefABCDEF", id[i])) {
            return 0;
        }
    }
    return 1;
}

static int handshake(struct hue_stream *s, const char *host, uint16_t port,
                     const char *identity, const uint8_t key[16])
{
    static const int suites[] = {MBEDTLS_TLS_PSK_WITH_AES_128_GCM_SHA256, 0};
    static const unsigned char label[] = "c9-screen-sync";
    char service[6];
    snprintf(service, sizeof(service), "%u", (unsigned)port);
    if (psa_crypto_init() != PSA_SUCCESS) return MBEDTLS_ERR_SSL_INTERNAL_ERROR;
    int ret = mbedtls_ctr_drbg_seed(&s->random, mbedtls_entropy_func, &s->entropy, label, sizeof(label) - 1);
    if (ret) return ret;
    ret = mbedtls_ssl_config_defaults(&s->config, MBEDTLS_SSL_IS_CLIENT,
                                      MBEDTLS_SSL_TRANSPORT_DATAGRAM, MBEDTLS_SSL_PRESET_DEFAULT);
    if (ret) return ret;
    mbedtls_ssl_conf_min_tls_version(&s->config, MBEDTLS_SSL_VERSION_TLS1_2);
    mbedtls_ssl_conf_max_tls_version(&s->config, MBEDTLS_SSL_VERSION_TLS1_2);
    mbedtls_ssl_conf_ciphersuites(&s->config, suites);
    mbedtls_ssl_conf_rng(&s->config, mbedtls_ctr_drbg_random, &s->random);
    mbedtls_ssl_conf_handshake_timeout(&s->config, 500, 4000);
    ret = mbedtls_ssl_conf_psk(&s->config, key, 16, (const unsigned char *)identity, strlen(identity));
    if (ret) return ret;
    ret = mbedtls_ssl_setup(&s->ssl, &s->config);
    if (ret) return ret;
    ret = mbedtls_net_connect(&s->socket, host, service, MBEDTLS_NET_PROTO_UDP);
    if (ret) return ret;
    mbedtls_ssl_set_bio(&s->ssl, &s->socket, mbedtls_net_send, mbedtls_net_recv, mbedtls_net_recv_timeout);
    mbedtls_ssl_set_timer_cb(&s->ssl, &s->timer, mbedtls_timing_set_delay, mbedtls_timing_get_delay);
    do {
        ret = mbedtls_ssl_handshake(&s->ssl);
    } while (ret == MBEDTLS_ERR_SSL_WANT_READ || ret == MBEDTLS_ERR_SSL_WANT_WRITE);
    return ret;
}

int hue_stream_open(struct hue_stream **out, const char *host, uint16_t port,
                    const char *area_uuid, const char *identity, const uint8_t key[16])
{
    if (!out) return MBEDTLS_ERR_SSL_BAD_INPUT_DATA;
    *out = NULL;
    if (!host || !*host || !port || !valid_uuid(area_uuid) || !identity || !*identity ||
        strlen(identity) > 128 || !key) return MBEDTLS_ERR_SSL_BAD_INPUT_DATA;
    struct hue_stream *s = calloc(1, sizeof(*s));
    if (!s) return MBEDTLS_ERR_SSL_ALLOC_FAILED;
    mbedtls_ssl_init(&s->ssl);
    mbedtls_ssl_config_init(&s->config);
    mbedtls_net_init(&s->socket);
    mbedtls_entropy_init(&s->entropy);
    mbedtls_ctr_drbg_init(&s->random);
    int ret = handshake(s, host, port, identity, key);
    if (ret) {
        hue_stream_close(s);
        return ret;
    }
    memcpy(s->area, area_uuid, sizeof(s->area));
    s->connected = 1;
    *out = s;
    return 0;
}

static void pack_component(uint8_t *dst, double normalized)
{
    // The desktop serializer clamps in double precision and truncates toward zero.
    uint16_t value = !(normalized > 0.) ? 0 :
                     normalized >= 1. ? UINT16_MAX : (uint16_t)(normalized * 65535.);
    dst[0] = (uint8_t)(value >> 8);
    dst[1] = (uint8_t)value;
}

int hue_stream_write(struct hue_stream *s, const struct hue_channel_rgb *channels, size_t count)
{
    if (!s || !s->connected || !channels || !count || count > 256)
        return MBEDTLS_ERR_SSL_BAD_INPUT_DATA;
    uint8_t packet[52 + 7 * 256] = "HueStream";
    uint8_t seen[256] = {0};
    packet[9] = 2;
    packet[11] = s->sequence;
    memcpy(packet + 16, s->area, sizeof(s->area));
    for (size_t i = 0; i < count; ++i) {
        const struct hue_channel_rgb *c = &channels[i];
        if (seen[c->id]) return MBEDTLS_ERR_SSL_BAD_INPUT_DATA;
        seen[c->id] = 1;
        uint8_t *p = packet + 52 + 7 * i;
        p[0] = c->id;
        pack_component(p + 1, c->red);
        pack_component(p + 3, c->green);
        pack_component(p + 5, c->blue);
    }
    size_t length = 52 + 7 * count;
    int ret;
    do {
        ret = mbedtls_ssl_write(&s->ssl, packet, length);
    } while (ret == MBEDTLS_ERR_SSL_WANT_READ || ret == MBEDTLS_ERR_SSL_WANT_WRITE);
    if (ret != (int)length) {
        s->connected = 0;
        return ret < 0 ? ret : MBEDTLS_ERR_SSL_INTERNAL_ERROR;
    }
    ++s->sequence;
    return 0;
}

void hue_stream_close(struct hue_stream *s)
{
    if (!s) return;
    if (s->connected) mbedtls_ssl_close_notify(&s->ssl);
    mbedtls_net_free(&s->socket);
    mbedtls_ssl_free(&s->ssl);
    mbedtls_ssl_config_free(&s->config);
    mbedtls_ctr_drbg_free(&s->random);
    mbedtls_entropy_free(&s->entropy);
    free(s);
}
