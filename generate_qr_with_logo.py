import qrcode
from PIL import Image
import pandas as pd
import os

CODES_CSV = "codes.csv"          # your codes file
LOGO_PATH = "Logo_E-Cell.png"    # your logo file
OUTPUT_FOLDER = "qr_output"      # folder for QR images
LOGO_SCALE = 4                   # 4 => logo ~25% of QR width

os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Load logo
logo = Image.open(LOGO_PATH).convert("RGBA")

# Read codes
df = pd.read_csv(CODES_CSV)      # expects a column named "code"

for idx, code in enumerate(df["code"]):
    if pd.isna(code):
        continue
    text = str(code).strip()
    if not text:
        continue

    # Create QR (black & white, high error correction)
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(text)          # QR contains only the code, e.g. "STU123"
    qr.make(fit=True)

    img_qr = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

    # Resize logo and paste in centre
    qr_w, qr_h = img_qr.size
    logo_size = qr_w // LOGO_SCALE
    logo_resized = logo.copy()
    logo_resized.thumbnail((logo_size, logo_size), Image.LANCZOS)

    # Position logo with a clean white background
    lw, lh = logo_resized.size

    # White padding behind logo
    padding = 10  # adjust if needed
    white_box = Image.new("RGBA", (lw + padding, lh + padding), (255, 255, 255, 255))

    # Center white box
    pos = ((qr_w - white_box.size[0]) // 2,
           (qr_h - white_box.size[1]) // 2)

    # Paste white box on QR
    img_qr.paste(white_box, pos)

    # Now position the logo slightly inside
    logo_pos = (pos[0] + padding//2, pos[1] + padding//2)
    img_qr.paste(logo_resized, logo_pos, mask=logo_resized)


    safe = "".join(c if c.isalnum() else "_" for c in text)
    out_path = os.path.join(OUTPUT_FOLDER, f"QR_{safe or idx}.png")
    img_qr.save(out_path)

print("Done. QR images are in folder:", OUTPUT_FOLDER)
