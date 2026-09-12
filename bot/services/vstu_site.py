"""Каталог расписаний на сайте ВолгГТУ.

Страница https://www.vstu.ru/student/raspisaniya/zanyatiy/ — оглавление: она
ведёт на страницы факультетов, а уже там лежат ссылки на файлы расписаний
(.xls или .xlsx, по одному на курс). Здесь мы обходим только те разделы,
которые нужны боту: семь дневных факультетов бакалавриата и общую страницу
магистратуры. Аспирантура и вечерние факультеты не берутся.

Разбор страниц — регулярными выражениями, а не HTML-парсером: нужный кусок
разметки предельно простой (заголовок раздела и список ссылок на файлы), а
лишняя зависимость этого не стоит.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://www.vstu.ru/student/raspisaniya/zanyatiy/"
PAGE_URL = BASE_URL + "index.php?dep={dep}"

LEVEL_BACHELOR = "bachelor"
LEVEL_MASTER = "master"

MASTER_DEP = "mag"


@dataclass(frozen=True)
class Department:
    dep: str
    title: str  # как на сайте: «Факультет электроники и вычислительной техники»
    short: str  # как в названиях файлов и групп: «ФЭВТ»
    level: str


# Ровно те разделы, которые перечислены в задаче. Список закрытый: на сайте
# есть ещё аспирантура и вечерние факультеты, их бот не показывает.
DEPARTMENTS: list[Department] = [
    Department("fastiv", "Факультет автоматизированных систем, транспорта и вооружений", "ФАСТиВ", LEVEL_BACHELOR),
    Department("fat", "Факультет автомобильного транспорта", "ФАТ", LEVEL_BACHELOR),
    Department("ftkm", "Факультет технологии конструкционных материалов", "ФТКМ", LEVEL_BACHELOR),
    Department("ftpp", "Факультет технологии пищевых производств", "ФТПП", LEVEL_BACHELOR),
    Department("feu", "Факультет экономики и управления", "ФЭУ", LEVEL_BACHELOR),
    Department("fevt", "Факультет электроники и вычислительной техники", "ФЭВТ", LEVEL_BACHELOR),
    Department("htf", "Химико-технологический факультет", "ХТФ", LEVEL_BACHELOR),
    Department(MASTER_DEP, "Магистратура", "", LEVEL_MASTER),
]
DEPARTMENT_BY_DEP = {d.dep: d for d in DEPARTMENTS}

# Короткие имена факультетов встречаются в названиях файлов магистратуры
# («ОН_Магистратура_1 курс ФАСТиВ (УТС,АТП,КТО,СМ).xlsx»).
FACULTY_SHORTS = [d.short for d in DEPARTMENTS if d.short]

LINK_RE = re.compile(
    r'<a[^>]+href="([^"]+\.xlsx?)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)
TAG_RE = re.compile(r"<[^>]+>")
# Курс из подписи: «2 курс», а также «1,2,3,4 курсы по сокращенным прогр.» —
# у такого файла берём первый курс из перечисления, чтобы он встал в начало
# списка, а полное название всё равно видно в настройках целиком.
COURSE_RE = re.compile(r"(\d+)(?:\s*[,–-]\s*\d+)*\s*курс", re.IGNORECASE)
FETCH_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Каталог меняется редко (файл перевыкладывают, ссылки остаются), поэтому
# держим разобранный список в памяти и не ходим на сайт на каждый экран.
CACHE_TTL_SECONDS = 30 * 60


@dataclass(frozen=True)
class SiteFile:
    """Один файл расписания на сайте."""

    dep: str
    dep_title: str
    faculty: str
    level: str
    course: int | None
    title: str  # «2 курс», «2 курс ( гр.228-234)» — то, что видно в списке
    file_name: str
    url: str


@dataclass
class Catalog:
    files: list[SiteFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fetched_at: float = 0.0

    def by_url(self, url: str) -> SiteFile | None:
        for item in self.files:
            if item.url == url:
                return item
        return None


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub("", html)).strip()


def _faculty_from_text(text: str, default: str) -> str:
    """Короткое имя факультета из названия файла («1 курс ФАСТиВ (…)»)."""
    upper = text.upper()
    for short in FACULTY_SHORTS:
        if short.upper() in upper:
            return short
    return default


def parse_department_page(html: str, dep: Department, page_url: str) -> list[SiteFile]:
    """Ссылки на файлы расписаний с одной страницы факультета."""
    out: list[SiteFile] = []
    seen: set[str] = set()
    for href, label in LINK_RE.findall(html):
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)

        file_name = unquote(url.rsplit("/", 1)[-1])
        # Подпись ссылки на сайте — это имя файла: «2 курс ( гр.228-234).xlsx»
        title = _strip_tags(label) or file_name
        title = re.sub(r"\.xlsx?$", "", title, flags=re.IGNORECASE).strip()
        title = re.sub(r"\s+", " ", title)

        course_match = COURSE_RE.search(title) or COURSE_RE.search(file_name)
        course = int(course_match.group(1)) if course_match else None

        out.append(
            SiteFile(
                dep=dep.dep,
                dep_title=dep.title,
                faculty=_faculty_from_text(f"{title} {file_name}", dep.short),
                level=dep.level,
                course=course,
                title=title,
                file_name=file_name,
                url=url,
            )
        )
    return out


async def _fetch_page(session: aiohttp.ClientSession, dep: Department) -> tuple[Department, str]:
    url = PAGE_URL.format(dep=dep.dep)
    async with session.get(url) as resp:
        resp.raise_for_status()
        raw = await resp.read()
    try:
        return dep, raw.decode("utf-8")
    except UnicodeDecodeError:  # на старых страницах сайта встречается cp1251
        return dep, raw.decode("cp1251", errors="replace")


async def fetch_catalog() -> Catalog:
    """Обходит страницы всех нужных разделов и собирает список файлов."""
    catalog = Catalog(fetched_at=time.time())
    async with aiohttp.ClientSession(timeout=FETCH_TIMEOUT) as session:
        tasks = [_fetch_page(session, dep) for dep in DEPARTMENTS]
        for dep, result in zip(
            DEPARTMENTS, await asyncio.gather(*tasks, return_exceptions=True)
        ):
            if isinstance(result, BaseException):
                log.warning("Страница %s не открылась: %s", dep.dep, result)
                catalog.errors.append(f"{dep.title}: {result}")
                continue
            _, html = result
            found = parse_department_page(html, dep, PAGE_URL.format(dep=dep.dep))
            if not found:
                catalog.errors.append(f"{dep.title}: на странице нет ссылок на расписания")
            catalog.files.extend(found)
    return catalog


_cache: Catalog | None = None
_lock = asyncio.Lock()


async def get_catalog(force: bool = False) -> Catalog:
    """Каталог из памяти; при первом обращении и по истечении срока — с сайта."""
    global _cache
    async with _lock:
        fresh = (
            _cache is not None
            and _cache.files
            and time.time() - _cache.fetched_at < CACHE_TTL_SECONDS
        )
        if fresh and not force:
            return _cache  # type: ignore[return-value]
        catalog = await fetch_catalog()
        if not catalog.files and _cache is not None:
            # Сайт недоступен — лучше показать прошлый список, чем пустой экран
            log.warning("Каталог не обновился, отдаём прошлый: %s", catalog.errors)
            return _cache
        _cache = catalog
        return catalog
