"""
Библиотека готовых логотипов, шрифтов и фоновых картинок — каждой
опции присвоен номер. Чтобы выбрать нужный вариант, человек просто
пишет боту команду с номером (например /logo 2), не нужно ничего
искать и загружать самому.

Если понадобится добавить свой вариант — команды /set_logo,
/set_font_bold и т.д. (см. bot.py) по-прежнему работают отдельно и
имеют приоритет над выбором из этой библиотеки.
"""

from pathlib import Path

ROOT_DIR = Path(__file__).parent

LOGOS = {
    1: {
        "file": ROOT_DIR / "logo_1_white.png",
        "title": "Белый (знак + надпись)",
        "note": "для тёмных фото",
    },
    2: {
        "file": ROOT_DIR / "logo_2_lime_cream.png",
        "title": "Лаймовый знак + кремовая надпись",
        "note": "для тёмных фото",
    },
    3: {
        "file": ROOT_DIR / "logo_3_lime_dark.png",
        "title": "Лаймовый знак + тёмно-зелёная надпись",
        "note": "для светлых фото",
    },
    4: {
        "file": ROOT_DIR / "logo_4_icon_only.png",
        "title": "Только знак, без надписи",
        "note": "универсальный, компактный",
    },
}

FONTS = {
    1: {
        "bold": ROOT_DIR / "DejaVuSans-Bold.ttf",
        "medium": ROOT_DIR / "DejaVuSans.ttf",
        "italic": ROOT_DIR / "DejaVuSans-BoldOblique.ttf",
        "title": "Геометричный (по умолчанию)",
        "note": "нейтральный, гарантированно с кириллицей",
    },
    2: {
        "bold": ROOT_DIR / "font2_Lora-Bold.ttf",
        "medium": ROOT_DIR / "font2_Lora-Regular.ttf",
        "italic": ROOT_DIR / "font2_Lora-BoldItalic.ttf",
        "title": "С засечками, классический (Lora)",
        "note": "более элегантный, менее «плакатный»",
    },
    # Место под инстаграмовский скруглённый шрифт (SF Pro Rounded /
    # похожий). Свободного файла с кириллицей под рукой нет — как
    # только пришлёте свой .ttf через /set_font_bold, он ляжет туда
    # же и станет доступен так же просто, отдельным номером можно
    # оформить после того, как появится файл.
}

TEMPLATES = {
    1: {
        "file": ROOT_DIR / "template_1_coffee.jpg",
        "title": "Игрок с ракеткой у кофейни",
        "note": "вертикальный, хорош для сторис",
    },
    2: {
        "file": ROOT_DIR / "template_2_action.jpg",
        "title": "Удар ракеткой крупным планом",
        "note": "широкий кадр, лучше для постов",
    },
    3: {
        "file": ROOT_DIR / "template_3_terrace.jpg",
        "title": "Терраса клуба с вывеской",
        "note": "вертикальный, атмосферный",
    },
}


def menu_text() -> str:
    lines = ["ЛОГОТИП — команда /logo <номер>"]
    for n, v in LOGOS.items():
        lines.append(f"  {n}. {v['title']} — {v['note']}")

    lines.append("")
    lines.append("ШРИФТ — команда /font <номер>")
    for n, v in FONTS.items():
        lines.append(f"  {n}. {v['title']} — {v['note']}")

    lines.append("")
    lines.append("ГОТОВАЯ ПОДЛОЖКА — команда /template <номер>")
    lines.append("(если не хотите присылать своё фото)")
    for n, v in TEMPLATES.items():
        lines.append(f"  {n}. {v['title']} — {v['note']}")

    lines.append("")
    lines.append("Свой файл можно прислать и напрямую: команды")
    lines.append("/set_logo, /set_font_bold, /set_font_medium,")
    lines.append("/set_font_italic — с файлом в этом же сообщении.")
    return "\n".join(lines)
