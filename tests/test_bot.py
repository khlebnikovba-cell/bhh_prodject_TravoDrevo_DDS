import unittest
from datetime import date

from bot import ParseError, VendorRule, _library_is_empty, parse_transaction


REFERENCE_DATE = date(2026, 9, 7)
VENDORS = [
    VendorRule("Higgsfield", ("higgsfield", "хигсвел", "хигсфилд"), "Подписки"),
]


class ParseTransactionTests(unittest.TestCase):
    def parse(self, text: str):
        return parse_transaction(text, "TravoDrevo", REFERENCE_DATE, "RUB", VENDORS)

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
        self.assertEqual(transaction.currency, "RUB")

    def test_existing_compact_format(self):
        transaction = self.parse("1500 материалы бетон М300")

        self.assertEqual(str(transaction.amount), "1500")
        self.assertEqual(transaction.category, "материалы")
        self.assertEqual(transaction.description, "бетон М300")
        self.assertEqual(transaction.currency, "RUB")

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


if __name__ == "__main__":
    unittest.main()
