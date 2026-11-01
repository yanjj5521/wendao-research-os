from __future__ import annotations

import types
import unittest

from hub.routes.auth import session_reset


class SessionResetTests(unittest.TestCase):
    def test_clears_stale_session_without_csrf(self) -> None:
        request = types.SimpleNamespace(
            session={"hub_user_id": 7, "csrf": "stale-token"}
        )
        response = session_reset(request)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        self.assertEqual(request.session, {})

    def test_repeat_reset_is_idempotent(self) -> None:
        request = types.SimpleNamespace(session={})
        response = session_reset(request)
        self.assertEqual(response.status_code, 303)


if __name__ == "__main__":
    unittest.main()
