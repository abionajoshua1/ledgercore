from django.test import SimpleTestCase
from apps.accounting.models import LedgerAccount, AccountType


class LedgerAccountTests(SimpleTestCase):

    def test_normal_side(self):

        test_cases = [
            (AccountType.ASSET, "DEBIT"),
            (AccountType.EXPENSE, "DEBIT"),
            (AccountType.LIABILITY, "CREDIT"),
            (AccountType.EQUITY, "CREDIT"),
            (AccountType.INCOME, "CREDIT"),
        ]

        for account_type, expected in test_cases:
            account = LedgerAccount(account_type=account_type)
            actual = account.normal_side

            self.assertEqual(actual, expected)