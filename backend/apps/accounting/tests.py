from django.test import SimpleTestCase, TestCase, TransactionTestCase
from apps.accounting.models import LedgerAccount, AccountType, Currency, JournalEntry, JournalLine, OperationType, EntryDirection
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from unittest.mock import patch
from apps.accounting.services import (
    JournalPostingError,
    create_journal_entry,
)


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

class JournalEntryTests(TransactionTestCase):
    def setUp(self):
        self.ngn = Currency.objects.create(
            currency_code="NGN",
            currency_name="Nigerian Naira",
            minor_unit_exponent=2,
        )
        self.bank_account = LedgerAccount.objects.create(
            account_code="BANK-001",
            account_name="Settlement Bank",
            account_type=AccountType.ASSET,
            currency=self.ngn,
        )
        self.wallet_account = LedgerAccount.objects.create(
            account_code="WALLET-001",
            account_name="Customer Wallet",
            account_type=AccountType.LIABILITY,
            currency=self.ngn,
        )

    def test_balanced_journal_entry_commits_successfully(self):
        with transaction.atomic():
            self.entry = JournalEntry.objects.create(
                reference="DEPOSIT-001",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Customer wallet deposit",
            )
            JournalLine.objects.create(
                entry=self.entry,
                account=self.bank_account,
                line_number=1,
                direction=EntryDirection.DEBIT,
                amount=10000,
                currency=self.ngn,
            )
            JournalLine.objects.create(
                entry=self.entry,
                account=self.wallet_account,
                line_number=2,
                direction=EntryDirection.CREDIT,
                amount=10000,
                currency=self.ngn,
            )
        self.entry.refresh_from_db()

        self.assertIsNotNone(self.entry.posted_at)
        self.assertEqual(JournalLine.objects.filter(entry=self.entry).count(), 2)

    def test_journal_entry_with_one_line_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                entry = JournalEntry.objects.create(
                    reference="DEPOSIT-002",
                    operation_type=OperationType.DEPOSIT,
                    currency=self.ngn,
                    description="Customer wallet deposit",
                )
                JournalLine.objects.create(
                    entry=entry,
                    account=self.bank_account,
                    line_number=1,
                    direction=EntryDirection.DEBIT,
                    amount=10000,
                    currency=self.ngn,
                )
        self.assertFalse(
            JournalEntry.objects.filter(reference="DEPOSIT-002").exists()
        )

    def test_unbalanced_journal_entry_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                entry = JournalEntry.objects.create(
                    reference="DEPOSIT-003",
                    operation_type=OperationType.DEPOSIT,
                    currency=self.ngn,
                    description="Customer wallet deposit",
                )
                JournalLine.objects.create(
                    entry=entry,
                    account=self.bank_account,
                    line_number=1,
                    direction=EntryDirection.DEBIT,
                    amount=10000,
                    currency=self.ngn,
                )
                JournalLine.objects.create(
                    entry=entry,
                    account=self.wallet_account,
                    line_number=2,
                    direction=EntryDirection.CREDIT,
                    amount=9000,
                    currency=self.ngn,
                )
        self.assertFalse(
            JournalEntry.objects.filter(reference="DEPOSIT-003").exists()
        )

    def test_posted_journal_entry_cannot_be_updated(self):
        with transaction.atomic():
            entry = JournalEntry.objects.create(
                reference="DEPOSIT-004",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Customer wallet deposit",
            )
            JournalLine.objects.create(
                entry=entry,
                account=self.bank_account,
                line_number=1,
                direction=EntryDirection.DEBIT,
                amount=10000,
                currency=self.ngn,
            )
            JournalLine.objects.create(
                entry=entry,
                account=self.wallet_account,
                line_number=2,
                direction=EntryDirection.CREDIT,
                amount=10000,
                currency=self.ngn,
            )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalEntry.objects.filter(
                    reference="DEPOSIT-004"
                ).update(description="Modified description")

        entry.refresh_from_db()
        self.assertEqual(entry.description, "Customer wallet deposit")

    def test_posted_journal_entry_cannot_be_deleted(self):
        with transaction.atomic():
            entry = JournalEntry.objects.create(
                reference="DEPOSIT-005",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Customer wallet deposit",
            )
            JournalLine.objects.create(
                entry=entry,
                account=self.bank_account,
                line_number=1,
                direction=EntryDirection.DEBIT,
                amount=10000,
                currency=self.ngn,
            )
            JournalLine.objects.create(
                entry=entry,
                account=self.wallet_account,
                line_number=2,
                direction=EntryDirection.CREDIT,
                amount=10000,
                currency=self.ngn,
            )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalEntry.objects.filter(reference="DEPOSIT-005").delete()

        self.assertTrue(
            JournalEntry.objects.filter(reference="DEPOSIT-005").exists()
        )

    def test_posted_journal_line_cannot_be_updated(self):
        with transaction.atomic():
            entry = JournalEntry.objects.create(
                reference="DEPOSIT-006",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Customer wallet deposit",
            )
            debit_line = JournalLine.objects.create(
                entry=entry,
                account=self.bank_account,
                line_number=1,
                direction=EntryDirection.DEBIT,
                amount=10000,
                currency=self.ngn,
            )
            JournalLine.objects.create(
                entry=entry,
                account=self.wallet_account,
                line_number=2,
                direction=EntryDirection.CREDIT,
                amount=10000,
                currency=self.ngn,
            )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalLine.objects.filter(id=debit_line.id).update(amount=15000)

        debit_line.refresh_from_db()
        self.assertEqual(debit_line.amount, 10000)

    def test_posted_journal_line_cannot_be_deleted(self):
        with transaction.atomic():
            entry = JournalEntry.objects.create(
                reference="DEPOSIT-007",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Customer wallet deposit",
            )
            debit_line = JournalLine.objects.create(
                entry=entry,
                account=self.bank_account,
                line_number=1,
                direction=EntryDirection.DEBIT,
                amount=10000,
                currency=self.ngn,
            )
            JournalLine.objects.create(
                entry=entry,
                account=self.wallet_account,
                line_number=2,
                direction=EntryDirection.CREDIT,
                amount=10000,
                currency=self.ngn,
            )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalLine.objects.filter(id=debit_line.id).delete()

        self.assertTrue(
            JournalLine.objects.filter(id=debit_line.id).exists()
        )

    def test_journal_line_currency_must_match_entry_currency(self):
        usd = Currency.objects.create(
            currency_code="USD",
            currency_name="US Dollar",
            minor_unit_exponent=2,
        )
        usd_account = LedgerAccount.objects.create(
            account_code="BANK-USD-001",
            account_name="Customer Wallet USD",
            account_type=AccountType.ASSET,
            currency=usd,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                entry = JournalEntry.objects.create(
                    reference="DEPOSIT-008",
                    operation_type=OperationType.DEPOSIT,
                    currency=self.ngn,
                    description="Customer wallet deposit",
                )
                JournalLine.objects.create(
                    entry=entry,
                    account=usd_account,
                    line_number=1,
                    direction=EntryDirection.DEBIT,
                    amount=10000,
                    currency=usd,
                )
        self.assertFalse(
            JournalEntry.objects.filter(reference="DEPOSIT-008").exists()
        )

    def test_journal_line_currency_must_match_account_currency(self):
        usd = Currency.objects.create(
            currency_code="USD",
            currency_name="US Dollar",
            minor_unit_exponent=2,
        )
        usd_account = LedgerAccount.objects.create(
            account_code="BANK-USD-002",
            account_name="Customer Wallet USD",
            account_type=AccountType.ASSET,
            currency=usd,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                entry = JournalEntry.objects.create(
                    reference="DEPOSIT-009",
                    operation_type=OperationType.DEPOSIT,
                    currency=self.ngn,
                    description="Customer wallet deposit",
                )
                JournalLine.objects.create(
                    entry=entry,
                    account=usd_account,
                    line_number=1,
                    direction=EntryDirection.DEBIT,
                    amount=10000,
                    currency=self.ngn,
                )
        self.assertFalse(
            JournalEntry.objects.filter(reference="DEPOSIT-009").exists()
        )

    def test_cannot_add_line_to_posted_journal_entry(self):
        with transaction.atomic():
            entry = JournalEntry.objects.create(
                reference="DEPOSIT-010",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Customer wallet deposit",
            )
            JournalLine.objects.create(
                entry=entry,
                account=self.bank_account,
                line_number=1,
                direction=EntryDirection.DEBIT,
                amount=10000,
                currency=self.ngn,
            )
            JournalLine.objects.create(
                entry=entry,
                account=self.wallet_account,
                line_number=2,
                direction=EntryDirection.CREDIT,
                amount=10000,
                currency=self.ngn,
            )
        with self.assertRaises(IntegrityError) as context:
            with transaction.atomic():
                JournalLine.objects.create(
                    entry=entry,
                    account=self.bank_account,
                    line_number=3,
                    direction=EntryDirection.CREDIT,
                    amount=5000,
                    currency=self.ngn,
                )

        self.assertIn(
            "Cannot add lines to posted journal entry",
            str(context.exception)
        )
        self.assertEqual(JournalLine.objects.filter(entry=entry).count(), 2)
        
class JournalPostingServiceTests(TransactionTestCase):

    def setUp(self):
        self.ngn = Currency.objects.create(
            currency_code="NGN",
            currency_name="Nigerian Naira",
            minor_unit_exponent=2,
        )

        self.usd = Currency.objects.create(
            currency_code="USD",
            currency_name="US Dollar",
            minor_unit_exponent=2,
        )

        self.bank_account = LedgerAccount.objects.create(
            account_code="BANK-TEST",
            account_name="Test Bank",
            account_type=AccountType.ASSET,
            currency=self.ngn,
        )

        self.wallet_account = LedgerAccount.objects.create(
            account_code="WALLET-TEST",
            account_name="Test Wallet",
            account_type=AccountType.LIABILITY,
            currency=self.ngn,
        )

        self.usd_account = LedgerAccount.objects.create(
            account_code="USD-TEST",
            account_name="USD Account",
            account_type=AccountType.ASSET,
            currency=self.usd,
        )

    def get_valid_lines(self):
        return [
            {
                "account": self.bank_account,
                "direction": EntryDirection.DEBIT,
                "amount": 10000,
            },
            {
                "account": self.wallet_account,
                "direction": EntryDirection.CREDIT,
                "amount": 10000,
            },
        ]

    def create_entry(self, lines=None):
        return create_journal_entry(
            reference="TEST-JOURNAL-001",
            operation_type=OperationType.DEPOSIT,
            currency=self.ngn,
            description="Test journal entry",
            lines=lines if lines is not None else self.get_valid_lines(),
        )

    def test_balanced_journal_entry_posts_successfully(self):
        entry = self.create_entry()

        entry.refresh_from_db()

        self.assertIsNotNone(entry.posted_at)
        self.assertEqual(entry.lines.count(), 2)

    def test_unbalanced_entry_is_rejected(self):
        lines = self.get_valid_lines()
        lines[1]["amount"] = 5000

        with self.assertRaises(JournalPostingError):
            self.create_entry(lines)

        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_entry_with_fewer_than_two_lines_is_rejected(self):
        lines = self.get_valid_lines()[:1]

        with self.assertRaises(JournalPostingError):
            self.create_entry(lines)

        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_currency_mismatch_is_rejected(self):
        lines = self.get_valid_lines()
        lines[0]["account"] = self.usd_account

        with self.assertRaises(JournalPostingError):
            self.create_entry(lines)

        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_invalid_direction_is_rejected(self):
        lines = self.get_valid_lines()
        lines[0]["direction"] = "INVALID"

        with self.assertRaises(JournalPostingError):
            self.create_entry(lines)

        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_non_positive_or_non_integer_amount_is_rejected(self):
        for amount in [0, -100, 1.5, True]:
            with self.subTest(amount=amount):
                lines = self.get_valid_lines()
                lines[0]["amount"] = amount

                with self.assertRaises(JournalPostingError):
                    self.create_entry(lines)

        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_inactive_account_is_rejected(self):
        self.bank_account.is_active = False
        self.bank_account.save(update_fields=["is_active"])

        with self.assertRaises(JournalPostingError):
            self.create_entry()

        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_failure_during_line_creation_rolls_back_entry(self):
        original_create = JournalLine.objects.create
        calls = 0

        def create_then_fail(*args, **kwargs):
            nonlocal calls
            calls += 1

            if calls == 1:
                return original_create(*args, **kwargs)

            raise RuntimeError("Simulated journal line failure")

        with patch(
            "apps.accounting.services.JournalLine.objects.create",
            side_effect=create_then_fail,
        ):
            with self.assertRaises(RuntimeError):
                self.create_entry()

        self.assertEqual(JournalEntry.objects.count(), 0)
        self.assertEqual(JournalLine.objects.count(), 0)