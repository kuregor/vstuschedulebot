/* Mini App «Расписание». Поведение перенесено из макета Claude Design:
   вкладки Расписание/Календарь, фильтр недель, шторки пары и дня.
   Данные приходят из /api/schedule. */

const tg = window.Telegram?.WebApp;
const SOURCE_URL = "https://www.vstu.ru/student/raspisanie-zanyatiy/";

const TYPES = {
  lek: { label: "Лекция", color: "var(--lek)", tint: "var(--lek-tint)" },
  sem: { label: "Семинар", color: "var(--sem)", tint: "var(--sem-tint)" },
  lab: { label: "Лаба", color: "var(--lab)", tint: "var(--lab-tint)" },
  other: { label: "Занятие", color: "var(--other)", tint: "var(--other-tint)" },
};
const TYPE_ORDER = ["lek", "sem", "lab", "other"];
const DOW = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const DOW_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"];

const state = { tab: "list", filter: "both", data: null, lessons: new Map(), sheet: null };

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const paint = (type) => TYPES[type] || TYPES.other;
const haptic = (style = "light") => tg?.HapticFeedback?.impactOccurred?.(style);

/* ── данные ──────────────────────────────────────────────────────── */

async function api(path, options = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  if (tg?.initData) headers["X-Telegram-Init-Data"] = tg.initData;
  const resp = await fetch(path, Object.assign({}, options, { headers }));
  if (!resp.ok) throw new Error(await resp.text() || resp.statusText);
  return resp.json();
}

async function loadSchedule(groupId) {
  const query = groupId ? `?group_id=${groupId}` : "";
  const data = await api(`/api/schedule${query}`);
  state.data = data;
  state.lessons = new Map();
  if (!data.empty) {
    data.weeks.forEach((week) => week.days.forEach((day) =>
      day.lessons.forEach((lesson) => state.lessons.set(lesson.id, lesson))));
  }
  render();
}

/* ── шапка ───────────────────────────────────────────────────────── */

function renderTopbar() {
  const bar = $("topbar");
  bar.replaceChildren();
  const data = state.data;
  if (!data || data.empty) return;

  const row = el("div", "topbar-row");
  const titles = el("div", "title-col");

  const groupBtn = el("button", "group-btn");
  groupBtn.append(el("span", null, data.group.name), el("span", "caret", "▾"));
  groupBtn.onclick = () => { haptic(); openGroupPicker(); };

  const sub = [data.group.level_title, data.group.course ? `${data.group.course} курс` : "",
    data.group.faculty, data.semester.title].filter(Boolean).join(" · ");
  titles.append(groupBtn, el("div", "subtitle", sub));

  const now = el("div", "now-col");
  now.append(el("div", "now-label", "СЕЙЧАС"),
    el("div", "now-week", `Неделя ${data.semester.current_week}`));

  row.append(titles, now);
  bar.append(row);

  if (state.tab === "list") {
    const seg = el("div", "seg");
    [["both", "Обе недели"], ["1", "1-я"], ["2", "2-я"]].forEach(([key, label]) => {
      const btn = el("button", state.filter === key ? "on" : null, label);
      btn.onclick = () => { haptic(); state.filter = key; render(); };
      seg.append(btn);
    });
    bar.append(seg);
  } else {
    const busy = el("div", "busy-row");
    busy.append(el("span", "label", "Занято дней:"),
      el("span", "value", `${data.busy.days} / ${data.busy.study_days}`),
      el("span", "year", String(new Date(data.semester.start).getFullYear())));
    bar.append(busy);
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

function groupByTime(lessons) {
  const order = [], map = new Map();
  lessons.forEach((lesson) => {
    const key = `${lesson.start}|${lesson.end}|${lesson.slot}`;
    if (!map.has(key)) { map.set(key, []); order.push(key); }
    map.get(key).push(lesson);
  });
  return order.map((key) => map.get(key));
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

  node.append(bar, main);
  node.onclick = () => { haptic(); openLessonSheet(lesson); };
  return node;
}

function renderList() {
  const view = $("view");
  const wrap = el("div", "list");
  const weeks = state.data.weeks.filter((w) => state.filter === "both" || String(w.id) === state.filter);

  weeks.forEach((week) => {
    const block = el("div", "week");
    const head = el("div", `week-head${week.id === 2 ? " w2" : ""}`);
    head.append(el("span", "label", week.label), el("span", "range", week.range),
      el("span", "count", week.count));
    block.append(head);

    // пустые дни в финальном макете скрыты
    const days = week.days.filter((day) => day.lessons.length);
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
      head2.append(el("span", "dates", day.dates));
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
  $("sheet-root").hidden = true;
  $("sheet-root").replaceChildren();
  state.sheet = null;
  tg?.BackButton?.hide?.();
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

    sheet.append(el("div", "sheet-cap", "ДАТЫ ЗАНЯТИЙ"));
    const chips = el("div", "chips");
    if (lesson.dates.length) {
      lesson.dates.forEach((d) => chips.append(el("span", "chip", shortDate(d))));
    } else {
      chips.append(el("span", "chip", lesson.note || "нет данных"));
    }
    sheet.append(chips);

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

      row.append(time, bar, main);
      list.append(row);
    });
    sheet.append(list);
  });
}

async function openGroupPicker() {
  const data = await api("/api/groups");
  openSheet((sheet) => {
    sheet.append(el("div", "sheet-title", "Выберите группу"));
    data.levels.filter((level) => level.groups.length).forEach((level) => {
      sheet.append(el("div", "level-title", level.title.toUpperCase()));
      const grid = el("div", "group-grid");
      level.groups.forEach((group) => {
        const chip = el("button",
          `group-chip${group.id === state.data?.group?.id ? " on" : ""}`, group.name);
        chip.onclick = async () => {
          haptic("medium");
          closeSheet();
          showLoading();
          await loadSchedule(group.id);
        };
        grid.append(chip);
      });
      sheet.append(grid);
    });
  });
}

/* ── каркас ──────────────────────────────────────────────────────── */

function renderTabs() {
  const bar = $("tabbar");
  bar.replaceChildren();
  [["list", "Расписание"], ["cal", "Календарь"]].forEach(([key, label]) => {
    const btn = el("button", state.tab === key ? "on" : null);
    btn.append(el("span", "icon"), el("span", "label", label));
    btn.onclick = () => { haptic(); state.tab = key; render(); };
    bar.append(btn);
  });
}

function render() {
  const data = state.data;
  if (data?.empty) {
    $("topbar").replaceChildren();
    $("tabbar").replaceChildren();
    const box = el("div", "state");
    box.append(el("b", null, "Расписание не загружено"),
      el("div", null, "Отправьте боту ссылку на файл .xls командой /import"));
    $("view").replaceChildren(box);
    return;
  }
  renderTopbar();
  renderTabs();
  if (state.tab === "list") renderList(); else renderCalendar();
  syncTopbarHeight();
  window.scrollTo({ top: 0 });
}

function showLoading() {
  const box = el("div", "state");
  box.append(el("div", "spinner"), el("div", null, "Загружаем расписание…"));
  $("view").replaceChildren(box);
}

function showError(message) {
  const box = el("div", "state");
  box.append(el("b", null, "Не удалось загрузить"), el("div", null, message));
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

async function boot() {
  if (tg) {
    tg.ready();
    tg.expand();
    tg.setHeaderColor?.("#f6f4f0");
    tg.setBackgroundColor?.("#f6f4f0");
    tg.BackButton?.onClick?.(() => { if (state.sheet) closeSheet(); else tg.close(); });
  }
  showLoading();
  try {
    await loadSchedule();
  } catch (err) {
    showError(String(err.message || err));
  }
}

boot();
