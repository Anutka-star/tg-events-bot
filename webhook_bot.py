# -*- coding: utf-8 -*-
"""
Кнопочный бот (личка): фильтр событий по темам и датам.
Работает на PythonAnywhere как Flask-приложение (вебхук Telegram).

Настройка — файл /home/<логин>/keys.txt, две строки в любом порядке:
  токен бота (строка вида 123456:AA...)
  полная ссылка на events.json (https://raw.githubusercontent.com/<логин GitHub>/tg-events-bot/main/events.json)
"""
import json
import os
import re
import time
import traceback
from datetime import datetime, timedelta, date

import requests
from flask import Flask, request

HOOK_PATH = "hook-ni-7k2f9x"  # секретная часть адреса вебхука
HOME = os.path.expanduser("~")

app = Flask(__name__)

# --- ключи ---
_raw = ""
for candidate in (os.path.join(HOME, "keys.txt"), os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.txt")):
    if os.path.exists(candidate):
        _raw = open(candidate, encoding="utf-8-sig", errors="ignore").read()
        break
TOKEN = (re.search(r"\b(\d{6,12}:[A-Za-z0-9_-]{30,})\b", _raw) or [None, ""])[1]
EVENTS_URL = (re.search(r"(https://\S+events\.json)", _raw) or [None, ""])[1]
API = f"https://api.telegram.org/bot{TOKEN}"

TOPICS = ["IT", "ИИ", "Бизнес", "Английский", "Всё"]
PERIODS = [("today", "Сегодня"), ("week", "Неделя"), ("month", "Месяц"), ("all", "Все даты")]
PERIOD_NAME = dict(PERIODS)

_cache = {"t": 0.0, "events": []}


def log_error(text):
    with open(os.path.join(HOME, "bot_errors.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.utcnow().isoformat()} {text}\n")


def msk_today():
    return (datetime.utcnow() + timedelta(hours=3)).date()


def get_events():
    """Список событий из GitHub, кэш 10 минут."""
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
            continue  # события без даты — только в «Все даты»
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


def render_list(events, topic, period):
    head = f"🔎 <b>{topic}</b> · {PERIOD_NAME[period]} — найдено {len(events)}\n"
    if not events:
        return head + "\nПока пусто. Попробуйте другой период или тему."
    lines = []
    for e in events[:20]:
        iso = e.get("iso", "")
        when = f"{iso[8:10]}.{iso[5:7]}" if iso else "дата на сайте"
        mark = " 🖥" if e.get("online") else ""
        lines.append(f"📅 {when}{mark} — <a href=\"{e['url']}\">{html_escape(e['title'][:90])}</a>")
    tail = f"\n…и ещё {len(events) - 20}" if len(events) > 20 else ""
    return head + "\n" + "\n".join(lines) + tail


def kb_topics():
    rows = [[{"text": "💻 IT", "callback_data": "t|IT"}, {"text": "🤖 ИИ", "callback_data": "t|ИИ"}],
            [{"text": "💼 Бизнес", "callback_data": "t|Бизнес"}, {"text": "🇬🇧 Английский", "callback_data": "t|Английский"}],
            [{"text": "📋 Всё сразу", "callback_data": "t|Всё"}]]
    return {"inline_keyboard": rows}


def kb_periods(topic):
    rows = [[{"text": "Сегодня", "callback_data": f"p|{topic}|today"},
             {"text": "Неделя", "callback_data": f"p|{topic}|week"}],
            [{"text": "Месяц", "callback_data": f"p|{topic}|month"},
             {"text": "Все даты", "callback_data": f"p|{topic}|all"}],
            [{"text": "← Темы", "callback_data": "menu"}]]
    return {"inline_keyboard": rows}


def kb_again():
    return {"inline_keyboard": [[{"text": "🔄 Новый фильтр", "callback_data": "menu"}]]}


def tg(method, payload):
    r = requests.post(f"{API}/{method}", json=payload, timeout=15)
    if not r.ok:
        log_error(f"{method}: {r.status_code} {r.text[:300]}")
    return r


@app.route("/")
def index():
    return "ok"


@app.route(f"/{HOOK_PATH}", methods=["POST"])
def hook():
    try:
        upd = request.get_json(force=True, silent=True) or {}
        if "message" in upd:
            chat = upd["message"]["chat"]["id"]
            tg("sendMessage", {"chat_id": chat, "parse_mode": "HTML",
                               "text": "Привет! Я подберу мероприятия Санкт-Петербурга.\nВыберите тему:",
                               "reply_markup": kb_topics()})
        elif "callback_query" in upd:
            cq = upd["callback_query"]
            chat = cq["message"]["chat"]["id"]
            mid = cq["message"]["message_id"]
            data = cq.get("data", "")
            tg("answerCallbackQuery", {"callback_query_id": cq["id"]})
            if data == "menu":
                tg("editMessageText", {"chat_id": chat, "message_id": mid,
                                       "text": "Выберите тему:", "reply_markup": kb_topics()})
            elif data.startswith("t|"):
                topic = data.split("|", 1)[1]
                tg("editMessageText", {"chat_id": chat, "message_id": mid,
                                       "text": f"Тема: {topic}. За какой период?",
                                       "reply_markup": kb_periods(topic)})
            elif data.startswith("p|"):
                _, topic, period = data.split("|", 2)
                try:
                    events = filter_events(topic, period)
                    text = render_list(events, topic, period)
                except Exception:
                    log_error(traceback.format_exc())
                    text = "Не смог получить список событий, попробуйте чуть позже."
                tg("editMessageText", {"chat_id": chat, "message_id": mid, "parse_mode": "HTML",
                                       "text": text, "disable_web_page_preview": True,
                                       "reply_markup": kb_again()})
    except Exception:
        log_error(traceback.format_exc())
    return "ok"
