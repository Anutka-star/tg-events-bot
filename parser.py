# -*- coding: utf-8 -*-
"""
Ежедневный мониторинг мероприятий СПб (IT, ИИ, бизнес) и отправка новых в Telegram-канал.

Режимы:
  python parser.py            — обычный прогон: сбор, карточки новых событий в канал,
                                обновление seen.json и events.json
  python parser.py --dry-run  — сбор без отправки и без записи seen.json
  python parser.py --digest   — понедельничный дайджест недели в канал
                                (seen.json не трогает, карточки не шлёт)

Настройки из переменных окружения: TG_TOKEN, TG_CHAT.
"""
import json
import os
import re
import sys
import time as time_mod
from datetime import date, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(HERE, "seen.json")
EVENTS_FILE = os.path.join(HERE, "events.json")
TODAY = date.today()
BOT_LINK = "https://t.me/NorthernIntelligence_bot"
ENRICH_MAX = 60          # сколько страниц событий обогащаем за один прогон
ENR_VERSION = 2          # версия обогащения: поднимаем — старые события обогатятся заново
CARDS_MAX = 25           # максимум карточек в канал за прогон

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
          "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

AI_RE = re.compile(r"\bИИ\b|нейросет|искусственн\w+ интеллект|\bAI\b|\bML\b|машинн\w+ обучени|GPT|LLM|data science", re.I)
IT_RE = re.compile(r"\bИТ\b|\bIT\b|разработ|программир|кибер|дата-центр|облач|DevOps|тестиров|цифров", re.I)
NET_RE = re.compile(r"нетворкинг|networking|бизнес-завтрак|знакомств|деловые связи", re.I)
TOPIC_EMOJI = {"IT": "💻", "ИИ": "🤖", "Бизнес": "💼", "Английский": "🇬🇧"}


def classify(default_topic, text):
    if AI_RE.search(text):
        return "ИИ"
    if default_topic == "Бизнес" and IT_RE.search(text) and not re.search(r"семинар|кадр|труд|налог", text, re.I):
        return "IT"
    if default_topic == "IT" or IT_RE.search(text):
        return default_topic if default_topic != "Бизнес" else "IT"
    return default_topic


def fetch(url):
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    return r.text


def clean(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def find_date(text):
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
                if not m.group(3) and d < TODAY:
                    d = date(year + 1, month, int(m.group(1)))
                return d, m.group(0)
            except ValueError:
                pass
    return None, ""


def tidy_title(t):
    t = clean(t)
    for pat in (r"^Точка Кипения СПб\s*", r"^.{0,45}?\d{2}\.\d{2}\.\d{4}\s*\|\s*(Онлайн|Санкт-Петербург|Москва)?\s*",
                r"^\d{1,2}\s+[А-Яа-я]+\s+\d{4}\s*",
                r"^\d{1,2}\.\d{1,2}\.\d{4}\s*\|?\s*", r"^\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}\s*",
                r"^(Мастер-класс|Лекция|Встреча|Вебинар|Конференция|Семинар|Митап)\s+(?=[А-ЯA-Z«])"):
        t = re.sub(pat, "", t)
    t = re.sub(r"\s*(Подробнее|УЧАСТВОВАТЬ|Регистрация)\s*$", "", t, flags=re.I)
    # хвост вида « онлайн Маркетинг Продажи» (категории после формата) — отрезаем
    t = re.sub(r"\s+(онлайн|очно)(\s+[А-ЯЁ][а-яё]+){1,6}\s*$", "", t)
    return clean(t)


def card_of(a, href_re, limit=450, hops=5):
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
    href_re = re.compile(href_pattern)
    soup = BeautifulSoup(fetch(url), "html.parser")
    best = {}
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
        if d and d < TODAY:
            continue
        tm = ""
        m = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", ctext)
        if m:
            tm = m.group(0)
        out.append({
            "title": title[:130], "url": full, "date": dstr,
            "iso": d.isoformat() if d else "",
            "time": tm,
            "topic": classify(topic, title + " " + ctext),
            "online": bool(re.search(r"онлайн|online|вебинар|трансляци", ctext, re.I)),
            "net": bool(NET_RE.search(title + " " + ctext)),
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

# организатор по источнику, если со страницы не достали
SOURCE_ORG = {
    "🏛 ЦРПП «Мой бизнес» СПб": "Центр «Мой бизнес» СПб",
    "🏛 СПб Торгово-промышленная палата": "СПб ТПП",
    "🔥 Точка кипения СПб": "Точка кипения СПб",
    "☁️ Cloud.ru (вебинары)": "Cloud.ru",
    "🏟 Экспофорум": "Экспофорум",
    "🇬🇧 Центр британской книги (Timepad)": "Центр британской книги",
    "🇬🇧 Открытая гостиная (Timepad)": "Открытая гостиная (библиотека Лермонтова)",
}


def enrich_page(e):
    """Дотягиваем со страницы события: афишу, описание, цену, время, организатора."""
    try:
        html = fetch(e["url"])
    except Exception:
        e["enr"] = ENR_VERSION  # чтобы не долбить недоступную страницу каждый день
        return e

    def meta(*names):
        for n in names:
            m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']+)["\']' % re.escape(n), html, re.I) \
                or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(n), html, re.I)
            if m:
                return m.group(1).strip()
        return ""

    img = meta("og:image", "twitter:image")
    if img.startswith("//"):
        img = "https:" + img
    elif img.startswith("/"):
        img = urljoin(e["url"], img)
    if not img.lower().startswith("http") or re.search(r"logo_header|favicon|/ict/images/", img):
        img = ""  # логотип сайта — не афиша
    desc = meta("og:description", "description", "twitter:description")
    desc = clean(re.sub(r"&[a-z]+;", " ", desc))[:400]

    soup = BeautifulSoup(html, "html.parser")
    for bad in soup(["script", "style"]):
        bad.decompose()
    text = clean(soup.get_text(" ", strip=True))[:8000]

    # --- организатор: сначала точные места конкретных сайтов, потом общий шаблон
    BAD_ORG = re.compile(r"^(спикер|каталог|организатор|площадк|о нас|новост|реклам|программа|участник)", re.I)
    org = ""
    node = soup.select_one("div.organizers a[href*='/companies/']")  # ict2go
    if node:
        org = clean(node.get_text())
    if not org:
        node = soup.select_one("div.organizer-events a[href*='/organizers/']")  # all-events
        if node:
            im = node.find("img")
            cand = clean((im.get("alt") or im.get("title")) if im else "") or clean(node.get_text())
            t_low, c_low = clean(e.get("title", "")).lower()[:40], cand.lower()
            # подпись к логотипу часто дублирует название события — такое не берём
            if cand and t_low and t_low[:25] not in c_low and c_low[:25] not in t_low:
                org = cand
    if not org:
        m = re.search(r"Организатор[ы]?\s*:\s*(.{2,60}?)(?:\s{2,}|Будь в курсе|Сайт|Контакты|$)", text)
        if m:
            org = clean(m.group(1))
    if not org:
        org = meta("og:site_name")
    if not org or BAD_ORG.search(org) or org.lower() in (
            "ict2go", "ict2go.ru", "all-events", "all-events.ru", "timepad", "все события", "all events"):
        org = ""

    # --- время: «Начало [12.08.2026] в 15:00» точнее, чем первое попавшееся время
    m = re.search(r"Начало(?:\s+\d{1,2}\.\d{1,2}\.\d{4})?\s+в\s+([01]?\d|2[0-3]):([0-5]\d)", text, re.I)
    if m:
        e["time"] = f"{m.group(1)}:{m.group(2)}"
    elif not e.get("time"):
        m = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", text)
        if m:
            e["time"] = m.group(0)

    # --- цена: подписанное поле, затем общие шаблоны
    price = ""
    m = re.search(r"(?:Стоимость|Цена|Участие|Билеты)\s*:?\s{0,3}(бесплатн\w+|от\s?\d[\d\s]{0,8}\s?(?:₽|руб)\S{0,4}|\d[\d\s]{0,8}\s?(?:₽|руб)\S{0,4})", text, re.I)
    if m:
        price = clean(m.group(1))
    elif re.search(r"\b(бесплатно|бесплатное|участие свободное|free)\b", text, re.I):
        price = "Бесплатно"
    else:
        m = re.search(r"(от\s?\d[\d\s]{0,8}\s?(?:₽|руб))|(\d[\d\s]{0,8}\s?(?:₽|руб))", text, re.I)
        if m:
            price = clean(m.group(0))
    if price:
        price = re.sub(r"бесплатн\w+", "Бесплатно", price, flags=re.I)
        price = price.replace("руб.", "₽").replace("руб", "₽")

    if NET_RE.search((e.get("title", "") + " " + desc)):
        e["net"] = True
    e.update({"img": img, "desc": desc, "price": price, "org_page": clean(org)[:60], "enr": ENR_VERSION})
    return e


def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_card(e):
    """Текст карточки события (HTML, до 1000 символов — лимит подписи к фото)."""
    topic = e.get("topic", "IT")
    tags = f"#{topic.replace(' ', '_')}"
    if e.get("net"):
        tags += " #Нетворкинг"
    fmt = "🖥 Онлайн" if e.get("online") else "📍 Офлайн, СПб"
    org = e.get("org") or e.get("org_page") or SOURCE_ORG.get(e.get("source", ""), "") or "см. по ссылке"
    if len(org) > 50:
        org = SOURCE_ORG.get(e.get("source", ""), "см. по ссылке")
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
    desc = clean(e.get("desc", ""))
    if desc:
        room = 1000 - sum(len(x) for x in lines) - len(e["url"]) - 80
        if room > 60:
            lines += ["", f"📝 {html_escape(desc[:room])}"]
    lines += ["", f"🎟 <a href=\"{e['url']}\">Регистрация / билеты</a>"]
    return "\n".join(lines)


def tg_api(token, method, payload):
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=30)
    return r


def send_card(token, chat, e):
    caption = build_card(e)
    if e.get("img"):
        r = tg_api(token, "sendPhoto", {"chat_id": chat, "photo": e["img"], "caption": caption,
                                        "parse_mode": "HTML"})
        if r.ok:
            return True
    r = tg_api(token, "sendMessage", {"chat_id": chat, "text": caption, "parse_mode": "HTML",
                                      "disable_web_page_preview": False})
    if not r.ok:
        print(f"  не отправилась карточка: {r.status_code} {r.text[:150]}")
    return r.ok


def collect_all():
    """Собирает события со всех источников. Возвращает (события, ошибки)."""
    all_events, errors = [], []
    for name, fn in SOURCES:
        try:
            events = fn()
            for e in events:
                all_events.append(dict(e, source=name))
            print(f"OK  {name}: всего {len(events)}")
        except Exception as ex:
            errors.append(name)
            print(f"СБОЙ {name}: {type(ex).__name__}: {str(ex)[:120]}")
    return all_events, errors


def load_prev_events():
    try:
        with open(EVENTS_FILE, encoding="utf-8") as f:
            return {e["url"]: e for e in json.load(f).get("events", [])}
    except Exception:
        return {}


def send_digest(token, chat, events):
    """Понедельничный дайджест: события ближайших 7 дней по дням недели."""
    end = TODAY + timedelta(days=7)
    week = [e for e in events
            if e.get("iso") and TODAY <= date.fromisoformat(e["iso"]) <= end]
    week.sort(key=lambda e: (e["iso"], e.get("time") or "99"))
    head = (f"☀️ <b>Доброе утро, Петербург!</b>\n"
            f"Подборка мероприятий на неделю {TODAY.strftime('%d.%m')}–{end.strftime('%d.%m')}:\n")
    by_day, seen_titles = {}, set()
    for e in week:
        nt = re.sub(r"\W+", "", e["title"].lower())[:40]
        if nt in seen_titles:
            continue
        seen_titles.add(nt)
        by_day.setdefault(e["iso"], []).append(e)
    chunks, cur = [], head
    for iso in sorted(by_day):
        d = date.fromisoformat(iso)
        block = f"\n<b>{WEEKDAYS[d.weekday()]} {d.strftime('%d.%m')}</b>\n"
        for e in by_day[iso][:8]:
            emoji = TOPIC_EMOJI.get(e.get("topic", ""), "•")
            mark = " 🖥" if e.get("online") else ""
            block += f"{emoji}{mark} <a href=\"{e['url']}\">{html_escape(e['title'][:80])}</a>\n"
        if len(cur) + len(block) > 3600:
            chunks.append(cur); cur = ""
        cur += block
    cur += (f"\n➕ Больше мероприятий, фильтры по темам и датам — "
            f"в нашем боте: {BOT_LINK}")
    chunks.append(cur)
    for ch in chunks:
        r = tg_api(token, "sendMessage", {"chat_id": chat, "text": ch, "parse_mode": "HTML",
                                          "disable_web_page_preview": True})
        if not r.ok:
            print("дайджест не отправился:", r.status_code, r.text[:150])
    print(f"Дайджест: {len(week)} событий недели, сообщений {len(chunks)}")


def main():
    dry = "--dry-run" in sys.argv
    digest = "--digest" in sys.argv
    token, chat = os.environ.get("TG_TOKEN", ""), os.environ.get("TG_CHAT", "")
    if not dry and (not token or not chat):
        print("ОШИБКА: не заданы TG_TOKEN / TG_CHAT"); sys.exit(1)

    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:
        seen = {}
    first_run = not seen

    all_events, errors = collect_all()
    prev = load_prev_events()

    # переносим обогащение с прошлых прогонов
    for e in all_events:
        p = prev.get(e["url"])
        if p and p.get("enr", 0) >= ENR_VERSION:
            for k in ("img", "desc", "price", "org_page", "enr"):
                e[k] = p.get(k, e.get(k, ""))
            if not e.get("time") and p.get("time"):
                e["time"] = p["time"]

    def finalize_org(e):
        op = e.get("org_page", "")
        e["org"] = op if 0 < len(op) <= 50 else SOURCE_ORG.get(e.get("source", ""), "")

    fresh = [e for e in all_events if e["url"] not in seen]

    # обогащаем: сначала новые, затем старые без обогащения
    budget = ENRICH_MAX
    for e in fresh:
        if budget <= 0:
            break
        if e.get("enr", 0) < ENR_VERSION:
            enrich_page(e); budget -= 1
    if not digest:
        for e in all_events:
            if budget <= 0:
                break
            if e.get("enr", 0) < ENR_VERSION:
                enrich_page(e); budget -= 1
    print(f"Обогащено страниц за прогон: {ENRICH_MAX - budget}")
    for e in all_events:
        finalize_org(e)

    if not digest:
        for e in all_events:
            seen.setdefault(e["url"], TODAY.isoformat())
        if not dry:
            with open(SEEN_FILE, "w", encoding="utf-8") as f:
                json.dump(seen, f, ensure_ascii=False, indent=0)
        with open(EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"updated": TODAY.isoformat(), "events": all_events}, f,
                      ensure_ascii=False, indent=0)

    if dry:
        print(f"\n— Пробный прогон. Новых: {len(fresh)}")
        for e in fresh[:3]:
            print("\n" + "=" * 40 + "\n" + build_card(e))
        return

    if digest:
        send_digest(token, chat, all_events)
        return

    if first_run:
        tg_api(token, "sendMessage",
               {"chat_id": chat,
                "text": f"🤖 Бот запущен. Отслеживаю {len(SOURCES) - len(errors)} источников "
                        f"мероприятий СПб — новые буду присылать сюда каждый день."})
        print("Посев выполнен.")
        return

    if not fresh:
        print("Новых событий нет.")
        return

    sent = 0
    for e in fresh[:CARDS_MAX]:
        if send_card(token, chat, e):
            sent += 1
        time_mod.sleep(2.2)
    if len(fresh) > CARDS_MAX:
        tg_api(token, "sendMessage",
               {"chat_id": chat, "parse_mode": "HTML",
                "text": f"…и ещё {len(fresh) - CARDS_MAX} новых событий — "
                        f"смотрите в боте: {BOT_LINK}"})
    print(f"Карточек отправлено: {sent} из {len(fresh)} новых")


if __name__ == "__main__":
    main()
