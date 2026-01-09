
# -*- coding: utf-8 -*-
"""
Discord Webhook 完全対応モジュール
- Embed送信（403/権限不足時はテキストに自動フォールバック）
- 長文分割（2000文字ガード）、Embed description 4096文字ガード
- Forum/スレッド対応（?thread_id=...）、?wait=true で本文取得
- レートリミット(429)対応：Retry-After を尊重して再送
- 失敗時のレスポンス本文を常時ログ表示
- User-Agent の調整（WAF回避）
"""

import os
import json
import time
import datetime
import urllib.request
import urllib.error
import ssl
from typing import Optional, Dict, Any, List, Tuple

# ====== 時刻ユーティリティ ======
def _jst_now() -> datetime.datetime:
    try:
        import pytz
        jst = pytz.timezone("Asia/Tokyo")
        return datetime.datetime.now(jst)
    except Exception:
        return datetime.datetime.now()

# ====== 文字列ユーティリティ ======
DISCORD_CONTENT_LIMIT = 2000          # content の最大文字数
DISCORD_EMBED_DESC_LIMIT = 4096       # embed.description 上限
DISCORD_MESSAGE_TOTAL_LIMIT = 6000    # 1メッセージ総文字上限（目安）

def _split_content(s: str, limit: int = DISCORD_CONTENT_LIMIT) -> List[str]:
    """Discordのcontent上限に合わせて安全に分割。改行優先・語切れ緩和。"""
    out: List[str] = []
    cur = s.strip()
    while len(cur) > limit:
        # 直近の改行で切る
        cut = cur.rfind("\n", 0, limit)
        if cut < 0:
            # 改行がない場合、空白で切る
            cut = cur.rfind(" ", 0, limit)
        if cut < 0:
            cut = limit
        out.append(cur[:cut].rstrip())
        cur = cur[cut:].lstrip()
    if cur:
        out.append(cur)
    return out

def _truncate_embed_description(desc: str) -> str:
    if desc is None:
        return ""
    if len(desc) <= DISCORD_EMBED_DESC_LIMIT:
        return desc
    # 末尾に省略記号
    return desc[:DISCORD_EMBED_DESC_LIMIT - 3] + "..."

# ====== Discord Webhook クライアント ======
class DiscordWebhookClient:
    def __init__(self, webhook_url: str,
                 thread_id: Optional[str] = None,
                 wait: bool = True,
                 user_agent: Optional[str] = None,
                 timeout_sec: int = 10):
        if not webhook_url:
            raise ValueError("webhook_url is required")
        self.webhook_url = webhook_url
        self.thread_id = thread_id
        self.wait = wait
        self.timeout_sec = timeout_sec
        self.user_agent = user_agent or "facility-monitor/1.0 (+python-urllib)"

    @staticmethod
    def from_env() -> "DiscordWebhookClient":
        url = os.getenv("DISCORD_WEBHOOK_URL", "").strip() or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        th = os.getenv("DISCORD_THREAD_ID", "").strip() or None
        wt = os.getenv("DISCORD_WAIT", "1").strip() == "1"
        ua = os.getenv("DISCORD_USER_AGENT", "").strip() or None
        return DiscordWebhookClient(webhook_url=url, thread_id=th, wait=wt, user_agent=ua)

    # 実送信（429リトライ、本文ログ）
    def _post(self, payload: Dict[str, Any]) -> Tuple[int, str, Dict[str, Any]]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        # URL クエリの組み立て
        url = self.webhook_url
        params = []
        if self.wait:
            params.append("wait=true")
        if self.thread_id:
            params.append(f"thread_id={self.thread_id}")
        if params:
            url = f"{url}?{'&'.join(params)}"

        req = urllib.request.Request(url=url, data=data,
                                     headers={
                                         "Content-Type": "application/json",
                                         "User-Agent": self.user_agent,
                                     })
        ctx = ssl.create_default_context()

        tries = 0
        max_tries = 3
        last_body = ""
        last_headers = {}

        while True:
            tries += 1
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=self.timeout_sec) as resp:
                    body = resp.read().decode("utf-8", errors="ignore")
                    status = getattr(resp, "status", 200)
                    headers = dict(resp.headers) if resp.headers else {}
                    return status, body, headers
            except urllib.error.HTTPError as e:
                status = e.code
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    body = ""
                headers = dict(e.headers) if e.headers else {}
                last_body = body
                last_headers = headers

                # レートリミット(429)はRetry-After尊重
                if status == 429 and tries < max_tries:
                    retry_after = float(headers.get("Retry-After", "1.0"))
                    print(f"[WARN] Discord 429: retry_after={retry_after}s; body={body}", flush=True)
                    time.sleep(max(0.5, retry_after))
                    continue

                # それ以外は即返す
                return status, body, headers
            except Exception as e:
                # ネットワーク系例外はそのまま返す
                return -1, f"Exception: {e}", {}

    # Embed送信（403時テキストフォールバック）
    def send_embed(self, title: str, description: str,
                   color: int = 0x00B894,
                   footer_text: str = "Facility monitor") -> bool:
        embed = {
            "title": title,
            "description": _truncate_embed_description(description or ""),
            "color": color,
            "timestamp": _jst_now().isoformat(),
            "footer": {"text": footer_text},
        }
        payload = {"embeds": [embed]}

        status, body, headers = self._post(payload)
        if status in (200, 204):
            print(f"[INFO] Discord notified (embed): title='{title}' len={len(description or '')} body={body}", flush=True)
            return True

        # 403: Missing Permissions 等 → テキストフォールバック
        print(f"[WARN] Embed failed: HTTP {status}; body={body}. Falling back to plain text.", flush=True)
        text = f"**{title}**\n{description or ''}"
        return self.send_text(text)

    # テキスト送信（長文分割）
    def send_text(self, content: str) -> bool:
        pages = _split_content(content or "", limit=DISCORD_CONTENT_LIMIT)
        ok_all = True
        for i, page in enumerate(pages, 1):
            payload = {"content": page}
            status, body, headers = self._post(payload)
            if status in (200, 204):
                print(f"[INFO] Discord notified (text p{i}/{len(pages)}): {len(page)} chars body={body}", flush=True)
            else:
                ok_all = False
                print(f"[ERROR] Discord text failed (p{i}/{len(pages)}): HTTP {status} body={body}", flush=True)
        return ok_all

# ====== 集約通知（施設×月：改善行） ======
def send_aggregate_lines(webhook_url: Optional[str],
                         facility_alias: str,
                         month_text: str,
                         lines: List[str]) -> None:
    """
    施設×月の改善セル一覧をDiscordへ通知。
    - Embed優先、失敗時はテキストへ自動フォールバック
    - 空の lines は投稿しない
    環境変数:
      - DISCORD_FORCE_TEXT: "1" なら常にテキスト
      - DISCORD_THREAD_ID: Forum/スレッド投稿先が必要な場合の thread_id
      - DISCORD_WAIT: "1" でレスポンス本文取得（デフォルトON）
      - DISCORD_MAX_LINES: 行数上限（長文抑制）
    """
    if not webhook_url:
        return
    if not lines:
        return

    force_text = (os.getenv("DISCORD_FORCE_TEXT", "0").strip() == "1")
    max_lines_env = os.getenv("DISCORD_MAX_LINES", "").strip()
    max_lines = None
    try:
        if max_lines_env:
            max_lines = max(1, int(max_lines_env))
    except Exception:
        max_lines = None
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ほか {len(lines) - max_lines} 件"]

    title = f"{facility_alias} {month_text}"
    description = "\n".join(lines)

    client = DiscordWebhookClient.from_env()
    # from_env は DISCORD_WEBHOOK_URL を見るが、明示引数が優先
    client.webhook_url = webhook_url

    if force_text:
        content = f"**{title}**\n{description}"
        client.send_text(content)
        return

    # まず Embed を試し、失敗時はテキストへ
    client.send_embed(title=title, description=description, color=0x00B894, footer_text="Facility monitor")

