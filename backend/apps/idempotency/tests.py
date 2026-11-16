from typing import Any, cast

from django.test import TestCase, SimpleTestCase
from django.contrib.auth import get_user_model

from apps.idempotency.models import IdempotencyKey
from apps.idempotency.services import (
    generate_request_hash,
    claim_idempotency_key,
    IdempotencyKeyReuseError,
    RequestInProgressError,
)

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections, transaction
from django.test import TransactionTestCase

User = get_user_model()

class GenerateRequestHashTests(SimpleTestCase):

    def test_same_request_produces_same_hash(self):
        first_hash = generate_request_hash(
            method="POST",
            path="/v1/transfers",
            body={"amount": 5000, "currency": "NGN"},
        )

        second_hash = generate_request_hash(
            method="POST",
            path="/v1/transfers",
            body={"amount": 5000, "currency": "NGN"},
        )

        self.assertEqual(first_hash, second_hash)

    def test_dictionary_key_order_does_not_change_hash(self):
        first_hash = generate_request_hash(
            method="POST",
            path="/v1/transfers",
            body={"amount": 5000, "currency": "NGN"},
        )

        second_hash = generate_request_hash(
            method="POST",
            path="/v1/transfers",
            body={"currency": "NGN", "amount": 5000},
        )

        self.assertEqual(first_hash, second_hash)

    def test_nested_dictionary_key_order_does_not_change_hash(self):
        first_hash = generate_request_hash(
            method="POST",
            path="/v1/transfers",
            body={"transfer": {"amount": 5000, "currency": "NGN"}},
        )

        second_hash = generate_request_hash(
            method="POST",
            path="/v1/transfers",
            body={"transfer": {"currency": "NGN", "amount": 5000}},
        )

        self.assertEqual(first_hash, second_hash)

    def test_different_method_produces_different_hash(self):
        first_hash = generate_request_hash(
            method="POST",
            path="/v1/transfers",
            body={"amount": 5000},
        )

        second_hash = generate_request_hash(
            method="PUT",
            path="/v1/transfers",
            body={"amount": 5000},
        )

        self.assertNotEqual(first_hash, second_hash)

    def test_different_path_produces_different_hash(self):
        first_hash = generate_request_hash(
            method="POST",
            path="/v1/transfers",
            body={"amount": 5000},
        )

        second_hash = generate_request_hash(
            method="POST",
            path="/v1/deposits",
            body={"amount": 5000},
        )

        self.assertNotEqual(first_hash, second_hash)

    def test_different_body_produces_different_hash(self):
        first_hash = generate_request_hash(
            method="POST",
            path="/v1/transfers",
            body={"amount": 5000},
        )

        second_hash = generate_request_hash(
            method="POST",
            path="/v1/transfers",
            body={"amount": 6000},
        )

        self.assertNotEqual(first_hash, second_hash)
        
class ClaimIdempotencyKeyTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="joshua",
            password="test-password",
        )
        self.key = "transfer-request-001"
        self.request_hash = "a" * 64

    def test_new_key_is_successfully_claimed(self):
        record, created = claim_idempotency_key(
            user=self.user,
            key=self.key,
            request_hash=self.request_hash,
        )

        self.assertTrue(created)
        self.assertEqual(record.status, IdempotencyKey.Status.IN_PROGRESS)
        self.assertEqual(record.user, self.user)

    def test_existing_in_progress_key_raises_error(self):
        claim_idempotency_key(
            user=self.user,
            key=self.key,
            request_hash=self.request_hash,
        )

        with self.assertRaises(RequestInProgressError):
            claim_idempotency_key(
                user=self.user,
                key=self.key,
                request_hash=self.request_hash,
            )

    def test_completed_key_returns_existing_record(self):
        record, created = claim_idempotency_key(
            user=self.user,
            key=self.key,
            request_hash=self.request_hash,
        )

        record.status = IdempotencyKey.Status.COMPLETED
        record.response_status = 201
        record.response_body = {"reference": "TRF-001"}
        record.save()

        replay_record, replayed = claim_idempotency_key(
            user=self.user,
            key=self.key,
            request_hash=self.request_hash,
        )

        self.assertFalse(replayed)
        self.assertEqual(replay_record.id, record.id)
        self.assertEqual(replay_record.response_status, 201)
        self.assertEqual(
            replay_record.response_body,
            {"reference": "TRF-001"},
        )

    def test_reusing_key_with_different_request_hash_raises_error(self):
        claim_idempotency_key(
            user=self.user,
            key=self.key,
            request_hash=self.request_hash,
        )

        with self.assertRaises(IdempotencyKeyReuseError):
            claim_idempotency_key(
                user=self.user,
                key=self.key,
                request_hash="b" * 64,
            )
            

class IdempotencyConcurrencyTests(TransactionTestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="concurrent_user",
            password="test-password",
        )

    def test_concurrent_requests_cannot_both_claim_same_key(self):
        barrier = Barrier(2)

        user_id = self.user.pk
        key = "concurrent-transfer-001"
        request_hash = "a" * 64

        def attempt_claim():
            close_old_connections()

            try:
                user = User.objects.get(pk=user_id)

                barrier.wait(timeout=10)

                try:
                    with transaction.atomic():
                        record, created = claim_idempotency_key(
                            user=user,
                            key=key,
                            request_hash=request_hash,
                        )

                    return "claimed" if created else "replayed"

                except RequestInProgressError:
                    return "in_progress"

            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(attempt_claim)
            second = executor.submit(attempt_claim)

            results = [
                first.result(timeout=20),
                second.result(timeout=20),
            ]

        self.assertCountEqual(
            results,
            ["claimed", "in_progress"],
        )

        self.assertEqual(
            IdempotencyKey.objects.filter(
                user=self.user,
                key=key,
            ).count(),
            1,
        )
