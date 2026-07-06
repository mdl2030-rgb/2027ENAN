import json
import os
import socket
import sqlite3
import webbrowser
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError:
    DATA_DIR = BASE_DIR / "data"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "transaction_statements.db"
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8765"))

COMPANY = {
    "name": "(주)이난",
    "business_no": "621-81-68878",
    "ceo": "오세민",
    "address": "부산시 금정구 체육공원로 368 신천화훼단지",
    "business_type": "도소매외",
    "business_item": "생화, 조경수외",
}


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS statements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_no TEXT,
            customer_name TEXT,
            customer_phone TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(statements)").fetchall()}
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE statements ADD COLUMN updated_at TEXT")
    return conn


def get_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def safe_text(value):
    return escape(str(value or ""))


def money(value):
    try:
        return f"{round(float(value)):,}"
    except (TypeError, ValueError):
        return "0"


def short_date(value):
    text = str(value or "")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[5:10]
    return text


def calc_totals(items):
    qty = 0
    subtotal = 0
    tax = 0
    for item in items:
        item_qty = float(item.get("qty") or 0)
        price = float(item.get("price") or 0)
        item_tax = float(item.get("tax") or 0)
        qty += item_qty
        subtotal += item_qty * price
        tax += item_tax
    return {"qty": qty, "subtotal": subtotal, "tax": tax, "grand": subtotal + tax}


def kakao_message(data, statement_id=None):
    totals = calc_totals(data.get("items") or [])
    lines = [
        f"{COMPANY['name']} 거래명세서",
        f"작성일자: {data.get('statementDate', '')}",
        f"문서번호: {data.get('statementNo', '')}",
        f"거래처: {data.get('customerName', '')}",
        f"합계금액: {money(totals['grand'])}원",
        "",
        "품목",
    ]
    for item in (data.get("items") or [])[:8]:
        lines.append(
            f"- {item.get('name') or '품명 미입력'} / 수량 {item.get('qty') or 0} / "
            f"단가 {money(item.get('price'))} / 세액 {money(item.get('tax'))}"
        )
    if len(data.get("items") or []) > 8:
        lines.append(f"- 외 {len(data.get('items')) - 8}건")
    if data.get("memo"):
        lines += ["", f"비고: {data.get('memo')}"]
    if statement_id:
        lines += ["", f"명세서 번호: {statement_id}"]
    return "\n".join(lines)


def statement_html(data, statement_id=None, toolbar=False, standalone=False):
    items = data.get("items") or []
    totals = calc_totals(items)
    rows = []
    for item in items:
        amount = float(item.get("qty") or 0) * float(item.get("price") or 0)
        rows.append(
            f"""
            <tr>
              <td>{safe_text(short_date(item.get('date')))}</td>
              <td>{safe_text(item.get('name'))}</td>
              <td>{safe_text(item.get('spec'))}</td>
              <td class="num">{money(item.get('qty'))}</td>
              <td class="num">{money(item.get('price'))}</td>
              <td class="num">{money(amount)}</td>
              <td class="num">{money(item.get('tax'))}</td>
              <td>{safe_text(item.get('note'))}</td>
            </tr>
            """
        )
    while len(rows) < 8:
        rows.append("<tr><td>&nbsp;</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>")

    toolbar_html = ""
    if toolbar and not standalone:
        toolbar_html = f"""
        <div class="screen-toolbar">
          <button onclick="window.print()">인쇄 / PDF</button>
          <a href="/download/{statement_id}.html">HTML 다운로드</a>
          <button onclick="copyKakaoMessage()">카카오톡 메시지 복사</button>
        </div>
        <textarea id="kakaoMessage" class="share-message" readonly>{safe_text(kakao_message(data, statement_id))}</textarea>
        """

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>거래명세서 {safe_text(data.get('statementNo'))}</title>
  <style>{common_css()}</style>
</head>
<body>
  {toolbar_html}
  {statement_body(data, rows, totals)}
  <script>
    async function copyKakaoMessage() {{
      await navigator.clipboard.writeText(document.getElementById("kakaoMessage").value);
      alert("카카오톡 메시지를 복사했습니다.");
    }}
  </script>
</body>
</html>"""


def statement_body(data, rows, totals):
    return f"""
  <main class="paper">
    <h1 class="doc-title">거 래 명 세 서</h1>
    <section class="head-grid">
      <table class="meta">
        <tr><th>작성일자</th><td>{safe_text(data.get('statementDate'))}</td><th>문서번호</th><td>{safe_text(data.get('statementNo'))}</td></tr>
        <tr><th>결제조건</th><td>{safe_text(data.get('paymentType'))}</td><th>담당자</th><td>{safe_text(data.get('manager'))}</td></tr>
      </table>
      <table class="supplier">
        <tr><th>등록번호</th><td>{COMPANY['business_no']}</td></tr>
        <tr><th>상호</th><td><strong>{COMPANY['name']}</strong></td></tr>
        <tr><th>대표자</th><td>{COMPANY['ceo']}</td></tr>
        <tr><th>주소</th><td>{COMPANY['address']}</td></tr>
        <tr><th>업태</th><td>{COMPANY['business_type']}</td></tr>
        <tr><th>종목</th><td>{COMPANY['business_item']}</td></tr>
      </table>
    </section>
    <section class="recipient">
      <div class="customer">공급받는자: {safe_text(data.get('customerName'))} 귀하</div>
      <div class="summary"><span>합계금액</span><span>{money(totals['grand'])}원</span></div>
    </section>
    <table class="items">
      <thead><tr><th>일자</th><th>품명</th><th>규격</th><th>수량</th><th>단가</th><th>공급가액</th><th>세액</th><th>비고</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
      <tfoot><tr class="total"><th colspan="3">합계</th><td class="num">{money(totals['qty'])}</td><td></td><td class="num">{money(totals['subtotal'])}</td><td class="num">{money(totals['tax'])}</td><td class="num">{money(totals['grand'])}</td></tr></tfoot>
    </table>
    <section class="bottom">
      <div><strong>비고</strong><div class="memo">{safe_text(data.get('memo'))}</div></div>
      <div class="seal-area">위와 같이 거래하였음을 확인합니다.<br>공급자: {COMPANY['name']}<img class="seal" src="/static/seal.gif" alt="법인인감"></div>
    </section>
  </main>"""


def common_css():
    return """
    * { box-sizing: border-box; }
    body { margin: 0; font-family: "Malgun Gothic", Arial, sans-serif; color: #1f2933; background: #eef2f6; }
    button, a.button, .screen-toolbar a { border: 1px solid #cfd8e3; background: #fff; color: #1f2933; border-radius: 6px; min-height: 36px; padding: 7px 11px; font-weight: 800; text-decoration: none; cursor: pointer; }
    .screen-toolbar { max-width: 980px; margin: 16px auto 8px; display: flex; gap: 8px; flex-wrap: wrap; }
    .share-message { display: block; max-width: 980px; width: calc(100% - 24px); min-height: 110px; margin: 0 auto 10px; padding: 10px; border: 1px solid #cfd8e3; border-radius: 6px; }
    .paper { max-width: 980px; margin: 0 auto 18px; background: #fff; padding: 16px; border: 1px solid #cfd8e3; }
    .doc-title { text-align: center; margin: 0 0 12px; font-size: 28px; font-weight: 900; letter-spacing: 0; }
    .head-grid { display: grid; grid-template-columns: 1fr 350px; gap: 12px; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    th, td { border: 1px solid #cfd8e3; padding: 6px 7px; min-height: 32px; font-size: 13px; }
    th { background: #f4f7fb; text-align: center; font-weight: 900; }
    .meta { border-top: 2px solid #1f2933; }
    .supplier, .items { border: 2px solid #1f2933; }
    .recipient { display: grid; grid-template-columns: 1fr 250px; gap: 10px; margin: 12px 0; align-items: end; }
    .customer { border-bottom: 2px solid #1f2933; padding: 0 8px 8px; font-size: 18px; font-weight: 900; }
    .summary { border: 2px solid #1f2933; padding: 10px; display: flex; justify-content: space-between; font-weight: 900; }
    .items th, .items td { height: 30px; padding: 5px 6px; }
    .num { text-align: right; }
    .total th, .total td { font-weight: 900; background: #fbfcfe; }
    .bottom { display: grid; grid-template-columns: 1fr 230px; gap: 10px; margin-top: 10px; }
    .memo { border: 1px solid #cfd8e3; min-height: 74px; padding: 8px; white-space: pre-wrap; }
    .seal-area { position: relative; border: 1px solid #cfd8e3; min-height: 92px; padding: 10px; font-weight: 800; line-height: 1.55; }
    .seal { position: absolute; right: 10px; bottom: 6px; width: 76px; height: 76px; object-fit: contain; mix-blend-mode: multiply; }
    @media (max-width: 860px) { .head-grid, .recipient, .bottom { grid-template-columns: 1fr; } .paper { overflow-x: auto; } .items { min-width: 840px; } }
    @media print {
      @page { size: A4 portrait; margin: 9mm; }
      body { background: #fff; }
      .screen-toolbar, .share-message { display: none !important; }
      html, body { overflow: hidden !important; }
      *::-webkit-scrollbar { display: none !important; width: 0 !important; height: 0 !important; }
      .paper { width: 100%; max-width: none; margin: 0; border: 0; padding: 0; overflow: hidden !important; }
      table { width: 100% !important; table-layout: fixed !important; }
      th, td { height: 27px; padding: 4px 5px; overflow: hidden; }
      .items { min-width: 0 !important; border-collapse: collapse !important; }
      .items th:nth-child(1), .items td:nth-child(1) { width: 13%; }
      .items th:nth-child(2), .items td:nth-child(2) { width: 16%; }
      .items th:nth-child(3), .items td:nth-child(3) { width: 10%; }
      .items th:nth-child(4), .items td:nth-child(4) { width: 8%; }
      .items th:nth-child(5), .items td:nth-child(5) { width: 12%; }
      .items th:nth-child(6), .items td:nth-child(6) { width: 14%; }
      .items th:nth-child(7), .items td:nth-child(7) { width: 12%; }
      .items th:nth-child(8), .items td:nth-child(8) { width: 15%; }
      .doc-title { font-size: 26px; margin-bottom: 8px; }
      .memo { border: 0; min-height: 52px; overflow: hidden !important; }
      .seal { width: 70px; height: 70px; }
    }
    """


def manifest_json():
    return json.dumps(
        {
            "name": "(주)이난 거래명세서",
            "short_name": "이난 명세서",
            "description": "(주)이난 거래명세서 작성 및 카카오톡 공유",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#e9eef4",
            "theme_color": "#176b87",
            "icons": [
                {
                    "src": "/static/icon.svg",
                    "sizes": "any",
                    "type": "image/svg+xml",
                    "purpose": "any maskable",
                }
            ],
        },
        ensure_ascii=False,
    )


def service_worker_js():
    return """
const CACHE_NAME = "inan-statement-pwa-v1";
const APP_SHELL = [
  "/",
  "/manifest.webmanifest",
  "/static/icon.svg",
  "/static/seal.gif"
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        return response;
      })
      .catch(() => caches.match(request).then((cached) => cached || caches.match("/")))
  );
});
"""


def render_app():
    today = datetime.now().strftime("%Y-%m-%d")
    statement_no = datetime.now().strftime("%Y%m%d-001")
    mobile_url = f"http://{get_lan_ip()}:{PORT}/"
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>거래명세서 프로그램 - {COMPANY['name']}</title>
  <meta name="theme-color" content="#176b87">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="이난 명세서">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/static/icon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="/static/icon.svg">
  <style>
    {common_css()}
    body {{ background: #e9eef4; }}
    .app {{ max-width: 980px; margin: 0 auto; padding: 18px; }}
    .top {{ display: flex; justify-content: space-between; gap: 10px; align-items: center; margin-bottom: 12px; }}
    .top h1 {{ margin: 0; font-size: 21px; }}
    .tabs, .actions {{ display: flex; gap: 7px; flex-wrap: wrap; }}
    button.active, button.primary {{ background: #176b87; border-color: #176b87; color: #fff; }}
    button.danger {{ color: #b42318; }}
    .page, .kakao-page {{ display: none; }}
    .page.active, .kakao-page.active {{ display: block; }}
    input, textarea {{ width: 100%; border: 0; outline: 0; padding: 7px; font: inherit; background: transparent; }}
    textarea {{ resize: vertical; border: 1px solid #cfd8e3; border-radius: 6px; background: #fff; }}
    .edit-grid {{ display: grid; grid-template-columns: 92px 1fr 92px 1fr; border-top: 2px solid #1f2933; border-left: 1px solid #cfd8e3; }}
    .cell, .field {{ border-right: 1px solid #cfd8e3; border-bottom: 1px solid #cfd8e3; min-height: 34px; display: flex; align-items: center; }}
    .cell {{ justify-content: center; background: #f4f7fb; font-weight: 900; font-size: 13px; }}
    .company {{ margin-top: 10px; border: 2px solid #1f2933; }}
    .line {{ display: grid; grid-template-columns: 88px 1fr 38px; gap: 7px; align-items: end; }}
    .line label, .line span {{ font-weight: 900; padding-bottom: 8px; }}
    .line input {{ border-bottom: 2px solid #1f2933; font-size: 17px; font-weight: 800; }}
    .remove {{ width: 30px; border: 0; color: #b42318; background: transparent; font-weight: 900; cursor: pointer; }}
    .kakao-page {{ background: #fff; border: 1px solid #cfd8e3; padding: 16px; min-height: 640px; }}
    .kakao-grid {{ display: grid; grid-template-columns: 1fr 280px; gap: 14px; }}
    .message {{ min-height: 360px; }}
    .saved-list {{ display: grid; gap: 8px; }}
    .saved-item {{ border: 1px solid #cfd8e3; padding: 9px; border-radius: 6px; display: grid; gap: 5px; }}
    .saved-item a {{ color: #176b87; font-weight: 800; }}
    .hint {{ color: #667085; margin: 0 0 10px; }}
    .mobile-url {{ border: 1px solid #cfd8e3; background: #f8fafc; border-radius: 6px; padding: 10px; margin-bottom: 12px; }}
    .mobile-url strong {{ display: block; margin-bottom: 5px; }}
    .mobile-url code {{ display: block; word-break: break-all; font-size: 16px; font-weight: 900; color: #176b87; }}
    .mobile-actions {{ display: none; }}
    @media (max-width: 860px) {{ .top, .recipient, .bottom, .kakao-grid {{ grid-template-columns: 1fr; flex-direction: column; align-items: stretch; }} .edit-grid {{ grid-template-columns: 88px minmax(140px, 1fr); }} }}
    @media (max-width: 640px) {{
      .app {{ padding: 10px 10px 76px; }}
      .top h1 {{ font-size: 18px; }}
      .tabs button, .actions button, a.button {{ flex: 1 1 auto; min-height: 42px; }}
      .paper, .kakao-page {{ padding: 11px; border-radius: 0; }}
      .doc-title {{ font-size: 24px; }}
      .edit-grid {{ grid-template-columns: 82px 1fr; }}
      .company table, .company tbody, .company tr, .company th, .company td {{ display: block; width: 100%; }}
      .company tr {{ border-bottom: 1px solid #cfd8e3; }}
      .company th {{ text-align: left; border: 0; padding-bottom: 2px; }}
      .company td {{ border: 0; padding-top: 2px; }}
      .items {{ min-width: 0; border: 0; }}
      .items thead {{ display: none; }}
      .items tbody tr {{ display: grid; gap: 7px; border: 1px solid #cfd8e3; border-radius: 6px; padding: 8px; margin-bottom: 9px; }}
      .items tbody td {{ display: grid; grid-template-columns: 74px 1fr; align-items: center; border: 0; height: auto; padding: 0; }}
      .items tbody td::before {{ content: attr(data-label); font-weight: 900; color: #475467; }}
      .items input {{ min-height: 38px; border: 1px solid #cfd8e3; border-radius: 5px; background: #fff; }}
      .items tfoot tr {{ display: grid; grid-template-columns: 1fr 1fr; gap: 5px; border: 2px solid #1f2933; padding: 8px; }}
      .items tfoot th, .items tfoot td {{ display: block; border: 0; height: auto; padding: 2px; }}
      .items tfoot th {{ grid-column: 1 / -1; text-align: left; }}
      .remove-col {{ display: block; }}
      .remove {{ width: 100%; min-height: 38px; border: 1px solid #f3b1aa; background: #fff7f6; }}
      .message {{ min-height: 300px; }}
      .mobile-actions {{ position: fixed; left: 0; right: 0; bottom: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 9px 10px; background: rgba(255,255,255,.96); border-top: 1px solid #cfd8e3; }}
      .mobile-actions button {{ min-height: 46px; }}
    }}
    @media print {{
      .top, .kakao-page, .remove, .remove-col {{ display: none !important; }}
      html, body {{ width: 100%; overflow: hidden !important; }}
      *::-webkit-scrollbar {{ display: none !important; width: 0 !important; height: 0 !important; }}
      .app, .page, #statementPage {{ max-width: none; padding: 0; overflow: hidden !important; }}
      .page, .page.active {{ display: block !important; }}
      input, textarea {{ padding: 3px 5px; }}
      textarea {{ border: 0; min-height: 52px; resize: none !important; overflow: hidden !important; }}
      .paper {{ display: block !important; width: 100%; max-width: none; overflow: hidden !important; }}
      .edit-grid {{ display: grid !important; grid-template-columns: 92px 1fr 92px 1fr !important; }}
      .company table, .company tbody, .company tr {{ display: table !important; width: 100% !important; }}
      .company tbody {{ display: table-row-group !important; }}
      .company tr {{ display: table-row !important; }}
      .company th, .company td {{ display: table-cell !important; width: auto !important; border: 1px solid #cfd8e3 !important; padding: 4px 5px !important; }}
      .items {{ width: 100% !important; max-width: 100% !important; min-width: 0 !important; overflow: hidden !important; border: 2px solid #1f2933 !important; border-collapse: collapse !important; table-layout: fixed !important; }}
      .items thead {{ display: table-header-group !important; }}
      .items tbody {{ display: table-row-group !important; }}
      .items tfoot {{ display: table-footer-group !important; }}
      .items tr {{ display: table-row !important; border: 0 !important; padding: 0 !important; margin: 0 !important; }}
      .items th, .items td {{ display: table-cell !important; border: 1px solid #cfd8e3 !important; height: 27px !important; padding: 4px 5px !important; font-size: 12px !important; overflow: hidden !important; }}
      .items td::before {{ content: none !important; }}
      .items input {{ min-height: 0 !important; border: 0 !important; border-radius: 0 !important; padding: 2px 3px !important; }}
      .items th:nth-child(1), .items td:nth-child(1) {{ width: 13%; }}
      .items th:nth-child(2), .items td:nth-child(2) {{ width: 16%; }}
      .items th:nth-child(3), .items td:nth-child(3) {{ width: 10%; }}
      .items th:nth-child(4), .items td:nth-child(4) {{ width: 8%; }}
      .items th:nth-child(5), .items td:nth-child(5) {{ width: 12%; }}
      .items th:nth-child(6), .items td:nth-child(6) {{ width: 14%; }}
      .items th:nth-child(7), .items td:nth-child(7) {{ width: 12%; }}
      .items th:nth-child(8), .items td:nth-child(8) {{ width: 15%; }}
      .bottom {{ display: grid !important; grid-template-columns: 1fr 230px !important; }}
    }}
  </style>
</head>
<body>
  <main class="app">
    <div class="top">
      <h1>{COMPANY['name']} 거래명세서</h1>
      <div class="tabs">
        <button class="active" id="statementTab" type="button">거래명세서</button>
        <button id="kakaoTab" type="button">카카오톡</button>
      </div>
      <div class="actions">
        <button id="addRow" type="button">품목 추가</button>
        <button class="primary" id="save" type="button">저장</button>
        <button id="print" type="button">인쇄 / PDF</button>
        <button class="danger" id="clear" type="button">새 문서</button>
      </div>
    </div>

    <section class="page active" id="statementPage">
      <div class="paper">
        <h2 class="doc-title">거 래 명 세 서</h2>
        <div class="edit-grid">
          <label class="cell">작성일자</label><div class="field"><input id="statementDate" type="date" value="{today}"></div>
          <label class="cell">문서번호</label><div class="field"><input id="statementNo" value="{statement_no}"></div>
          <label class="cell">결제조건</label><div class="field"><input id="paymentType" placeholder="현금 / 카드 / 월말결제"></div>
          <label class="cell">담당자</label><div class="field"><input id="manager" placeholder="담당자명"></div>
          <label class="cell">고객 연락처</label><div class="field"><input id="customerPhone" placeholder="010-0000-0000"></div>
          <label class="cell">전송상태</label><div class="field"><input id="sendStatus" value="작성중" readonly></div>
        </div>
        <div class="company">
          <table>
            <tr><th>등록번호</th><td>{COMPANY['business_no']}</td><th>상호</th><td><strong>{COMPANY['name']}</strong></td></tr>
            <tr><th>대표자</th><td>{COMPANY['ceo']}</td><th>주소</th><td>{COMPANY['address']}</td></tr>
            <tr><th>업태</th><td>{COMPANY['business_type']}</td><th>종목</th><td>{COMPANY['business_item']}</td></tr>
          </table>
        </div>
        <div class="recipient">
          <div class="line"><label>공급받는자</label><input id="customerName" placeholder="거래처명"><span>귀하</span></div>
          <div class="summary"><span>합계금액</span><span id="grandTotal">0원</span></div>
        </div>
        <table class="items">
          <thead><tr><th>일자</th><th>품명</th><th>규격</th><th>수량</th><th>단가</th><th>공급가액</th><th>세액</th><th>비고</th><th class="remove-col">삭제</th></tr></thead>
          <tbody id="itemBody"></tbody>
          <tfoot><tr class="total"><th colspan="3">합계</th><td class="num" id="totalQty">0</td><td></td><td class="num" id="subtotal">0</td><td class="num" id="taxTotal">0</td><td class="num" id="tableTotal">0</td><td class="remove-col"></td></tr></tfoot>
        </table>
        <section class="bottom">
          <div><strong>비고</strong><textarea id="memo" placeholder="입금계좌, 배송사항, 기타 전달사항"></textarea></div>
          <div class="seal-area">위와 같이 거래하였음을 확인합니다.<br>공급자: {COMPANY['name']}<img class="seal" src="/static/seal.gif" alt="법인인감"></div>
        </section>
      </div>
    </section>

    <section class="kakao-page" id="kakaoPage">
      <h2>카카오톡 전송 준비</h2>
      <p class="hint">저장 후 아래 메시지를 복사해서 고객 카카오톡 채팅방에 붙여넣으세요.</p>
      <div class="mobile-url">
        <strong>휴대폰 접속 주소</strong>
        <code>{mobile_url}</code>
      </div>
      <div class="kakao-grid">
        <div>
          <textarea id="shareMessage" class="message" readonly></textarea>
          <div class="actions" style="margin-top:8px">
            <button id="copyMessage" type="button">메시지 복사</button>
            <button id="nativeShare" type="button">카카오톡으로 공유</button>
            <a class="button" id="downloadLink" href="#" hidden>명세서 다운로드</a>
          </div>
        </div>
        <div>
          <h3>저장된 명세서</h3>
          <div id="savedList" class="saved-list"></div>
        </div>
      </div>
    </section>
    <div class="mobile-actions">
      <button class="primary" id="mobileSave" type="button">저장</button>
      <button id="mobileShare" type="button">카카오톡 공유</button>
    </div>
  </main>

  <script>
    const itemBody = document.getElementById("itemBody");
    const ids = ["statementDate", "statementNo", "paymentType", "manager", "customerPhone", "customerName", "memo"];
    let currentStatementId = null;
    let autoSaveTimer = null;
    const n = (v) => Number(String(v || "").replace(/[^\\d.-]/g, "")) || 0;
    const fmt = (v) => Math.round(v).toLocaleString("ko-KR");
    const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);

    function row(data = {{}}) {{
      const tr = document.createElement("tr");
      tr.innerHTML = `<td data-label="일자"><input class="center date" type="date"></td><td data-label="품명"><input class="name"></td><td data-label="규격"><input class="center spec"></td><td data-label="수량"><input class="num qty" type="number" min="0"></td><td data-label="단가"><input class="num price" inputmode="numeric"></td><td data-label="공급가액"><input class="num amount" readonly></td><td data-label="세액"><input class="num tax" inputmode="numeric"></td><td data-label="비고"><input class="note"></td><td data-label="삭제" class="center remove-col"><button class="remove" type="button">X</button></td>`;
      tr.querySelector(".date").value = data.date || document.getElementById("statementDate").value || today();
      tr.querySelector(".name").value = data.name || "";
      tr.querySelector(".spec").value = data.spec || "";
      tr.querySelector(".qty").value = data.qty || "";
      tr.querySelector(".price").value = data.price ? fmt(data.price) : "";
      tr.querySelector(".tax").value = data.tax ? fmt(data.tax) : "";
      tr.querySelector(".note").value = data.note || "";
      tr.addEventListener("input", updateTotals);
      tr.querySelector(".remove").addEventListener("click", () => {{ if (itemBody.children.length > 1) {{ tr.remove(); updateTotals(); scheduleAutoSave(); }} }});
      itemBody.appendChild(tr);
      updateTotals();
    }}

    function updateTotals() {{
      let qty = 0, subtotal = 0, tax = 0;
      itemBody.querySelectorAll("tr").forEach((tr) => {{
        const q = n(tr.querySelector(".qty").value);
        const p = n(tr.querySelector(".price").value);
        const t = n(tr.querySelector(".tax").value);
        const amount = q * p;
        tr.querySelector(".amount").value = amount ? fmt(amount) : "";
        qty += q; subtotal += amount; tax += t;
      }});
      document.getElementById("totalQty").textContent = fmt(qty);
      document.getElementById("subtotal").textContent = fmt(subtotal);
      document.getElementById("taxTotal").textContent = fmt(tax);
      document.getElementById("tableTotal").textContent = fmt(subtotal + tax);
      document.getElementById("grandTotal").textContent = fmt(subtotal + tax) + "원";
    }}

    function collect() {{
      const data = Object.fromEntries(ids.map((id) => [id, document.getElementById(id).value]));
      data.items = [...itemBody.querySelectorAll("tr")].map((tr) => ({{
        date: tr.querySelector(".date").value,
        name: tr.querySelector(".name").value,
        spec: tr.querySelector(".spec").value,
        qty: n(tr.querySelector(".qty").value),
        price: n(tr.querySelector(".price").value),
        tax: n(tr.querySelector(".tax").value),
        note: tr.querySelector(".note").value
      }})).filter((item) => item.name || item.qty || item.price || item.tax);
      return data;
    }}

    function fill(data) {{
      ids.forEach((id) => document.getElementById(id).value = data[id] || "");
      itemBody.innerHTML = "";
      (data.items && data.items.length ? data.items : [{{}}]).forEach(row);
      while (itemBody.children.length < 8) row();
      updateTotals();
    }}

    function showPage(name) {{
      const statement = name === "statement";
      document.getElementById("statementPage").classList.toggle("active", statement);
      document.getElementById("kakaoPage").classList.toggle("active", !statement);
      document.getElementById("statementTab").classList.toggle("active", statement);
      document.getElementById("kakaoTab").classList.toggle("active", !statement);
    }}

    function scheduleAutoSave() {{
      if (!currentStatementId) return;
      clearTimeout(autoSaveTimer);
      document.getElementById("sendStatus").value = "수정중";
      autoSaveTimer = setTimeout(() => save({{ silent: true }}), 1200);
    }}

    async function save(options = {{}}) {{
      const silent = options.silent === true;
      const url = currentStatementId ? `/api/statements/${{currentStatementId}}` : "/api/statements";
      const method = currentStatementId ? "PUT" : "POST";
      const res = await fetch(url, {{ method, headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(collect()) }});
      const out = await res.json();
      currentStatementId = out.id;
      document.getElementById("sendStatus").value = "저장됨";
      document.getElementById("shareMessage").value = out.message;
      document.getElementById("downloadLink").href = `/download/${{out.id}}.html`;
      document.getElementById("downloadLink").hidden = false;
      await loadSaved();
      document.getElementById("sendStatus").value = silent ? "자동저장됨" : "저장됨";
      if (!silent) {{
        showPage("kakao");
        alert("저장했습니다. 카카오톡 화면에서 메시지를 복사하세요.");
      }}
    }}

    async function loadStatement(id) {{
      const res = await fetch(`/api/statements/${{id}}`);
      const out = await res.json();
      currentStatementId = out.id;
      fill(out.data);
      document.getElementById("sendStatus").value = "수정중";
      document.getElementById("shareMessage").value = out.message;
      document.getElementById("downloadLink").href = `/download/${{out.id}}.html`;
      document.getElementById("downloadLink").hidden = false;
      showPage("statement");
    }}

    async function copyMessage() {{
      if (!document.getElementById("shareMessage").value) await save();
      await navigator.clipboard.writeText(document.getElementById("shareMessage").value);
      alert("카카오톡 메시지를 복사했습니다.");
    }}

    async function nativeShare() {{
      if (!document.getElementById("shareMessage").value) await save();
      const text = document.getElementById("shareMessage").value;
      const downloadLink = document.getElementById("downloadLink");
      if (navigator.share) {{
        if (!downloadLink.hidden && window.File && navigator.canShare) {{
          try {{
            const response = await fetch(downloadLink.href);
            const blob = await response.blob();
            const file = new File([blob], "거래명세서.html", {{ type: "text/html" }});
            if (navigator.canShare({{ files: [file] }})) {{
              await navigator.share({{ title: "거래명세서", text, files: [file] }});
              return;
            }}
          }} catch (error) {{}}
        }}
        await navigator.share({{ title: "거래명세서", text }});
      }} else {{
        await copyMessage();
      }}
    }}

    async function loadSaved() {{
      const res = await fetch("/api/statements");
      const list = await res.json();
      document.getElementById("savedList").innerHTML = list.map((item) => `<div class="saved-item"><strong>${{item.statement_no || "문서번호 없음"}}</strong><span>${{item.customer_name || "거래처 없음"}} · ${{item.updated_at || item.created_at}}</span><button type="button" data-edit="${{item.id}}">불러와 수정</button><a href="/statement/${{item.id}}" target="_blank">보기 / 인쇄</a></div>`).join("");
    }}

    function clearDoc() {{
      ids.forEach((id) => document.getElementById(id).value = "");
      currentStatementId = null;
      document.getElementById("statementDate").value = today();
      document.getElementById("statementNo").value = today().replaceAll("-", "") + "-001";
      document.getElementById("sendStatus").value = "작성중";
      itemBody.innerHTML = "";
      for (let i = 0; i < 8; i += 1) row();
      document.getElementById("shareMessage").value = "";
      document.getElementById("downloadLink").hidden = true;
      showPage("statement");
      updateTotals();
    }}

    function compactPrintDates() {{
      itemBody.querySelectorAll(".date").forEach((input) => {{
        if (!input.dataset.fullDate) input.dataset.fullDate = input.value;
        const value = input.value || "";
        if (value.length >= 10) {{
          input.type = "text";
          input.value = value.slice(5, 10);
        }}
      }});
    }}

    function restorePrintDates() {{
      itemBody.querySelectorAll(".date").forEach((input) => {{
        input.type = "date";
        if (input.dataset.fullDate) {{
          input.value = input.dataset.fullDate;
          delete input.dataset.fullDate;
        }}
      }});
    }}

    document.getElementById("addRow").addEventListener("click", () => row());
    document.getElementById("save").addEventListener("click", save);
    document.getElementById("print").addEventListener("click", () => window.print());
    document.getElementById("copyMessage").addEventListener("click", copyMessage);
    document.getElementById("nativeShare").addEventListener("click", nativeShare);
    document.getElementById("mobileSave").addEventListener("click", save);
    document.getElementById("mobileShare").addEventListener("click", nativeShare);
    document.getElementById("savedList").addEventListener("click", (event) => {{
      const button = event.target.closest("[data-edit]");
      if (button) loadStatement(button.dataset.edit);
    }});
    document.getElementById("statementTab").addEventListener("click", () => showPage("statement"));
    document.getElementById("kakaoTab").addEventListener("click", () => showPage("kakao"));
    document.getElementById("statementPage").addEventListener("input", scheduleAutoSave);
    document.getElementById("clear").addEventListener("click", () => {{ if (confirm("새 문서를 작성할까요?")) clearDoc(); }});
    window.addEventListener("beforeprint", compactPrintDates);
    window.addEventListener("afterprint", restorePrintDates);
    for (let i = 0; i < 8; i += 1) row();
    loadSaved();

    if ("serviceWorker" in navigator) {{
      window.addEventListener("load", () => {{
        navigator.serviceWorker.register("/service-worker.js").catch(() => {{}});
      }});
    }}
  </script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type="text/html; charset=utf-8", headers=None):
        encoded = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            self.send(200, render_app())
            return
        if path == "/manifest.webmanifest":
            self.send(200, manifest_json(), "application/manifest+json; charset=utf-8")
            return
        if path == "/service-worker.js":
            self.send(
                200,
                service_worker_js(),
                "application/javascript; charset=utf-8",
                headers={"Service-Worker-Allowed": "/"},
            )
            return
        if path == "/api/statements":
            with db() as conn:
                rows = conn.execute(
                    "SELECT id, statement_no, customer_name, customer_phone, created_at, updated_at FROM statements ORDER BY id DESC LIMIT 30"
                ).fetchall()
            self.send(200, json.dumps([dict(row) for row in rows], ensure_ascii=False), "application/json; charset=utf-8")
            return
        if path.startswith("/api/statements/"):
            statement_id = path.rsplit("/", 1)[-1]
            with db() as conn:
                row = conn.execute("SELECT * FROM statements WHERE id = ?", (statement_id,)).fetchone()
            if not row:
                self.send(404, json.dumps({"error": "거래명세서를 찾을 수 없습니다."}, ensure_ascii=False), "application/json; charset=utf-8")
                return
            data = json.loads(row["data"])
            self.send(
                200,
                json.dumps(
                    {
                        "id": row["id"],
                        "data": data,
                        "message": kakao_message(data, row["id"]),
                        "view_url": f"/statement/{row['id']}",
                        "download_url": f"/download/{row['id']}.html",
                    },
                    ensure_ascii=False,
                ),
                "application/json; charset=utf-8",
            )
            return
        if path.startswith("/statement/"):
            statement_id = path.rsplit("/", 1)[-1]
            with db() as conn:
                row = conn.execute("SELECT * FROM statements WHERE id = ?", (statement_id,)).fetchone()
            if not row:
                self.send(404, "거래명세서를 찾을 수 없습니다.")
                return
            self.send(200, statement_html(json.loads(row["data"]), statement_id, toolbar=True))
            return
        if path.startswith("/download/") and path.endswith(".html"):
            statement_id = path.removeprefix("/download/").removesuffix(".html")
            with db() as conn:
                row = conn.execute("SELECT * FROM statements WHERE id = ?", (statement_id,)).fetchone()
            if not row:
                self.send(404, "거래명세서를 찾을 수 없습니다.")
                return
            filename = f"transaction_statement_{statement_id}.html"
            self.send(
                200,
                statement_html(json.loads(row["data"]), statement_id, standalone=True),
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
            return
        if path.startswith("/static/"):
            asset = (STATIC_DIR / path.removeprefix("/static/")).resolve()
            if STATIC_DIR.resolve() not in asset.parents or not asset.exists():
                self.send(404, "파일을 찾을 수 없습니다.")
                return
            content_type = "image/gif" if asset.suffix.lower() == ".gif" else "application/octet-stream"
            self.send(200, asset.read_bytes(), content_type)
            return
        self.send(404, "페이지를 찾을 수 없습니다.")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/statements":
            self.send(404, "페이지를 찾을 수 없습니다.")
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO statements (statement_no, customer_name, customer_phone, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    data.get("statementNo"),
                    data.get("customerName"),
                    data.get("customerPhone"),
                    json.dumps(data, ensure_ascii=False),
                    created_at,
                    created_at,
                ),
            )
            statement_id = cur.lastrowid
        self.send(
            200,
            json.dumps(
                {
                    "id": statement_id,
                    "message": kakao_message(data, statement_id),
                    "view_url": f"/statement/{statement_id}",
                    "download_url": f"/download/{statement_id}.html",
                },
                ensure_ascii=False,
            ),
            "application/json; charset=utf-8",
        )

    def do_PUT(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/statements/"):
            self.send(404, "페이지를 찾을 수 없습니다.")
            return
        statement_id = parsed.path.rsplit("/", 1)[-1]
        length = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        with db() as conn:
            cur = conn.execute(
                """
                UPDATE statements
                SET statement_no = ?, customer_name = ?, customer_phone = ?, data = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    data.get("statementNo"),
                    data.get("customerName"),
                    data.get("customerPhone"),
                    json.dumps(data, ensure_ascii=False),
                    updated_at,
                    statement_id,
                ),
            )
        if cur.rowcount == 0:
            self.send(404, json.dumps({"error": "거래명세서를 찾을 수 없습니다."}, ensure_ascii=False), "application/json; charset=utf-8")
            return
        self.send(
            200,
            json.dumps(
                {
                    "id": int(statement_id),
                    "message": kakao_message(data, statement_id),
                    "view_url": f"/statement/{statement_id}",
                    "download_url": f"/download/{statement_id}.html",
                },
                ensure_ascii=False,
            ),
            "application/json; charset=utf-8",
        )

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    db().close()
    pc_url = f"http://127.0.0.1:{PORT}/"
    mobile_url = f"http://{get_lan_ip()}:{PORT}/"
    print(f"PC 접속 주소: {pc_url}")
    print(f"휴대폰 접속 주소: {mobile_url}")
    print("휴대폰은 PC와 같은 와이파이에 연결한 뒤 위 주소로 접속하세요.")
    print("종료하려면 이 창에서 Ctrl+C를 누르세요.")
    webbrowser.open(pc_url)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
