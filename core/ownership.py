"""Чистое ядро истории владения участком — без UI и Qt, без I/O.

Модель владельца расширена тремя необязательными полями:

* ``share`` — доля в праве. Принимает строку ``"1/2"``, ``"0,5"``, ``"0.5"``
  или число. Если не указана — доля распределяется поровну между
  собственниками (``is_owner=True``), активными в тот же период (см.
  :func:`effective_weights`). Доля в праве нужна прежде всего для деления
  фиксированного членского взноса (ГК РФ ст. 249); для тарифа «за м²»
  распределение и так задаётся площадью владельца (``area``).
* ``since`` — дата приобретения (ISO ``YYYY-MM-DD``). Если не указана —
  владелец считается собственником «с самого начала».
* ``until`` — дата прекращения права (ISO). **Исключающая** граница: на саму
  дату перехода участок принадлежит уже новому собственнику. Если не указана —
  владеет по настоящее время (открытый период).

Обратная совместимость — полная. Владелец может быть:

* обычной строкой (``"Иванов И.И."``) — тогда это один открытый период
  владения без долей и документов;
* dict без новых полей — поведение ровно как до появления истории владения
  (один открытый период, текущий собственник);
* dict со старым полем ``relation`` (``"Собственник"`` / ``"Главный
  собственник"``) — распознаётся как собственник.

Никакая существующая логика расчётов этот модуль пока не вызывает — он
полностью аддитивный. Интеграция в начисления — отдельным шагом
(:func:`core.vznosy.balances_by_owner`).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from core.energy import _parse_iso

# Наименования, под которыми старые записи считались собственниками.
_OWNER_RELATIONS = ("", "Главный собственник", "Собственник")

# ── вид права (как в выписке ЕГРН: «Вид, номер и дата регистрации права») ──
FORM_INDIVIDUAL = "individual"   # Собственность (индивидуальная), доля 1/1
FORM_SHARED = "shared"           # Общая долевая — явные доли (1/2, 21/100)
FORM_JOINT = "joint"             # Общая совместная — доли не выделены (поровну)
FORMS = (FORM_INDIVIDUAL, FORM_SHARED, FORM_JOINT)
FORM_LABELS = {
    FORM_INDIVIDUAL: "Индивидуальная (1/1)",
    FORM_SHARED: "Общая долевая",
    FORM_JOINT: "Общая совместная",
}


def plot_ownership_form(plot_record: dict) -> str:
    """Вид права участка. Если поле не задано — выводит по составу владельцев.

    Инференс (для отображения/значения по умолчанию в UI):
    один собственник → индивидуальная; несколько с заданными долями →
    долевая; несколько без долей → совместная. Само начисление должно
    использовать ЯВНО сохранённое значение (``plot_record.get("ownership_form")``),
    чтобы не менять расчёт у старых данных автоматически.
    """
    f = (plot_record or {}).get("ownership_form")
    if f in FORMS:
        return f
    owners = [o for o in (plot_record or {}).get("owners", []) or [] if is_owner(o)]
    if len(owners) <= 1:
        return FORM_INDIVIDUAL
    if any(owner_share(o) is not None for o in owners):
        return FORM_SHARED
    return FORM_JOINT


# ── аксессоры владельца ───────────────────────────────────────────────

def owner_name(owner) -> str:
    if isinstance(owner, dict):
        return str(owner.get("name", ""))
    return str(owner)


def is_owner(owner) -> bool:
    """Является ли запись собственником (а не просто контактным лицом).

    По умолчанию True. Старые записи распознаются по полю ``relation``.
    """
    if isinstance(owner, dict):
        if "is_owner" in owner:
            return bool(owner["is_owner"])
        return owner.get("relation", "") in _OWNER_RELATIONS
    return True


def owner_area(owner) -> Optional[float]:
    """Площадь (доля площади) владельца в м², либо None."""
    if isinstance(owner, dict):
        v = owner.get("area")
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return None


def owner_since(owner) -> Optional[date]:
    """Дата приобретения права (включающая граница) или None."""
    if isinstance(owner, dict):
        return _parse_iso(str(owner.get("since", "")))
    return None


def owner_until(owner) -> Optional[date]:
    """Дата прекращения права (исключающая граница) или None."""
    if isinstance(owner, dict):
        return _parse_iso(str(owner.get("until", "")))
    return None


# ── доли в праве ──────────────────────────────────────────────────────

def parse_share(v) -> Optional[float]:
    """Парсит долю: ``"1/2"`` | ``"0,5"`` | ``0.5`` → float. Пусто/ошибка → None.

    Ноль и отрицательные значения трактуются как «не задано» (None), чтобы
    не делить сумму на бессмысленную долю.
    """
    if v is None:
        return None
    if isinstance(v, bool):  # bool — подтип int, но это не доля
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    txt = str(v).strip().replace(",", ".")
    if not txt:
        return None
    try:
        if "/" in txt:
            num, den = txt.split("/", 1)
            num_f, den_f = float(num), float(den)
            if den_f == 0:
                return None
            result = num_f / den_f
        else:
            result = float(txt)
    except (ValueError, ZeroDivisionError):
        return None
    return result if result > 0 else None


def owner_share(owner) -> Optional[float]:
    """Доля в праве владельца как float, либо None."""
    if isinstance(owner, dict):
        return parse_share(owner.get("share"))
    return None


def effective_weights(owners: list) -> list[float]:
    """Веса для распределения денежной суммы между владельцами.

    Сумма результата всегда равна 1.0 (если список непуст) — так
    распределённые суммы в точности реконсилируются с начислением на участок,
    без потерянных копеек. Правила:

    * у кого задана ``share`` — берётся как вес;
    * у кого не задана — получают поровну остаток ``1 - сумма_заданных``;
    * результат нормируется к сумме 1.0 (страхует от долей, не дающих в сумме
      единицу, например ``1/3 + 1/3 + 1/3``).

    Возвращает список весов, выровненный по входному списку ``owners``.
    """
    n = len(owners)
    if n == 0:
        return []

    shares = [owner_share(o) for o in owners]
    known = [s for s in shares if s is not None]
    known_sum = sum(known)
    n_missing = sum(1 for s in shares if s is None)

    raw: list[float]
    if n_missing == 0:
        raw = list(shares)  # type: ignore[arg-type]
    else:
        remainder = max(0.0, 1.0 - known_sum)
        per_missing = remainder / n_missing
        raw = [(per_missing if s is None else s) for s in shares]

    total = sum(raw)
    if total <= 0:
        # Все доли нулевые/отсутствуют и остатка нет — делим поровну.
        return [1.0 / n] * n
    return [r / total for r in raw]


# ── активность во времени ─────────────────────────────────────────────

def is_active_at(owner, d: date) -> bool:
    """Владел ли участком на дату ``d``.

    ``since`` — включающая граница, ``until`` — исключающая
    (на дату перехода активен уже новый собственник).
    """
    s = owner_since(owner)
    if s is not None and d < s:
        return False
    u = owner_until(owner)
    if u is not None and d >= u:
        return False
    return True


def owners_at(owners: list, d: date, *, only_owners: bool = True) -> list:
    """Владельцы, активные на дату ``d``.

    ``only_owners=True`` — оставить лишь собственников (отбросить контактных лиц).
    """
    out = []
    for o in owners or []:
        if only_owners and not is_owner(o):
            continue
        if is_active_at(o, d):
            out.append(o)
    return out


def has_history(owners: list) -> bool:
    """Есть ли у участка хоть одна дата перехода (since/until).

    Если нет — таймлайн вырождается в один открытый период (текущие
    собственники), и поведение совпадает с прежним «всё на участок».
    """
    for o in owners or []:
        if owner_since(o) is not None or owner_until(o) is not None:
            return True
    return False


def transfer_dates(owners: list, *, only_owners: bool = True) -> list[date]:
    """Отсортированный список уникальных дат переходов (since и until)."""
    pts: set[date] = set()
    for o in owners or []:
        if only_owners and not is_owner(o):
            continue
        s = owner_since(o)
        if s is not None:
            pts.add(s)
        u = owner_until(o)
        if u is not None:
            pts.add(u)
    return sorted(pts)


def ownership_segments(owners: list, *,
                       only_owners: bool = True) -> list[tuple]:
    """Разбивает таймлайн владения на сегменты по датам переходов.

    Возвращает список ``(date_from | None, date_to | None, [active owners])``,
    отсортированный по времени. ``date_from`` — включающая граница сегмента,
    ``date_to`` — исключающая (= следующая дата перехода или None для открытого
    последнего сегмента). Состав владельцев внутри одного сегмента неизменен.

    Сегмент может содержать пустой список владельцев — это «провал» в истории
    (прежний продал, новый ещё не оформлен); вызывающий код может пометить
    такой период как «собственник не определён».

    Аналог :func:`core.energy.billing_segments` — тот же приём, что уже
    применён для истории смены типа расчёта за электроэнергию.
    """
    relevant = [o for o in (owners or [])
                if (not only_owners or is_owner(o))]
    bounds = transfer_dates(relevant, only_owners=False)
    edges: list[Optional[date]] = [None, *bounds, None]

    segs: list[tuple] = []
    for i in range(len(edges) - 1):
        a, b = edges[i], edges[i + 1]
        probe = _segment_probe(a, b)
        active = [o for o in relevant if is_active_at(o, probe)]
        segs.append((a, b, active))
    return segs


def _segment_probe(a: Optional[date], b: Optional[date]) -> date:
    """Точка внутри сегмента ``[a, b)`` для проверки активности.

    Активность постоянна внутри сегмента (границы — это и есть точки смены),
    поэтому достаточно одной пробной даты.
    """
    if a is not None:
        return a                      # since включающая → активен на a
    if b is not None:
        return date.fromordinal(b.toordinal() - 1)  # день до первой границы
    return date.min                   # переходов нет вовсе


# ── текстовые ярлыки для UI ───────────────────────────────────────────

def owners_label(owners: list, *, only_owners: bool = True,
                 empty: str = "—") -> str:
    """Имена владельцев через запятую (для шапок карточек/квитанций)."""
    names = [owner_name(o) for o in (owners or [])
             if (not only_owners or is_owner(o)) and owner_name(o)]
    return ", ".join(names) if names else empty


# ── группы (новая модель владения) ───────────────────────────────────────────
#
# Группа = dict {"since": str|null, "until": str|null, "owners": [...],
#                "debt_at_close": {"vznosy": float, "energy": float}}
# Активная группа: "until" == None.  Архивная: "until" задан.
# Участок содержит поле "groups": [group, ...].
# Обратная совместимость: участки со старым полем "owners" мигрируются на лету.

def plot_groups(plot: dict) -> list:
    """Список групп участка (с ленивой миграцией из старого формата)."""
    if not plot:
        return []
    if "groups" in plot:
        return plot["groups"] or []
    return _groups_from_legacy(plot)


def _groups_from_legacy(plot: dict) -> list:
    """Преобразует старый формат owners → groups (in-memory, без записи на диск)."""
    owners = plot.get("owners", []) or []
    current = [o for o in owners if not owner_until(o)]
    departed = [o for o in owners if owner_until(o)]

    egrn = plot.get("egrn_doc", "")
    current_copy: list = []
    for i, o in enumerate(current):
        o_c = dict(o) if isinstance(o, dict) else {"name": str(o), "is_owner": True}
        if egrn and i == 0 and not o_c.get("egrn_doc"):
            o_c["egrn_doc"] = egrn
        current_copy.append(o_c)

    groups: list = []
    for o in departed:
        until_d = owner_until(o)
        o_c = dict(o) if isinstance(o, dict) else {"name": str(o), "is_owner": True}
        groups.append({
            "since": None,
            "until": until_d.isoformat() if until_d else None,
            "owners": [o_c],
            "debt_at_close": {"vznosy": 0.0, "energy": 0.0},
        })
    groups.sort(key=lambda g: g.get("until") or "")
    groups.append({"since": None, "until": None, "owners": current_copy})
    return groups


def active_group(plot: dict) -> Optional[dict]:
    """Активная группа участка (until=None), или None если не найдена."""
    for g in plot_groups(plot):
        if g.get("until") is None:
            return g
    return None


def archived_groups(plot: dict) -> list:
    """Архивные группы, отсортированные по until убыванию (свежие сначала)."""
    out = [g for g in plot_groups(plot) if g.get("until") is not None]
    return sorted(out, key=lambda g: g.get("until") or "", reverse=True)


def group_since(group: dict) -> Optional[date]:
    return _parse_iso(str(group.get("since") or ""))


def group_until(group: dict) -> Optional[date]:
    return _parse_iso(str(group.get("until") or ""))


def group_owners(group: dict) -> list:
    return group.get("owners", []) or []


def group_label(group: dict, *, empty: str = "—") -> str:
    return owners_label(group_owners(group), only_owners=True, empty=empty)


def migrate_plot_to_groups(plot: dict) -> dict:
    """Возвращает копию участка в формате groups (не меняет оригинал).

    Если участок уже содержит поле 'groups' — возвращает без изменений.
    Вызывать перед сохранением для персистирования миграции на диск.
    """
    if "groups" in plot:
        return plot
    groups = _groups_from_legacy(plot)
    result = {k: v for k, v in plot.items()
              if k not in ("owners", "ownership_form", "egrn_doc")}
    result["groups"] = groups
    return result
