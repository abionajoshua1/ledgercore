import uuid

from django.db import transaction, IntegrityError
from django.contrib.auth import get_user_model

from apps.accounting.models import AccountType, Currency, LedgerAccount, JournalLine
from apps.wallets.models import Wallet

from django.db.models import Sum, Q

User = get_user_model()

class WalletCreationError(Exception):
    pass

@transaction.atomic
def create_wallet(*, user, currency):
    user = User.objects.select_for_update().get(pk=user.pk)
    currency = Currency.objects.get(pk=currency.pk)

    if not currency.enabled:
        raise WalletCreationError("This currency is currently unavailable.")

    existing_wallet = Wallet.objects.filter(
        user=user,
        currency=currency,
    ).exclude(
        status=Wallet.Status.CLOSED
    ).exists()

    if existing_wallet:
        raise WalletCreationError(
            "You already have a wallet in this currency."
        )

    wallet_id = uuid.uuid4()

    try:
        with transaction.atomic():
            ledger_account = LedgerAccount.objects.create(
                account_code=f"WALLET-{wallet_id.hex[:20]}",
                account_name=f"Customer Wallet {wallet_id.hex[:12].upper()}",
                account_type=AccountType.LIABILITY,
                currency=currency,
            )

            wallet = Wallet.objects.create(
                id=wallet_id,
                user=user,
                currency=currency,
                ledger_account=ledger_account,
            )

    except IntegrityError as exc:
        cause = exc.__cause__
        constraint_name = getattr(
            getattr(cause, "diag", None),
            "constraint_name",
            None,
        )

        if constraint_name == "uniq_nonclosed_wallet_per_user_currency":
            raise WalletCreationError(
                "You already have a wallet in this currency."
            ) from exc

        raise

    return wallet

def get_wallet_balance(*, wallet):
    journal_lines = JournalLine.objects.filter(
        account_id=wallet.ledger_account_id,
        entry__posted_at__isnull=False,
    )

    totals = journal_lines.aggregate(
        total_credits=Sum(
            "amount",
            filter=Q(direction="CREDIT"),
        ),
        total_debits=Sum(
            "amount",
            filter=Q(direction="DEBIT"),
        ),
    )

    total_credits = totals.get("total_credits") or 0
    total_debits = totals.get("total_debits") or 0

    return total_credits - total_debits

class WalletLifecycleError(Exception):
    pass


@transaction.atomic
def deactivate_wallet(*, wallet):
    wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)

    if wallet.status == Wallet.Status.INACTIVE:
        raise WalletLifecycleError("This wallet is already inactive.")

    if wallet.status == Wallet.Status.CLOSED:
        raise WalletLifecycleError("A closed wallet cannot be deactivated.")

    wallet.status = Wallet.Status.INACTIVE
    wallet.save(update_fields=["status"])

    return wallet

@transaction.atomic
def reactivate_wallet(*, wallet):
    wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)

    if wallet.status == Wallet.Status.ACTIVE:
        raise WalletLifecycleError("This wallet is already active.")

    if wallet.status == Wallet.Status.CLOSED:
        raise WalletLifecycleError("A closed wallet cannot be reactivated.")

    wallet.status = Wallet.Status.ACTIVE
    wallet.save(update_fields=["status"])

    return wallet

@transaction.atomic
def close_wallet(*, wallet):
    wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)

    if wallet.status == Wallet.Status.CLOSED:
        raise WalletLifecycleError("This wallet is already closed.")

    balance = get_wallet_balance(wallet=wallet)

    if balance != 0:
        raise WalletLifecycleError(
            "A wallet with a non-zero balance cannot be closed."
        )

    wallet.status = Wallet.Status.CLOSED
    wallet.save(update_fields=["status"])

    return wallet