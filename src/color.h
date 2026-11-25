#pragma once

struct c9_rgb { float r, g, b; };
struct c9_lab { float l, a, b; };
struct c9_smoothing { float lightness, chroma; };

struct c9_lab c9_rgb_to_lab(struct c9_rgb rgb, int srgb_encoded);
struct c9_rgb c9_lab_to_rgb(struct c9_lab lab);
struct c9_lab c9_filter_lab(struct c9_lab previous, struct c9_lab next, float alpha_l, float alpha_ab);
float c9_temporal_alpha(float alpha_at_30hz, float fps);

// One interpolation tick for one Video/Moderate area, audio and background
// lighting disabled, on the standard sRGB input and gamut-correction-2 path.
struct c9_smoothing c9_video_moderate_smoothing(float fps);
struct c9_rgb c9_video_moderate_apply(struct c9_rgb region, struct c9_lab *history,
                                      float gain, struct c9_smoothing alpha, unsigned black_threshold);
struct c9_rgb c9_video_moderate_tick(struct c9_rgb region, struct c9_lab *history,
                                     float gain, float fps, unsigned black_threshold);
