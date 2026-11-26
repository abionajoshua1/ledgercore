
from django.test import TestCase, TransactionTestCase

from apps.accounting.models import Currency, OperationType
from apps.payments.models import Transaction, TransactionStatus
from apps.payments.services import create_transaction
from apps.payments.transaction_services import (
    ConcurrentTransactionTransition,
    InvalidTransactionTransition,
    transition_transaction,
)

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections


class TransactionStatusTransitionTests(TestCase):
    def setUp(self):
        self.currency = Currency.objects.create(
            currency_code="NGN",
            currency_name="Nigerian Naira",
            minor_unit_exponent=2,
        )

        self.transaction = create_transaction(
            type=OperationType.DEPOSIT,
            amount=5000,
            currency=self.currency,
            status=TransactionStatus.PENDING,
        )

    def test_pending_can_transition_to_processing(self):
        result = transition_transaction(
            self.transaction.id,
            TransactionStatus.PENDING,
            TransactionStatus.PROCESSING,
        )

        self.assertEqual(result.status, TransactionStatus.PROCESSING)

    def test_processing_can_transition_to_unknown(self):
        self.transaction.status = TransactionStatus.PROCESSING
        self.transaction.save(update_fields=["status"])

        result = transition_transaction(
            self.transaction.id,
            TransactionStatus.PROCESSING,
            TransactionStatus.UNKNOWN,
        )

        self.assertEqual(result.status, TransactionStatus.UNKNOWN)

    def test_unknown_can_transition_to_completed(self):
        self.transaction.status = TransactionStatus.UNKNOWN
        self.transaction.save(update_fields=["status"])

        result = transition_transaction(
            self.transaction.id,
            TransactionStatus.UNKNOWN,
            TransactionStatus.COMPLETED,
        )

        self.assertEqual(result.status, TransactionStatus.COMPLETED)

    def test_rejects_invalid_transition(self):
        with self.assertRaises(InvalidTransactionTransition):
            transition_transaction(
                self.transaction.id,
                TransactionStatus.PENDING,
                TransactionStatus.FAILED,
            )

        self.transaction.refresh_from_db()
        self.assertEqual(
            self.transaction.status,
            TransactionStatus.PENDING,
        )

    def test_rejects_transition_when_expected_status_is_stale(self):
        with self.assertRaises(ConcurrentTransactionTransition):
            transition_transaction(
                self.transaction.id,
                TransactionStatus.PROCESSING,
                TransactionStatus.COMPLETED,
            )

        self.transaction.refresh_from_db()
        self.assertEqual(
            self.transaction.status,
            TransactionStatus.PENDING,
        )

    def test_raises_when_transaction_does_not_exist(self):
        with self.assertRaises(Transaction.DoesNotExist):
            transition_transaction(
                "00000000-0000-0000-0000-000000000000",
                TransactionStatus.PENDING,
                TransactionStatus.PROCESSING,
            )


class ConcurrentTransactionTransitionTests(TransactionTestCase):
    def setUp(self):
        self.currency = Currency.objects.create(
            currency_code="NGN",
            currency_name="Nigerian Naira",
            minor_unit_exponent=2,
        )

        self.transaction = create_transaction(
            type=OperationType.DEPOSIT,
            amount=5000,
            currency=self.currency,
            status=TransactionStatus.PENDING,
        )

    def test_only_one_worker_can_transition_the_same_transaction(self):
        barrier = Barrier(2)

        def attempt_transition():
            close_old_connections()

            try:
                barrier.wait()

                transition_transaction(
                    self.transaction.id,
                    TransactionStatus.PENDING,
                    TransactionStatus.PROCESSING,
                )

                return "success"

            except ConcurrentTransactionTransition:
                return "stale"

            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(attempt_transition),
                executor.submit(attempt_transition),
            ]

            results = [future.result() for future in futures]

        self.assertCountEqual(results, ["success", "stale"])

        self.transaction.refresh_from_db()
        self.assertEqual(
            self.transaction.status,
            TransactionStatus.PROCESSING,
        )
