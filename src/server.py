from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import io
import zipfile
from datetime import date, datetime, timezone
from email.parser import BytesParser
from email.policy import default
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from excel import write_xlsx
from importers import import_workbook
from auth import AuthStore, normalize_role

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
DATA_FILE = Path(os.getenv("DATA_FILE", ROOT / "data" / "orders.json"))
USERS_FILE = Path(os.getenv("USERS_FILE", ROOT / "data" / "users.json"))
AUDIT_FILE = Path(os.getenv("AUDIT_FILE", ROOT / "data" / "audit.jsonl"))
LOCK = threading.Lock()
AUDIT_LOCK = threading.Lock()
AUTH = AuthStore(USERS_FILE)
MEMBER_MANAGEMENT_ROLES = {"owner", "developer"}
ORDER_ADMIN_ROLES = {"owner", "developer", "sales_manager", "md"}
AS_HISTORY_ROLES = {"owner", "developer", "as_manager"}
ROLE_RANK = {"owner": 0, "developer": 1, "as_manager": 2, "sales_manager": 3, "md": 4, "worker": 5}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_ARCHIVE_FILES = 20
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
BACKUP_RETENTION_DAYS = 14
LOGIN_FAILURE_LIMIT = 5
LOGIN_BLOCK_SECONDS = 5 * 60
LOGIN_LOCK = threading.Lock()
LOGIN_FAILURES: dict[str, list[float]] = {}


class OrderHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64


def user_role(user: dict) -> str:
    return normalize_role(str(user.get("role", "worker")))


def read_orders() -> list[dict]:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def backup_file(file_path: Path) -> None:
    if not file_path.exists():
        return
    backup_dir = file_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    backup = backup_dir / f"{file_path.name}.{today}.bak"
    if not backup.exists():
        shutil.copy2(file_path, backup)
    cutoff = time.time() - BACKUP_RETENTION_DAYS * 24 * 60 * 60
    for old_backup in backup_dir.glob(f"{file_path.name}.*.bak"):
        if old_backup.stat().st_mtime < cutoff:
            old_backup.unlink()


def write_audit(event: str, user: dict | None = None, **details: object) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "userId": user.get("id", "") if user else "",
        "displayName": user.get("displayName", "") if user else "",
        **details,
    }
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOCK, AUDIT_FILE.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def login_key(client_ip: str, username: str) -> str:
    return f"{client_ip}:{username.strip().lower()}"


def login_blocked(key: str, now: float | None = None) -> bool:
    current = now if now is not None else time.time()
    with LOGIN_LOCK:
        failures = [item for item in LOGIN_FAILURES.get(key, []) if current - item < LOGIN_BLOCK_SECONDS]
        LOGIN_FAILURES[key] = failures
        return len(failures) >= LOGIN_FAILURE_LIMIT


def record_login_result(key: str, success: bool, now: float | None = None) -> None:
    with LOGIN_LOCK:
        if success:
            LOGIN_FAILURES.pop(key, None)
        else:
            LOGIN_FAILURES.setdefault(key, []).append(now if now is not None else time.time())


def write_orders(orders: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    backup_file(DATA_FILE)
    temporary = DATA_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(orders, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(DATA_FILE)


def archive_excel_files(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    files = [
        item for item in archive.infolist()
        if not item.is_dir()
        and item.filename.lower().endswith((".xlsx", ".xls"))
        and not item.filename.startswith("__MACOSX/")
    ]
    if not files:
        raise ValueError("ZIP 안에 .xlsx 또는 .xls 파일이 없습니다.")
    if len(files) > MAX_ARCHIVE_FILES:
        raise ValueError(f"ZIP 안의 엑셀 파일은 최대 {MAX_ARCHIVE_FILES}개까지 지원합니다.")
    if sum(item.file_size for item in files) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ValueError("ZIP 압축 해제 크기는 최대 100MB입니다.")
    return files


def order_number_key(order: dict) -> list[tuple[int, object]]:
    value = str(order.get("orderNumber", ""))
    return [(0, int(part)) if part.isdigit() else (1, part.lower()) for part in re.split(r"(\d+)", value)]


def order_datetime_key(order: dict) -> tuple[str, str]:
    ordered_at = str(order.get("orderedAt", "")).strip().replace("/", "-").replace(".", "-")
    ordered_at = re.sub(r"\s+", " ", ordered_at)
    digits = re.sub(r"\D", "", ordered_at)
    if len(digits) >= 14:
        normalized = digits[:14]
    elif len(digits) == 8:
        normalized = f"{digits}000000"
    else:
        normalized = ordered_at
    order_number = re.sub(r"\d+", lambda match: match.group(0).zfill(24), str(order.get("orderNumber", "")).lower())
    return normalized, order_number


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, status: int, payload: object, headers: dict[str, str] | None = None) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def _session_token(self) -> str:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["halfbook_session"].value if "halfbook_session" in cookie else ""

    def _current_user(self) -> dict | None:
        return AUTH.user_for_token(self._session_token())

    def _require_user(self) -> dict | None:
        user = self._current_user()
        if not user: self._json(401, {"error": "로그인이 필요합니다."})
        return user

    def _require_member_manager(self) -> dict | None:
        user = self._require_user()
        if user and user_role(user) not in MEMBER_MANAGEMENT_ROLES:
            self._json(403, {"error": "총책임자 또는 개발자만 회원을 관리할 수 있습니다."})
            return None
        return user

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("업로드 크기는 최대 30MB입니다.")
        return self.rfile.read(length)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json(200, {"ok": True})
        if path == "/api/auth/status":
            return self._json(200, {"setupRequired": len(AUTH.read_users()) == 0, "user": self._current_user()})
        if path == "/api/users":
            if not self._require_member_manager(): return
            return self._json(200, [AUTH.public_user(item) for item in AUTH.read_users()])
        if path.startswith("/api/") and not self._require_user(): return
        if path == "/api/orders/as-history":
            user = self._current_user()
            if not user or user_role(user) not in AS_HISTORY_ROLES:
                return self._json(403, {"error": "AS 출고 이력은 총책임자, 개발자, AS 담당자만 조회할 수 있습니다."})
            orders = sorted(
                (item for item in read_orders() if item.get("shippingDone")),
                key=lambda order: (str(order.get("shippingAt", "")), order_datetime_key(order)),
            )
            return self._json(200, orders)
        if path == "/api/orders/cancelled":
            orders = sorted(
                (item for item in read_orders() if item.get("cancelledAt")),
                key=order_datetime_key,
            )
            return self._json(200, orders)
        if path == "/api/orders/archived":
            orders = sorted(
                (item for item in read_orders() if item.get("archivedAt")),
                key=order_datetime_key,
            )
            return self._json(200, orders)
        if path == "/api/orders":
            orders = sorted(
                (item for item in read_orders() if not item.get("archivedAt") and not item.get("cancelledAt")),
                key=order_datetime_key,
            )
            return self._json(200, orders)
        if path == "/api/export/shipped":
            user = self._require_user()
            if not user: return
            if user_role(user) not in ORDER_ADMIN_ROLES: return self._json(403, {"error": "해당 권한으로 출고 엑셀을 조회할 수 없습니다."})
            return self._export_shipped(archive=False)
        return super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/auth/setup":
            if AUTH.read_users(): return self._json(409, {"error": "관리자 설정이 이미 완료됐습니다."})
            return self._create_user(role="owner", login_after=True)
        if path == "/api/auth/register":
            if not AUTH.read_users():
                return self._json(409, {"error": "먼저 최초 관리자 계정을 설정하세요."})
            return self._create_user(role="worker", login_after=False)
        if path == "/api/auth/login":
            try:
                payload = json.loads(self._body())
                username = str(payload.get("username", ""))
                key = login_key(self.client_address[0], username)
                if login_blocked(key):
                    return self._json(429, {"error": "로그인 시도가 너무 많습니다. 5분 후 다시 시도하세요."}, {"Retry-After": str(LOGIN_BLOCK_SECONDS)})
                authenticated = AUTH.authenticate(username, str(payload.get("password", "")))
                record_login_result(key, authenticated is not None)
                if not authenticated:
                    write_audit("login_failed", username=username.strip().lower(), clientIp=self.client_address[0])
                    return self._json(401, {"error": "아이디 또는 비밀번호가 올바르지 않습니다."})
                token, user = authenticated
                write_audit("login_succeeded", user, clientIp=self.client_address[0])
                return self._json(200, {"user": user}, {"Set-Cookie": f"halfbook_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"})
            except json.JSONDecodeError: return self._json(400, {"error": "로그인 정보를 확인하세요."})
        if path == "/api/auth/logout":
            AUTH.logout(self._session_token())
            return self._json(200, {"ok": True}, {"Set-Cookie": "halfbook_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
        user = self._require_user()
        if not user: return
        if path == "/api/users":
            if user_role(user) not in MEMBER_MANAGEMENT_ROLES: return self._json(403, {"error": "총책임자 또는 개발자만 계정을 추가할 수 있습니다."})
            return self._create_user(role=None, login_after=False, current_user=user)
        if path == "/api/orders/manual":
            if user_role(user) not in ORDER_ADMIN_ROLES: return self._json(403, {"error": "해당 권한으로 수기 주문을 등록할 수 없습니다."})
            return self._create_manual_order(user)
        if path == "/api/export/shipped":
            if user_role(user) not in ORDER_ADMIN_ROLES: return self._json(403, {"error": "해당 권한으로 출고 완료 엑셀을 만들 수 없습니다."})
            return self._export_shipped(archive=True)
        if path != "/api/import":
            return self._json(404, {"error": "요청 경로를 찾을 수 없습니다."})
        if user_role(user) not in ORDER_ADMIN_ROLES:
            return self._json(403, {"error": "해당 권한으로 주문 엑셀을 가져올 수 없습니다."})
        try:
            content_type = self.headers.get("Content-Type", "")
            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + self._body()
            )
            files = [part for part in message.iter_attachments() if part.get_filename()]
            if not files:
                return self._json(400, {"error": "엑셀 파일을 선택하세요."})
            imported, errors = [], []
            for part in files[:10]:
                filename = Path(part.get_filename()).name
                try:
                    content = part.get_payload(decode=True)
                    if filename.lower().endswith((".xlsx", ".xls")):
                        imported.extend(import_workbook(content, filename))
                    elif filename.lower().endswith(".zip"):
                        with zipfile.ZipFile(io.BytesIO(content)) as archive:
                            for excel_file in archive_excel_files(archive):
                                imported.extend(import_workbook(archive.read(excel_file), Path(excel_file.filename).name))
                    else:
                        raise ValueError(".xlsx, .xls 또는 .zip 파일만 지원합니다.")
                except Exception as error:
                    errors.append(f"{filename}: {error}")
            if not imported:
                return self._json(400, {"error": "\n".join(errors)})
            with LOCK:
                orders = read_orders()
                keys = {order["importKey"] for order in orders}
                added = [order for order in imported if order["importKey"] not in keys]
                orders.extend(added)
                write_orders(orders)
            write_audit("orders_imported", user, added=len(added), duplicates=len(imported) - len(added), files=len(files))
            return self._json(200, {"added": len(added), "duplicates": len(imported) - len(added), "errors": errors})
        except ValueError as error:
            return self._json(400, {"error": str(error)})
        except Exception:
            return self._json(500, {"error": "엑셀 처리 중 오류가 발생했습니다."})

    def _create_user(self, role: str | None, login_after: bool, current_user: dict | None = None) -> None:
        try:
            payload = json.loads(self._body())
            now = datetime.now(timezone.utc).isoformat()
            selected_role = normalize_role(role or str(payload.get("role", "worker")))
            if current_user and user_role(current_user) == "developer" and ROLE_RANK.get(selected_role, 999) <= ROLE_RANK["developer"]:
                return self._json(403, {"error": "개발자는 총책임자나 개발자 권한을 지정할 수 없습니다."})
            user = AUTH.create_user(str(payload.get("username", "")), str(payload.get("displayName", "")), str(payload.get("password", "")), selected_role, now)
            if login_after:
                token, user = AUTH.authenticate(str(payload.get("username", "")), str(payload.get("password", "")))
                return self._json(201, {"user": user}, {"Set-Cookie": f"halfbook_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"})
            return self._json(201, user)
        except ValueError as error: return self._json(400, {"error": str(error)})
        except json.JSONDecodeError: return self._json(400, {"error": "계정 정보를 확인하세요."})

    def _create_manual_order(self, user: dict) -> None:
        try:
            payload = json.loads(self._body())
            order_number = str(payload.get("orderNumber", "")).strip()
            product_name = str(payload.get("productName", "")).strip()
            recipient = str(payload.get("recipient", "")).strip()
            phone = str(payload.get("phone", "")).strip()
            worker = user["displayName"]
            if not all((order_number, product_name, recipient, phone, worker)):
                return self._json(400, {"error": "주문번호, 상품명, 수령인, 연락처, 작업자를 입력하세요."})
            try:
                quantity = max(1, int(payload.get("quantity") or 1))
                amount = max(0, int(payload.get("amount") or 0))
            except (TypeError, ValueError):
                return self._json(400, {"error": "수량과 금액은 숫자로 입력하세요."})
            now = datetime.now(timezone.utc).isoformat()
            with LOCK:
                orders = read_orders()
                if any(str(order.get("orderNumber", "")).strip() == order_number for order in orders):
                    return self._json(409, {"error": "이미 등록된 주문번호입니다."})
                order = {
                    "id": str(uuid4()), "importKey": f"전화주문:{order_number}", "channel": "전화주문",
                    "sourceFile": "수기입력", "orderNumber": order_number,
                    "orderedAt": str(payload.get("orderedAt", "")).strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "productName": product_name, "optionName": str(payload.get("optionName", "")).strip(),
                    "productCode": str(payload.get("productCode", "")).strip(), "quantity": quantity, "amount": amount,
                    "recipient": recipient, "phone": phone, "postalCode": str(payload.get("postalCode", "")).strip(),
                    "address": str(payload.get("address", "")).strip(),
                    "deliveryMessage": str(payload.get("deliveryMessage", "")).strip(), "courier": "", "trackingNumber": "",
                    "managementNumber": "", "preparing": False, "preparingBy": "", "preparingAt": "",
                    "productionDone": False, "productionBy": "", "productionAt": "",
                    "shippingDone": False, "shippingBy": "", "shippingAt": "",
                    "createdBy": worker, "createdAt": now, "updatedAt": now,
                }
                orders.append(order)
                write_orders(orders)
            write_audit("order_created", user, orderId=order["id"], orderNumber=order_number, channel="전화주문")
            return self._json(201, order)
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "수기 주문 내용을 확인하세요."})

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        user = self._require_user()
        if not user: return
        if path.startswith("/api/users/"):
            if user_role(user) not in MEMBER_MANAGEMENT_ROLES: return self._json(403, {"error": "총책임자 또는 개발자만 계정을 수정할 수 있습니다."})
            return self._update_user(path.rsplit("/", 1)[-1], user)
        if not path.startswith("/api/orders/"):
            return self._json(404, {"error": "요청 경로를 찾을 수 없습니다."})
        try:
            payload = json.loads(self._body())
            worker = user["displayName"]
            action, checked = payload.get("action"), bool(payload.get("checked"))
            now = datetime.now(timezone.utc).isoformat()
            with LOCK:
                orders = read_orders()
                order = next((item for item in orders if item["id"] == path.rsplit("/", 1)[-1]), None)
                if not order:
                    return self._json(404, {"error": "주문을 찾을 수 없습니다."})
                if order.get("cancelledAt"):
                    return self._json(409, {"error": "이미 취소된 주문입니다.", "order": order})
                if action == "cancel":
                    if user_role(user) not in ORDER_ADMIN_ROLES:
                        return self._json(403, {"error": "해당 권한으로 주문을 취소할 수 없습니다.", "order": order})
                    reason = str(payload.get("reason", "")).strip()
                    if not reason:
                        return self._json(400, {"error": "취소 사유를 입력하세요.", "order": order})
                    if len(reason) > 500:
                        return self._json(400, {"error": "취소 사유는 500자 이내로 입력하세요.", "order": order})
                    if order.get("shippingDone") or order.get("archivedAt"):
                        return self._json(409, {"error": "출고 완료 또는 보관된 주문은 취소할 수 없습니다.", "order": order})
                    if order.get("productionDone") and user_role(user) not in MEMBER_MANAGEMENT_ROLES:
                        return self._json(403, {"error": "제작 완료 주문은 총책임자 또는 개발자만 취소할 수 있습니다.", "order": order})
                    order.update({
                        "cancelledAt": now, "cancelledBy": worker, "cancelReason": reason,
                        "preparing": False, "preparingBy": "", "preparingAt": "", "updatedAt": now,
                    })
                    write_orders(orders)
                    write_audit("order_cancelled", user, orderId=order["id"], orderNumber=order.get("orderNumber", ""))
                    return self._json(200, order)
                if action == "preparing":
                    if checked and (order.get("productionDone") or order.get("shippingDone")):
                        return self._json(409, {"error": "제작 또는 출고가 완료된 주문은 준비 중으로 되돌릴 수 없습니다.", "order": order})
                    current_worker = str(order.get("preparingBy", "")).strip()
                    if checked and order.get("preparing") and current_worker != worker:
                        return self._json(409, {"error": f"{current_worker} 작업자가 이미 준비 중입니다.", "order": order})
                    if not checked and order.get("preparing") and current_worker and current_worker != worker:
                        return self._json(409, {"error": f"준비 중 상태는 {current_worker} 작업자만 해제할 수 있습니다.", "order": order})
                    order.update({"preparing": checked, "preparingBy": worker if checked else "", "preparingAt": now if checked else ""})
                elif action == "managementNumber":
                    management_number = str(payload.get("managementNumber", "")).strip()
                    if len(management_number) > 100:
                        return self._json(400, {"error": "제품 관리번호는 100자 이내로 입력하세요."})
                    duplicate = next((item for item in orders if item.get("id") != order.get("id") and management_number and str(item.get("managementNumber", "")).strip() == management_number), None)
                    if duplicate:
                        return self._json(409, {"error": f"이미 주문 {duplicate.get('orderNumber', '')}에 등록된 관리번호입니다.", "order": order})
                    order.update({"managementNumber": management_number, "managementNumberBy": worker if management_number else "", "managementNumberAt": now if management_number else ""})
                elif action == "production":
                    if not checked and order.get("shippingDone"):
                        return self._json(409, {"error": "출고 완료된 주문은 제작 완료를 해제할 수 없습니다.", "order": order})
                    current_worker = str(order.get("preparingBy", "")).strip()
                    if checked and order.get("preparing") and current_worker and current_worker != worker:
                        return self._json(409, {"error": f"{current_worker} 작업자가 준비 중인 주문입니다.", "order": order})
                    order.update({
                        "productionDone": checked, "productionBy": worker if checked else "", "productionAt": now if checked else "",
                        "preparing": False if checked else order.get("preparing", False),
                        "preparingBy": "" if checked else order.get("preparingBy", ""),
                        "preparingAt": "" if checked else order.get("preparingAt", ""),
                    })
                elif action == "shipping":
                    if checked and not order.get("productionDone"):
                        return self._json(409, {"error": "제작 완료 후 출고 처리할 수 있습니다.", "order": order})
                    order.update({
                        "shippingDone": checked, "shippingBy": worker if checked else "", "shippingAt": now if checked else "",
                        "courier": str(payload.get("courier", "")).strip(),
                    })
                else:
                    return self._json(400, {"error": "잘못된 처리 요청입니다."})
                order["updatedAt"] = now
                write_orders(orders)
            write_audit("order_updated", user, orderId=order["id"], orderNumber=order.get("orderNumber", ""), action=action, checked=checked)
            return self._json(200, order)
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "요청 내용을 확인하세요."})

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        user = self._require_user()
        if not user: return
        if not path.startswith("/api/users/"):
            return self._json(404, {"error": "요청 경로를 찾을 수 없습니다."})
        if user_role(user) not in MEMBER_MANAGEMENT_ROLES:
            return self._json(403, {"error": "총책임자 또는 개발자만 계정을 삭제할 수 있습니다."})
        user_id = path.rsplit("/", 1)[-1]
        if user_id == user["id"]:
            return self._json(400, {"error": "현재 로그인한 계정은 삭제할 수 없습니다."})
        users = AUTH.read_users()
        target = next((item for item in users if item["id"] == user_id), None)
        if not target:
            return self._json(404, {"error": "사용자를 찾을 수 없습니다."})
        if user_role(user) == "developer" and ROLE_RANK.get(user_role(target), 999) <= ROLE_RANK["developer"]:
            return self._json(403, {"error": "개발자는 총책임자나 다른 개발자 계정을 삭제할 수 없습니다."})
        if user_role(target) == "owner" and sum(user_role(item) == "owner" and item.get("enabled", True) for item in users) <= 1:
            return self._json(400, {"error": "마지막 총책임자 계정은 삭제할 수 없습니다."})
        try:
            deleted = AUTH.delete_user(user_id)
            return self._json(200, deleted)
        except ValueError as error:
            return self._json(400, {"error": str(error)})

    def _update_user(self, user_id: str, current_user: dict) -> None:
        try:
            payload = json.loads(self._body())
            users = AUTH.read_users()
            target = next((item for item in users if item["id"] == user_id), None)
            if not target:
                return self._json(404, {"error": "사용자를 찾을 수 없습니다."})
            role = normalize_role(str(payload.get("role", target.get("role", "worker"))))
            enabled = bool(payload.get("enabled", target.get("enabled", True)))
            if user_role(current_user) == "developer" and (
                ROLE_RANK.get(user_role(target), 999) <= ROLE_RANK["developer"]
                or ROLE_RANK.get(role, 999) <= ROLE_RANK["developer"]
            ):
                return self._json(403, {"error": "개발자는 총책임자나 개발자 권한을 수정 또는 지정할 수 없습니다."})
            if user_id == current_user["id"] and (role not in MEMBER_MANAGEMENT_ROLES or not enabled):
                return self._json(400, {"error": "현재 로그인한 계정의 관리 권한이나 사용 상태는 변경할 수 없습니다."})
            active_owners = sum(user_role(item) == "owner" and item.get("enabled", True) for item in users)
            removes_active_owner = user_role(target) == "owner" and target.get("enabled", True) and (role != "owner" or not enabled)
            if removes_active_owner and active_owners <= 1:
                return self._json(400, {"error": "마지막 총책임자는 다른 권한으로 변경하거나 비활성화할 수 없습니다."})
            updated = AUTH.update_user(
                user_id,
                str(payload.get("username", target.get("username", ""))),
                str(payload.get("displayName", target.get("displayName", ""))),
                role,
                enabled,
                str(payload.get("password", "")),
            )
            return self._json(200, updated)
        except ValueError as error:
            return self._json(400, {"error": str(error)})
        except json.JSONDecodeError:
            return self._json(400, {"error": "계정 정보를 확인하세요."})

    def _export_shipped(self, archive: bool) -> None:
        headers = ["번호", "채널", "주문번호", "주문일", "상품명", "옵션", "수량", "상품코드", "제품관리번호", "수령인", "연락처", "우편번호", "주소", "배송메시지", "택배사", "제작담당자", "제작완료일", "출고담당자", "출고완료일"]
        fields = ["channel", "orderNumber", "orderedAt", "productName", "optionName", "quantity", "productCode", "managementNumber", "recipient", "phone", "postalCode", "address", "deliveryMessage", "courier", "productionBy", "productionAt", "shippingBy", "shippingAt"]
        with LOCK:
            orders = read_orders()
            new_shipped = [order for order in orders if order.get("shippingDone") and not order.get("archivedAt")]
            if archive:
                archived_at = datetime.now(timezone.utc).isoformat()
                for order in new_shipped:
                    order["archivedAt"] = archived_at
                    order["updatedAt"] = archived_at
                if new_shipped:
                    write_orders(orders)
            shipped = [order for order in orders if order.get("shippingDone")]
            if not shipped:
                return self._json(400, {"error": "출고 완료된 주문이 없습니다."})
        shipped.sort(key=lambda order: order.get("shippingAt", ""))
        rows = [[index, *(order.get(field, "") for field in fields)] for index, order in enumerate(shipped, 1)]
        content = write_xlsx(headers, rows)
        if archive:
            write_audit("orders_exported", self._current_user(), count=len(shipped), archived=len(new_shipped))
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="shipped-orders-all-{date.today().isoformat()}.xlsx"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "3000"))
    print(f"Order workflow sample: http://localhost:{port}")
    OrderHTTPServer(("0.0.0.0", port), Handler).serve_forever()
