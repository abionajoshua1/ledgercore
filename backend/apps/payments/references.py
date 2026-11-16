import secrets
import string


def generate_transaction_reference():
    alphabet = string.ascii_uppercase + string.digits

    suffix = "".join(
        secrets.choice(alphabet)
        for _ in range(8)
    )

    return f"TXN-{suffix}"