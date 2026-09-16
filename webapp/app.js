/* Mini App «Расписание». Поведение перенесено из макета Claude Design:
   вкладки Расписание/Календарь, фильтр недель, шторки пары и дня.
   Данные приходят из /api/schedule. */

const tg = window.Telegram?.WebApp;
const SOURCE_URL = "https://www.vstu.ru/student/raspisanie-zanyatiy/";

const TYPES = {
  lek: { label: "Лекция", color: "var(--lek)", tint: "var(--lek-tint)" },
  sem: { label: "Практика", color: "var(--sem)", tint: "var(--sem-tint)" },
  lab: { label: "Лаба", color: "var(--lab)", tint: "var(--lab-tint)" },
};
const TYPE_ORDER = ["lek", "sem", "lab"];
const DOW = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const DOW_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"];

const state = {
  tab: "list", filter: "both", data: null, lessons: new Map(), sheet: null,
  // экран настроек: каталог сайта, открытая шторка выбора и то, что в ней листают
  settings: null, picker: null, level: "", faculty: "", busy: false,
  // «Реальные пары»: показывать только те, что идут на ближайшем повторении
  // недели, — см. realLessons(). Выбор с экрана настроек, живёт в localStorage.
  real: false,
  // заметки: ключ «id пары|дата» -> текст; дата выбранная в шторке пары
  notes: {}, sheetDate: "",
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const paint = (type) => TYPES[type] || TYPES.sem;
const haptic = (style = "light") => tg?.HapticFeedback?.impactOccurred?.(style);

/* ── данные ──────────────────────────────────────────────────────── */

/* Запрос к боту с таймаутом и повторами.

   Приложение живёт за быстрым туннелем, и тот иногда теряет соединение:
   запрос уходит и не доходит никуда. Без таймаута fetch в этом случае висит
   до последнего, и на экране вечно «Загружаем расписание…» — лечилось только
   тем, что человек сам перезапускал мини-апп по нескольку раз.

   Ответ 4xx повторять бессмысленно: это отказ по существу (не та подпись
   Telegram, нет такого файла), а не потеря пакета. */
const API_TIMEOUT_MS = 7000;
const API_ATTEMPTS = 3;

/* ── кэш расписания на устройстве ──────────────────────────────────

   Файл на сайте перевыкладывают раз в несколько дней, а приложение
   открывают по несколько раз в день. Поэтому ответ /api/schedule лежит в
   localStorage: при запуске расписание рисуется из него сразу, до всякой
   сети, а у сервера спрашивается только «не изменилось ли». В запрос уходит
   заголовок If-None-Match с меткой version из прошлого ответа — если файл
   тот же, сервер отвечает 304 без тела и без чтения пар из базы.

   Вторая польза — обрыв связи. Адрес быстрого туннеля меняется при
   переподключении, и запросы из уже открытого окна уходят в никуда; теперь
   на экране в этот момент остаётся расписание из кэша, а не ошибка.

   localStorage бывает недоступен (приватный режим, запрет на данные сайтов)
   и тогда бросает при любом обращении — поэтому все три функции молчаливые:
   без кэша приложение просто работает как раньше. */
const CACHE_KEY = "vstu.schedule";
// Пока кэш моложе этого срока, сервер не спрашиваем вовсе: приложение часто
// сворачивают и открывают снова, и такие серии запросов ничего не приносят.
const CACHE_TRUST_MS = 5 * 60 * 1000;
const NOT_MODIFIED = Symbol("not-modified");

function readCache() {
  try {
    const box = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
    return box && box.data && box.etag ? box : null;
  } catch (err) {
    return null;
  }
}

function writeCache(box) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(box));
  } catch (err) {
    /* переполнен или запрещён — обойдёмся без кэша */
  }
}

function dropCache() {
  try {
    localStorage.removeItem(CACHE_KEY);
  } catch (err) {
    /* см. выше */
  }
}

/* Режим показа списка — выбор с экрана настроек. Хранится отдельно от кэша
   расписания: переключение не должно ронять сам кэш и заставлять приложение
   идти в сеть. Обращения молчаливые по той же причине, что и у кэша. */
const REAL_KEY = "vstu.real";

function readReal() {
  try {
    return localStorage.getItem(REAL_KEY) === "1";
  } catch (err) {
    return false;
  }
}

function writeReal(on) {
  try {
    localStorage.setItem(REAL_KEY, on ? "1" : "0");
  } catch (err) {
    /* см. выше */
  }
}

/* Заметки к парам. Ключ — «id пары|дата», как их присылает сервер: заметка
   висит на конкретном занятии конкретного числа, а не на паре вообще.

   Копия в localStorage нужна для первого кадра: расписание поднимается из
   своего кэша мгновенно, и заметки должны появиться вместе с ним, а не через
   секунду после ответа сервера. Дальше список перезаписывается ответом
   /api/notes — он и есть истина. */
const NOTES_KEY = "vstu.notes";

function readNotes() {
  try {
    const box = JSON.parse(localStorage.getItem(NOTES_KEY) || "null");
    return box && typeof box === "object" ? box : {};
  } catch (err) {
    return {};
  }
}

function writeNotes(notes) {
  try {
    localStorage.setItem(NOTES_KEY, JSON.stringify(notes));
  } catch (err) {
    /* переполнен или запрещён — обойдёмся без кэша */
  }
}

const noteKey = (lessonId, iso) => `${lessonId}|${iso}`;
const noteFor = (lessonId, iso) => state.notes[noteKey(lessonId, iso)] || "";

/* Заметки одной пары по её датам, в порядке занятий. */
function notesOfLesson(lesson) {
  return lesson.dates
    .map((iso) => ({ iso, text: noteFor(lesson.id, iso) }))
    .filter((item) => item.text);
}

async function loadNotes(groupId = "") {
  const query = groupId ? `?group_id=${groupId}` : "";
  const data = await api(`/api/notes${query}`);
  const notes = {};
  (data.notes || []).forEach((item) => {
    notes[noteKey(item.lesson_id, item.date)] = item.text;
  });
  state.notes = notes;
  writeNotes(notes);
}

/* Сохранение заметки: пустой текст сервер понимает как «удалить».

   На экране изменение показываем сразу, не дожидаясь ответа: человек уже
   закрыл клавиатуру, и ждать сеть ему незачем. Если запрос не дошёл — правим
   обратно и говорим об этом. */
async function saveNote(lessonId, iso, text) {
  const key = noteKey(lessonId, iso);
  const before = state.notes[key] || "";
  const clean = text.trim();
  if (clean === before) return;

  if (clean) state.notes[key] = clean;
  else delete state.notes[key];
  writeNotes(state.notes);
  render();

  try {
    await api("/api/note", {
      method: "POST",
      body: JSON.stringify({ lesson_id: lessonId, date: iso, text: clean }),
    });
  } catch (err) {
    if (before) state.notes[key] = before;
    else delete state.notes[key];
    writeNotes(state.notes);
    render();
    showToast("Заметка не сохранилась: " + explainFailure(err));
  }
}

async function api(path, options = {}) {
  let lastError;
  for (let attempt = 1; attempt <= API_ATTEMPTS; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
    try {
      const headers = Object.assign(
        { "Content-Type": "application/json" }, options.headers || {});
      if (tg?.initData) headers["X-Telegram-Init-Data"] = tg.initData;
      const resp = await fetch(path,
        Object.assign({}, options, { headers, signal: controller.signal }));
      // 304 — расписание не менялось, тела в ответе нет. Проверка идёт до
      // resp.ok: там только 200-299, и иначе этот ответ ушёл бы в ошибку.
      if (resp.status === 304) return NOT_MODIFIED;
      if (!resp.ok) {
        const err = new Error(await resp.text() || resp.statusText);
        err.final = resp.status >= 400 && resp.status < 500;
        throw err;
      }
      return await resp.json();
    } catch (err) {
      lastError = err.name === "AbortError"
        ? new Error("сервер не ответил вовремя")
        : err;
      if (err.final || attempt === API_ATTEMPTS) break;
      // Экран ждёт молча почти двадцать секунд — говорим, что происходит,
      // иначе повторы неотличимы от зависания.
      document.dispatchEvent(new CustomEvent("api-retry", { detail: attempt + 1 }));
      await new Promise((done) => setTimeout(done, 500 * attempt));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError;
}

function applyData(data) {
  state.data = data;
  state.lessons = new Map();
  if (!data.empty) {
    data.weeks.forEach((week) => week.days.forEach((day) =>
      day.lessons.forEach((lesson) => state.lessons.set(lesson.id, lesson))));
  }
  render();
}

async function loadSchedule(groupId, etag = "") {
  const query = groupId ? `?group_id=${groupId}` : "";
  const data = await api(`/api/schedule${query}`,
    etag ? { headers: { "If-None-Match": etag } } : {});

  if (data === NOT_MODIFIED) {
    // На экране уже то же самое — только продлеваем срок доверия кэшу.
    const box = readCache();
    if (box) {
      box.checkedAt = Date.now();
      writeCache(box);
    }
    return;
  }

  applyData(data);
  if (data.empty || !data.version) {
    // Группа ещё не выбрана — хранить нечего, а прошлый кэш уже не про неё.
    dropCache();
    return;
  }
  writeCache({ etag: data.version, checkedAt: Date.now(), data });
}

/* ── шапка ───────────────────────────────────────────────────────── */

function renderTopbar() {
  const bar = $("topbar");
  bar.replaceChildren();
  const data = state.data;
  const loaded = data && !data.empty;

  const row = el("div", "topbar-row");
  const titles = el("div", "title-col");

  // Группа выбирается в настройках, поэтому в шапке это просто заголовок.
  // Дефис в названии заменён тонким пробелом — так в макете: «САПР 1.4».
  let title = "Расписание";
  let sub = "Группа не выбрана";
  if (state.tab === "settings") {
    title = "Настройки";
    sub = "";
  } else if (loaded) {
    title = data.group.name.replace("-", " ");
    sub = [data.group.level_title, data.group.course ? `${data.group.course} курс` : "",
      data.group.faculty, data.semester.title].filter(Boolean).join(" · ");
  }
  titles.append(el("div", "screen-title", title));
  if (sub) titles.append(el("div", "subtitle", sub));
  row.append(titles);

  if (loaded) {
    const now = el("div", "now-col");
    now.append(el("div", "now-label", "СЕЙЧАС"),
      el("div", "now-week", `Неделя ${data.semester.current_week}`));
    row.append(now);
  }
  bar.append(row);

  if (state.tab === "settings") {
    syncTopbarHeight();
    return;
  }

  if (state.tab === "list" && loaded) {
    const seg = el("div", "seg");
    [["both", "Обе недели"], ["1", "1-я"], ["2", "2-я"]].forEach(([key, label]) => {
      const btn = el("button", state.filter === key ? "on" : null, label);
      btn.onclick = () => { haptic(); state.filter = key; render(); };
      seg.append(btn);
    });
    bar.append(seg);
  } else if (loaded) {
    const busy = el("div", "busy-row");
    busy.append(el("span", "label", "Занято дней:"),
      el("span", "value", `${data.busy.days} / ${data.busy.study_days}`),
      el("span", "year", String(new Date(data.semester.start).getFullYear())));
    bar.append(busy);
  }

  if (!loaded) {
    syncTopbarHeight();
    return;
  }

  const legend = el("div", "legend");
  TYPE_ORDER.forEach((type) => {
    const item = el("div", "legend-item");
    const dot = el("span", "legend-dot");
    dot.style.background = paint(type).color;
    item.append(dot, el("span", "legend-label", paint(type).label));
    legend.append(item);
  });
  bar.append(legend);
}

/* ── вкладка «Расписание» ────────────────────────────────────────── */

/* Пары дня, которые идут на этом повторении недели.

   В шаблоне учебного отдела две пары могут делить один слот: у САПР-1.4 во
   вторник недели 1 стоят и лаба (29.09, 27.10, …), и лекция (15.09, 13.10,
   …) — обе в неделе 1, но в конкретный вторник идёт ровно одна. Приложение
   рисует такой слот блоком «2 ПАРЫ», и какая будет на самом деле — не видно.

   В режиме «Реальные пары» остаются те, у которых есть дата внутри границ
   недели (week.from … week.to — ближайшее её повторение, оно же подписано в
   шапке). Даты приходят строками «ГГГГ-ММ-ДД», поэтому сравниваются как есть.

   Пары без дат остаются всегда: расчёт мог не найти ни одной (файл без
   колонок с числами месяцев), и спрятать такую пару хуже, чем показать. */
function realLessons(week, day) {
  if (!state.real || !week.from || !week.to) return day.lessons;
  return day.lessons.filter((lesson) => !lesson.dates.length
    || lesson.dates.some((on) => on >= week.from && on <= week.to));
}

/* Подпись дня в режиме «реальные пары»: одна дата вместо перечня всех дат
   этого дня за месяц («01 · 15 · 29 сент»). Берём её у самих пар — так она
   точно про то, что показано ниже. */
function realDayLabel(week, day) {
  for (const lesson of day.lessons) {
    const hit = lesson.dates.find((on) => on >= week.from && on <= week.to);
    if (hit) return shortDate(hit);
  }
  return day.dates;
}

function groupByTime(lessons) {
  const order = [], map = new Map();
  lessons.forEach((lesson) => {
    const key = `${lesson.start}|${lesson.end}|${lesson.slot}`;
    if (!map.has(key)) { map.set(key, []); order.push(key); }
    map.get(key).push(lesson);
  });
  return order.map((key) => map.get(key));
}

/* Плашка заметки — из макета: тонкая полоса слева, дата мелким моноширинным
   и сам текст. В шторке дня дата не нужна: она там и так в заголовке. */
function noteLine(dateLabel, text) {
  const row = el("div", "note");
  row.append(el("span", "note-bar"));
  const body = el("div", "note-body");
  if (dateLabel) body.append(el("div", "note-date", dateLabel));
  body.append(el("div", "note-text", text));
  row.append(body);
  return row;
}

/* Короткое сообщение внизу экрана: сейчас — единственное место, где человеку
   нужно сказать, что правка не уехала на сервер. */
function showToast(text) {
  document.querySelector(".toast")?.remove();
  const box = el("div", "toast", text);
  document.body.append(box);
  setTimeout(() => box.remove(), 3500);
}

function lessonNode(lesson) {
  const node = el("button", "lesson");
  const bar = el("div", "lesson-bar");
  bar.style.background = paint(lesson.type).color;

  const main = el("div", "lesson-main");
  main.append(el("div", "lesson-title", lesson.title));

  const meta = el("div", "lesson-meta");
  const badge = el("span", "type-badge", paint(lesson.type).label);
  badge.style.background = paint(lesson.type).color;
  meta.append(badge);
  if (lesson.room) meta.append(el("span", "lesson-room", lesson.room));
  if (lesson.teacher) meta.append(el("span", "lesson-teacher", lesson.teacher));
  main.append(meta);

  if (lesson.dates.length) {
    // в карточке показываем не больше шести дат, полный список — в шторке
    const shown = lesson.dates.slice(0, 6).map(shortDate).join(", ");
    const tail = lesson.dates.length > 6 ? ` +${lesson.dates.length - 6}` : "";
    main.append(el("div", "lesson-dates", shown + tail));
  } else if (lesson.note) {
    main.append(el("div", "lesson-dates", lesson.note));
  }

  const notes = notesOfLesson(lesson);
  if (notes.length) {
    const box = el("div", "notes");
    notes.forEach((item) => box.append(noteLine(shortDate(item.iso), item.text)));
    main.append(box);
  }

  node.append(bar, main);
  node.onclick = () => { haptic(); openLessonSheet(lesson); };
  return node;
}

function renderList() {
  const view = $("view");
  const wrap = el("div", "list");
  const weeks = state.data.weeks.filter((w) => state.filter === "both" || String(w.id) === state.filter);

  weeks.forEach((week) => {
    // пустые дни в финальном макете скрыты; в режиме «реальные пары» день
    // может опустеть и здесь — значит на этой неделе он свободен
    const days = week.days
      .map((day) => Object.assign({}, day, { lessons: realLessons(week, day) }))
      .filter((day) => day.lessons.length);
    const total = days.reduce((sum, day) => sum + day.lessons.length, 0);

    const block = el("div", "week");
    const head = el("div", `week-head${week.id === 2 ? " w2" : ""}`);
    head.append(el("span", "label", week.label), el("span", "range", week.range),
      // счётчик с сервера считает весь шаблон недели, после фильтра он другой
      el("span", "count", state.real ? pluralPairs(total).toLowerCase() : week.count));
    block.append(head);

    if (!days.length) {
      const empty = el("div", "day empty");
      const line = el("div", "no-lessons");
      line.append(el("span", "t1", "Пар нет"), el("span", "t2", "вся неделя свободна"));
      empty.append(line);
      block.append(empty);
    }

    days.forEach((day) => {
      const card = el("div", `day${day.is_today ? " today" : ""}`);
      const head2 = el("div", "day-head");
      head2.append(el("span", "short", day.short), el("span", "name", day.name));
      if (day.is_today) head2.append(el("span", "badge", "СЕГОДНЯ"));
      head2.append(el("span", "dates", state.real ? realDayLabel(week, day) : day.dates));
      card.append(head2);

      const slots = el("div", "slots");
      groupByTime(day.lessons).forEach((items) => {
        const first = items[0];
        const sameType = items.every((x) => x.type === first.type);
        const slot = el("div", `slot${sameType ? "" : " mixed"}`);
        if (sameType) slot.style.background = paint(first.type).tint;

        const time = el("div", "slot-time");
        time.append(el("div", "start", first.start), el("div", "end", first.end),
          el("div", "num", first.slot));
        if (items.length > 1) time.append(el("div", "split", pluralPairs(items.length)));

        const list = el("div", "slot-items");
        items.forEach((lesson) => list.append(lessonNode(lesson)));

        slot.append(time, list);
        slots.append(slot);
      });
      card.append(slots);
      block.append(card);
    });
    wrap.append(block);
  });

  const foot = el("div", "foot");
  const link = el("a", null, "источник: расписание ВолгГТУ");
  link.href = SOURCE_URL;
  link.target = "_blank";
  link.rel = "noopener";
  foot.append(link);
  wrap.append(foot);

  view.replaceChildren(wrap);
}

/* ── вкладка «Календарь» ─────────────────────────────────────────── */

function renderCalendar() {
  const view = $("view");
  const wrap = el("div", "cal");
  const index = state.data.index;
  const today = state.data.semester.today;

  state.data.months.forEach((month) => {
    const card = el("div", "month");
    const head = el("div", "month-head");

    const first = new Date(Date.UTC(month.year, month.month - 1, 1));
    const daysInMonth = new Date(Date.UTC(month.year, month.month, 0)).getUTCDate();
    let busyCount = 0;
    for (let d = 1; d <= daysInMonth; d++) {
      if (index[iso(month.year, month.month, d)]) busyCount++;
    }

    head.append(el("span", "name", month.name), el("span", "year", String(month.year)),
      el("span", "busy", `${busyCount} занято`));
    card.append(head);

    const dowRow = el("div", "dow-row");
    DOW.forEach((label, i) => dowRow.append(el("div", i > 4 ? "weekend" : null, label)));
    card.append(dowRow);

    const cells = el("div", "cells");
    const lead = (first.getUTCDay() + 6) % 7;
    for (let i = 0; i < lead; i++) cells.append(el("div", "cell pad"));

    for (let d = 1; d <= daysInMonth; d++) {
      const key = iso(month.year, month.month, d);
      const ids = index[key] || [];
      const isSunday = new Date(Date.UTC(month.year, month.month - 1, d)).getUTCDay() === 0;
      const classes = ["cell"];
      if (ids.length) classes.push("busy");
      else if (isSunday) classes.push("sun");
      if (key === today) classes.push("today");

      const cell = el("button", classes.join(" "));
      cell.append(el("div", "num", String(d)));

      if (ids.length) {
        const dots = el("div", "cell-dots");
        ids.map((id) => state.lessons.get(id)).filter(Boolean)
          .sort((a, b) => TYPE_ORDER.indexOf(a.type) - TYPE_ORDER.indexOf(b.type))
          .forEach((lesson) => {
            const dot = el("span");
            dot.style.background = paint(lesson.type).color;
            dots.append(dot);
          });
        cell.append(dots);
      }
      cell.onclick = () => { haptic(); openDaySheet(key, ids); };
      cells.append(cell);
    }
    card.append(cells);
    wrap.append(card);
  });

  wrap.append(el("div", "foot", "один кружок — одна пара, цвет — тип занятия"));
  view.replaceChildren(wrap);
}

/* ── шторки ──────────────────────────────────────────────────────── */

function openSheet(build) {
  const root = $("sheet-root");
  root.hidden = false;
  root.replaceChildren();

  const scrim = el("div", "scrim");
  scrim.onclick = closeSheet;
  const sheet = el("div", "sheet");
  sheet.append(el("div", "grabber"));
  build(sheet);

  const close = el("button", "close-btn", "Закрыть");
  close.onclick = closeSheet;
  sheet.append(close);

  root.append(scrim, sheet);
  state.sheet = true;
  tg?.BackButton?.show?.();
}

function closeSheet() {
  // Поле заметки исчезает вместе со шторкой, и blur в этот момент приходит не
  // во всех браузерах — забираем текст сами, пока элемент ещё на месте.
  const input = document.querySelector(".note-input");
  if (input && input.dataset.lesson) {
    saveNote(Number(input.dataset.lesson), input.dataset.date, input.value);
  }
  $("sheet-root").hidden = true;
  $("sheet-root").replaceChildren();
  state.sheet = null;
  tg?.BackButton?.hide?.();
}

/* Даты занятия и заметка на выбранную дату — сердце шторки пары.

   Дата выбирается чипом: по умолчанию ближайшая будущая (а если занятия
   кончились — последняя). На чипах с заметкой стоит точка, так что видно,
   где что записано, не перебирая даты по одной.

   Заметка сохраняется, когда человек уходит из поля: на каждый набранный
   символ ходить на сервер незачем. Шторка закрывается — правка тоже
   сохраняется (см. closeSheet). */
function datesAndNote(lesson) {
  const box = el("div", "dates-note");
  const dates = lesson.dates;
  const today = state.data?.semester?.today || "";
  if (!dates.includes(state.sheetDate)) {
    state.sheetDate = dates.find((iso) => iso >= today) || dates[dates.length - 1];
  }
  let editing = false;

  const cap = el("div", "sheet-cap");
  cap.append(el("span", null, "ДАТЫ ЗАНЯТИЙ"), el("span", "cap-note", "выберите дату"));
  const chips = el("div", "chips");
  const editor = el("div", "note-editor");

  const paintChips = () => {
    chips.replaceChildren();
    dates.forEach((iso) => {
      const on = iso === state.sheetDate;
      const chip = el("button", `chip pick${on ? " on" : ""}`, shortDate(iso));
      if (noteFor(lesson.id, iso)) chip.append(el("span", "chip-dot"));
      chip.onclick = () => {
        haptic();
        state.sheetDate = iso;
        editing = false;
        paintChips();
        paintEditor();
      };
      chips.append(chip);
    });
  };

  const paintEditor = () => {
    editor.replaceChildren();
    const iso = state.sheetDate;
    const text = noteFor(lesson.id, iso);

    if (!text && !editing) {
      const add = el("button", "note-add");
      add.append(el("span", "plus", "+"), el("span", "label", "Заметка"),
        el("span", "when", shortDate(iso)));
      add.onclick = () => { haptic(); editing = true; paintEditor(); };
      editor.append(add);
      return;
    }

    const head = el("div", "sheet-cap");
    head.append(el("span", null, `ЗАМЕТКА НА ${shortDate(iso)}`));
    if (text) {
      const drop = el("button", "note-drop", "Удалить");
      drop.onclick = () => {
        haptic();
        editing = false;
        saveNote(lesson.id, iso, "");
        paintChips();
        paintEditor();
      };
      head.append(drop);
    }
    editor.append(head);

    if (editing) {
      const input = el("textarea", "note-input");
      input.value = text;
      input.rows = 3;
      input.maxLength = 500;
      input.placeholder = "Что принести, что сдать, ссылки…";
      // сервер узнаёт о правке отсюда и из closeSheet — по этим данным
      input.dataset.lesson = lesson.id;
      input.dataset.date = iso;
      input.onblur = () => {
        editing = false;
        saveNote(lesson.id, iso, input.value);
        paintChips();
        paintEditor();
      };
      editor.append(input);
      setTimeout(() => input.focus(), 60);
    } else {
      const view = el("button", "note-view", text);
      view.onclick = () => { haptic(); editing = true; paintEditor(); };
      editor.append(view);
    }
  };

  paintChips();
  paintEditor();
  box.append(cap, chips, editor);
  return box;
}

function openLessonSheet(lesson) {
  openSheet((sheet) => {
    const tags = el("div", "sheet-tags");
    const badge = el("span", "type-badge", paint(lesson.type).label);
    badge.style.background = paint(lesson.type).color;
    badge.style.padding = "3px 6px";
    badge.style.borderRadius = "6px";
    tags.append(badge, el("span", "sheet-when",
      `неделя ${lesson.week} · ${DOW_FULL[lesson.weekday - 1]}`));
    sheet.append(tags, el("div", "sheet-title", lesson.title));

    const facts = el("div", "facts");
    const timeFact = el("div", "fact");
    timeFact.append(el("div", "cap", "ВРЕМЯ"),
      el("div", "val mono", `${lesson.start} – ${lesson.end}`),
      el("div", "sub mono", `пары ${lesson.slot}`));
    const roomFact = el("div", "fact");
    roomFact.append(el("div", "cap", "АУДИТОРИЯ"),
      el("div", "val", lesson.room || "—"),
      el("div", "sub", lesson.teacher || ""));
    facts.append(timeFact, roomFact);
    sheet.append(facts);

    if (lesson.dates.length) {
      sheet.append(datesAndNote(lesson));
    } else {
      // дат нет — вешать заметку не на что, показываем только текст из файла
      sheet.append(el("div", "sheet-cap", "ДАТЫ ЗАНЯТИЙ"));
      const chips = el("div", "chips");
      chips.append(el("span", "chip", lesson.note || "нет данных"));
      sheet.append(chips);
    }

    if (lesson.note) {
      sheet.append(el("div", "sheet-cap", "В ФАЙЛЕ РАСПИСАНИЯ"));
      sheet.append(el("div", "lesson-dates", lesson.note));
    }
  });
}

function openDaySheet(isoDate, ids) {
  const lessons = ids.map((id) => state.lessons.get(id)).filter(Boolean)
    .sort((a, b) => a.start.localeCompare(b.start));
  const [y, m, d] = isoDate.split("-").map(Number);
  const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay();

  openSheet((sheet) => {
    const head = el("div", "sheet-day-head");
    head.append(el("span", "title", `${d} ${MONTHS_GEN[m - 1]}`),
      el("span", "dow", DOW_FULL[(dow + 6) % 7]));
    if (lessons.length) head.append(el("span", "week", `Неделя ${lessons[0].week}`));
    sheet.append(head);

    if (!lessons.length) {
      sheet.append(el("div", "free-day", "Пар нет — свободный день"));
      return;
    }
    const list = el("div", "sheet-list");
    lessons.forEach((lesson) => {
      const row = el("div", "sheet-lesson");
      row.style.background = paint(lesson.type).tint;

      const time = el("div", "slot-time");
      time.append(el("div", "start", lesson.start), el("div", "end", lesson.end),
        el("div", "num", lesson.slot));

      const bar = el("div", "lesson-bar");
      bar.style.background = paint(lesson.type).color;

      const main = el("div", "lesson-main");
      main.append(el("div", "lesson-title", lesson.title));
      const meta = el("div", "lesson-meta");
      const badge = el("span", "type-badge", paint(lesson.type).label);
      badge.style.background = paint(lesson.type).color;
      meta.append(badge);
      if (lesson.room) meta.append(el("span", "lesson-room", lesson.room));
      if (lesson.teacher) meta.append(el("span", "lesson-teacher", lesson.teacher));
      main.append(meta);

      const note = noteFor(lesson.id, isoDate);
      if (note) main.append(noteLine("", note));

      row.append(time, bar, main);
      list.append(row);
    });
    sheet.append(list);
  });
}

/* ── вкладка «Настройки» ─────────────────────────────────────────── */

/* Уровень -> факультет -> курс — это и есть конкретный файл на сайте
   ВолгГТУ. Выбрали курс — бот скачивает файл и разбирает его, после чего
   в строке «Группа» появляются группы именно из этого файла. */

async function loadSettings() {
  state.settings = await api("/api/settings");
  const picked = state.settings.selected;
  state.level = state.level || picked.level || state.settings.levels[0]?.key || "";
  state.faculty = state.faculty || picked.faculty || "";
  if (!currentFaculty()) state.faculty = facultiesOf(state.level)[0]?.key || "";
}

const levelsOf = () => state.settings?.levels || [];
const facultiesOf = (levelKey) =>
  levelsOf().find((l) => l.key === levelKey)?.faculties || [];
const currentFaculty = () =>
  facultiesOf(state.level).find((f) => f.key === state.faculty) || null;
const currentFiles = () => currentFaculty()?.files || [];
const currentFile = () =>
  currentFiles().find((f) => f.url === state.settings?.selected.url) || null;

function statusLine(file) {
  if (!file) return "";
  if (file.status === "error") return `не загрузилось: ${file.message}`;
  if (file.status === "ok") {
    const when = file.updated ? `, обновлено ${file.updated}` : "";
    return `${file.groups} групп, ${file.lessons} пар${when}`;
  }
  return "ещё не загружено";
}

function settingsRow(label, value, hint, onClick) {
  const row = el("button", "set-row");
  const left = el("div", "set-row-main");
  left.append(el("div", "label", label));
  if (hint) left.append(el("div", "hint", hint));
  row.append(left, el("div", "value", value || "не выбрано"), el("span", "chev"));
  row.onclick = () => { haptic(); onClick(); };
  return row;
}

/* «14–19 сен и 21–26 сен» — границы ближайших повторений обеих недель.
   Берём подписи, уже посчитанные сервером для шапок недель, только без
   пометки «· сейчас»: здесь она не к месту. */
function weekSpans() {
  const weeks = state.data && !state.data.empty ? state.data.weeks : [];
  const ranges = weeks.map((week) => week.range.replace(" · сейчас", ""));
  return ranges.length === 2 ? `${ranges[0]} и ${ranges[1]}` : "";
}

/* Пункт «Список пар»: показывать шаблон недель целиком или только то, что
   идёт на ближайшем повторении каждой недели (см. realLessons). */
function viewCard() {
  const card = el("div", "set-card");
  card.append(el("div", "set-cap", "СПИСОК ПАР"));

  const seg = el("div", "seg");
  [[false, "Все пары"], [true, "Реальные пары"]].forEach(([value, label]) => {
    const btn = el("button", state.real === value ? "on" : null, label);
    btn.onclick = () => {
      haptic();
      state.real = value;
      writeReal(value);
      render();
    };
    seg.append(btn);
  });
  card.append(seg);

  const spans = weekSpans();
  card.append(el("div", "set-hint", state.real
    ? (spans
      ? `Только пары, которые идут ${spans}. Остальные — в «Календаре».`
      : "Только пары, которые идут на ближайших двух неделях.")
    : "Всё, что стоит в файле учебного отдела. Пары, делящие одно время, "
      + "показываются одним блоком — идут они по очереди, в разные даты."));
  return card;
}

/* Пункт «Уведомления»: писать ли в чат, когда расписание группы изменилось.

   Хранится у бота, а не в localStorage: сообщение отправляет он сам, и знать
   об отказе должен он же. Значение приходит в /api/settings. */
function notifyCard() {
  const card = el("div", "set-card");
  card.append(el("div", "set-cap", "УВЕДОМЛЕНИЯ"));
  const on = state.settings.notify !== false;

  const seg = el("div", "seg");
  [[true, "Включены"], [false, "Выключены"]].forEach(([value, label]) => {
    const btn = el("button", on === value ? "on" : null, label);
    btn.onclick = () => {
      haptic();
      state.settings.notify = value;
      render();
      api("/api/notify", { method: "POST", body: JSON.stringify({ on: value }) })
        .catch(() => {
          // не сохранилось — возвращаем переключатель туда, где он был
          state.settings.notify = !value;
          render();
        });
    };
    seg.append(btn);
  });
  card.append(seg);

  card.append(el("div", "set-hint", on
    ? "Бот напишет, когда учебный отдел перевыложит файл и в расписании группы "
      + "что-то изменится: новая пара, другая аудитория или преподаватель."
    : "Бот молчит об изменениях. Расписание в приложении всё равно обновляется само."));
  return card;
}

function renderSettings() {
  const view = $("view");
  const wrap = el("div", "set");
  const settings = state.settings;

  if (!settings) {
    view.replaceChildren(loadingBox("Читаем каталог сайта…"));
    return;
  }

  const levelCard = el("div", "set-card");
  levelCard.append(el("div", "set-cap", "УРОВЕНЬ ОБРАЗОВАНИЯ"));
  const seg = el("div", "seg");
  levelsOf().forEach((level) => {
    const btn = el("button", state.level === level.key ? "on" : null, level.title);
    btn.onclick = () => {
      haptic();
      state.level = level.key;
      state.faculty = facultiesOf(level.key)[0]?.key || "";
      render();
    };
    seg.append(btn);
  });
  levelCard.append(seg);
  wrap.append(levelCard);

  const faculty = currentFaculty();
  const file = currentFile();
  const selectedGroup = settings.groups.find((g) => g.id === settings.selected.group_id);

  const rows = el("div", "set-card set-rows");
  // Подписи есть только у курса: там видно, загружен ли файл и когда обновлялся.
  // Полное название факультета и число групп повторяют то, что и так на виду.
  rows.append(settingsRow("Факультет", faculty ? faculty.short : "", "",
    () => openPicker("faculty")));
  rows.append(settingsRow("Курс", file ? file.title : "", statusLine(file),
    () => openPicker("course")));
  rows.append(settingsRow("Группа", selectedGroup ? selectedGroup.name : "", "",
    () => openPicker("group")));
  wrap.append(rows);

  wrap.append(viewCard());
  wrap.append(notifyCard());

  const note = el("div", "set-note");
  note.append(el("div", null, "Расписание с сайта ВолгГТУ, обновляется само."));
  const link = el("a", null, "раздел расписаний на сайте");
  link.href = SOURCE_URL;
  link.target = "_blank";
  link.rel = "noopener";
  note.append(link);
  wrap.append(note);

  view.replaceChildren(wrap);
}

function optionNode(label, sub, selected, onClick) {
  const btn = el("button", `opt${selected ? " on" : ""}`);
  const text = el("span", "opt-text");
  text.append(el("span", "opt-label", label));
  if (sub) text.append(el("span", "opt-sub", sub));
  btn.append(text, el("span", "opt-ring"));
  btn.onclick = onClick;
  return btn;
}

function openPicker(kind) {
  const settings = state.settings;
  if (!settings) return;

  const build = (sheet) => {
    let title = "";
    const list = el("div", "opt-list");

    if (kind === "faculty") {
      title = "Факультет";
      facultiesOf(state.level).forEach((f) => {
        list.append(optionNode(f.short, f.title, f.key === state.faculty, () => {
          haptic("medium");
          state.faculty = f.key;
          closeSheet();
          render();
        }));
      });
    }

    if (kind === "course") {
      title = "Курс";
      currentFiles().forEach((f) => {
        list.append(optionNode(f.title, statusLine(f), f.url === settings.selected.url,
          () => { haptic("medium"); closeSheet(); pickFile(f); }));
      });
    }

    if (kind === "group") {
      title = "Группа";
      if (!settings.groups.length) {
        list.append(el("div", "free-day", "Сначала выберите курс — бот загрузит файл расписания."));
      }
      settings.groups.forEach((g) => {
        list.append(optionNode(g.name, "", g.id === settings.selected.group_id,
          () => { haptic("medium"); closeSheet(); pickGroup(g); }));
      });
    }

    sheet.append(el("div", "sheet-title", title), list);
  };

  openSheet(build);
}

async function pickFile(file) {
  state.busy = true;
  $("view").replaceChildren(loadingBox("Открываем файл расписания…"));
  try {
    const resp = await api("/api/source", {
      method: "POST",
      body: JSON.stringify({ url: file.url }),
    });
    // settings перечитываем целиком: у источника изменились статус и счётчики
    await loadSettings();
    state.settings.selected.url = resp.source.url;
    state.settings.groups = resp.groups;
    if (resp.source.status === "error") {
      state.settings.selected.group_id = null;
    }
  } catch (err) {
    showError(explainFailure(err));
    state.busy = false;
    return;
  }
  state.busy = false;
  render();
  if (state.settings.groups.length) openPicker("group");
}

async function pickGroup(group) {
  state.busy = true;
  $("view").replaceChildren(loadingBox("Загружаем расписание…"));
  try {
    await api("/api/group", {
      method: "POST",
      body: JSON.stringify({ group_id: group.id }),
    });
    state.settings.selected.group_id = group.id;
    // Группа выбрана — в настройках больше делать нечего, показываем расписание.
    state.tab = "list";
    // Кэш чистим до запроса: если он не дойдёт, на следующем запуске из
    // кэша поднялось бы расписание прежней группы.
    dropCache();
    state.notes = {};
    writeNotes(state.notes);
    await loadSchedule(group.id);
    refreshNotes(group.id);
  } catch (err) {
    showError(explainFailure(err));
  } finally {
    state.busy = false;
  }
}

/* ── каркас ──────────────────────────────────────────────────────── */

function renderTabs() {
  const bar = $("tabbar");
  bar.replaceChildren();
  [["list", "Расписание"], ["cal", "Календарь"], ["settings", "Настройки"]].forEach(([key, label]) => {
    const btn = el("button", state.tab === key ? "on" : null);
    btn.append(el("span", "icon"), el("span", "label", label));
    btn.onclick = () => { haptic(); openTab(key); };
    bar.append(btn);
  });
}

function openTab(key) {
  state.tab = key;
  render();
  // Каталог сайта читается при первом заходе в настройки, а не на старте:
  // тому, у кого группа уже выбрана, эти запросы ни к чему.
  if (key === "settings" && !state.settings) {
    loadSettings()
      .then(render)
      .catch((err) => showError(explainFailure(err), () => openTab("settings")));
  }
}

function render() {
  const data = state.data;
  // Вкладки рисуем всегда: даже когда расписания ещё нет, из приложения
  // нужно попасть в настройки и выбрать его.
  renderTopbar();
  renderTabs();

  if (state.tab === "settings") {
    renderSettings();
  } else if (!data || data.empty) {
    const box = el("div", "state");
    box.append(el("b", null, "Расписание не выбрано"),
      el("div", null, data?.message || "Откройте «Настройки» и выберите факультет, курс и группу."));
    const go = el("button", "close-btn", "Открыть настройки");
    go.onclick = () => { haptic(); openTab("settings"); };
    box.append(go);
    $("view").replaceChildren(box);
  } else if (state.tab === "list") {
    renderList();
  } else {
    renderCalendar();
  }

  syncTopbarHeight();
  // при открытой шторке список остаётся там, где его листали: правка заметки
  // перерисовывает экран под шторкой, и прыжок наверх был бы заметен
  if (!state.sheet) window.scrollTo({ top: 0 });
}

function loadingBox(text) {
  const box = el("div", "state");
  box.append(el("div", "spinner"), el("div", null, text), el("div", "retry-note"));
  return box;
}

function showLoading() {
  $("view").replaceChildren(loadingBox("Загружаем расписание…"));
}

function showError(message, retry) {
  const box = el("div", "state");
  box.append(el("b", null, "Не удалось загрузить"), el("div", null, message));
  if (retry) {
    const again = el("button", "close-btn", "Повторить");
    again.onclick = () => { haptic(); retry(); };
    box.append(again);
  }
  $("view").replaceChildren(box);
}

function iso(y, m, d) {
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}
function pluralPairs(n) {
  const form = n % 10 === 1 && n % 100 !== 11 ? "ПАРА"
    : n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14) ? "ПАРЫ" : "ПАР";
  return `${n} ${form}`;
}
function syncTopbarHeight() {
  const bar = document.getElementById("topbar");
  document.documentElement.style.setProperty("--topbar-h", `${bar.offsetHeight}px`);
}
function shortDate(value) {
  const [, m, d] = value.split("-");
  return `${d}.${m}`;
}

/* Высота видимой части окна по данным самого Telegram.

   100vh внутри мини-аппа больше того, что реально видно: сверху ещё шапка
   клиента, и на короткой странице нижняя панель оказывалась за краем экрана.
   viewportStableHeight — высота без учёта клавиатуры и анимаций, то есть
   именно та, под которую надо верстать. Значение приходит заново на каждое
   изменение окна: разворот мини-аппа, поворот телефона. */
function syncViewportHeight() {
  const height = tg?.viewportStableHeight;
  const app = document.querySelector(".app");
  if (!app || !height) return;
  app.style.minHeight = `${height}px`;
}

async function boot() {
  state.real = readReal();
  if (tg) {
    tg.ready();
    tg.expand();
    tg.setHeaderColor?.("#f6f4f0");
    tg.setBackgroundColor?.("#f6f4f0");
    tg.BackButton?.onClick?.(() => { if (state.sheet) closeSheet(); else tg.close(); });
    tg.onEvent?.("viewportChanged", syncViewportHeight);
    syncViewportHeight();
  }
  await loadInitial();
}

/* Пока идут повторные попытки, пишем об этом прямо в окно загрузки. */
document.addEventListener("api-retry", (event) => {
  const note = document.querySelector(".state .retry-note");
  if (note) {
    note.textContent =
      `Связь неустойчивая, попытка ${event.detail} из ${API_ATTEMPTS}…`;
  }
});

/* Заметки тянем всегда, даже когда расписание взято из кэша: их правят чаще
   расписания, в том числе с другого устройства, а весят они считанные байты.
   Молча — без сети на экране остаются заметки из прошлого запуска. */
function refreshNotes(groupId = "") {
  loadNotes(groupId).then(render).catch(() => {});
}

async function loadInitial() {
  state.notes = readNotes();
  const cached = readCache();
  if (cached) {
    applyData(cached.data);
    refreshNotes();
    if (Date.now() - cached.checkedAt < CACHE_TRUST_MS) return;
    try {
      // Без group_id: группу называет сервер. Если её сменили с другого
      // устройства, метка не совпадёт и придёт уже новое расписание.
      await loadSchedule("", cached.etag);
    } catch (err) {
      /* связи нет — на экране расписание из кэша, заменить его нечем */
    }
    return;
  }

  showLoading();
  try {
    await loadSchedule();
    refreshNotes();
  } catch (err) {
    showError(explainFailure(err), loadInitial);
  }
}

/* Почему не загрузилось — человеческим языком. Сервер на запрос без подписи
   отвечает одинаково, а причины разные: не поднялся мост Telegram или
   страницу открыли в обычном браузере. */
function explainFailure(err) {
  if (!window.Telegram) {
    return "Не загрузился мост Telegram. Закройте приложение и откройте снова.";
  }
  if (!tg?.initData) {
    return "Приложение открыто вне Telegram. Откройте его кнопкой «Расписание» в боте.";
  }
  if (String(err.message).includes("не ответил вовремя")) {
    // Адрес быстрого туннеля меняется при каждом переподключении, и уже
    // открытое окно остаётся на прошлом поддомене: оттуда запросы никуда
    // не приходят. Кнопка в боте к этому времени уже ведёт на новый адрес.
    return "Сервер не отвечает. Скорее всего сменился адрес приложения — "
      + "закройте окно и откройте расписание заново кнопкой в боте.";
  }
  return String(err.message || err);
}

boot();
