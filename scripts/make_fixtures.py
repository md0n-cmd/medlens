"""Builds synthetic upload fixtures in samples/files/ from the synthetic .txt samples."""
import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples" / "files"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
random.seed(7)


def text_pdf(src: Path, dst: Path) -> None:
    c = canvas.Canvas(str(dst), pagesize=A4)
    c.setFont("Courier", 8.5)
    y = A4[1] - 40
    for line in src.read_text().splitlines():
        c.drawString(30, y, line)
        y -= 11
    c.save()


def render(src: Path, size=20) -> Image.Image:
    lines = src.read_text().splitlines()
    font = ImageFont.truetype(FONT, size)
    w = int(max(font.getlength(l) for l in lines)) + 80
    img = Image.new("RGB", (w, len(lines) * int(size * 1.5) + 80), "white")
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        d.text((40, 40 + i * int(size * 1.5)), l, fill="black", font=font)
    return img


def phone_photo(img: Image.Image) -> Image.Image:
    bg = Image.new("RGB", img.size, (238, 232, 218))
    img = Image.blend(img, bg, 0.25)                                  # off-white paper
    img = img.rotate(2, expand=True, fillcolor=(205, 200, 190), resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    px = img.load()
    for _ in range(img.width * img.height // 40):                     # sensor noise
        x, y = random.randrange(img.width), random.randrange(img.height)
        v = random.randint(-40, 40)
        r, g, b = px[x, y]
        px[x, y] = (max(0, min(255, r + v)),) * 3
    return img


def image_pdf(img: Image.Image, dst: Path) -> None:
    img.convert("RGB").save(dst, "PDF", resolution=200)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    cbc, lipid = ROOT / "samples" / "01_cbc.txt", ROOT / "samples" / "02_lipid_diabetes.txt"
    text_pdf(cbc, OUT / "01_cbc.pdf")
    clean = render(cbc)
    clean.save(OUT / "01_cbc.png")
    phone_photo(clean).save(OUT / "01_cbc_scan.jpg", quality=80)
    image_pdf(render(lipid), OUT / "02_lipid_scan.pdf")
    for p in sorted(OUT.iterdir()):
        print(p.name, p.stat().st_size)
