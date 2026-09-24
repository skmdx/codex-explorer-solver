# Codex Explorer / Solver v2 — 必要な情報だけを親へ渡す

仕様確認日: 2026-09-24。前版を置き換える改良キットです。
Spotifyの記事を参考に、探索分離に加えて「決定的な抽出 → 安価な読取り → 親の判断」の順序を導入しました。
Portal契約・Claude Code・追加Pythonライブラリは不要です。使用するモデルの認証・利用枠は別途必要です。

**検証の境界:** 102件のローカルテストが通過しました（前版48件＋追加54件）。詳細は `test-results.txt` を参照してください。
元ZIPのpayloadからの更新、設定の保存、検索・ログ・予算CLIのスモーク試験も通過しました（`smoke-results.json`）。
実Codex CLIは作成環境にないため、実モデルとの結合、権限設定の実効性、修正成功率、トークン削減率は未測定です。
API/CLI互換性は公開仕様への照合とモック試験までです。「90%削減」を本キットの性能とは主張しません。

## 1. 何を変えたか

| 作業 | v2の経路 |
|---|---|
| 既知の文字列・シンボルを探す | `gateway.py search`。モデルを呼ばず行参照を返す |
| 既知ファイル群への事実確認 | `repo_reader`。限定した質問と根拠位置だけ |
| 場所が不明な修正 | `repo_explorer`。従来の探索分離 |
| 原因推論・並行性・設計・編集 | 親Solverが必要な原文を確認して担当 |
| 大量のテストログ | `capture.py`。全文は外部ファイル、親には終了状態と短い原文末尾 |
| 大きな直接読取り | 任意のPreToolUseフック。最初はaudit、確認後のみdeny |
| 外部workerの呼出し | 任意の永続予算。失敗も計上、同時実行1、初期値は全体2回 |

`gateway.py`はAST解析器や全文索引サーバーではありません。最大12個の明示ファイルに対する固定文字列検索です。
ファイル名が不明な段階では、探索役が通常の範囲を絞った検索を使います。

## 2. 前提と新規インストール

Python 3.11以降、信頼済みローカルGitリポジトリ、認証済みの対応Codex CLIを想定します。
プロセス管理・フック監査はLinux/macOS/WSL向けです。

```bash
# ZIPを展開した場所
KIT="$HOME/Downloads/codex-explorer-solver-v2"

# 対象リポジトリのルートで、まず計画だけ確認
python3 "$KIT/install.py" --repo "$PWD"

# 新規配置。既存ファイルは上書きしない
python3 "$KIT/install.py" --repo "$PWD" --apply
```

既存の `AGENTS.md`、`.codex/config.toml`、`.codex/hooks.json` は変更しません。
モデルは初期値を交換できます。

```bash
python3 "$KIT/install.py" --repo "$PWD" --apply \
  --explorer-model YOUR_AVAILABLE_LIGHT_MODEL \
  --solver-model YOUR_AVAILABLE_STRONG_MODEL
```

`--explorer-model`はExplorerとReader、`--solver-model`は強いExplorerの設定です。
親のモデルは起動時に別指定します。初期値はExplorer=`gpt-6-luna/high`、Reader=`gpt-6-luna/medium`、
強いExplorer=`gpt-6-sol/medium`です。Readerのmediumはこの設計での仮の出発点であり、論文の最適値ではありません。
異なるモデルを指定する場合、各roleのreasoning effortの対応も確認してください。勝手なモデル昇格は行いません。

## 3. 前版から更新する

```bash
KIT="$HOME/Downloads/codex-explorer-solver-v2"

# 変更計画のみ。前版標準ファイルのハッシュと照合する
python3 "$KIT/upgrade.py" --repo "$PWD"

# 更新前データをリポジトリ外へ退避してから適用
python3 "$KIT/upgrade.py" --repo "$PWD" --apply \
  --backup-dir "$HOME/.local/state/codex-es/upgrade-20260924"
```

バックアップ先は新しい名前にしてください。更新中はキットを他プロセスから編集しないでください。
前版のモデル設定・プロンプト等を変更している場合は競合として**何も変更せず停止**します。
その場合は `payload/` と差分比較し、カスタマイズを維持して手動マージしてください。
不明なローカル変更を強制上書きするオプションはありません。
更新ツールはGitのcommit/reset/stashを実行しません。

## 4. 通常のCodex内で使う

```bash
codex --strict-config -m gpt-6-sol \
  -c 'model_reasoning_effort="medium"' \
  -c agents.enabled=true \
  -c agents.max_concurrent_threads_per_session=1
```

CLI内で明示呼出しします。

```text
$explore-solve
課題: ……
期待する挙動: ……
既知のファイル/シンボル/エラー: ……
受け入れ条件: ……
```

スキルは明示利用のみです。すべての依頼に常時長い規則を注入しません。
Native subagent経路の「合計2回」は指示上の予算です。機械的なタスク別制限が必要なら次節の外部経路を使います。
親のsandboxから別Codexを起動できない環境では、外部経路を通常のターミナルから先に実行してください。
権限や認証のエラーを理由に自動的に権限を広げません。

## 5. モデルを呼ばない検索とサイズ確認

次はリポジトリに実在するパス・文字列に置き換えます。

```bash
python3 .codex/es/gateway.py inspect --root . \
  --path src/service.py --path src/handler.py --purpose lookup

python3 .codex/es/gateway.py search --root . \
  --path src/service.py --path src/handler.py --literal connect
```

検索は行本文ではなく、path/start/end/sha256を返します。長い1行のヒットも全文を親へ出しません。
`next_offset`があれば、同じ引数に `--offset 値 --expect-snapshot ハッシュ` を追加できます。
途中で入力ファイルが変わったらページ継続は拒否します。
`total_matches_in_scope=0`は**指定ファイル内での不一致**だけを示します。

原文を読む段階では従来のヘルパーを使います。

```bash
python3 .codex/es/evidence.py read --root . \
  --path src/service.py --start 10 --end 60
```

返されたハッシュを `--expect-sha256` に渡せば、その内容から変わった読み取りを拒否します。
編集の際は必要な関数・型・呼出し関係を読んでください。範囲上限は理解の上限ではありません。
この版ではシンボリックリンクのソース参照も拒否します。実体のリポジトリ内パスを指定してください。

## 6. 外部workerと永続予算

### タスクの予算を一度作る

`/tmp/task.txt` に元の課題・受け入れ条件を記述します。親の会話全文は入れません。

```bash
STATE="$HOME/.local/state/codex-es/task-001"
python3 .codex/es/budget.py init --repo "$PWD" \
  --task-file /tmp/task.txt --state-dir "$STATE" --max-calls 2
```

STATEは未作成のディレクトリで、リポジトリ外に置きます。
同じタスクの全外部workerで、このSTATEを共有してください。枠が尽きるたび新しいSTATEを作る運用では制限になりません。

### 場所が不明ならExplorer

```bash
python3 .codex/es/locate.py --repo "$PWD" \
  --task-file /tmp/task.txt --state-dir "$STATE" \
  --out-dir /tmp/es-task-001-localize --timeout 120
```

### 既知のファイル群への質問ならReader

`/tmp/lookup.txt` に、例えば「この2ファイルのどのメソッドがDB接続を作るか。定義と呼出し箇所を示す」と記述します。

```bash
python3 .codex/es/locate.py --repo "$PWD" --mode reader \
  --path src/service.py --path src/handler.py \
  --task-file /tmp/lookup.txt --state-dir "$STATE" \
  --out-dir /tmp/es-task-001-read --timeout 120
```

Reader入力は最大12ファイル、原文合計64 KiBです。行番号付きスナップショットを**Python内部で**組み立ててworkerへ渡すため、
親が一度全文を読んでから委譲する必要がありません。上限超過時は切り捨てず拒否します。
Readerは固定コーパスからの事実抽出専用です。無ツール利用はプロンプト上の指定であり、CLI全ツールの物理的禁止ではありません。
返却された全参照を指定パスに限定して検証し、さらに**未引用の入力も含む全ファイル**のハッシュを再確認します。
コードの意味の正しさまでは検証しません。

`handoff.json` が検証済みの結果です。`request.txt`や`events.jsonl`を親に読み込ませると隔離の意味が薄れます。
親には元の課題と `handoff.json` のパスだけを渡し、索引を検証して原文から実装させます。

```text
$explore-solve
/tmp/task.txt の課題を実装してください。
探索済みの索引は /tmp/es-task-001-localize/handoff.json です。
初期探索を重複実行せず、必要な原文を確認して続行してください。
```

足りない定義・登録などが観測された場合だけ、元の課題にその不足を追記した短いファイルを使い、
同じSTATEと新しいout-dirで `--deep` を指定します。深い探索は最大1回、初期探索やReaderと合計して2回が初期値です。
この例のExplorerとReaderを両方実行した場合、既に2枠を使っています。さらにDeepを呼べるわけではありません。

### 集計・異常終了

```bash
python3 .codex/es/budget.py status --state-dir "$STATE"
```

回数はSQLiteトランザクションで予約し、同時に1workerだけを許可します。失敗や無効な回答も回数に含みます。
前の消費量が取得できなければ、初期設定では次のworkerを拒否します。
`init --allow-unknown-usage` は、不明な消費があっても回数枠内の続行を明示的に許すオプションです。
`init --soft-token-limit N` は**既に観測した**worker消費がN以上なら次の起動を断る指定で、進行中のAPI要求の厳密な上限ではありません。

wrapper自体が強制終了した場合、予約はrunningのまま残り、時間で勝手に解放しません。
実プロセスの停止を確認した後だけ `budget.py mark-abandoned --state-dir "$STATE" --job-id ID` を実行してください。
この操作はプロセスをkillせず、消費量を不明として記録します。回数を返金したり未知の消費を0にしたりしません。

この仕組みは**同じSTATEを使うlocate.py呼出しだけ**を制限します。Native subagent・別のCodex・親の使用量は含みません。

## 7. テストログを保持しつつ短く返す

対象プロジェクトのテストコマンドをそのまま指定します。

```bash
python3 .codex/es/capture.py --repo "$PWD" \
  --out-dir /tmp/es-test-001 --timeout 300 -- \
  python3 -m unittest discover -s tests -v
```

stdout.log、stderr.log、capture.jsonをリポジトリ外へ保存します。
通常終了時は元の終了コードを返し、親には終了状態・ログ参照・短い原文末尾を返します。
終了コード0だけで全受け入れ条件の達成を宣言しません。
プレビュー外に失敗原因がある場合は保存済みログを検索してください。ログ取得目的での再テストは不要です。
非UTF-8やJSON化した表示サイズが大きい場合、プレビューを省略したことを明示します。元バイト列はログに残ります。
タイムアウトは124、ログ上限検知は125、割込みは130です。16 MiBのログ監視はポーリングなので厳密なバイト上限ではありません。

このコマンドはsandboxではなく、指定コマンドを通常の権限で実行します。入力待ちの対話プログラムには不向きです。
`--`の後をshell文字列として再解釈しません。自ら `sh -c` を指定した場合だけ、そのshellの意味になります。

## 8. 任意の大規模読取りガード

まず設定の断片だけ生成します。フック登録・信頼設定は自動変更しません。

```bash
python3 .codex/es/configure_guard.py --repo "$PWD" --mode audit \
  --audit-file "$HOME/.local/state/codex-es/read-audit.jsonl" \
  > /tmp/es-hooks.snippet.json
```

内容を確認して `.codex/hooks.json` の既存 `hooks.PreToolUse` 配列へマージします。
既存フックを置き換えず、同じ要素を重複登録しないでください。対応Codexでは信頼レビューも必要です。
初期thresholdは12,000バイトです。行数や推定token数ではありません。

auditは遮断せず、検知種別とサイズだけを監査ファイルへ追記します。ソース全文・コマンド文字列は記録しません。
挙動を確認したら同じ生成コマンドを `--mode deny` に変え、既存要素を差し替えます。

認識するのは単純なcat、head/tailの一部、数値範囲sed、Readのfile_path/offset/limitです。
Bashの複合式・パイプ・シェル展開・Python独自読取り・MCP・特殊toolなどは完全解析しません。
未認識形は通し、認識していないものを安全と証明しません。`cat big | grep ...` も「狭いから安全」とは分類しません。
フック失敗・未認識入力はfail-openです。費用面の補助であってセキュリティ境界ではありません。
このガードは親/子の両方へかかり得ます。session_idから親子を勝手に推定しません。

## 9. データと権限

ソースと課題はworkerモデルに送信されます。親へ出ないことは「外部送信されない」ことではありません。
ログやrequestにはコード・課題・機密情報が含まれ得ます。runディレクトリは700、主要ファイルは600です。
保存期間・暗号化・MCP/プラグイン・アカウントのデータ取扱いは利用環境で管理してください。
read-only sandboxはMCP経由の外部操作を一括禁止しません。不要な連携は実名で無効化します。

```bash
# 他のlocate.py引数に追加
--disable-mcp YOUR_SERVER_NAME
```

Native用にはroleファイル末尾へ `[mcp_servers.NAME]` と `enabled = false` を追加してください。
空のテーブルで継承が解除されるとは想定しません。無制限権限・自動承認・信頼変更をこのキットは要求しません。

## 10. 最初の検証と評価

まず編集なしで、検索→原文取得→workerの索引検証を試します。その後にguardのauditログを確認します。
明示ReaderのJSONが安定するか、CLI設定キーや使用モデルが利用可能かを確認してから実タスクへ適用してください。

```bash
cd "$KIT"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

比較はA=親のみ、B=前版の探索分離、C=v2の抽出+ログ保持、D=C+必要時Reader、の順で機能を増やします。
同じモデル・予算・受け入れ条件で複数回計測します。品質、総token、親token、cached input、再読込み、失敗、遅延を分離します。
主指標は「失敗・全worker・親を含む総token ÷ 外部判定の成功数」。
`metrics.json`とbudget statusはworkerだけなので、これだけではend-to-end削減率を出せません。
キャッシュ入力とreasoningをinput/outputへ二重加算しないでください。未知usageは0ではありません。

設計上の判断と見送った機能は `docs/DESIGN.ja.md`、一次資料は `docs/SOURCES.md` を参照してください。
