import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.accounting.models import Currency, LedgerAccount


class Wallet(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        CLOSED = "CLOSED", "Closed"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="wallets",
    )

    currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        related_name="wallets",
    )

    ledger_account = models.OneToOneField(
        LedgerAccount,
        on_delete=models.PROTECT,
        related_name="wallet",
    )

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "currency"],
                condition=~Q(status="CLOSED"),
                name="uniq_nonclosed_wallet_per_user_currency",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["ACTIVE", "INACTIVE", "CLOSED"]),
                name="wallet_status_valid",
            ),
        ]