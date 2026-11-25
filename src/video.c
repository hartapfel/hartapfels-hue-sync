#include "video.h"
#include <math.h>

// Single-display "Video Default" areas in the desktop preset's native order.
// Room Y and Z run in descending order in this representation.
static const struct video_area areas[C9_VIDEO_AREA_COUNT] = {
    {{ .33f, .1f, 1}, {1, -1, -1},       {.5f, 0, 1, 1}},
    {{-.33f, .1f, 1}, {.33f, -1, -1},    {.25f, 0, .75f, 1}},
    {{-1, .1f, 1},    {-.33f, -1, -1},   {0, 0, .5f, 1}},
    {{-.33f, 1, .33f}, {.33f, .1f, -.33f}, {.3f, .3f, .7f, .7f}},
    {{-.33f, 1, -.33f}, {.33f, .1f, -1},   {.2f, .8f, .8f, 1}},
    {{ .33f, 1, -.33f}, {1, .1f, -1},      {.7f, .67f, 1, 1}},
    {{ .33f, 1, .33f}, {1, .1f, -.33f},    {.8f, .24f, 1, .76f}},
    {{ .39f, 1, 1},   {1, .1f, .33f},     {.7f, 0, 1, .375f}},
    {{ .13f, 1, 1},   {.39f, .1f, .33f},  {.6f, 0, .9f, .2f}},
    {{-.13f, 1, 1},   {.13f, .1f, .33f},  {.35f, 0, .65f, .2f}},
    {{-.39f, 1, 1},   {-.13f, .1f, .33f}, {.1f, 0, .4f, .2f}},
    {{-1, 1, 1},      {-.39f, .1f, .33f}, {0, 0, .3f, .375f}},
    {{-1, 1, .33f},   {-.33f, .1f, -.33f}, {0, .24f, .2f, .76f}},
    {{-1, 1, -.33f},  {-.33f, .1f, -1},   {0, .67f, .29f, 1}},
};

const struct video_area *c9_video_areas(void)
{
    return areas;
}

uint32_t c9_video_area_mask(double x, double y, double z)
{
    uint32_t mask = 0;
    for (unsigned i = 0; i < C9_VIDEO_AREA_COUNT; ++i) {
        const struct video_area *a = &areas[i];
        if (x >= a->room_start.x && x <= a->room_end.x &&
            y <= a->room_start.y && y >= a->room_end.y &&
            z <= a->room_start.z && z >= a->room_end.z)
            mask |= UINT32_C(1) << i;
    }
    return mask;
}

int c9_region_grid(unsigned width, unsigned height, struct screen_rect screen,
                   unsigned max_samples, struct bitmap_region *region, unsigned *budget)
{
    if (!region || !budget || !width || !height || width > 16384 || height > 16384 ||
        !max_samples || max_samples > 65536 ||
        !(screen.left >= 0 && screen.left <= screen.right && screen.right <= 1 &&
          screen.top >= 0 && screen.top <= screen.bottom && screen.bottom <= 1))
        return 0;
    region->x = (unsigned)(width * screen.left);
    region->y = (unsigned)(height * screen.top);
    region->width = (unsigned)(width * (screen.right - screen.left));
    region->height = (unsigned)(height * (screen.bottom - screen.top));
    *budget = 0;
    if (region->width >= 3 && region->height >= 3) {
        unsigned area = region->width * region->height;
        float fraction = (float)area / (float)(width * height);
        unsigned assigned = (unsigned)(fraction * max_samples);
        unsigned cap = (region->width - 2) * (region->height - 2);
        *budget = assigned < cap ? assigned : cap;
    }
    return 1;
}

static struct c9_rgb normalize(const uint32_t rgb[3], float max_code)
{
    return (struct c9_rgb){rgb[0] / max_code, rgb[1] / max_code, rgb[2] / max_code};
}

static int changed(struct c9_rgb old, struct c9_rgb next)
{
    const float tolerance = 2.f / 255.f;  // Video Moderate ColorsCompareTreshold
    return fabsf(old.r - next.r) > tolerance || fabsf(old.g - next.g) > tolerance ||
           fabsf(old.b - next.b) > tolerance;
}

int c9_video_ingest(struct video_region_state *state, const struct capture_bitmap *frame,
                    struct screen_rect screen, unsigned max_samples, unsigned *samples)
{
    struct bitmap_region region;
    unsigned budget;
    if (!frame || !state || !samples ||
        !c9_region_grid(frame->width, frame->height, screen, max_samples, &region, &budget))
        return 0;
    struct bitmap_result result = {0};
    if (budget && !analyze_region(frame, region, budget, &result)) return 0;
    *samples = result.samples;
    float max_code = frame->layout == RGB10A2 ? 1023.f : 255.f;
    struct c9_rgb trimean = normalize(result.trimean, max_code);
    struct c9_rgb average = normalize(result.average, max_code);
    // Desktop retains both previous colors when either comparison is close.
    if (changed(state->trimean, trimean) && changed(state->average, average)) {
        state->trimean = trimean;
        state->average = average;
    }
    return 1;
}

struct c9_rgb c9_video_tick(struct video_region_state *state, float gain, float fps,
                            unsigned black_threshold)
{
    return c9_video_moderate_tick(state->trimean, &state->interpolation, gain, fps, black_threshold);
}
