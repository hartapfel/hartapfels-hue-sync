#pragma once
#include "bitmap.h"
#include "color.h"
#include "hue_stream.h"

struct screen_rect { float left, top, right, bottom; };
enum { C9_VIDEO_AREA_COUNT = 14 };
struct hue_position { float x, y, z; };
struct video_area {
    struct hue_position room_start, room_end;
    struct screen_rect screen;
};
const struct video_area *c9_video_areas(void);
// Bit i is set when the desktop preset's ith room cuboid includes this Hue
// position. Points on a shared face can belong to more than one area.
uint32_t c9_video_area_mask(double x, double y, double z);
struct c9_area_color { double red, green, blue, alpha; };
// Use a channel's cached membership mask and rendered area effects in layer
// order. Video starts from black when the desktop retain-color option is off.
struct hue_channel_rgb c9_mix_video_channel(uint8_t id, uint32_t mask,
                                           const struct c9_area_color layers[C9_VIDEO_AREA_COUNT]);
struct video_region_state {
    struct c9_rgb trimean, average;
    struct c9_lab interpolation;
};

// Map normalized coordinates within the active picture to the desktop CPU
// sampling grid. A valid region can have zero samples on a small capture.
int c9_region_grid(unsigned width, unsigned height, struct screen_rect screen,
                   unsigned max_samples, struct bitmap_region *region, unsigned *budget);

// Capture and interpolation run at independent rates. Zero-initialize one
// state per zone, ingest on capture, then tick at the interpolation rate.
int c9_video_ingest(struct video_region_state *state, const struct capture_bitmap *frame,
                    struct screen_rect screen, unsigned max_samples, unsigned *samples);
struct c9_rgb c9_video_tick(struct video_region_state *state, float gain, float fps,
                            unsigned black_threshold);
