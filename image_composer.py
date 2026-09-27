"""
Наложение расписания свободных слотов на подложку (шаблон истории/поста).

Работает с любым размером подложки — вся геометрия карточки и текста
считается в долях от размера картинки, поэтому стиль не «плывёт»
ни на сторис (1080×1920), ни на квадратном посте (1080×1080).

Шрифты и логотип можно заменить своими прямо через бота (команды
/set_font_bold, /set_font_medium, /set_font_italic, /set_logo — см.
bot.py и README). Загруженные файлы кладутся в ASSETS_DIR и имеют
приоритет над встроенными по умолчанию (DejaVu Sans, без логотипа).
"""

import os
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).parent

# ASSETS_DIR — папка для файлов, загруженных через бота (шрифты, лого).
# По умолчанию — просто assets/ рядом с кодом. Если хостинг поддерживает
# постоянное хранилище (Railway Volume и т.п.) — укажите путь к нему
# через переменную окружения ASSETS_DIR, чтобы загруженное не терялось
# при передеплое (см. README, раздел про Volume).
ASSETS_DIR = Path(os.getenv("ASSETS_DIR", BASE_DIR / "assets"))
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# Встроенные шрифты по умолчанию — гарантированно поддерживают кириллицу
DEFAULT_FONT_BOLD = BASE_DIR / "DejaVuSans-Bold.ttf"
DEFAULT_FONT_MEDIUM = BASE_DIR / "DejaVuSans.ttf"
DEFAULT_FONT_ITALIC = BASE_DIR / "DejaVuSans-BoldOblique.ttf"

# Загружаемые через бота файлы (если присланы — используются вместо дефолтных)
CUSTOM_FONT_BOLD = ASSETS_DIR / "font_bold.ttf"
CUSTOM_FONT_MEDIUM = ASSETS_DIR / "font_medium.ttf"
CUSTOM_FONT_ITALIC = ASSETS_DIR / "font_italic.ttf"
LOGO_PATH = ASSETS_DIR / "logo.png"


def _resolve_font(custom: Path, default: Path) -> str:
    return str(custom) if custom.exists() else str(default)


def _current_fonts() -> tuple[str, str, str]:
    """Возвращает актуальные пути к шрифтам (кастомные, если загружены, иначе — по умолчанию)."""
    return (
        _resolve_font(CUSTOM_FONT_BOLD, DEFAULT_FONT_BOLD),
        _resolve_font(CUSTOM_FONT_MEDIUM, DEFAULT_FONT_MEDIUM),
        _resolve_font(CUSTOM_FONT_ITALIC, DEFAULT_FONT_ITALIC),
    )


DEEP_GREEN = (0, 67, 42)
LIME = (202, 210, 63)
CREAM = (237, 233, 207)



def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def compose_schedule_image(
    template_bytes: bytes,
    date_label: str,
    slots: list[str],
) -> bytes:
    """
    template_bytes — исходная подложка (PNG/JPEG), присланная пользователем.
    date_label      — дата для заголовка, например "24 сентября".
    slots           — список свободных часов, например ["11:00", "13:00", "15:30"].

    Возвращает готовое изображение (PNG) в виде байтов.
    """
    img = Image.open(BytesIO(template_bytes)).convert("RGBA")
    W, H = img.size

    font_bold, font_medium, font_italic = _current_fonts()

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # ---- логотип (если загружен через /set_logo) — верхний левый угол ----
    if LOGO_PATH.exists():
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA")
            logo_w = int(W * 0.22)
            logo_h = int(logo_w * logo.size[1] / logo.size[0])
            logo = logo.resize((logo_w, logo_h))
            overlay.paste(logo, (int(W * 0.06), int(H * 0.04)), logo)
        except Exception:
            pass  # битый файл лого не должен ронять всю сборку картинки

    # ---- геометрия карточки (доли от размера картинки) ----
    pad_x = int(W * 0.08)
    inner_x = pad_x + int(W * 0.05)
    card_top = int(H * 0.58)
    card_bottom = int(H * 0.95)
    radius = int(W * 0.045)

    # сколько строк слотов реально понадобится — чтобы карточка не была
    # длиннее, чем нужно, если часов мало
    cols = 3 if len(slots) > 6 else 2
    rows_needed = max(1, -(-len(slots) // cols))  # округление вверх
    title_block_h = int(H * 0.02) + int(W * 0.052 * 1.25) + int(W * 0.034 * 1.6)
    cell_h = int(H * 0.06)
    gap = int(W * 0.02)
    grid_h = rows_needed * cell_h + (rows_needed - 1) * gap
    needed_card_h = title_block_h + grid_h + int(H * 0.045)
    card_top = max(int(H * 0.30), card_bottom - needed_card_h)

    draw.rounded_rectangle(
        [pad_x, card_top, W - pad_x, card_bottom],
        radius=radius,
        fill=(*DEEP_GREEN, 235),
    )

    # ---- заголовок ----
    title_size = int(W * 0.052)
    sub_size = int(W * 0.034)
    title_font = _font(font_bold, title_size)
    sub_font = _font(font_medium, sub_size)

    ty = card_top + int(H * 0.028)
    draw.text((inner_x, ty), "СВОБОДНЫЕ ЧАСЫ", font=title_font, fill=LIME)
    ty += int(title_size * 1.25)
    draw.text((inner_x, ty), f"на {date_label}", font=sub_font, fill=CREAM)
    ty += int(sub_size * 1.6)

    # ---- сетка слотов (пилюли) ----
    slot_font = _font(font_italic, int(W * 0.042))
    cell_w = (W - 2 * pad_x - int(W * 0.05) - int(W * 0.03)) // cols

    x0 = inner_x
    y0 = ty + int(H * 0.008)

    for i, slot in enumerate(slots):
        col = i % cols
        row = i // cols
        x = x0 + col * (cell_w + gap)
        y = y0 + row * (cell_h + gap)
        if y + cell_h > card_bottom - int(H * 0.02):
            break  # защита от переполнения карточки
        draw.rounded_rectangle(
            [x, y, x + cell_w - gap, y + cell_h],
            radius=int(cell_h * 0.35),
            fill=(*LIME, 255),
        )
        bbox = draw.textbbox((0, 0), slot, font=slot_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = x + (cell_w - gap - tw) / 2
        tyy = y + (cell_h - th) / 2 - bbox[1]
        draw.text((tx, tyy), slot, font=slot_font, fill=DEEP_GREEN)

    result = Image.alpha_composite(img, overlay).convert("RGB")
    out = BytesIO()
    result.save(out, format="PNG")
    return out.getvalue()
