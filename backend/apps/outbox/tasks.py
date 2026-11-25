import logging

from celery import shared_task

from apps.outbox.models import OutboxEvent

logger = logging.getLogger(__name__)


@shared_task
def process_outbox_event(event_id):
    event = OutboxEvent.objects.get(pk=event_id)

    logger.info(
        "Outbox event received",
        extra={
            "event_id": event.id,
            "event_type": event.event_type,
            "payload": event.payload,
        },
    )
    
@shared_task
def publish_outbox_events():
    from apps.outbox.services import publish_pending_events

    return publish_pending_events()