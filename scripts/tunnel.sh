#!/bin/sh
# SSH-реверс до localhost.run + публикация выданного адреса.
#
# С зарегистрированным ключом (https://admin.localhost.run/) поддомен
# держится между переподключениями, пока жив контейнер. Без ключа скрипт
# работает анонимно, со случайным поддоменом на каждое соединение. Адрес в
# любом случае публикуется в общий том, откуда его читает бот и сам
# переставляет кнопки приложения.
#
# Против двух бед бесплатного localhost.run здесь приняты меры:
#
# * Сессию без трафика он рвёт примерно через полчаса («tunnel inactivity
#   timeout»). Поэтому скрипт раз в PING_EVERY секунд сам стучится в
#   опубликованный адрес: запрос проходит через их край и туннель и
#   считается трафиком.
# * Переподключение с ключом иногда получает адрес только через 2–4 минуты
#   (28.09.2026: 2 мин 13 с и 3 мин 44 с), и всё это время по адресу
#   приложения «no tunnel here». Поэтому попытка, не получившая адрес за
#   KEY_WAIT секунд, обрывается, и следующая идёт без ключа: адрес станет
#   другим, но бот переведёт на него кнопки сам. Следующее переподключение
#   снова пробует ключ.
set -u

STATE_FILE="${STATE_FILE:-/state/url}"
TARGET="${TARGET:-bot:7070}"
KEY_SRC="${KEY_SRC:-/key}"
KEY="/tmp/lhr_key"
# Сколько ждать адрес от сессии с ключом и без него, в секундах.
KEY_WAIT="${KEY_WAIT:-40}"
ANON_WAIT="${ANON_WAIT:-90}"
# Как часто стучаться в свой адрес, в секундах.
PING_EVERY="${PING_EVERY:-300}"
# Адрес, выданный текущей сессией. По нему сторож видит, что ждать нечего.
SESSION_URL="/tmp/session_url"
OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 \
-o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes"

apk add --no-cache openssh-client >/dev/null

# Ключ приходит бинд-монтом с Windows и получает права 0777 — ssh такой
# отвергает («permissions are too open»). Права на самом монте не меняются,
# поэтому работаем с копией.
if [ -f "$KEY_SRC" ]; then
    cp "$KEY_SRC" "$KEY"
    chmod 600 "$KEY"
    HAVE_KEY=1
    echo "[tunnel] ключ найден: поддомен держится между переподключениями"
else
    HAVE_KEY=""
    echo "[tunnel] ключа нет: анонимный туннель, поддомен будет меняться"
fi

# Читает вывод ssh: печатает его в лог контейнера и публикует выданный адрес.
read_session() {
    while IFS= read -r line; do
        printf '%s\n' "$line"
        url=$(printf '%s' "$line" | grep -oE 'https://[a-z0-9.-]+\.lhr\.life' | head -1)
        [ -n "$url" ] || continue
        printf '%s' "$url" > "$SESSION_URL"
        [ "$url" = "$(cat "$STATE_FILE" 2>/dev/null)" ] && continue
        # пишем через временный файл: пустой /state/url означал бы для бота
        # "адреса нет" и вернул бы текстовые экраны вместо приложения
        printf '%s' "$url" > "${STATE_FILE}.tmp" && mv "${STATE_FILE}.tmp" "$STATE_FILE"
        echo "[tunnel] адрес опубликован: $url"
    done
}

# Обрывает сессию, если адрес не пришёл за $1 секунд; $2 — чем её найти в ps.
watchdog() {
    sleep "$1"
    [ -s "$SESSION_URL" ] && return
    echo "[tunnel] адрес не выдан за $1 с — обрываю попытку"
    pkill -f "$2"
}

# Стучится в опубликованный адрес, чтобы localhost.run не счёл туннель
# простаивающим. Ответ не важен: пока туннель лежит, запрос просто не пройдёт.
keep_alive() {
    while true; do
        sleep "$PING_EVERY"
        url=$(cat "$STATE_FILE" 2>/dev/null)
        [ -n "$url" ] && wget -q -T 10 -O /dev/null "$url/api/health" 2>/dev/null
    done
}
keep_alive &

keyed="$HAVE_KEY"
while true; do
    rm -f "$SESSION_URL"
    if [ -n "$keyed" ]; then
        watchdog "$KEY_WAIT" "IdentitiesOnly=yes" &
        guard=$!
        # shellcheck disable=SC2086
        ssh -i "$KEY" -o IdentitiesOnly=yes $OPTS -R "80:${TARGET}" localhost.run 2>&1 | read_session
    else
        watchdog "$ANON_WAIT" "nokey@localhost.run" &
        guard=$!
        # shellcheck disable=SC2086
        ssh $OPTS -R "80:${TARGET}" nokey@localhost.run 2>&1 | read_session
    fi
    kill "$guard" 2>/dev/null

    if [ -s "$SESSION_URL" ]; then
        # сессия работала — в следующий раз снова с ключом, если он есть
        keyed="$HAVE_KEY"
        echo "[tunnel] соединение закрыто, переподключаюсь через 5с"
        sleep 5
    elif [ -n "$keyed" ]; then
        # с ключом адреса не дождались — сразу пробуем без ключа
        keyed=""
        echo "[tunnel] пробую без ключа"
    else
        keyed="$HAVE_KEY"
        echo "[tunnel] адреса нет и без ключа, повтор через 5с"
        sleep 5
    fi
done
