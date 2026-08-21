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

# Blender Studio — Rain, Snow, Vincent, Einar, каст Sprite Fright. CC-BY.
BLENDER_STUDIO = {"enabled": True, "limit": 6}

# Highend3d — крупнейшая библиотека Maya-ригов
HIGHEND3D_FREE = {"enabled": True, "limit": 8, "pages": 2}
HIGHEND3D_PAID = {"enabled": True, "limit": 4, "pages": 1}

# Не публиковать риги, у которых не нашлось ни описания, ни картинки
REQUIRE_DESCRIPTION = True
