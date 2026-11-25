#!/usr/bin/env python3
"""LinkedIn /verify tool — render a banner in its real profile context.

Simulates the desktop profile: banner on top, the circular profile photo
straddling the bottom-left, the name strip below. Also emits a red
"occlusion disc" overlay on the banner so hidden pixels are obvious.

Geometry calibrated to a real LinkedIn desktop profile
(1584x396 asset coords): photo centre ~ (200, 396), radius ~ 132.

Usage: banner_verify.py [--mobile] <banner.png> <out.png> [name]
"""
import sys
from PIL import Image, ImageDraw, ImageFont

# Asset-coord (1584x396) LinkedIn desktop profile-photo disc.
PHOTO_CX, PHOTO_CY, PHOTO_R = 200, 396, 132
PAGE_PAD, NAME_H = 40, 150


def load_font(size):
    for p in ("/System/Library/Fonts/Helvetica.ttc",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def occlusion_overlay(banner):
    """Red wash over the disc that the profile photo will hide."""
    ov = Image.new("RGBA", banner.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.ellipse([PHOTO_CX - PHOTO_R, PHOTO_CY - PHOTO_R,
               PHOTO_CX + PHOTO_R, PHOTO_CY + PHOTO_R], fill=(220, 40, 40, 90))
    return Image.alpha_composite(banner.convert("RGBA"), ov).convert("RGB")


def render(banner_path, out_path, name):
    banner = Image.open(banner_path).convert("RGB")
    w, h = banner.size
    page = Image.new("RGB", (w + 2 * PAGE_PAD, h + NAME_H + PAGE_PAD), (244, 242, 238))
    page.paste(occlusion_overlay(banner), (PAGE_PAD, PAGE_PAD))
    d = ImageDraw.Draw(page)
    # profile photo disc (white ring + grey fill) straddling banner bottom
    cx, cy, r = PAGE_PAD + PHOTO_CX, PAGE_PAD + PHOTO_CY, PHOTO_R
    d.ellipse([cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6], fill=(255, 255, 255))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(108, 122, 137))
    f = load_font(34)
    d.text((cx, cy), "PHOTO", font=load_font(26), fill=(235, 235, 235), anchor="mm")
    d.text((PAGE_PAD + 24, PAGE_PAD + h + 70), name, font=f, fill=(20, 20, 20))
    page.save(out_path)
    print("MOCK:", out_path, page.size, f"| photo disc cx={PHOTO_CX} cy={PHOTO_CY} r={PHOTO_R}")


def render_mobile(banner_path, out_path, name):
    """Mobile crop: LinkedIn hides ~15% each side; photo is larger, bottom-left."""
    banner = Image.open(banner_path).convert("RGB")
    w, h = banner.size
    cut = int(w * 0.15)
    vis = banner.crop((cut, 0, w - cut, h))
    vw, vh = vis.size
    page = Image.new("RGB", (vw + 2 * PAGE_PAD, vh + NAME_H + PAGE_PAD), (244, 242, 238))
    page.paste(vis, (PAGE_PAD, PAGE_PAD))
    d = ImageDraw.Draw(page)
    r = int(vh * 0.40)
    cx, cy = PAGE_PAD + int(vw * 0.16), PAGE_PAD + vh
    d.ellipse([cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6], fill=(255, 255, 255))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(108, 122, 137))
    d.text((cx, cy - r // 2), "PHOTO", font=load_font(24), fill=(235, 235, 235), anchor="mm")
    d.text((PAGE_PAD + 24, PAGE_PAD + vh + 70), name, font=load_font(30), fill=(20, 20, 20))
    page.save(out_path)
    print("MOBILE:", out_path, page.size, f"| visible centre {vw}px of {w}px (15% cut each side)")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--mobile"]
    name = args[2] if len(args) > 2 else "Laurie (Lourens) Scheepers"
    (render_mobile if "--mobile" in sys.argv else render)(args[0], args[1], name)
