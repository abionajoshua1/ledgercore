from django.test import SimpleTestCase, TestCase
from apps.accounting.models import LedgerAccount, AccountType, Currency
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

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
            
class LedgerAccountDatabaseTests(TestCase):
    def test_same_account_code_allowed_for_different_currencies(self):
        ngn = Currency.objects.create(
            currency_code="NGN",
            currency_name="Nigerian Naira",
            minor_unit_exponent=2,
        )

        usd = Currency.objects.create(
            currency_code="USD",
            currency_name="US Dollar",
            minor_unit_exponent=2,
        )

        account_one = LedgerAccount.objects.create(
            account_code="WALLET-001",
            account_name="Customer Wallet NGN",
            account_type=AccountType.LIABILITY,
            currency=ngn,
        )

        account_two = LedgerAccount.objects.create(
            account_code="WALLET-001",
            account_name="Customer Wallet USD",
            account_type=AccountType.LIABILITY,
            currency=usd,
        )
        
    def test_duplicate_account_code_rejected_for_same_currency(self):
        ngn = Currency.objects.create(
            currency_code="NGN",
            currency_name="Nigerian Naira",
            minor_unit_exponent=2,
        )
        
        LedgerAccount.objects.create(
            account_code="WALLET-001",
            account_name="Customer Wallet 1",
            account_type=AccountType.LIABILITY,
            currency=ngn,
        )
        
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerAccount.objects.create(
                    account_code="WALLET-001",
                    account_name="Customer Wallet 2",
                    account_type=AccountType.LIABILITY,
                    currency=ngn,
                )
                
        actual = LedgerAccount.objects.count()
        expected = 1

        self.assertEqual(actual, expected)

    def test_currency_cannot_be_deleted_while_referenced(self):
        ngn = Currency.objects.create(
            currency_code="NGN",
            currency_name="Nigerian Naira",
            minor_unit_exponent=2,
        )

        LedgerAccount.objects.create(
            account_code="WALLET-001",
            account_name="Customer Wallet NGN",
            account_type=AccountType.LIABILITY,
            currency=ngn,
        )

        with self.assertRaises(ProtectedError):
            ngn.delete()

        actual = Currency.objects.filter(currency_code="NGN").exists()
        expected = True

        self.assertEqual(actual, expected)