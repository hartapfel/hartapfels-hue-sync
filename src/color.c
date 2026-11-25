#include "color.h"
#include <math.h>

struct c9_smoothing c9_video_moderate_smoothing(float fps)
{
    return c9_video_smoothing(fps, .025f, .30f);
}

float c9_temporal_alpha(float alpha_at_30hz, float fps)
{
    return fps > 0.f ? 1.f - powf(1.f - alpha_at_30hz, 30.f / fps) : 0.f;
}

float c9_streaming_brightness_gain(unsigned percent)
{
    // Desktop's streaming slider puts unity at its midpoint and 4x at its maximum.
    double position = (percent > 100 ? 100 : percent) / 100.0;
    return (float)(position < 0.5 ? 2.0 * position : 1.0 + 6.0 * (position - 0.5));
}

struct c9_rgb c9_video_moderate_apply(struct c9_rgb region, struct c9_lab *history,
                                      float gain, struct c9_smoothing alpha, unsigned black_threshold)
{
    float peak = fmaxf(region.r, fmaxf(region.g, region.b));
    if (peak > 0.f) {
        float scale = fminf(gain, 1.f / peak);
        region.r *= scale;
        region.g *= scale;
        region.b *= scale;
    }
    *history = c9_filter_lab(*history, c9_rgb_to_lab(region, 1),
                             alpha.lightness, alpha.chroma);
    struct c9_rgb output = c9_lab_to_rgb(*history);
    // The mode-2 gamut function returns immediately for these clamped RGB values.
    if (fmaxf(output.r, fmaxf(output.g, output.b)) <= black_threshold / 255.0)
        return (struct c9_rgb){0.f, 0.f, 0.f};
    return output;
}

struct c9_rgb c9_video_moderate_tick(struct c9_rgb region, struct c9_lab *history,
                                     float gain, float fps, unsigned black_threshold)
{
    return c9_video_moderate_apply(region, history, gain, c9_video_moderate_smoothing(fps), black_threshold);
}
