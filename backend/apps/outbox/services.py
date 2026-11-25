from django.db import transaction
from django.utils import timezone

from apps.outbox.models import OutboxEvent
from apps.outbox.tasks import process_outbox_event

BATCH_SIZE = 100


def create_outbox_event(*, event_type, payload):
    return OutboxEvent.objects.create(
        event_type=event_type,
        payload=payload,
    )


def publish_pending_events():
    published_count = 0

    with transaction.atomic():
        events = list(
            OutboxEvent.objects
            .select_for_update(skip_locked=True)
            .filter(published_at__isnull=True)
            .order_by("id")[:BATCH_SIZE]
        )

        for event in events:
            process_outbox_event.delay(event.id)

            event.published_at = timezone.now()
            event.save(update_fields=["published_at"])

            published_count += 1

    return published_count