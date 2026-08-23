# -*- coding: utf-8 -*-
"""
Animation Rigs Bot — собирает персонажные риги для Maya и Blender,
адаптирует описание на русский через Gemini и публикует в Telegram-канал.

Запускается по расписанию в GitHub Actions. Ничего не скачивает и не
перезаливает: у большинства ригов лицензия запрещает раздачу файлов,
поэтому в посте всегда ссылка на страницу автора.

Переменные окружения:
  BOT_TOKEN       — токен бота от @BotFather
  CHANNEL_ID      — @имя_канала или числовой id
  GEMINI_API_KEY  — бесплатный ключ с aistudio.google.com/apikey (необязательно)
  CATCH_UP        — 1: пометить всё найденное как виденное, ничего не постить
"""

import os
import re
import sys
import json
import time
import html
import hashlib
import datetime as dt
from urllib.parse import urljoin

import requests
import yaml

import config
import airtable_source

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "posted.json")
SEED_FILE = os.path.join(HERE, "seed_rigs.yml")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TG_API = "https://api.telegram.org/bot{token}/{method}"

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()


def log(msg):
    print("[{}] {}".format(dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- состояние

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"posted": [], "sources": [], "counter": 0, "image_hashes": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("posted", [])
        data.setdefault("sources", [])
        data.setdefault("counter", 0)
        data.setdefault("image_hashes", [])
        return data
    except Exception as e:
        log("posted.json не читается ({}), начинаю с чистого списка".format(e))
        return {"posted": [], "sources": [], "counter": 0, "image_hashes": []}


def save_state(state):
    state["posted"] = state["posted"][-2000:]
    state["sources"] = state["sources"][-5:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    push_state()


def push_state():
    """Сразу коммитим память в репозиторий.

    Запуск живёт почти час, и если ждать конца задания, то при отмене или
    сбое бот забудет, что уже публиковал, и погонит дубли по второму кругу.
    """
    if os.environ.get("GIT_AUTOSAVE", "").lower() not in ("1", "true", "yes"):
        return
    import subprocess

    cmds = [
        ["git", "add", "posted.json"],
        ["git", "diff", "--staged", "--quiet"],
    ]
    try:
        subprocess.run(cmds[0], cwd=HERE, check=False, capture_output=True)
        changed = subprocess.run(cmds[1], cwd=HERE, capture_output=True).returncode != 0
        if not changed:
            return
        subprocess.run(["git", "commit", "-m", "update posted rigs"],
                       cwd=HERE, check=False, capture_output=True)
        subprocess.run(["git", "pull", "--rebase", "--autostash"],
                       cwd=HERE, check=False, capture_output=True)
        r = subprocess.run(["git", "push"], cwd=HERE, capture_output=True)
        if r.returncode != 0:
            log("  ! память не запушилась: {}".format(
                r.stderr.decode('utf-8', 'replace')[:150]))
    except Exception as e:
        log("  ! память не сохранилась в репозиторий: {}".format(e))


def rig_id(url):
    key = (url or "").split("?")[0].rstrip("/").lower()
    return hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()[:16]


# ---------------------------------------------------------------- утилиты

def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"(?is)<(script|style|nav|footer).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


_DEADLINE = [None]
# с какой страницы архива Highend3d заходить в этот раз
_archive_page = [1]
# с какой записи архива Animation Buffet начинать в этот раз
_buffet_index = [1]
# с какой записи архива Airtable начинать в этот раз
_airtable_index = [0]


def start_clock():
    _DEADLINE[0] = time.time() + config.COLLECT_BUDGET_SEC


def reset_clock(seconds):
    """Новый лимит времени — например, отдельный на этап публикации.

    Бюджет сбора и бюджет публикации нельзя мешать: если публикация
    работает по остаткам от сбора, один тормозящий сайт съедает время,
    и потом ни у одного рига «не находится картинка», хотя дело
    вовсе не в картинках. На этом канал уже простаивал часами.
    """
    _DEADLINE[0] = time.time() + seconds


def out_of_time():
    """Пора закругляться со сбором и публиковать то, что уже есть."""
    return _DEADLINE[0] is not None and time.time() > _DEADLINE[0]


def get(url, **kw):
    if out_of_time():
        return None
    try:
        r = requests.get(url, headers={"User-Agent": UA},
                         timeout=config.HTTP_TIMEOUT, **kw)
        if r.status_code != 200:
            log("  ! {} -> HTTP {}".format(url[:70], r.status_code))
            return None
        return r.text
    except Exception as e:
        log("  ! {} -> {}".format(url[:70], e))
        return None


def meta(page, prop):
    for pattern in (
        r'<meta[^>]+(?:property|name)=["\']{}["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{}["\']',
    ):
        m = re.search(pattern.format(re.escape(prop)), page, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def smart_trim(text, max_sentences, hard_limit):
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for i, s in enumerate(parts):
        if i >= max_sentences:
            break
        cand = (out + " " + s).strip()
        if len(cand) > hard_limit:
            break
        out = cand
    if not out:
        cut = text[:hard_limit]
        pos = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        out = cut[:pos + 1] if pos > 60 else cut.rsplit(" ", 1)[0]
    return out.strip()


# ---------------------------------------------------------------- источники

def load_seed():
    try:
        with open(SEED_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        log("seed_rigs.yml не читается: {}".format(e))
        return []
    out = []
    for it in data.get("rigs") or []:
        if not it.get("url") or not it.get("name"):
            continue
        out.append({
            "url": it["url"],
            "name": it["name"],
            "author": it.get("author", ""),
            "software": it.get("software", ""),
            "free": bool(it.get("free", True)),
            "price": str(it.get("price", "") or ""),
            "license": it.get("license", ""),
            "size_mb": it.get("size_mb"),
            "thumb": it.get("thumb", ""),
            "rating": it.get("rating", ""),
            "description": it.get("ru") or it.get("description", ""),
            "ready": bool(it.get("ru")),   # текст уже по-русски, ИИ не нужен
            "tags": it.get("tags", ""),
            "source": "curated",
        })
    return out


BS = "https://studio.blender.org"


def fetch_blender_studio(limit):
    index = get(BS + "/characters/")
    if not index:
        return []
    paths, seen = [], set()
    for p in re.findall(r'href="(/characters/[a-z0-9\-]+/v\d+/?)"', index):
        if p not in seen:
            seen.add(p)
            paths.append(p)

    out = []
    for path in paths[:limit * 2]:
        if out_of_time():
            log("  ! бюджет времени исчерпан, беру что успел")
            break
        url = BS + path
        page = get(url)
        if not page:
            continue
        text = strip_html(page)
        size = None
        m = re.search(r"([\d.,]+)\s*(MB|GB)\b", text, re.I)
        if m:
            v = float(m.group(1).replace(",", ""))
            size = v * 1024 if m.group(2).upper() == "GB" else v
        out.append({
            "url": url,
            "name": meta(page, "og:title") or path.strip("/").split("/")[1].title(),
            "author": "Blender Studio",
            "software": "Blender",
            "free": True,
            "price": "",
            "license": "CC-BY 4.0",
            "size_mb": size,
            "thumb": meta(page, "og:image"),
            "rating": "",
            "description": meta(page, "og:description") or text[:1500],
            "ready": False,
            "tags": "#blender #free",
            "source": "blender-studio",
        })
        if len(out) >= limit:
            break
        time.sleep(0.4)
    return out


HE = "https://www.highend3d.com"


def fetch_highend3d(limit, pages, paid, sort="newest", tag=None, start_page=1):
    base = (HE + "/character-rigs/c/marketplace") if paid \
        else (HE + "/maya/character-rigs/c/downloads")
    links, seen = [], set()
    for page_no in range(start_page, start_page + pages):
        listing = get("{}?page={}&sort={}".format(base, page_no, sort))
        if not listing:
            break
        # Ссылки на сайте бывают и абсолютными, и относительными —
        # ловим оба вида, иначе можно молча собрать пустой список.
        for href in re.findall(r'href="([^"#?]+)"', listing):
            path = href
            if path.startswith("http"):
                m = re.match(r"https?://[^/]*highend3d\.com(/.*)", path)
                if not m:
                    continue
                path = m.group(1)
            elif not path.startswith("/"):
                continue
            if "/character-rigs/" not in path:
                continue
            if "/downloads/" not in path and "/marketplace" not in path:
                continue
            # сами страницы-листинги нам не нужны, только карточки ригов
            if path.rstrip("/").endswith(("/c", "/downloads", "/marketplace")):
                continue
            url = HE + path
            if url not in seen and url != base:
                seen.add(url)
                links.append(url)
        time.sleep(0.5)

    log("    highend3d: собрано ссылок {}".format(len(links)))

    # не перебираем всю страницу подряд: берём с запасом и останавливаемся
    out = []
    for url in links[:limit * 3]:
        if out_of_time():
            log("  ! бюджет времени исчерпан, беру что успел")
            break
        page = get(url)
        if not page:
            continue
        text = strip_html(page)
        name = re.sub(r"\s*(for Maya|\|.*)$", "", meta(page, "og:title") or "").strip()
        if not name:
            continue

        price = ""
        pm = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)", text)
        if pm:
            price = "$" + pm.group(1)

        rating = ""
        dm = re.search(r"([\d,]{3,})\s+downloads?", text, re.I)
        if dm:
            rating = dm.group(1) + " скачиваний"
        sm = re.search(r"([0-5](?:\.\d)?)\s*(?:stars|/\s*5)", text, re.I)
        if sm:
            rating = (rating + " · " if rating else "") + sm.group(1) + "★"

        lic = ""
        lm = re.search(r"License\s*:?\s*([A-Za-z0-9 \-\.]{2,40})", text)
        if lm:
            lic = lm.group(1).strip()

        out.append({
            "url": url,
            "name": name,
            "author": "",
            "software": "Maya",
            "free": not paid and not price,
            "price": price,
            "license": lic,
            "size_mb": None,
            "thumb": meta(page, "og:image"),
            "rating": rating,
            "description": meta(page, "og:description") or text[:1500],
            "ready": False,
            "tags": "#maya " + ("#paid" if paid else "#free"),
            "source": tag or ("highend3d-paid" if paid else "highend3d-free"),
            "fresh": sort == "newest",
        })
        if len(out) >= limit:
            break
        time.sleep(0.4)
    return out


def _any_word(words, text):
    """Совпадение по границам слова: 'rig' не должен ловиться в 'right'."""
    pattern = r"(?<![\w-])(?:" + "|".join(words) + r")(?![\w-])"
    return re.search(pattern, text, re.I | re.U) is not None


def looks_like_rig(text):
    """Пускаем только то, что похоже на выложенный риг персонажа.

    Мало найти слово 'rig' — нужно ещё, чтобы речь шла о персонаже
    или о готовом ассете. Иначе в канал лезут статьи про профессию.
    """
    low = (text or "").lower()
    if any(sw in low for sw in config.RIG_STOPWORDS):
        return False
    if not _any_word(config.RIG_WORDS, low):
        return False
    return (_any_word(config.SUBJECT_WORDS, low)
            or _any_word(config.RELEASE_WORDS, low))


def fetch_news_feeds():
    """Новинки со всего интернета — через ленты профильных изданий.

    У самих сайтов с ригами ни API, ни RSS нет, зато издания пишут
    про каждый заметный релиз в тот же день.
    """
    import feedparser

    now = dt.datetime.now(dt.timezone.utc)
    max_age = config.NEWS_MAX_AGE_DAYS * 86400
    out = []

    for name, url in config.NEWS_FEEDS:
        raw = get(url)
        if not raw:
            continue
        try:
            feed = feedparser.parse(raw)
        except Exception as e:
            log("  ! лента {} не разобралась: {}".format(name, e))
            continue

        taken = 0
        for e in (feed.entries or [])[:config.NEWS_LIMIT]:
            title = strip_html(e.get("title", ""))
            summary = strip_html(e.get("summary", "") or e.get("description", ""))
            link = e.get("link") or ""
            if not title or not link:
                continue
            if not looks_like_rig(title + " " + summary):
                continue

            ts = None
            for field in ("published_parsed", "updated_parsed"):
                tm = e.get(field)
                if tm:
                    try:
                        ts = dt.datetime(*tm[:6], tzinfo=dt.timezone.utc)
                    except Exception:
                        pass
                    break
            if ts and (now - ts).total_seconds() > max_age:
                continue

            thumb = ""
            for key in ("media_content", "media_thumbnail"):
                for m in e.get(key, []) or []:
                    if m.get("url"):
                        thumb = m["url"]
                        break
                if thumb:
                    break
            if not thumb:
                body = summary + "".join(
                    c.get("value", "") for c in e.get("content", []) or [])
                m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', body)
                if m:
                    thumb = m.group(1)

            out.append({
                "url": link,
                "name": title,
                "author": "",
                "software": "",
                "free": None,          # по ленте не понять — скажем честно
                "price": "",
                "license": "",
                "size_mb": None,
                "thumb": thumb,
                "rating": "",
                "description": summary[:1500],
                "ready": False,
                "tags": "#новинка",
                "source": "news:" + name,
                "fresh": True,
            })
            taken += 1
        log("  {}: {} подходящих".format(name, taken))
    return out


ANIMA = "https://anima.to"


def fetch_anima_to(limit, pages, known):
    """anima.to — агрегатор ригов от самых разных авторов.

    Файлы у себя не хранит, только карточки со ссылками наружу,
    зато собирает и Maya, и Blender от десятков разных студий и
    одиночек. Лучший источник разнообразия из всех найденных.
    """
    links, seen = [], set()
    for page_no in range(1, pages + 1):
        url = "{}/rigs/?sort=recent&page={}".format(ANIMA, page_no)
        listing = get(url)
        if not listing:
            break
        for href in re.findall(r'href="(/rigs/\d+)"', listing):
            full = ANIMA + href
            if full not in seen:
                seen.add(full)
                links.append(full)
        time.sleep(0.4)

    if not links:
        log("  ! anima.to не отдал ни одной карточки")
        return []

    out = []
    for url in links:
        if url in known:
            continue
        if out_of_time():
            log("  ! бюджет времени исчерпан, беру что успел")
            break
        page = get(url)
        if not page:
            continue
        text = strip_html(page)
        name = re.sub(r"\s*\|.*$", "", meta(page, "og:title") or "").strip()
        if not name:
            continue

        low = text.lower()
        soft = []
        if "maya" in low:
            soft.append("Maya")
        if "blender" in low:
            soft.append("Blender")
        if "unreal" in low:
            soft.append("Unreal")

        price = ""
        pm = re.search(r"\$\s?([\d,]+(?:\.\d{2})?)", text)
        if pm:
            price = "$" + pm.group(1)
        free = ("free" in low or "бесплатн" in low) and not price

        tags = "#" + " #".join(s.lower() for s in soft) if soft else "#rig"
        tags += " #free" if free else " #paid"

        out.append({
            "url": url,
            "name": name,
            "author": "",
            "software": ", ".join(soft),
            "free": True if free else (False if price else None),
            "price": price,
            "license": "",
            "size_mb": None,
            "thumb": meta(page, "og:image"),
            "rating": "",
            "description": meta(page, "og:description") or text[:1200],
            "ready": False,
            "tags": tags,
            "source": "anima.to",
            "fresh": True,
        })
        if len(out) >= limit:
            break
        time.sleep(0.4)
    return out


def _softwares(text):
    low = (text or "").lower()
    out = []
    for name, key in (("Maya", "maya"), ("Blender", "blender"),
                      ("3ds Max", "3ds max"), ("Unreal", "unreal"),
                      ("Cinema 4D", "cinema 4d")):
        if key in low:
            out.append(name)
    return ", ".join(out)


def fetch_woo_store(label, base, limit, pages=2, category=None):
    """Магазин на WooCommerce — берём товары через открытый Store API.

    Он отдаёт название, ссылку, цену и картинку одним запросом,
    так что ни разбора HTML, ни похода на каждую страницу не нужно.
    """
    out = []
    for page in range(1, pages + 1):
        if out_of_time():
            break
        url = "{}/wp-json/wc/store/v1/products?per_page=100&page={}".format(
            base.rstrip("/"), page)
        if category:
            url += "&category=" + str(category)
        try:
            r = requests.get(url, headers={"User-Agent": UA},
                             timeout=config.HTTP_TIMEOUT)
            if not r.ok:
                log("  ! {}: HTTP {}".format(label, r.status_code))
                break
            items = r.json()
        except Exception as e:
            log("  ! {}: {}".format(label, str(e)[:100]))
            break
        if not items:
            break

        for it in items:
            name = strip_html(it.get("name") or "")
            link = it.get("permalink") or ""
            if not name or not link:
                continue
            desc = strip_html(it.get("short_description")
                              or it.get("description") or "")
            prices = it.get("prices") or {}
            raw = str(prices.get("price") or "")
            minor = int(prices.get("currency_minor_unit") or 2)
            symbol = prices.get("currency_prefix") or prices.get("currency_symbol") or "$"
            free = raw in ("", "0", "0" * len(raw))
            price = ""
            if not free and raw.isdigit():
                price = "{}{:.2f}".format(symbol, int(raw) / (10 ** minor))

            images = it.get("images") or []
            thumb = (images[0].get("src") if images else "") or ""
            soft = _softwares(name + " " + desc) or "Maya"

            out.append({
                "url": link,
                "name": name,
                "author": label.split("·")[0].strip(),
                "software": soft,
                "free": free,
                "price": price,
                "license": "",
                "size_mb": None,
                "thumb": thumb,
                "rating": "",
                "description": desc[:1500],
                "ready": False,
                "tags": ("#maya" if "Maya" in soft else "#rig")
                        + (" #free" if free else " #paid"),
                "source": label,
                "fresh": page == 1,
            })
            if len(out) >= limit:
                return out
        time.sleep(0.3)
    return out


BUFFET = "https://animationbuffet.blogspot.com"


def fetch_animation_buffet(limit, start_index=1):
    """Animation Buffet — каталог Maya-ригов, который ведут с 2008 года.

    Отдаёт JSON-ленту Blogger: 660 записей, в заголовке каждой прямо
    написано, бесплатный риг или платный.
    """
    url = ("{}/feeds/posts/default?alt=json&max-results=50&start-index={}"
           .format(BUFFET, start_index))
    try:
        r = requests.get(url, headers={"User-Agent": UA},
                         timeout=config.HTTP_TIMEOUT)
        if not r.ok:
            log("  ! animation-buffet: HTTP {}".format(r.status_code))
            return []
        feed = r.json().get("feed", {})
    except Exception as e:
        log("  ! animation-buffet: {}".format(str(e)[:100]))
        return []

    out = []
    for entry in feed.get("entry", []) or []:
        title = strip_html((entry.get("title") or {}).get("$t", ""))
        if not title or "review" in title.lower():
            continue          # это повтор-обзор того же рига
        link = ""
        for l in entry.get("link", []) or []:
            if l.get("rel") == "alternate":
                link = l.get("href", "")
                break
        if not link:
            continue

        raw = (entry.get("content") or {}).get("$t", "")
        desc = strip_html(raw)
        low = title.lower()
        free = "(free" in low or "free " in low
        paid = "(paid" in low
        soft = _softwares(title + " " + desc[:400]) or "Maya"

        thumb = ((entry.get("media$thumbnail") or {}).get("url") or "")
        if thumb:
            thumb = re.sub(r"/s\d+(-c)?/", "/s1600/", thumb)
        if not thumb:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw)
            if m:
                thumb = m.group(1)

        name = re.sub(r"\s*\((free|paid)[^)]*\)\s*", " ", title, flags=re.I)
        name = re.sub(r"\s*-\s*(free|paid)\s+maya.*$", "", name, flags=re.I).strip()

        out.append({
            "url": link,
            "name": name or title,
            "author": "",
            "software": soft,
            "free": True if free else (False if paid else None),
            "price": "",
            "license": "",
            "size_mb": None,
            "thumb": thumb,
            "rating": "",
            "description": desc[:1500],
            "ready": False,
            "tags": "#maya " + ("#free" if free else "#paid" if paid else "#rig"),
            "source": "animation-buffet",
            "fresh": start_index <= 50,
        })
        if len(out) >= limit:
            break
    return out


def collect():
    start_clock()
    rigs = []

    for r in load_seed():
        r["fresh"] = False
        rigs.append(r)
    log("  курируемый список: {}".format(len(rigs)))

    # Airtable — главный источник: 1350 курируемых ригов, 1232 под Maya.
    air = getattr(config, "AIRTABLE", {}) or {}
    if air.get("enabled"):
        got = airtable_source.fetch(
            air, get, log, UA, config.HTTP_TIMEOUT,
            int(air.get("limit", 24)),
            start_index=_airtable_index[0],
            fresh_days=int(air.get("fresh_days", 45)))
        log("  airtable (с записи {}): {}".format(_airtable_index[0], len(got)))
        rigs += got

    rigs += fetch_news_feeds()

    if config.BLENDER_STUDIO.get("enabled"):
        got = fetch_blender_studio(config.BLENDER_STUDIO.get("limit", 6))
        for r in got:
            r["fresh"] = False
        log("  blender-studio: {}".format(len(got)))
        rigs += got

    if config.HIGHEND3D_FREE.get("enabled"):
        c = config.HIGHEND3D_FREE
        got = fetch_highend3d(c.get("limit", 8), c.get("pages", 2), paid=False,
                              sort=c.get("sort", "newest"))
        log("  highend3d-free: {}".format(len(got)))
        rigs += got

    if getattr(config, "HIGHEND3D_POPULAR", {}).get("enabled"):
        c = config.HIGHEND3D_POPULAR
        # Архив глубокий: 456 бесплатных Maya-ригов на 19 страницах.
        # Каждый запуск заходит с новой страницы, поэтому за сутки
        # бот обходит всю библиотеку, а не топчется на первой странице.
        start = _archive_page[0]
        got = fetch_highend3d(c.get("limit", 8), c.get("pages", 2), paid=False,
                              sort=c.get("sort", "downloads"),
                              tag="highend3d-popular", start_page=start)
        log("  highend3d-popular (стр. {}+): {}".format(start, len(got)))
        rigs += got

    # --- магазины на WooCommerce: отдают всё готовым JSON ---
    for block in getattr(config, "WOO_STORES", []) or []:
        if not block.get("enabled", True):
            continue
        got = fetch_woo_store(block["name"], block["url"],
                              int(block.get("limit", 12)),
                              pages=int(block.get("pages", 2)),
                              category=block.get("category"))
        log("  {}: {}".format(block["name"], len(got)))
        rigs += got

    buffet = getattr(config, "ANIMATION_BUFFET", {})
    if buffet.get("enabled"):
        # шагаем по архиву: каждый запуск смотрит новый кусок из 660 записей
        start = _buffet_index[0]
        got = fetch_animation_buffet(int(buffet.get("limit", 12)), start)
        log("  animation-buffet (с записи {}): {}".format(start, len(got)))
        rigs += got

    if getattr(config, "ANIMA_TO", {}).get("enabled"):
        c = config.ANIMA_TO
        got = fetch_anima_to(c.get("limit", 8), c.get("pages", 2), known=set())
        log("  anima.to: {}".format(len(got)))
        rigs += got

    if config.HIGHEND3D_PAID.get("enabled"):
        c = config.HIGHEND3D_PAID
        got = fetch_highend3d(c.get("limit", 4), c.get("pages", 1), paid=True,
                              sort=c.get("sort", "newest"))
        log("  highend3d-paid: {}".format(len(got)))
        rigs += got

    unique, seen = [], set()
    for r in rigs:
        rid = rig_id(r["url"])
        if rid in seen:
            continue
        seen.add(rid)
        r["id"] = rid
        unique.append(r)
    return unique


# ---------------------------------------------------------------- Gemini

GEMINI_URL = ("https://generativelanguage.googleapis.com/{ver}/models/"
              "{model}:generateContent")
_MODELS = []

PROMPT = """Ты редактор русскоязычного телеграм-канала о персонажных ригах \
для Maya и Blender. Читают аниматоры.

Напиши текст поста про этот риг.

Требования:
- {n} предложения максимум, не длиннее {lim} символов;
- скажи, что это за персонаж и чем риг полезен аниматору: лицевые контролы, \
IK/FK, пикер, слои анимации, требования к версии софта — но ТОЛЬКО то, что \
есть в данных ниже;
- НИЧЕГО НЕ ВЫДУМЫВАЙ. Пиши только то, что есть в данных;
- НИКОГДА не сообщай об отсутствии информации. Запрещены любые фразы вроде \
«других данных нет», «подробности не указаны», «в открытом доступе \
информации нет», «возможности рига не описаны». Читателю не нужен отчёт \
о том, чего мы не знаем;
- если про возможности рига в данных ничего нет, напиши одну короткую фразу \
о том, кто этот персонаж, и на этом закончи. А если и этого не известно — \
верни пустой ответ, вообще без текста. Пустой пост лучше пустой фразы;
- не пиши цену, лицензию и название софта — они уже есть в посте отдельной \
строкой;
- термины как принято в индустрии: риг, пикер, IK/FK, блендшейпы, скиннинг;
- нейтральный тон, без рекламы и восклицательных знаков, без обращений \
к читателю;
- в ответе только текст поста, без заголовка, разметки и хэштегов.

Данные о риге:
{facts}"""


# Фразы-пустышки об отсутствии информации. Их выдаёт и ИИ, и сайты-источники:
# «Другой технической информации о возможностях в открытом доступе нет.»
# В посте они бесполезны — лучше просто название рига.
_ABSENCE = re.compile(
    r"(нет|отсутств\w*|не\s+(указан\w*|приводит\w*|сообщ\w*|уточня\w*|"
    r"раскрыва\w*|публику\w*|предоставл\w*|описан\w*|найден\w*|"
    r"содержит\w*|даёт\w*|дает\w*)|неизвестн\w*|недоступн\w*)", re.I)

_INFO_WORD = re.compile(
    r"(информац\w*|данн\w*|сведен\w*|подробност\w*|детал\w*|описан\w*|"
    r"характеристик\w*|спецификац\w*|документац\w*|требован\w*|"
    r"возможност\w*|особенност\w*)", re.I)


# Фразы-тавтологии: «для персонажа X доступен новый риг». В канале о ригах
# это не информация, а строчка ради строчки — читателю хватает названия.
_EMPTY_LINE = re.compile(
    r"^(для\s+(персонажа|героя)\s+)?[^.]{0,45}?"
    r"(доступен|доступна|создан|создана|выпущен|выпущена|представлен|"
    r"представлена|появился|появилась|вышел|вышла)\s+"
    r"(новый|новая|бесплатный|бесплатная)?\s*(риг|модель)\s*[.!]?$", re.I)


def drop_no_info(text):
    """Выбросить предложения, которые сообщают, что информации нет.

    Отсекается только связка «слово про информацию + слово про её
    отсутствие» в одном предложении, поэтому нормальные фразы вроде
    «в комплект входит пикер» остаются нетронутыми.
    """
    text = (text or "").strip()
    if not text:
        return ""
    keep = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        clean = sentence.strip()
        if not clean:
            continue
        if _INFO_WORD.search(clean) and _ABSENCE.search(clean):
            continue
        if _EMPTY_LINE.match(clean):
            continue
        keep.append(clean)
    out = " ".join(keep).strip()
    # если после чистки осталась только оборванная связка — считаем пустым
    if len(out) < 25:
        return ""
    return out


def _safe(msg):
    text = str(msg)
    if GEMINI_KEY:
        text = text.replace(GEMINI_KEY, "***")
    return re.sub(r"key=[^&\s]+", "key=***", text)


def available_models():
    if _MODELS:
        return _MODELS
    for ver in ("v1beta", "v1"):
        try:
            r = requests.get(
                "https://generativelanguage.googleapis.com/{}/models".format(ver),
                params={"key": GEMINI_KEY, "pageSize": 200}, timeout=40)
            if not r.ok:
                log("  ! список моделей ({}): {}".format(ver, r.status_code))
                continue
            names = [(ver, m["name"].split("/")[-1])
                     for m in r.json().get("models", [])
                     if "generateContent" in (m.get("supportedGenerationMethods") or [])]
            if names:
                log("  доступно моделей: {} ({})".format(
                    len(names), ", ".join(n for _, n in names[:5])))
                _MODELS.extend(names)
                return _MODELS
        except Exception as e:
            log("  ! список моделей ({}): {}".format(ver, _safe(e)[:120]))
    return []


def model_queue():
    found = available_models()
    if not found:
        return [("v1beta", config.GEMINI_MODEL)] + \
               [("v1beta", m) for m in config.GEMINI_FALLBACK_MODELS]
    bad = ("tts", "image", "embedding", "live", "audio", "native", "vision",
           "thinking", "learnlm", "aqa")
    usable = [(v, n) for v, n in found if not any(b in n for b in bad)] or found

    def rank(item):
        _, name = item
        prefs = [config.GEMINI_MODEL] + list(config.GEMINI_FALLBACK_MODELS)
        alias = 1 if ("latest" in name or name.endswith("-preview")) else 0
        if name in prefs:
            return (0, alias, prefs.index(name))
        if "flash-lite" in name:
            return (1, alias, len(name))
        if "flash" in name:
            return (2, alias, len(name))
        if "gemma" in name:
            return (3, alias, len(name))
        if "pro" in name:
            return (4, alias, len(name))
        return (5, alias, len(name))

    return sorted(usable, key=rank)[:6]


def ai_describe(rig):
    """Текст поста от ИИ. None — если ИИ недоступен."""
    if not GEMINI_KEY:
        return None
    facts = {k: v for k, v in (
        ("название", rig["name"]),
        ("автор", rig["author"]),
        ("софт", rig["software"]),
        ("бесплатный", rig["free"]),
        ("цена", rig["price"]),
        ("лицензия", rig["license"]),
        ("отзывы", rig["rating"]),
        ("описание с сайта", (rig["description"] or "")[:4000]),
    ) if v not in (None, "", [])}

    body = {
        "contents": [{"parts": [{"text": PROMPT.format(
            n=config.SUMMARY_SENTENCES, lim=config.SUMMARY_MAX_CHARS,
            facts=json.dumps(facts, ensure_ascii=False, indent=1))}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 700},
    }

    for ver, model in model_queue():
        r = None
        for pause in (6, 18):
            try:
                r = requests.post(GEMINI_URL.format(ver=ver, model=model),
                                  params={"key": GEMINI_KEY}, json=body, timeout=90)
            except Exception as e:
                log("  ! Gemini {}: {}".format(model, _safe(e)[:120]))
                r = None
                break
            if r.status_code not in (429, 500, 502, 503):
                break
            log("  ! {} перегружена ({}), жду {} c".format(model, r.status_code, pause))
            time.sleep(pause)

        if r is None:
            continue
        if r.status_code in (400, 403, 404):
            log("  ! модель {} недоступна ({}), беру следующую".format(
                model, r.status_code))
            continue
        if not r.ok:
            log("  ! {} ответила {}, беру следующую".format(model, r.status_code))
            continue
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = re.sub(r"\s+", " ", "".join(p.get("text", "") for p in parts))
            text = text.strip().strip('"')
        except Exception as e:
            log("  ! не разобрал ответ {}: {}".format(model, e))
            continue
        if len(text) < 40:
            continue
        text = smart_trim(text, config.SUMMARY_SENTENCES + 1,
                          config.SUMMARY_MAX_CHARS)
        text = drop_no_info(text)
        if not text:
            log("  ИИ нечего сказать по делу — пост выйдет без описания")
            return ""
        log("  текст написан ИИ ({}, {} симв.)".format(model, len(text)))
        return text
    return None


# ---------------------------------------------------------------- пост

def build_caption(rig, body):
    lines = ["<b>{}</b>".format(html.escape(rig["name"]))]
    if body:
        lines += ["", html.escape(body)]
    lines.append("")

    meta_parts = []
    if rig["software"]:
        meta_parts.append("🧩 {}".format(html.escape(rig["software"])))
    if rig["author"]:
        meta_parts.append("👤 {}".format(html.escape(rig["author"])))
    if rig["free"] is None:
        meta_parts.append("🔎 Условия — на странице автора")
    elif rig["free"]:
        meta_parts.append("💚 Бесплатно")
    else:
        meta_parts.append("💰 {}".format(html.escape(rig["price"] or "платный")))
    if rig["rating"]:
        meta_parts.append("⭐ {}".format(html.escape(rig["rating"])))
    if rig["license"]:
        meta_parts.append("📄 {}".format(html.escape(rig["license"])))
    if rig["size_mb"]:
        meta_parts.append("💾 {:.1f} МБ".format(float(rig["size_mb"])))
    lines += meta_parts

    label = "Купить" if rig["free"] is False else "Открыть"
    if rig["free"] is True:
        label = "Скачать"
    lines += ["", '🔗 <a href="{}">{} у автора</a>'.format(
        html.escape(rig["url"], quote=True), label)]

    seen, tags = set(), []
    for t in (rig["tags"] + " " + config.EXTRA_HASHTAGS).split():
        if t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    if tags:
        lines += ["", " ".join(tags)]
    return "\n".join(lines)


def tg(method, data=None, files=None):
    r = requests.post(TG_API.format(token=BOT_TOKEN, method=method),
                      data=data, files=files, timeout=60)
    try:
        payload = r.json()
    except Exception:
        raise RuntimeError("Telegram вернул не-JSON: {}".format(r.text[:200]))
    if not payload.get("ok"):
        raise RuntimeError("Telegram: {}".format(payload.get("description")))
    return payload["result"]


class NoImage(Exception):
    """Картинки для рига не нашлось — публиковать такой пост не будем."""


# мусорные картинки, которые часто лежат на страницах
_JUNK_IMG = re.compile(
    r"(logo|icon|favicon|avatar|sprite|banner|placeholder|blank|spacer|"
    r"pixel|button|badge|arrow|star|rating|social|facebook|twitter|"
    r"youtube|patreon|paypal|cart|search|"
    # оформление сайта, а не персонаж: шапки, обложки, заглушки
    r"header|footer|masthead|hero-|cover-|-cover|default|profile|"
    r"watermark|share-image|og-image|og_image)", re.I)

_IMG_EXT = re.compile(r"\.(jpe?g|png|webp)(\?|$)", re.I)


def _image_size(data):
    """Размеры картинки без сторонних библиотек. (0,0) — если не понял."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w = int.from_bytes(data[16:20], "big")
            h = int.from_bytes(data[20:24], "big")
            return w, h
        if data[:2] == b"\xff\xd8":  # JPEG
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h = int.from_bytes(data[i + 5:i + 7], "big")
                    w = int.from_bytes(data[i + 7:i + 9], "big")
                    return w, h
                seg = int.from_bytes(data[i + 2:i + 4], "big")
                if seg <= 0:
                    break
                i += 2 + seg
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return 512, 512  # размеры webp не парсим, считаем годным
    except Exception:
        pass
    return 0, 0


def download_image(url, min_side=None):
    """Скачать и проверить картинку. None — если не годится."""
    if not url or not url.startswith("http"):
        return None
    min_side = min_side or getattr(config, "MIN_IMAGE_SIDE", 300)
    try:
        r = requests.get(url, headers={"User-Agent": UA},
                         timeout=config.HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.content
    except Exception:
        return None

    if len(data) < 8000 or len(data) > 9_000_000:
        return None
    if not (data[:2] == b"\xff\xd8" or data[:8] == b"\x89PNG\r\n\x1a\n"
            or data[:4] == b"RIFF"):
        return None

    w, h = _image_size(data)
    if w and h and (w < min_side or h < min_side):
        return None

    name = os.path.basename(url.split("?")[0]) or "preview.jpg"
    if "." not in name:
        name += ".jpg"
    return (name, data)


def _name_words(name):
    """Значимые слова из названия рига — по ним узнаём «свою» картинку."""
    words = re.findall(r"[a-z0-9]{3,}", (name or "").lower())
    skip = {"rig", "rigs", "maya", "blender", "the", "and", "for", "free",
            "paid", "character", "pro", "new", "vol", "part"}
    return [w for w in words if w not in skip]


def images_from_page(url, name=""):
    """Все кандидаты в картинки со страницы рига, лучшие — первыми."""
    page = get(url)
    if not page:
        return []

    out, seen = [], set()

    def add(candidate):
        if not candidate:
            return
        candidate = html.unescape(candidate.strip())
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        elif candidate.startswith("/"):
            m = re.match(r"(https?://[^/]+)", url)
            if not m:
                return
            candidate = m.group(1) + candidate
        if not candidate.startswith("http"):
            return
        if candidate in seen:
            return
        if _JUNK_IMG.search(candidate):
            return
        seen.add(candidate)
        out.append(candidate)

    add(meta(page, "og:image"))
    add(meta(page, "twitter:image"))
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', page, re.I):
        src = m.group(1)
        if _IMG_EXT.search(src):
            add(src)

    # Картинка, в адресе которой встречается имя персонажа, почти наверняка
    # относится к этому ригу, а не к оформлению сайта. Такие — вперёд.
    words = _name_words(name)
    if words:
        def own(candidate):
            low = candidate.lower()
            return 0 if any(w in low for w in words) else 1
        out.sort(key=own)
    return out[:12]


DDG = "https://duckduckgo.com"


def search_image(query):
    """Поиск картинки в интернете. Без ключей, через DuckDuckGo."""
    try:
        r = requests.post(DDG + "/", data={"q": query},
                          headers={"User-Agent": UA},
                          timeout=config.HTTP_TIMEOUT)
        m = re.search(r"vqd=[\"']?([\d-]+)[\"']?", r.text)
        if not m:
            log("    поиск картинки: не получил токен")
            return []
        vqd = m.group(1)
        r = requests.get(
            DDG + "/i.js",
            params={"l": "us-en", "o": "json", "q": query, "vqd": vqd,
                    "f": ",,,", "p": "1"},
            headers={"User-Agent": UA, "Referer": DDG + "/"},
            timeout=config.HTTP_TIMEOUT)
        if not r.ok:
            return []
        data = r.json()
    except Exception as e:
        log("    поиск картинки не удался: {}".format(str(e)[:100]))
        return []

    out = []
    for item in (data.get("results") or [])[:15]:
        src = item.get("image") or ""
        if src and not _JUNK_IMG.search(src):
            out.append(src)
    return out


def _img_hash(data):
    return hashlib.sha1(data).hexdigest()[:16]


def _short(url):
    """Короткая запись адреса для лога: хост и последний кусок пути."""
    try:
        parts = url.split("?")[0].split("/")
        return parts[2] + "/…/" + (parts[-1] or parts[-2])[:40]
    except Exception:
        return url[:60]


def find_image(rig, used_hashes=()):
    """Картинка персонажа для поста. Обязательная и обязательно своя.

    Проверка на повтор здесь не украшение, а суть: у сайтов есть общие
    шапки и обложки, и без неё два разных персонажа выходят в канал с
    одной и той же картинкой. Любая картинка, которая уже была в канале,
    считается чужой и отбрасывается — берём следующего кандидата.
    """
    tried = 0

    def take(got, where):
        """Принять кандидата, если такой картинки в канале ещё не было."""
        if not got:
            return None
        name, data = got
        digest = _img_hash(data)
        if digest in used_hashes:
            log("    ↺ эта картинка уже была в канале — беру другую")
            return None
        rig["_img_hash"] = digest
        log("    картинка {}: {}".format(where, _short(rig.get("_img_src", ""))))
        return got

    # 1. превью, которое уже нашлось при сборе
    if rig.get("thumb"):
        rig["_img_src"] = rig["thumb"]
        got = take(download_image(rig.get("thumb")), "из превью источника")
        if got:
            return got

    # 2. любые подходящие картинки со страницы рига — там, куда ведёт ссылка
    for src in images_from_page(rig["url"], rig.get("name", "")):
        if out_of_time():
            break
        tried += 1
        rig["_img_src"] = src
        got = take(download_image(src), "со страницы рига")
        if got:
            return got
        if tried > 10:
            break

    # 3. поиск в интернете по имени персонажа
    if getattr(config, "IMAGE_WEB_SEARCH", True):
        soft = (rig.get("software") or "").split(",")[0].strip()
        query = " ".join(x for x in [rig.get("name"), soft, "character rig"] if x)
        for src in search_image(query)[:10]:
            if out_of_time():
                break
            rig["_img_src"] = src
            got = take(download_image(src), "найдена поиском")
            if got:
                return got

    return None


def publish(rig, used_hashes=()):
    if rig.get("ready"):
        body = rig["description"]
    else:
        body = ai_describe(rig)
        # "" — ИИ ответил, но говорить нечего; None — ИИ вообще не сработал
        if body is None:
            body = smart_trim(rig["description"], config.SUMMARY_SENTENCES,
                              config.SUMMARY_MAX_CHARS)
            if body:
                log("  ИИ не сработал — беру описание с сайта как есть")
    body = drop_no_info(body)

    # Картинка обязательна, и обязательно новая: пост без изображения
    # персонажа не публикуем, повтор чужой картинки — тоже.
    photo = find_image(rig, used_hashes)
    if not photo:
        raise NoImage(rig["name"])

    caption = build_caption(rig, body)
    if len(caption) > 1024:
        caption = caption[:1020].rsplit("\n", 1)[0]

    tg("sendPhoto", data={"chat_id": CHANNEL_ID, "caption": caption,
                          "parse_mode": "HTML"}, files={"photo": photo})
    return "с картинкой"


# ---------------------------------------------------------------- главное

def is_maya(rig):
    text = " ".join([
        rig.get("software") or "", rig.get("name") or "",
        (rig.get("description") or "")[:300],
    ]).lower()
    return "maya" in text


def is_blender(rig):
    text = " ".join([
        rig.get("software") or "", rig.get("name") or "",
        (rig.get("description") or "")[:300],
    ]).lower()
    return "blender" in text and "maya" not in text


def pick(pool, recent_sources, want_archive, want_maya=True):
    """Следующий риг — по трём предпочтениям сразу.

    Порядок важности: софт (Maya чаще, чем Blender), затем дорожка
    (новинка или архив), затем источник, который давно не мелькал.
    Ни одно из предпочтений не жёсткое: если подходящего нет,
    берём лучшее из того, что есть, а не встаём колом.
    """
    if not pool:
        return None

    def score(item):
        idx, rig = item
        soft_ok = is_maya(rig) if want_maya else is_blender(rig)
        lane_ok = bool(rig.get("fresh")) != want_archive
        fresh_source = rig.get("source") not in recent_sources
        # меньше — лучше
        return (0 if soft_ok else 1,
                0 if lane_ok else 1,
                0 if fresh_source else 1,
                idx)

    return min(enumerate(pool), key=score)[1]


def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        log("ОШИБКА: не заданы BOT_TOKEN и/или CHANNEL_ID")
        sys.exit(1)

    me = tg("getMe")
    log("Бот @{} на связи".format(me.get("username")))
    if not GEMINI_KEY:
        log("GEMINI_API_KEY не задан — тексты пойдут как есть с сайтов")

    state = load_state()
    known = set(state["posted"])
    log("В памяти {} опубликованных ригов".format(len(known)))

    # шагаем по страницам архива, чтобы не топтаться на первой
    depth = int(getattr(config, "ARCHIVE_MAX_PAGE", 19))
    step = int(config.HIGHEND3D_POPULAR.get("pages", 2))
    _archive_page[0] = (int(state.get("archive_page", 0)) % max(1, depth)) + 1
    state["archive_page"] = int(state.get("archive_page", 0)) + step

    # то же самое для архива Animation Buffet: 660 записей, шагаем по 50
    total = int(getattr(config, "BUFFET_TOTAL", 660))
    _buffet_index[0] = (int(state.get("buffet_index", 0)) % max(1, total)) + 1
    state["buffet_index"] = int(state.get("buffet_index", 0)) + 50

    # и по архиву Airtable: окно едет вперёд на limit записей за запуск
    air = getattr(config, "AIRTABLE", {}) or {}
    air_step = int(air.get("limit", 24))
    _airtable_index[0] = int(state.get("airtable_index", 0))
    state["airtable_index"] = _airtable_index[0] + air_step

    log("Собираю риги...")
    rigs = collect()
    # сбор закончен — у публикации свой запас времени, чужой она не наследует
    reset_clock(int(getattr(config, "PUBLISH_BUDGET_SEC", 200)))
    fresh = [r for r in rigs if r["id"] not in known]
    if config.REQUIRE_DESCRIPTION:
        fresh = [r for r in fresh if (r["description"] or "").strip()]
    log("Найдено: {}, из них новых: {}".format(len(rigs), len(fresh)))

    if not fresh:
        log("Новых ригов нет. Готово.")
        return

    if os.environ.get("CATCH_UP", "").lower() in ("1", "true", "yes"):
        for r in fresh:
            state["posted"].append(r["id"])
        save_state(state)
        log("Режим догона: {} помечено виденными, ничего не опубликовано.".format(
            len(fresh)))
        return

    posted = publish_loop(state, fresh)
    log("Готово. Опубликовано за этот запуск: {}".format(posted))


def publish_one(state, pool):
    """Опубликовать один риг из пула. True — получилось."""
    if not pool:
        return False
    every = getattr(config, "ARCHIVE_EVERY", 2)
    counter = int(state.get("counter", 0))
    want_archive = bool(every) and (counter % every == every - 1)

    # Maya чаще Blender: из каждых MAYA_SHARE постов один отдаём Blender.
    share = max(1, getattr(config, "MAYA_SHARE", 4))
    want_maya = (counter % share) != (share - 1)

    # Если у рига не находится картинка, он не публикуется — берём
    # следующего кандидата, а не оставляем канал без поста.
    for _ in range(getattr(config, "IMAGE_RETRY_CANDIDATES", 5)):
        rig = pick(pool, state["sources"], want_archive, want_maya)
        if rig is None:
            return False
        pool.remove(rig)
        try:
            return _do_publish(state, rig, counter)
        except NoImage as e:
            log("  ⤼ пропуск «{}»: не нашёл картинку".format(str(e)[:45]))
            state["posted"].append(rig["id"])
            save_state(state)
            if out_of_time():
                return False
    return False


def _do_publish(state, rig, counter):
    try:
        used = set(state.get("image_hashes") or ())
        how = publish(rig, used)
        log("  ✔ опубликовано {} [{}]: {}".format(
            how, "новинка" if rig.get("fresh") else "архив", rig["name"][:55]))
        state["counter"] = counter + 1
        state["posted"].append(rig["id"])
        state["sources"].append(rig["source"])
        # запоминаем картинку, чтобы она больше никогда не повторилась
        digest = rig.get("_img_hash")
        if digest:
            hashes = state.setdefault("image_hashes", [])
            hashes.append(digest)
            del hashes[:-500]
        save_state(state)
        return True
    except NoImage:
        raise           # обрабатывается выше: берём следующего кандидата
    except Exception as e:
        log("  ✖ не опубликовал «{}»: {}".format(rig["name"][:40], e))
        state["posted"].append(rig["id"])
        save_state(state)
        return False


def publish_loop(state, pool):
    """Публикуем с шагом POST_EVERY_MINUTES, пока не выйдет LOOP_MINUTES.

    Шаг держим сами: на расписание GitHub полагаться нельзя, оно
    пропускает короткие интервалы. Отсчёт идёт от времени последнего
    поста и хранится в posted.json, поэтому ритм не сбивается на
    границе запусков и восстанавливается после пропусков.
    """
    step = getattr(config, "POST_EVERY_MINUTES", 20) * 60
    window = getattr(config, "LOOP_MINUTES", 0) * 60
    limit = config.MAX_POSTS_PER_RUN
    started = time.time()
    posted = 0
    last = float(state.get("last_post_ts", 0) or 0)

    # Короткий режим: опубликовать и сразу выйти.
    # Так работает бот новостей GTA 6, и у него ритм не рвётся.
    # Длинные задания GitHub душит: пока одно висит, срабатывания
    # расписания в это время просто отбрасываются.
    if window <= 0:
        gap = getattr(config, "MIN_GAP_MINUTES", 15) * 60
        since = time.time() - last
        if last and since < gap:
            log("С прошлого поста прошло {} мин из {} — рано, выхожу."
                .format(int(since // 60), gap // 60))
            return 0
        for _ in range(limit):
            if not pool or not publish_one(state, pool):
                break
            posted += 1
            state["last_post_ts"] = time.time()
            save_state(state)
        return posted

    # Длинный режим (LOOP_MINUTES > 0): держим шаг внутри одного запуска.
    while True:
        wait = step - (time.time() - last)
        if wait > 0:
            if (time.time() - started) + wait > window:
                log("Окно запуска кончается, следующий пост — в новом запуске "
                    "(через {} мин {} с).".format(int(wait) // 60, int(wait) % 60))
                break
            log("Следующий пост через {} мин {} с".format(
                int(wait) // 60, int(wait) % 60))
            time.sleep(wait)

        if not pool:
            log("Пул пуст, добираю источники...")
            known = set(state["posted"])
            pool = [r for r in collect() if r["id"] not in known]
            if config.REQUIRE_DESCRIPTION:
                pool = [r for r in pool if (r["description"] or "").strip()]
            log("  добавилось: {}".format(len(pool)))

        if pool and publish_one(state, pool):
            posted += 1
            last = time.time()
            state["last_post_ts"] = last
            save_state(state)
        else:
            # публиковать нечего — не долбим источники каждую секунду
            log("Нечего публиковать, жду.")
            last = time.time() - step + 120

        if posted >= limit:
            log("Достигнут потолок постов за запуск ({}).".format(limit))
            break
        if time.time() - started > window:
            break

    return posted


if __name__ == "__main__":
    main()
