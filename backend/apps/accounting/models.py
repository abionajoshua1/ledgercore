from django.db import models
from django.core.validators import MinLengthValidator
import uuid
from django.conf import settings


class Currency(models.Model):
    currency_code = models.CharField(
        max_length=3,
        unique=True,
        validators=[MinLengthValidator(3)],
    )
    currency_name = models.CharField(max_length=50)
    minor_unit_exponent = models.PositiveSmallIntegerField()
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.currency_name} ({self.currency_code})"

class AccountType(models.TextChoices):
    ASSET = 'ASSET', 'Asset'
    LIABILITY = 'LIABILITY', 'Liability'
    EQUITY = 'EQUITY', 'Equity'
    INCOME = 'INCOME', 'Income'
    EXPENSE = 'EXPENSE', 'Expense'

class LedgerAccount(models.Model):
    @property
    def normal_side(self):
        if self.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
            return "DEBIT"
        elif self.account_type in [AccountType.LIABILITY, AccountType.EQUITY, AccountType.INCOME]:
            return "CREDIT"
        else:
            return None

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_code = models.CharField(max_length=30)
    account_name = models.CharField(max_length=100)
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices
    )

    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["currency", "account_code"],
                name="unique_account_code_per_currency",
            ),
            models.UniqueConstraint(
                fields=["id", "currency"],
                name="unique_account_id_currency",
            ),
        ]

class OperationType(models.TextChoices):
    DEPOSIT = 'DEPOSIT', 'Deposit'
    WITHDRAWAL = 'WITHDRAWAL', 'Withdrawal'
    TRANSFER = 'TRANSFER', 'Transfer'

class EntryDirection(models.TextChoices):
    DEBIT = 'DEBIT', 'Debit'
    CREDIT = 'CREDIT', 'Credit'

class JournalEntry(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    reference = models.CharField(max_length=50, unique=True)
    operation_type = models.CharField(
        max_length=20,
        choices=OperationType.choices
    )
    currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT
    )
    description = models.CharField(max_length=255)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='initiated_journal_entries'
    )
    posted_at = models.DateTimeField(
        null=True,
        editable=False,
    )

    def __str__(self):
        return self.reference

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["id", "currency"],
                name="unique_entry_id_currency",
            ),
            models.CheckConstraint(
                condition=models.Q(operation_type__in=OperationType.values),
                name="journal_entry_valid_operation_type",
            ),
        ]

class JournalLine(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.PROTECT,
        related_name='lines'
    )
    account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.PROTECT,
        related_name='journal_lines'
    )
    line_number = models.PositiveIntegerField()
    direction = models.CharField(
        max_length=6,
        choices=EntryDirection.choices,
    )
    amount = models.PositiveBigIntegerField()
    description = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )
    currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
    )

    def __str__(self):
        return f"{self.entry.reference} - Line {self.line_number}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "line_number"],
                name="unique_line_number_per_entry",
            ),
            models.CheckConstraint(
                condition=models.Q(line_number__gte=1),
                name="journal_line_number_at_least_one",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="journal_line_amount_greater_than_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(direction__in=EntryDirection.values),
                name="journal_line_valid_direction",
            ),
        ]