import os
import tempfile
import unittest

TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["TENNIS_DB_PATH"] = os.path.join(TEMP_DIR.name, "test.db")
os.environ["SECRET_KEY"] = "test-secret"

from app import app, db, update_user_from_oidc  # noqa: E402


class ServerFlowTest(unittest.TestCase):
    def test_oidc_refresh_preserves_inactive_status(self):
        claims = {"first_name": "Ева", "last_name": "Новикова", "class_name": "", "role": "user", "email": ""}
        with db() as connection:
            connection.execute("UPDATE users SET status='inactive' WHERE id='u8'")
            update_user_from_oidc(connection, "u8", claims)
            status = connection.execute("SELECT status FROM users WHERE id='u8'").fetchone()["status"]
            self.assertEqual(status, "inactive")
            connection.execute("UPDATE users SET status='active' WHERE id='u8'")

    def test_match_request_notification_and_confirmation(self):
        requester = app.test_client()
        opponent = app.test_client()

        public_state = requester.get("/api/state")
        self.assertEqual(public_state.status_code, 200)
        initial_matches = len(public_state.get_json()["matches"])

        self.assertEqual(requester.post("/api/login", json={"login": "maxim", "password": "123456"}).status_code, 200)
        created = requester.post("/api/requests", json={"opponent": "u8", "scoreRequester": 11, "scoreOpponent": 7})
        self.assertEqual(created.status_code, 200)
        request_id = created.get_json()["id"]
        token = created.get_json()["token"]

        self.assertEqual(requester.post(f"/api/requests/{request_id}/notify", json={}).status_code, 200)
        self.assertEqual(opponent.post("/api/login", json={"login": "eva", "password": "123456"}).status_code, 200)
        opponent_state = opponent.get("/api/state").get_json()
        self.assertTrue(any(item["id"] == request_id and item["notified"] for item in opponent_state["requests"]))

        self.assertEqual(opponent.get(f"/confirm/{token}").status_code, 200)
        self.assertEqual(opponent.get(f"/api/confirm/{token}").get_json()["id"], request_id)
        self.assertEqual(opponent.post(f"/api/requests/{request_id}/accept", json={}).status_code, 200)

        final_state = requester.get("/api/state").get_json()
        self.assertEqual(len(final_state["matches"]), initial_matches + 1)


if __name__ == "__main__":
    unittest.main()
