#pragma once
#include "bitmap.h"

enum { C9_BAR_HISTORY = 201 };
struct c9_bar_history {
    uint16_t observed[C9_BAR_HISTORY], sorted[C9_BAR_HISTORY];
};
struct c9_bar_detector {
    uint32_t random[624];
    unsigned random_index, history_index;
    struct c9_bar_history horizontal, vertical;
};

// Initialize once per capture session. Each successful call observes one frame.
void c9_bars_init(struct c9_bar_detector *state);
int c9_active_picture(struct c9_bar_detector *state, const struct capture_bitmap *frame,
                      struct bitmap_region *picture);
