from django.db import transaction

from apps.accounting.models import (
    EntryDirection,
    JournalEntry,
    JournalLine,
    OperationType,
)


class JournalPostingError(Exception):
    pass


@transaction.atomic
def create_journal_entry(
    *,
    reference,
    operation_type,
    currency,
    description,
    lines,
    transaction_record=None,
    initiated_by=None,
):
    lines = list(lines)

    if len(lines) < 2:
        raise JournalPostingError(
            "A journal entry must contain at least two lines."
        )

    if operation_type not in OperationType.values:
        raise JournalPostingError(
            "Invalid journal operation type."
        )

    debit_total = 0
    credit_total = 0

    for line in lines:
        account = line["account"]
        
        if not account.is_active:
            raise JournalPostingError(
                "Cannot post to an inactive ledger account."
            )
    
        direction = line["direction"]
        amount = line["amount"]

        if account.currency_id != currency.pk:
            raise JournalPostingError(
                "Journal account currency must match entry currency."
            )

        if direction not in EntryDirection.values:
            raise JournalPostingError(
                "Invalid journal line direction."
            )

        if type(amount) is not int or amount <= 0:
            raise JournalPostingError(
                "Journal line amounts must be positive integers."
            )

        if direction == EntryDirection.DEBIT:
            debit_total += amount
        else:
            credit_total += amount

    if debit_total != credit_total:
        raise JournalPostingError(
            "Journal entry debits and credits must balance."
        )

    if transaction_record is not None:
        if transaction_record.currency_id != currency.pk:
            raise JournalPostingError(
                "Transaction currency must match journal currency."
            )

        if transaction_record.type != operation_type:
            raise JournalPostingError(
                "Transaction type must match journal operation type."
            )

    entry = JournalEntry.objects.create(
        transaction=transaction_record,
        reference=reference,
        operation_type=operation_type,
        currency=currency,
        description=description,
        initiated_by=initiated_by,
    )

    for line_number, line in enumerate(lines, start=1):
        JournalLine.objects.create(
            entry=entry,
            account=line["account"],
            line_number=line_number,
            direction=line["direction"],
            amount=line["amount"],
            description=line.get("description", ""),
            currency=currency,
        )

    return entry