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

CMD ["python", "-m", "bot.main"]
