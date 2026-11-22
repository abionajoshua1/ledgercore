from apps.audit.models import AuditEvent


def create_audit_event(*, actor, action, detail):
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        detail=detail,
    )