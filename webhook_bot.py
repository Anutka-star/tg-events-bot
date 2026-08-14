# -*- coding: utf-8 -*-
"""
Кнопочный бот (личка): меню, фильтры по темам и датам, выдача карточками,
кнопка «Написать основателям» (пересылает сообщение админу).

Файл /home/<логин>/mysite/keys.txt (или рядом с этим файлом), строки в любом порядке:
  токен бота (вида 123456:AA...)
  ссылка на events.json (https://raw.githubusercontent.com/.../events.json)
  Telegram ID админа (просто число) — для кнопки «Написать основателям»
"""
import json
import os
import re
import time
import traceback
from datetime import datetime, timedelta, date

import requests
from flask import Flask, request

HOOK_PATH = "hook-ni-7k2f9x"
HOME = os.path.expanduser("~")

app = Flask(__name__)

_raw = ""
for candidate in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.txt"),
                  os.path.join(HOME, "keys.txt")):
    if os.path.exists(candidate):
        _raw = open(candidate, encoding="utf-8-sig", errors="ignore").read()
        break
TOKEN = (re.search(r"\b(\d{6,12}:[A-Za-z0-9_-]{30,})\b", _raw) or [None, ""])[1]
EVENTS_URL = (re.search(r"(https://\S+events\.json)", _raw) or [None, ""])[1]
_admin = re.search(r"^\s*(\d{5,12})\s*$", _raw, re.M)
ADMIN_ID = _admin.group(1) if _admin else ""
API = f"https://api.telegram.org/bot{TOKEN}"

PERIOD_NAME = {"today": "Сегодня", "week": "Неделя", "month": "Месяц", "all": "Все даты"}
TOPIC_EMOJI = {"IT": "💻", "ИИ": "🤖", "Бизнес": "💼", "Английский": "🇬🇧"}
PAGE = 5

_cache = {"t": 0.0, "events": []}


def log_error(text):
    try:
        with open(os.path.join(HOME, "bot_errors.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.utcnow().isoformat()} {text}\n")
    except Exception:
        pass


def msk_today():
    return (datetime.utcnow() + timedelta(hours=3)).date()


def get_events():
    if time.time() - _cache["t"] > 600 or not _cache["events"]:
        r = requests.get(EVENTS_URL, timeout=15)
        r.raise_for_status()
        _cache["events"] = r.json().get("events", [])
        _cache["t"] = time.time()
    return _cache["events"]


def filter_events(topic, period):
    today = msk_today()
    horizon = {"today": today, "week": today + timedelta(days=7), "month": today + timedelta(days=31)}
    out = []
    for e in get_events():
        if topic != "Всё" and e.get("topic") != topic:
            continue
        iso = e.get("iso", "")
        if period == "all":
            out.append(e); continue
        if not iso:
            continue
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            continue
        if d < today or d > horizon[period]:
            continue
        out.append(e)
    out.sort(key=lambda e: e.get("iso") or "9999-12-31")
    return out


def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_card(e):
    topic = e.get("topic", "IT")
    tags = f"#{topic.replace(' ', '_')}"
    if e.get("net"):
        tags += " #Нетворкинг"
    fmt = "🖥 Онлайн" if e.get("online") else "📍 Офлайн, СПб"
    org = e.get("org") or e.get("org_page") or "см. по ссылке"
    if len(org) > 50:
        org = "см. по ссылке"
    price = e.get("price") or "см. по ссылке"
    when = e.get("date") or "дата на странице события"
    tm = e.get("time") or "—"
    lines = [
        f"{TOPIC_EMOJI.get(topic, '🎪')} <b>{html_escape(e['title'])}</b>",
        "",
        f"📅 <b>Дата:</b> {html_escape(when)}",
        f"🕒 <b>Время:</b> {html_escape(tm)}",
        f"🏷 <b>Тип:</b> {tags}",
        f"🌐 <b>Формат:</b> {fmt}",
        f"🏛 <b>Организатор:</b> {html_escape(org)}",
        f"💰 <b>Цена:</b> {html_escape(price)}",
    ]
    desc = re.sub(r"\s+", " ", e.get("desc", "") or "").strip()
    if desc:
        room = 1000 - sum(len(x) for x in lines) - len(e["url"]) - 80
        if room > 60:
            lines += ["", f"📝 {html_escape(desc[:room])}"]
    lines += ["", f"🎟 <a href=\"{e['url']}\">Регистрация / билеты</a>"]
    return "\n".join(lines)


def kb_topics():
    rows = [[{"text": "💻 IT", "callback_data": "t|IT"}, {"text": "🤖 ИИ", "callback_data": "t|ИИ"}],
            [{"text": "💼 Бизнес", "callback_data": "t|Бизнес"}, {"text": "🇬🇧 Английский", "callback_data": "t|Английский"}],
            [{"text": "📋 Всё сразу", "callback_data": "t|Всё"}]]
    if ADMIN_ID:
        rows.append([{"text": "✍️ Написать основателям", "callback_data": "write"}])
    return {"inline_keyboard": rows}


def kb_periods(topic):
    return {"inline_keyboard": [
        [{"text": "Сегодня", "callback_data": f"p|{topic}|today|0"},
         {"text": "Неделя", "callback_data": f"p|{topic}|week|0"}],
        [{"text": "Месяц", "callback_data": f"p|{topic}|month|0"},
         {"text": "Все даты", "callback_data": f"p|{topic}|all|0"}],
        [{"text": "← Темы", "callback_data": "menu"}]]}


def tg(method, payload):
    """Вызов Telegram. Ошибки сети глушим и НЕ пишем адрес запроса в лог (в нём токен)."""
    try:
        r = requests.post(f"{API}/{method}", json=payload, timeout=20)
    except Exception as ex:
        log_error(f"{method}: {type(ex).__name__}")
        return None
    if not r.ok:
        log_error(f"{method}: {r.status_code} {r.text[:250]}")
    return r


def send_card(chat, e):
    caption = build_card(e)
    if e.get("img"):
        r = tg("sendPhoto", {"chat_id": chat, "photo": e["img"], "caption": caption, "parse_mode": "HTML"})
        if r is not None and r.ok:
            return
    tg("sendMessage", {"chat_id": chat, "text": caption, "parse_mode": "HTML",
                       "disable_web_page_preview": False})


def send_page(chat, topic, period, offset):
    events = filter_events(topic, period)
    total = len(events)
    if not total:
        tg("sendMessage", {"chat_id": chat,
                           "text": f"🔎 {topic} · {PERIOD_NAME[period]}: пока ничего не нашлось. "
                                   f"Попробуйте другой период или тему.",
                           "reply_markup": {"inline_keyboard": [[{"text": "🔄 Новый фильтр", "callback_data": "menu"}]]}})
        return
    batch = events[offset:offset + PAGE]
    for e in batch:
        send_card(chat, e)
        time.sleep(0.4)
    shown_to = offset + len(batch)
    rows = []
    if shown_to < total:
        rows.append([{"text": f"Ещё ➡️ ({total - shown_to} осталось)",
                      "callback_data": f"p|{topic}|{period}|{shown_to}"}])
    rows.append([{"text": "🔄 Новый фильтр", "callback_data": "menu"}])
    tg("sendMessage", {"chat_id": chat,
                       "text": f"🔎 <b>{html_escape(topic)}</b> · {PERIOD_NAME[period]}: "
                               f"показано {shown_to} из {total}",
                       "parse_mode": "HTML", "reply_markup": {"inline_keyboard": rows}})


WELCOME = ("Привет! Я — бот «Северный интеллект» 🤖\n"
           "Подбираю мероприятия Санкт-Петербурга: IT, ИИ, бизнес, английские клубы.\n\n"
           "Выберите тему:")


@app.route("/")
def index():
    return "ok"


@app.route(f"/{HOOK_PATH}", methods=["POST"])
def hook():
    try:
        upd = request.get_json(force=True, silent=True) or {}
        if "message" in upd:
            msg = upd["message"]
            chat = msg["chat"]["id"]
            text = msg.get("text", "")
            if text.startswith("/start") or text.startswith("/menu"):
                tg("sendMessage", {"chat_id": chat, "text": WELCOME, "reply_markup": kb_topics()})
            elif ADMIN_ID and str(chat) != ADMIN_ID:
                # любое обычное сообщение в личку — передаём основателям
                user = msg.get("from", {})
                who = "@" + user["username"] if user.get("username") else \
                    (user.get("first_name", "") + " " + user.get("last_name", "")).strip()
                tg("sendMessage", {"chat_id": ADMIN_ID, "parse_mode": "HTML",
                                   "text": f"✉️ <b>Сообщение боту</b> от {html_escape(who or 'без имени')} "
                                           f"(id {chat}):\n\n{html_escape(text or '[не текст]')}"})
                if msg.get("photo") or msg.get("document") or msg.get("voice"):
                    tg("forwardMessage", {"chat_id": ADMIN_ID, "from_chat_id": chat,
                                          "message_id": msg["message_id"]})
                tg("sendMessage", {"chat_id": chat,
                                   "text": "Спасибо! Передала ваше сообщение основателям 🙌\n"
                                           "Вернуться к событиям — /menu",
                                   "reply_markup": {"inline_keyboard": [[{"text": "🔄 К событиям", "callback_data": "menu"}]]}})
            else:
                tg("sendMessage", {"chat_id": chat, "text": WELCOME, "reply_markup": kb_topics()})
        elif "callback_query" in upd:
            cq = upd["callback_query"]
            chat = cq["message"]["chat"]["id"]
            mid = cq["message"]["message_id"]
            data = cq.get("data", "")
            tg("answerCallbackQuery", {"callback_query_id": cq["id"]})
            if data == "menu":
                tg("sendMessage", {"chat_id": chat, "text": "Выберите тему:", "reply_markup": kb_topics()})
            elif data == "write":
                tg("sendMessage", {"chat_id": chat,
                                   "text": "✍️ Напишите сообщение прямо сюда, в этот чат — "
                                           "я передам его основателям."})
            elif data.startswith("t|"):
                topic = data.split("|", 1)[1]
                tg("editMessageText", {"chat_id": chat, "message_id": mid,
                                       "text": f"Тема: {topic}. За какой период показать события?",
                                       "reply_markup": kb_periods(topic)})
            elif data.startswith("p|"):
                _, topic, period, offset = data.split("|", 3)
                try:
                    send_page(chat, topic, period, int(offset))
                except Exception:
                    log_error(traceback.format_exc())
                    tg("sendMessage", {"chat_id": chat,
                                       "text": "Не получилось достать список событий, попробуйте чуть позже."})
    except Exception:
        log_error(traceback.format_exc())
    return "ok"
