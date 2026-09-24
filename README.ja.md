# Codex Explorer / Solver v4 — AGY / Gemini 3.8 Flash

## このワークスペースへの導入

共通本体は `tools/codex-explorer-solver/payload/.codex/es`、入口は
`/home/user/codex-work/.codex/es`。`explore-solve` はユーザースキルとして登録し、
各Gitリポジトリで明示的に呼び出す。親ワークスペースのスキル・Hooksは
子Gitリポジトリから自動検出されないため、この配置を使う。

自動Hooksは実機で登録・信頼・出力保存まで確認したが、code modeが原文を
親へ再表示するため登録を解除した。大量出力には同梱の `capture.py` を明示的に使う。
既定のスキル動作は明示呼出しのみで、自動委譲は有効にしていない。

AGY起動にはソースexportを `--add-dir` で渡す。2026-09-24のAGY 1.2.0では、
init.toolsは実効権限ではなく全体カタログを通知する。定義と実際のtool eventを検査し、
構造化応答用のfinishを含めたReader/Explorerで実機成功を確認した。実機の使用量・成立条件は
`/home/user/codex-work/note/codex-explorer-solver/20260924/` の測定記録を参照。

以下は配布ZIPの使用説明と検証時点の記録。

親SolverとHooksはCodexのまま、Explorer・Reader・追加探索をすべて
Antigravity CLI (`agy`) 経由のGemini 3.8 Flashへ移した版です。
`gemini` CLI、Gemini APIの直接呼出し、Codexの子、`codex exec` workerは使いません。

**実機結合の状況:** このキットを作成した環境に実AGY/Codexはありません。
ローカルテスト235件が通過しました。AGYの正常系・異常系には明示的なfake AGYを使用し、
実サービスとの結合試験とは区別しています。テスト結果は
`test-results.txt`、旧v3 ZIPからの移行確認は `smoke-agy-results.json`。
実モデルでの成功率・使用量削減・請求額の改善は未測定です。

## 1. 構成

```text
Codex parent (Solver; native subagents disabled)
  ├─ 確定的な検索・必要範囲の原文取得
  ├─ locate.py → agy MAIN agent → Gemini 3.8 Flash Medium
  │     ├─ Explorer: view_file / grep_search
  │     └─ Reader: 指定ファイルの番号付き原文をstdinで送信
  ├─ 必要な場合だけ同じFlash Highで追加探索
  └─ 元ソース・ハッシュ確認 → 親が編集・テスト
       └─ Codex Hooks: 大きな出力の保存、短い表示、復元
```

| 役割 | AGY slug | 補足 |
|---|---|---|
| 初期Explorer | `gemini-3.8-flash-medium` | 修正場所が不明なときだけ |
| Reader | `gemini-3.8-flash-medium` | 既知のファイルへの限定した事実質問 |
| 追加Explorer | `gemini-3.8-flash-high` | 同じモデル系列。回数制限なし |
| 親Solver | 既存のCodex選択 | 本キットは変更しない |

これは `agy --agent` で制限付きの**メインエージェント**を起動する方式です。
Codexからspawnする方式でも、AGYからさらに子をspawnする方式でもありません。
CLIの正規slugは `agy models` で確認します。見つからなければ停止し、別モデルに
フォールバックしません。APIのモデルIDをCLIのslugと混同しないでください。

## 2. 要件

- Python 3.11以降、Git、Linux/macOS/WSL。ネイティブWindowsは未検証です。
- Google公式のAntigravity CLI。初回は通常のターミナルで `agy` を開いて認証を完了。
- `agy --help` に `--input-format`, `--output-format`, `--json-schema`,
  `--model`, `--agent`, `--print-timeout` があること。
- `agy models` に利用する3.8 Flashのslugがあること。
- Codexからのプロセス起動、認証情報へのアクセス、必要なネットワーク接続が
  利用者・管理者のポリシー内で許可されていること。本キットは許可を迂回しません。

## 3. v3から更新

対象リポジトリのルートで、Codexなどの同時書込みを止めて実行します。

```bash
KIT="$HOME/Downloads/codex-explorer-solver-v4"

# 計画のみ
python3 "$KIT/upgrade.py" --repo "$PWD"

# 内容を確認して適用。毎回新しいバックアップ先を使用
python3 "$KIT/upgrade.py" --repo "$PWD" --apply \
  --backup-dir "$HOME/.local/state/codex-es-backups/v4-$(date +%Y%m%d-%H%M%S)"
```

未変更のv1/v2/v3 payloadを認識します。以下の旧キット専用ファイルをバックアップ
後に撤去します。独自編集があれば、何も変更せず停止して手動マージを求めます。

```text
.codex/agents/repo_explorer.toml
.codex/agents/repo_reader.toml
.codex/agents/repo_deep_explorer.toml
```

`AGENTS.md`、実際の `.codex/config.toml`、`.codex/hooks.json`、登録済みHooksの
所有権manifest、キット以外のエージェントは変更しません。標準v3のHook実行コード
自体も変更しないため、既に正常稼働しているv3 Hooksを再登録する必要はありません。
旧native-spawnカウンタが登録されていてもAGYを数えるものではありません。
不要なら `configure_hooks.py` で `--native-spawn-limit 0` として別途更新できます。

新規導入は次を使用します。

```bash
python3 "$KIT/install.py" --repo "$PWD"          # 計画のみ
python3 "$KIT/install.py" --repo "$PWD" --apply  # 既存の同名ファイルを上書きしない
```

新規Hooks導入についてだけは `docs/V3-README.ja.md` のHooks登録節を参照してください。
同文書の旧Codex子エージェント起動例はv4では使いません。

## 4. CLI確認と通常利用

```bash
agy models
python3 .codex/es/locate.py --check

# 親はCodex。モデル選択は従来のまま。子エージェントツールを無効化
codex -c 'agents.enabled=false'
```

`--check` はhelp/version/model一覧を調べるだけで、モデルへの課題送信はしません。
認証済み推論・custom agentの適用・返却形式までの実機確認ではありません。
Codex内で明示的にスキルを呼びます。

```text
$explore-solve

課題: ……
期待する挙動: ……
既知のファイル・シンボル・エラー: ……
受け入れ条件: ……
```

スキルは必要時だけ `locate.py` を起動し、共通の使用量記録を作成・再利用する
指示を持ちます。既にSTATEや検証済みhandoffがある場合は、それを渡してください。
既知の修正場所、単純な固定文字列検索ではAGYを呼びません。

## 5. 手動で探索を実行する

ここでは使い捨ての私有ディレクトリを例にします。再開をまたぐ長いタスクでは、
消去されない私有ディレクトリを指定してください。同じタスクで新しいSTATEを作り、
上限を回避してはいけません。

```bash
RUN="$(mktemp -d "${TMPDIR:-/tmp}/codex-es-agy.XXXXXX")"

cat > "$RUN/task.txt" <<'TASK'
対象の不具合と受け入れ条件をここに記述してください。
探索に必要な手掛かりだけを書き、親の会話履歴や秘密情報は入れません。
TASK

python3 .codex/es/budget.py init --repo "$PWD" \
  --task-file "$RUN/task.txt" --state-dir "$RUN/budget"

python3 .codex/es/locate.py --repo "$PWD" \
  --task-file "$RUN/task.txt" --state-dir "$RUN/budget" \
  --out-dir "$RUN/explore-01"
```

範囲が分かる場合は `--scope src --scope tests` などを追加します。ファイルやディレクトリ
のリテラルパスであり、glob式やGit pathspecではありません。

Readerは上記の実行コマンドに `--mode reader --path src/example.py` を追加します。
`--path` は複数指定できます。ReaderとExplorerは代替経路で、両方を必ず実行しません。
`--deep` は同じSTATEを使うHighでの探索です。先行workerは不要で、回数制限はありません。
追加探索の質問は、元課題の全文を繰り返すのではなく、未解決の関係と候補に絞れます。

返却は小さなJSONで、検証済みの `handoff_path` と `metrics_path` を含みます。
親へ `events.jsonl` や `request.jsonl` の全文を渡してはいけません。

```bash
python3 .codex/es/evidence.py show --root . \
  --handoff "$RUN/explore-01/handoff.json"
python3 .codex/es/budget.py status --state-dir "$RUN/budget"
```

Codexには課題とhandoffのパスを渡し、「初期探索を重複せず原文を確認して実装」と
依頼します。行範囲は索引であり編集範囲の制限ではありません。
`show`は全参照を検証して行番号付き原文も返します。同じファイルは一度だけ読み込みます。
合計16,000 bytesを超える場合は切り詰めずエラーとし、`check`と必要範囲の`read`を使います。

大量のコマンド出力には`capture.py`を使います。正常終了時は各ログ末尾最大256 bytesと
終了コード・ログ位置を返し、異常終了時は各末尾最大1,536 bytesと終了理由を返します。
完全なstdout/stderrと実行情報・hashは出力先のログと`capture.json`に保存します。

## 6. スナップショットと原文検証

AGYは元リポジトリを作業ディレクトリにせず、私有の一時的なソースexportで動きます。
既定では**Git追跡済みファイルの現在の作業ツリー内容**を複製します。未commitの変更も
含みます。未追跡は `--include-untracked` で明示追加します（ignore対象は含みません）。
Readerの明示 `--path` は未追跡ファイルも対象にできます。

agent設定、.git、credentialらしいファイル、バイナリ、symlinkなどは除外します。
除外記録は `source-manifest.json` にあります。これは万能な秘密情報検出ではありません。
workerはexport範囲の外の不存在を証明しません。Git submodule内容や未取得LFS実体なども
自動的に完全対応したとは扱わないでください。

既定の上限: 5,000ファイル、合計64 MiB、1ファイル1 MiB。Readerは12ファイル・192 KiB。
大きい単体ファイルなどの除外を記録し、合計上限超過は部分的に黙って切らず停止します。
`--scope` や設定で調整してください。値は論文由来の最適値ではありません。

Geminiにはhashを作らせません。返却時にホストが**実行前に保存したバイト列のhash**を
付加し、exportと元の全対象ファイルの一致を確認します。引用しなかった入力も確認するので、
否定的回答の前提が変わった場合も拒否します。新規ファイルを含めたリポジトリ全体の
原子的スナップショットではなく、exportされた明示集合の検証です。

## 7. 権限・プロトコル・失敗時の扱い

- custom main agentはExplorerで `view_file` / `grep_search` / `finish`、Readerで `finish` のみ。
  `subagent:false`、`commandExecutionPolicy:off`、MCP/skills/pluginsなしの定義です。
- CLIの `init` でモデル・agent・必須toolの掲載を確認します。tool listは全体カタログであり、
  実効権限とは扱いません。観測された書込み、shell、MCPなどの呼出しと全権許可モードを拒否します。
- AGYのplanモードやterminal sandboxをread-only保証と誤解しません。
  本キットはOS sandboxを構築せず、AGYの全体設定・global hooksも書き換えません。
  custom tools制限はAGYの仕様と実装に依存します。init監視は実行開始前の安全境界では
  なく、予期しない構成を検出する補助です。global設定・拡張も利用者側で点検してください。
- structured_outputだけを受け取り、コードブロック・説明文からJSONを「救済」しません。
  schema逸脱、未知のevent、複数result、古い原文、不正な行参照などはエラーにします。
- 認証・モデル未提供・権限・quotaの失敗で自動的にモデル変更・再実行しません。
- 同じSTATEで回数無制限、同時1件。追加探索にも回数上限はありません。
  失敗も記録し、usageが不明なら0とせず不明のまま後続実行を受け付けます。
  `--max-calls`、`--soft-token-limit`、`--allow-unknown-usage`は廃止しました。
- 局所deadline・log上限・観測tool回数監視は強制課金上限ではありません。
  超過したtool eventが通知された時点でその呼出しはすでに始まっている場合があります。
  プロセス停止後も処理済みの費用は取り消されません。

## 8. 使用量とプライバシー

`metrics.json` はAGYの最後のresultの累積usageを**一度だけ**記録します。
各stepのusageを再加算せず、thinking/cacheをtotalへ足し直しません。
Codex側の使用量は含みません。トークン定義、providerのquota、AI credits、API課金を
同一指標と見なさず、必要なら提供元の明細で確認してください。

ソースと質問はGoogle/Antigravity側へ送られます。Readerは指定範囲の全文、Explorerは
読取りに必要な内容が送信対象になります。利用者の組織のデータ方針に従ってください。
CLIの認証・保存履歴はAGY側の設定に従い、本キットの私有ログとは別です。

出力ディレクトリにはソースコピー・質問・イベント・返却内容を保存します。
ディレクトリは0700、主要なログは0600です。必要な確認後の保存期間・消去は利用者が
管理します。ログを公開リポジトリにcommitしないでください。parentの全文表示もしません。

## 9. ローカルテストと実機スモーク

```bash
# AGYを呼ばない。fake CLIによる正常系・異常系 + 既存の原文/予算/Hooks回帰試験
python3 "$KIT/tests/run_tests.py"

# 実際のv3 ZIPからの移行。AGY部分はfake CLI
python3 "$KIT/tests/smoke_agy.py" --v3-zip /path/to/codex-explorer-solver-v3.zip

# 任意: 合成した小リポジトリで最大2回の実AGY実行。実quotaを消費
python3 "$KIT/tests/smoke_agy_live.py" --run-models
```

実機スモークは利用者のリポジトリを変更せず、人工のコードだけを送信します。
失敗した場合は出力ディレクトリのmetrics・stderrを確認し、制限を外すのではなく、
CLI仕様差・認証・custom agent適用の問題を解決してください。

## 10. 資料

設計の詳細は `docs/DESIGN.ja.md`、一次資料は `docs/SOURCES-v4.md`。
`docs/V2-*` と `docs/V3-*` は履歴資料で、v4の起動・モデル設定はこのREADMEが正です。
