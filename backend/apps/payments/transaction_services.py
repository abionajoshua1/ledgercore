from django.db import transaction

from apps.payments.models import Transaction, TransactionStatus


class InvalidTransactionTransition(Exception):
    pass


class ConcurrentTransactionTransition(Exception):
    pass


ALLOWED_TRANSITIONS = {
    TransactionStatus.PENDING: {
        TransactionStatus.PROCESSING,
        TransactionStatus.COMPLETED,
        TransactionStatus.EXPIRED,
    },
    TransactionStatus.PROCESSING: {
        TransactionStatus.COMPLETED,
        TransactionStatus.FAILED,
        TransactionStatus.UNKNOWN,
    },
    TransactionStatus.UNKNOWN: {
        TransactionStatus.COMPLETED,
        TransactionStatus.FAILED,
    },
}


@transaction.atomic
def transition_transaction(transaction_id, expected_status, new_status):
    allowed_statuses = ALLOWED_TRANSITIONS.get(expected_status, set())

    if new_status not in allowed_statuses:
        raise InvalidTransactionTransition(
            f"Transition from {expected_status} to {new_status} is not allowed."
        )

    updated = Transaction.objects.filter(
        id=transaction_id,
        status=expected_status,
    ).update(status=new_status)

    if updated == 0:
        if not Transaction.objects.filter(id=transaction_id).exists():
            raise Transaction.DoesNotExist(
                f"Transaction {transaction_id} does not exist."
            )

        raise ConcurrentTransactionTransition(
            f"Transaction {transaction_id} is no longer in "
            f"the expected status: {expected_status}."
        )

    return Transaction.objects.get(id=transaction_id)