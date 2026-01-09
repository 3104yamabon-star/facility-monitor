
# -*- coding: utf-8 -*-
"""
さいたま市 施設予約システムの空き状況監視
 - 監視範囲の限定（カレンダー枠）
 - 日付の無いセルをスキップ
 - 次の月遷移の安定化（施設固有セレクタ／outerHTML変化待ち）
 - 集計が変化した時のみタイムスタンプ付き履歴を保存
 - パフォーマンス改善（不要リソースのロード抑制）

【環境変数（GitHub Secrets 想定）】
- BASE_URL: 例 "https://saitama.rsv.ws-scs.jp/web/"
- DISCORD_WEBHOOK_URL: （任意）

【config.json 例】
{
  "facilities": [
    {
      "name": "南浦和コミュニティセンター",
      "click_sequence": ["施設の空き状況", "利用目的から", "屋内スポーツ", "バドミントン", "南浦和コミュニティセンター"],
      "month_shifts": [0, 1, 2, 3],
      "calendar_selector": "table.reservation-calendar",
      "next_month_selector": "a[href*='moveCalender']"
    }
  ],
  "next_month_label": "次の月",
  "calendar_root_hint": "空き状況",
  "status_patterns": { ... },
  "css_class_patterns": { ... },
  "debug": { "dump_calendar_html": true, ... }
}
"""

import os
import sys
import json
import re
import time
import datetime
from pathlib import Path

from PIL import Image, ImageDraw  # 使わないが、既存互換のため残置
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# （任意）JST時間帯チェック用
try:
    import pytz
except Exception:
    pytz = None

# === 環境変数（GitHub Secrets） ===
BASE_URL = os.getenv("BASE_URL")  # 例: "https://saitama.rsv.ws-scs.jp/web/"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# === パス ===
BASE_DIR = Path(__file__).resolve().parent
SNAP_DIR = BASE_DIR / "snapshots"
CONFIG_PATH = BASE_DIR / "config.json"

# === 施設短縮名（保存ディレクトリ用） ===
FACILITY_TITLE_ALIAS = {
    "岩槻南部公民館": "岩槻",
    "南浦和コミュニティセンター": "南浦和",
    "岸町公民館": "岸町",
    "鈴谷公民館": "鈴谷",
}

# === ステータス序列（比較用） ===
STATUS_RANK = {"×": 0, "△": 1, "○": 2, "〇": 2}

# ------------------------------
# 基本ユーティリティ
# ------------------------------
def ensure_dirs():
    SNAP_DIR.mkdir(parents=True, exist_ok=True)

def load_config():
    """純粋JSONとして config.json をロードし、最低限のキーを検証"""
    try:
        text = CONFIG_PATH.read_text("utf-8")
        cfg = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[ERROR] config.json の読み込みに失敗: {e}", flush=True)
        raise
    for key in ["facilities", "status_patterns", "css_class_patterns"]:
        if key not in cfg:
            raise RuntimeError(f"config.json の '{key}' が不足しています")
    return cfg

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

# ------------------------------
# Playwright 操作（遷移）
# ------------------------------
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
            locator.scroll_into_view_if_needed()
            locator.click(timeout=timeout_ms)
            return True
        except Exception as e:
            if not quiet:
                print(f"[WARN] try_click_text: 例外 {e}（label='{label}'）", flush=True)
            continue
    return False

def navigate_to_facility(page, facility):
    """
    トップ → click_sequence の順で施設の当月ページまで到達（初回のみ）
    """
    if not BASE_URL:
        raise RuntimeError("BASE_URL が未設定です。Secrets の BASE_URL に https://saitama.rsv.ws-scs.jp/web/ を入れてください。")

    # トップへ
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    # 初回は要素出現待ち（networkidleは重いので使わない）
    page.wait_for_load_state("domcontentloaded", timeout=30000)

    # 任意ダイアログがあれば閉じる
    for opt in ["同意する", "OK", "確認", "閉じる"]:
        try_click_text(page, opt, timeout_ms=2000)

    # 施設のクリック手順
    for label in facility.get("click_sequence", []):
        ok = try_click_text(page, label)
        if not ok:
            raise RuntimeError(f"クリック対象が見つかりません：『{label}』（施設: {facility.get('name','')}）")

def get_current_year_month_text(page, calendar_root=None):
    """
    ページ（もしくはカレンダー枠）から 'YYYY年M月' を抽出。
    見つからない場合は None。
    """
    pattern = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
    targets = []
    if calendar_root is not None:
        try:
            targets.append(calendar_root.inner_text())
        except Exception:
            pass
    try:
        targets.append(page.inner_text("body"))
    except Exception:
        pass

    for txt in targets:
        if not txt:
            continue
        m = pattern.search(txt)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
            return f"{y}年{mo}月"
    return None

def locate_calendar_root(page, hint: str, facility: dict = None):
    """
    カレンダー枠を厳密に特定する:
    - config の calendar_selector があればそれを最優先
    - グリッド候補は role=grid / table / div.calendar 等だが、
      '曜日文字' と '十分なセル数(>=28)' を持つもののみ採用
    - 失敗時は body にフォールバックせず例外を送出
    """
    # 1) facility 固有セレクタがあれば最優先
    sel_cfg = (facility or {}).get("calendar_selector")
    if sel_cfg:
        loc = page.locator(sel_cfg)
        if loc.count() > 0:
            el = loc.first
            return el

    # 2) 汎用探索（曜日ヘッダとセル数チェック）
    candidates = []
    weekday_markers = ["日曜日", "月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日", "月", "火", "水", "木", "金", "土"]

    for sel in ["[role='grid']", "table", "section", "div.calendar", "div"]:
        loc = page.locator(sel)
        cnt = loc.count()
        for i in range(cnt):
            el = loc.nth(i)
            try:
                t = (el.inner_text() or "").strip()
            except Exception:
                continue

            score = 0
            if hint and hint in t:
                score += 2

            # 曜日が揃っているか（7種のうち4種以上含む）
            wk = sum(1 for w in weekday_markers if w in t)
            if wk >= 4:
                score += 3

            # セル数判定（td / gridcell が 28 以上ある）
            try:
                cells = el.locator(":scope td, :scope [role='gridcell'], :scope .fc-daygrid-day, :scope .calendar-day")
                cell_cnt = cells.count()
                if cell_cnt >= 28:
                    score += 3
            except Exception:
                pass

            if score >= 5:
                candidates.append((score, el))

    if not candidates:
        raise RuntimeError("カレンダー枠の特定に失敗しました（候補が見つからないため監視を中止）。")

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]

def dump_calendar_html(calendar_root, out_path: Path):
    """デバッグ用：カレンダー要素の outerHTML を保存"""
    try:
        html = calendar_root.evaluate("el => el.outerHTML")
        Path(out_path).write_text(html, "utf-8")
    except Exception as e:
        print(f"[WARN] calendar HTML dump 失敗: {e}", flush=True)

def take_calendar_screenshot(calendar_root, out_path: Path):
    """カレンダー要素のみスクリーンショット"""
    calendar_root.scroll_into_view_if_needed()
    calendar_root.screenshot(path=str(out_path))

# ------------------------------
# 月遷移（安定化）
# ------------------------------
def _compute_next_month_text(prev_month_text: str) -> str:
    """'YYYY年M月' → 次月の 'YYYY年M月' を返す（待機の目標に使う）"""
    try:
        m = re.match(r"(\d{4})年(\d{1,2})月", prev_month_text or "")
        if not m:
            return ""
        y, mo = int(m.group(1)), int(m.group(2))
        if mo == 12:
            y += 1
            mo = 1
        else:
            mo += 1
        return f"{y}年{mo}月"
    except Exception:
        return ""

def click_next_month(page,
                     label_primary="次の月",
                     calendar_root=None,
                     prev_month_text=None,
                     wait_timeout_ms=20000,
                     facility=None):
    """
    「次の月」へ遷移（強化版）：
    - 施設固有 next_month_selector を最優先
    - has-text / href*='moveCalender' / href の JS eval の順でフォールバック
    - 待機は「カレンダー枠 outerHTML の変化」を優先し、補助で月テキスト変化
    """
    # 0) facility 固有セレクタがあれば最優先
    sel_cfg = (facility or {}).get("next_month_selector")
    clicked = False

    def _try_click(selector: str):
        nonlocal clicked
        try:
            el = page.locator(selector).first
            el.scroll_into_view_if_needed()
            el.click(timeout=2000)
            clicked = True
        except Exception:
            pass

    if sel_cfg:
        _try_click(sel_cfg)

    # スコープの候補
    scopes = []
    if calendar_root is not None:
        scopes.append(calendar_root)
    scopes.append(page)

    selectors = [
        "a:has-text('次の月')",
        "a:has-text('次')",
        "a[href*='moveCalender']",
    ]

    # フォールバッククリック
    for scope in scopes:
        if clicked:
            break
        for sel in selectors[:2]:
            try:
                el = scope.locator(sel).first
                el.scroll_into_view_if_needed()
                el.click(timeout=2000)
                clicked = True
                break
            except Exception:
                pass
        if clicked:
            break
        # 直接 href*='moveCalender' をクリック
        try:
            el = scope.locator("a[href*='moveCalender']").first
            el.scroll_into_view_if_needed()
            el.click(timeout=2000)
            clicked = True
        except Exception:
            pass
        if clicked:
            break
        # 最終フォールバック：href の JavaScript を eval 実行
        try:
            el = scope.locator("a[href*='moveCalender']").first
            href = el.get_attribute("href") or ""
            if href.startswith("javascript:"):
                js = href[len("javascript:"):].strip()
                page.evaluate(js)
                clicked = True
        except Exception:
            pass

    if not clicked:
        return False

    # === 待機：outerHTML の変化を優先的に検知 ===
    old_html = None
    if calendar_root is not None:
        try:
            old_html = calendar_root.evaluate("el => el.outerHTML")
        except Exception:
            pass

    try:
        if old_html:
            # 枠の差し替え検知（calendar_selector 優先、無ければ一般的候補）
            page.wait_for_function(
                """(old) => {
                    const root =
                        document.querySelector('table.reservation-calendar')
                        || document.querySelector('[role="grid"]')
                        || document.querySelector('table');
                    if (!root) return false;
                    return root.outerHTML !== old;
                }""",
                arg=old_html,
                timeout=wait_timeout_ms
            )
            return True
    except Exception:
        pass

    # 補助：月テキストの変化
    try:
        next_goal = _compute_next_month_text(prev_month_text or "")
        if next_goal:
            page.wait_for_function(
                """(goal) => {
                    const txt = document.body.innerText || '';
                    return txt.includes(goal);
                }""",
                arg=next_goal,
                timeout=wait_timeout_ms
            )
        else:
            page.wait_for_function(
                """(prev) => {
                    const txt = document.body.innerText || '';
                    const m = txt.match(/(\d{4})\s*年\s*(\d{1,2})\s*月/);
                    if (!m) return false;
                    const cur = `${m[1]}年${parseInt(m[2], 10)}月`;
                    return prev && cur !== prev;
                }""",
                arg=prev_month_text or "",
                timeout=wait_timeout_ms
            )
        return True
    except Exception:
        pass

    return False

# ------------------------------
# 空き状況集計（○/△/×）
# ------------------------------
def summarize_vacancies(page, calendar_root, config):
    """
    カレンダー要素から日別の空き状況を抽出し ○/△/× を集計する。
    日付の無いセル（ヘッダや説明セル）はスキップする。
    """
    import re as _re

    patterns = config["status_patterns"]
    summary = {"○": 0, "△": 0, "×": 0, "未判定": 0}
    details = []

    def _status_from_text(raw):
        txt = (raw or "").strip()
        txt_norm = txt.replace("　", " ").lower()

        # 記号の直接検出
        for ch in ["○", "〇", "△", "×"]:
            if ch in txt:
                return {"〇": "○"}.get(ch, ch)

        # パターン（キーワード）で検出
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

    # 対象セル：tbody 内の td に限定（ヘッダ th を除外）
    candidates = calendar_root.locator(":scope tbody td, :scope [role='gridcell']")
    cnt = candidates.count()

    for i in range(cnt):
        el = candidates.nth(i)

        # セル内テキスト
        try:
            txt = (el.inner_text() or "").strip()
        except Exception:
            continue

        # 先頭付近で日付（1日, 2日 ...）を探す
        head = txt[:40]
        mday = _re.search(r"^([1-9]|[12]\d|3[01])\s*日", head, flags=_re.MULTILINE)

        # aria-label/title に日付がある場合も補助検出
        if not mday:
            try:
                aria = el.get_attribute("aria-label") or ""
                title = el.get_attribute("title") or ""
                mday = _re.search(r"([1-9]|[12]\d|3[01])\s*日", aria + " " + title)
            except Exception:
                pass

        # img の alt/title も一応確認
        if not mday:
            try:
                imgs = el.locator("img")
                jcnt = imgs.count()
                for j in range(jcnt):
                    alt = imgs.nth(j).get_attribute("alt") or ""
                    tit = imgs.nth(j).get_attribute("title") or ""
                    mm = _re.search(r"([1-9]|[12]\d|3[01])\s*日", alt + " " + tit)
                    if mm:
                        mday = mm
                        break
            except Exception:
                pass

        # ★ 日付が無いセルはスキップ（ヘッダ・説明セル除外）
        if not mday:
            continue

        day_label = f"{mday.group(0)}"

        # 状態判定：テキスト → 画像 → aria/title → class
        st = _status_from_text(txt)

        if not st:
            try:
                imgs = el.locator("img")
                jcnt = imgs.count()
                for j in range(jcnt):
                    alt = imgs.nth(j).get_attribute("alt") or ""
                    tit = imgs.nth(j).get_attribute("title") or ""
                    src = imgs.nth(j).get_attribute("src") or ""
                    st = _status_from_text(alt + " " + tit) or _status_from_text(src)
                    if st:
                        break
            except Exception:
                pass

        if not st:
            try:
                aria = el.get_attribute("aria-label") or ""
                tit = el.get_attribute("title") or ""
                cls = (el.get_attribute("class") or "").lower()
                st = _status_from_text(aria + " " + tit)

                if not st:
                    for kw in config["css_class_patterns"]["circle"]:
                        if kw in cls:
                            st = "○"; break
                if not st:
                    for kw in config["css_class_patterns"]["triangle"]:
                        if kw in cls:
                            st = "△"; break
                if not st:
                    for kw in config["css_class_patterns"]["cross"]:
                        if kw in cls:
                            st = "×"; break
            except Exception:
                pass

        if not st:
            st = "未判定"

        summary[st] += 1
        details.append({"day": day_label, "status": st, "text": txt})

    return summary, details

# ------------------------------
# 保存ユーティリティ（履歴は変化時のみ）
# ------------------------------
from datetime import datetime

def facility_month_dir(f_short, month_text):
    safe_fac = re.sub(r"[\\/:*?\"<>|]+", "_", f_short)
    safe_month = re.sub(r"[\\/:*?\"<>|]+", "_", month_text or "unknown_month")
    d = SNAP_DIR / safe_fac / safe_month
    d.mkdir(parents=True, exist_ok=True)
    return d

def load_last_summary(outdir: Path) -> dict:
    """過去の status_counts.json を読み、直近 summary を返す。無ければ None。"""
    fp = outdir / "status_counts.json"
    if not fp.exists():
        return None
    try:
        data = json.loads(fp.read_text("utf-8"))
        return data.get("summary")
    except Exception:
        return None

def summaries_changed(prev: dict, cur: dict) -> bool:
    """summary(dict) の変化判定。キー欠損も考慮して厳密比較。"""
    if prev is None and cur is not None:
        return True
    if prev is None and cur is None:
        return False
    keys = {"○", "△", "×", "未判定"}
    for k in keys:
        if prev.get(k, 0) != cur.get(k, 0):
            return True
    return False

def save_calendar_assets(cal_root, outdir: Path, save_timestamped: bool):
    """
    カレンダーHTML/PNGを保存する。
    save_timestamped=True のとき、履歴用にタイムスタンプ付きファイルも作成。
    最新の別名（calendar.html / calendar.png）は毎回更新（運用により変更可）。
    """
    latest_html = outdir / "calendar.html"
    latest_png = outdir / "calendar.png"

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    html_ts = outdir / f"calendar_{ts}.html"
    png_ts = outdir / f"calendar_{ts}.png"

    # 最新を更新
    dump_calendar_html(cal_root, latest_html)
