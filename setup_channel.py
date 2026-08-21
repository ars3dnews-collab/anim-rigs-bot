# -*- coding: utf-8 -*-
"""
Оформление канала: название, описание, закреплённый пост.

Запускается вручную из вкладки Actions — воркфлоу "Setup channel".
Токен берётся из secrets, никуда не выводится.

Текст правится прямо здесь, в этом файле.
"""

import os
import sys
import html

import requests

TG_API = "https://api.telegram.org/bot{token}/{method}"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()

# ---------------------------------------------------------------------------
# Название канала. До 128 символов.
# ---------------------------------------------------------------------------
TITLE = "Риги · Maya и Blender"

# ---------------------------------------------------------------------------
# Описание канала. Жёсткий лимит Telegram — 255 символов.
# Видно в шапке и в поиске, поэтому первая фраза должна объяснять суть.
# ---------------------------------------------------------------------------
DESCRIPTION = (
    "Персонажные риги для Maya и Blender: свежие релизы со всего мира "
    "и проверенная временем классика. В каждом посте — что умеет риг, "
    "цена и ссылка на автора. Без перезаливов чужих файлов."
)

# ---------------------------------------------------------------------------
# Закреплённый пост. Здесь можно писать длинно, HTML-разметка работает.
# ---------------------------------------------------------------------------
PINNED = """<b>Риги для аниматоров — в одном месте</b>

Здесь выходят персонажные риги для <b>Maya</b> и <b>Blender</b>: и новинки, которые появились на этой неделе, и вещи, на которых учится индустрия уже десять лет.

<b>Что в каждом посте</b>
Что это за персонаж и чем риг полезен: лицевые контролы, IK/FK, пикер, требования к версии софта. Дальше — цена или пометка «бесплатно», лицензия, вес файла и ссылка на страницу автора.

<b>Навигация по тегам</b>
#maya · #blender — по софту
#free · #paid — бесплатные и платные
#новинка — свежие релизы

<b>Почему нет самих файлов</b>
Мы не перезаливаем риги. У большинства авторов лицензия это прямо запрещает: у AnimSchool, Animation Mentor, Mery и CGTarian сказано, что забирать риг нужно только с их сайта. Скачивание по ссылке из поста — это ещё и способ сказать спасибо человеку, который сделал риг бесплатно.

<b>Нашёл хороший риг, которого тут нет?</b>
Присылай ссылку в личку — добавим."""


def tg(method, **data):
    r = requests.post(TG_API.format(token=BOT_TOKEN, method=method),
                      data=data, timeout=60)
    payload = r.json()
    if not payload.get("ok"):
        raise RuntimeError("{}: {}".format(method, payload.get("description")))
    return payload["result"]


def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        print("ОШИБКА: не заданы BOT_TOKEN и/или CHANNEL_ID")
        sys.exit(1)

    me = tg("getMe")
    print("Бот @{} на связи".format(me.get("username")))

    if len(DESCRIPTION) > 255:
        print("ОШИБКА: описание длиннее 255 символов ({})".format(len(DESCRIPTION)))
        sys.exit(1)

    try:
        tg("setChatTitle", chat_id=CHANNEL_ID, title=TITLE)
        print("Название: {}".format(TITLE))
    except Exception as e:
        print("! название не поменялось: {}".format(e))

    try:
        tg("setChatDescription", chat_id=CHANNEL_ID, description=DESCRIPTION)
        print("Описание: {} символов".format(len(DESCRIPTION)))
    except Exception as e:
        print("! описание не поменялось: {}".format(e))

    msg = tg("sendMessage", chat_id=CHANNEL_ID, text=PINNED,
             parse_mode="HTML", disable_web_page_preview="true")
    print("Пост-приветствие опубликован (id {})".format(msg["message_id"]))

    tg("pinChatMessage", chat_id=CHANNEL_ID, message_id=msg["message_id"],
       disable_notification="true")
    print("Закреплён.")


if __name__ == "__main__":
    main()
