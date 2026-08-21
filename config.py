# -*- coding: utf-8 -*-
"""Настройки бота. Правится прямо в репозитории — Actions подхватит при следующем запуске."""

# Сколько ригов публиковать за один запуск воркфлоу
MAX_POSTS_PER_RUN = 1

# Пауза между постами внутри одного запуска, секунд
DELAY_BETWEEN_POSTS = 5

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
NEWS_FEEDS = [
    ("BlenderNation", "https://www.blendernation.com/feed/"),
    ("CG Channel", "https://www.cgchannel.com/feed/"),
    ("80 Level", "https://80.lv/feed/"),
    ("Blender Studio", "https://studio.blender.org/blog/feed/"),
]

# Сколько записей смотреть в каждой ленте
NEWS_LIMIT = 30

# Не старше скольких дней считать новинкой
NEWS_MAX_AGE_DAYS = 45

RIG_KEYWORDS = (
    "rig", "rigged", "rigging", "character setup", "auto-rig", "autorig",
    "риг", "риггинг",
)

# Отсекаем то, что ригом не является
RIG_STOPWORDS = (
    "job", "vacancy", "hiring", "course review", "webinar replay",
    "рынок труда", "вакансия",
)

# Не публиковать риги, у которых не нашлось ни описания, ни картинки
REQUIRE_DESCRIPTION = True
