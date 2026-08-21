# -*- coding: utf-8 -*-
"""
THE ANIMATOR AIRTABLE — самая большая курируемая коллекция ригов в сети.

1350 записей: 1232 под Maya, 71 под Blender; 821 бесплатный, 521 платный.
У каждой записи есть автор, категория, стиль, система рига и список
возможностей — из этого получается точный пост даже там, где текстового
описания нет. База живая: последние записи добавлены в августе 2026-го.

Доступ идёт по публичной ссылке. Внутренний API базы требует подписанный
accessPolicy, и эта подпись лежит прямо в HTML страницы шаринга, поэтому
бот сначала забирает страницу, вынимает подпись и по ней читает данные.

Картинки базы отдаются только по отдельной подписи, поэтому изображение
персонажа бот ищет обычным путём — на странице рига и в интернете.
"""

import re
import json
import time
import calendar
from urllib.parse import quote

import requests

HEADERS = {
    "x-time-zone": "UTC",
    "x-user-locale": "en",
    "x-requested-with": "XMLHttpRequest",
    "x-airtable-page-load-id": "pglrigsbot00000000",
}

# Возможности рига: в базе это отдельные колонки со значением YES/NO.
FEATURES = (
    ("FACIAL CONTROLS?", "facial controls"),
    ("FK/IK LIMBS", "FK/IK limbs"),
    ("FK/IK SPINE?", "FK/IK spine"),
    ("STRETCHY IK?", "stretchy IK"),
    ("SHAPEABLE STRETCHY IK?", "shapeable stretchy IK"),
    ("PICKER?", "animation picker"),
    ("PINS?", "pins"),
    ("GLOBAL SCALE?", "global scale"),
    ("COG CONTROL?", "COG control"),
    ("POSE LIBRARY?", "pose library"),
    ("GAME-ENGINE FRIENDLY?", "game-engine friendly"),
)


def clean_label(text):
    """Подписи в базе иногда с техническим префиксом: "-shape- Humanoid"."""
    text = re.sub(r"^-\s*[a-z][a-z /-]{0,14}\s*-\s*", "", (text or "").strip(),
                  flags=re.I)
    return text.strip(" -")


def quill_text(value):
    """Описание хранится как документ Quill — склеиваем куски текста.

    Заодно выбрасываем служебные строки вроде "Old link: ...", которые
    редакторы базы оставляют для себя, а в посте они выглядят мусором.
    """
    if isinstance(value, str):
        raw = value
    elif isinstance(value, dict):
        parts = []
        for op in value.get("documentValue") or []:
            piece = op.get("insert")
            if isinstance(piece, str):
                parts.append(piece)
        raw = "".join(parts)
    else:
        return ""

    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^(old|new|backup|alt(ernate)?)\s+link\s*:", line, re.I):
            continue
        if re.match(r"^(broken|dead)\b", line, re.I):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _maps(schema):
    """name -> id для колонок и id -> подпись для всех выпадающих списков."""
    by_name, choices = {}, {}
    for col in schema.get("columns") or []:
        by_name[(col.get("name") or "").strip().upper()] = col.get("id")
        opts = (col.get("typeOptions") or {}).get("choices") or {}
        if isinstance(opts, dict):
            for cid, ch in opts.items():
                choices[cid] = (ch or {}).get("name") or ""
    return by_name, choices


def _labels(cell, choices):
    """Значение select/multiSelect -> человеческие подписи."""
    if isinstance(cell, str):
        one = clean_label(choices.get(cell, ""))
        return [one] if one else []
    if isinstance(cell, list):
        out = [clean_label(choices.get(c, "")) for c in cell]
        return [c for c in out if c]
    return []


def _access_policy(share_url, get, log):
    """Подпись доступа к базе. Лежит в HTML страницы уже url-кодированной."""
    page = get(share_url)
    if not page:
        return ""
    m = re.search(r'[?&]accessPolicy=([^"&\'\s<\\]+)', page)
    if not m:
        log("  ! airtable: подписи доступа нет на странице базы")
    return m.group(1) if m else ""


def fetch(cfg, get, log, ua, timeout, limit, start_index=0, fresh_days=45):
    """Список ригов из базы: сначала новинки, затем окно архива."""
    share = cfg.get("share_url") or ""
    app_id = cfg.get("app_id") or ""
    table_id = cfg.get("table_id") or ""
    view_id = cfg.get("view_id") or ""
    if not (share and app_id and table_id and view_id):
        return []

    policy = _access_policy(share, get, log)
    if not policy:
        return []

    params = json.dumps({
        "includeDataForTableIds": [table_id],
        "includeDataForViewIds": [view_id],
        "shouldIncludeSchemaChecksum": False,
        "mayOnlyIncludeRowAndCellDataForIncludedViews": False,
        "mayExcludeCellDataForLargeViews": False,
        "allowMsgpackOfResult": False,
        "canClientSupportPreviewMode": True,
    }, separators=(",", ":"))

    url = ("https://airtable.com/v0.3/application/{app}/read"
           "?stringifiedObjectParams={p}&requestId=reqrigsbot00001"
           "&accessPolicy={ap}").format(
        app=app_id, p=quote(params, safe=""), ap=policy)

    headers = dict(HEADERS)
    headers["User-Agent"] = ua
    headers["x-airtable-application-id"] = app_id
    try:
        # ответ около 6 МБ — таймаут на чтение берём с запасом
        r = requests.get(url, headers=headers, timeout=max(timeout, 30))
        if not r.ok:
            log("  ! airtable: HTTP {}".format(r.status_code))
            return []
        data = (r.json() or {}).get("data") or {}
    except Exception as e:
        log("  ! airtable: {}".format(str(e)[:120]))
        return []

    schema = next((t for t in data.get("tableSchemas") or []
                   if t.get("id") == table_id), None)
    table = next((t for t in data.get("tableDatas") or []
                  if t.get("id") == table_id), None)
    if not schema or not table:
        log("  ! airtable: в ответе нет нужной таблицы")
        return []

    by_name, choices = _maps(schema)
    rows = table.get("rows") or list((table.get("rowsById") or {}).values())

    c = by_name.get
    c_name, c_link = c("NAME"), c("LINK")
    c_price, c_soft = c("PRICE"), c("SOFTWARE")
    c_rigger, c_desc = c("RIGGER"), c("DESCRIPTION")
    c_date = c("DATE ADDED TO DATABASE")
    c_cat, c_sub = c("CATEGORY"), c("SUB-CATEGORY")
    c_style, c_system, c_tags = c("STYLE"), c("RIG SYSTEM"), c("TAGS")

    fresh_edge = time.time() - fresh_days * 86400
    fresh_rigs, archive = [], []

    for row in rows:
        cells = row.get("cellValuesByColumnId") or {}
        name = (cells.get(c_name) or "").strip()
        link = (cells.get(c_link) or "").strip()
        if not name or not link:
            continue
        if not link.lower().startswith("http"):
            link = "https://" + link.lstrip("/")

        soft_low = " ".join(_labels(cells.get(c_soft), choices)).lower()
        if "maya" in soft_low:
            software = "Maya"
        elif "blender" in soft_low:
            software = "Blender"
        else:
            continue          # 3ds Max, C4D, Harmony — не наша тема

        prices = [p.upper() for p in _labels(cells.get(c_price), choices)]
        free = True if "FREE" in prices else (False if "PAID" in prices else None)

        author = ""
        ref = cells.get(c_rigger)
        if isinstance(ref, list) and ref:
            author = (ref[0] or {}).get("foreignRowDisplayName") or ""

        # описание: сначала текст из базы, если он там есть
        desc = quill_text(cells.get(c_desc))

        # затем паспорт рига из полей базы — в этом и есть её ценность
        facts = []
        cat = _labels(cells.get(c_cat), choices) + _labels(cells.get(c_sub), choices)
        if cat:
            facts.append("Category: " + " / ".join(cat))
        style = _labels(cells.get(c_style), choices)
        if style:
            facts.append("Style: " + ", ".join(style))
        system = _labels(cells.get(c_system), choices)
        if system:
            facts.append("Rig system: " + ", ".join(system))
        tags = _labels(cells.get(c_tags), choices)
        if tags:
            facts.append("Tags: " + ", ".join(tags[:6]))

        have = []
        for field, label in FEATURES:
            cid = by_name.get(field)
            if not cid:
                continue
            vals = [v.upper() for v in _labels(cells.get(cid), choices)]
            if any(v.startswith("YES") for v in vals):
                have.append(label)
        if have:
            facts.append("Rig features: " + ", ".join(have))

        text = (desc + ("\n" if desc and facts else "") + ". ".join(facts)).strip()
        if not text:
            text = "{} character rig for {}.".format(name, software)

        added = cells.get(c_date) or ""
        is_fresh = False
        if isinstance(added, str) and len(added) >= 10:
            try:
                ts = calendar.timegm(time.strptime(added[:10], "%Y-%m-%d"))
                is_fresh = ts >= fresh_edge
            except Exception:
                pass

        tag_line = "#" + software.lower()
        if free is True:
            tag_line += " #free"
        elif free is False:
            tag_line += " #paid"

        rig = {
            "url": link,
            "name": name,
            "author": author,
            "software": software,
            "free": free,
            "price": "",
            "license": "",
            "size_mb": None,
            "thumb": "",
            "rating": "",
            "description": text[:1500],
            "ready": False,
            "tags": tag_line,
            "source": "airtable",
            "fresh": is_fresh,
        }
        (fresh_rigs if is_fresh else archive).append(rig)

    # Новинки берём всегда, архив — окном: каждый запуск заходит с новой
    # позиции, поэтому за сутки бот обходит всю базу по кругу.
    out = fresh_rigs[:limit]
    if archive:
        start = start_index % len(archive)
        window = archive[start:start + limit]
        if len(window) < limit:
            window += archive[:limit - len(window)]
        out += window
    return out
