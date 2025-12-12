import qrcode
from PIL import Image, ImageDraw
import pandas as pd
import os
from pathlib import Path
import shutil

CSV_FILE = "codes.csv"
LOGO_FILE = "Logo_E-Cell.png"
OUT_DIR = "qr_output"

LOGO_SCALE = 6
BOX_PADDING = 16
BOX_ROUND = 14
BOX_SIZE = 12
QR_BORDER = 4

os.makedirs(OUT_DIR, exist_ok=True)

# Load CSV
df = pd.read_csv(CSV_FILE, dtype=str)
if "code" in df.columns:
    codes = df["code"].astype(str).str.strip().tolist()
else:
    codes = df[df.columns[0]].astype(str).str.strip().tolist()

codes = [c for c in codes if c]

# Load logo
logo = Image.open(LOGO_FILE).convert("RGBA")

def make_qr(text, out_path):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=BOX_SIZE,
        border=QR_BORDER,
    )
    qr.add_data(text)
    qr.make(fit=True)

    img_qr = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

    qr_w, qr_h = img_qr.size
    logo_target = qr_w // LOGO_SCALE

    logo_resized = logo.copy()
    logo_resized.thumbnail((logo_target, logo_target), Image.LANCZOS)
    lw, lh = logo_resized.size

    # White rounded box
    box_w = lw + BOX_PADDING
    box_h = lh + BOX_PADDING

    white_box = Image.new("RGBA", (box_w, box_h), (255,255,255,255))
    mask = Image.new("L", (box_w, box_h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0,0),(box_w-1, box_h-1)], radius=BOX_ROUND, fill=255)
    white_box.putalpha(mask)

    box_pos = ((qr_w - box_w)//2, (qr_h - box_h)//2)
    img_qr.paste(white_box, box_pos, white_box)

    logo_pos = (box_pos[0] + (box_w - lw)//2, box_pos[1] + (box_h - lh)//2)
    img_qr.paste(logo_resized, logo_pos, logo_resized)

    img_qr.save(out_path, "PNG")


# Generate sequentially
for idx, code in enumerate(codes, start=1):
    filename = f"qr_{idx:03}.png"  # → qr_001.png
    out_path = os.path.join(OUT_DIR, filename)
    make_qr(code, out_path)
    if idx % 100 == 0:
        print(f"Generated: {idx}")

# Create zip file
zip_path = "qr_output.zip"
if os.path.exists(zip_path):
    os.remove(zip_path)
shutil.make_archive("qr_output", "zip", OUT_DIR)

print("Done! Saved to:", OUT_DIR)
print("Zip file:", zip_path)
