"""Generate the multi-resolution icon for '1. refresh.bat'.

Two drawings are used: a detailed one for 48px and up, and a bolder,
simplified one for 32px and below so the icon still reads in the taskbar
and in Explorer's small-icon view.
"""
import io, math, struct
from PIL import Image, ImageDraw, ImageFilter

S = 1024
C = S / 2
NAVY_TOP   = (24, 54, 84)
NAVY_BOT   = (8, 18, 34)
EMERALD    = (34, 197, 94)
EMERALD_LT = (74, 222, 128)
WHITE      = (248, 250, 252)


def background(corner=0.215, rim=True):
    bg = Image.new("RGB", (S, S))
    d = ImageDraw.Draw(bg)
    for y in range(S):
        t = y / (S - 1)
        d.line([(0, y), (S, y)],
               fill=tuple(round(a + (b - a) * t) for a, b in zip(NAVY_TOP, NAVY_BOT)))
    sheen = Image.new("L", (S, S), 0)
    ImageDraw.Draw(sheen).polygon([(0, 0), (S, 0), (0, S)], fill=46)
    bg = Image.composite(Image.new("RGB", (S, S), (255, 255, 255)), bg,
                         sheen.filter(ImageFilter.GaussianBlur(S * 0.22)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], int(S * corner), fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(bg, (0, 0), mask)
    if rim:
        ImageDraw.Draw(img).rounded_rectangle(
            [7, 7, S - 8, S - 8], int(S * corner) - 7, outline=(255, 255, 255, 30), width=7)
    return img


def arrowhead(d, x, y, ux, uy, length, half, fill):
    """Triangle centred on (x, y), pointing along the unit vector (ux, uy)."""
    d.polygon([(x + ux * length * 0.60, y + uy * length * 0.60),
               (x - ux * length * 0.40 - uy * half, y - uy * length * 0.40 + ux * half),
               (x - ux * length * 0.40 + uy * half, y - uy * length * 0.40 - ux * half)],
              fill=fill)


def draw_ring(img, radius, width, start, end, inner_rim):
    d = ImageDraw.Draw(img)
    box = [C - radius, C - radius, C + radius, C + radius]
    d.arc(box, start, end, fill=EMERALD + (255,), width=width)
    if inner_rim:
        inset = width * 0.30
        d.arc([box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset],
              start, end, fill=EMERALD_LT + (110,), width=int(width * 0.28))
    # arrowhead sits over the arc's end cap so no stub pokes out
    th = math.radians(end)
    px, py = C + radius * math.cos(th), C + radius * math.sin(th)
    arrowhead(d, px, py, -math.sin(th), math.cos(th), width * 2.05, width * 1.20,
              EMERALD + (255,))


def detailed():
    img = background()
    draw_ring(img, 372, 78, -48, 246, inner_rim=True)
    d = ImageDraw.Draw(img)
    pts = [(322, 648), (446, 552), (562, 614), (686, 424)]
    d.line(pts, fill=WHITE + (255,), width=60, joint="curve")
    for p in pts[:-1]:
        d.ellipse([p[0] - 30, p[1] - 30, p[0] + 30, p[1] + 30], fill=WHITE + (255,))
    (ax, ay), (bx, by) = pts[-2], pts[-1]
    ln = math.hypot(bx - ax, by - ay)
    arrowhead(d, bx, by, (bx - ax) / ln, (by - ay) / ln, 130, 86, WHITE + (255,))
    return img


def simplified():
    """Fewer, fatter shapes: survives the trip down to 16px."""
    img = background(corner=0.20, rim=False)
    draw_ring(img, 358, 116, -40, 240, inner_rim=False)
    d = ImageDraw.Draw(img)
    a, b = (352, 668), (656, 400)          # one clean up-and-right stroke
    d.line([a, b], fill=WHITE + (255,), width=104)
    d.ellipse([a[0] - 52, a[1] - 52, a[0] + 52, a[1] + 52], fill=WHITE + (255,))
    ln = math.hypot(b[0] - a[0], b[1] - a[1])
    arrowhead(d, b[0], b[1], (b[0] - a[0]) / ln, (b[1] - a[1]) / ln, 250, 152, WHITE + (255,))
    return img


def bmp_payload(im):
    """32-bit bottom-up DIB + empty AND mask, as an .ico expects."""
    w, h = im.size
    hdr = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    px = im.load()
    xor = bytearray()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))
    and_mask = bytes(((w + 31) // 32) * 4 * h)
    return hdr + bytes(xor) + and_mask


def write_ico(path, frames):
    """frames: list of (size, PIL image already at that size, use_png)."""
    entries, blobs, offset = [], [], 6 + 16 * len(frames)
    for size, im, use_png in frames:
        if use_png:
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            data = buf.getvalue()
        else:
            data = bmp_payload(im)
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                                   len(data), offset))
        blobs.append(data)
        offset += len(data)
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(frames)))
        for e in entries:
            f.write(e)
        for b in blobs:
            f.write(b)


big, small = detailed(), simplified()
big.save("scratch/refresh_icon_1024.png")
big256 = big.resize((256, 256), Image.LANCZOS)
small256 = small.resize((256, 256), Image.LANCZOS)

frames = [(256, big256, True)]
frames += [(s, big256.resize((s, s), Image.LANCZOS), False) for s in (128, 64, 48)]
frames += [(s, small256.resize((s, s), Image.LANCZOS), False) for s in (32, 24, 16)]
write_ico("refresh.ico", frames)

sheet = Image.new("RGBA", (760, 300), (28, 32, 40, 255))
x = 20
for size, im, _ in frames:
    view = im if size >= 48 else im.resize((size, size), Image.NEAREST)
    sheet.paste(view, (x, 20), view)
    x += size + 18
sheet.save("scratch/icon_sizes.png")
print("wrote refresh.ico", [f[0] for f in frames])
