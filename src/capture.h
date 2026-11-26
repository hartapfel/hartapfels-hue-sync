#pragma once
#include "bitmap.h"
#include <signal.h>

struct capture_settings {
    unsigned width, height, fps;
    enum yuv_matrix matrix;
    enum yuv_range range;
};

// The callback borrows the frame until it returns. Zero continues, a positive
// value requests an orderly stop, and a negative value reports a consumer error.
typedef int (*capture_callback)(const struct capture_bitmap *, void *);
typedef int (*capture_cancelled)(void *);
int c9_capture_video(const struct capture_settings *settings, capture_callback consume,
                     void *context, capture_cancelled cancelled);
int c9_capture_display(const struct capture_settings *settings, capture_callback consume,
                       void *context, capture_cancelled cancelled);
int c9_receive_piccap(const struct capture_settings *settings, capture_callback consume,
                      void *context, capture_cancelled cancelled);
