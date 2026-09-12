# Бот вместе со встроенным веб-сервером Mini App (webapp/ отдаётся из того же процесса).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# bot/webapp/server.py ищет статику в <корень проекта>/webapp — сохраняем раскладку.
COPY bot/ ./bot/
COPY webapp/ ./webapp/
COPY scripts/ ./scripts/

# Сжатые копии статики: aiohttp сам отдаёт app.js.gz вместо app.js, когда
# клиент понимает gzip. Мост Telegram худеет со 116 КБ до 18 — через узкий
# прокси Telegram Desktop это разница между «открылось» и вечной загрузкой.
# Делается только в образе: рядом с исходниками .gz быстро устарел бы.
RUN cd webapp && for f in *.js *.css; do gzip -9 -k -f "$f"; done

CMD ["python", "-m", "bot.main"]
