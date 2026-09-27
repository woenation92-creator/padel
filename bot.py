"""
Telegram-бот «Грань: расписание».

Логика для пользователя:
1. Присылаете подложку (картинку) в бот.
2. Бот спрашивает дату.
3. Бот сам заходит на сайт клуба headless-браузером и забирает
   свободные часы на эту дату (ручной ввод — только аварийный
   запасной вариант, если сайт не ответил).
4. Бот присылает готовую картинку с расписанием — можно сразу постить
   в сторис/пост.

Запуск:
    pip install -r requirements.txt
    cp .env.example .env      # и впишите туда токен бота
    python bot.py
"""

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, BufferedInputFile, FSInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone, date

from schedule import fetch_schedule_auto, parse_manual_slots, AUTO_FETCH_ENABLED, DEBUG_CAPTURE_FILE
from image_composer import (
    compose_schedule_image,
    LOGO_PATH,
    CUSTOM_FONT_BOLD,
    CUSTOM_FONT_MEDIUM,
    CUSTOM_FONT_ITALIC,
)
import library

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Махачкала — UTC+3 круглый год, без перехода на летнее/зимнее время
CLUB_TZ = timezone(timedelta(hours=3))

RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _format_ru_date(d: date) -> str:
    return f"{d.day} {RU_MONTHS[d.month - 1]}"


def _date_keyboard():
    today = datetime.now(CLUB_TZ).date()
    builder = InlineKeyboardBuilder()
    for offset, label in ((0, "Сегодня"), (1, "Завтра"), (2, "Послезавтра")):
        d = today + timedelta(days=offset)
        builder.button(text=f"{label} ({_format_ru_date(d)})", callback_data=f"pickdate:{d.isoformat()}")
    builder.adjust(1)
    return builder.as_markup()


class Flow(StatesGroup):
    waiting_photo = State()
    waiting_date = State()
    waiting_slots = State()


def register_handlers(dp: Dispatcher) -> None:

    @dp.message(CommandStart())
    async def start(message: Message, state: FSMContext):
        await state.clear()
        await state.set_state(Flow.waiting_photo)
        await message.answer(
            "Привет! Пришли подложку (картинку), на которую нужно "
            "наложить расписание свободных часов.\n\n"
            "Команда /menu — выбрать готовый логотип, шрифт или подложку "
            "по номеру, без загрузки файлов.\n"
            "Команда /cancel — начать заново в любой момент.\n"
            "Команда /debug <дата> — проверить, как бот видит сайт "
            "(для настройки, не для обычного использования)."
        )

    @dp.message(F.text == "/cancel")
    async def cancel(message: Message, state: FSMContext):
        await state.clear()
        await state.set_state(Flow.waiting_photo)
        await message.answer("Ок, начнём заново. Пришли подложку.")

    @dp.message(F.text.startswith("/debug"))
    async def debug_schedule(message: Message):
        """
        Калибровка прямо из телефона, без терминала и компьютера.
        Пишете боту, например: /debug 24 сентября
        Бот сходит на сайт, покажет что нашёл, и пришлёт файлом всё,
        что перехватил по сети — это и есть debug_schedule_capture.json,
        описанный в README. Его можно переслать разработчику или в чат
        с Клодом, чтобы дописать разбор под реальный сайт.
        """
        parts = message.text.split(maxsplit=1)
        date_arg = parts[1] if len(parts) > 1 else "сегодня"

        await message.answer(f"Захожу на сайт, проверяю расписание на «{date_arg}»…")
        try:
            slots = await fetch_schedule_auto(date_arg)
        except Exception as e:
            log.exception("Ошибка в /debug")
            await message.answer(f"Упал с ошибкой: {e!r}")
            slots = None

        if slots:
            await message.answer(f"Нашёл часы: {', '.join(slots)}")
        else:
            await message.answer(
                "Ничего не нашёл автоматически. Сейчас пришлю файл с тем, что "
                "вообще удалось перехватить по сети — по нему можно понять, "
                "как поправить разбор (см. README, раздел «Калибровка»)."
            )

        if DEBUG_CAPTURE_FILE.exists():
            await message.answer_document(
                FSInputFile(str(DEBUG_CAPTURE_FILE)),
                caption="Сырой перехваченный сетевой трафик страницы бронирования.",
            )
        else:
            await message.answer(
                "Файла с перехваченным трафиком нет — похоже, сайт вообще "
                "не открылся (проверьте, что granpadel.ru доступен)."
            )

    @dp.message(F.text == "/menu")
    async def show_menu(message: Message):
        await message.answer(library.menu_text())

    @dp.message(F.text.startswith("/logo"))
    async def pick_logo(message: Message):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await message.answer("Напиши номер, например: /logo 2\nСписок номеров — команда /menu")
            return
        n = int(parts[1].strip())
        item = library.LOGOS.get(n)
        if not item:
            await message.answer(f"Нет варианта номер {n}. Список — команда /menu")
            return
        LOGO_PATH.write_bytes(item["file"].read_bytes())
        await message.answer(f"Готово — логотип «{item['title']}» теперь используется на всех картинках.")

    @dp.message(F.text.startswith("/font"))
    async def pick_font(message: Message):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await message.answer("Напиши номер, например: /font 2\nСписок номеров — команда /menu")
            return
        n = int(parts[1].strip())
        item = library.FONTS.get(n)
        if not item:
            await message.answer(f"Нет варианта номер {n}. Список — команда /menu")
            return
        CUSTOM_FONT_BOLD.write_bytes(item["bold"].read_bytes())
        CUSTOM_FONT_MEDIUM.write_bytes(item["medium"].read_bytes())
        CUSTOM_FONT_ITALIC.write_bytes(item["italic"].read_bytes())
        await message.answer(f"Готово — шрифт «{item['title']}» теперь используется на всех картинках.")

    @dp.message(F.text.startswith("/template"))
    async def pick_template(message: Message, state: FSMContext):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await message.answer("Напиши номер, например: /template 1\nСписок номеров — команда /menu")
            return
        n = int(parts[1].strip())
        item = library.TEMPLATES.get(n)
        if not item:
            await message.answer(f"Нет варианта номер {n}. Список — команда /menu")
            return
        await state.update_data(template_bytes=item["file"].read_bytes())
        await state.set_state(Flow.waiting_date)
        await message.answer(
            f"Взял подложку «{item['title']}». На какую дату расписание?\n"
            "Выбери кнопкой или напиши текстом, например: 24 сентября",
            reply_markup=_date_keyboard(),
        )

    # ---- загрузка своих файлов: шрифт/лого с подписью-командой к файлу ----
    ASSET_UPLOAD_TARGETS = {
        "/set_logo": LOGO_PATH,
        "/set_font_bold": CUSTOM_FONT_BOLD,
        "/set_font_medium": CUSTOM_FONT_MEDIUM,
        "/set_font_italic": CUSTOM_FONT_ITALIC,
    }

    def _is_asset_upload(message: Message) -> bool:
        """Хендлер должен сработать, только если это документ с нужной
        подписью-командой — иначе пропускаем дальше, к обычной загрузке
        подложки в Flow.waiting_photo."""
        if not message.document:
            return False
        caption = (message.caption or "").strip().split()
        return bool(caption) and caption[0] in ASSET_UPLOAD_TARGETS

    @dp.message(_is_asset_upload)
    async def upload_asset(message: Message):
        caption = message.caption.strip().split()
        target_path = ASSET_UPLOAD_TARGETS[caption[0]]
        file = await message.bot.get_file(message.document.file_id)
        file_bytes_io = await message.bot.download_file(file.file_path)
        target_path.write_bytes(file_bytes_io.read())
        await message.answer(f"Файл сохранён как {target_path.name}. Дальше все картинки будут с ним.")

    @dp.message(F.text == "/reset_assets")
    async def reset_assets(message: Message):
        removed = []
        for p in (LOGO_PATH, CUSTOM_FONT_BOLD, CUSTOM_FONT_MEDIUM, CUSTOM_FONT_ITALIC):
            if p.exists():
                p.unlink()
                removed.append(p.name)
        if removed:
            await message.answer("Сбросил к настройкам по умолчанию: " + ", ".join(removed))
        else:
            await message.answer("И так всё было по умолчанию, сбрасывать нечего.")

    @dp.message(Flow.waiting_photo, F.photo | F.document)
    async def got_photo(message: Message, state: FSMContext):
        if message.photo:
            file_id = message.photo[-1].file_id
        else:
            # разрешаем и файл-картинку, если прислали как документ
            if not (message.document.mime_type or "").startswith("image/"):
                await message.answer("Это не похоже на картинку. Пришли фото или файл-изображение.")
                return
            file_id = message.document.file_id

        await state.update_data(photo_file_id=file_id)
        await state.set_state(Flow.waiting_date)
        await message.answer(
            "Принял картинку. На какую дату расписание?\n"
            "Выбери кнопкой или напиши текстом, например: 24 сентября",
            reply_markup=_date_keyboard(),
        )

    @dp.message(Flow.waiting_photo)
    async def not_a_photo(message: Message):
        await message.answer("Пришли, пожалуйста, картинку (фото или файл-изображение).")

    async def _process_date(message: Message, state: FSMContext, date_label: str):
        """Общая логика после того, как дата известна — не важно, пришла
        она текстом или нажатием кнопки."""
        await state.update_data(date_label=date_label)

        auto_slots = None
        if AUTO_FETCH_ENABLED:
            await message.answer("Пробую подтянуть расписание автоматически…")
            try:
                auto_slots = await fetch_schedule_auto(date_label)
            except Exception:
                log.exception("Ошибка автополучения расписания")
                auto_slots = None

        if auto_slots:
            await finish(message, state, auto_slots)
        else:
            await state.set_state(Flow.waiting_slots)
            await message.answer(
                "Не смог сам получить расписание с сайта (бывает — сайт не ответил "
                "вовремя или сменил структуру). Напиши свободные часы через запятую.\n"
                "Например: 11:00, 13:00, 15:30, 18:00, 20:00"
            )

    @dp.message(Flow.waiting_date)
    async def got_date(message: Message, state: FSMContext):
        await _process_date(message, state, message.text.strip())

    @dp.callback_query(Flow.waiting_date, F.data.startswith("pickdate:"))
    async def got_date_button(callback: CallbackQuery, state: FSMContext):
        iso_date = callback.data.split(":", 1)[1]
        d = date.fromisoformat(iso_date)
        date_label = _format_ru_date(d)

        # уберём кнопки под уже обработанным сообщением, чтобы не жали дважды
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await callback.answer(f"Дата: {date_label}")
        await _process_date(callback.message, state, date_label)

    @dp.message(Flow.waiting_slots)
    async def got_slots(message: Message, state: FSMContext):
        slots = parse_manual_slots(message.text or "")
        if not slots:
            await message.answer("Не увидел ни одного времени. Попробуй ещё раз, через запятую.")
            return
        await finish(message, state, slots)

    async def finish(message: Message, state: FSMContext, slots: list[str]):
        data = await state.get_data()
        date_label = data["date_label"]

        await message.answer("Собираю картинку…")

        if "template_bytes" in data:
            template_bytes = data["template_bytes"]
        else:
            photo_file_id = data["photo_file_id"]
            file = await message.bot.get_file(photo_file_id)
            file_bytes_io = await message.bot.download_file(file.file_path)
            template_bytes = file_bytes_io.read()

        try:
            result_bytes = compose_schedule_image(template_bytes, date_label, slots)
        except Exception:
            log.exception("Ошибка сборки изображения")
            await message.answer(
                "Не получилось собрать картинку. Проверь, что подложка — "
                "нормальный JPEG/PNG, и попробуй ещё раз с /cancel."
            )
            return

        await message.answer_photo(
            BufferedInputFile(result_bytes, filename="schedule.png"),
            caption="Готово! Можно постить 🎾",
        )
        await state.clear()
        await state.set_state(Flow.waiting_photo)
        await message.answer("Пришли следующую подложку, если нужно ещё одно расписание.")


async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Скопируйте .env.example в .env и впишите "
            "туда токен, полученный у @BotFather в Telegram."
        )

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    register_handlers(dp)

    log.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
