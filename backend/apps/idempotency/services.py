
import hashlib
import json

from django.db import IntegrityError, transaction

from apps.idempotency.models import IdempotencyKey


class IdempotencyKeyReuseError(Exception):
    pass


class RequestInProgressError(Exception):
    pass


def generate_request_hash(*, method, path, body):
    payload = {
        "method": method.upper(),
        "path": path,
        "body": body,
    }

    canonical_payload = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

    return hashlib.sha256(
        canonical_payload.encode("utf-8")
    ).hexdigest()


def claim_idempotency_key(*, user, key, request_hash):
    connection = transaction.get_connection()

    if not connection.in_atomic_block:
        raise RuntimeError(
            "claim_idempotency_key must run inside an atomic transaction."
        )

    try:
        with transaction.atomic():
            record = IdempotencyKey.objects.create(
                user=user,
                key=key,
                request_hash=request_hash,
                status=IdempotencyKey.Status.IN_PROGRESS,
            )

        return record, True

    except IntegrityError:
        record = (
            IdempotencyKey.objects
            .select_for_update()
            .filter(user=user, key=key)
            .first()
        )

        if record is None:
            raise

        if record.request_hash != request_hash:
            raise IdempotencyKeyReuseError(
                "This idempotency key was already used for a different request."
            )

        if record.status == IdempotencyKey.Status.COMPLETED:
            return record, False

        raise RequestInProgressError(
            "A request with this idempotency key is still in progress."
        )
