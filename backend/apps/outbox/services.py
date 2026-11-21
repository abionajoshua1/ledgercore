from apps.outbox.models import OutboxEvent


def create_outbox_event(*, event_type, payload):
    return OutboxEvent.objects.create(
        event_type=event_type,
        payload=payload,
    )