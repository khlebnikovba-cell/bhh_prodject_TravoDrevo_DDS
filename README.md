# Telegram DDS Bot

Бот принимает траты в Telegram и ведет ДДС в Google Sheets.

## Что умеет

- Записывает траты и поступления в лист `Transactions`.
- Создает лист `DDS` с цельным журналом операций и лист `Summary` со сводками.
- Распознает даты, валюты и известные сервисы из листа `Library`.
- Поддерживает быстрый ввод из Telegram.
- Может ограничить доступ списком Telegram user ID.

## Быстрый старт

1. Создай Telegram-бота через [BotFather](https://t.me/BotFather) и скопируй токен.
2. Создай Google Sheet и скопируй ID из URL:

   `https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit`

3. Создай service account в Google Cloud, включи Google Sheets API, скачай JSON-ключ и положи его в проект как `credentials.json`.
4. В Google Sheet нажми `Share` и выдай доступ email-адресу service account из `credentials.json`.
5. Создай `.env` по примеру:

   ```bash
   cp .env.example .env
   ```

6. Установи зависимости и запусти:

   ```bash
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   venv/bin/python bot.py
   ```

## Настройки

- `TELEGRAM_BOT_TOKEN` - токен Telegram-бота.
- `GOOGLE_SHEET_ID` - ID Google Sheets.
- `GOOGLE_SERVICE_ACCOUNT_FILE` - путь к JSON-ключу service account.
- `DEFAULT_PROJECT` - проект по умолчанию, если не указан в сообщении.
- `TIMEZONE` - таймзона для дат.
- `ALLOWED_TELEGRAM_USER_IDS` - опционально, список ID через запятую.

## Форматы сообщений

```text
1500 материалы бетон М300
-50000 оплата от клиента
3 сентября покупка тарифа ультра на хигсвел - 1 месяц за 275 долларов
Дом | 1500 | Материалы | Бетон М300
/add Дом | 1500 | Материалы | Бетон М300
```

Положительная сумма считается расходом. Отрицательная сумма или слово `доход` считается поступлением.
Если валюта не указана, бот предложит выбрать `RUB`, `THB` или `USD` перед записью.

## Структура таблицы

Лист `Transactions`:

- `datetime`
- `date`
- `month`
- `cashflow_type`
- `amount`
- `project`
- `category`
- `description`
- `telegram_user`
- `telegram_user_id`
- `telegram_message_id`
- `currency`
- `vendor`
- `source_text`

Лист `Library` хранит канонические названия сервисов, их алиасы и категории. Листы `DDS` и `Summary` создаются ботом автоматически: в `DDS` каждая строка является одной операцией, а итоги по валютам находятся в `Summary`.
