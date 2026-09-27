# Этот файл нужен, чтобы Railway/Render (или любой другой хостинг,
# понимающий Docker) сам поставил Python, все зависимости и браузер
# для Playwright — вручную ничего ставить не придётся, платформа всё
# сделает сама при деплое.

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Ставим сам браузер (Chromium) и системные библиотеки под него —
# флаг --with-deps сам подтянет всё нужное для Debian/Ubuntu
RUN playwright install --with-deps chromium

COPY . .

CMD ["python", "bot.py"]
