"""يرسم صورة الليدربورد بتصميم Abyss الداكن (الخلفية من assets/template.png)."""
import io
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

BASE = Path(__file__).parent
# يشتغل سواء حطيت الملفات في مجلد assets أو مباشرة بجانب الكود
ASSETS = BASE / "assets" if (BASE / "assets").is_dir() else BASE

# ====== إعدادات تقدر تغيرها ======
GOLD_MIN = 400    # نقاط الرتبة Gold وفوق
SILVER_MIN = 200  # نقاط الرتبة Silver وفوق، وتحتها Bronze
ROWS = 10         # عدد اللاعبين في كل عمود
# ================================

S = 2  # تكبير داخلي عشان الحواف تطلع ناعمة

# مواقع العناصر على القالب (بحجمه الأصلي 1325x1187)
LEFT_X, RIGHT_X, COL_W = 56, 689, 581
ROW_Y0, ROW_PITCH, ROW_H = 418, 66, 58
SEASON_POS = (1268, 324)

TEXT = (238, 238, 242)
MUTED = (150, 152, 162)
TIER_COLORS = {
    "Gold": (222, 184, 92),
    "Silver": (188, 194, 208),
    "Bronze": (200, 138, 90),
}

HAS_RAQM = features.check("raqm")
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:  # noqa: BLE001
    arabic_reshaper = None
    get_display = None

_template_cache = None


def _template() -> Image.Image:
    global _template_cache
    if _template_cache is None:
        img = Image.open(ASSETS / "template.png").convert("RGB")
        _template_cache = img.resize((img.width * S, img.height * S), Image.LANCZOS)
    return _template_cache.copy()


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


def _fit(text: str, font, max_w: int) -> str:
    if font.getlength(_prep(text)) <= max_w * S:
        return text
    while len(text) > 1 and font.getlength(_prep(text + "…")) > max_w * S:
        text = text[:-1]
    return text + "…"


def _cards(size, rows) -> Image.Image:
    """طبقة شفافة فيها البطاقات الزجاجية الداكنة."""
    ov = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for x, y, w, highlight in rows:
        fill = (96, 96, 104, 205) if highlight else (62, 62, 68, 192)
        border = (255, 255, 255, 150) if highlight else (255, 255, 255, 80)
        d.rounded_rectangle((x * S, y * S, (x + w) * S, (y + ROW_H) * S), radius=14 * S, fill=fill, outline=border, width=S)
    return ov


def _avatar(img: Image.Image, d: ImageDraw.ImageDraw, x, y, size, img_bytes, initial):
    """صورة اللاعب بشكل دائري، ولو ما فيه صورة يحط حرف أول الاسم."""
    dd = size * S
    av = None
    if img_bytes:
        try:
            av = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((dd, dd), Image.LANCZOS)
        except Exception:  # noqa: BLE001
            av = None
    if av is None:
        av = Image.new("RGB", (dd, dd), (70, 70, 78))
        ImageDraw.Draw(av).text((dd // 2, dd // 2), _prep(initial), font=_font(True, 20), fill=TEXT, anchor="mm")
    mask = Image.new("L", (dd, dd), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, dd - 1, dd - 1), fill=255)
    img.paste(av, (x * S, y * S), mask)
    d.ellipse((x * S, y * S, x * S + dd - 1, y * S + dd - 1), outline=(255, 255, 255, 110), width=S)


def _row_content(img, d, x, y, w, idx, p, tier, value, highlight, avatars):
    d.text(((x + 18) * S, (y + ROW_H / 2) * S), f"{idx:02d}", font=_font(True, 18), fill=MUTED, anchor="lm")
    nickname = p.get("display") or p["name"]
    username = p.get("username")
    _avatar(img, d, x + 52, y + 9, 40, avatars.get(p.get("roblox_id")), (nickname or "?")[:1].upper())
    tx = x + 106
    nick_font = _font(True, 20)
    d.text((tx * S, (y + 21) * S), _prep(_fit(nickname, nick_font, w - 106 - 150)), font=nick_font, fill=TEXT, anchor="lm")
    small = _font(False, 12)
    tier_x = tx
    if username:
        label = _fit(f"@{username}", small, 160)
        d.text((tx * S, (y + 42) * S), _prep(label), font=small, fill=MUTED, anchor="lm")
        tier_x = tx + small.getlength(_prep(label)) / S + 12
    d.text((tier_x * S, (y + 42) * S), tier, font=_font(True, 12), fill=TIER_COLORS[tier], anchor="lm")
    color = TIER_COLORS["Gold"] if tier == "Gold" else (255, 255, 255 if highlight else 255)
    if tier != "Gold" and not highlight:
        color = (214, 216, 224)
    d.text(((x + w - 22) * S, (y + ROW_H / 2) * S), value, font=_font(True, 24), fill=color, anchor="rm")


def render(players: list[dict], avatars: dict | None = None) -> bytes:
    """players: [{name, points, streak, best, roblox_id?, display?, username?}]
    avatars: {roblox_id: صورة PNG بالبايتات}"""
    avatars = avatars or {}
    img = _template()

    by_points = sorted(players, key=lambda p: (-p["points"], p["name"].lower()))[:ROWS]
    by_streak = sorted(players, key=lambda p: (-p["streak"], -p["best"], p["name"].lower()))[:ROWS]

    layout = []
    for i in range(len(by_points)):
        layout.append((LEFT_X, ROW_Y0 + i * ROW_PITCH, COL_W, i == 0))
    for i in range(len(by_streak)):
        layout.append((RIGHT_X, ROW_Y0 + i * ROW_PITCH, COL_W, i == 0))

    img = Image.alpha_composite(img.convert("RGBA"), _cards(img.size, layout)).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")

    # الأسبوع (يتغير)، والباقي مكتوب في القالب
    now = datetime.now().isocalendar()
    d.text((SEASON_POS[0] * S, SEASON_POS[1] * S), f"Season {now[0]}-W{now[1]:02d}",
           font=_font(False, 18), fill=(140, 142, 152), anchor="rm")

    for i, p in enumerate(by_points):
        _row_content(img, d, LEFT_X, ROW_Y0 + i * ROW_PITCH, COL_W, i + 1, p, tier_for(p["points"]),
                     str(p["points"]), i == 0, avatars)
    for i, p in enumerate(by_streak):
        _row_content(img, d, RIGHT_X, ROW_Y0 + i * ROW_PITCH, COL_W, i + 1, p, tier_for(p["points"]),
                     f"{p['streak']} / {p['best']}", i == 0, avatars)

    out = img.resize((img.width // S, img.height // S), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


if __name__ == "__main__":
    def fake_avatar(color):
        im = Image.new("RGB", (150, 150), color)
        ImageDraw.Draw(im).ellipse((45, 25, 105, 85), fill=(240, 240, 240))
        ImageDraw.Draw(im).rectangle((35, 95, 115, 150), fill=(240, 240, 240))
        b = io.BytesIO()
        im.save(b, "PNG")
        return b.getvalue()

    colors = [(90, 120, 200), (200, 120, 90), (110, 170, 120), (170, 110, 190), (200, 170, 80)]
    demo = [
        {"name": "nef", "display": "nef", "username": "nef_x", "roblox_id": 1, "points": 282, "streak": 0, "best": 0},
        {"name": "aak3", "display": "aak3", "username": "aak3", "roblox_id": 2, "points": 253, "streak": 0, "best": 0},
        {"name": "RJF", "display": "RJF 👑", "username": "RJF_pro", "roblox_id": 3, "points": 506, "streak": 31, "best": 31},
        {"name": "q", "display": "قاوليج", "username": "qawleeg", "roblox_id": 4, "points": 434, "streak": 2, "best": 5},
        {"name": "Madre", "display": "Madre", "username": "Madre_1", "roblox_id": 5, "points": 426, "streak": 3, "best": 8},
        {"name": "عووو ابو سوسو المفترس", "display": "عووو ابو سوسو المفترس", "username": "abo_sosso", "roblox_id": 6, "points": 322, "streak": 18, "best": 18},
        {"name": "manual_player", "points": 316, "streak": 9, "best": 9},
        {"name": "KinicH", "points": 162, "streak": 1, "best": 4},
        {"name": "zocka56", "points": 120, "streak": 7, "best": 7},
        {"name": "Radar", "points": 110, "streak": 8, "best": 8},
        {"name": "u4ig", "points": 90, "streak": 5, "best": 9},
    ]
    avs = {i: fake_avatar(colors[i % 5]) for i in range(1, 7)}
    Path("/tmp/preview3.png").write_bytes(render(demo, avs))
    print("saved")
