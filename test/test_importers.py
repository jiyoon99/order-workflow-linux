import sys
import unittest
import io
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from excel import read_first_sheet, write_xlsx
from importers import import_workbook

COLLECTED_SAMPLE = Path(os.getenv("COLLECTED_SAMPLE_FILE", "test/fixtures/collected-orders.xlsx"))


class ImporterTests(unittest.TestCase):
    def test_imports_kakao_order(self):
        content = write_xlsx(
            ["결제번호", "채널상품번호", "상품명", "수령인명", "수량", "주문일시"],
            [["3371403104", "K-PRODUCT", "카카오 상품", "홍길동", "1", "2026-06-11 10:00:00"]],
        )
        orders = import_workbook(content, "kakao.xlsx")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["channel"], "카카오")
        self.assertEqual(orders[0]["orderNumber"], "3371403104")

    def test_imports_coupang_orders(self):
        content = write_xlsx(
            ["주문번호", "묶음배송번호", "등록상품명", "수취인이름", "구매수(수량)", "주문일"],
            [
                ["COUPANG-1", "BUNDLE-1", "쿠팡 상품 1", "정영희", "1", "2026-06-11 11:00:00"],
                ["COUPANG-2", "BUNDLE-2", "쿠팡 상품 2", "김고객", "1", "2026-06-11 12:00:00"],
                ["COUPANG-3", "BUNDLE-3", "쿠팡 상품 3", "이고객", "1", "2026-06-11 13:00:00"],
            ],
        )
        orders = import_workbook(content, "coupang.xlsx")
        self.assertEqual(len(orders), 3)
        self.assertEqual(orders[0]["channel"], "쿠팡")
        self.assertEqual(orders[0]["recipient"], "정영희")

    def test_exported_workbook_can_be_read(self):
        content = write_xlsx(["주문번호", "담당자"], [["100", "홍길동"]])
        self.assertEqual(read_first_sheet(content), [{"주문번호": "100", "담당자": "홍길동"}])

    def test_imports_legacy_xls_workbook(self):
        if os.getenv("RUN_LIBREOFFICE_TESTS") != "1":
            self.skipTest("RUN_LIBREOFFICE_TESTS=1일 때 실행합니다.")
        if not shutil.which("libreoffice"):
            self.skipTest("LibreOffice가 없습니다.")
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            xlsx = temporary / "legacy-source.xlsx"
            xlsx.write_bytes(write_xlsx(
                ["주문번호", "상품명", "수령인", "수량"],
                [["XLS-1", "구형 엑셀 상품", "홍길동", "2"]],
            ))
            result = subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "xls", "--outdir", directory, str(xlsx)],
                check=False,
                capture_output=True,
                timeout=30,
            )
            xls = temporary / "legacy-source.xls"
            if result.returncode != 0 or not xls.exists():
                self.skipTest("LibreOffice에서 .xls 테스트 파일을 만들지 못했습니다.")
            orders = import_workbook(xls.read_bytes(), xls.name)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["orderNumber"], "XLS-1")
        self.assertEqual(orders[0]["quantity"], 2)

    def test_imports_html_workbook_with_xls_extension(self):
        content = """<html><body><table><tr>
        <td>주문 번호</td><td>주문일자</td><td>상품주문번호</td><td>상품코드</td>
        <td>자체상품코드</td><td>상품명</td><td>옵션정보</td><td>상품수량</td><td>판매가</td>
        <td>수취인 이름</td><td>수취인 핸드폰 번호</td><td>수취인 주소</td><td>수취인 나머지 주소</td>
        </tr><tr><td>260615052855</td><td>2026-06-15 05:28:36</td><td>106928</td><td>1000000493</td>
        <td>NT371B5M</td><td>테스트 노트북</td><td>A급</td><td>2</td><td>390000.00</td>
        <td>홍길동</td><td>010-1234-5678</td><td>서울시</td><td>101호</td></tr></table></body></html>""".encode()
        orders = import_workbook(content, "shop-export.xls")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["channel"], "고도몰")
        self.assertEqual(orders[0]["orderNumber"], "260615052855")
        self.assertEqual(orders[0]["quantity"], 2)
        self.assertEqual(orders[0]["address"], "서울시 101호")

    def test_groups_godomall_additions_into_main_order(self):
        content = """<html><body><table><tr>
        <td>주문 번호</td><td>주문일자</td><td>상품주문번호</td><td>상품코드</td><td>자체상품코드</td>
        <td>상품명</td><td>옵션정보</td><td>상품수량</td><td>판매가</td><td>총 결제 금액</td>
        <td>수취인 이름</td><td>수취인 핸드폰 번호</td><td>수취인 주소</td>
        </tr><tr><td>ORDER-1</td><td>2026-06-15</td><td>1</td><td>P1</td><td>MAIN</td>
        <td>기본 노트북</td><td>A급</td><td>1</td><td>390000</td><td>460000</td>
        <td>홍길동</td><td>010-1234-5678</td><td>서울시</td></tr>
        <tr><td>ORDER-1</td><td>2026-06-15</td><td>2</td><td>P2</td><td></td>
        <td>[추가]메모리 업그레이드</td><td></td><td>1</td><td>70000</td><td>460000</td>
        <td>홍길동</td><td>010-1234-5678</td><td>서울시</td></tr></table></body></html>""".encode()
        orders = import_workbook(content, "shop-export.xls")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["productName"], "기본 노트북")
        self.assertEqual(orders[0]["optionName"], "A급 / [추가]메모리 업그레이드")
        self.assertEqual(orders[0]["amount"], 460000)

    def test_imports_generic_order_columns(self):
        content = write_xlsx(
            ["주문번호", "주문일시", "상품명", "옵션명", "수량", "수령인", "연락처", "주소", "배송메시지"],
            [["MANUAL-1", "2026-06-12 12:00", "테스트 상품", "검정", "2", "홍길동", "010-1234-5678", "서울시", "문 앞"]],
        )
        orders = import_workbook(content, "other-channel.xlsx")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["orderNumber"], "MANUAL-1")
        self.assertEqual(orders[0]["recipient"], "홍길동")
        self.assertEqual(orders[0]["quantity"], 2)

    def test_imports_collected_order_workbook(self):
        if os.getenv("RUN_EXTERNAL_SAMPLE_TESTS") != "1":
            self.skipTest("RUN_EXTERNAL_SAMPLE_TESTS=1일 때 실행합니다.")
        if not COLLECTED_SAMPLE.exists():
            self.skipTest("주문수집 샘플 파일이 없습니다.")
        orders = import_workbook(COLLECTED_SAMPLE.read_bytes(), COLLECTED_SAMPLE.name)
        self.assertEqual(len(orders), 32)
        self.assertEqual(orders[0]["channel"], "쿠팡")
        self.assertEqual(orders[0]["amount"], 390000)
        self.assertEqual(orders[4]["channel"], "고도몰")
        self.assertIn("무선키마세트", orders[4]["optionName"])

    def test_source_zip_contains_two_excel_files(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("kakao.xlsx", write_xlsx(["주문번호"], [["1"]]))
            archive.writestr("coupang.xlsx", write_xlsx(["주문번호"], [["2"]]))
        with zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive:
            excel_names = [name for name in archive.namelist() if name.endswith(".xlsx")]
        self.assertEqual(len(excel_names), 2)


if __name__ == "__main__":
    unittest.main()
