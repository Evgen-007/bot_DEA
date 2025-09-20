# WX Bot (RU-only)

Telegram-бот на Python для быстрого получения METAR/SPECI/TAF по российским аэродромам (ICAO `U***`).

## Возможности

- Команды `/wx`, `/route`, `/brief` и свободный текст ("Москва", "Сочи")
- Автоматическое сопоставление городов с ICAO на основе локального среза OurAirports
- Получение METAR/SPECI (за последние 6 часов) и TAF (24 часа) из NOAA ADDS
- Простая эвристика категории погоды: VFR/MVFR/IFR/LIFR
- Форматированный HTML-ответ с возрастом METAR и заметками, читаемый в Telegram
- Анализ маршрута (коридор ±80 км) с подбором станций и резюме проходимости
- Подбор актуальных SIGMET из AWC GeoJSON по коридору маршрута
- Экспорт брифинга в формате НАМС-86 (DOCX и PDF) с одним запросом `/brief`

## Установка

```bash
python -m venv .venv
source .venv/bin/activate  # или .venv\Scripts\activate в Windows
pip install -r requirements.txt
```

## Настройка

1. Скопируйте `.env.example` в `.env` и пропишите токен Telegram-бота:

```bash
cp .env.example .env  # в Windows: copy .env.example .env
```

2. При необходимости измените `HTTP_TIMEOUT` (секунды ожидания NOAA ADDS).

## Запуск

```bash
python -m bot.main
```

После запуска бот начинает polling и готов отвечать. Примеры запросов:

- `/start` — подсказка и примеры использования
- `/wx UUEE UUWW URSS` — сводка для Шереметьево, Внуково и Сочи
- `/route UUEE URSS (UUWW)` — маршрут Москва–Сочи с коридором и SIGMET
- `/brief UUEE UUWW` — сформировать файлы брифинга (DOCX/PDF)
- `Москва, Сочи` — свободный текст, будут найдены совпадения из справочника

## Зависимости

- aiogram 3
- httpx
- python-dotenv
- pydantic 2

Все зависимости установлены в `requirements.txt` с фиксированными версиями.
