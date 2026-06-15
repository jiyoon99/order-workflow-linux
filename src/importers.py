from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from uuid import uuid4

from excel import read_first_sheet


def _first(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key, "")).strip()
        if value:
            return value
    return ""


def _number(value: str, fallback: int = 0) -> int:
    try:
        cleaned = re.sub(r"[^0-9.-]", "", str(value).replace(",", ""))
        return int(float(cleaned)) if cleaned else fallback
    except (TypeError, ValueError):
        return fallback


def _collected_orders(rows: list[dict[str, str]], source_file: str) -> list[dict]:
    groups: list[list[dict[str, str]]] = []
    for row in rows:
        starts_order = bool(_first(row, "주문일시", "수취인 이름"))
        if starts_order or not groups:
            groups.append([row])
        else:
            groups[-1].append(row)

    orders = []
    for group_index, group in enumerate(groups, 1):
        first = group[0]
        product_lines = [_first(row, "상품명 + 옵션명") for row in group if _first(row, "상품명 + 옵션명")]
        product_name = product_lines[0] if product_lines else ""
        extra_options = product_lines[1:]
        registered_option = _first(first, "등록옵션명")
        option_name = " / ".join([value for value in [registered_option, *extra_options] if value])
        signature = "|".join([
            _first(first, "플랫폼"), _first(first, "주문일시"), _first(first, "수취인 이름"),
            product_name, option_name, str(group_index),
        ])
        short_id = hashlib.sha256(signature.encode()).hexdigest()[:10].upper()
        order_number = f"수집-{short_id}"
        orders.append({
            "importKey": f"주문수집:{short_id}",
            "channel": _first(first, "플랫폼") or "기타",
            "sourceFile": source_file,
            "orderNumber": order_number,
            "orderedAt": _first(first, "주문일시"),
            "productName": product_name,
            "optionName": option_name,
            "productCode": registered_option,
            "quantity": _number(_first(first, "수량"), 1),
            "amount": _number(_first(first, "총 상품결제금액")),
            "recipient": _first(first, "수취인 이름"),
            "phone": "",
            "postalCode": "",
            "address": "",
            "deliveryMessage": _first(first, "배송메세지", "배송메시지"),
            "courier": "",
            "trackingNumber": "",
        })
    return orders


def _kakao(row: dict[str, str], source_file: str) -> dict:
    order_number = _first(row, "주문번호", "결제번호", "주문 번호")
    return {
        "importKey": f"카카오:{order_number}:{_first(row, '채널상품번호', '판매자상품번호')}:{_first(row, '옵션')}",
        "channel": "카카오", "sourceFile": source_file, "orderNumber": order_number,
        "orderedAt": _first(row, "주문일", "주문일시", "결제일시"), "productName": _first(row, "상품명", "주문상품명"),
        "optionName": _first(row, "옵션", "옵션명"), "productCode": _first(row, "판매자상품번호", "채널상품번호", "상품코드"),
        "quantity": _number(_first(row, "수량", "주문수량"), 1), "amount": _number(_first(row, "정산기준금액", "상품금액", "결제금액")),
        "recipient": _first(row, "수령인명", "수령인", "받는분"), "phone": _first(row, "하이픈포함 수령인연락처1", "수령인연락처1", "수령인연락처", "연락처"),
        "postalCode": _first(row, "우편번호", "배송지우편번호"), "address": _first(row, "배송지주소", "주소", "배송주소"),
        "deliveryMessage": _first(row, "배송메세지", "배송메시지"), "courier": _first(row, "택배사코드", "택배사"),
        "trackingNumber": _first(row, "송장번호"),
    }


def _coupang(row: dict[str, str], source_file: str) -> dict:
    order_number = _first(row, "주문번호", "주문 번호")
    return {
        "importKey": f"쿠팡:{order_number}:{_first(row, '옵션ID', '노출상품ID')}",
        "channel": "쿠팡", "sourceFile": source_file, "orderNumber": order_number,
        "orderedAt": _first(row, "주문일", "주문일시"), "productName": _first(row, "등록상품명", "노출상품명(옵션명)", "상품명"),
        "optionName": _first(row, "등록옵션명", "옵션명", "옵션"), "productCode": _first(row, "업체상품코드", "옵션ID", "노출상품ID", "상품코드"),
        "quantity": _number(_first(row, "구매수(수량)", "수량", "주문수량"), 1), "amount": _number(_first(row, "결제액", "결제금액", "상품금액")),
        "recipient": _first(row, "수취인이름", "수령인", "받는분"), "phone": _first(row, "수취인전화번호", "연락처", "수령인연락처"),
        "postalCode": _first(row, "우편번호", "배송지우편번호"), "address": _first(row, "수취인 주소", "주소", "배송주소"),
        "deliveryMessage": _first(row, "배송메세지", "배송메시지"), "courier": _first(row, "택배사"),
        "trackingNumber": _first(row, "운송장번호"),
    }


def _godomall(row: dict[str, str], source_file: str) -> dict:
    order_number = _first(row, "주문 번호")
    address = " ".join(value for value in [
        _first(row, "수취인 주소"), _first(row, "수취인 나머지 주소"),
    ] if value)
    return {
        "importKey": f"고도몰:{order_number}:{_first(row, '상품주문번호', '주문코드(순서)')}:{_first(row, '상품코드')}",
        "channel": "고도몰", "sourceFile": source_file, "orderNumber": order_number,
        "orderedAt": _first(row, "주문일자"), "productName": _first(row, "상품명", "주문 상품명"),
        "optionName": _first(row, "옵션정보", "텍스트옵션정보"), "productCode": _first(row, "자체상품코드", "상품코드"),
        "quantity": _number(_first(row, "상품수량"), 1), "amount": _number(_first(row, "판매가", "총 결제 금액")),
        "recipient": _first(row, "수취인 이름"), "phone": _first(row, "수취인 핸드폰 번호", "수취인 전화번호"),
        "postalCode": _first(row, "수취인 구 우편번호 (6자리)"), "address": address,
        "deliveryMessage": _first(row, "주문시 남기는 글"), "courier": _first(row, "배송 업체 번호"),
        "trackingNumber": _first(row, "송장 번호"),
    }


def _godomall_orders(rows: list[dict[str, str]], source_file: str) -> list[dict]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        order_number = _first(row, "주문 번호")
        if order_number:
            groups.setdefault(order_number, []).append(row)

    orders = []
    for order_number, items in groups.items():
        main = next((row for row in items if not _first(row, "상품명").startswith("[추가]")), items[0])
        order = _godomall(main, source_file)
        additions = []
        for row in items:
            if row is main:
                continue
            name = _first(row, "상품명")
            if not name:
                continue
            quantity = _number(_first(row, "상품수량"), 1)
            additions.append(f"{name} x{quantity}" if quantity > 1 else name)
        order["optionName"] = " / ".join(value for value in [order["optionName"], *additions] if value)
        order["amount"] = _number(_first(main, "총 결제 금액"), order["amount"])
        order["importKey"] = f"고도몰:{order_number}"
        orders.append(order)
    return orders


def import_workbook(content: bytes, source_file: str) -> list[dict]:
    rows = read_first_sheet(content, source_file)
    if not rows:
        raise ValueError("엑셀에 주문 데이터가 없습니다.")
    headers = set(rows[0].keys())
    if {"플랫폼", "상품명 + 옵션명", "수취인 이름"} <= headers:
        imported = _collected_orders(rows, source_file)
        importer = None
    elif {"주문 번호", "상품명", "상품수량", "수취인 이름"} <= headers:
        imported = _godomall_orders(rows, source_file)
        importer = None
    elif ({"결제번호", "채널상품번호"} <= headers) or "수령인명" in headers:
        importer = _kakao
    elif ({"묶음배송번호", "수취인이름"} <= headers) or "수취인이름" in headers:
        importer = _coupang
    elif ({"주문번호", "상품명"} <= headers or {"주문 번호", "상품명"} <= headers) and headers.intersection({"수령인", "받는분", "수령인명", "수취인이름"}):
        importer = _kakao
    else:
        detected = ", ".join(list(headers)[:12]) or "열 이름 없음"
        raise ValueError(f"지원하지 않는 엑셀 양식입니다. 감지된 열: {detected}")

    now = datetime.now(timezone.utc).isoformat()
    orders = []
    source_orders = imported if importer is None else (importer(row, source_file) for row in rows)
    for order in source_orders:
        if not order["orderNumber"] or not order["productName"]:
            continue
        order.update({
            "id": str(uuid4()), "managementNumber": "",
            "preparing": False, "preparingBy": "", "preparingAt": "",
            "productionDone": False, "productionBy": "", "productionAt": "",
            "shippingDone": False, "shippingBy": "", "shippingAt": "", "createdAt": now, "updatedAt": now,
        })
        orders.append(order)
    if not orders:
        raise ValueError("주문번호와 상품명이 있는 주문 행을 찾지 못했습니다.")
    return orders
