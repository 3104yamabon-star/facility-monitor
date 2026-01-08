
# -*- coding: utf-8 -*-
"""
さいたま市 施設予約システムの空き状況監視（改善のみ通知/キャプチャ保存）

【この版の主な仕様】
- 施設ごとの month_shifts（例：当月=0、翌月=1、+2ヶ月=2、+3ヶ月=3）に従い、
  当月から指定された月まで「次の月」を連続クリックして到達・キャプチャ保存。
  - 南浦和/岩槻: [0,1,2,3]（当月〜3ヶ月後まで）
  - 岸町/鈴谷:   [0,1]      （当月〜翌月まで）
- 各月のカレンダー要素を抽出し、HTML/PNG を snapshots/<施設短縮名>/<YYYY年M月>/ に保存。
- ステータス検出は文字（「全て空き」「一部空き」「予約あり」「受付期間外」「休館日」「保守日」「雨天」など）
  を config.json の patterns に基づき記号（○/△/×）へ正規化して利用可能。

【必須環境変数（GitHub Secretsなど）】
- BASE_URL: 例 "https://saitama.rsv.ws-scs.jp/web/"
- DISCORD_WEBHOOK_URL: Discord に通知を送る場合のみ必須（本コードでは通知は任意）

【設定ファイル】
- config.json（本物の JSON 形式）
  - facilities[].name / click_sequence / month_shifts（例: [0,1,2,3]）
  - next_month_label: "次の月"（サイトUIの表記に合わせる）
  - status_patterns / css_class_patterns / debug など
"""

import os
import sys
import json
import re
import datetime
from pathlib import Path
import requests
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# （任意）JST時間帯チェック用
try:
    import pytz
except Exception:
    pytz = None

# === 環境変数（GitHub Secrets で設定） ===
BASE_URL = os.getenv("BASE_URL")  # 例: "https://saitama.rsv.ws-scs.jp/web/"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# === ファイルパス ===
BASE_DIR = Path(__file__).resolve().parent
SNAP_DIR = BASE_DIR / "snapshots"
CONFIG_PATH = BASE_DIR / "config.json"

# === 施設短縮名（Discordタイトルや保存ディレクトリ用） ===
FACILITY_TITLE_ALIAS = {
    "岩槻南部公民館": "岩槻",
    "南浦和コミュニティセンター": "南浦和",
    "岸町公民館": "岸町",
    "鈴谷公民館": "鈴谷",
}

# === ステータスの序列（改善判定に使用） ===
STATUS_RANK = {"×": 0, "△": 1, "○": 2, "〇": 2}

# --------------------------------------------------------------------------------
# 基本ユーティリティ
# --------------------------------------------------------------------------------
def ensure_dirs():
    SNAP_DIR.mkdir(parents=True, exist_ok=True)

def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text("utf-8"))
    except Exception as e:
        print(f"[ERROR] config.json の読み込みに失敗: {e}", flush=True)
        raise

def jst_now():
    if pytz is None:
        return datetime.datetime.now()
    jst = pytz.timezone("Asia/Tokyo")
    return datetime.datetime.now(jst)

def is_within_monitoring_window(start_hour=5, end_hour=23):
    """JSTで 05:00〜23:59 を監視対象にする"""
    try:
        now = jst_now()
        return start_hour <= now.hour <= end_hour
    except Exception:
        return True  # 失敗時は実行

# --------------------------------------------------------------------------------
# Playwright 操作（遷移）
# --------------------------------------------------------------------------------
def try_click_text(page, label, timeout_ms=15000, quiet=True):
    """
    指定ラベルのリンク/ボタン/テキストをクリック。
    厳密一致を優先しつつ、フォールバックで text= を使う。
    """
    locators = [
        page.get_by_role("link", name=label, exact=True),
        page.get_by_role("button", name=label, exact=True),
        page.get_by_text(label, exact=True),
        page.locator(f"text={label}"),
    ]
    for locator in locators:
        try:
            locator.wait_for(timeout=timeout_ms)
            locator.click(timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=30000)
            return True
        except Exception as e:
            if not quiet:
                print(f"[WARN] try_click_text: 例外 {e}（label='{label}'）", flush=True)
            continue
    return False

def navigate_to_facility(page, facility):
    """
    トップへ → click_sequence の順で施設の当月ページまで到達
    （鈴谷は click_sequence に「すべて」を含める）
    """
    if not BASE_URL:
        raise RuntimeError("BASE_URL が未設定です。Secrets の BASE_URL に https://saitama.rsv.ws-scs.jp/web/ を入れてください。")
    # トップへ
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_load_state("networkidle", timeout=60000)
    # 任意のダイアログ（同意など）がある場合のフォールバック
    for opt in ["同意する", "OK", "確認", "閉じる"]:
        try_click_text(page, opt, timeout_ms=2000)
    # 施設のクリック手順
    for label in facility["click_sequence"]:
        ok = try_click_text(page, label)
        if not ok:
            raise RuntimeError(f"クリック対象が見つかりません：『{label}』（施設: {facility['name']}）")

def get_current_year_month_text(page):
    """
    ページ本文から 'YYYY年M月' を抽出（例：2026年1月）
    見つからない場合は None
    """
    try:
        text = page.inner_text("body")
        m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text)
        if m:
            return f"{m.group(1)}年{int(m.group(2))}月"
    except Exception:
        pass
    return None

def locate_calendar_root(page, hint):
    """
    カレンダー本体らしき要素（grid/table等）を探索して最もテキスト量が多いものを選ぶ。
    """
    candidates = []
    for sel in ["[role='grid']", "table", "section", "div.calendar", "div"]:
        loc = page.locator(sel)
        cnt = loc.count()
        for i in range(cnt):
            el = loc.nth(i)
            try:
                t = (el.inner_text() or "").strip()
                # ヒント一致 or カレンダーらしい語句を含む
                if (hint and hint in t) or re.search(r"(空き状況|予約あり|一部空き|カレンダー)", t):
                    candidates.append((len(t), el))
            except Exception:
                continue
    if not candidates:
        return page.locator("body")
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]

def dump_calendar_html(calendar_root, out_path):
    """デバッグ用にカレンダー要素の outerHTML を保存"""
    try:
        html = calendar_root.evaluate("el => el.outerHTML")
        Path(out_path).write_text(html, "utf-8")
    except Exception as e:
        print(f"[WARN] calendar HTML dump 失敗: {e}", flush=True)

def take_calendar_screenshot(calendar_root, out_path):
    """カレンダー要素のみスクリーンショット"""
    calendar_root.scroll_into_view_if_needed()
    calendar_root.screenshot(path=str(out_path))

# --------------------------------------------------------------------------------
# ステータス認識（×/△/○/〇）
# --------------------------------------------------------------------------------
def status_from_text(raw_text, patterns):
    """
    テキストからステータスを判断（直書き記号＋限定語彙優先）
    """
    txt = (raw_text or "").strip()
    # 全角スペース→半角、全体を小文字化（限定語彙の誤検知防止のため置換は最小限）
    txt_norm = txt.replace("　", " ").lower()

    # 直書き記号（全角記号の揺れ対応）
    for ch in ["○", "〇", "△", "×"]:
        if ch in txt:
            return ch

    # 文字化された確定語（config.json 側の patterns を優先してチェック）
    for kw in patterns["circle"]:
        if kw.lower() in txt_norm:
            return "○"
    for kw in patterns["triangle"]:
        if kw.lower() in txt_norm:
            return "△"
    for kw in patterns["cross"]:
        if kw.lower() in txt_norm:
            return "×"

    return None

def status_from_img(el, patterns):
    """<img> の alt/title/src からの判定（ログ出力あり）"""
    alt = el.get_attribute("alt") or ""
    title = el.get_attribute("title") or ""
    src = el.get_attribute("src") or ""
    s = status_from_text(alt + " " + title, patterns)
    if s:
        print(f"[DEBUG] status_from_img: alt/titleで検出 status='{s}' alt='{alt[:40]}' title='{title[:40]}'", flush=True)
        return s
    s = status_from_text(src, patterns)
