from django.db import IntegrityError, transaction

from apps.payments.models import Transaction
from apps.payments.references import generate_transaction_reference


def create_transaction(**fields):
    for _ in range(3):
        reference = generate_transaction_reference()

        try:
            with transaction.atomic():
                return Transaction.objects.create(
                    reference=reference,
                    **fields,
                )

        except IntegrityError as exc:
            cause = exc.__cause__
            constraint_name = getattr(
                getattr(cause, "diag", None),
                "constraint_name",
                None,
            )

            if constraint_name != "payments_transaction_reference_key":
                raise

    raise RuntimeError(
        "Could not generate a unique transaction reference."
    )