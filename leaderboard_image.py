"""يرسم صورة الليدربورد (ألوان فضي/أبيض مثل شعار Abyss)."""
import io
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

BASE = Path(__file__).parent
# يشتغل سواء حطيت الملفات في مجلد assets أو مباشرة بجانب الكود
ASSETS = BASE / "assets" if (BASE / "assets").is_dir() else BASE

# ====== إعدادات تقدر تغيرها ======
GAME_TITLE = "TIME BOMB"
SUBTITLE = "Abyss"
HEADER_RIGHT = "RANKINGS"
FOOTER_LEFT = "ABYSS SERVER"
FOOTER_RIGHT = "TIME BOMB  /  RANKED"
GOLD_MIN = 400    # نقاط الرتبة Gold وفوق
SILVER_MIN = 200  # نقاط الرتبة Silver وفوق، وتحتها Bronze
# ================================

W, H = 1200, 1075
S = 2  # تكبير داخلي عشان الحواف تطلع ناعمة

# ألوان
BG_TOP = (236, 238, 242)
BG_BOTTOM = (247, 248, 250)
CARD = (255, 255, 255)
CARD_BORDER = (214, 218, 225)
HI_CARD = (226, 229, 236)
HI_BORDER = (160, 166, 180)
TEXT = (27, 29, 34)
MUTED = (122, 128, 142)
ACCENT = (96, 104, 124)
DIVIDER = (208, 212, 220)
TIER_COLORS = {
    "Gold": (176, 136, 38),
    "Silver": (110, 118, 134),
    "Bronze": (160, 98, 58),
}

HAS_RAQM = features.check("raqm")
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:  # noqa: BLE001
    arabic_reshaper = None
    get_display = None


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(ASSETS / name), size * S)


def _prep(text: str) -> str:
    """تجهيز النص العربي. لو raqm موجود Pillow يتكفل فيه، وإلا نستخدم المكتبات."""
    if HAS_RAQM or not arabic_reshaper:
        return text
    return get_display(arabic_reshaper.reshape(text))


def tier_for(points: int) -> str:
    if points >= GOLD_MIN:
        return "Gold"
    if points >= SILVER_MIN:
        return "Silver"
    return "Bronze"


class _Canvas:
    def __init__(self):
        self.img = Image.new("RGB", (W * S, H * S), BG_BOTTOM)
        top = Image.new("RGB", (1, 2))
        # تدرج خفيف من فوق لتحت
        grad = Image.new("RGB", (1, H * S))
        for y in range(H * S):
            t = y / (H * S - 1)
            grad.putpixel((0, y), tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)))
        self.img.paste(grad.resize((W * S, H * S)))
        del top
        self.d = ImageDraw.Draw(self.img)

    def rect(self, x1, y1, x2, y2, fill, outline=None, r=12, width=1):
        self.d.rounded_rectangle(
            (x1 * S, y1 * S, x2 * S, y2 * S), radius=r * S, fill=fill, outline=outline, width=width * S
        )

    def line(self, x1, y1, x2, y2, fill, width=1):
        self.d.line((x1 * S, y1 * S, x2 * S, y2 * S), fill=fill, width=width * S)

    def text(self, x, y, text, font, fill, anchor="lm"):
        self.d.text((x * S, y * S), _prep(text), font=font, fill=fill, anchor=anchor)

    def fit(self, text, font, max_w):
        """يقص الاسم الطويل ويحط ... في آخره."""
        if font.getlength(_prep(text)) <= max_w * S:
            return text
        while len(text) > 1 and font.getlength(_prep(text + "…")) > max_w * S:
            text = text[:-1]
        return text + "…"


def _load_logo(size_h: int) -> Image.Image:
    logo = Image.open(ASSETS / "logo.png").convert("RGBA").crop((270, 380, 985, 900))
    ratio = size_h / logo.height
    return logo.resize((int(logo.width * ratio), size_h), Image.LANCZOS)


def _row(c: _Canvas, x, y, w, idx, name, tier, value, highlight):
    c.rect(x, y, x + w, y + 62, HI_CARD if highlight else CARD, HI_BORDER if highlight else CARD_BORDER, r=12)
    c.text(x + 16, y + 31, f"{idx:02d}", _font(True, 20), MUTED)
    name = c.fit(name, _font(True, 21), w - 61 - 150)
    c.text(x + 61, y + 22, name, _font(True, 21), TEXT)
    c.text(x + 61, y + 46, tier, _font(True, 13), TIER_COLORS[tier])
    color = TIER_COLORS["Gold"] if tier == "Gold" else (TEXT if highlight else ACCENT)
    c.text(x + w - 18, y + 31, value, _font(True, 25), color, anchor="rm")


def render(players: list[dict]) -> bytes:
    """players: [{'name': str, 'points': int, 'streak': int, 'best': int}]"""
    c = _Canvas()

    # شريط علوي فضي
    for x in range(W * S):
        t = x / (W * S - 1)
        col = tuple(int(a + (b - a) * t) for a, b in zip((176, 182, 196), (240, 241, 245)))
        c.d.line((x, 0, x, 5 * S), fill=col)

    # الهيدر: الشعار + الاسم
    c.rect(48, 28, 178, 112, (255, 255, 255), CARD_BORDER, r=16)
    logo = _load_logo(76)
    logo_bg = Image.new("RGBA", logo.size, (255, 255, 255, 255))
    logo_bg.alpha_composite(logo)
    logo_small = logo_bg.convert("RGB")
    logo_small.thumbnail((116, 76), Image.LANCZOS)
    c.img.paste(logo_small.resize((logo_small.width * S, logo_small.height * S), Image.LANCZOS),
                ((113 - logo_small.width // 2) * S, (70 - logo_small.height // 2) * S))
    c.text(198, 55, GAME_TITLE, _font(True, 30), TEXT)
    c.text(198, 90, SUBTITLE, _font(False, 17), MUTED)
    c.text(1152, 70, HEADER_RIGHT, _font(True, 16), ACCENT, anchor="rm")
    c.line(48, 138, 1152, 138, DIVIDER)

    # العنوان
    now = datetime.now().isocalendar()
    c.text(48, 192, "LEADERBOARD", _font(True, 36), TEXT)
    c.text(1152, 192, f"Season {now[0]}-W{now[1]:02d}", _font(False, 18), MUTED, anchor="rm")

    # عناوين الأعمدة
    c.text(48, 245, "TOP POINTS", _font(True, 24), ACCENT)
    c.text(564, 245, "POINTS", _font(True, 13), MUTED, anchor="rm")
    c.text(624, 245, "WIN STREAK", _font(True, 24), ACCENT)
    c.text(1140, 245, "NOW / BEST", _font(True, 13), MUTED, anchor="rm")
    c.line(600, 225, 600, 990, DIVIDER)

    by_points = sorted(players, key=lambda p: (-p["points"], p["name"].lower()))[:10]
    by_streak = sorted(players, key=lambda p: (-p["streak"], -p["best"], p["name"].lower()))[:10]

    for i, p in enumerate(by_points):
        _row(c, 48, 280 + i * 72, 528, i + 1, p["name"], tier_for(p["points"]), str(p["points"]), i == 0)
    for i, p in enumerate(by_streak):
        _row(c, 624, 280 + i * 72, 528, i + 1, p["name"], tier_for(p["points"]),
             f"{p['streak']} / {p['best']}", i == 0)

    # الفوتر
    c.line(48, 1012, 1152, 1012, DIVIDER)
    c.text(48, 1044, FOOTER_LEFT, _font(True, 14), MUTED)
    c.text(1152, 1044, FOOTER_RIGHT, _font(False, 14), MUTED, anchor="rm")

    out = c.img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


if __name__ == "__main__":
    demo = [
        {"name": "zombie", "points": 512, "streak": 4, "best": 9},
        {"name": "RJF", "points": 506, "streak": 31, "best": 31},
        {"name": "قاوليج", "points": 434, "streak": 2, "best": 5},
        {"name": "Madre", "points": 426, "streak": 3, "best": 8},
        {"name": "qrk", "points": 421, "streak": 6, "best": 13},
        {"name": "عووو ابو سوسو المفترس", "points": 322, "streak": 18, "best": 18},
        {"name": "u4ig", "points": 316, "streak": 9, "best": 9},
        {"name": "KinicH", "points": 262, "streak": 1, "best": 4},
        {"name": "Radar", "points": 150, "streak": 8, "best": 8},
        {"name": "Draven السينسي", "points": 240, "streak": 12, "best": 12},
        {"name": "zocka56", "points": 90, "streak": 7, "best": 7},
    ]
    Path("/tmp/preview.png").write_bytes(render(demo))
    print("saved")
