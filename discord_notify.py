
# -*- coding: utf-8 -*-
"""
Discord Webhook にメンションを自動付与して通知するフルコード
- DISCORD_WEBHOOK_URL      : Webhook URL
- DISCORD_MENTION_USER_ID  : メンション対象のユーザーID（18桁数字）
- DISCORD_USE_EVERYONE     : "1" なら @everyone を使用（権限必須）
- DISCORD_USE_HERE         : "1" なら @here を使用
- DISCORD_WAIT             : "1" なら webhook の wait=true を付与（既定で有効）
- DISCORD_THREAD_ID        : スレッドID（任意）
- DISCORD_USER_AGENT       : UA 文字列（任意）
"""

import os
import json
import time
from typing import Any, Dict, Optional, Tuple

# ========== ヘルパー：メンションと allowed_mentions を生成 ==========
def _build_mention_and_allowed() -> Tuple[str, Dict[str, Any]]:
    """
    送信前にメンション文字列と allowed_mentions を決定。
    優先順位：
      1) DISCORD_MENTION_USER_ID があれば <@ID>
      2) DISCORD_USE_EVERYONE=1 なら @everyone
      3) DISCORD_USE_HERE=1 なら @here
      4) それ以外はメンションなし
    """
    mention = ""
    allowed: Dict[str, Any] = {}

    uid = os.getenv("DISCORD_MENTION_USER_ID", "").strip()
    use_everyone = os.getenv("DISCORD_USE_EVERYONE", "0").strip() == "1"
    use_here = os.getenv("DISCORD_USE_HERE", "0").strip() == "1"

    if uid:
        # ユーザーメンションが最優先（あなた一人のサーバーならこれが最適）
        mention = f"<@{uid}>"
        allowed = {"allowed_mentions": {"parse": [], "users": [uid]}}
    elif use_everyone:
        mention = "@everyone"
        allowed = {"allowed_mentions": {"parse": ["everyone"]}}
    elif use_here:
        mention = "@here"
        # @here は parse=["everyone"] でも機能するクライアントがあるが、
        # ここでは roles/users を parse せず、@here の文言を content に載せる運用。
        allowed = {"allowed_mentions": {"parse": []}}
    else:
        allowed = {"allowed_mentions": {"parse": []}}

    return mention, allowed


# ========== ユーティリティ ==========
DISCORD_CONTENT_LIMIT = 2000
DISCORD_EMBED_DESC_LIMIT = 4096

def _split_content(s: str, limit: int = DISCORD_CONTENT_LIMIT):
    pages = []
    cur = (s or "").strip()
    while len(cur) > limit:
        cut = cur.rfind("\n", 0, limit)
        if cut < 0:
            cut = cur.rfind(" ", 0, limit)
        if cut < 0:
            cut = limit
        pages.append(cur[:cut].rstrip())
        cur = cur[cut:].lstrip()
    if cur:
        pages.append(cur)
    return pages

def _truncate_embed_description(desc: str) -> str:
    if desc is None:
        return ""
    if len(desc) <= DISCORD_EMBED_DESC_LIMIT:
        return desc
    return desc[:DISCORD_EMBED_DESC_LIMIT - 3] + "..."


# ========== Webhook クライアント ==========
class DiscordWebhookClient:
    def __init__(
        self,
        webhook_url: str,
        thread_id: Optional[str] = None,
        wait: bool = True,
        user_agent: Optional[str] = None,
        timeout_sec: int = 10,
    ):
        if not webhook_url:
            raise ValueError("webhook_url is required")
        self.webhook_url = webhook_url
        self.thread_id = thread_id
        self.wait = wait
        self.timeout_sec = timeout_sec
        self.user_agent = user_agent or "facility-monitor/mention/1.0 (+python-urllib)"

    @staticmethod
    def from_env() -> "DiscordWebhookClient":
        url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        th = os.getenv("DISCORD_THREAD_ID", "").strip() or None
        wt = os.getenv("DISCORD_WAIT", "1").strip() == "1"
        ua = os.getenv("DISCORD_USER_AGENT", "").strip() or None
        return DiscordWebhookClient(webhook_url=url, thread_id=th, wait=wt, user_agent=ua)

    def _post(self, payload: Dict[str, Any]) -> Tuple[int, str, Dict[str, Any]]:
        import urllib.request, urllib.error, ssl
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        url = self.webhook_url
        params = []
        if self.wait:
            params.append("wait=true")
        if self.thread_id:
            params.append(f"thread_id={self.thread_id}")
        if params:
            url = f"{url}?{'&'.join(params)}"

        req = urllib.request.Request(
            url=url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": self.user_agent},
        )
        ctx = ssl.create_default_context()
        tries = 0
        max_tries = 3
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
                if status == 429 and tries < max_tries:
                    retry_after = float(headers.get("Retry-After", "1.0"))
                    print(f"[WARN] Discord 429: retry_after={retry_after}s; body={body}", flush=True)
                    time.sleep(max(0.5, retry_after))
                    continue
                return status, body, headers
            except Exception as e:
                return -1, f"Exception: {e}", {}

    def send_text(self, content: str) -> bool:
        mention, allowed = _build_mention_and_allowed()
        pages = _split_content(content or "", limit=DISCORD_CONTENT_LIMIT)
        ok_all = True
        for i, page in enumerate(pages, 1):
            page_with_mention = f"{mention} {page}".strip() if mention else page
            payload = {"content": page_with_mention, **allowed}
            status, body, headers = self._post(payload)
            if status in (200, 204):
                print(f"[INFO] Discord notified (text p{i}/{len(pages)}): {len(page_with_mention)} chars body={body}", flush=True)
            else:
                ok_all = False
                print(f"[ERROR] Discord text failed (p{i}/{len(pages)}): HTTP {status} body={body}", flush=True)
        return ok_all

    def send_embed(self, title: str, description: str, color: int = 0x00B894, footer_text: str = "Facility monitor") -> bool:
        mention, allowed = _build_mention_and_allowed()
        embed = {
            "title": title,
            "description": _truncate_embed_description(description or ""),
            "color": color,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+09:00"),  # JSTの簡易表記
            "footer": {"text": footer_text},
        }
        payload = {"content": mention if mention else "", "embeds": [embed], **allowed}
        status, body, headers = self._post(payload)
        if status in (200, 204):
            print(f"[INFO] Discord notified (embed): title='{title}' len={len(description or '')} body={body}", flush=True)
            return True
        print(f"[WARN] Embed failed: HTTP {status}; body={body}. Falling back to plain text.", flush=True)
        text = f"**{title}**\n{description or ''}"
        return self.send_text(text)


# ========== サンプル main（動作確認用） ==========
def main():
    # ここは最小限の動作確認。環境変数で設定してください。
    # 例：
    #   setx DISCORD_WEBHOOK_URL "https://discord.com/api/webhooks/xxxx/xxxx"
    #   setx DISCORD_MENTION_USER_ID "123456789012345678"
    #
    # Linux/macOS:
    #   export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxxx/xxxx"
    #   export DISCORD_MENTION_USER_ID="123456789012345678"

    client = DiscordWebhookClient.from_env()
    # テキスト通知
    client.send_text("テスト通知：空き枠が増えました。")
    # Embed通知
    client.send_embed(
        title="南浦和 2026年1月",
        description="2026年1月12日（月） : ✖️ → ⭕️\n2026年1月15日（木） : △ → ⭕️",
        color=0x3498DB,
        footer_text="Facility monitor",
    )

if __name__ == "__main__":
    main()
