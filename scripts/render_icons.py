"""Render assets/icon.svg into the brand PNGs served by Home Assistant."""

import io
from pathlib import Path

import cairosvg
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "icon.svg"
TARGET = ROOT / "custom_components" / "georg" / "brand"

svg = SOURCE.read_bytes()
for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
    # The SVG is 420x375: render at full width, center on a transparent square.
    png = cairosvg.svg2png(
        bytestring=svg, output_width=size, output_height=round(size * 375 / 420)
    )
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(image, (0, (size - image.height) // 2), image)
    canvas.save(TARGET / name, optimize=True)
    print(f"{name}: {size}x{size}")
