"""Self-contained API regressions: no main import, watcher, or production DB.

Run: python3 -m pytest backend/tests/test_api_regressions.py
Requires backend requirements plus pytest and httpx. Does not require conftest.
"""
import hashlib
import importlib.util
import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# If database has not been imported by a shared fixture, intercept its engine
# construction as well. Even importing this test never opens a production DB.
if "app.database" not in sys.modules:
    with patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}):
        bootstrap_engine = create_engine("sqlite://", poolclass=StaticPool,
                                         connect_args={"check_same_thread": False})
        with patch("sqlalchemy.create_engine", return_value=bootstrap_engine):
            from app import database
else:
    from app import database

with patch.dict(os.environ, {"SECRET_KEY": "api-regression-test-secret-not-for-production-32"}):
    from app.api import auth, user, application, account, switch, link

from app.database import (Base, get_db, User, UserStatus, Server, Account,
                          AccountStatus, Application, Switch, SwitchPort,
                          ServerInterface, Connection)

PASSWORD = "correct-password"
LEGACY_HASH = hashlib.sha256(PASSWORD.encode()).hexdigest()


class APIRegressions(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.admin = self.make_user("admin", is_admin=True)
        self.member = self.make_user("member")
        self.server = Server(host="compute.test", port=22)
        self.gateway = Server(host="gateway.test", port=22, is_gateway=True)
        self.db.add_all([self.server, self.gateway])
        self.db.commit()
        app = FastAPI()
        for prefix, module in [("auth", auth), ("user", user), ("app", application),
                               ("account", account), ("switch", switch), ("link", link)]:
            app.include_router(module.router, prefix=f"/{prefix}")
        def override_db():
            try:
                yield self.db
            finally:
                # Match get_db's close/rollback behavior without discarding the
                # session used by assertions and test data setup.
                self.db.rollback()
        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.addCleanup(self.client.close)

    def make_user(self, name, **kwargs):
        obj = User(username=name, realname=name, account_name=name,
                   mail=f"{name}@example.com", public_key="", password=LEGACY_HASH,
                   status=kwargs.pop("status", UserStatus.ACTIVE), **kwargs)
        self.db.add(obj)
        self.db.commit()
        return obj

    def authenticate(self, obj=None):
        obj = obj or self.member
        self.client.cookies.clear()
        self.client.cookies.set("access_token", auth.create_access_token(
            {"sub": obj.username, "id": obj.id}, timedelta(minutes=5)))

    def registration(self, **changes):
        data = dict(username="newuser", realname="New User", account_name="newuser",
                    mail="new@example.com", public_key="", password=PASSWORD)
        data.update(changes)
        return self.client.post("/auth/register", json=data)

    def ports(self, count=4):
        sw = Switch(name="test-switch", num_row=1, num_col=count)
        self.db.add(sw)
        self.db.flush()
        ports = [SwitchPort(switch=sw, phy_row=0, phy_col=i) for i in range(count)]
        self.db.add_all(ports)
        self.db.commit()
        return ports

    def connect_ports(self, first, second):
        return self.client.post("/link/switch_port/connect", json={
            "port_a_id": first.id, "port_b_id": second.id})

    def test_public_or_missing_jwt_secret_fails_closed(self):
        for secret in ("", "change-me", "your-secret-key", "too-short"):
            with self.subTest(secret=secret), patch.dict(os.environ, {"SECRET_KEY": secret}):
                spec = importlib.util.spec_from_file_location("invalid_auth_config", auth.__file__)
                module = importlib.util.module_from_spec(spec)
                with self.assertRaisesRegex(RuntimeError, "Set SECRET_KEY"):
                    spec.loader.exec_module(module)

    def test_auth_statuses_and_protected_devices(self):
        for path in ("/user/me", "/user/admin", "/link/devices"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
        self.authenticate()
        self.assertEqual(self.client.get("/user/admin").status_code, 403)
        self.assertEqual(self.client.get("/link/devices").status_code, 200)
        self.member.status = UserStatus.GRADUATED
        self.db.commit()
        for path in ("/user/me", "/user/admin", "/link/devices"):
            self.assertEqual(self.client.get(path).status_code, 401)

    def test_invalid_expired_and_mismatched_tokens(self):
        tokens = ["not-a-token",
                  auth.create_access_token({"sub": "member"}, timedelta(seconds=-1)),
                  auth.create_access_token({"sub": "member", "id": self.admin.id}, timedelta(minutes=1))]
        for token in tokens:
            self.client.cookies.clear()
            self.client.cookies.set("access_token", token)
            self.assertEqual(self.client.get("/user/me").status_code, 401)

    def test_me_whitelists_public_fields_and_logout_expires_cookie(self):
        self.authenticate()
        data = self.client.get("/user/me").json()
        self.assertNotIn("password", data)
        self.assertNotIn("accounts", data)
        self.assertEqual(set(data), set(user.USER_PUBLIC_FIELDS) | {"public_key_revision"})
        self.assertRegex(data["public_key_revision"], r"^[0-9a-f]{64}$")
        response = self.client.post("/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        # Real login cookies (same domain/path) are actually removed by logout.
        self.client.cookies.clear()
        self.client.post("/auth/login", data={"username": "member", "password": PASSWORD})
        self.client.post("/auth/logout")
        self.assertEqual(self.client.get("/user/me").status_code, 401)

    def test_legacy_login_upgrades_and_new_hash_keeps_working(self):
        response = self.client.post("/auth/login", data={"username": " MEMBER ", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.db.refresh(self.member)
        self.assertTrue(self.member.password.startswith("$pbkdf2-sha256$"))
        self.assertTrue(auth.verify_password(PASSWORD, self.member.password))
        upgraded = self.member.password
        self.assertEqual(self.client.post("/auth/login", data={"username": "member", "password": PASSWORD}).status_code, 200)
        self.db.refresh(self.member)
        self.assertEqual(self.member.password, upgraded)
        self.assertFalse(auth.verify_password(PASSWORD, "invalid-hash"))

    def test_unsuccessful_or_inactive_login_does_not_upgrade(self):
        for password, state in [("wrong", UserStatus.ACTIVE), (PASSWORD, UserStatus.VERIFYING),
                                (PASSWORD, UserStatus.GRADUATED)]:
            self.member.status = state
            self.db.commit()
            self.assertEqual(self.client.post("/auth/login", data={"username": "member", "password": password}).status_code, 401)
            self.db.refresh(self.member)
            self.assertEqual(self.member.password, LEGACY_HASH)

    def test_registration_validation_and_salted_hashes(self):
        for value in ("", "   ", "-root", "a;id", "a$(id)", "a/b", "a b", "a\n", "a" * 33):
            with self.subTest(account=value):
                self.assertEqual(self.registration(account_name=value).status_code, 422)
        self.assertEqual(self.registration(username="  ").status_code, 422)
        self.assertEqual(self.registration(password="short").status_code, 422)
        response = self.registration(username=" NEWUSER ", account_name="new_user-1")
        self.assertEqual(response.status_code, 201, response.text)
        registered = self.db.query(User).filter_by(username="newuser").one()
        self.assertTrue(auth.verify_password(PASSWORD, registered.password))
        self.assertNotEqual(registered.password, auth.get_password_hash(PASSWORD))
        self.assertEqual(registered.status, UserStatus.VERIFYING)

    def test_duplicate_email_registration_and_profile_are_friendly(self):
        self.assertEqual(self.registration(mail="MEMBER@example.com").status_code, 409)
        self.authenticate()
        response = self.client.post("/user/update", json={
            "id": self.member.id, "realname": "Member", "mail": self.admin.mail,
            "public_key": "", "old_password": PASSWORD})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.member.mail, "member@example.com")
        self.assertEqual(self.client.post("/user/update", json={
            "id": self.member.id, "realname": "Member", "mail": self.member.mail,
            "public_key": "", "old_password": PASSWORD, "new_password": "short"}).status_code, 422)

    def test_application_authorization_active_target_and_duplicates(self):
        payload = {"uid": self.admin.id, "server_id": self.server.id, "need_sudo": True}
        with patch.object(application.logger, "info") as log:
            self.assertEqual(self.client.post("/app/submit", json=payload).status_code, 401)
            self.authenticate()
            self.assertEqual(self.client.post("/app/submit", json=payload).status_code, 403)
            log.assert_not_called()
        payload["uid"] = self.member.id
        self.assertEqual(self.client.post("/app/submit", json=payload).status_code, 201)
        self.assertEqual(self.client.post("/app/submit", json=payload).status_code, 409)
        self.assertEqual(self.db.query(Application).count(), 1)
        self.authenticate(self.admin)
        self.member.status = UserStatus.GRADUATED
        self.db.commit()
        self.assertEqual(self.client.post("/app/submit", json=payload).status_code, 409)
        pending = self.db.query(Application).one()
        self.assertEqual(self.client.post(f"/app/{pending.id}/approve").status_code, 409)
        self.assertEqual(self.db.query(Account).count(), 0)

    def test_admin_grant_resolves_pending_application(self):
        self.authenticate()
        payload = {"uid": self.member.id, "server_id": self.server.id, "need_sudo": False}
        self.client.post("/app/submit", json=payload)
        self.authenticate(self.admin)
        self.assertEqual(self.client.post("/app/submit", json=payload).status_code, 201)
        self.assertEqual(self.db.query(Application).count(), 0)
        self.assertEqual(self.db.query(Account).count(), 1)

    def test_last_admin_and_self_demotion_are_rejected(self):
        self.authenticate(self.admin)
        for action in ("revoke-admin", "graduate"):
            response = self.client.post(f"/user/user/{self.admin.id}/{action}")
            self.assertEqual(response.status_code, 409)
        self.make_user("otheradmin", is_admin=True)
        self.assertEqual(self.client.post(f"/user/user/{self.admin.id}/revoke-admin").status_code, 403)
        self.db.refresh(self.admin)
        self.assertTrue(self.admin.is_admin)
        self.assertEqual(self.admin.status, UserStatus.ACTIVE)

    def test_gateway_permissions_and_account_metadata(self):
        self.authenticate(self.admin)
        for target, requested, expected in [(self.member, True, False), (self.admin, False, True)]:
            response = self.client.post("/app/submit", json={
                "uid": target.id, "server_id": self.gateway.id, "need_sudo": requested})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["is_sudo"], expected)
        acct = self.db.query(Account).filter_by(user_id=self.member.id).one()
        for action in ("sudo", "revoke"):
            self.assertEqual(self.client.put(f"/account/{acct.id}/{action}").status_code, 409)
        self.assertTrue(acct.is_login_able)
        self.assertFalse(acct.is_sudo)
        self.client.post(f"/user/user/{self.member.id}/grant-admin")
        self.db.refresh(acct)
        self.assertTrue(acct.is_sudo)
        self.assertEqual(acct.status, AccountStatus.DIRTY)
        self.client.post(f"/user/user/{self.member.id}/revoke-admin")
        self.db.refresh(acct)
        self.assertFalse(acct.is_sudo)
        self.authenticate()
        result = self.client.get("/user/accounts").json()[0]
        self.assertTrue(result["is_gateway"])
        self.assertFalse(result["is_user_admin"])
        self.assertEqual(self.client.post("/app/submit", json={
            "uid": self.member.id, "server_id": self.gateway.id, "need_sudo": True}).status_code, 409)

    def test_graduate_and_restore_update_gateway_access(self):
        self.authenticate(self.admin)
        acct = Account(user=self.member, server=self.gateway)
        self.db.add(acct)
        self.db.commit()
        self.assertEqual(self.client.post(f"/user/user/{self.member.id}/graduate").status_code, 200)
        self.db.refresh(acct)
        self.assertFalse(acct.is_login_able)
        self.assertFalse(acct.is_sudo)
        self.assertEqual(self.client.post(f"/user/user/{self.member.id}/restore").status_code, 200)
        self.db.refresh(acct)
        self.assertTrue(acct.is_login_able)
        self.assertFalse(acct.is_sudo)

    def test_approval_enforces_gateway_sudo_from_user_role(self):
        self.authenticate(self.admin)
        pending = Application(user=self.member, server=self.gateway, need_sudo=True)
        self.db.add(pending)
        self.db.commit()
        self.assertEqual(self.client.post(f"/app/{pending.id}/approve").status_code, 200)
        self.assertFalse(self.db.query(Account).one().is_sudo)

    def test_normal_accounts_remain_manageable(self):
        acct = Account(user=self.member, server=self.server)
        self.db.add(acct)
        self.db.commit()
        self.authenticate(self.admin)
        self.assertEqual(self.client.put(f"/account/{acct.id}/sudo").status_code, 204)
        self.db.refresh(acct)
        self.assertTrue(acct.is_sudo)
        self.assertEqual(self.client.put(f"/account/{acct.id}/revoke").status_code, 204)
        self.db.refresh(acct)
        self.assertFalse(acct.is_login_able)
        self.assertFalse(acct.is_sudo)

    def test_switch_dimensions_and_success(self):
        self.authenticate(self.admin)
        for row, col in [(0, 2), (-1, 2), (2, 0), (True, 2), (1.5, 2), (1000, 1000)]:
            self.assertEqual(self.client.post("/switch/add", json={
                "name": "invalid", "num_row": row, "num_col": col}).status_code, 422)
        self.assertEqual(self.db.query(Switch).count(), 0)
        response = self.client.post("/switch/add", json={"name": "ok", "num_row": 2, "num_col": 3})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.db.query(SwitchPort).count(), 6)

    def test_switch_port_failure_rolls_back_switch(self):
        self.authenticate(self.admin)
        def fail_insert(*args):
            raise SQLAlchemyError("simulated port insert failure")
        event.listen(SwitchPort, "before_insert", fail_insert)
        try:
            response = self.client.post("/switch/add", json={"name": "broken", "num_row": 2, "num_col": 3})
        finally:
            event.remove(SwitchPort, "before_insert", fail_insert)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.db.query(Switch).count(), 0)
        self.assertEqual(self.db.query(SwitchPort).count(), 0)

    def test_connections_replace_deduplicate_and_detach_all_old_peers(self):
        self.authenticate(self.admin)
        a, b, c, d = self.ports()
        self.assertEqual(self.connect_ports(a, b).status_code, 200)
        self.assertEqual(self.connect_ports(a, b).status_code, 200)
        self.assertEqual(self.db.query(Connection).count(), 1)
        self.assertEqual(self.connect_ports(c, d).status_code, 200)
        self.assertEqual(self.connect_ports(a, c).status_code, 200)
        self.db.expire_all()
        self.assertEqual(self.db.query(Connection).count(), 1)
        self.assertEqual(a.conn_id, c.conn_id)
        self.assertIsNone(b.conn_id)
        self.assertIsNone(d.conn_id)
        self.assertEqual(self.client.get("/link/devices").status_code, 200)
        self.assertEqual(self.client.post("/link/switch_port/disconnect", json={"switch_port_id": a.id}).status_code, 204)
        self.db.expire_all()
        self.assertIsNone(c.conn_id)
        self.assertEqual(self.db.query(Connection).count(), 0)

    def test_connection_replacement_and_deletion_failure_roll_back(self):
        self.authenticate(self.admin)
        a, b, c, d = self.ports()
        self.connect_ports(a, b)
        self.connect_ports(c, d)
        original = [a.conn_id, b.conn_id, c.conn_id, d.conn_id]
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("simulated failure")):
            self.assertEqual(self.connect_ports(a, c).status_code, 500)
        self.db.expire_all()
        self.assertEqual([a.conn_id, b.conn_id, c.conn_id, d.conn_id], original)
        with patch.object(self.db, "commit", side_effect=SQLAlchemyError("simulated failure")):
            self.assertEqual(self.client.post("/link/switch_port/disconnect", json={"switch_port_id": a.id}).status_code, 500)
        self.db.expire_all()
        self.assertEqual([a.conn_id, b.conn_id, c.conn_id, d.conn_id], original)
        self.assertEqual(self.db.query(Connection).count(), 2)

    def test_interface_connections_use_same_atomic_replacement(self):
        self.authenticate(self.admin)
        a, b = self.ports(2)
        interfaces = [ServerInterface(server=self.server, interface=f"eth{i}", manufacturer="test", pci_address=str(i)) for i in range(2)]
        self.db.add_all(interfaces)
        self.db.commit()
        ia, ib = interfaces
        data = {"interface_a_id": ia.id, "interface_b_id": ib.id}
        for _ in range(2):
            self.assertEqual(self.client.post("/link/interface/connect", json=data).status_code, 200)
        data = {"switch_port_id": a.id, "interface_id": ia.id}
        for _ in range(2):
            self.assertEqual(self.client.post("/link/switch_port/interface/connect", json=data).status_code, 200)
        self.db.expire_all()
        self.assertIsNone(ib.conn_id)
        self.assertEqual(a.conn_id, ia.conn_id)
        self.assertEqual(self.db.query(Connection).count(), 1)
        self.assertEqual(self.client.post("/link/interface/disconnect", json={"interface_id": ia.id}).status_code, 204)
        self.db.expire_all()
        self.assertIsNone(a.conn_id)

    def test_missing_connection_is_an_actionable_conflict(self):
        self.authenticate(self.admin)
        a, b = self.ports(2)
        # Emulate a legacy SQLite database populated without FK enforcement.
        self.db.commit()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.exec_driver_sql("UPDATE switch_port SET conn_id=99999 WHERE id=?", (a.id,))
        self.db.expire_all()
        response = self.client.get("/link/devices")
        self.assertEqual(response.status_code, 409)
        self.assertIn("99999", response.json()["detail"])
        self.assertEqual(self.connect_ports(a, b).status_code, 409)
        self.assertEqual(self.db.query(Connection).count(), 0)

    def test_inconsistent_connection_is_actionable_not_process_exit(self):
        self.authenticate()
        a, b = self.ports(2)
        conn = Connection()
        a.conn = conn
        self.db.add(conn)
        self.db.commit()
        response = self.client.get("/link/devices")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Disconnect and reconnect", response.json()["detail"])
        # Administrator can repair a one-ended connection by replacing it.
        self.authenticate(self.admin)
        self.assertEqual(self.connect_ports(a, b).status_code, 200)
        self.assertEqual(self.client.get("/link/devices").status_code, 200)


if __name__ == "__main__":
    unittest.main()
