from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.accounting.models import Currency, LedgerAccount, AccountType
from apps.wallets.models import Wallet

from apps.wallets.services import create_wallet as create_wallet_service, WalletCreationError
from unittest.mock import patch

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import TransactionTestCase

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

        self.liability_account.refresh_from_db()

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