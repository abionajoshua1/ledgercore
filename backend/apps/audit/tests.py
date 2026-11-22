from django.db import DatabaseError
from django.test import TestCase

from apps.audit.models import AuditEvent
from django.contrib.auth import get_user_model


class AuditEventAppendOnlyTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="audit_test_user",
            password="test-password",
        )

        self.audit_event = AuditEvent.objects.create(
            actor=self.user,
            action="transfer_posted",
            detail={
                "resource_type": "transaction",
                "resource_id": "test-transaction-001",
                "outcome": "success",
            },
        )

    def test_allows_new_audit_events(self):
        event = AuditEvent.objects.create(
            actor=self.user,
            action="wallet_created",
            detail={"outcome": "success"},
        )

        self.assertIsNotNone(event.pk)
        self.assertEqual(AuditEvent.objects.count(), 2)

    def test_rejects_audit_event_updates(self):
        with self.assertRaises(DatabaseError):
            AuditEvent.objects.filter(
                pk=self.audit_event.pk,
            ).update(action="modified_action")

    def test_rejects_audit_event_deletions(self):
        with self.assertRaises(DatabaseError):
            AuditEvent.objects.filter(
                pk=self.audit_event.pk,
            ).delete()