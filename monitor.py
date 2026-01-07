
# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import datetime
import io
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageChops, ImageDraw

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE_DIR = Path(__file__).resolve().parent
SNAP_DIR = BASE_DIR / "snapshots"
CONFIG_PATH = BASE_DIR / "config.json"

# 必須：GitHub Secretsに設定して環境変数化
BASE_URL = os.getenv("BASE_URL")  # 例: "https://example.saitama.jp/reserve/"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

TITLE_FOR_DISCORD = "岩槻0"  # 投稿タイトル指定

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def ensure_dirs():
    SNAP_DIR.mkdir(parents=True, exist_ok=True)

def navigate_and_capture(config, out_path):
    """
    Playwrightで指定のボタンクリック手順により目的ページへ到達し、スクリーンショットを保存。
    """
    if not BASE_URL:
        raise RuntimeError("BASE_URL が未設定です。GitHub Secrets または環境変数で設定してください。")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1200})
        page = context.new_page()

        # 初期ページへ
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)

        # クリック手順
        for label in config["click_sequence"]:
            clicked = False
            for locator in [
                page.get_by_role("link", name=label, exact=True),
                page.get_by_role("button", name=label, exact=True),
                page.get_by_text(label, exact=True),
                page.locator(f"text={label}")
            ]:
                try:
                    locator.wait_for(timeout=15000)
                    locator.click(timeout=15000)
                    page.wait_for_load_state("networkidle", timeout=30000)
                    clicked = True
                    break
                except PlaywrightTimeoutError:
                    # 該当ロケータで見つからなければ次パターンへ
                    continue

            if not clicked:
                raise RuntimeError(f"クリック対象が見つかりませんでした：『{label}』")

        # スクリーンショット（カレンダー全体）
        if config.get("full_page_screenshot", True):
            page.wait_for_timeout(1000)  # レイアウト安定待ち
            page.screenshot(path=str(out_path), full_page=True)
        else:
            page.screenshot(path=str(out_path), full_page=False)

        context.close()
        browser.close()


def compute_diff_boxes(prev_img, curr_img, diff_threshold_pixel=25, tile_size=50, tile_pixel_threshold=150):
    """
    差分画像を作成し、タイル単位で差分が顕著な領域を矩形化して返す。
    """
    # サイズ差がある場合は現在画像に合わせて過去画像をリサイズ
    if prev_img.size != curr_img.size:
        prev_img = prev_img.resize(curr_img.size, Image.LANCZOS)

    prev_rgb = prev_img.convert("RGB")
    curr_rgb = curr_img.convert("RGB")

    # ピクセル差分（絶対差）
    diff_img = ImageChops.difference(prev_rgb, curr_rgb).convert("L")
    diff_np = np.array(diff_img)

    # 閾値で二値化
    mask = (diff_np > diff_threshold_pixel).astype(np.uint8)

    h, w = mask.shape
    boxes = []

    # タイル分割で差分検知
    for y in range(0, h, tile_size):
        for x in range(0, w, tile_size):
            tile = mask[y:min(y+tile_size, h), x:min(x+tile_size, w)]
            changed = int(tile.sum())
            if changed >= tile_pixel_threshold:
                boxes.append([x, y, min(x+tile_size, w), min(y+tile_size, h)])

    # 矩形マージ（重なり・隣接を簡易統合）
    boxes = merge_rectangles(boxes)

    return boxes, diff_img


def rectangles_overlap_or_adjacent(a, b, pad=5):
    """
    2矩形が重なる・近接（パディング内）なら True
    """
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return not (ax2 + pad < bx1 or bx2 + pad < ax1 or ay2 + pad < by1 or by2 + pad < ay1)


def merge_rectangles(rects, pad=5):
    """
    単純な重なり・近接マージ処理
    """
    merged = []
    rects = rects[:]
    while rects:
        base = rects.pop(0)
        changed = True
        while changed:
            changed = False
            keep = []
            for r in rects:
                if rectangles_overlap_or_adjacent(base, r, pad=pad):
                    # 結合（外接矩形）
                    base = [
                        min(base[0], r[0]),
                        min(base[1], r[1]),
                        max(base[2], r[2]),
                        max(base[3], r[3]),
                    ]
                    changed = True
                else:
                    keep.append(r)
            rects = keep
        merged.append(base)
    return merged


def draw_yellow_highlight(base_img, boxes, alpha=160):
    """
    差分領域に黄色半透明の矩形を描画。
    """
    overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for (x1, y1, x2, y2) in boxes:
        # 塗りつぶし（半透明黄色）
        draw.rectangle([x1, y1, x2, y2], fill=(255, 255, 0, alpha))
        # 枠線（不透明黄色）
        draw.rectangle([x1, y1, x2, y2], outline=(255, 255, 0, 255), width=3)

    composed = base_img.convert("RGBA")
    composed = Image.alpha_composite(composed, overlay)
    return composed.convert("RGB")


def send_to_discord(image_path, message_title="岩槻0", extra_text=None):
    """
    Discord Webhookへ画像を送信。タイトルをメッセージ本文として送付。
    """
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("DISCORD_WEBHOOK_URL が未設定です。GitHub Secrets または環境変数で設定してください。")

    content = message_title
    if extra_text:
        content += f"\n{extra_text}"

    files = {"file": open(image_path, "rb")}
    data = {"content": content}

    resp = requests.post(DISCORD_WEBHOOK_URL, data=data, files=files, timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"Discord送信に失敗しました: HTTP {resp.status_code} {resp.text}")


def main():
    ensure_dirs()
    config = load_config()

    now = datetime.datetime.now()
    current_path = SNAP_DIR / "current.png"
    latest_path = SNAP_DIR / "latest.png"
    ts = now.strftime("%Y%m%d_%H%M%S")
    diff_out_path = SNAP_DIR / f"diff_{ts}.png"

    # 1) 現在スクショ取得
    navigate_and_capture(config, current_path)

    # 2) 過去スクショあり→差分検出
    if latest_path.exists():
        prev_img = Image.open(latest_path)
        curr_img = Image.open(current_path)

        boxes, diff_img = compute_diff_boxes(
            prev_img, curr_img,
            diff_threshold_pixel=config.get("diff_threshold_pixel", 25),
            tile_size=config.get("tile_size", 50),
            tile_pixel_threshold=config.get("tile_pixel_threshold", 150),
        )

        if boxes:
            # 差分あり：黄色マーキング画像生成
            highlighted = draw_yellow_highlight(curr_img, boxes, alpha=config.get("yellow_alpha", 160))
            highlighted.save(diff_out_path, format="PNG")

            # Discord送信（タイトル「岩槻0」）
            jst_str = now.strftime("%Y-%m-%d %H:%M:%S")
            extra_text = f"差分を検知しました（{jst_str} JST）。"
            send_to_discord(str(diff_out_path), message_title=TITLE_FOR_DISCORD, extra_text=extra_text)

            # latestを更新
            curr_img.save(latest_path)
        else:
            # 差分なし：latestを更新のみ（サイズ差などの変動に備える）
            curr_img.save(latest_path)
    else:
        # 初回：基準画像としてlatestを作成
        Image.open(current_path).save(latest_path)

    # 現在スクショは作業用なので削除しても良い（残したいならこの行を消してください）
    try:
        os.remove(current_path)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)
