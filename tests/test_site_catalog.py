"""Разбор страниц расписаний сайта ВолгГТУ (по сохранённым кускам разметки).

Запуск:  python -m pytest tests -q
"""
from __future__ import annotations

from bot.services.vstu_site import (
    DEPARTMENT_BY_DEP,
    MASTER_DEP,
    PAGE_URL,
    parse_department_page,
)

FEVT_PAGE = """
<h2>Факультет электроники и вычислительной техники</h2>
<h4>Очная форма (нормативные сроки обучения)</h4>
<ul>
<li><a href="../../../upload/raspisanie/z/ОН_ФЭВТ_1 курс.xls">1 курс.xls</a></li>
<li><a href="../../../upload/raspisanie/z/ОН_ФЭВТ_2 курс.xls">2 курс.xls</a></li>
<li><a href="../../../upload/raspisanie/z/ОН_ФЭВТ_3 курс.xls">3 курс.xls</a></li>
</ul>
<a href="/student/raspisaniya/zanyatiy/index.php?dep=fat">Факультет автомобильного транспорта</a>
"""

FTKM_PAGE = """
<h2>Факультет технологии конструкционных материалов</h2>
<a href="../../../upload/raspisanie/z/ОН_ФТКМ_2 курс ( гр.228-234).xlsx">2 курс ( гр.228-234).xlsx</a>
<a href="../../../upload/raspisanie/z/ОН_ФТКМ_1,2,3,4 курсы по сокращенным прогр..xls">1,2,3,4 курсы по сокращенным прогр..xls</a>
"""

MAG_PAGE = """
<h2>Магистратура</h2>
<a href="../../../upload/raspisanie/z/ОН_Магистратура_1 курс ФАСТиВ (УТС,АТП,КТО,СМ).xlsx">1 курс ФАСТиВ (УТС,АТП,КТО,СМ).xlsx</a>
<a href="../../../upload/raspisanie/z/ОН_Магистратура_2 курс ХТФ.xlsx">2 курс ХТФ.xlsx</a>
"""


def _parse(dep: str, html: str):
    department = DEPARTMENT_BY_DEP[dep]
    return parse_department_page(html, department, PAGE_URL.format(dep=dep))


def test_faculty_page_gives_one_entry_per_course():
    files = _parse("fevt", FEVT_PAGE)
    assert [f.title for f in files] == ["1 курс", "2 курс", "3 курс"]
    assert [f.course for f in files] == [1, 2, 3]
    assert {f.faculty for f in files} == {"ФЭВТ"}
    assert {f.level for f in files} == {"bachelor"}


def test_relative_links_become_absolute():
    first = _parse("fevt", FEVT_PAGE)[0]
    assert first.url == "https://www.vstu.ru/upload/raspisanie/z/ОН_ФЭВТ_1 курс.xls"
    assert first.file_name == "ОН_ФЭВТ_1 курс.xls"


def test_links_to_other_pages_are_not_files():
    # ссылка на страницу соседнего факультета не должна попасть в каталог
    assert all(f.url.lower().endswith((".xls", ".xlsx")) for f in _parse("fevt", FEVT_PAGE))


def test_extra_detail_in_title_is_kept():
    files = _parse("ftkm", FTKM_PAGE)
    titles = [f.title for f in files]
    assert "2 курс ( гр.228-234)" in titles
    # файл сразу на несколько курсов относим к первому из перечисленных
    multi = next(f for f in files if f.title.startswith("1,2,3,4"))
    assert multi.course == 1


def test_master_faculty_comes_from_file_name():
    files = _parse(MASTER_DEP, MAG_PAGE)
    assert [(f.faculty, f.course) for f in files] == [("ФАСТиВ", 1), ("ХТФ", 2)]
    assert {f.level for f in files} == {"master"}
