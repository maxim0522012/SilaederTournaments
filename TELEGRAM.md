# Уведомления в Telegram без webhook

Игрок указывает ник в профиле сайта, открывает персональную ссылку на бота и один раз нажимает «Запустить». Отдельный процесс получает команды через long polling и сохраняет `chat_id` игрока.

## Настройка

Создайте бота через [@BotFather](https://t.me/BotFather), затем добавьте настройки только в серверный `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=новый_токен_из_BotFather
TELEGRAM_BOT_USERNAME=имя_бота_без_символа_собачки
```

Токен нельзя добавлять в исходный код или документацию.

## SOCKS5-прокси

Если прямой доступ к Telegram недоступен, добавьте в `.env`:

```dotenv
TELEGRAM_PROXY_URL=socks5h://127.0.0.1:1080
```

Прокси с авторизацией:

```dotenv
TELEGRAM_PROXY_URL=socks5h://username:password@proxy.example.org:1080
```

Рекомендуется `socks5h://`: в этом режиме имя `api.telegram.org` также разрешается через прокси. Поддерживаются только схемы `socks5://` и `socks5h://`. Специальные символы в логине и пароле нужно записывать в URL-кодировке.

После изменения настроек установите зависимости и перезапустите процессы:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Без Docker запустите сайт и polling в отдельных терминалах:

```powershell
.\.venv\Scripts\python.exe app.py
.\.venv\Scripts\python.exe -m flask --app app telegram-polling
```

С Docker достаточно пересобрать контейнеры:

```powershell
docker compose up -d --build
docker compose logs -f telegram-poller
```

Публичный webhook и `TELEGRAM_WEBHOOK_SECRET` не нужны.

## Подключение игрока

1. Игрок открывает свой профиль на сайте.
2. Вводит ник Telegram и нажимает «Сохранить и подключить».
3. В открывшемся боте нажимает «Запустить».

После привязки уведомления отправляются на сайте, по email и в Telegram. Если Telegram недоступен, заявка и остальные способы уведомления продолжают работать.
