# Codex Explorer/Solver v3 — Hooksで出力経路を制御する

確認日: 2026-09-24。Python 3.11以降、Linux/macOS/WSL向け。実際の検証環境はLinux。
Codex CLI・実モデルとの結合試験はこの環境では未実施です。ローカルの契約・回帰試験と、実モデルでの品質/費用評価を区別してください。

## v2から変えたこと

v2のExplorer/Reader/原文検証/capture/外部worker予算を維持したまま、次を追加しました。

|イベント|動作|初期設定|
|---|---|---|
|PostToolUse / Bash|大きなテスト・検索出力を保存し、短い原文プレビューと復元コマンドへ置換|auditでは記録のみ|
|PreToolUse / spawn_agent|観測したsession_id+turn_id単位で、native spawnの受付回数を制限|無効。明示的に指定したときだけ登録|
|PreCompact|保存済み出力のセッションチェックポイントを記録|無言。要約モデルは呼ばない|
|SessionStart / compact,resume|同じセッションに保存済み出力があるときだけ復元先の短い索引を通知|enforceのみ|

HooksはLLM・外部API・テストコマンドを呼びません。`Stop`でテストを再実行したり、自動継続を強制したりしません。
`PreToolUse`のallow/updatedInputも使いません。費用最適化のために権限を承認したり、コマンドの意味を書き換えたりしない方針です。

**出力を短くすることが目的であり、修正成功率や総トークン削減率を保証するものではありません。**

## 1. 既存v1/v2を更新する

対象リポジトリのルートで、Codexを終了してから実行します。並行したファイル更新中に実行しないでください。

```bash
KIT="$HOME/Downloads/codex-explorer-solver-v3"
BACKUPS="$HOME/.local/state/codex-es-backups/$(date +%Y%m%d-%H%M%S)"

# dry-run
python3 "$KIT/upgrade.py" --repo "$PWD"

# 標準の旧版ファイルだけ更新。同名ファイルの独自編集があれば停止する
python3 "$KIT/upgrade.py" --repo "$PWD" --apply \
  --backup-dir "$BACKUPS/payload"
```

旧版でモデル名や指示文を編集していた場合も、勝手に上書きしません。
競合は`payload/`との比較で手動マージしてください。無視して強制更新するオプションはありません。

この段階では`.codex/hooks.json`、`.codex/config.toml`、`AGENTS.md`、信頼状態を変更しません。

新規導入の場合は上記upgradeの代わりに次を実行します。

```bash
python3 "$KIT/install.py" --repo "$PWD"
python3 "$KIT/install.py" --repo "$PWD" --apply
```

探索用のモデルは旧版と同じです。利用アカウントに合わせた変更は旧版の導入説明を参照してください。

## 2. Hooksを監査モードで登録する

`STATE`はこのリポジトリ専用で、リポジトリ外に置きます。v2外部worker用のtask STATEとは別です。
保存ログには機密情報が含まれ得ます。共有領域・同期先に置かず、アクセス範囲を確認してください。

```bash
STATE="$HOME/.local/state/codex-es-hooks/my-project"

# 既存Hooksとマージした結果を見る。まだ変更しない
python3 .codex/es/configure_hooks.py --repo "$PWD" \
  --state-dir "$STATE" --mode audit

# 専用Hookを追加。既存Hooksを消さず、変更前のファイルを退避する
python3 .codex/es/configure_hooks.py --repo "$PWD" \
  --state-dir "$STATE" --mode audit --apply \
  --backup-dir "$BACKUPS/hooks-audit"
```

Codexを起動し、`/hooks`で追加した定義とスクリプトを確認して信頼してください。
プロジェクト自体も信頼されている必要があります。管理ポリシーがローカルHooksを禁止する環境では使えません。
`--dangerously-bypass-hook-trust`や権限の自動承認は設定しません。

通常通り`$explore-solve`を明示して作業します。auditはツール結果を置換せず、元の応答内容も保存しません。
分類とバイト数等のメタデータだけを記録します。

```bash
python3 .codex/es/hook_artifacts.py --root . --state-dir "$STATE" report
```

`would_spill`がない場合、このHookで減らせる対象は観測されていません。
小さな作業ではPython起動・I/Oのコストだけ増える可能性があるため、不要な登録は外してください。

## 3. 確認後に置換を有効化する

Codexを終了してから、同じ登録をenforceへ切り替えます。別の追加登録にはなりません。

```bash
python3 .codex/es/configure_hooks.py --repo "$PWD" \
  --state-dir "$STATE" --mode enforce --apply \
  --backup-dir "$BACKUPS/hooks-enforce"
```

定義のハッシュが変わるので、起動後に`/hooks`で再確認します。
初回は破壊的変更を伴わないテスト・検索で、返却が短い参照になり、保存内容を復元できることを確かめてください。
`tests/smoke_hooks.py`はローカル契約の試験であり、実Codexの代替試験ではありません。

### 対象と対象外

対象はBashとして通知される単純な`pytest`、`python3 -m unittest`、`cargo test/check/clippy`、
`npm/pnpm/yarn test`、`rg`、`grep`等です。閾値は正規化した応答JSONで12,000 UTF-8バイト。
最終Hook返却は6,144バイト以内。これは**トークン数ではありません**。

`cat`、数値範囲の`sed`、`head/tail`等による原文読取り、`apply_patch`、MCP、機械向け出力、
パイプ・リダイレクト・変数展開を含むshell、未知の結果形式は置換しません。
機械形式の除外は認識したオプションに限り、任意プログラムの出力形式を完全には推定できません。
複雑な実行経路には既存`capture.py`を明示的に使ってください。

v2のread_guardを登録済みでも、勝手には削除しません。denyモードの旧Hookが先に読取りを止めることがあります。
不要なら`/hooks`でその旧Hookを別途無効にしてください。

### 復元する

返却には`artifact_id`と、実際の絶対パスを含む`recovery_command`が付きます。そのコマンドで利用可能フィールドを確認します。

```bash
# IDを実際に返された64桁のartifact_idへ置換
ID="実際のartifact_id"
python3 .codex/es/hook_artifacts.py --root . --state-dir "$STATE" info --id "$ID"
python3 .codex/es/hook_artifacts.py --root . --state-dir "$STATE" search \
  --id "$ID" --field stdout --literal AssertionError
python3 .codex/es/hook_artifacts.py --root . --state-dir "$STATE" read \
  --id "$ID" --field stdout --start 100 --end 120
```

`field`は`info`に表示された`stdout/stderr/output/text`等を使用します。
必要箇所が一行の巨大データなら`slice --offset N --length M`を使えます。
UTF-8文字の途中を切る指定はエラーになり、無言で文字を書き換えません。
範囲が大きすぎるreadもエラーにして、重要情報を黙って切り捨てません。

保存対象は**Hookに届いたtool_responseのJSON値**です。正規化JSONに再エンコードするため元のJSON文字列の空白まで同じとは限りません。
上流で既に省略された出力を復元することはできません。stdout/stderrを実行開始から保存したい場合はcapture.pyが必要です。

## 4. native subagentの増殖も抑える場合

Explorer/Solverを使うセッション向けの任意設定です。全ワークフローへ一律適用する初期値にはしていません。

```bash
python3 .codex/es/configure_hooks.py --repo "$PWD" \
  --state-dir "$STATE" --mode enforce --native-spawn-limit 2 --apply \
  --backup-dir "$BACKUPS/hooks-native-budget"
```

観測した`session_id + turn_id`ごとに、ネイティブの`spawn_agent`全ロールを2回まで受付けます。
`agent_type`が省略されたツールでも数えます。同じtool_use_idの再通知は二重計上せず、並列呼出しはSQLiteで直列化します。
ほかのHookによる拒否・起動失敗が後から起きても枠を返しません。余分に使うより保守的に止めるためです。

これはタスク全体の上限でも、API呼出し数・トークン・課金額の上限でもありません。
別turn、子の別turn、followup/send_message、外部codex exec、Hook非対応経路は集計範囲外です。
既存のv2 `budget.py`は外部worker用の別管理です。二つを合算して「全支出を制御できた」と報告しないでください。

## 5. 安全な動作と制約

保存が成功してから置換します。ディスク不足・保存不整合・台帳失敗時は元の結果をそのまま通し、UI警告を返します。
ネイティブ起動上限を明示的に有効にした場合、その上限を管理できない起動は逆に拒否します。
ただしHook自体が発火しない・不正JSONを解釈できない場合まで防ぐセキュリティ境界ではありません。

code mode内の呼出しを壊さないため、PostToolUseでは`decision:block`やexit 2ではなく`continue:false`を使います。
それでもJSが元の大きな結果を再出力すれば、親の入力へ再流入し得ます。**code modeでの総削減を保証しません**。
実環境の経路で確認し、必要なら明示的captureを使ってください。

Hooksは単一の同期ディスパッチャーでイベントを処理します。ただし他のHooksは同時実行され得ます。
登録順に基づくパイプラインや、他Hookの副作用を取り消すことは想定していません。

保存領域はディレクトリ0700・ファイル0600、既存symlinkは拒否。blob容量は標準128 MiBで、上限到達時は置換しません。
この容量にSQLite台帳は含まれません。自動削除は行わず、作業終了・復元不要を確認してからSTATEを利用者が削除します。
現行作業で参照している保存物を自動GCしてしまうことを避けています。

## 6. 無効化・削除

即時の無効化は`/hooks`を使用します。キットの登録だけ削除する場合はCodex終了後に次を実行します。

```bash
python3 .codex/es/configure_hooks.py --repo "$PWD" --remove
python3 .codex/es/configure_hooks.py --repo "$PWD" --remove --apply \
  --backup-dir "$BACKUPS/hooks-remove"
```

`hooks-installed.json`に記録した自分のグループと一致するものだけを削除します。
登録を独自編集・複製していた場合は停止して、他の設定を消しません。
既存JSONの意味は保持しますが、インデント等は整形し直します。完全な元バイト列はバックアップにあります。

## 評価と検証

```bash
python3 -m unittest discover -s "$KIT/tests" -v
python3 "$KIT/tests/smoke_hooks.py"
```

`test-results.txt`と`smoke-results.json`はローカル実行結果です。実Codex・モデルでの結合/性能は未検証です。
`report`のバイト数は「このHookに見えた応答と、このHookの返却」の比較であり、キャッシュ・既存の省略・再取得・
code modeの再出力・親と子の使用量を含みません。`token_savings`/`billing_savings`は未計測なのでnullを返します。

同じ実タスクでv2とv3を比較し、成功率と**失敗試行も含む全トークン/成功数**を測ってください。
モデル/推論設定/ツール上限/キャッシュ条件/テスト条件を揃え、保存ログの再読取り量とHook実行時間も記録します。

詳細: `docs/DESIGN.ja.md`、公開一次資料: `docs/SOURCES.md`。
旧版ツールの詳細は`docs/V2-README.ja.md`と`docs/V2-DESIGN.ja.md`に履歴として残しています。v3の仕様は本書が優先です。

### 同梱版のローカル試験結果

184件（従来102件＋Hooks関連82件）が通過。v2実ZIPからの更新smokeも通過。
実Codexは未実施。詳細は同梱のtest-results.txt/smoke-results.jsonを参照。
