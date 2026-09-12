#!/bin/sh
# SSH-реверс до localhost.run + публикация выданного адреса.
#
# С зарегистрированным ключом (https://admin.localhost.run/) поддомен
# постоянный: он привязан к ключу и переживает переподключения. Без ключа
# скрипт работает как раньше — анонимно, со случайным поддоменом при каждом
# соединении. Поэтому адрес всё равно публикуется в общий том, откуда его
# читает бот.
set -u

STATE_FILE="${STATE_FILE:-/state/url}"
TARGET="${TARGET:-bot:7070}"
KEY_SRC="${KEY_SRC:-/key}"
KEY="/tmp/lhr_key"

apk add --no-cache openssh-client >/dev/null

# Ключ приходит бинд-монтом с Windows и получает права 0777 — ssh такой
# отвергает («permissions are too open»). Права на самом монте не меняются,
# поэтому работаем с копией.
if [ -f "$KEY_SRC" ]; then
    cp "$KEY_SRC" "$KEY"
    chmod 600 "$KEY"
    LOGIN="localhost.run"
    IDENTITY="-i $KEY -o IdentitiesOnly=yes"
    echo "[tunnel] ключ найден: постоянный поддомен"
else
    LOGIN="nokey@localhost.run"
    IDENTITY=""
    echo "[tunnel] ключа нет: анонимный туннель, поддомен будет меняться"
fi

while true; do
    # shellcheck disable=SC2086
    ssh $IDENTITY \
        -o StrictHostKeyChecking=accept-new \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 \
        -o ExitOnForwardFailure=yes \
        -R "80:${TARGET}" "$LOGIN" 2>&1 |
    while IFS= read -r line; do
        printf '%s\n' "$line"
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
