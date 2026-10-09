from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import unittest

from task_scheduling import UserManager
from tests.helpers import NOW


class UserManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.users = UserManager()
        self.users.register("alice", 3)

    def test_registration_and_lookup(self) -> None:
        self.assertEqual(self.users.get("alice").quota, 3)
        self.assertEqual([user.id for user in self.users.list_users()], ["alice"])

    def test_invalid_users_and_quotas(self) -> None:
        for name, quota in [("", 3), (" alice", 3), ("bob", -1), ("bob", True), ("bob", 1.5)]:
            with self.subTest(name=name, quota=quota), self.assertRaises(ValueError):
                self.users.register(name, quota)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.users.register("alice", 5)
        with self.assertRaisesRegex(ValueError, "unknown user"):
            self.users.get("missing")

    def test_quota_limit_and_status(self) -> None:
        day = NOW.date()
        self.assertEqual([self.users.try_consume("alice", day) for _ in range(4)], [True, True, True, False])
        status = self.users.quota_status("alice", day)
        self.assertEqual((status.quota, status.used, status.remaining), (3, 3, 0))

    def test_usage_is_independent_per_user_and_date(self) -> None:
        self.users.register("bob", 5)
        day = NOW.date()
        for _ in range(3):
            self.users.try_consume("alice", day)
        self.assertTrue(self.users.try_consume("bob", day))
        tomorrow = day + timedelta(days=1)
        self.assertEqual(self.users.quota_status("alice", tomorrow).used, 0)
        self.assertTrue(self.users.try_consume("alice", tomorrow))
        self.assertEqual(self.users.quota_status("alice", day).used, 3)

    def test_zero_quota(self) -> None:
        self.users.register("blocked", 0)
        self.assertFalse(self.users.try_consume("blocked", NOW.date()))

    def test_atomic_quota_reservations(self) -> None:
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.users.try_consume("alice", NOW.date()), range(100)))
        self.assertEqual(sum(results), 3)
        self.assertEqual(self.users.quota_status("alice", NOW.date()).used, 3)

    def test_invalid_quota_day(self) -> None:
        for value in [NOW, "2026-10-09", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.users.try_consume("alice", value)
