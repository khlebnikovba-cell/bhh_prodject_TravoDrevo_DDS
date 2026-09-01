import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


TRANSACTIONS_SHEET = "Transactions"
DDS_SHEET = "DDS"
TRANSACTION_HEADERS = [
    "datetime",
    "date",
    "month",
    "cashflow_type",
    "amount",
    "project",
    "category",
    "description",
    "telegram_user",
    "telegram_user_id",
    "telegram_message_id",
]

HELP_TEXT = """Пиши траты одним из форматов:

`1500 материалы бетон М300`
`-1500 материалы бетон М300`
`Дом | 1500 | Материалы | Бетон М300`
`/add 1500 материалы бетон М300`
`/add Дом | 1500 | Материалы | Бетон М300`

Положительная сумма считается расходом. Отрицательная сумма или слово `доход` считается поступлением.

Команды:
/summary - краткая сводка
/help - помощь
"""


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    google_sheet_id: str
    service_account_file: Path
    default_project: str
    timezone: ZoneInfo
    allowed_user_ids: set[int]


@dataclass(frozen=True)
class ParsedTransaction:
    amount: Decimal
    project: str
    category: str
    description: str
    cashflow_type: str


class SettingsError(RuntimeError):
    pass


class ParseError(ValueError):
    pass


class SheetsLedger:
    def __init__(self, settings: Settings):
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_file(
            settings.service_account_file,
            scopes=scopes,
        )
        self.sheet = gspread.authorize(credentials).open_by_key(settings.google_sheet_id)

    def ensure_layout(self) -> None:
        transactions = self._worksheet(TRANSACTIONS_SHEET)
        first_row = transactions.row_values(1)
        if first_row != TRANSACTION_HEADERS:
            transactions.resize(rows=max(transactions.row_count, 1000), cols=len(TRANSACTION_HEADERS))
            transactions.update("A1:K1", [TRANSACTION_HEADERS])
            transactions.freeze(rows=1)

        dds = self._worksheet(DDS_SHEET)
        dds.clear()
        dds.batch_update(
            [
                {"range": "A1", "values": [["Monthly net cashflow"]]},
                {
                    "range": "A2",
                    "values": [
                        [
                            '=QUERY(Transactions!A:K, "select C, sum(E) where C is not null group by C order by C label C \'Month\', sum(E) \'Net cashflow\'", 1)'
                        ]
                    ],
                },
                {"range": "D1", "values": [["Project / category cashflow"]]},
                {
                    "range": "D2",
                    "values": [
                        [
                            '=QUERY(Transactions!A:K, "select F, G, sum(E) where F is not null group by F, G order by F, G label F \'Project\', G \'Category\', sum(E) \'Net cashflow\'", 1)'
                        ]
                    ],
                },
                {"range": "H1", "values": [["Raw expenses only"]]},
                {
                    "range": "H2",
                    "values": [
                        [
                            '=QUERY(Transactions!A:K, "select B, F, G, E, H where D = \'expense\' order by B desc label B \'Date\', F \'Project\', G \'Category\', E \'Amount\', H \'Description\'", 1)'
                        ]
                    ],
                },
            ],
            value_input_option="USER_ENTERED",
        )

    def append(self, transaction: ParsedTransaction, update: Update, now: datetime) -> None:
        message = update.effective_message
        user = update.effective_user
        signed_amount = -abs(transaction.amount) if transaction.cashflow_type == "expense" else abs(transaction.amount)
        row = [
            now.isoformat(timespec="seconds"),
            now.strftime("%Y-%m-%d"),
            now.strftime("%Y-%m"),
            transaction.cashflow_type,
            str(signed_amount),
            transaction.project,
            transaction.category,
            transaction.description,
            user.full_name if user else "",
            str(user.id) if user else "",
            str(message.message_id) if message else "",
        ]
        self._worksheet(TRANSACTIONS_SHEET).append_row(row, value_input_option="USER_ENTERED")

    def totals(self) -> tuple[Decimal, Decimal, Decimal]:
        rows = self._worksheet(TRANSACTIONS_SHEET).get_all_records()
        income = Decimal("0")
        expense = Decimal("0")
        for row in rows:
            amount = _to_decimal(str(row.get("amount", 0)))
            if amount >= 0:
                income += amount
            else:
                expense += amount
        return income, expense, income + expense

    def _worksheet(self, title: str):
        try:
            return self.sheet.worksheet(title)
        except gspread.WorksheetNotFound:
            return self.sheet.add_worksheet(title=title, rows=1000, cols=20)


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    service_account_file = Path(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials.json")).expanduser()
    default_project = os.getenv("DEFAULT_PROJECT", "General").strip() or "General"
    timezone_name = os.getenv("TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
    allowed_user_ids = {
        int(value.strip())
        for value in os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").split(",")
        if value.strip()
    }

    missing = []
    if not token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not sheet_id:
        missing.append("GOOGLE_SHEET_ID")
    if not service_account_file.exists():
        missing.append(f"GOOGLE_SERVICE_ACCOUNT_FILE ({service_account_file})")
    if missing:
        raise SettingsError("Missing settings: " + ", ".join(missing))

    return Settings(
        telegram_token=token,
        google_sheet_id=sheet_id,
        service_account_file=service_account_file,
        default_project=default_project,
        timezone=ZoneInfo(timezone_name),
        allowed_user_ids=allowed_user_ids,
    )


def parse_transaction(text: str, default_project: str) -> ParsedTransaction:
    raw = text.strip()
    raw = re.sub(r"^/add(?:@\w+)?\s*", "", raw, flags=re.IGNORECASE)
    if not raw:
        raise ParseError("Не вижу сумму и категорию.")

    if "|" in raw:
        parts = [part.strip() for part in raw.split("|")]
        if len(parts) < 3:
            raise ParseError("Для формата через `|` нужно минимум: проект | сумма | категория.")
        project, amount_text, category = parts[:3]
        description = " | ".join(parts[3:]).strip()
        amount = _to_decimal(amount_text)
        cashflow_type = _cashflow_type(raw, amount)
        return ParsedTransaction(abs(amount), project or default_project, category, description, cashflow_type)

    normalized = raw.replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d{1,2})?", normalized)
    if not match:
        raise ParseError("Не нашел сумму. Пример: `1500 материалы бетон`.")

    amount = _to_decimal(match.group(0))
    before = normalized[: match.start()].strip()
    after = normalized[match.end() :].strip()
    words = after.split()
    if not words:
        raise ParseError("После суммы укажи категорию. Пример: `1500 материалы бетон`.")

    project = default_project
    if before:
        before_words = [word for word in before.split() if word.lower() not in {"доход", "поступление", "расход", "трата"}]
        if before_words:
            project = " ".join(before_words)

    category = words[0]
    description = " ".join(words[1:])
    cashflow_type = _cashflow_type(raw, amount)
    return ParsedTransaction(abs(amount), project, category, description, cashflow_type)


def _cashflow_type(text: str, amount: Decimal) -> str:
    lowered = text.lower()
    if amount < 0 or any(word in lowered for word in ("доход", "поступление", "приход")):
        return "income"
    return "expense"


def _to_decimal(value: str) -> Decimal:
    cleaned = value.replace(" ", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ParseError(f"Не смог прочитать сумму `{value}`.") from exc


def _format_money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ")


def is_allowed(settings: Settings, update: Update) -> bool:
    user = update.effective_user
    return not settings.allowed_user_ids or (user is not None and user.id in settings.allowed_user_ids)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def add_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    ledger: SheetsLedger = context.application.bot_data["ledger"]

    if not is_allowed(settings, update):
        await update.message.reply_text("У тебя нет доступа к этому боту.")
        return

    try:
        transaction = parse_transaction(update.message.text or "", settings.default_project)
        now = datetime.now(settings.timezone)
        ledger.append(transaction, update, now)
    except ParseError as exc:
        await update.message.reply_text(f"{exc}\n\n/help покажет примеры.", parse_mode="Markdown")
        return

    sign = "-" if transaction.cashflow_type == "expense" else "+"
    await update.message.reply_text(
        f"Записал: {sign}{_format_money(transaction.amount)} | "
        f"{transaction.project} | {transaction.category}"
    )


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    ledger: SheetsLedger = context.application.bot_data["ledger"]

    if not is_allowed(settings, update):
        await update.message.reply_text("У тебя нет доступа к этому боту.")
        return

    income, expense, balance = ledger.totals()
    await update.message.reply_text(
        "Сводка по таблице:\n"
        f"Доходы: {_format_money(income)}\n"
        f"Расходы: {_format_money(abs(expense))}\n"
        f"Баланс: {_format_money(balance)}"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    ledger = SheetsLedger(settings)
    ledger.ensure_layout()

    app = Application.builder().token(settings.telegram_token).build()
    app.bot_data["settings"] = settings
    app.bot_data["ledger"] = ledger
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("add", add_transaction))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_transaction))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
