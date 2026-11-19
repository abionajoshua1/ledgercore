
from django.db import transaction

from apps.accounting.models import (
    EntryDirection,
    OperationType,
)
from apps.accounting.services import create_journal_entry

from apps.idempotency.models import IdempotencyKey
from apps.idempotency.services import (
    claim_idempotency_key,
    generate_request_hash,
)

from apps.payments.models import Transaction, TransactionStatus
from apps.payments.services import create_transaction
from apps.wallets.models import Wallet
from apps.wallets.services import get_wallet_available_balance

from django.db.models import Sum
from django.utils import timezone
from datetime import datetime, time, timedelta, timezone as datetime_timezone

from apps.wallets.models import WalletTransferLimit


class TransferError(Exception):
    pass


class WalletNotFoundError(TransferError):
    pass


class WalletInactiveError(TransferError):
    pass


class InsufficientFundsError(TransferError):
    pass

class TransferLimitNotConfiguredError(TransferError):
    pass

class TransferLimitExceededError(TransferError):
    pass

class InvalidTransferError(TransferError):
    pass


def lock_and_validate_wallets(
    *,
    user,
    source_wallet_id,
    destination_wallet_id,
    currency_code,
):
    if str(source_wallet_id) == str(destination_wallet_id):
        raise InvalidTransferError(
            "Source and destination wallets must be different."
        )

    wallets = (
        Wallet.objects
        .select_for_update()
        .filter(
            pk__in=[source_wallet_id, destination_wallet_id]
        )
        .order_by("pk")
    )

    locked_wallets = {
        str(wallet.pk): wallet
        for wallet in wallets
    }

    source_wallet = locked_wallets.get(str(source_wallet_id))
    destination_wallet = locked_wallets.get(
        str(destination_wallet_id)
    )

    if source_wallet is None or destination_wallet is None:
        raise WalletNotFoundError(
            "Source or destination wallet was not found."
        )

    if source_wallet.user_id != user.pk:
        raise WalletNotFoundError(
            "Source or destination wallet was not found."
        )

    if (
        source_wallet.status != Wallet.Status.ACTIVE
        or destination_wallet.status != Wallet.Status.ACTIVE
    ):
        raise WalletInactiveError(
            "Both wallets must be active."
        )

    if source_wallet.currency_id != destination_wallet.currency_id:
        raise InvalidTransferError(
            "Wallet currencies must match."
        )

    if source_wallet.currency.currency_code != currency_code:
        raise InvalidTransferError(
            "Request currency must match both wallet currencies."
        )

    return source_wallet, destination_wallet


def validate_transfer_funds(*, source_wallet, amount):
    if type(amount) is not int or amount <= 0:
        raise InvalidTransferError(
            "Transfer amount must be a positive integer."
        )

    available_balance = get_wallet_available_balance(
        wallet=source_wallet,
    )

    if amount > available_balance:
        raise InsufficientFundsError(
            "Insufficient available balance."
        )

    return available_balance
    

def validate_transfer_limits(*, source_wallet, amount):
    try:
        limits = WalletTransferLimit.objects.get(
            wallet=source_wallet,
        )
    except WalletTransferLimit.DoesNotExist:
        raise TransferLimitNotConfiguredError(
            "Transfer limits have not been configured for this wallet."
        )

    if amount > limits.max_transaction_amount:
        raise TransferLimitExceededError(
            "Transfer amount exceeds the maximum transaction limit."
        )

    now = timezone.now().astimezone(datetime_timezone.utc)
    day_start = datetime.combine(
        now.date(),
        time.min,
        tzinfo=datetime_timezone.utc,
    )
    next_day = day_start + timedelta(days=1)

    daily_transfers = Transaction.objects.filter(
        source_wallet=source_wallet,
        type=OperationType.TRANSFER,
        status=TransactionStatus.COMPLETED,
        created_at__gte=day_start,
        created_at__lt=next_day,
    )

    daily_transaction_count = daily_transfers.count()

    if daily_transaction_count + 1 > limits.daily_transaction_limit:
        raise TransferLimitExceededError(
            "Daily transaction limit exceeded."
        )

    daily_amount = daily_transfers.aggregate(
        total=Sum("amount"),
    )["total"] or 0

    if daily_amount + amount > limits.daily_amount_limit:
        raise TransferLimitExceededError(
            "Daily transfer amount limit exceeded."
        )


def create_transfer_records(
    *,
    user,
    source_wallet,
    destination_wallet,
    amount,
):
    transfer = create_transaction(
        type=OperationType.TRANSFER,
        status=TransactionStatus.COMPLETED,
        amount=amount,
        currency=source_wallet.currency,
        source_wallet=source_wallet,
        destination_wallet=destination_wallet,
    )

    journal_entry = create_journal_entry(
        reference=transfer.reference,
        operation_type=OperationType.TRANSFER,
        currency=source_wallet.currency,
        description="Wallet transfer",
        transaction_record=transfer,
        initiated_by=user,
        lines=[
            {
                "account": source_wallet.ledger_account,
                "direction": EntryDirection.DEBIT,
                "amount": amount,
            },
            {
                "account": destination_wallet.ledger_account,
                "direction": EntryDirection.CREDIT,
                "amount": amount,
            },
        ],
    )

    return transfer, journal_entry


def build_transfer_response(*, transfer, journal_entry):
    return {
        "id": str(transfer.id),
        "reference": transfer.reference,
        "type": transfer.type,
        "status": transfer.status,
        "amount": transfer.amount,
        "fee": 0,
        "currency": transfer.currency.currency_code,
        "source_wallet_id": str(transfer.source_wallet_id),
        "destination_wallet_id": str(transfer.destination_wallet_id),
        "journal_entry_id": str(journal_entry.id),
        "created_at": transfer.created_at.isoformat(),
    }


@transaction.atomic
def execute_transfer(
    *,
    user,
    source_wallet_id,
    destination_wallet_id,
    amount,
    currency,
    idempotency_key,
    path,
):
    if (
        not isinstance(currency, str)
        or len(currency) != 3
        or not currency.isalpha()
    ):
        raise InvalidTransferError(
            "Invalid currency code."
        )

    currency_code = currency.upper()

    request_body = {
        "source_wallet_id": str(source_wallet_id),
        "destination_wallet_id": str(destination_wallet_id),
        "amount": amount,
        "currency": currency_code,
    }

    request_hash = generate_request_hash(
        method="POST",
        path=path,
        body=request_body,
    )

    idempotency_record, is_new_request = claim_idempotency_key(
        user=user,
        key=idempotency_key,
        request_hash=request_hash,
    )

    if not is_new_request:
        return (
            idempotency_record.response_status,
            idempotency_record.response_body,
            True,
        )

    source_wallet, destination_wallet = lock_and_validate_wallets(
        user=user,
        source_wallet_id=source_wallet_id,
        destination_wallet_id=destination_wallet_id,
        currency_code=currency_code,
    )

    validate_transfer_funds(
        source_wallet=source_wallet,
        amount=amount,
    )
    
    validate_transfer_limits(
        source_wallet=source_wallet,
        amount=amount,
    )

    transfer, journal_entry = create_transfer_records(
        user=user,
        source_wallet=source_wallet,
        destination_wallet=destination_wallet,
        amount=amount,
    )

    response_body = build_transfer_response(
        transfer=transfer,
        journal_entry=journal_entry,
    )

    idempotency_record.status = IdempotencyKey.Status.COMPLETED
    idempotency_record.response_status = 201
    idempotency_record.response_body = response_body
    idempotency_record.transaction = transfer

    idempotency_record.save(
        update_fields=[
            "status",
            "response_status",
            "response_body",
            "transaction",
        ]
    )

    return 201, response_body, False
