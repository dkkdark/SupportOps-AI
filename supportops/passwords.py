"""Bound memory-heavy password work without weakening Argon2 parameters."""

from threading import BoundedSemaphore
from argon2 import PasswordHasher
from fastapi import HTTPException


class BoundedPasswords:
    def __init__(self):
        # Each Argon2 operation uses about 64 MB. Two fit the VPS API container.
        # This limit is per API process and shared by every password endpoint.
        self._slots = BoundedSemaphore(2)
        self._hasher = PasswordHasher()

    def _run(self, operation, *args):
        if not self._slots.acquire(blocking=False):
            raise HTTPException(
                429,
                "Sign-in services are busy. Please try again shortly.",
                headers={"Retry-After": "1"},
            )
        try:
            return operation(*args)
        finally:
            self._slots.release()

    def hash(self, password):
        return self._run(self._hasher.hash, password)

    def verify(self, encoded, password):
        return self._run(self._hasher.verify, encoded, password)

    def check_needs_rehash(self, encoded):
        return self._hasher.check_needs_rehash(encoded)


PASSWORDS = BoundedPasswords()
