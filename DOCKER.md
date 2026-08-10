# Запуск через Docker Compose

Перед первым запуском должны существовать файлы `.env` и `tennis.db`. Текущая база данных будет один раз скопирована в постоянный Docker-volume.

Порт задаётся в `.env`, например `PORT=5000`. После изменения порта также укажите его в `APP_URL` и адресах возврата OIDC.

Если обычный локальный сервер уже работает на порту 5000, сначала остановите его. Затем выполните:

```powershell
docker compose up -d --build
```

Сайт будет доступен по адресу <http://localhost:5000> и по IP компьютера в школьной сети на порту 5000.

Проверить состояние:

```powershell
docker compose ps
docker compose logs -f tennis
```

Остановить сайт без удаления данных:

```powershell
docker compose down
```

После изменения исходного кода пересоберите образ:

```powershell
docker compose up -d --build
```

Данные хранятся в volume `tennis-data`. Команда `docker compose down -v` удалит этот volume вместе с Docker-копией базы данных.
