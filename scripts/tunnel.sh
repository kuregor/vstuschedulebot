#!/bin/sh
# SSH-реверс до localhost.run + публикация выданного адреса.
#
# Быстрый туннель выдаёт новый поддомен при каждом переподключении. Скрипт
# кладёт актуальный адрес в общий том, откуда его читает бот, — поэтому
# править .env и пересоздавать контейнер после обрыва больше не нужно.
set -u

STATE_FILE="${STATE_FILE:-/state/url}"
TARGET="${TARGET:-bot:7070}"

apk add --no-cache openssh-client >/dev/null

while true; do
    ssh -o StrictHostKeyChecking=accept-new         -o ServerAliveInterval=30         -o ServerAliveCountMax=3         -o ExitOnForwardFailure=yes         -R "80:${TARGET}" nokey@localhost.run 2>&1 |
    while IFS= read -r line; do
        printf '%s
' "$line"
        url=$(printf '%s' "$line" | grep -oE 'https://[a-z0-9.-]+\.lhr\.life' | head -1)
        [ -n "$url" ] || continue
        [ "$url" = "$(cat "$STATE_FILE" 2>/dev/null)" ] && continue
        # пишем через временный файл: пустой /state/url означал бы для бота
        # "адреса нет" и вернул бы текстовые экраны вместо приложения
        printf '%s' "$url" > "${STATE_FILE}.tmp" && mv "${STATE_FILE}.tmp" "$STATE_FILE"
        echo "[tunnel] адрес опубликован: $url"
    done
    echo "[tunnel] соединение закрыто, переподключаюсь через 5с"
    sleep 5
done
