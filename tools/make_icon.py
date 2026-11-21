"""Draw the Cook Manager icon: a flame with a thermometer in it."""
import pathlib
import sys

from PIL import Image, ImageDraw

OUT = pathlib.Path(sys.argv[1])
S = 2048  # supersampled canvas
U = S / 1000.0


def bezier(p0, p1, p2, p3, n=60):
    pts = []
    for i in range(n + 1):
        t = i / n
        a = (1 - t) ** 3
        b = 3 * (1 - t) ** 2 * t
        c = 3 * (1 - t) * t**2
        d = t**3
        pts.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return pts


def flame(scale=1.0, cx=500, base=950):
    segs = [
        ((500, 950), (290, 950), (140, 790), (165, 590)),
        ((165, 590), (170, 480), (250, 410), (285, 290)),
        ((285, 290), (320, 360), (360, 400), (405, 370)),
        ((405, 370), (470, 300), (440, 150), (500, 40)),
        ((500, 40), (640, 190), (800, 360), (820, 560)),
        ((820, 560), (870, 780), (710, 950), (500, 950)),
    ]
    pts = []
    for seg in segs:
        pts.extend(bezier(*seg))
    return [((cx + (x - 500) * scale) * U, (base + (y - 950) * scale) * U) for x, y in pts]


def lerp(c1, c2, t):
    return tuple(round(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


# vertical gradient, amber at the tip to red at the base
gradient = Image.new("RGB", (S, S))
gd = ImageDraw.Draw(gradient)
top, bottom = (255, 167, 38), (229, 57, 53)
for y in range(S):
    gd.line([(0, y), (S, y)], fill=lerp(top, bottom, min(max((y / S - 0.05) / 0.9, 0), 1)))

mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).polygon(flame(), fill=255)
icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
icon.paste(gradient, (0, 0), mask)

d = ImageDraw.Draw(icon)
white = (255, 255, 255, 255)
red = (198, 40, 40, 255)
# thermometer: stem + bulb
cx, stem_top, bulb_y, stem_w, bulb_r = 500, 400, 770, 46, 92
d.rounded_rectangle(
    [(cx - stem_w) * U, stem_top * U, (cx + stem_w) * U, bulb_y * U], radius=stem_w * U, fill=white
)
d.ellipse([(cx - bulb_r) * U, (bulb_y - bulb_r) * U, (cx + bulb_r) * U, (bulb_y + bulb_r) * U], fill=white)
# mercury
d.rounded_rectangle([(cx - 18) * U, 540 * U, (cx + 18) * U, bulb_y * U], radius=18 * U, fill=red)
d.ellipse([(cx - 56) * U, (bulb_y - 56) * U, (cx + 56) * U, (bulb_y + 56) * U], fill=red)
# scale ticks
for y in (470, 530, 590):
    d.rounded_rectangle([(cx + 62) * U, (y - 9) * U, (cx + 120) * U, (y + 9) * U], radius=9 * U, fill=white)

OUT.mkdir(parents=True, exist_ok=True)
for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
    icon.resize((size, size), Image.LANCZOS).save(OUT / name, optimize=True)
print("written", sorted(p.name for p in OUT.iterdir()))
