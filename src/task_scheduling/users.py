"""User registration and atomic daily quota accounting."""

from datetime import date
from threading import RLock

from .models import QuotaStatus, User, require_date


class UserManager:
    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._usage: dict[tuple[str, date], int] = {}
        self._lock = RLock()

    def register(self, user_id: str, quota: int) -> User:
        user = User(user_id, quota)
        with self._lock:
            if user_id in self._users:
                raise ValueError(f"user already exists: {user_id}")
            self._users[user_id] = user
        return user

    def get(self, user_id: str) -> User:
        with self._lock:
            try:
                return self._users[user_id]
            except KeyError as exc:
                raise ValueError(f"unknown user: {user_id}") from exc

    def list_users(self) -> tuple[User, ...]:
        with self._lock:
            return tuple(self._users.values())

    def quota_status(self, user_id: str, day: date) -> QuotaStatus:
        require_date(day)
        with self._lock:
            user = self.get(user_id)
            return QuotaStatus(user.id, day, user.quota, self._usage.get((user_id, day), 0))

    def try_consume(self, user_id: str, day: date) -> bool:
        """Reserve one attempt atomically; failed actions do not refund it."""
        require_date(day)
        with self._lock:
            status = self.quota_status(user_id, day)
            if status.remaining == 0:
                return False
            self._usage[(user_id, day)] = status.used + 1
            return True

