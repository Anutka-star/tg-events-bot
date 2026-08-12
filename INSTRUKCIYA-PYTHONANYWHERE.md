# Инструкция: кнопочный бот на PythonAnywhere (~15 минут, бесплатно)

Делается ПОСЛЕ настройки GitHub (см. INSTRUKCIYA-GITHUB.md).

## Шаг 1. Аккаунт
1. Откройте **www.pythonanywhere.com** → Pricing & signup → **Create a Beginner account** (Free).
2. Логин выбирайте латиницей — он станет адресом бота: `логин.pythonanywhere.com`.

## Шаг 2. Файлы
1. Вкладка **Files** → откройте папку **mysite** (если её нет — создайте: поле
   «Directories» → введите `mysite` → New directory).
2. В папке mysite → **Upload a file** → загрузите `webhook_bot.py` из папки
   tg-events-bot на рабочем столе.
3. Там же → в поле «Files» введите `keys.txt` → **New file** → вставьте три строки:
   - токен бота (тот же, что в Keys.txt на рабочем столе);
   - ссылку `https://raw.githubusercontent.com/ВАШ_ЛОГИН_GITHUB/tg-events-bot/main/events.json`;
   - ваш личный Telegram ID (число из бота @getidsbot — нужно для кнопки
     «Написать основателям»: сообщения пользователей будут приходить вам).
   Нажмите **Save**.

## Шаг 3. Веб-приложение
1. Вкладка **Web** → **Add a new web app** → Next → **Flask** → **Python 3.10** → Next
   (путь оставьте предложенный).
2. На странице Web найдите раздел **Code** → ссылка **WSGI configuration file** → откройте.
3. В самом низу файла замените строку
   `from flask_app import app as application`
   на
   `from webhook_bot import app as application`
   и нажмите **Save**.
4. Вернитесь на вкладку **Web** → зелёная кнопка **Reload**.
5. Проверка: откройте `https://ВАШ_ЛОГИН.pythonanywhere.com` — должно быть написано `ok`.

## Шаг 4. Подключить бота к этому адресу (вебхук)
Откройте в браузере одной строкой (подставьте СВОЙ токен и СВОЙ логин):

```
https://api.telegram.org/botВАШ_ТОКЕН/setWebhook?url=https://ВАШ_ЛОГИН.pythonanywhere.com/hook-ni-7k2f9x
```

Должен появиться ответ `{"ok":true,"result":true,...}`.

## Шаг 5. Проверка
Откройте в Telegram личку своего бота → напишите `/start` → появятся кнопки тем
(IT / ИИ / Бизнес / Английский / Всё), затем период (Сегодня / Неделя / Месяц / Все даты)
→ бот пришлёт список со ссылками.

## Важно помнить
- Раз в МЕСЯЦ PythonAnywhere просит нажать жёлтую кнопку **Run until 1 month
  from today** на вкладке Web (письмо-напоминание придёт за неделю) — иначе
  кнопочный бот заснёт. Ежедневной ленты канала это не касается.
- Кнопка «Английский» может быть пустой: события англоклубов идут с Timepad,
  который пускает не все автоматические запросы.
- Если кнопки молчат: вкладка Web → **Error log** и файл `bot_errors.log` в Files —
  пришлите текст ошибки.
