from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from apps.accounting.models import Currency, OperationType
from apps.payments.models import Transaction
from apps.payments.references import generate_transaction_reference
from apps.payments.services import create_transaction

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