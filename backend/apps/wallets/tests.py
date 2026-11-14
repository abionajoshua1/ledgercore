from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.accounting.models import Currency, LedgerAccount, AccountType, JournalEntry, JournalLine, OperationType, EntryDirection
from apps.wallets.models import Wallet

from apps.wallets.services import close_wallet, create_wallet as create_wallet_service, WalletCreationError, get_wallet_balance, reactivate_wallet
from unittest.mock import patch

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import TransactionTestCase

from apps.wallets.services import (
    deactivate_wallet,
    WalletLifecycleError,
)

User = get_user_model()


class WalletDatabaseTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="joshua",
            password="test-password",
        )

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

        self.liability_account = LedgerAccount.objects.create(
            account_code="WALLET-001",
            account_name="Joshua NGN Wallet",
            account_type=AccountType.LIABILITY,
            currency=self.ngn,
        )

    def create_wallet(self, currency=None, ledger_account=None, status=None):
        return Wallet.objects.create(
            user=self.user,
            currency=currency or self.ngn,
            ledger_account=ledger_account or self.liability_account,
            **({"status": status} if status else {}),
        )

    def test_wallet_with_matching_currency_and_liability_account_is_created(self):
        wallet = self.create_wallet()

        self.assertEqual(wallet.currency, self.ngn)
        self.assertEqual(wallet.ledger_account, self.liability_account)
        self.assertEqual(wallet.status, Wallet.Status.ACTIVE)

    def test_wallet_rejects_ledger_account_with_different_currency(self):
        usd_account = LedgerAccount.objects.create(
            account_code="WALLET-USD-001",
            account_name="Joshua USD Wallet",
            account_type=AccountType.LIABILITY,
            currency=self.usd,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_wallet(
                    currency=self.ngn,
                    ledger_account=usd_account,
                )

        self.assertEqual(Wallet.objects.count(), 0)

    def test_wallet_rejects_non_liability_ledger_account(self):
        asset_account = LedgerAccount.objects.create(
            account_code="BANK-001",
            account_name="Settlement Bank",
            account_type=AccountType.ASSET,
            currency=self.ngn,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_wallet(ledger_account=asset_account)

        self.assertEqual(Wallet.objects.count(), 0)

    def test_linked_ledger_account_cannot_change_from_liability(self):
        self.create_wallet()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerAccount.objects.filter(
                    id=self.liability_account.id
                ).update(account_type=AccountType.ASSET)

        self.liability_account = LedgerAccount.objects.get(
            id=self.liability_account.id
        )

        self.assertEqual(
            self.liability_account.account_type,
            AccountType.LIABILITY,
        )

    def test_duplicate_non_closed_wallet_is_rejected(self):
        self.create_wallet()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_wallet()

        self.assertEqual(Wallet.objects.count(), 1)

    def test_inactive_wallet_still_prevents_duplicate(self):
        self.create_wallet(status=Wallet.Status.INACTIVE)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_wallet()

        self.assertEqual(Wallet.objects.count(), 1)

    def test_closed_wallet_allows_new_wallet_in_same_currency(self):
        self.create_wallet(status=Wallet.Status.CLOSED)

        new_account = LedgerAccount.objects.create(
            account_code="WALLET-002",
            account_name="Joshua New NGN Wallet",
            account_type=AccountType.LIABILITY,
            currency=self.ngn,
        )

        new_wallet = self.create_wallet(ledger_account=new_account)

        self.assertEqual(Wallet.objects.count(), 2)
        self.assertEqual(new_wallet.status, Wallet.Status.ACTIVE)
        
    def test_create_wallet_service_creates_wallet_and_liability_account(self):
        wallet = create_wallet_service(
            user=self.user,
            currency=self.ngn,
        )

        self.assertEqual(wallet.user, self.user)
        self.assertEqual(wallet.currency, self.ngn)
        self.assertEqual(wallet.status, Wallet.Status.ACTIVE)

        self.assertEqual(
            wallet.ledger_account.account_type,
            AccountType.LIABILITY,
        )

        self.assertEqual(
            wallet.ledger_account.currency,
            self.ngn,
        )
        
    def test_create_wallet_rejects_disabled_currency(self):
        self.usd.enabled = False
        self.usd.save(update_fields=["enabled"])

        wallet_count_before = Wallet.objects.count()
        account_count_before = LedgerAccount.objects.count()

        with self.assertRaises(WalletCreationError):
            create_wallet_service(
                user=self.user,
                currency=self.usd,
            )

        self.assertEqual(Wallet.objects.count(), wallet_count_before)
        self.assertEqual(LedgerAccount.objects.count(), account_count_before)


    def test_create_wallet_rejects_duplicate_and_creates_no_orphan_account(self):
        create_wallet_service(
            user=self.user,
            currency=self.ngn,
        )

        account_count_before = LedgerAccount.objects.count()

        with self.assertRaises(WalletCreationError):
            create_wallet_service(
                user=self.user,
                currency=self.ngn,
            )

        self.assertEqual(Wallet.objects.count(), 1)
        self.assertEqual(LedgerAccount.objects.count(), account_count_before)
        
    def test_ledger_account_is_rolled_back_when_wallet_creation_fails(self):
        account_count_before = LedgerAccount.objects.count()
        wallet_count_before = Wallet.objects.count()

        with patch(
            "apps.wallets.services.Wallet.objects.create",
            side_effect=IntegrityError("Simulated wallet creation failure"),
        ):
            with self.assertRaises(IntegrityError):
                create_wallet_service(
                    user=self.user,
                    currency=self.ngn,
                )

        self.assertEqual(
            LedgerAccount.objects.count(),
            account_count_before,
        )

        self.assertEqual(
            Wallet.objects.count(),
            wallet_count_before,
        )
        
    def test_new_wallet_has_zero_balance(self):
        wallet = create_wallet_service(
            user=self.user,
            currency=self.ngn,
        )

        balance = get_wallet_balance(wallet=wallet)

        self.assertEqual(balance, 0)
 
class WalletCreationConcurrencyTests(TransactionTestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="concurrent_user",
            password="test-password",
        )

        self.currency = Currency.objects.create(
            currency_code="NGN",
            currency_name="Nigerian Naira",
            minor_unit_exponent=2,
        )

    def test_concurrent_requests_cannot_create_duplicate_wallets(self):
        barrier = Barrier(2)

        def create_wallet_concurrently():
            close_old_connections()

            try:
                user = User.objects.get(pk=self.user.pk)
                currency = Currency.objects.get(pk=self.currency.pk)

                # Make both threads start wallet creation together.
                barrier.wait(timeout=10)

                try:
                    create_wallet_service(
                        user=user,
                        currency=currency,
                    )
                    return "created"

                except WalletCreationError:
                    return "duplicate"

            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(create_wallet_concurrently)
                for _ in range(2)
            ]

            results = [future.result(timeout=30) for future in futures]

        self.assertCountEqual(
            results,
            ["created", "duplicate"],
        )

        self.assertEqual(
            Wallet.objects.filter(
                user=self.user,
                currency=self.currency,
            ).count(),
            1,
        )

        self.assertEqual(LedgerAccount.objects.count(), 1)
        
class WalletBalanceTests(TransactionTestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="balance_user",
            password="test-password",
        )

        self.ngn = Currency.objects.create(
            currency_code="NGN",
            currency_name="Nigerian Naira",
            minor_unit_exponent=2,
        )

        self.wallet = create_wallet_service(
            user=self.user,
            currency=self.ngn,
        )

    def test_wallet_balance_reflects_credits_and_debits(self):
        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-NGN-001",
            account_name="NGN Settlement Account",
            account_type=AccountType.ASSET,
            currency=self.ngn,
        )

        with transaction.atomic():
            deposit = JournalEntry.objects.create(
                reference="DEP-TEST-001",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Test deposit",
                initiated_by=self.user,
            )

            JournalLine.objects.create(
                entry=deposit,
                account=self.wallet.ledger_account,
                line_number=1,
                direction=EntryDirection.CREDIT,
                amount=10000,
                currency=self.ngn,
            )

            JournalLine.objects.create(
                entry=deposit,
                account=settlement_account,
                line_number=2,
                direction=EntryDirection.DEBIT,
                amount=10000,
                currency=self.ngn,
            )

            withdrawal = JournalEntry.objects.create(
                reference="WD-TEST-001",
                operation_type=OperationType.WITHDRAWAL,
                currency=self.ngn,
                description="Test withdrawal",
                initiated_by=self.user,
            )

            JournalLine.objects.create(
                entry=withdrawal,
                account=self.wallet.ledger_account,
                line_number=1,
                direction=EntryDirection.DEBIT,
                amount=3000,
                currency=self.ngn,
            )

            JournalLine.objects.create(
                entry=withdrawal,
                account=settlement_account,
                line_number=2,
                direction=EntryDirection.CREDIT,
                amount=3000,
                currency=self.ngn,
            )

        balance = get_wallet_balance(wallet=self.wallet)

        self.assertEqual(balance, 7000)
        
    def test_wallet_balance_excludes_unposted_journal_entries(self):
        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-NGN-002",
            account_name="NGN Settlement Account",
            account_type=AccountType.ASSET,
            currency=self.ngn,
        )

        with transaction.atomic():
            deposit = JournalEntry.objects.create(
                reference="DEP-TEST-002",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Test deposit",
                initiated_by=self.user,
            )

            JournalLine.objects.create(
                entry=deposit,
                account=self.wallet.ledger_account,
                line_number=1,
                direction=EntryDirection.CREDIT,
                amount=5000,
                currency=self.ngn,
            )

            JournalLine.objects.create(
                entry=deposit,
                account=settlement_account,
                line_number=2,
                direction=EntryDirection.DEBIT,
                amount=5000,
                currency=self.ngn,
            )

            self.assertIsNone(deposit.posted_at)
            self.assertEqual(
                get_wallet_balance(wallet=self.wallet),
                0,
            )

        deposit.refresh_from_db()

        self.assertIsNotNone(deposit.posted_at)
        self.assertEqual(
            get_wallet_balance(wallet=self.wallet),
            5000,
        )
        
    def test_wallet_balance_is_isolated_to_its_ledger_account(self):
        second_user = User.objects.create_user(
            username="second_balance_user",
            password="test-password",
        )

        second_wallet = create_wallet_service(
            user=second_user,
            currency=self.ngn,
        )

        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-NGN-003",
            account_name="NGN Settlement Account",
            account_type=AccountType.ASSET,
            currency=self.ngn,
        )

        with transaction.atomic():
            first_deposit = JournalEntry.objects.create(
                reference="DEP-ISOLATION-001",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="First customer deposit",
                initiated_by=self.user,
            )

            JournalLine.objects.create(
                entry=first_deposit,
                account=self.wallet.ledger_account,
                line_number=1,
                direction=EntryDirection.CREDIT,
                amount=5000,
                currency=self.ngn,
            )

            JournalLine.objects.create(
                entry=first_deposit,
                account=settlement_account,
                line_number=2,
                direction=EntryDirection.DEBIT,
                amount=5000,
                currency=self.ngn,
            )

            second_deposit = JournalEntry.objects.create(
                reference="DEP-ISOLATION-002",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Second customer deposit",
                initiated_by=second_user,
            )

            JournalLine.objects.create(
                entry=second_deposit,
                account=second_wallet.ledger_account,
                line_number=1,
                direction=EntryDirection.CREDIT,
                amount=9000,
                currency=self.ngn,
            )

            JournalLine.objects.create(
                entry=second_deposit,
                account=settlement_account,
                line_number=2,
                direction=EntryDirection.DEBIT,
                amount=9000,
                currency=self.ngn,
            )

        self.assertEqual(
            get_wallet_balance(wallet=self.wallet),
            5000,
        )

        self.assertEqual(
            get_wallet_balance(wallet=second_wallet),
            9000,
        )
        
    def test_inactive_and_closed_wallets_retain_their_balance(self):
        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-NGN-004",
            account_name="NGN Settlement Account",
            account_type=AccountType.ASSET,
            currency=self.ngn,
        )

        with transaction.atomic():
            deposit = JournalEntry.objects.create(
                reference="DEP-LIFECYCLE-001",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Wallet lifecycle test deposit",
                initiated_by=self.user,
            )

            JournalLine.objects.create(
                entry=deposit,
                account=self.wallet.ledger_account,
                line_number=1,
                direction=EntryDirection.CREDIT,
                amount=12000,
                currency=self.ngn,
            )

            JournalLine.objects.create(
                entry=deposit,
                account=settlement_account,
                line_number=2,
                direction=EntryDirection.DEBIT,
                amount=12000,
                currency=self.ngn,
            )

        self.wallet.status = Wallet.Status.INACTIVE
        self.wallet.save(update_fields=["status"])

        self.assertEqual(
            get_wallet_balance(wallet=self.wallet),
            12000,
        )

        self.wallet.status = Wallet.Status.CLOSED
        self.wallet.save(update_fields=["status"])

        self.assertEqual(
            get_wallet_balance(wallet=self.wallet),
            12000,
        )
        
class WalletLifecycleTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lifecycle_user",
            password="test-password",
        )

        self.ngn = Currency.objects.create(
            currency_code="NGN",
            currency_name="Nigerian Naira",
            minor_unit_exponent=2,
        )

        self.wallet = create_wallet_service(
            user=self.user,
            currency=self.ngn,
        )
    def test_active_wallet_can_be_deactivated(self):
        deactivate_wallet(wallet=self.wallet)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.status,
            Wallet.Status.INACTIVE,
        )
        
    def test_already_inactive_wallet_cannot_be_deactivated(self):
        self.wallet.status = Wallet.Status.INACTIVE
        self.wallet.save(update_fields=["status"])

        with self.assertRaises(WalletLifecycleError):
            deactivate_wallet(wallet=self.wallet)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.status,
            Wallet.Status.INACTIVE,
        )
        
    def test_closed_wallet_cannot_be_deactivated(self):
        self.wallet.status = Wallet.Status.CLOSED
        self.wallet.save(update_fields=["status"])

        with self.assertRaises(WalletLifecycleError):
            deactivate_wallet(wallet=self.wallet)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.status,
            Wallet.Status.CLOSED,
        )
        
    def test_funded_wallet_can_be_deactivated(self):
        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-NGN-005",
            account_name="NGN Settlement Account",
            account_type=AccountType.ASSET,
            currency=self.ngn,
        )

        with transaction.atomic():
            deposit = JournalEntry.objects.create(
                reference="DEP-LIFECYCLE-002",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Wallet lifecycle test deposit",
                initiated_by=self.user,
            )

            JournalLine.objects.create(
                entry=deposit,
                account=self.wallet.ledger_account,
                line_number=1,
                direction=EntryDirection.CREDIT,
                amount=15000,
                currency=self.ngn,
            )

            JournalLine.objects.create(
                entry=deposit,
                account=settlement_account,
                line_number=2,
                direction=EntryDirection.DEBIT,
                amount=15000,
                currency=self.ngn,
            )

        deactivate_wallet(wallet=self.wallet)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.status,
            Wallet.Status.INACTIVE,
        )

        self.assertEqual(
            get_wallet_balance(wallet=self.wallet),
            15000,
        )
        
    def test_inactive_wallet_can_be_reactivated(self):
        self.wallet.status = Wallet.Status.INACTIVE
        self.wallet.save(update_fields=["status"])

        reactivate_wallet(wallet=self.wallet)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.status,
            Wallet.Status.ACTIVE,
        )

    def test_active_wallet_cannot_be_reactivated(self):
        with self.assertRaises(WalletLifecycleError):
            reactivate_wallet(wallet=self.wallet)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.status,
            Wallet.Status.ACTIVE,
        )

    def test_closed_wallet_cannot_be_reactivated(self):
        self.wallet.status = Wallet.Status.CLOSED
        self.wallet.save(update_fields=["status"])

        with self.assertRaises(WalletLifecycleError):
            reactivate_wallet(wallet=self.wallet)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.status,
            Wallet.Status.CLOSED,
        )
        
    def test_active_wallet_can_be_closed(self):
        close_wallet(wallet=self.wallet)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.status,
            Wallet.Status.CLOSED,
        )
        
    def test_inactive_wallet_can_be_closed(self):
        self.wallet.status = Wallet.Status.INACTIVE
        self.wallet.save(update_fields=["status"])

        close_wallet(wallet=self.wallet)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.status,
            Wallet.Status.CLOSED,
        )
        
    def test_wallet_with_non_zero_balance_cannot_be_closed(self):
        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-NGN-006",
            account_name="NGN Settlement Account",
            account_type=AccountType.ASSET,
            currency=self.ngn,
        )

        with transaction.atomic():
            deposit = JournalEntry.objects.create(
                reference="DEP-LIFECYCLE-003",
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description="Wallet lifecycle test deposit",
                initiated_by=self.user,
            )

            JournalLine.objects.create(
                entry=deposit,
                account=self.wallet.ledger_account,
                line_number=1,
                direction=EntryDirection.CREDIT,
                amount=20000,
                currency=self.ngn,
            )

            JournalLine.objects.create(
                entry=deposit,
                account=settlement_account,
                line_number=2,
                direction=EntryDirection.DEBIT,
                amount=20000,
                currency=self.ngn,
            )

        with self.assertRaises(WalletLifecycleError):
            close_wallet(wallet=self.wallet)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.status,
            Wallet.Status.ACTIVE,
        )

        self.assertEqual(
            get_wallet_balance(wallet=self.wallet),
            20000,
        )
        
    def test_closed_wallet_cannot_be_closed_again(self):
        self.wallet.status = Wallet.Status.CLOSED
        self.wallet.save(update_fields=["status"])

        with self.assertRaises(WalletLifecycleError):
            close_wallet(wallet=self.wallet)

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.status,
            Wallet.Status.CLOSED,
        )
        
def test_closing_wallet_preserves_historical_ledger_records(self):
    settlement_account = LedgerAccount.objects.create(
        account_code="SETTLEMENT-NGN-007",
        account_name="NGN Settlement Account",
        account_type=AccountType.ASSET,
        currency=self.ngn,
    )

    with transaction.atomic():
        deposit = JournalEntry.objects.create(
            reference="DEP-LIFECYCLE-004",
            operation_type=OperationType.DEPOSIT,
            currency=self.ngn,
            description="Wallet closure history test deposit",
            initiated_by=self.user,
        )

        JournalLine.objects.create(
            entry=deposit,
            account=self.wallet.ledger_account,
            line_number=1,
            direction=EntryDirection.CREDIT,
            amount=15000,
            currency=self.ngn,
        )

        JournalLine.objects.create(
            entry=deposit,
            account=settlement_account,
            line_number=2,
            direction=EntryDirection.DEBIT,
            amount=15000,
            currency=self.ngn,
        )

        withdrawal = JournalEntry.objects.create(
            reference="WDR-LIFECYCLE-004",
            operation_type=OperationType.WITHDRAWAL,
            currency=self.ngn,
            description="Wallet closure history test withdrawal",
            initiated_by=self.user,
        )

        JournalLine.objects.create(
            entry=withdrawal,
            account=self.wallet.ledger_account,
            line_number=1,
            direction=EntryDirection.DEBIT,
            amount=15000,
            currency=self.ngn,
        )

        JournalLine.objects.create(
            entry=withdrawal,
            account=settlement_account,
            line_number=2,
            direction=EntryDirection.CREDIT,
            amount=15000,
            currency=self.ngn,
        )

    self.assertEqual(get_wallet_balance(wallet=self.wallet), 0)

    close_wallet(wallet=self.wallet)

    self.wallet.refresh_from_db()

    self.assertEqual(self.wallet.status, Wallet.Status.CLOSED)
    self.assertTrue(JournalEntry.objects.filter(pk=deposit.pk).exists())
    self.assertTrue(JournalEntry.objects.filter(pk=withdrawal.pk).exists())
    self.assertEqual(JournalLine.objects.filter(entry=deposit).count(), 2)
    self.assertEqual(JournalLine.objects.filter(entry=withdrawal).count(), 2)
    self.assertEqual(get_wallet_balance(wallet=self.wallet), 0)