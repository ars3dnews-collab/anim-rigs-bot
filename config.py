# -*- coding: utf-8 -*-
"""Настройки бота. Правится прямо в репозитории — Actions подхватит при следующем запуске."""

# Сколько ригов публиковать за один запуск воркфлоу
MAX_POSTS_PER_RUN = 1

# Пауза между постами внутри одного запуска, секунд
DELAY_BETWEEN_POSTS = 5

# Таймаут одного HTTP-запроса, секунд. Highend3d временами тупит,
# и без этого один медленный сайт вешает весь запуск.
HTTP_TIMEOUT = 12

# Сколько секунд всего отводится на обход источников. Как только время
# вышло, бот перестаёт ходить по сайтам и публикует из того, что успел.
COLLECT_BUDGET_SEC = 100

# Длина текста от ИИ
SUMMARY_SENTENCES = 4
SUMMARY_MAX_CHARS = 450

# Модель Gemini по умолчанию. Если ключу доступны другие — бот подберёт сам.
GEMINI_MODEL = "gemini-flash-lite-latest"
GEMINI_FALLBACK_MODELS = ("gemini-flash-latest", "gemini-2.0-flash")

# Хэштеги в конец каждого поста
EXTRA_HASHTAGS = "#rig #риг"

# ---------------------------------------------------------------------------
# Автопоиск. Курируемый список seed_rigs.yml работает всегда.
# ---------------------------------------------------------------------------

# Чередование: каждый N-й пост берётся из архива, остальные — новинки.
# 2 — через один. 3 — две новинки, потом архив. 0 — только новинки.
ARCHIVE_EVERY = 2

# Blender Studio — Rain, Snow, Vincent, Einar, каст Sprite Fright. CC-BY.
BLENDER_STUDIO = {"enabled": True, "limit": 6}

# Highend3d — крупнейшая библиотека Maya-ригов.
# newest — новинки, popular — проверенное временем (сортировка по скачиваниям).
HIGHEND3D_FREE = {"enabled": True, "limit": 8, "pages": 2, "sort": "newest"}
HIGHEND3D_POPULAR = {"enabled": True, "limit": 8, "pages": 2, "sort": "downloads"}
HIGHEND3D_PAID = {"enabled": True, "limit": 4, "pages": 1, "sort": "newest"}

# ---------------------------------------------------------------------------
# Новинки со всего интернета.
# У сайтов с ригами нет ни API, ни RSS — зато они есть у профильных изданий,
# которые пишут про каждый заметный релиз. Из ленты берутся только записи,
# в заголовке или описании которых есть слова из RIG_KEYWORDS.
# ---------------------------------------------------------------------------
# Тематические ленты точнее общих: там уже отобрано редакцией по теме.
# Если какая-то отдаёт 404 — просто убери строку, бот переживёт.
NEWS_FEEDS = [
    ("BlenderNation · rigging", "https://www.blendernation.com/tag/rigging/feed/"),
    ("BlenderNation · rig", "https://www.blendernation.com/tag/rig/feed/"),
    ("BlenderNation · character", "https://www.blendernation.com/tag/character/feed/"),
    ("CG Channel · rigging", "https://www.cgchannel.com/tag/rigging/feed/"),
    ("80 Level · rigging", "https://80.lv/tag/rigging/feed/"),
]

# Сколько записей смотреть в каждой ленте
NEWS_LIMIT = 30

# Не старше скольких дней считать новинкой
NEWS_MAX_AGE_DAYS = 45

# Слово про риг. Проверяется ПО ГРАНИЦАМ СЛОВА, иначе "right" и "bright"
# считаются ригами — на этом канал один раз уже обжёгся.
RIG_WORDS = (
    r"rig", r"rigs", r"rigged", r"rigging", r"auto-?rig(?:ger|ging)?",
    r"риг", r"риги", r"ригги?нг",
)

# Одного слова "rig" мало: нужен ещё признак, что речь о персонаже
# или о выложенном ассете, а не о статье про профессию риггера.
SUBJECT_WORDS = (
    r"character", r"creature", r"biped", r"quadruped", r"humanoid",
    r"cartoon", r"body", r"face", r"facial", r"anatomy",
    r"персонаж\w*", r"существ\w*",
)

RELEASE_WORDS = (
    r"free", r"released?", r"release", r"available", r"download\w*",
    r"launch\w*", r"asset", r"pack", r"library", r"updated?", r"version",
    r"бесплатн\w*", r"релиз", r"скачат\w*",
)

# Явно не риг персонажа
RIG_STOPWORDS = (
    "job", "vacancy", "hiring", "career", "salary", "interview",
    "course", "webinar", "tutorial", "podcast", "breakdown",
    "how to become", "showreel", "contest", "awards",
    "вакансия", "карьер", "зарплат", "интервью", "курс", "вебинар",
)

# Не публиковать риги, у которых не нашлось ни описания, ни картинки
REQUIRE_DESCRIPTION = True
