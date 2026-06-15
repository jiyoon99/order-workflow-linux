# Order Workflow

카카오, 쿠팡, 고도몰 등 여러 판매 채널의 주문 파일을 하나의 작업 화면으로 통합하고, 제작부터 출고 및 AS 조회까지 관리하는 사내 주문 처리 웹 애플리케이션입니다.

반복적인 엑셀 취합, 작업자 간 중복 처리, 출고 이력 추적의 어려움을 줄이는 것을 목표로 개발했습니다.

## 핵심 기능

### 주문 통합

- 카카오, 쿠팡, 고도몰 및 통합 주문 수집 양식 지원
- `.xlsx`, `.xls`, ZIP 묶음 업로드 지원
- 채널, 주문번호, 상품 기준 중복 주문 방지
- 고도몰 본상품과 추가상품을 하나의 주문으로 그룹화
- 수기 주문 등록 및 주문번호 중복 검사
- 주문일시 기준 오름차순 정렬

### 제작 및 출고

- 전체, 제작 대기, 준비 중, 제작 완료, 출고 완료 상태 필터
- 작업 선점 및 담당자 표시로 중복 작업 방지
- 제품 관리번호 등록 및 중복 검사
- 제작 완료 작업자와 처리 시각 기록
- 택배사, 송장번호, 출고 담당자와 처리 시각 기록
- 누적 출고 내역 `.xlsx` 다운로드
- 완료 주문 자동 보관 및 이력 검색

### 취소 및 AS

- 권한에 따른 주문 취소와 취소 사유 기록
- 취소 주문 전용 보관함
- 고객 이름 또는 연락처 기반 출고 이력 검색
- 고객별 출고 제품, 배송 정보, 담당자 이력 조회

### 계정 및 권한

| 역할 | 주요 권한 |
| --- | --- |
| 총책임자 | 전체 기능, 회원 관리, 제작 완료 주문 취소 |
| 개발자 | 전체 기능, 회원 관리, 운영 지원 |
| 판매 담당자 / MD | 주문 등록, 파일 가져오기, 출고 처리, 주문 취소 |
| AS 담당자 | 주문 조회, 작업 처리, 고객 출고 이력 검색 |
| 일반 작업자 | 주문 조회 및 제작 상태 처리 |

## 업무 흐름

```mermaid
flowchart LR
    A[판매 채널 주문 파일] --> B[양식 자동 판별 및 정규화]
    B --> C[중복 검사]
    C --> D[제작 대기]
    D --> E[준비 중]
    E --> F[제작 완료]
    F --> G[출고 완료]
    G --> H[완료 주문 보관함]
    H --> I[AS 고객 출고 이력]
```

## 기술 구성

- Backend: Python 3 표준 라이브러리 기반 HTTP API
- Frontend: Vanilla JavaScript, HTML, CSS
- Storage: JSON 파일 기반 영속 저장소
- Spreadsheet: 자체 XLSX 파서/생성기, LibreOffice 기반 구형 XLS 변환
- Deployment: Linux systemd 서비스 템플릿
- Test: Python `unittest`

외부 웹 프레임워크 없이 동작하도록 구성해 설치 부담을 줄였습니다. 주문, 회원, 감사 로그의 저장 위치는 환경변수로 분리할 수 있습니다.

## 데이터 보호

- 비밀번호 PBKDF2-SHA256 해시 저장
- 세션 쿠키 `HttpOnly`, `SameSite=Strict` 적용
- 로그인 연속 실패 시 일시 차단
- 역할 기반 API 접근 제어
- 데이터 변경 전 일별 백업 및 14일 보존
- 주문 등록, 상태 변경, 취소 등 주요 작업 감사 로그 기록
- 임시 파일 작성 후 원자적 교체로 저장 중 파일 손상 방지
- 업로드 파일 크기, ZIP 파일 개수 및 압축 해제 크기 제한

실제 주문 데이터, 회원 데이터, 감사 로그와 백업 파일은 `.gitignore`로 공개 저장소에서 제외합니다.

## 프로젝트 구조

```text
order-workflow-linux/
├── public/              # 주문 작업 화면
├── src/
│   ├── server.py        # HTTP API 및 주문 처리
│   ├── auth.py          # 로그인, 세션, 권한 관리
│   ├── importers.py     # 판매 채널별 주문 정규화
│   └── excel.py         # XLSX 읽기 및 생성
├── test/                # 인증, 가져오기, API 테스트
├── deploy/              # systemd 및 시작 스크립트 예시
└── data/                # 로컬 운영 데이터, Git 추적 제외
```

## 실행 방법

### Linux

```bash
git clone https://github.com/jiyoon99/order-workflow-linux.git
cd order-workflow-linux
python3 src/server.py
```

### Windows

```powershell
git clone https://github.com/jiyoon99/order-workflow-linux.git
cd order-workflow-linux
py src/server.py
```

브라우저에서 `http://localhost:3000`을 엽니다. 첫 실행 시 생성하는 첫 계정이 총책임자 권한을 갖습니다.

`.xlsx`는 Python 3만으로 처리할 수 있습니다. 구형 바이너리 `.xls`를 가져오려면 서버에 LibreOffice가 설치되어 있어야 합니다.

## 환경변수

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `PORT` | `3000` | 웹 서버 포트 |
| `DATA_FILE` | `data/orders.json` | 주문 데이터 경로 |
| `USERS_FILE` | `data/users.json` | 회원 데이터 경로 |
| `AUDIT_FILE` | `data/audit.jsonl` | 감사 로그 경로 |

## 테스트

```bash
python3 -m unittest discover -s test -v
```

현재 자동 테스트는 인증, 권한, 주문 가져오기, 중복 방지, 상태 변경, 취소, 보관 및 AS 조회 흐름을 검증합니다.

## 운영 참고

이 저장소는 Linux에서 운영 중인 버전의 소스 코드 포트폴리오입니다. 실제 개인정보와 계정 정보는 포함하지 않습니다. 인터넷에 직접 공개 배포할 경우 HTTPS 리버스 프록시, 방화벽, 외부 DB, 별도 백업 저장소를 추가하는 구성이 필요합니다.
