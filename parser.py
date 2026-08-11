# -*- coding: utf-8 -*-
"""
Ежедневный мониторинг мероприятий СПб (IT, ИИ, бизнес) и отправка новых в Telegram-канал.

Как работает:
1. Обходит источники (список SOURCES ниже), собирает события: название + ссылка + дата.
2. Сравнивает со списком уже виденных (seen.json).
3. Новые отправляет сообщением в Telegram-канал и дописывает в seen.json.
Первый запуск (seen.json пуст) — «посев»: запоминает всё текущее и шлёт приветствие,
чтобы не завалить канал сотней старых событий.

Настройки берутся из переменных окружения:
  TG_TOKEN — токен бота из @BotFather
  TG_CHAT  — @имя_канала или числовой id (-100...)
Запуск без отправки (проверка парсинга): python parser.py --dry-run
"""
import json
import os
import re
import sys
from datetime import date
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(HERE, "seen.json")
EVENTS_FILE = os.path.join(HERE, "events.json")  # база для кнопочного бота
TODAY = date.today()

AI_RE = re.compile(r"\bИИ\b|нейросет|искусственн\w+ интеллект|\bAI\b|\bML\b|машинн\w+ обучени|GPT|LLM|data science", re.I)
IT_RE = re.compile(r"\bИТ\b|\bIT\b|разработ|программир|кибер|дата-центр|облач|DevOps|тестиров|цифров", re.I)


def classify(default_topic, text):
    """Тема события: ИИ важнее ИТ, иначе тема источника."""
    if AI_RE.search(text):
        return "ИИ"
    if default_topic == "Бизнес" and IT_RE.search(text) and not re.search(r"семинар|кадр|труд|налог", text, re.I):
        return "IT"
    if default_topic == "IT" or IT_RE.search(text):
        return default_topic if default_topic != "Бизнес" else "IT"
    return default_topic

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
          "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}


def fetch(url):
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    return r.text


def clean(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def find_date(text):
    """Ищет дату дд.мм.гггг или «12 августа [2026]» в тексте. Возвращает (date|None, строка)."""
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if m:
        try:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return d, m.group(0)
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})\s+(январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*(?:\s+(\d{4}))?", text, re.I)
    if m:
        stem = m.group(2).lower()[:6]
        month = next((v for k, v in MONTHS.items() if stem.startswith(k)), None)
        if month:
            year = int(m.group(3)) if m.group(3) else TODAY.year
            try:
                d = date(year, month, int(m.group(1)))
                if not m.group(3) and d < TODAY:  # «12 августа» без года, уже прошло → скорее всего следующий год
                    d = date(year + 1, month, int(m.group(1)))
                return d, m.group(0)
            except ValueError:
                pass
    return None, ""


def tidy_title(t):
    """Срезаем из заголовка даты, время и служебные слова по краям."""
    t = clean(t)
    for pat in (r"^Точка Кипения СПб\s*", r"^.{0,45}?\d{2}\.\d{2}\.\d{4}\s*\|\s*(Онлайн|Санкт-Петербург|Москва)?\s*",
                r"^\d{1,2}\s+[А-Яа-я]+\s+\d{4}\s*",
                r"^\d{1,2}\.\d{1,2}\.\d{4}\s*\|?\s*", r"^\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}\s*",
                r"^(Мастер-класс|Лекция|Встреча|Вебинар|Конференция|Семинар|Митап)\s+(?=[А-ЯA-Z«])"):
        t = re.sub(pat, "", t)
    t = re.sub(r"\s*(Подробнее|УЧАСТВОВАТЬ|Регистрация)\s*$", "", t, flags=re.I)
    return clean(t)


def card_of(a, href_re, limit=450, hops=5):
    """Карточка события: поднимаемся к родителям, пока внутри одна ссылка на событие
    и текст остаётся компактным (иначе прихватим чужие события)."""
    node = a
    for _ in range(hops):
        parent = getattr(node, "parent", None)
        if parent is None:
            break
        if len(parent.get_text(strip=True)) >= limit:
            break
        if len(set(x.get("href", "") for x in parent.find_all("a", href=href_re))) > 1:
            break
        node = parent
    return node


def links_source(url, href_pattern, base, city_filter=False, min_title=12, topic="Бизнес"):
    """Универсальный сборщик: все ссылки по шаблону + текст карточки."""
    href_re = re.compile(href_pattern)
    soup = BeautifulSoup(fetch(url), "html.parser")
    best = {}  # url -> (title, ctext)
    for a in soup.find_all("a", href=href_re):
        full = urljoin(base, a.get("href", "")).split("?")[0]
        card = card_of(a, href_re)
        ctext = clean(card.get_text(" ", strip=True))
        title = tidy_title(a.get_text(" ", strip=True))
        if len(title) < min_title:
            title = tidy_title(ctext)[:110]
        prev = best.get(full)
        if prev is None or len(title) > len(prev[0]):
            best[full] = (title, ctext)
    out = []
    for full, (title, ctext) in best.items():
        if len(title) < min_title:
            continue
        if city_filter and not re.search(r"Санкт-Петербург|Петербург|СПб|Онлайн|online", ctext, re.I):
            continue
        d, dstr = find_date(ctext)
        if d and d < TODAY:  # прошедшие не интересны
            continue
        out.append({
            "title": title[:130], "url": full, "date": dstr,
            "iso": d.isoformat() if d else "",
            "topic": classify(topic, title + " " + ctext),
            "online": bool(re.search(r"онлайн|online|вебинар|трансляци", ctext, re.I)),
        })
    return out


SOURCES = [
    ("🏛 ЦРПП «Мой бизнес» СПб", lambda: links_source(
        "https://www.crpp.ru/meropriyatiya_all/meropriyatiya_vse/",
        r"events\d+\.html$", "https://www.crpp.ru/meropriyatiya_all/meropriyatiya_vse/", topic="Бизнес")),
    ("🏛 СПб Торгово-промышленная палата", lambda: links_source(
        "https://spbtpp.ru/events/", r"events_\d+\.html$", "https://spbtpp.ru/events/", topic="Бизнес")),
    ("🔥 Точка кипения СПб", lambda: links_source(
        "https://tboil.spb.ru/events/actual/", r"/events/actual/\d+", "https://tboil.spb.ru", topic="Бизнес")),
    ("💻 ict2go (ИТ-события)", lambda: links_source(
        "https://ict2go.ru/events/", r"^/events/\d+/$", "https://ict2go.ru", city_filter=True, topic="IT")),
    ("📅 all-events (деловые события)", lambda: links_source(
        "https://all-events.ru/events/", r"/events/[a-z0-9_-]{10,}/$", "https://all-events.ru", city_filter=True, topic="Бизнес")),
    ("☁️ Cloud.ru (вебинары)", lambda: links_source(
        "https://cloud.ru/events", r"/events/[a-z0-9-]{10,}$", "https://cloud.ru", topic="IT")),
    ("🏟 Экспофорум", lambda: links_source(
        "https://www.expoforum.ru/calendar/", r"/calendar/[a-z0-9-]{6,}/$", "https://www.expoforum.ru", topic="Бизнес")),
    ("🇬🇧 Центр британской книги (Timepad)", lambda: links_source(
        "https://british-book-centre.timepad.ru/events/", r"/event/\d+", "https://british-book-centre.timepad.ru", topic="Английский")),
    ("🇬🇧 Открытая гостиная (Timepad)", lambda: links_source(
        "https://otkrytaya-gostinaya.timepad.ru/events/", r"/event/\d+", "https://otkrytaya-gostinaya.timepad.ru", topic="Английский")),
]


def tg_send(token, chat, text):
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=25,
    )
    if not r.ok:
        raise RuntimeError(f"Telegram ответил {r.status_code}: {r.text[:200]}")


def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    dry = "--dry-run" in sys.argv
    token, chat = os.environ.get("TG_TOKEN", ""), os.environ.get("TG_CHAT", "")
    if not dry and (not token or not chat):
        print("ОШИБКА: не заданы TG_TOKEN / TG_CHAT"); sys.exit(1)

    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:
        seen = {}
    first_run = not seen

    new_by_source, errors, total_found, all_events = {}, [], 0, []
    for name, fn in SOURCES:
        try:
            events = fn()
            total_found += len(events)
            for e in events:
                all_events.append(dict(e, source=name))
            fresh = [e for e in events if e["url"] not in seen]
            for e in fresh:
                seen[e["url"]] = TODAY.isoformat()
            if fresh:
                new_by_source[name] = fresh
            print(f"OK  {name}: всего {len(events)}, новых {len(fresh)}")
        except Exception as ex:
            errors.append(name)
            print(f"СБОЙ {name}: {type(ex).__name__}: {ex}")

    if not dry:  # пробный прогон ничего не запоминает
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=0)
    # база всех актуальных событий — для кнопочного бота (пишем всегда)
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated": TODAY.isoformat(), "events": all_events}, f, ensure_ascii=False, indent=0)

    if dry:
        print(f"\n— Пробный прогон: сообщений не отправляю. Новых: {sum(len(v) for v in new_by_source.values())}")
        for name, evs in new_by_source.items():
            print(f"\n{name}")
            for e in evs[:5]:
                print("  •", e["title"], "|", e["date"], "|", e["url"])
        return

    if first_run:
        ok_sources = len(SOURCES) - len(errors)
        tg_send(token, chat,
                f"🤖 Бот запущен. Отслеживаю {ok_sources} источников мероприятий СПб "
                f"(IT, ИИ, бизнес). Сейчас в базе {len(seen)} актуальных событий — "
                f"новые буду присылать сюда раз в день.")
        print("Посев выполнен, приветствие отправлено.")
        return

    n_new = sum(len(v) for v in new_by_source.values())
    if not n_new:
        print("Новых событий нет — сообщение не отправляю.")
        return

    d = TODAY.strftime("%d.%m.%Y")
    chunks, cur = [], f"🗓 <b>Новые события — {d}</b> (всего {n_new})\n"
    for name, evs in new_by_source.items():
        block = f"\n<b>{html_escape(name)}</b>\n"
        for e in evs:
            when = f" — {e['date']}" if e["date"] else ""
            block += f"• <a href=\"{e['url']}\">{html_escape(e['title'])}</a>{when}\n"
        if len(cur) + len(block) > 3800:
            chunks.append(cur); cur = ""
        cur += block
    chunks.append(cur)
    for ch in chunks:
        tg_send(token, chat, ch)
    print(f"Отправлено сообщений: {len(chunks)}, новых событий: {n_new}")


if __name__ == "__main__":
    main()
