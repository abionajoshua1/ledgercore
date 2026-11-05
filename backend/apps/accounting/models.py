from django.db import models
from django.core.validators import MinLengthValidator
import uuid


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
            )
        ]