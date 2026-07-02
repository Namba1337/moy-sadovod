"""Реестр людей СНТ — источник истины для контактов владельцев.

Человек = dict ``{"id": str, "name": str, "phone": str, "email": str,
"opd_doc": str, "member_doc": str}`` (``opd_doc``/``member_doc`` — необязательные
пути к последним загруженным «Согласие на ОПД»/«Заявление в СНТ»: эти два
документа привязаны к человеку, а не к конкретному участку, в отличие от
выписки ЕГРН, которая относится к конкретному объекту недвижимости и в
реестре не кэшируется).
Запись владельца в группе ссылается на человека через поле ``person_id`` и
дополнительно хранит кэш ``name``/``phone``/``email``: читатели расчётов,
квитанций и распознавания платежей по ФИО продолжают работать на кэше, а реестр
добавляется аддитивно (источник истины для будущей синхронизации и вкладки
«Контакты»).

Модуль не зависит от Qt; из I/O — только load/save своего JSON (по образцу
``core/energy.py``).
"""
from __future__ import annotations

import copy
import json
import os
import re
import uuid

from core.utils import _read_json, DATA_DIR

PEOPLE_FILE = os.path.join(DATA_DIR, "snt_people.json")


# ── загрузка / сохранение ─────────────────────────────────────────────

def load_people() -> list:
    """[{"id", "name", "phone", "email"}]"""
    return _read_json(PEOPLE_FILE, [])


def save_people(data: list) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PEOPLE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── аксессоры / поиск ─────────────────────────────────────────────────

def _norm_name(name) -> str:
    """Нормализация ФИО только для сравнения/дедупа (оригинал хранится отдельно)."""
    return re.sub(r"\s+", " ", str(name or "").strip()).casefold()


def norm_name(name) -> str:
    """Публичная обёртка над ``_norm_name`` — для сравнения ФИО за пределами
    модуля (например, защита от дублей контактов внутри группы в UI)."""
    return _norm_name(name)


def people_index(people: list) -> dict:
    """{id: person} — для быстрого резолва person_id → запись."""
    return {p["id"]: p for p in (people or [])
            if isinstance(p, dict) and p.get("id")}


def get(people: list, pid: str) -> dict | None:
    if not pid:
        return None
    for p in people or []:
        if isinstance(p, dict) and p.get("id") == pid:
            return p
    return None


def find_by_name(people: list, name: str) -> dict | None:
    """Первый человек с совпадающим (нормализованным) полным ФИО, либо None."""
    key = _norm_name(name)
    if not key:
        return None
    for p in people or []:
        if isinstance(p, dict) and _norm_name(p.get("name")) == key:
            return p
    return None


def create_person(name: str, phone: str = "", email: str = "",
                  opd_doc: str = "", member_doc: str = "") -> dict:
    """Новая запись человека с уникальным id.

    ``opd_doc``/``member_doc`` — необязательный путь к уже загруженному
    документу этого контакта (см. докстринг модуля); ключи добавляются,
    только если значение непустое, чтобы не засорять JSON пустыми полями.
    """
    person = {
        "id": uuid.uuid4().hex,
        "name": str(name or "").strip(),
        "phone": str(phone or "").strip(),
        "email": str(email or "").strip(),
    }
    if opd_doc:
        person["opd_doc"] = opd_doc
    if member_doc:
        person["member_doc"] = member_doc
    return person


# ── миграция: реестр из владельцев участков ───────────────────────────

def _owner_lists(plot: dict) -> list:
    """Списки владельцев участка во всех группах (или legacy top-level owners)."""
    groups = plot.get("groups")
    if groups:
        return [g.get("owners", []) or [] for g in groups if isinstance(g, dict)]
    if plot.get("owners"):
        return [plot.get("owners") or []]
    return []


def migrate_people_from_plots(plots: list) -> tuple[list, list]:
    """Строит реестр людей из владельцев участков и проставляет ``person_id``.

    Чистая функция: вход не мутируется (работает на глубокой копии),
    возвращает ``(people, plots_with_person_id)``.

    Правила:
    * дедуп по полному ФИО (нормализованному ``_norm_name``);
    * телефон/email человека = первый непустой среди слитых записей;
    * идемпотентна: уже проставленный ``person_id`` переиспользуется, повторный
      прогон не плодит дублей;
    * ``person_id`` проставляется во ВСЕ записи владельцев (активные и архивные —
      связь не меняет замороженный снимок), кроме записей без ФИО.
    """
    people: list = []
    by_norm: dict[str, dict] = {}
    by_id: dict[str, dict] = {}

    def _ensure(name: str, phone: str, email: str, pid) -> dict:
        name = str(name or "").strip()
        phone = str(phone or "").strip()
        email = str(email or "").strip()
        person = None
        if pid and pid in by_id:
            person = by_id[pid]
        if person is None:
            key = _norm_name(name)
            if key and key in by_norm:
                person = by_norm[key]
        if person is None:
            person = {"id": pid or uuid.uuid4().hex,
                      "name": name, "phone": phone, "email": email}
            people.append(person)
            by_id[person["id"]] = person
            k = _norm_name(name)
            if k:
                by_norm[k] = person
            return person
        # существующий человек — дополняем недостающие контакты
        if not person.get("phone") and phone:
            person["phone"] = phone
        if not person.get("email") and email:
            person["email"] = email
        return person

    out = copy.deepcopy(plots) if plots else []
    for plot in out:
        if not isinstance(plot, dict):
            continue
        for owners in _owner_lists(plot):
            for owner in owners:
                if not isinstance(owner, dict):
                    continue
                if not str(owner.get("name", "")).strip():
                    continue  # запись без ФИО — не человек
                person = _ensure(owner.get("name", ""), owner.get("phone", ""),
                                 owner.get("email", ""), owner.get("person_id"))
                owner["person_id"] = person["id"]
    return people, out
