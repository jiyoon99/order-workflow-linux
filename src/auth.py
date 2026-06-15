from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import shutil
from pathlib import Path
from uuid import uuid4

ROLE_LABELS = {
    "owner": "총책임자",
    "developer": "개발자",
    "as_manager": "AS 담당자",
    "sales_manager": "판매 담당자",
    "md": "MD",
    "worker": "일반 작업자",
}


def normalize_role(role: str) -> str:
    return "owner" if role == "admin" else role


class AuthStore:
    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self.lock = threading.Lock()
        self.session_lock = threading.Lock()
        self.sessions: dict[str, dict] = {}

    def read_users(self) -> list[dict]:
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []

    def write_users(self, users: list[dict]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if self.file_path.exists():
            backup_dir = self.file_path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{self.file_path.name}.{time.strftime('%Y%m%d')}.bak"
            if not backup.exists():
                shutil.copy2(self.file_path, backup)
            cutoff = time.time() - 14 * 24 * 60 * 60
            for old_backup in backup_dir.glob(f"{self.file_path.name}.*.bak"):
                if old_backup.stat().st_mtime < cutoff:
                    old_backup.unlink()
        temporary = self.file_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(users, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.file_path)

    @staticmethod
    def hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"

    @staticmethod
    def verify_password(password: str, encoded: str) -> bool:
        try:
            algorithm, rounds, salt, expected = encoded.split("$")
            if algorithm != "pbkdf2_sha256": return False
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError):
            return False

    def public_user(self, user: dict) -> dict:
        public = {key: user.get(key) for key in ("id", "username", "displayName", "role", "enabled", "createdAt")}
        public["role"] = normalize_role(str(public.get("role", "worker")))
        return public

    def create_user(self, username: str, display_name: str, password: str, role: str, created_at: str) -> dict:
        username = username.strip().lower()
        display_name = display_name.strip()
        if not username or not display_name or len(password) < 8:
            raise ValueError("아이디·작업자명과 8자 이상의 비밀번호를 입력하세요.")
        role = normalize_role(role)
        if role not in ROLE_LABELS: raise ValueError("잘못된 권한입니다.")
        with self.lock:
            users = self.read_users()
            if any(user["username"] == username for user in users): raise ValueError("이미 사용 중인 아이디입니다.")
            user = {"id": str(uuid4()), "username": username, "displayName": display_name, "passwordHash": self.hash_password(password), "role": role, "enabled": True, "createdAt": created_at}
            users.append(user)
            self.write_users(users)
        return self.public_user(user)

    def update_user(self, user_id: str, username: str, display_name: str, role: str, enabled: bool, password: str = "") -> dict:
        username = username.strip().lower()
        display_name = display_name.strip()
        if not username or not display_name:
            raise ValueError("아이디와 이름을 입력하세요.")
        role = normalize_role(role)
        if role not in ROLE_LABELS:
            raise ValueError("잘못된 권한입니다.")
        if password and len(password) < 8:
            raise ValueError("새 비밀번호는 8자 이상이어야 합니다.")
        with self.lock:
            users = self.read_users()
            user = next((item for item in users if item["id"] == user_id), None)
            if not user:
                raise ValueError("사용자를 찾을 수 없습니다.")
            if any(item["id"] != user_id and item["username"] == username for item in users):
                raise ValueError("이미 사용 중인 아이디입니다.")
            user.update({"username": username, "displayName": display_name, "role": role, "enabled": enabled})
            if password:
                user["passwordHash"] = self.hash_password(password)
            self.write_users(users)
        if not enabled:
            self.invalidate_user_sessions(user_id)
        return self.public_user(user)

    def delete_user(self, user_id: str) -> dict:
        with self.lock:
            users = self.read_users()
            user = next((item for item in users if item["id"] == user_id), None)
            if not user:
                raise ValueError("사용자를 찾을 수 없습니다.")
            self.write_users([item for item in users if item["id"] != user_id])
        self.invalidate_user_sessions(user_id)
        return self.public_user(user)

    def invalidate_user_sessions(self, user_id: str) -> None:
        with self.session_lock:
            for token, session in list(self.sessions.items()):
                if session["userId"] == user_id:
                    self.sessions.pop(token, None)

    def authenticate(self, username: str, password: str) -> tuple[str, dict] | None:
        username = username.strip().lower()
        user = next((item for item in self.read_users() if item["username"] == username and item.get("enabled", True)), None)
        if not user or not self.verify_password(password, user.get("passwordHash", "")): return None
        token = secrets.token_urlsafe(32)
        with self.session_lock:
            self.sessions[token] = {"userId": user["id"], "expiresAt": time.time() + 12 * 60 * 60}
        return token, self.public_user(user)

    def user_for_token(self, token: str) -> dict | None:
        with self.session_lock:
            session = self.sessions.get(token)
            if not session: return None
            if session["expiresAt"] < time.time():
                self.sessions.pop(token, None)
                return None
        user = next((item for item in self.read_users() if item["id"] == session["userId"] and item.get("enabled", True)), None)
        return self.public_user(user) if user else None

    def logout(self, token: str) -> None:
        with self.session_lock:
            self.sessions.pop(token, None)
