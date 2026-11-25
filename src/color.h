#pragma once

struct c9_rgb { float r, g, b; };
struct c9_lab { float l, a, b; };

struct c9_lab c9_rgb_to_lab(struct c9_rgb rgb, int srgb_encoded);
struct c9_rgb c9_lab_to_rgb(struct c9_lab lab);
struct c9_lab c9_filter_lab(struct c9_lab previous, struct c9_lab next, float alpha_l, float alpha_ab);
float c9_temporal_alpha(float alpha_at_30hz, float fps);
