
import uuid

from django.db import models
from django.db.models import F, Q

from apps.accounting.models import Currency, OperationType
from apps.wallets.models import Wallet


class TransactionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    UNKNOWN = "UNKNOWN", "Unknown"
    EXPIRED = "EXPIRED", "Expired"
    REVERSED = "REVERSED", "Reversed"


class Transaction(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    reference = models.CharField(
        max_length=50,
        unique=True,
    )
    type = models.CharField(
        max_length=20,
        choices=OperationType.choices,
    )
    status = models.CharField(
        max_length=12,
        choices=TransactionStatus.choices,
        default=TransactionStatus.PENDING,
    )
    amount = models.PositiveBigIntegerField()
    currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        related_name="transactions",
    )
    source_wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="outgoing_transactions",
    )
    destination_wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incoming_transactions",
    )
    provider_reference = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
    )

    def __str__(self):
        return self.reference

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="transaction_amount_greater_than_zero",
            ),
            models.CheckConstraint(
                condition=Q(status__in=TransactionStatus.values),
                name="transaction_valid_status",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(type=OperationType.TRANSFER)
                    | (
                        Q(source_wallet__isnull=False)
                        & Q(destination_wallet__isnull=False)
                        & ~Q(source_wallet=F("destination_wallet"))
                    )
                ),
                name="transaction_transfer_wallets_valid",
            ),
            models.CheckConstraint(
                condition=Q(type__in=OperationType.values),
                name="transaction_valid_type",
            ),
        ]
