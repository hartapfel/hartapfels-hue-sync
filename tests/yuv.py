"""Reference decoder for the capture matrices supported by the ARM worker."""
import math


def decode(y, u, v, matrix, full):
    kr, kb = ((.299, .114), (.2126, .0722), (.2627, .0593))[matrix]
    y = y if full else (y - 16) * 255 / 219
    u = (u - 128) * (1 if full else 255 / 224)
    v = (v - 128) * (1 if full else 255 / 224)
    red = y + 2 * (1 - kr) * v
    blue = y + 2 * (1 - kb) * u
    green = (y - kr * red - kb * blue) / (1 - kr - kb)
    return tuple(max(0, min(255, math.floor(x + .5))) for x in (red, green, blue))
