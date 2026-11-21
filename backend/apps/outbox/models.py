from django.db import models
from django.db.models import Q


class OutboxEvent(models.Model):
    event_type = models.TextField()
    payload = models.JSONField()
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(
                fields=["id"],
                name="outbox_unpublished_idx",
                condition=Q(published_at__isnull=True),
            ),
        ]