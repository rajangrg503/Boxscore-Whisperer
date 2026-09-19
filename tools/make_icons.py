"""Render the wordmark's basketball into the icon sizes iOS wants.

WHY THIS EXISTS
Adding the site to an iPhone's home screen produced a black square with a
grey "B" in it -- iOS inventing a placeholder, because the page offers
only a `shortcut icon` (which it ignores for the home screen) and no
`apple-touch-icon`. The mark itself already exists as inline SVG in
app.py's wordmark; it just has never been available as a PNG at the size
Apple asks for.

Rather than commit a PNG whose origin nobody can reconstruct, this draws
it from the same coordinates the SVG uses, so the icon and the wordmark
cannot drift apart. It needs only Pillow, and it writes into assets/.

    python3 tools/make_icons.py

Output goes to static/, not assets/, because that is the one directory
Streamlit will serve over HTTP (with server.enableStaticServing), and an
apple-touch-icon has to be fetchable by URL -- a data: URI does not work
for it. assets/favicon.png stays where it is: set_page_config reads that
one off disk, never over the wire.

Two things differ deliberately from the inline SVG:
  * the background is filled, not transparent -- iOS composites home
    screen icons onto white otherwise, and a dark mark on white is not
    the brand
  * the artwork is inset, because iOS crops every icon to a rounded
    square and edge-to-edge artwork loses its corners
"""

import os

from PIL import Image, ImageDraw

# Matches the SVG in app.py (viewBox 0 0 64 64) and the palette in
# .streamlit/config.toml.
VIEWBOX = 64.0
BG = (10, 13, 18, 255)        # --bw-bg
GREEN = (34, 197, 94, 255)    # --bw-accent
BALL = (255, 140, 66, 255)    # #ff8c42
SEAM = (10, 13, 18, 255)

# iOS uses 180x180 for the home screen; 192 and 512 are the sizes a web
# app manifest is conventionally given.
SIZES = {"apple-touch-icon.png": 180, "icon-192.png": 192, "icon-512.png": 512}

# How much of the square the artwork occupies, leaving room for the mask.
INSET = 0.76
SUPERSAMPLE = 4


def _quad(p0, p1, p2, steps=600):
    """Points along a quadratic bezier -- the SVG's Q commands, which
    Pillow has no primitive for."""
    (x0, y0), (x1, y1), (x2, y2) = p0, p1, p2
    points = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        points.append((u * u * x0 + 2 * u * t * x1 + t * t * x2,
                       u * u * y0 + 2 * u * t * y1 + t * t * y2))
    return points


def _stroke(draw, points, color, width):
    """A round-capped stroke along a path.

    Pillow's thick `line` draws each segment separately and leaves a
    ridge at every joint, which on a curve sampled finely enough to look
    smooth turns the stroke into a comb. Stamping a disc at closely
    spaced points along the path gives the same shape with clean edges,
    which is what a real stroke is anyway.
    """
    r = width / 2.0
    for x, y in points:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _blend(color, opacity):
    """Flatten a partly transparent stroke against the background, since
    the icon has no alpha to spare."""
    return tuple(round(c * opacity + b * (1 - opacity))
                 for c, b in zip(color[:3], BG[:3])) + (255,)


def render(size):
    """One icon, drawn at SUPERSAMPLE scale and reduced for smooth edges."""
    big = size * SUPERSAMPLE
    img = Image.new("RGBA", (big, big), BG)
    draw = ImageDraw.Draw(img)

    scale = big * INSET / VIEWBOX
    offset = (big - VIEWBOX * scale) / 2.0

    def pt(x, y):
        return (offset + x * scale, offset + y * scale)

    def width(w):
        return max(1, round(w * scale))

    # The two sound arcs, quieter one first (the SVG gives it 0.5 alpha).
    _stroke(draw, _quad(pt(11, 25), pt(7.5, 32), pt(11, 39)),
            _blend(GREEN, 0.5), width(3.2))
    _stroke(draw, _quad(pt(16.5, 21), pt(11, 32), pt(16.5, 43)),
            GREEN, width(3.2))

    # The ball.
    cx, cy = pt(38, 32)
    r = 16 * scale
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BALL)

    seam = width(1.8)
    draw.line([pt(22, 32), pt(54, 32)], fill=SEAM, width=seam)
    draw.line([pt(38, 16), pt(38, 48)], fill=SEAM, width=seam)
    _stroke(draw, _quad(pt(26.5, 20.5), pt(33, 32), pt(26.5, 43.5)), SEAM, seam)
    _stroke(draw, _quad(pt(49.5, 20.5), pt(43, 32), pt(49.5, 43.5)), SEAM, seam)

    return img.resize((size, size), Image.LANCZOS).convert("RGB")


def main():
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "static")
    os.makedirs(out_dir, exist_ok=True)
    for name, size in SIZES.items():
        path = os.path.join(out_dir, name)
        render(size).save(path, "PNG", optimize=True)
        print(f"{path}  {size}x{size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
