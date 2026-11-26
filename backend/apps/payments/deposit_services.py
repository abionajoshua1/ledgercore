
import secrets
import string

from django.db import IntegrityError, transaction

from apps.accounting.models import OperationType
from apps.audit.services import create_audit_event
from apps.idempotency.models import IdempotencyKey
from apps.idempotency.services import (
    claim_idempotency_key,
    generate_request_hash,
)
from apps.outbox.services import create_outbox_event
from apps.payments.models import Transaction
from apps.payments.services import create_transaction
from apps.wallets.models import Wallet


class DepositError(Exception):
    pass


class WalletNotFoundError(DepositError):
    pass


class WalletInactiveError(DepositError):
    pass


class InvalidDepositError(DepositError):
    pass


def generate_provider_reference():
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(
        secrets.choice(alphabet)
        for _ in range(24)
    )
    return f"SIM-DEP-{suffix}"


def lock_and_validate_deposit_wallet(
    *,
    user,
    wallet_id,
    currency_code,
):
    wallet = (
        Wallet.objects
        .select_for_update()
        .filter(
            pk=wallet_id,
            user=user,
        )
        .first()
    )

    if wallet is None:
        raise WalletNotFoundError(
            "Wallet was not found."
        )

    if wallet.status != Wallet.Status.ACTIVE:
        raise WalletInactiveError(
            "Wallet must be active."
        )

    if not wallet.currency.enabled:
        raise InvalidDepositError(
            "Wallet currency is not enabled."
        )

    if wallet.currency.currency_code != currency_code:
        raise InvalidDepositError(
            "Request currency must match wallet currency."
        )

    return wallet


def build_deposit_response(*, deposit):
    return {
        "id": str(deposit.id),
        "reference": deposit.reference,
        "provider_reference": deposit.provider_reference,
        "type": deposit.type,
        "status": deposit.status,
        "amount": deposit.amount,
        "currency": deposit.currency.currency_code,
        "destination_wallet_id": str(
            deposit.destination_wallet_id
        ),
        "created_at": deposit.created_at.isoformat(),
    }


@transaction.atomic
def initiate_deposit(
    *,
    user,
    wallet_id,
    amount,
    currency,
    idempotency_key,
    path,
    request_id=None,
):
    if type(amount) is not int or amount <= 0:
        raise InvalidDepositError(
            "Deposit amount must be a positive integer."
        )

    if (
        not isinstance(currency, str)
        or len(currency) != 3
        or not currency.isalpha()
    ):
        raise InvalidDepositError(
            "Invalid currency code."
        )

    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise InvalidDepositError(
            "An idempotency key is required."
        )

    currency_code = currency.upper()

    request_body = {
        "wallet_id": str(wallet_id),
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

    wallet = lock_and_validate_deposit_wallet(
        user=user,
        wallet_id=wallet_id,
        currency_code=currency_code,
    )

    deposit = None

    for _ in range(3):
        provider_reference = generate_provider_reference()

        try:
            with transaction.atomic():
                deposit = create_transaction(
                    type=OperationType.DEPOSIT,
                    amount=amount,
                    currency=wallet.currency,
                    destination_wallet=wallet,
                    provider_reference=provider_reference,
                )
                break

        except IntegrityError as exc:
            cause = exc.__cause__
            constraint_name = getattr(
                getattr(cause, "diag", None),
                "constraint_name",
                None,
            )

            if constraint_name != "payments_transaction_provider_reference_key":
                raise

    if deposit is None:
        raise RuntimeError(
            "Could not generate a unique provider reference."
        )

    create_outbox_event(
        event_type="deposit_requested",
        payload={
            "request_id": str(request_id) if request_id else None,
            "transaction_id": str(deposit.id),
            "transaction_reference": deposit.reference,
            "provider_reference": deposit.provider_reference,
            "wallet_id": str(wallet.id),
            "amount": amount,
            "currency": currency_code,
        },
    )

    create_audit_event(
        actor=user,
        action="deposit_requested",
        detail={
            "resource_type": "transaction",
            "resource_id": str(deposit.id),
            "resource_reference": deposit.reference,
            "provider_reference": deposit.provider_reference,
            "outcome": "pending",
            "wallet_id": str(wallet.id),
            "amount": amount,
            "currency": currency_code,
            "request_id": str(request_id) if request_id else None,
        },
    )

    response_body = build_deposit_response(
        deposit=deposit,
    )

    idempotency_record.status = IdempotencyKey.Status.COMPLETED
    idempotency_record.response_status = 202
    idempotency_record.response_body = response_body
    idempotency_record.transaction = deposit

    idempotency_record.save(
        update_fields=[
            "status",
            "response_status",
            "response_body",
            "transaction",
        ]
    )

    return 202, response_body, False
