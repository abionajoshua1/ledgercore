import uuid

from django.db import transaction
from django.contrib.auth import get_user_model

from apps.accounting.models import AccountType, Currency, LedgerAccount
from apps.wallets.models import Wallet

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

    return wallet