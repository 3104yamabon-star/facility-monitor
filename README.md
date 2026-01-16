
# README — さいたま市 施設予約システム 空き状況監視ツール
### （月表示監視＋改善日クリックで「空き」時間帯抽出＋Discord通知）

## 1. 概要
本ツールは、さいたま市の公共施設予約システムを自動で巡回し、月間カレンダーの空き状況を解析し、改善が見られた日について時間帯ごとの「空き」を抽出、Discord に通知します。

## 2. 主な機能
- 月間カレンダーの自動解析（○/△/×/休館日/保守日/受付期間外）
- 前回との差分から改善日（×→△、△→○ など）を抽出
- 改善日のみ詳細画面へ遷移して「空き」時間帯を収集
- 施設ごとの色分けやメンション対応を含む Discord 通知
- スナップショット（HTML/PNG）保存とローテーション
- 複数施設・複数月（設定による）を自動で巡回

## 3. インストール手順
```bash
python -m venv .venv
source .venv/bin/activate
pip install playwright pytz jpholiday
python -m playwright install chromium
```

## 4. 設定ファイル `config.json`
添付の `config (1).json` に準拠した設定が必要です。以下は主要項目の説明です。

### 4.1 selectors
```json
"selectors": {
  "back_from_month_selector": "a[href*='gRsvWInstSrchMonthVacantBackAction']",
  "month_table_selector": "table.m_akitablelist",
  "facility_list_selector": "table.tcontent a[href*='gRsvWTransInstSrchInstAction']",
  "facility_link_by_code_template": "table.tcontent a[href*="gRsvWTransInstSrchInstAction"][href*="'{code}'"]",
  "next_month_selector_candidates": [
    "a[href*='moveCalender'][href*='gRsvWInstSrchMonthVacantAction']",
    "a:has-text('次の月')",
    "a:has-text('翌月')"
  ]
}
```

### 4.2 facilities
各施設ごとに遷移手順、部屋選択、クリック補助などを定義します。

例：
```json
{
  "name": "南浦和コミュニティセンター",
  "alias": "南浦和",
  "facility_code": "5140",
  "month_shifts": [0,1,2,3],
  "click_sequence": ["施設の空き状況","利用目的から","屋内スポーツ","バドミントン","南浦和コミュニティセンター"],
  "post_facility_click_steps": [],
  "calendar_selector": "table.m_akitablelist",
  "step_hints": {"施設の空き状況": ".tcontent", "南浦和コミュニティセンター": ".tcontent"}
}
```

### 4.3 status_patterns
```json
"status_patterns": {
  "circle": ["全て空き"],
  "triangle": ["一部空き"],
  "cross": ["予約あり"],
  "holiday": ["休館日"],
  "maintenance": ["保守日"],
  "outside": ["受付期間外"]
}
```

### 4.4 css_class_patterns
セルの class 属性から補助的に判定します。

```json
"css_class_patterns": {
  "circle": ["status-ok","ok","vacant","maru","available","is-available","icon-ok"],
  "triangle": ["status-partial","partial","few","warning","limited","icon-partial"],
  "cross": ["status-ng","full","unavailable","batsu","is-full","icon-ng"]
}
```

## 5. 環境変数
主要な環境変数：
- `BASE_URL`：監視対象サイト
- `DISCORD_WEBHOOK_URL`
- `OUTPUT_DIR`
- `MONITOR_START_HOUR`, `MONITOR_END_HOUR`
- `FAST_ROUTES`
- `GRACE_MS`
- `DISCORD_MENTION_USER_ID`

## 6. 出力構成
```
OUTPUT_DIR/
  └─ 南浦和/
        └─ 2026年1月/
            ├─ calendar.html
            ├─ calendar.png
            ├─ calendar_20260112_103000.html
            ├─ calendar_20260112_103000.png
            └─ status_counts.json
```

## 7. 実行方法
### 全施設：
```bash
python monitor.py
```

### 特定施設のみ：
```bash
python monitor.py --facility "鈴谷公民館"
```

### 強制実行（監視時間外でも）：
```bash
MONITOR_FORCE=1 python monitor.py
```

## 8. トラブルシューティング
- 月表示へ遷移しない → `click_sequence` / `special_selectors` を見直す
- 翌月に進まない → `next_month_selector_candidates` を追加
- 時間帯が取得できない → 施設の時間帯対応テーブル（内蔵）を調整
- 改善日が検出されない → status_patterns を再確認

## 9. ライセンス / 注意事項
- Discord Webhook URL は絶対に公開しない
- スナップショットに個人情報が含まれる場合は取り扱いに注意
- サイトへの負荷軽減のため、実行間隔や GRACE_MS を調整してください

