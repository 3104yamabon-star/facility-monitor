
README — さいたま市 施設予約システム 空き状況監視ツール
###（月表示監視＋改善日クリックで「空き」時間帯抽出＋Discord通知）

📌 この README の目的
本ツールは、さいたま市の「公共施設予約システム（月間カレンダー）」を自動監視し、

月間カレンダーの 空き状況（○/△/×/休館/保守/受付期間外）を自動解析
前回との差分から 改善日（×→△、△→○、×→○、未判定→△/○など）を抽出
改善日の 日付セルをクリック → 時間帯表示に遷移
各時間帯の中から 「空き」だけ抽出（○や△ではなく「空き」表示）
Discord へまとめて通知（施設別色分け／メンション対応）
HTML・PNG の保存 ＋ ローテーション管理

までを自動化します。
設定ファイル（config (1).json）を参照して、各施設固有の遷移ロジックと CSS セレクタが柔軟に定義できます。

1. 機能概要
✔ 月表示（カレンダー）の解析

HTML / aria-label / title / img alt / img src / CSS class など複数手がかりでステータス判定
本設定では以下の種別に対応：

"全て空き" → ◯
"一部空き" → △
"予約あり" → ×
"休館日" → × 扱い or 未判定 扱い（通知対象外）
"保守日" "受付期間外" も認識



✔ 改善日の判定
status_counts.json との比較で改善日のみ抽出。
改善ルールはソースコード側の固定値（×→△、△→○ etc.）
✔ 時間帯表示の解析（改善日だけクリック）

FACILITY_TIME_MAP により、施設ごとの時間帯ラベル → 「9～11時」などに変換
時間帯セルの "空き" / "予約あり" を抽出
「空き」だけ Discord に通知

✔ Discord 通知

施設ごとの色（南浦和＝ブルー、岩槻＝グリーン など）
メンション可：個別ユーザ / everyone / here
Embed またはテキスト強制も可

✔ スナップショット

calendar.html, calendar.png
状態変化時：タイムスタンプ付き保存
月別フォルダに整理
max N ファイルまで自動削除（ローテーション）

✔ 次の月へ自動遷移

月遷移リンクを config の候補リスト から探索
施設ごとに month_shifts: [0,1,2,3] → 今月〜3ヶ月先を監視


2. 前提条件

Python 3.9+
Playwright（Chromium）
ネットワーク：

さいたま市 施設予約サイト
Discord Webhook


オプション：pytz / jpholiday


3. インストール
Shellpython -m venv .venvsource .venv/bin/activatepip install playwright pytz jpholidaypython -m playwright install chromiumその他の行を表示する

4. 設定ファイル config.json（今回の config (1).json 形式）
以下の構造が必要です。

● selectors（共通セレクタ定義）
JSON"selectors": {  "back_from_month_selector": "a[href*='gRsvWInstSrchMonthVacantBackAction']",  "month_table_selector": "table.m_akitablelist",  "facility_list_selector": "table.tcontent a[href*='gRsvWTransInstSrchInstAction']",  "facility_link_by_code_template":    "table.tcontent a[href*=\"gRsvWTransInstSrchInstAction\"][href*=\"'{code}'\"]",  "next_month_selector_candidates": [    "a[href*='moveCalender'][href*='gRsvWInstSrchMonthVacantAction']",    "a:has-text('次の月')",    "a:has-text('翌月')"  ]}その他の行を表示する

● facilities（施設ごとの遷移定義）
各施設ブロックには以下の要素があります：

















































キー内容name画面上で表示される施設名称aliasDiscord通知・フォルダ保存用の短縮名facility_codeBldCd で選択するための ID（館選択ワークフロー用）click_sequence画面上のラベルを順にクリックするリストpost_facility_click_steps部屋選択等の追加ステップcalendar_selector月表示テーブルの CSSspecial_selectors通常クリックできない場合の直接セレクタ指定special_pre_actionsクリック前に SCROLL や WAIT を実行step_hints遷移待ち判定に使う CSSmonth_shifts今月から何ヶ月先まで巡回するか（例：0,1,2,3）
※ config (1).json の例：

南浦和＝4ヶ月先まで
岸町・鈴谷＝1ヶ月先まで
駒場＝4ヶ月先まで
鈴谷・駒場は特定操作に special_selectors を追加


● status_patterns（ステータス判定キーワード）
JSON"status_patterns": {  "circle": ["全て空き"],  "triangle": ["一部空き"],  "cross": ["予約あり"],  "holiday": ["休館日"],  "maintenance": ["保守日"],  "outside": ["受付期間外"]}その他の行を表示する
注：
本ツールは最終的に ○/△/×/未判定 の 4分類に統一します。
休館日・保守日などは × 扱い or 未判定扱い（通知しない）になります。

● css_class_patterns（class属性から補助判定）
JSON"css_class_patterns": {  "circle": ["status-ok","ok","vacant","maru","available","is-available","icon-ok"],  "triangle": ["status-partial","partial","few","warning","limited","icon-partial"],  "cross": ["status-ng","full","unavailable","batsu","is-full","icon-ng"]}その他の行を表示する

● debug
JSON"debug": {  "dump_calendar_html": true,  "log_top_samples": 15,  "highlight_alpha": 160,  "highlight_border_width": 3}その他の行を表示する
monitor.py 内部で _debug フォルダへ証跡が保存されます。

5. 環境変数（monitor.py 側で使用）
主要項目（抜粋）









































変数説明BASE_URLトップURLOUTPUT_DIR保存先DISCORD_WEBHOOK_URLWebhook URL（通知）MONITOR_START_HOUR / MONITOR_END_HOUR実行許可時間FAST_ROUTESGA/フォントブロックGRACE_MS画面安定化待ちDISCORD_MENTION_USER_ID通知時のメンションDISCORD_FORCE_TEXTEmbedを使わずテキスト通知

6. 監視の流れ（内部動作）
以下の処理を施設ごとに順番に実行：

施設トップへ遷移
click_sequence を順番に実行（special_selectors があれば優先）
post_facility_click_steps を実行
月間カレンダーを解析 → summary/details 出力
前回との差分から改善日抽出
改善日だけ

日付セルクリック → 時間帯表示へ
"空き" の時間帯だけ抽出


Discord 通知
month_shifts に従い 翌月 → 翌々月…と遷移
各月ごとに（4〜7）を繰り返す
次の施設へ


7. 出力
OUTPUT_DIR/
  └─ 施設名（alias）/
       └─ YYYY年M月/
            ├─ calendar.html
            ├─ calendar.png
            ├─ calendar_YYYYmmdd_HHMMSS.html/png
            └─ status_counts.json

  └─ _debug/
       ├─ timesheet_after_click_dayXX.png
       ├─ back_to_month_failed.html
       └─ ...


8. 実行方法
全施設を監視
Shellpython monitor.pyその他の行を表示する
特定施設だけ
Shellpython monitor.py --facility "鈴谷公民館"その他の行を表示する
監視時間外でも強制実行
ShellMONITOR_FORCE=1 python monitor.pyその他の行を表示する

9. トラブルシューティング（設定ファイル版）

































症状原因と対処🔴 月表示へ遷移しないclick_sequence が誤っている → label を修正🔴 クリック対象が見つからないspecial_selectors で CSS を追加🔴 翌月に進まないselectors.next_month_selector_candidates を増強🔴 部屋選択が必要post_facility_click_steps + special_selectors を追加🔴 改善日が抽出されないstatus_patterns / css_class_patterns の見直し🔴 時間帯が取れないFACILITY_TIME_MAP の対象施設ラベルを追加

10. セキュリティ上の注意

Discord Webhook URL は絶対に公開しない
スナップショットに個人情報が映る場合は保存先を限定
定期実行の場合、アクセス過多にならないように GRACE_MS を調整


11. 拡張ポイント

遷移ステップ追加：special_pre_actions / special_selectors
時間帯ラベル追加：FACILITY_TIME_MAP に追記
施設追加：facilities 配列にコピーして追加
API 化：Discord 以外の通知にも容易に拡張可


12. まとめ
今回の README は、
monitor.py（本体） と config (1).json（設定） の両方に完全に適合するようリライトしたものです。
