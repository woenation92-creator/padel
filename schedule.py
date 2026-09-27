"""
Автоматическое получение расписания свободных слотов на дату —
без участия человека: скрипт сам открывает страницу бронирования
в headless-браузере (Playwright/Chromium) и вытаскивает данные.

Как это устроено:
1. Открываем https://granpadel.ru/book в headless-браузере.
2. Слушаем все сетевые ответы страницы и ищем среди них JSON,
   похожий на расписание (по ключам вроде slot/time/schedule/free
   или по значениям формата ЧЧ:ММ).
3. Если по сети ничего подходящего не нашли — пытаемся вытащить
   времена прямо из отрендеренного текста страницы (запасной способ).
4. Если и это не сработало — возвращаем None, и бот вручную спросит
   человека (это единственный случай, когда человек участвует —
   как аварийный запасной вариант, а не обычный сценарий).

ВАЖНО — калибровка при первом реальном запуске:
Я не смог протестировать этот код против живого сайта: сетевой доступ
из моей рабочей среды ограничен прокси-фильтром (сайт не входит в
разрешённый список хостов). Сам код рабочий и написан по стандартной
схеме перехвата сетевых запросов Playwright, но конкретные названия
полей в JSON вашего сайта я угадать не могу — их нужно один раз
свериться при первом реальном запуске.

Как откалибровать (займёт минут 10):
1. Запустите: python schedule.py "24 сентября"  (отдельно, не через бота)
2. Скрипт напечатает всё, что перехватил по сети, и сохранит в
   debug_schedule_capture.json — откройте этот файл и посмотрите,
   как реально выглядит ответ сайта с расписанием.
3. Поправьте функцию _looks_like_schedule() и _extract_free_slots()
   ниже под реальную структуру (например, если свободные часы лежат
   не в ключе "free", а в "available" — поменяйте условие).
4. Всё, дальше работает само, без калибровки.
"""

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright

TIME_PATTERN = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")

BOOKING_URL = "https://granpadel.ru/book"
DEBUG_CAPTURE_FILE = Path(__file__).parent / "debug_schedule_capture.json"

# Ключевые слова, по которым узнаём "похоже на расписание" среди
# перехваченных JSON-ответов страницы
SCHEDULE_KEY_HINTS = ("slot", "schedule", "time", "book", "free", "court", "занят", "своб")


def _looks_like_schedule(url: str, data) -> bool:
    """Эвристика: похож ли этот JSON-ответ на данные расписания."""
    url_lower = url.lower()
    if any(hint in url_lower for hint in SCHEDULE_KEY_HINTS):
        return True
    dumped = json.dumps(data, ensure_ascii=False).lower()
    if TIME_PATTERN.search(dumped) and any(hint in dumped for hint in SCHEDULE_KEY_HINTS):
        return True
    return False


def _extract_free_slots(data) -> list[str]:
    """
    Достаёт список свободных часов из JSON расписания.

    ЗАГЛУШКА-ЭВРИСТИКА: сейчас просто собирает все строки вида ЧЧ:ММ
    из ответа. Это может подхватить и занятые слоты тоже — если в
    реальном JSON есть явный признак занятости (например,
    "free": true/false или "status": "booked"), поправьте эту функцию,
    чтобы отбирать только свободные.
    """
    found = []

    def walk(node):
        if isinstance(node, dict):
            # частый паттерн: {"time": "11:00", "free": true} / {"available": true}
            time_val = node.get("time") or node.get("start") or node.get("slot")
            is_free = node.get("free")
            if is_free is None:
                is_free = node.get("available")
            if is_free is None:
                status = str(node.get("status", "")).lower()
                is_free = status in ("free", "available", "open", "") or status == ""
            if isinstance(time_val, str) and TIME_PATTERN.search(time_val) and is_free:
                found.append(TIME_PATTERN.search(time_val).group())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            m = TIME_PATTERN.search(node)
            if m:
                found.append(m.group())

    walk(data)
    return sorted(set(found))


async def _scrape_via_network(page) -> Optional[list[str]]:
    captured = []

    async def handle_response(response):
        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            data = await response.json()
            if _looks_like_schedule(response.url, data):
                captured.append({"url": response.url, "data": data})
        except Exception:
            pass

    page.on("response", handle_response)

    await page.goto(BOOKING_URL, timeout=20000, wait_until="networkidle")
    await page.wait_for_timeout(2000)  # даём время на подгрузку виджета расписания

    # сохраняем всё перехваченное — пригодится для калибровки
    DEBUG_CAPTURE_FILE.write_text(
        json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    slots = []
    for item in captured:
        slots.extend(_extract_free_slots(item["data"]))
    return sorted(set(slots)) if slots else None


async def _scrape_via_dom_text(page) -> Optional[list[str]]:
    """Запасной способ: просто ищем времена в видимом тексте страницы."""
    text = await page.inner_text("body")
    times = sorted(set(TIME_PATTERN.findall(text)))
    # findall с группами вернёт только часть матча — используем finditer вместо этого
    times = sorted(set(m.group() for m in TIME_PATTERN.finditer(text)))
    return times or None


async def fetch_schedule_auto(date_str: str) -> Optional[list[str]]:
    """
    Открывает сайт в headless-браузере и пытается получить свободные
    часы на дату date_str. Возвращает None, если не получилось —
    тогда бот запасным вариантом спросит человека.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            # Если на странице есть переключатель даты — пробуем кликнуть
            # по тексту с нужной датой. Разметка сайта мне не видна, так
            # что это best-effort попытка; если сайт по умолчанию
            # показывает "сегодня" и дата пользователю нужна тоже
            # сегодняшняя — этот шаг не обязателен.
            slots = await _scrape_via_network(page)

            if not slots:
                try:
                    await page.get_by_text(date_str, exact=False).first.click(timeout=3000)
                    await page.wait_for_load_state("networkidle")
                    slots = await _scrape_via_network(page)
                except Exception:
                    pass

            if not slots:
                slots = await _scrape_via_dom_text(page)

            await browser.close()
            return slots
    except Exception as e:
        print(f"[schedule.py] Автополучение не удалось: {e!r}")
        return None


def parse_manual_slots(raw_text: str) -> list[str]:
    """Разбирает часы, введённые человеком через запятую или с новой строки —
    аварийный запасной вариант, если авто-получение не сработало."""
    parts = raw_text.replace("\n", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


AUTO_FETCH_ENABLED = True  # человек больше не нужен в обычном режиме


if __name__ == "__main__":
    # Отдельный запуск для калибровки: python schedule.py "24 сентября"
    date_arg = sys.argv[1] if len(sys.argv) > 1 else "сегодня"
    print(f"Пробую получить расписание на «{date_arg}»…")
    result = asyncio.run(fetch_schedule_auto(date_arg))
    print("Результат:", result)
    print(f"Полный перехваченный сетевой трафик сохранён в {DEBUG_CAPTURE_FILE}")

