import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from bot import (
    ParseError,
    Settings,
    VendorRule,
    _library_is_empty,
    add_transaction,
    confirm_currency,
    parse_transaction,
)


REFERENCE_DATE = date(2026, 9, 7)
VENDORS = [
    VendorRule("Higgsfield", ("higgsfield", "хигсвел", "хигсфилд"), "Подписки"),
]


class ParseTransactionTests(unittest.TestCase):
    def parse(self, text: str):
        return parse_transaction(text, "TravoDrevo", REFERENCE_DATE, VENDORS)

    def test_subscription_date_duration_and_usd_amount(self):
        transaction = self.parse(
            "3 сентября покупка тарифа ультра на хигсвел - 1 месяц за 275 долларов"
        )

        self.assertEqual(transaction.transaction_date, date(2026, 9, 3))
        self.assertEqual(str(transaction.amount), "275")
        self.assertEqual(transaction.currency, "USD")
        self.assertEqual(transaction.category, "Подписки")
        self.assertEqual(transaction.vendor, "Higgsfield")
        self.assertEqual(transaction.description, "Тариф Ultra, 1 месяц")

    def test_future_written_date_without_year_uses_previous_year(self):
        transaction = self.parse("10 октября 500 рублей материалы")

        self.assertEqual(transaction.transaction_date, date(2025, 10, 10))
        self.assertEqual(str(transaction.amount), "500")

    def test_relative_date(self):
        transaction = self.parse("вчера 1200 руб доставка")

        self.assertEqual(transaction.transaction_date, date(2026, 9, 6))
        self.assertEqual(transaction.category, "доставка")

    def test_numeric_date_is_not_used_as_amount(self):
        transaction = self.parse("03.09 подписка Higgsfield 99 USD")

        self.assertEqual(transaction.transaction_date, date(2026, 9, 3))
        self.assertEqual(str(transaction.amount), "99")

    def test_duration_is_not_used_as_amount(self):
        transaction = self.parse("подписка Higgsfield 1 месяц 275")

        self.assertEqual(str(transaction.amount), "275")
        self.assertIsNone(transaction.currency)

    def test_existing_compact_format(self):
        transaction = self.parse("1500 материалы бетон М300")

        self.assertEqual(str(transaction.amount), "1500")
        self.assertEqual(transaction.category, "материалы")
        self.assertEqual(transaction.description, "бетон М300")
        self.assertIsNone(transaction.currency)

    def test_command_is_preserved_in_source_text(self):
        transaction = self.parse("/add 1500 материалы бетон М300")

        self.assertEqual(transaction.source_text, "/add 1500 материалы бетон М300")

    def test_pipe_format_with_currency(self):
        transaction = self.parse("Дом | 2300 THB | Доставка | Доставка материалов")

        self.assertEqual(transaction.project, "Дом")
        self.assertEqual(str(transaction.amount), "2300")
        self.assertEqual(transaction.currency, "THB")
        self.assertEqual(transaction.category, "Доставка")

    def test_unknown_subscription_vendor_is_preserved(self):
        transaction = self.parse("подписка на Example.AI - 20 USD")

        self.assertEqual(transaction.vendor, "Example.AI")
        self.assertEqual(transaction.category, "Подписки")

    def test_ambiguous_numbers_require_currency(self):
        with self.assertRaises(ParseError):
            self.parse("купил 2 товара 300")


class LibraryLayoutTests(unittest.TestCase):
    def test_header_only_library_is_empty(self):
        self.assertTrue(_library_is_empty([["canonical_name", "aliases", "default_category"]]))

    def test_library_with_vendor_is_not_empty(self):
        self.assertFalse(
            _library_is_empty(
                [
                    ["canonical_name", "aliases", "default_category"],
                    ["Higgsfield", "хигсвел", "Подписки"],
                ]
            )
        )


class FakeLedger:
    def __init__(self):
        self.appended = []

    def vendor_rules(self):
        return VENDORS

    def append(self, *args):
        self.appended.append(args)


def make_context(ledger):
    settings = Settings(
        telegram_token="token",
        google_sheet_id="sheet",
        service_account_file=Path("credentials.json"),
        default_project="TravoDrevo",
        timezone=ZoneInfo("Asia/Bangkok"),
        allowed_user_ids=set(),
    )
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": settings,
                "ledger": ledger,
                "pending_transactions": {},
            }
        )
    )


def make_message_update(text, user_id=10, message_id=20):
    message = SimpleNamespace(
        text=text,
        message_id=message_id,
        reply_text=AsyncMock(),
    )
    user = SimpleNamespace(id=user_id, full_name="Test User")
    return SimpleNamespace(message=message, effective_message=message, effective_user=user)


class CurrencyConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_currency_writes_immediately(self):
        ledger = FakeLedger()
        context = make_context(ledger)
        update = make_message_update("доставка 300 бат")

        await add_transaction(update, context)

        self.assertEqual(len(ledger.appended), 1)
        self.assertEqual(ledger.appended[0][0].currency, "THB")
        self.assertEqual(context.application.bot_data["pending_transactions"], {})

    async def test_missing_currency_prompts_without_writing(self):
        ledger = FakeLedger()
        context = make_context(ledger)
        update = make_message_update("вчера доставка 300")

        await add_transaction(update, context)

        self.assertEqual(ledger.appended, [])
        update.message.reply_text.assert_awaited_once()
        self.assertEqual(update.message.reply_text.await_args.args[0], "В какой валюте эта операция?")
        keyboard = update.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ["RUB", "THB", "USD"])

    async def test_effective_message_is_used_when_message_field_is_empty(self):
        ledger = FakeLedger()
        context = make_context(ledger)
        update = make_message_update("доставка 300 бат")
        update.message = None

        await add_transaction(update, context)

        self.assertEqual(len(ledger.appended), 1)
        update.effective_message.reply_text.assert_awaited_once()

    async def test_confirmation_writes_once_and_duplicate_is_rejected(self):
        ledger = FakeLedger()
        context = make_context(ledger)
        message_update = make_message_update("вчера доставка 300")
        await add_transaction(message_update, context)
        keyboard = message_update.message.reply_text.await_args.kwargs["reply_markup"]
        callback_data = keyboard.inline_keyboard[0][1].callback_data
        query = SimpleNamespace(
            data=callback_data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        callback_update = SimpleNamespace(callback_query=query, effective_user=message_update.effective_user)

        await confirm_currency(callback_update, context)
        await confirm_currency(callback_update, context)

        self.assertEqual(len(ledger.appended), 1)
        self.assertEqual(ledger.appended[0][0].currency, "THB")
        self.assertEqual(query.edit_message_text.await_count, 2)
        self.assertIn("уже использован", query.edit_message_text.await_args.args[0])

    async def test_different_user_cannot_confirm(self):
        ledger = FakeLedger()
        context = make_context(ledger)
        message_update = make_message_update("300 доставка", user_id=10)
        await add_transaction(message_update, context)
        keyboard = message_update.message.reply_text.await_args.kwargs["reply_markup"]
        query = SimpleNamespace(
            data=keyboard.inline_keyboard[0][0].callback_data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        callback_update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=99, full_name="Other User"),
        )

        await confirm_currency(callback_update, context)

        self.assertEqual(ledger.appended, [])
        query.answer.assert_awaited_once_with(
            "Эту валюту должен выбрать автор операции.", show_alert=True
        )

    async def test_expired_confirmation_is_not_written(self):
        ledger = FakeLedger()
        context = make_context(ledger)
        message_update = make_message_update("300 доставка")
        await add_transaction(message_update, context)
        pending = context.application.bot_data["pending_transactions"]
        pending_id, item = next(iter(pending.items()))
        pending[pending_id] = item.__class__(
            transaction=item.transaction,
            user_name=item.user_name,
            user_id=item.user_id,
            message_id=item.message_id,
            created_at=datetime.now(ZoneInfo("Asia/Bangkok")) - timedelta(hours=2),
        )
        query = SimpleNamespace(
            data=f"currency:{pending_id}:THB",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        callback_update = SimpleNamespace(
            callback_query=query,
            effective_user=message_update.effective_user,
        )

        await confirm_currency(callback_update, context)

        self.assertEqual(ledger.appended, [])
        self.assertIn("устарел", query.edit_message_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
