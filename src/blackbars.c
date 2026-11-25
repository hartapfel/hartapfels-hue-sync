#include "blackbars.h"
#include <string.h>

void c9_bars_init(struct c9_bar_detector *state)
{
    memset(state, 0, sizeof(*state));
    // Desktop uses MT19937 with seed zero and a uniform integer distribution.
    for (unsigned i = 1; i < 624; ++i) {
        uint32_t prev = state->random[i - 1];
        state->random[i] = UINT32_C(1812433253) * (prev ^ (prev >> 30)) + i;
    }
}

static uint32_t next_random(struct c9_bar_detector *state)
{
    unsigned i = state->random_index, next = i == 623 ? 0 : i + 1;
    uint32_t combined = (state->random[i] & UINT32_C(0x80000000)) |
                        (state->random[next] & UINT32_C(0x7fffffff));
    uint32_t y = state->random[(i + 397) % 624] ^ (combined >> 1);
    if (combined & 1) y ^= UINT32_C(0x9908b0df);
    state->random[i] = y;
    state->random_index = next;
    y ^= y >> 11;
    y ^= (y << 7) & UINT32_C(0x9d2c5680);
    y ^= (y << 15) & UINT32_C(0xefc60000);
    return y ^ (y >> 18);
}

static unsigned probe_position(struct c9_bar_detector *state, unsigned length)
{
    uint32_t first = (uint32_t)(.4f * length);
    uint32_t count = (uint32_t)(.6f * length) - first + 1;
    uint64_t product = (uint64_t)next_random(state) * count;
    if ((uint32_t)product < count) {
        uint32_t reject = (0u - count) % count;
        while ((uint32_t)product < reject) product = (uint64_t)next_random(state) * count;
    }
    return first + (unsigned)(product >> 32);
}

static unsigned observe(struct c9_bar_history *history, unsigned index, uint16_t value)
{
    uint16_t old = history->observed[index];
    if (old != value) {
        // Replace one observation in the sorted multiset. A stable scene
        // needs no sorting and finding the median is always a single load.
        unsigned i = 0;
        while (history->sorted[i] != old) ++i;
        while (i + 1 < C9_BAR_HISTORY && history->sorted[i + 1] < value) {
            history->sorted[i] = history->sorted[i + 1];
            ++i;
        }
        while (i && history->sorted[i - 1] > value) {
            history->sorted[i] = history->sorted[i - 1];
            --i;
        }
        history->sorted[i] = value;
        history->observed[index] = value;
    }
    return history->sorted[C9_BAR_HISTORY / 2];
}

int c9_active_picture(struct c9_bar_detector *state, const struct capture_bitmap *frame,
                      struct bitmap_region *picture)
{
    if (!state || !frame || !picture || !frame->width || !frame->height ||
        frame->width > 16384 || frame->height > 16384) return 0;
    unsigned col = probe_position(state, frame->width);
    unsigned row = probe_position(state, frame->height);
    unsigned left, top;
    if (!c9_bar_probe(frame, col, row, &left, &top)) return 0;
    left = observe(&state->horizontal, state->history_index, (uint16_t)left);
    top = observe(&state->vertical, state->history_index, (uint16_t)top);
    state->history_index = (state->history_index + 1) % C9_BAR_HISTORY;
    *picture = (struct bitmap_region){0, 0, frame->width, frame->height};
    if (left * 2 < frame->width && top * 2 < frame->height)
        *picture = (struct bitmap_region){left, top, frame->width - 2 * left, frame->height - 2 * top};
    return 1;
}
