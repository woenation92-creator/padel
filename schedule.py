"""
Автоматическое получение расписания свободных слотов на дату —
без участия человека.

ОБНОВЛЕНО после калибровки на реальном сайте (27.09.2026): у клуба
оказался чистый прямой API, поэтому теперь это основной и самый
быстрый способ:

    https://api.granpadel.ru/slots/availability
        ?date=YYYY-MM-DD
        &court_type=covered
        &duration_minutes=90

Ответ — JSON вида:
    {
      "date": "2026-09-27",
      "court_type": "covered",
      "slots": [
        {"start_time": "06:00", "status": "unavailable", ...},
        {"start_time": "22:00", "status": "available", ...},
        ...
      ]
    }

Свободным считается слот со status == "available". Статус
"alternative_available" означает, что часть времени свободна, но
меньше запрошенной длительности (например, просили 90 минут, а
реально свободно только 60) — сейчас такие слоты НЕ считаются
полностью свободными и в расписание не попадают. Если хотите
показывать их тоже — см. комментарий у ALSO_COUNT_ALTERNATIVE ниже.

Если прямой запрос вдруг перестанет работать (сайт изменит API,
закроет CORS, потребует авторизацию и т.п.) — автоматически
используется запасной способ через headless-браузер (Playwright),
как было раньше: открыть страницу, перехватить сетевые ответы,
разобрать их той же логикой.

Известное ограничение: у клуба есть открытый и крытый корты, а в
URL это параметр court_type. Подтверждён только "covered" (крытый).
Бот на всякий случай пробует ещё пару частых вариантов названия для
открытого корта (COURT_TYPES ниже) — те, что не подойдут, просто
тихо игнорируются, ничего не сломается. Если знаете точное название
для открытого корта — впишите его в список.
"""

import asyncio
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright

TIME_PATTERN = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")

# ---- прямой API (основной способ) ----
API_URL = "https://api.granpadel.ru/slots/availability"
COURT_TYPES = ["covered", "open"]  # "covered" подтверждён, "open" — предположение
DURATION_MINUTES = 90
ALSO_COUNT_ALTERNATIVE = False  # True — считать "alternative_available" тоже свободным

# ---- запасной способ через браузер ----
BOOKING_URL = "https://granpadel.ru/book"
DEBUG_CAPTURE_FILE = Path(__file__).parent / "debug_schedule_capture.json"
SCHEDULE_KEY_HINTS = ("slot", "schedule", "time", "book", "free", "court", "занят", "своб")

RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def parse_ru_date_text(text: str, today: date) -> Optional[date]:
    """Разбирает дату из текста: «27 сентября», «27.09», «27.09.2026»,
    «сегодня» / «завтра» / «послезавтра». Возвращает None, если не удалось."""
    t = text.strip().lower()

    if "послезавтра" in t:
        return today + timedelta(days=2)
    if "завтра" in t:
        return today + timedelta(days=1)
    if "сегодня" in t:
        return today

    m = re.match(r"(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?", t)
    if m:
        day = int(m.group(1))
        month = RU_MONTHS.get(m.group(2))
        if month:
            year = int(m.group(3)) if m.group(3) else today.year
            try:
                d = date(year, month, day)
                if not m.group(3) and d < today:
                    d = date(year + 1, month, day)
                return d
            except ValueError:
                return None

    m2 = re.match(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?", t)
    if m2:
        day, month = int(m2.group(1)), int(m2.group(2))
        year = int(m2.group(3)) if m2.group(3) else today.year
        try:
            d = date(year, month, day)
            if not m2.group(3) and d < today:
                d = date(year + 1, month, day)
            return d
        except ValueError:
            return None

    return None


def _slots_from_api_payload(data: dict) -> list[str]:
    """Достаёт свободные времена из ответа API — под точную структуру,
    подтверждённую калибровкой 27.09.2026."""
    slots = data.get("slots")
    if not isinstance(slots, list):
        return []
    good_statuses = {"available"}
    if ALSO_COUNT_ALTERNATIVE:
        good_statuses.add("alternative_available")

    found = []
    for item in slots:
        if not isinstance(item, dict):
            continue
        if item.get("status") in good_statuses:
            t = item.get("start_time")
            if isinstance(t, str) and TIME_PATTERN.match(t):
                found.append(t)
    return found


async def _fetch_direct_api(d: date) -> Optional[list[str]]:
    """Основной способ: напрямую стучимся в API сайта, без браузера.

    Заголовки ниже — не для маскировки, а потому что API, похоже,
    иначе отвечает (или не отвечает) без них: сайт различает запрос
    от настоящего браузера и «голый» HTTP-запрос без Referer/Origin/
    User-Agent. Без этих заголовков запрос молча не проходил и бот
    скатывался в более медленный запасной способ через браузер."""
    all_slots: list[str] = []
    got_any_response = False

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
        ),
        "Referer": "https://granpadel.ru/book",
        "Origin": "https://granpadel.ru",
        "Accept": "application/json, text/plain, */*",
    }

    async with httpx.AsyncClient(timeout=10, headers=headers) as client:
        for court_type in COURT_TYPES:
            try:
                resp = await client.get(
                    API_URL,
                    params={
                        "date": d.isoformat(),
                        "court_type": court_type,
                        "duration_minutes": DURATION_MINUTES,
                    },
                )
                if resp.status_code != 200:
                    print(f"[schedule.py] API {court_type}: статус {resp.status_code}, тело: {resp.text[:200]!r}")
                    continue
                data = resp.json()
                got_any_response = True
                all_slots.extend(_slots_from_api_payload(data))
            except Exception as e:
                print(f"[schedule.py] API {court_type}: исключение {e!r}")
                continue  # этот court_type не подошёл — пробуем следующий, не падаем

    if not got_any_response:
        return None  # API вообще не ответил — пойдём в запасной способ
    return sorted(set(all_slots))


# ==================== ЗАПАСНОЙ СПОСОБ (браузер) ====================

def _looks_like_schedule(url: str, data) -> bool:
    url_lower = url.lower()
    if any(hint in url_lower for hint in SCHEDULE_KEY_HINTS):
        return True
    dumped = json.dumps(data, ensure_ascii=False).lower()
    if TIME_PATTERN.search(dumped) and any(hint in dumped for hint in SCHEDULE_KEY_HINTS):
        return True
    return False


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
    await page.wait_for_timeout(2000)

    DEBUG_CAPTURE_FILE.write_text(
        json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    slots = []
    for item in captured:
        slots.extend(_slots_from_api_payload(item["data"]))
    return sorted(set(slots)) if slots else None


async def _scrape_via_dom_text(page) -> Optional[list[str]]:
    text = await page.inner_text("body")
    times = sorted(set(m.group() for m in TIME_PATTERN.finditer(text)))
    return times or None


async def _fetch_via_browser(date_str_for_click: str) -> Optional[list[str]]:
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            slots = await _scrape_via_network(page)

            if not slots:
                try:
                    await page.get_by_text(date_str_for_click, exact=False).first.click(timeout=3000)
                    await page.wait_for_load_state("networkidle")
                    slots = await _scrape_via_network(page)
                except Exception:
                    pass

            if not slots:
                slots = await _scrape_via_dom_text(page)

            await browser.close()
            return slots
    except Exception as e:
        print(f"[schedule.py] Запасной способ (браузер) тоже не удался: {e!r}")
        return None


# ==================== ПУБЛИЧНАЯ ФУНКЦИЯ ====================

async def fetch_schedule_auto(date_text: str) -> Optional[list[str]]:
    """
    date_text — как есть от пользователя: «27 сентября», «сегодня»,
    «24.09» и т.п. Сначала пробуем прямой API (быстро), если не
    получилось разобрать дату или API не ответил — пробуем браузер.
    """
    today = date.today()
    d = parse_ru_date_text(date_text, today)

    if d is not None:
        slots = await _fetch_direct_api(d)
        if slots is not None:
            return slots

    # дату не разобрали ИЛИ прямой API не ответил — пробуем браузер
    return await _fetch_via_browser(date_text)


def parse_manual_slots(raw_text: str) -> list[str]:
    """Разбирает часы, введённые человеком через запятую — аварийный
    запасной вариант, если и API, и браузер не сработали."""
    parts = raw_text.replace("\n", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


AUTO_FETCH_ENABLED = True


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else "сегодня"
    print(f"Пробую получить расписание на «{date_arg}»…")
    result = asyncio.run(fetch_schedule_auto(date_arg))
    print("Результат:", result)
    if DEBUG_CAPTURE_FILE.exists():
        print(f"(файл {DEBUG_CAPTURE_FILE} остался от предыдущего запуска запасного способа)")
