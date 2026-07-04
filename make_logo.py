#!/usr/bin/env python3
"""Create a descriptive AI Controller logo with Pillow."""
from PIL import Image, ImageDraw, ImageFont

W, H = 1024, 1024
BG = "#151519"
ORANGE = "#FF6A00"
GREY = "#2a2a32"
LIGHT = "#e8e8e8"
DARK_SCREEN = "#0d0d12"

img = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(img)

# Load fonts
font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 96)
font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
font_tiny = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)


def rounded_rect(draw, xy, radius, fill, outline=None, width=1):
    """Draw a rounded rectangle on xy=(x1,y1,x2,y2)."""
    x1, y1, x2, y2 = xy
    r = min(radius, (x2 - x1) // 2, (y2 - y1) // 2)
    draw.rectangle([x1 + r, y1, x2 - r, y2], fill=fill)
    draw.rectangle([x1, y1 + r, x2, y2 - r], fill=fill)
    draw.ellipse([x1, y1, x1 + 2 * r, y1 + 2 * r], fill=fill)
    draw.ellipse([x2 - 2 * r, y1, x2, y1 + 2 * r], fill=fill)
    draw.ellipse([x1, y2 - 2 * r, x1 + 2 * r, y2], fill=fill)
    draw.ellipse([x2 - 2 * r, y2 - 2 * r, x2, y2], fill=fill)
    if outline:
        # Draw outline arcs and lines
        draw.arc([x1, y1, x1 + 2 * r, y1 + 2 * r], 180, 270, fill=outline, width=width)
        draw.arc([x2 - 2 * r, y1, x2, y1 + 2 * r], 270, 360, fill=outline, width=width)
        draw.arc([x1, y2 - 2 * r, x1 + 2 * r, y2], 90, 180, fill=outline, width=width)
        draw.arc([x2 - 2 * r, y2 - 2 * r, x2, y2], 0, 90, fill=outline, width=width)
        draw.line([(x1 + r, y1), (x2 - r, y1)], fill=outline, width=width)
        draw.line([(x1 + r, y2), (x2 - r, y2)], fill=outline, width=width)
        draw.line([(x1, y1 + r), (x1, y2 - r)], fill=outline, width=width)
        draw.line([(x2, y1 + r), (x2, y2 - r)], fill=outline, width=width)


def circle(draw, center, radius, fill, outline=None, width=1):
    x, y = center
    draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=fill, outline=outline, width=width)


# --- Monitor in background ---
mon_x1, mon_y1 = 220, 140
mon_x2, mon_y2 = 804, 540
rounded_rect(draw, (mon_x1, mon_y1, mon_x2, mon_y2), 24, GREY, outline="#3a3a44", width=4)
# Screen
rounded_rect(draw, (mon_x1 + 20, mon_y1 + 60, mon_x2 - 20, mon_y2 - 30), 12, DARK_SCREEN)
# Tabs
tab_w = (mon_x2 - mon_x1 - 80) // 4
for i in range(4):
    tx1 = mon_x1 + 30 + i * tab_w
    ty1 = mon_y1 + 25
    rounded_rect(draw, (tx1, ty1, tx1 + tab_w - 10, ty1 + 30), 8, ORANGE if i == 0 else "#3a3a44")
    draw.text((tx1 + 18, ty1 + 4), f"{i+1}", font=font_small, fill=LIGHT)
# Stand
draw.rectangle([440, mon_y2, 584, mon_y2 + 50], fill="#3a3a44")
draw.ellipse([400, mon_y2 + 40, 624, mon_y2 + 80], fill="#2a2a32")

# --- Controller body ---
cx, cy = 512, 760
ctrl_w, ctrl_h = 560, 280
rounded_rect(draw, (cx - ctrl_w // 2, cy - ctrl_h // 2, cx + ctrl_w // 2, cy + ctrl_h // 2), 80, "#3a3a44", outline="#FF6A00", width=6)

# Keyboard overlay (grid across controller body)
grid_x1, grid_y1 = cx - 180, cy - 80
grid_x2, grid_y2 = cx + 180, cy + 60
key_w, key_h = 24, 18
for row in range(8):
    for col in range(15):
        kx = grid_x1 + col * (key_w + 4)
        ky = grid_y1 + row * (key_h + 4)
        if kx + key_w > grid_x2 or ky + key_h > grid_y2:
            continue
        rounded_rect(draw, (kx, ky, kx + key_w, ky + key_h), 3, "#1a1a1e" if (row + col) % 3 else ORANGE)

# Left stick
circle(draw, (cx - 170, cy - 20), 38, "#1a1a1e", outline=LIGHT, width=4)
circle(draw, (cx - 170, cy - 20), 18, ORANGE)
# Right stick
circle(draw, (cx + 170, cy - 20), 38, "#1a1a1e", outline=LIGHT, width=4)
circle(draw, (cx + 170, cy - 20), 18, ORANGE)

# D-pad left
draw.rectangle([cx - 260, cy - 35, cx - 210, cy + 5], fill="#1a1a1e", outline=LIGHT, width=3)
draw.rectangle([cx - 247, cy - 48, cx - 223, cy + 18], fill="#1a1a1e", outline=LIGHT, width=3)

# Action buttons right
circle(draw, (cx + 250, cy - 50), 14, ORANGE)
circle(draw, (cx + 280, cy - 20), 14, "#00cc66")
circle(draw, (cx + 250, cy + 10), 14, "#3399ff")
circle(draw, (cx + 220, cy - 20), 14, "#ff3333")

# Microphone at top center
circle(draw, (cx, cy - 130), 20, "#1a1a1e", outline=ORANGE, width=4)
draw.line([(cx, cy - 150), (cx, cy - 130)], fill=ORANGE, width=4)
draw.arc([cx - 15, cy - 125, cx + 15, cy - 95], 0, 180, fill=ORANGE, width=4)

# --- Speech bubble top right ---
bub_x1, bub_y1 = 740, 60
bub_x2, bub_y2 = 960, 160
rounded_rect(draw, (bub_x1, bub_y1, bub_x2, bub_y2), 20, ORANGE)
draw.polygon([(bub_x1 + 30, bub_y2), (bub_x1 + 60, bub_y2 + 30), (bub_x1 + 80, bub_y2)], fill=ORANGE)
draw.text((bub_x1 + 50, bub_y1 + 38), "TALK", font=font_small, fill=LIGHT)

# --- Coffee cup bottom right ---
cup_x, cup_y = 860, 900
draw.rectangle([cup_x - 40, cup_y - 40, cup_x + 40, cup_y + 40], fill=ORANGE, outline=LIGHT, width=3)
draw.arc([cup_x + 30, cup_y - 25, cup_x + 70, cup_y + 15], 270, 90, fill=LIGHT, width=4)
# Steam
draw.arc([cup_x - 20, cup_y - 80, cup_x, cup_y - 40], 180, 0, fill=LIGHT, width=3)
draw.arc([cup_x, cup_y - 90, cup_x + 20, cup_y - 50], 180, 0, fill=LIGHT, width=3)

# --- Sound waves left side ---
for i, (x, y, r) in enumerate([(160, 320, 30), (120, 340, 50), (80, 360, 70)]):
    draw.arc([x - r, y - r, x + r, y + r], 270, 90, fill=ORANGE, width=4)

# --- Product name ---
draw.text((250, 900), "AI Controller", font=font_large, fill=LIGHT)
# Tagline
draw.text((330, 990), "Voice-first couch computing", font=font_tiny, fill="#a0a0a8")

img.save("logo.png")
print("saved logo.png")
