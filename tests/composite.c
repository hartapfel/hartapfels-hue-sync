#include "bitmap.h"
#include <assert.h>

static void expect(const struct capture_bitmap *frame, unsigned x, unsigned y,
                   unsigned r, unsigned g, unsigned b)
{
    struct bitmap_result sample;
    struct bitmap_region region = {x, y, 1, 1};
    assert(analyze_region(frame, region, 1, &sample));
    assert(sample.average[0] == r && sample.average[1] == g && sample.average[2] == b);
}

int main(void)
{
    const uint8_t video[] = {100, 80, 60, 170, 150, 130};
    struct capture_bitmap base = {.width=2, .height=1, .layout=RGB24, .pixels={video,sizeof(video),6}};
    const uint8_t gm[] = {
        0,0,0,0, 8,16,32,128, 0,0,0,0, 30,20,10,255, 0,0,0,0,
        0,0,0,0, 0,0,0,0,     0,0,0,0, 3,2,1,255
    };
    struct capture_bitmap display = {.width=4, .height=2, .layout=COMPOSITED_BGRA,
        .pixels={gm,sizeof(gm),20}, .background=&base};
    expect(&display, 0, 0, 100, 80, 60);
    expect(&display, 1, 0, 82, 56, 38);
    expect(&display, 2, 0, 170, 150, 130);
    expect(&display, 3, 0, 10, 20, 30);
    expect(&display, 3, 1, 1, 2, 3);
    display.background = NULL;
    expect(&display, 1, 0, 32, 16, 8);
    expect(&display, 0, 0, 0, 0, 0);
    return 0;
}
