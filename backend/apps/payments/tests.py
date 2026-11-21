from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from apps.accounting.models import Currency, OperationType, JournalEntry, JournalLine
from apps.payments.models import Transaction
from apps.payments.references import generate_transaction_reference
from apps.payments.services import create_transaction

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from apps.accounting.models import (
    AccountType,
    Currency,
    EntryDirection,
    LedgerAccount,
    OperationType,
)
from apps.accounting.services import create_journal_entry
from apps.idempotency.services import IdempotencyKeyReuseError
from apps.payments.transfer_services import (
    InvalidTransferError,
    execute_transfer,
    lock_and_validate_wallets,
    TransferLimitExceededError,
    TransferLimitNotConfiguredError,
    validate_transfer_limits,
    InsufficientFundsError,
    validate_transfer_funds,
)
from apps.wallets.services import create_wallet, configure_wallet_transfer_limits, get_wallet_balance

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from apps.idempotency.models import IdempotencyKey

class TransactionReferenceTests(SimpleTestCase):

    def test_reference_has_expected_prefix(self):
        reference = generate_transaction_reference()

        self.assertTrue(reference.startswith("TXN-"))

    def test_reference_has_expected_length(self):
        reference = generate_transaction_reference()

        self.assertEqual(len(reference), 12)

    def test_reference_suffix_contains_only_uppercase_letters_and_digits(self):
        reference = generate_transaction_reference()
        suffix = reference.removeprefix("TXN-")

        self.assertTrue(suffix.isalnum())
        self.assertEqual(suffix, suffix.upper())

    def test_reference_generation_produces_different_candidates(self):
        references = {
            generate_transaction_reference()
            for _ in range(10)
        }

        self.assertEqual(len(references), 10)
        
class CreateTransactionTests(TestCase):

    @patch("apps.payments.services.Transaction.objects.create")
    @patch("apps.payments.services.generate_transaction_reference")
    def test_retries_when_reference_collides(
        self,
        mock_generate_reference,
        mock_create,
    ):
        mock_generate_reference.side_effect = [
            "TXN-FIRST123",
            "TXN-SECOND12",
        ]

        collision = IntegrityError("Duplicate reference")
        collision.__cause__ = Exception()
        collision.__cause__.diag = type(
            "Diagnostic",
            (),
            {"constraint_name": "payments_transaction_reference_key"},
        )()

        mock_create.side_effect = [
            collision,
            "created_transaction",
        ]

        result = create_transaction(amount=100)

        self.assertEqual(result, "created_transaction")
        self.assertEqual(mock_create.call_count, 2)
        self.assertEqual(mock_generate_reference.call_count, 2)

    @patch("apps.payments.services.Transaction.objects.create")
    @patch("apps.payments.services.generate_transaction_reference")
    def test_does_not_retry_unrelated_integrity_error(
        self,
        mock_generate_reference,
        mock_create,
    ):
        mock_generate_reference.return_value = "TXN-FIRST123"
        mock_create.side_effect = IntegrityError("Unrelated constraint")

        with self.assertRaises(IntegrityError):
            create_transaction(amount=100)

        self.assertEqual(mock_create.call_count, 1)
        self.assertEqual(mock_generate_reference.call_count, 1)

    def test_retries_after_real_database_reference_collision(self):
        currency = Currency.objects.create(
            currency_code="USD",
            currency_name="US Dollar",
            minor_unit_exponent=2,
        )

        with patch(
            "apps.payments.services.generate_transaction_reference",
            side_effect=[
                "TXN-DUPLICATE1",
                "TXN-DUPLICATE1",
                "TXN-UNIQUE001",
            ],
        ) as mock_generate:

            first_transaction = create_transaction(
                type=OperationType.DEPOSIT,
                amount=100,
                currency=currency,
            )

            second_transaction = create_transaction(
                type=OperationType.DEPOSIT,
                amount=200,
                currency=currency,
            )

        self.assertEqual(
            first_transaction.reference,
            "TXN-DUPLICATE1",
        )

        self.assertEqual(
            second_transaction.reference,
            "TXN-UNIQUE001",
        )

        self.assertEqual(mock_generate.call_count, 3)

        self.assertEqual(
            Transaction.objects.filter(
                reference="TXN-DUPLICATE1"
            ).count(),
            1,
        )
        

class TransferCurrencyValidationTests(TransactionTestCase):

    def setUp(self):
        User = get_user_model()

        self.sender = User.objects.create_user(
            username="transfer_sender",
            password="test-password",
        )

        self.recipient = User.objects.create_user(
            username="transfer_recipient",
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

        self.source_wallet = create_wallet(
            user=self.sender,
            currency=self.ngn,
        )

        self.destination_wallet = create_wallet(
            user=self.recipient,
            currency=self.ngn,
        )
        
        configure_wallet_transfer_limits(
            wallet=self.source_wallet,
            max_transaction_amount=50000,
            daily_amount_limit=200000,
            daily_transaction_limit=10,
        )

    def test_accepts_matching_request_currency(self):
        with transaction.atomic():
            source, destination = lock_and_validate_wallets(
                user=self.sender,
                source_wallet_id=self.source_wallet.id,
                destination_wallet_id=self.destination_wallet.id,
                currency_code="NGN",
            )

        self.assertEqual(source, self.source_wallet)
        self.assertEqual(destination, self.destination_wallet)

    def test_rejects_request_currency_that_does_not_match_wallets(self):
        with self.assertRaises(InvalidTransferError):
            with transaction.atomic():
                lock_and_validate_wallets(
                    user=self.sender,
                    source_wallet_id=self.source_wallet.id,
                    destination_wallet_id=self.destination_wallet.id,
                    currency_code="USD",
                )

    def test_rejects_invalid_currency_code(self):
        with self.assertRaises(InvalidTransferError):
            execute_transfer(
                user=self.sender,
                source_wallet_id=self.source_wallet.id,
                destination_wallet_id=self.destination_wallet.id,
                amount=100,
                currency="NG",
                idempotency_key="invalid-currency-key",
                path="/v1/transfers",
            )

    def test_rejects_idempotency_key_reused_with_different_currency(self):
        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-NGN",
            account_name="NGN Settlement Account",
            account_type=AccountType.ASSET,
            currency=self.ngn,
            is_system=True,
        )

        deposit = create_transaction(
            type=OperationType.DEPOSIT,
            status="COMPLETED",
            amount=15000,
            currency=self.ngn,
            destination_wallet=self.source_wallet,
        )

        create_journal_entry(
            reference=deposit.reference,
            operation_type=OperationType.DEPOSIT,
            currency=self.ngn,
            description="Test wallet funding",
            transaction_record=deposit,
            lines=[
                {
                    "account": settlement_account,
                    "direction": EntryDirection.DEBIT,
                    "amount": 15000,
                },
                {
                    "account": self.source_wallet.ledger_account,
                    "direction": EntryDirection.CREDIT,
                    "amount": 15000,
                },
            ],
        )

        status, response, replayed = execute_transfer(
            user=self.sender,
            source_wallet_id=self.source_wallet.id,
            destination_wallet_id=self.destination_wallet.id,
            amount=5000,
            currency="ngn",
            idempotency_key="currency-reuse-key",
            path="/v1/transfers",
        )

        self.assertEqual(status, 201)
        self.assertFalse(replayed)
        self.assertEqual(response["currency"], "NGN")

        with self.assertRaises(IdempotencyKeyReuseError):
            execute_transfer(
                user=self.sender,
                source_wallet_id=self.source_wallet.id,
                destination_wallet_id=self.destination_wallet.id,
                amount=5000,
                currency="USD",
                idempotency_key="currency-reuse-key",
                path="/v1/transfers",
            )
            
    def test_rejects_transfer_above_maximum_transaction_amount(self):
        with self.assertRaises(TransferLimitExceededError):
            validate_transfer_limits(
                source_wallet=self.source_wallet,
                amount=50001,
            )
            
    def test_rejects_transfer_exceeding_daily_amount_limit(self):
        previous_transfer = create_transaction(
            type=OperationType.TRANSFER,
            status="COMPLETED",
            amount=180000,
            currency=self.ngn,
            source_wallet=self.source_wallet,
            destination_wallet=self.destination_wallet,
        )

        with self.assertRaises(TransferLimitExceededError):
            validate_transfer_limits(
                source_wallet=self.source_wallet,
                amount=25000,
            )
            
    def test_rejects_transfer_exceeding_daily_transaction_limit(self):
        for _ in range(10):
            create_transaction(
                type=OperationType.TRANSFER,
                status="COMPLETED",
                amount=1000,
                currency=self.ngn,
                source_wallet=self.source_wallet,
                destination_wallet=self.destination_wallet,
            )

        with self.assertRaises(TransferLimitExceededError):
            validate_transfer_limits(
                source_wallet=self.source_wallet,
                amount=1000,
            )
            
    def test_rejects_transfer_when_limits_are_not_configured(self):
        self.source_wallet.transfer_limits.delete()

        with self.assertRaises(TransferLimitNotConfiguredError):
            validate_transfer_limits(
                source_wallet=self.source_wallet,
                amount=1000,
            )
            
    def test_execute_transfer_rejects_amount_above_configured_limit(self):
        configure_wallet_transfer_limits(
            wallet=self.source_wallet,
            max_transaction_amount=4000,
            daily_amount_limit=200000,
            daily_transaction_limit=10,
        )

        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-TEST",
            account_name="Test Settlement Account",
            account_type=AccountType.ASSET,
            currency=self.ngn,
            is_system=True,
        )

        deposit = create_transaction(
            type=OperationType.DEPOSIT,
            status="COMPLETED",
            amount=15000,
            currency=self.ngn,
            destination_wallet=self.source_wallet,
        )

        create_journal_entry(
            reference=deposit.reference,
            operation_type=OperationType.DEPOSIT,
            currency=self.ngn,
            description="Test wallet funding",
            transaction_record=deposit,
            lines=[
                {
                    "account": settlement_account,
                    "direction": EntryDirection.DEBIT,
                    "amount": 15000,
                },
                {
                    "account": self.source_wallet.ledger_account,
                    "direction": EntryDirection.CREDIT,
                    "amount": 15000,
                },
            ],
        )

        with self.assertRaises(TransferLimitExceededError):
            execute_transfer(
                user=self.sender,
                source_wallet_id=self.source_wallet.id,
                destination_wallet_id=self.destination_wallet.id,
                amount=5000,
                currency="NGN",
                idempotency_key="limit-exceeded-test",
                path="/v1/transfers",
            )

        self.assertEqual(
            Transaction.objects.filter(
                type=OperationType.TRANSFER,
            ).count(),
            0,
        )
        
    def test_concurrent_transfers_cannot_double_spend_wallet_balance(self):
        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-CONCURRENT",
            account_name="Concurrent Test Settlement Account",
            account_type=AccountType.ASSET,
            currency=self.ngn,
            is_system=True,
        )

        deposit = create_transaction(
            type=OperationType.DEPOSIT,
            status="COMPLETED",
            amount=10000,
            currency=self.ngn,
            destination_wallet=self.source_wallet,
        )

        create_journal_entry(
            reference=deposit.reference,
            operation_type=OperationType.DEPOSIT,
            currency=self.ngn,
            description="Concurrent transfer test funding",
            transaction_record=deposit,
            lines=[
                {
                    "account": settlement_account,
                    "direction": EntryDirection.DEBIT,
                    "amount": 10000,
                },
                {
                    "account": self.source_wallet.ledger_account,
                    "direction": EntryDirection.CREDIT,
                    "amount": 10000,
                },
            ],
        )

        barrier = Barrier(2)

        sender_id = self.sender.pk
        source_wallet_id = self.source_wallet.pk
        destination_wallet_id = self.destination_wallet.pk

        def attempt_transfer(idempotency_key):
            close_old_connections()

            try:
                user = get_user_model().objects.get(pk=sender_id)

                barrier.wait(timeout=10)

                try:
                    status, response, replayed = execute_transfer(
                        user=user,
                        source_wallet_id=source_wallet_id,
                        destination_wallet_id=destination_wallet_id,
                        amount=7000,
                        currency="NGN",
                        idempotency_key=idempotency_key,
                        path="/v1/transfers",
                    )

                    if status == 201 and not replayed:
                        return "success"

                    return "unexpected"

                except InsufficientFundsError:
                    return "insufficient_funds"

            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                attempt_transfer,
                "concurrent-transfer-a",
            )
            second = executor.submit(
                attempt_transfer,
                "concurrent-transfer-b",
            )

            results = [
                first.result(timeout=30),
                second.result(timeout=30),
            ]

        self.assertCountEqual(
            results,
            ["success", "insufficient_funds"],
        )

        self.assertEqual(
            Transaction.objects.filter(
                type=OperationType.TRANSFER,
                status="COMPLETED",
            ).count(),
            1,
        )

        self.assertEqual(
            get_wallet_balance(wallet=self.source_wallet),
            3000,
        )

        self.assertEqual(
            get_wallet_balance(wallet=self.destination_wallet),
            7000,
        )
        
    def test_opposing_concurrent_transfers_complete_without_deadlock(self):
        configure_wallet_transfer_limits(
            wallet=self.destination_wallet,
            max_transaction_amount=50000,
            daily_amount_limit=200000,
            daily_transaction_limit=10,
        )

        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-OPPOSING",
            account_name="Opposing Transfer Settlement",
            account_type=AccountType.ASSET,
            currency=self.ngn,
            is_system=True,
        )

        for wallet, reference_suffix in [
            (self.source_wallet, "A"),
            (self.destination_wallet, "B"),
        ]:
            deposit = create_transaction(
                type=OperationType.DEPOSIT,
                status="COMPLETED",
                amount=10000,
                currency=self.ngn,
                destination_wallet=wallet,
            )

            create_journal_entry(
                reference=deposit.reference,
                operation_type=OperationType.DEPOSIT,
                currency=self.ngn,
                description=f"Opposing transfer funding {reference_suffix}",
                transaction_record=deposit,
                lines=[
                    {
                        "account": settlement_account,
                        "direction": EntryDirection.DEBIT,
                        "amount": 10000,
                    },
                    {
                        "account": wallet.ledger_account,
                        "direction": EntryDirection.CREDIT,
                        "amount": 10000,
                    },
                ],
            )

        barrier = Barrier(2)

        def attempt_transfer(
            user_id,
            source_wallet_id,
            destination_wallet_id,
            idempotency_key,
        ):
            close_old_connections()

            try:
                user = get_user_model().objects.get(pk=user_id)

                barrier.wait(timeout=10)

                status, response, replayed = execute_transfer(
                    user=user,
                    source_wallet_id=source_wallet_id,
                    destination_wallet_id=destination_wallet_id,
                    amount=5000,
                    currency="NGN",
                    idempotency_key=idempotency_key,
                    path="/v1/transfers",
                )

                return status, replayed

            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                attempt_transfer,
                self.sender.pk,
                self.source_wallet.pk,
                self.destination_wallet.pk,
                "opposing-transfer-a",
            )

            second = executor.submit(
                attempt_transfer,
                self.recipient.pk,
                self.destination_wallet.pk,
                self.source_wallet.pk,
                "opposing-transfer-b",
            )

            results = [
                first.result(timeout=30),
                second.result(timeout=30),
            ]

        self.assertEqual(results, [(201, False), (201, False)])

        self.source_wallet.refresh_from_db()
        self.destination_wallet.refresh_from_db()

        self.assertEqual(
            get_wallet_balance(wallet=self.source_wallet),
            10000,
        )

        self.assertEqual(
            get_wallet_balance(wallet=self.destination_wallet),
            10000,
        )

        self.assertEqual(
            Transaction.objects.filter(
                type=OperationType.TRANSFER,
                status="COMPLETED",
            ).count(),
            2,
        )
        
    @patch(
        "apps.payments.transfer_services.create_journal_entry",
        side_effect=RuntimeError("Simulated journal posting failure"),
    )
    def test_journal_posting_failure_rolls_back_transfer(self, mock_journal):
        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-NGN",
            account_name="NGN Settlement Account",
            account_type=AccountType.ASSET,
            currency=self.ngn,
            is_system=True,
        )

        deposit = create_transaction(
            type=OperationType.DEPOSIT,
            status="COMPLETED",
            amount=15000,
            currency=self.ngn,
            destination_wallet=self.source_wallet,
        )

        create_journal_entry(
            reference=deposit.reference,
            operation_type=OperationType.DEPOSIT,
            currency=self.ngn,
            description="Test wallet funding",
            transaction_record=deposit,
            lines=[
                {
                    "account": settlement_account,
                    "direction": EntryDirection.DEBIT,
                    "amount": 15000,
                },
                {
                    "account": self.source_wallet.ledger_account,
                    "direction": EntryDirection.CREDIT,
                    "amount": 15000,
                },
            ],
        )
        initial_source_balance = get_wallet_balance(wallet=self.source_wallet)
        initial_destination_balance = get_wallet_balance(wallet=self.destination_wallet)

        with self.assertRaises(RuntimeError):
            execute_transfer(
                user=self.sender,
                source_wallet_id=self.source_wallet.id,
                destination_wallet_id=self.destination_wallet.id,
                amount=5000,
                currency="NGN",
                idempotency_key="atomicity-failure-key",
                path="/v1/transfers",
            )

        self.assertTrue(mock_journal.called)

        self.assertFalse(
            Transaction.objects.filter(type=OperationType.TRANSFER).exists()
        )
        self.assertFalse(
            JournalEntry.objects.filter(
                operation_type=OperationType.TRANSFER
            ).exists()
        )
        self.assertFalse(
            IdempotencyKey.objects.filter(
                user=self.sender,
                key="atomicity-failure-key",
            ).exists()
        )

        self.assertEqual(
            get_wallet_balance(wallet=self.source_wallet),
            initial_source_balance,
        )
        self.assertEqual(
            get_wallet_balance(wallet=self.destination_wallet),
            initial_destination_balance,
        )
        
    @patch(
        "apps.payments.transfer_services.IdempotencyKey.save",
        side_effect=RuntimeError("Simulated idempotency completion failure"),
    )
    def test_idempotency_completion_failure_rolls_back_transfer(
        self,
        mock_save,
    ):
        settlement_account = LedgerAccount.objects.create(
            account_code="SETTLEMENT-NGN",
            account_name="NGN Settlement Account",
            account_type=AccountType.ASSET,
            currency=self.ngn,
            is_system=True,
        )

        deposit = create_transaction(
            type=OperationType.DEPOSIT,
            status="COMPLETED",
            amount=15000,
            currency=self.ngn,
            destination_wallet=self.source_wallet,
        )

        create_journal_entry(
            reference=deposit.reference,
            operation_type=OperationType.DEPOSIT,
            currency=self.ngn,
            description="Test wallet funding",
            transaction_record=deposit,
            lines=[
                {
                    "account": settlement_account,
                    "direction": EntryDirection.DEBIT,
                    "amount": 15000,
                },
                {
                    "account": self.source_wallet.ledger_account,
                    "direction": EntryDirection.CREDIT,
                    "amount": 15000,
                },
            ],
        )

        initial_source_balance = get_wallet_balance(wallet=self.source_wallet)
        initial_destination_balance = get_wallet_balance(wallet=self.destination_wallet)

        with self.assertRaises(RuntimeError):
            execute_transfer(
                user=self.sender,
                source_wallet_id=self.source_wallet.id,
                destination_wallet_id=self.destination_wallet.id,
                amount=5000,
                currency="NGN",
                idempotency_key="completion-failure-key",
                path="/v1/transfers",
            )

        self.assertTrue(mock_save.called)

        self.assertFalse(
            Transaction.objects.filter(type=OperationType.TRANSFER).exists()
        )
        self.assertFalse(
            JournalEntry.objects.filter(
                operation_type=OperationType.TRANSFER
            ).exists()
        )
        self.assertFalse(
            IdempotencyKey.objects.filter(
                user=self.sender,
                key="completion-failure-key",
            ).exists()
        )

        self.assertEqual(
            get_wallet_balance(wallet=self.source_wallet),
            initial_source_balance,
        )
        self.assertEqual(
            get_wallet_balance(wallet=self.destination_wallet),
            initial_destination_balance,
        )