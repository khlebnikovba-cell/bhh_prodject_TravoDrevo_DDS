import asyncio
import logging
import os
import re
import secrets
from dataclasses import dataclass
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters


TRANSACTIONS_SHEET = "Transactions"
DDS_SHEET = "DDS"
SUMMARY_SHEET = "Summary"
LIBRARY_SHEET = "Library"
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
    "currency",
    "vendor",
    "source_text",
]
LIBRARY_HEADERS = ["canonical_name", "aliases", "default_category"]
DEFAULT_LIBRARY_ROWS = [
    ["Higgsfield", "хигсвел, хигсфилд, higgsfield", "Подписки"],
]
DDS_LAYOUT = [
    {"range": "A1", "values": [["Cashflow journal"]]},
    {
        "range": "A2",
        "values": [
            [
                '=IF(COUNT(Transactions!E2:E)=0; ""; QUERY(Transactions!A:N; "select B, D, F, G, M, E, L, H where B is not null order by B desc label B \'Date\', D \'Type\', F \'Project\', G \'Category\', M \'Vendor\', E \'Amount\', L \'Currency\', H \'Description\'"; 1))'
            ]
        ],
    },
]
SUMMARY_LAYOUT = [
    {"range": "A1", "values": [["Monthly net cashflow"]]},
    {
        "range": "A2",
        "values": [
            [
                '=IF(COUNT(Transactions!E2:E)=0; ""; QUERY(Transactions!A:N; "select C, L, sum(E) where C is not null group by C, L order by C, L label C \'Month\', L \'Currency\', sum(E) \'Net cashflow\'"; 1))'
            ]
        ],
    },
    {"range": "F1", "values": [["Project / category cashflow"]]},
    {
        "range": "F2",
        "values": [
            [
                '=IF(COUNT(Transactions!E2:E)=0; ""; QUERY(Transactions!A:N; "select F, G, L, sum(E) where F is not null group by F, G, L order by F, G, L label F \'Project\', G \'Category\', L \'Currency\', sum(E) \'Net cashflow\'"; 1))'
            ]
        ],
    },
]

MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
CURRENCY_ALIASES = {
    "USD": r"(?:usd|доллар(?:а|ов)?|долл(?:ар(?:а|ов)?)?|\$)",
    "RUB": r"(?:rub|руб(?:ль|ля|лей)?|₽)",
    "THB": r"(?:thb|бат(?:а|ов)?|฿)",
}
CURRENCY_BY_SYMBOL = {"$": "USD", "₽": "RUB", "฿": "THB"}
CURRENCY_CHOICES = ("RUB", "THB", "USD")
PENDING_TTL = timedelta(hours=1)

HELP_TEXT = """Пиши траты одним из форматов:

`1500 материалы бетон М300`
`-1500 материалы бетон М300`
`Дом | 1500 | Материалы | Бетон М300`
`3 сентября подписка Higgsfield на 1 месяц за 275 долларов`
`/add 1500 материалы бетон М300`
`/add Дом | 1500 | Материалы | Бетон М300`

Положительная сумма считается расходом. Отрицательная сумма или слово `доход` считается поступлением.
Если валюта не указана, бот попросит выбрать ее перед записью.

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
    transaction_date: date | None
    currency: str | None
    vendor: str
    source_text: str


@dataclass(frozen=True)
class VendorRule:
    canonical_name: str
    aliases: tuple[str, ...]
    default_category: str


@dataclass(frozen=True)
class PendingTransaction:
    transaction: ParsedTransaction
    user_name: str
    user_id: int
    message_id: int
    created_at: datetime


class SettingsError(RuntimeError):
    pass


class ParseError(ValueError):
    pass


def _library_is_empty(rows: list[list[str]]) -> bool:
    return not any(row and str(row[0]).strip() for row in rows[1:])


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
            transactions.update("A1:N1", [TRANSACTION_HEADERS])
            transactions.freeze(rows=1)

        library = self._worksheet(LIBRARY_SHEET)
        if library.row_values(1) != LIBRARY_HEADERS:
            library.update("A1:C1", [LIBRARY_HEADERS])
            library.freeze(rows=1)
        if _library_is_empty(library.get_all_values()):
            library.append_rows(DEFAULT_LIBRARY_ROWS, value_input_option="RAW")

        dds = self._worksheet(DDS_SHEET)
        dds.clear()
        dds.batch_update(DDS_LAYOUT, value_input_option="USER_ENTERED")

        summary = self._worksheet(SUMMARY_SHEET)
        summary.clear()
        summary.batch_update(SUMMARY_LAYOUT, value_input_option="USER_ENTERED")

    def append(
        self,
        transaction: ParsedTransaction,
        user_name: str,
        user_id: int,
        message_id: int,
        now: datetime,
    ) -> None:
        if not transaction.currency:
            raise ValueError("Currency must be selected before appending a transaction")
        signed_amount = -abs(transaction.amount) if transaction.cashflow_type == "expense" else abs(transaction.amount)
        transaction_date = transaction.transaction_date or now.date()
        row = [
            now.isoformat(timespec="seconds"),
            transaction_date.isoformat(),
            transaction_date.strftime("%Y-%m"),
            transaction.cashflow_type,
            str(signed_amount),
            transaction.project,
            transaction.category,
            transaction.description,
            user_name,
            str(user_id),
            str(message_id),
            transaction.currency,
            transaction.vendor,
            transaction.source_text,
        ]
        self._worksheet(TRANSACTIONS_SHEET).append_row(row, value_input_option="USER_ENTERED")

    def totals(self) -> dict[str, tuple[Decimal, Decimal, Decimal]]:
        rows = self._worksheet(TRANSACTIONS_SHEET).get_all_records()
        totals: dict[str, list[Decimal]] = {}
        for row in rows:
            amount = _to_decimal(str(row.get("amount", 0)))
            currency = str(row.get("currency") or "RUB").upper()
            income, expense = totals.setdefault(currency, [Decimal("0"), Decimal("0")])
            if amount >= 0:
                totals[currency][0] = income + amount
            else:
                totals[currency][1] = expense + amount
        return {
            currency: (income, expense, income + expense)
            for currency, (income, expense) in totals.items()
        }

    def vendor_rules(self) -> list[VendorRule]:
        rows = self._worksheet(LIBRARY_SHEET).get_all_records()
        rules = []
        for row in rows:
            canonical_name = str(row.get("canonical_name", "")).strip()
            if not canonical_name:
                continue
            aliases = tuple(
                alias.strip().lower()
                for alias in re.split(r"[,;]", str(row.get("aliases", "")))
                if alias.strip()
            )
            rules.append(
                VendorRule(
                    canonical_name=canonical_name,
                    aliases=(canonical_name.lower(), *aliases),
                    default_category=str(row.get("default_category", "")).strip() or "Прочее",
                )
            )
        return rules

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


def parse_transaction(
    text: str,
    default_project: str,
    reference_date: date | None = None,
    vendor_rules: list[VendorRule] | None = None,
) -> ParsedTransaction:
    source_text = text.strip()
    raw = source_text
    raw = re.sub(r"^/add(?:@\w+)?\s*", "", raw, flags=re.IGNORECASE)
    if not raw:
        raise ParseError("Не вижу сумму и категорию.")

    reference_date = reference_date or date.today()
    transaction_date, date_spans = _extract_date(raw, reference_date)
    rules = vendor_rules or []

    if "|" in raw:
        parts = [part.strip() for part in raw.split("|")]
        if len(parts) < 3:
            raise ParseError("Для формата через `|` нужно минимум: проект | сумма | категория.")
        project, amount_text, category = parts[:3]
        description = " | ".join(parts[3:]).strip()
        amount, currency, _ = _extract_money(amount_text, [])
        cashflow_type = _cashflow_type(raw, amount)
        vendor, vendor_category = _match_vendor(raw, rules)
        return ParsedTransaction(
            abs(amount),
            project or default_project,
            vendor_category or category,
            description,
            cashflow_type,
            transaction_date,
            currency,
            vendor,
            source_text,
        )

    amount, currency, money_span = _extract_money(raw, date_spans)
    vendor, vendor_category = _match_vendor(raw, rules)
    category, description = _category_and_description(raw, money_span, date_spans, vendor, vendor_category)
    cashflow_type = _cashflow_type(raw, amount)
    return ParsedTransaction(
        abs(amount),
        default_project,
        category,
        description,
        cashflow_type,
        transaction_date,
        currency,
        vendor,
        source_text,
    )


def _extract_date(text: str, reference_date: date) -> tuple[date | None, list[tuple[int, int]]]:
    lowered = text.lower()
    relative = re.search(r"\b(сегодня|вчера)\b", lowered)
    if relative:
        days = 1 if relative.group(1) == "вчера" else 0
        return reference_date - timedelta(days=days), [relative.span()]

    month_names = "|".join(MONTHS)
    written = re.search(rf"(?<!\d)(\d{{1,2}})\s+({month_names})(?:\s+(\d{{4}}))?\b", lowered)
    if written:
        parsed = _resolved_date(
            int(written.group(1)),
            MONTHS[written.group(2)],
            int(written.group(3)) if written.group(3) else None,
            reference_date,
        )
        return parsed, [written.span()]

    numeric = re.search(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](\d{2}|\d{4}))?(?!\d)", lowered)
    if numeric:
        year_text = numeric.group(3)
        year = None if year_text is None else int(year_text)
        if year is not None and year < 100:
            year += 2000
        parsed = _resolved_date(int(numeric.group(1)), int(numeric.group(2)), year, reference_date)
        return parsed, [numeric.span()]
    return None, []


def _resolved_date(day: int, month: int, year: int | None, reference_date: date) -> date:
    try:
        parsed = date(year or reference_date.year, month, day)
    except ValueError as exc:
        raise ParseError("Не смог прочитать дату в сообщении.") from exc
    if year is None and parsed > reference_date:
        parsed = parsed.replace(year=parsed.year - 1)
    return parsed


def _extract_money(
    text: str,
    excluded_spans: list[tuple[int, int]],
) -> tuple[Decimal, str | None, tuple[int, int]]:
    number = r"[-+]?\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d{1,2})?"
    aliases = "|".join(CURRENCY_ALIASES.values())
    currency_match = re.search(
        rf"(?<!\w)(?:(?P<symbol>[$₽฿])\s*(?P<prefix_amount>{number})|(?P<amount>{number})\s*(?P<currency>{aliases}))(?!\w)",
        text,
        flags=re.IGNORECASE,
    )
    if currency_match:
        amount_text = currency_match.group("prefix_amount") or currency_match.group("amount")
        marker = currency_match.group("symbol") or currency_match.group("currency") or ""
        return _to_decimal(amount_text), _currency_code(marker), currency_match.span()

    masked = list(text)
    duration_spans = [
        match.span()
        for match in re.finditer(
            r"(?<!\d)\d+(?:[.,]\d+)?\s*(?:дн(?:я|ей)?|недел(?:я|и|ь)|месяц(?:а|ев)?|год(?:а|ов)?)(?!\w)",
            text,
            flags=re.IGNORECASE,
        )
    ]
    for start, end in [*excluded_spans, *duration_spans]:
        masked[start:end] = " " * (end - start)
    masked_text = "".join(masked)
    candidates = list(re.finditer(rf"(?<!\w){number}(?!\w)", masked_text))
    if not candidates:
        raise ParseError("Не нашел сумму. Пример: `1500 материалы бетон`.")
    if len(candidates) > 1:
        preferred = [
            match
            for match in candidates
            if re.search(r"(?:за|оплатил(?:а)?|стоимость)\s*$", masked_text[: match.start()], re.IGNORECASE)
        ]
        if len(preferred) != 1:
            raise ParseError("Вижу несколько чисел и не понимаю сумму. Укажи валюту, например `275 USD`.")
        match = preferred[0]
    else:
        match = candidates[0]
    return _to_decimal(match.group(0)), None, match.span()


def _currency_code(marker: str) -> str:
    marker = marker.strip().lower()
    if marker in CURRENCY_BY_SYMBOL:
        return CURRENCY_BY_SYMBOL[marker]
    for code, pattern in CURRENCY_ALIASES.items():
        if re.fullmatch(pattern, marker, flags=re.IGNORECASE):
            return code
    return "RUB"


def _match_vendor(text: str, rules: list[VendorRule]) -> tuple[str, str]:
    lowered = text.lower()
    for rule in rules:
        if any(re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", lowered) for alias in rule.aliases):
            return rule.canonical_name, rule.default_category
    if re.search(r"\b(?:тариф|тарифа|подписк\w*|сервис\w*|сайт\w*)\b", lowered):
        candidate = re.search(
            r"\b(?:на|в)\s+([a-zа-я0-9._-]+)(?=\s*(?:[-—–]|$))",
            text,
            re.IGNORECASE,
        )
        if candidate:
            return candidate.group(1), "Подписки"
    return "", ""


def _category_and_description(
    text: str,
    money_span: tuple[int, int],
    date_spans: list[tuple[int, int]],
    vendor: str,
    vendor_category: str,
) -> tuple[str, str]:
    if vendor_category:
        plan_match = re.search(r"\bтариф[а]?\s+([a-zа-я0-9._-]+)", text, re.IGNORECASE)
        duration_match = re.search(
            r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(дн(?:я|ей)?|недел(?:я|и|ь)|месяц(?:а|ев)?|год(?:а|ов)?)",
            text,
            re.IGNORECASE,
        )
        parts = []
        if plan_match:
            plan = plan_match.group(1)
            plan = "Ultra" if plan.lower() == "ультра" else plan.capitalize()
            parts.append(f"Тариф {plan}")
        if duration_match:
            parts.append(f"{duration_match.group(1)} {duration_match.group(2).lower()}")
        return vendor_category, ", ".join(parts) or f"Оплата {vendor}"

    remaining = list(text)
    for start, end in [money_span, *date_spans]:
        remaining[start:end] = " " * (end - start)
    words = "".join(remaining).strip(" -—–,.").split()
    words = [word for word in words if word.lower() not in {"доход", "поступление", "приход", "расход", "трата"}]
    if not words:
        raise ParseError("Укажи категорию. Пример: `1500 материалы бетон`.")
    return words[0], " ".join(words[1:])


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


def _recorded_text(transaction: ParsedTransaction) -> str:
    sign = "-" if transaction.cashflow_type == "expense" else "+"
    return (
        f"Записал: {sign}{_format_money(transaction.amount)} | "
        f"{transaction.currency} | {transaction.project} | {transaction.category}"
        + (f" | {transaction.vendor}" if transaction.vendor else "")
    )


def _pending_transactions(context: ContextTypes.DEFAULT_TYPE) -> dict[str, PendingTransaction]:
    return context.application.bot_data.setdefault("pending_transactions", {})


def _remove_expired_pending(pending: dict[str, PendingTransaction], now: datetime) -> None:
    expired = [key for key, item in pending.items() if now - item.created_at > PENDING_TTL]
    for key in expired:
        pending.pop(key, None)


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
    message = update.effective_message

    if message is None:
        return

    if not is_allowed(settings, update):
        await message.reply_text("У тебя нет доступа к этому боту.")
        return

    try:
        now = datetime.now(settings.timezone)
        transaction = parse_transaction(
            message.text or "",
            settings.default_project,
            now.date(),
            ledger.vendor_rules(),
        )
    except ParseError as exc:
        await message.reply_text(f"{exc}\n\n/help покажет примеры.", parse_mode="Markdown")
        return

    user = update.effective_user
    if user is None:
        return

    if transaction.currency:
        ledger.append(transaction, user.full_name, user.id, message.message_id, now)
        await message.reply_text(_recorded_text(transaction))
        return

    pending = _pending_transactions(context)
    _remove_expired_pending(pending, now)
    pending_id = secrets.token_urlsafe(8)
    pending[pending_id] = PendingTransaction(
        transaction=transaction,
        user_name=user.full_name,
        user_id=user.id,
        message_id=message.message_id,
        created_at=now,
    )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(currency, callback_data=f"currency:{pending_id}:{currency}") for currency in CURRENCY_CHOICES]]
    )
    await message.reply_text("В какой валюте эта операция?", reply_markup=keyboard)


async def confirm_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return

    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "currency" or parts[2] not in CURRENCY_CHOICES:
        await query.answer("Неизвестный выбор валюты.", show_alert=True)
        return

    pending_id, currency = parts[1], parts[2]
    settings: Settings = context.application.bot_data["settings"]
    now = datetime.now(settings.timezone)
    pending = _pending_transactions(context)
    _remove_expired_pending(pending, now)
    item = pending.get(pending_id)
    if item is None:
        await query.answer()
        await query.edit_message_text("Выбор устарел или уже использован. Отправь трату заново.")
        return
    if item.user_id != user.id:
        await query.answer("Эту валюту должен выбрать автор операции.", show_alert=True)
        return

    ledger: SheetsLedger = context.application.bot_data["ledger"]
    transaction = replace(item.transaction, currency=currency)
    try:
        ledger.append(transaction, item.user_name, item.user_id, item.message_id, now)
    except Exception:
        logging.exception("Failed to append a confirmed transaction")
        await query.answer("Не удалось записать операцию. Попробуй еще раз.", show_alert=True)
        return

    pending.pop(pending_id, None)
    await query.answer()
    await query.edit_message_text(_recorded_text(transaction))


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    ledger: SheetsLedger = context.application.bot_data["ledger"]

    if not is_allowed(settings, update):
        await update.message.reply_text("У тебя нет доступа к этому боту.")
        return

    totals = ledger.totals()
    if not totals:
        await update.message.reply_text("В таблице пока нет операций.")
        return
    blocks = ["Сводка по таблице:"]
    for currency, (income, expense, balance) in sorted(totals.items()):
        blocks.append(
            f"\n{currency}\n"
            f"Доходы: {_format_money(income)}\n"
            f"Расходы: {_format_money(abs(expense))}\n"
            f"Баланс: {_format_money(balance)}"
        )
    await update.message.reply_text("\n".join(blocks))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    ledger = SheetsLedger(settings)
    ledger.ensure_layout()

    app = Application.builder().token(settings.telegram_token).build()
    app.bot_data["settings"] = settings
    app.bot_data["ledger"] = ledger
    app.bot_data["pending_transactions"] = {}
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("add", add_transaction))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CallbackQueryHandler(confirm_currency, pattern=r"^currency:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_transaction))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)
    finally:
        asyncio.set_event_loop(None)
        loop.close()


if __name__ == "__main__":
    main()
