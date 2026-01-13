# Facility Availability Monitor

さいたま市の施設予約システムの「空き状況」を Playwright（Chromium）で巡回し、  
月ごとの予約カレンダーを **HTML/PNGでスナップショット保存**、前回との差分（空きが増えた等）を **Discordへ通知** する自動監視ツールです。

## 機能概要

- 施設ごとのナビゲーション（リンク/ボタンのクリック列）を構成し、予約カレンダーへ到達します。
- 予約カレンダーのセルを解析し、**○/△/×/未判定**の件数サマリーと日別詳細を生成します。
- スナップショット（`calendar.html` / `calendar.png`）を出力。差分があった場合、時刻付きファイル（例：`calendar_20260113_152412.html/png`）も保存します。
- 「×→△」「△→○」など、**改善遷移**が発生した日を抽出し、**Discord（Webhook）へ通知**します（メンション設定可）。
- GitHub Actions 上で **JSTの監視時間帯**（既定：5:00〜23:55）にのみ実行。キャッシュと再利用で高速化し、成果物をコミット/プッシュ・Artifactsにも保存します。
- スナップショットの古い世代を**ローテーション**して、月ディレクトリ内のファイル数上限を維持します。

---

## リポジトリ構成

```
.
├── monitor.py                 # メイン監視ロジック（Playwright/Discord/解析/保存）
├── config.json                # 施設ごとのクリック手順・解析ヒント等の設定
└── .github/
    └── workflows/
        └── monitor.yml        # GitHub Actions ワークフロー（JST時間帯制御/キャッシュ/成果物）
```

---

## 前提条件 / 依存

- Python 3.11（GitHub Actionsでは`actions/setup-python@v5`でセットアップ）
- pip で必要パッケージをインストール（`requirements.txt` + 必要に応じて `playwright==1.46.0` をピン止め追加インストール）
- Playwright **Chromium** ブラウザ（キャッシュ再利用。未キャッシュ時は`python -m playwright install chromium`を実行）

> ローカル開発では、事前に `pip install -r requirements.txt` と `python -m playwright install chromium` を行ってください。

---

## セットアップ

### 1) `config.json` を確認/編集
施設ごとのクリック手順・解析ヒントをJSONで定義します。

### 2) Secrets / 環境変数
GitHub Actions で以下を設定します。

- `BASE_URL`：さいたま市施設予約トップ
- `DISCORD_WEBHOOK_URL`：通知先のDiscord Webhook URL
- （任意）`DISCORD_MENTION_USER_ID`：個別ユーザーにメンションする場合のDiscordユーザーID

### 3) 監視時間帯（JST）
既定は **05:00〜23:55** の間のみ実行します。`monitor.yml` の環境変数で調整可能です。

---

## 実行方法

### GitHub Actions（手動起動）
`workflow_dispatch` に対応しており、Actionsタブから**Run workflow**で起動できます。

### ローカル実行
環境変数を設定して `python monitor.py` を起動します。

---

## 出力と保存先

- ルート出力ディレクトリ：`OUTPUT_DIR`（既定：`snapshots/`）
- 階層：`snapshots/<施設エイリアス>/<YYYY年M月>/`
- 主なファイル：`calendar.html` / `calendar.png`、差分時のみ時刻付きファイル、`status_counts.json`

---

## 通知仕様（Discord）

- Embedを優先、失敗時はプレーンテキストにフォールバック
- タイトルは施設エイリアス、本文は改善遷移の行を列挙

---

## ライセンス / 注意事項

- 本ツールは、対象ウェブサイトの構造に依存します。サイト改修で動作しなくなる場合があります。
- 予約情報の取得は画面スクレイピングによります。過度なリクエストは避け、監視時間帯設定を適切に運用してください。
