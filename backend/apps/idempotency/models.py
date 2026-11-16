import uuid

from django.conf import settings
from django.db import models


class IdempotencyKey(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        COMPLETED = "COMPLETED", "Completed"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="idempotency_keys",
    )
    key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
    )
    response_status = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )
    response_body = models.JSONField(null=True, blank=True)
    transaction = models.ForeignKey(
        "payments.Transaction",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="idempotency_keys",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "key"],
                name="uniq_idempotency_key_per_user",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["IN_PROGRESS", "COMPLETED"]),
                name="idempotency_status_valid",
            ),
        ]

    def __str__(self):
        return f"{self.user._id}:{self.key}"