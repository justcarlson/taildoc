#!/usr/bin/env python3
"""Build the embedded preview atlas. Requires Pillow 12.3.0, not used at runtime.

Input: DejaVuSansMono-Bold.ttf from DejaVu Fonts 2.37 (see THIRD_PARTY_NOTICES).
Output: Python constant on stdout. The server remains a single stdlib-only file.
"""
import base64
import sys
import textwrap
import zlib

from PIL import Image, ImageDraw, ImageFont

font = ImageFont.truetype(sys.argv[1], 64)
chars = "".join(chr(i) for i in range(32, 127)) + "".join(chr(i) for i in range(160, 256))
masks = bytearray()
for char in chars:
    image = Image.new("L", (40, 80))
    ImageDraw.Draw(image).text((0, 0), char, font=font, fill=255)
    masks.extend(image.tobytes())
encoded = base64.b85encode(zlib.compress(bytes(masks), 9)).decode()
print("# Generated with scripts/build_preview_font.py; DejaVu license in THIRD_PARTY_NOTICES.")
print("PREVIEW_FONT = zlib.decompress(base64.b85decode(")
for line in textwrap.wrap(encoded, 88, break_on_hyphens=False, replace_whitespace=False):
    print("    " + repr(line))
print("))")
