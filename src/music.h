#pragma once
#include "video.h"
#include <signal.h>

struct music_filter { float bass[2], low_mid[2]; };
void c9_music_zones(struct music_filter *filter, const int16_t *stereo, unsigned frames,
                    uint8_t zones[C9_ZONE_BYTES]);
int c9_capture_music(volatile sig_atomic_t *stopping);
