#include "color.h"
#include <math.h>

static float linearize(float value)
{
    if (value <= 0.f) return 0.f;
    if (value >= 1.f) return 1.f;
    return value <= 0.04045f ? value / 12.92f : powf((value + 0.055f) / 1.055f, 2.4f);
}

static float encode(float value)
{
    if (value <= 0.f) return 0.f;
    if (value >= 1.f) return 1.f;
    return value <= 0.0031308f ? value * 12.92f : 1.055f * powf(value, 1.f / 2.4f) - 0.055f;
}

static float lab_component(float ratio)
{
    return ratio > 0.008856f ? powf(ratio, 1.f / 3.f) : 7.787f * ratio + 16.f / 116.f;
}

struct c9_lab c9_rgb_to_lab(struct c9_rgb rgb, int srgb_encoded)
{
    float r = 100.f * (srgb_encoded ? linearize(rgb.r) : rgb.r);
    float g = 100.f * (srgb_encoded ? linearize(rgb.g) : rgb.g);
    float b = 100.f * (srgb_encoded ? linearize(rgb.b) : rgb.b);
    float x = lab_component((g * 0.3576f + r * 0.4124f + b * 0.1805f) / 95.047f);
    float y = lab_component((g * 0.7152f + r * 0.2126f + b * 0.0722f) / 100.f);
    float z = lab_component((g * 0.1192f + r * 0.0193f + b * 0.9505f) / 108.883f);
    return (struct c9_lab){116.f * y - 16.f, 500.f * (x - y), 200.f * (y - z)};
}

static float inverse_component(float value)
{
    return value > 0.2068965584f ? value * (value * value) : value * 0.1284185499f - 0.0177129041f;
}

struct c9_rgb c9_lab_to_rgb(struct c9_lab lab)
{
    float fy = lab.l * (1.f / 116.f) + 16.f / 116.f;
    float x = inverse_component(lab.a * 0.002f + fy);
    float y = lab.l > 8.f ? fy * (fy * fy) : lab.l * 0.0011070565f;
    float z = inverse_component(fy - lab.b * 0.005f);
    return (struct c9_rgb){
        encode(x * 3.080342054f - y * 1.537398934f - z * 0.542943001f),
        encode(y * 1.875930071f - x * 0.921178401f + z * 0.045248415f),
        encode(x * 0.052881293f - y * 0.204011202f + z * 1.151129961f)
    };
}

float c9_temporal_alpha(float alpha_at_30hz, float fps)
{
    return fps > 0.f ? 1.f - powf(1.f - alpha_at_30hz, 30.f / fps) : 0.f;
}

struct c9_lab c9_filter_lab(struct c9_lab previous, struct c9_lab next, float alpha_l, float alpha_ab)
{
    return (struct c9_lab){
        (1.f - alpha_l) * previous.l + alpha_l * next.l,
        (1.f - alpha_ab) * previous.a + alpha_ab * next.a,
        (1.f - alpha_ab) * previous.b + alpha_ab * next.b
    };
}

struct c9_smoothing c9_video_moderate_smoothing(float fps)
{
    return (struct c9_smoothing){c9_temporal_alpha(0.025f, fps), c9_temporal_alpha(0.30f, fps)};
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
