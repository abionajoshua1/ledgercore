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
        
class Hold(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        CAPTURED = "CAPTURED", "Captured"
        RELEASED = "RELEASED", "Released"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        related_name="holds",
    )

    transaction = models.ForeignKey(
        "payments.Transaction",
        on_delete=models.PROTECT,
        related_name="holds",
    )

    amount = models.PositiveBigIntegerField()

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="hold_amount_greater_than_zero",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["ACTIVE", "CAPTURED", "RELEASED"]),
                name="hold_status_valid",
            ),
            models.UniqueConstraint(
                fields=["transaction"],
                name="uniq_hold_per_transaction",
            ),
        ]