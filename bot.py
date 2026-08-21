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
        return {"posted": [], "sources": [], "counter": 0}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("posted", [])
        data.setdefault("sources", [])
        data.setdefault("counter", 0)
        return data
    except Exception as e:
        log("posted.json не читается ({}), начинаю с чистого списка".format(e))
        return {"posted": [], "sources": [], "counter": 0}


def save_state(state):
    state["posted"] = state["posted"][-2000:]
    state["sources"] = state["sources"][-5:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


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


def get(url, **kw):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=40, **kw)
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


def fetch_highend3d(limit, pages, paid, sort="newest", tag=None):
    base = (HE + "/character-rigs/c/marketplace") if paid \
        else (HE + "/maya/character-rigs/c/downloads")
    links, seen = [], set()
    for page_no in range(1, pages + 1):
        listing = get("{}?page={}&sort={}".format(base, page_no, sort))
        if not listing:
            break
        for href in re.findall(r'href="(/[^"#?]*(?:downloads|marketplace)[^"#?]*)"', listing):
            if href.count("/") < 4:
                continue
            url = HE + href
            if url not in seen and url != base:
                seen.add(url)
                links.append(url)
        time.sleep(0.5)

    out = []
    for url in links:
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


def looks_like_rig(text):
    low = (text or "").lower()
    if any(sw in low for sw in config.RIG_STOPWORDS):
        return False
    return any(kw in low for kw in config.RIG_KEYWORDS)


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


def collect():
    rigs = []

    for r in load_seed():
        r["fresh"] = False
        rigs.append(r)
    log("  курируемый список: {}".format(len(rigs)))

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
        got = fetch_highend3d(c.get("limit", 8), c.get("pages", 2), paid=False,
                              sort=c.get("sort", "downloads"),
                              tag="highend3d-popular")
        log("  highend3d-popular: {}".format(len(got)))
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
- НИЧЕГО НЕ ВЫДУМЫВАЙ. Если про возможности рига в данных ничего нет, напиши \
только то, что известно достоверно. Две сухие строки лучше выдумки про \
несуществующий лицевой риг;
- не пиши цену, лицензию и название софта — они уже есть в посте отдельной \
строкой;
- термины как принято в индустрии: риг, пикер, IK/FK, блендшейпы, скиннинг;
- нейтральный тон, без рекламы и восклицательных знаков, без обращений \
к читателю;
- в ответе только текст поста, без заголовка, разметки и хэштегов.

Данные о риге:
{facts}"""


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
        log("  текст написан ИИ ({}, {} симв.)".format(model, len(text)))
        return smart_trim(text, config.SUMMARY_SENTENCES + 1, config.SUMMARY_MAX_CHARS)
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


def download_image(url):
    if not url or not url.startswith("http"):
        return None
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        data = r.content
        if len(data) < 2000 or len(data) > 9_000_000:
            return None
        name = os.path.basename(url.split("?")[0]) or "preview.jpg"
        if "." not in name:
            name += ".jpg"
        return (name, data)
    except Exception:
        return None


def publish(rig):
    body = rig["description"] if rig.get("ready") else (ai_describe(rig) or "")
    if not body and not rig.get("ready"):
        body = smart_trim(rig["description"], config.SUMMARY_SENTENCES,
                          config.SUMMARY_MAX_CHARS)
        if body:
            log("  ИИ не сработал — беру описание с сайта как есть")

    caption = build_caption(rig, body)
    photo = download_image(rig.get("thumb"))

    if photo and len(caption) <= 1024:
        try:
            tg("sendPhoto", data={"chat_id": CHANNEL_ID, "caption": caption,
                                  "parse_mode": "HTML"}, files={"photo": photo})
            return "с картинкой"
        except Exception as e:
            log("  ! с картинкой не вышло ({}), шлю текстом".format(e))

    tg("sendMessage", data={"chat_id": CHANNEL_ID, "text": caption,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": "false"})
    return "текстом"


# ---------------------------------------------------------------- главное

def pick(pool, recent_sources, want_archive):
    """Следующий риг.

    Сначала выбираем дорожку — новинки или архив, — потом внутри неё
    берём тот источник, который давно не мелькал в ленте.
    """
    primary = [r for r in pool if bool(r.get("fresh")) != want_archive]
    for group in (primary, pool):
        if not group:
            continue
        for rig in group:
            if rig["source"] not in recent_sources:
                return rig
        return group[0]
    return pool[0]


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

    log("Собираю риги...")
    rigs = collect()
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

    posted = 0
    counter = int(state.get("counter", 0))
    every = getattr(config, "ARCHIVE_EVERY", 2)
    while posted < config.MAX_POSTS_PER_RUN and fresh:
        want_archive = bool(every) and (counter % every == every - 1)
        rig = pick(fresh, state["sources"], want_archive)
        fresh.remove(rig)
        try:
            how = publish(rig)
            log("  ✔ опубликовано {} [{}]: {}".format(
                how, "новинка" if rig.get("fresh") else "архив", rig["name"][:55]))
            counter += 1
            state["counter"] = counter
            state["posted"].append(rig["id"])
            state["sources"].append(rig["source"])
            save_state(state)
            posted += 1
            if posted < config.MAX_POSTS_PER_RUN:
                time.sleep(config.DELAY_BETWEEN_POSTS)
        except Exception as e:
            log("  ✖ не опубликовал «{}»: {}".format(rig["name"][:40], e))
            state["posted"].append(rig["id"])
            save_state(state)

    log("Готово. Опубликовано: {}".format(posted))


if __name__ == "__main__":
    main()
