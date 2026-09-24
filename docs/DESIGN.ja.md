# 設計とツールの使い方

通常の利用方法は[README](../README.ja.md)を参照してください。
ここでは、調査を手動実行するときや、ツール本体を変更するときに必要な内容を説明します。

## 手動でAGYを呼ぶ

対象のGitリポジトリのルートで実行します。Python 3.11以降、Git、認証済みのAGYが必要です。
プロセス制御はLinux・macOS・WSLを対象にしています。

```bash
ES=/home/user/codex-work/.codex/es
RUN=$(mktemp -d /home/user/codex-work/tmp/explore-solve.XXXXXX)

cat > "$RUN/task.txt" <<'TASK'
ログイン失敗時の通知がどこで生成されるか調べてください。
通知を生成する関数と呼出し元を、ファイル名・行番号付きで示してください。
TASK

python3 "$ES/locate.py" --repo . \
  --task-file "$RUN/task.txt" \
  --state-dir "$RUN/state" \
  --out-dir "$RUN/explore"
```

`state-dir`はタスクの実行履歴を置く場所です。初回に自動作成され、追加調査でも同じ場所を使います。
`out-dir`はその1回の結果を置く、新しいディレクトリです。どちらも対象リポジトリの外に置きます。

| 調査方法 | 追加する引数 |
|---|---|
| ディレクトリを絞って検索する | `--scope src --scope tests` |
| 指定ファイルの全文を読ませる（Reader） | `--mode reader --path src/login.py`。複数ファイルは`--path`を繰り返す |
| Highモデルで検索する | `--deep`。Readerとは併用しない |
| 未追跡ファイルも検索対象にする | `--include-untracked`。Gitのignore対象は含まない |
| 実行期限を指定する | `--timeout 600`。省略時はAGYの標準期限を使う |

Readerは`--scope`・`--include-untracked`と併用せず、明示したファイルだけを渡します。
モデル設定は[agy.toml](../payload/.codex/es/agy.toml)にあり、1回だけ変える場合は`--model`を使います。

## 結果を読む

標準出力のJSONに、実行状態`status`、調査結果の状態`handoff_status`、使用量`usage`、調査範囲`scope`、失敗理由`error`を返します。
`evidence`には引用箇所のファイル名・行番号・ハッシュ・原文・未解決の問いが入ります。
その原文を読み、必要なら呼出し元や周辺の処理も確認します。別の取得コマンドを挟む必要はありません。

実行先には次のファイルを保存します。

| ファイル | 内容 |
|---|---|
| `handoff.json` | ハッシュ付きの引用位置と調査結果。原文自体は含まない |
| `metrics.json` | 成否、使用モデル、使用量、失敗理由 |
| `source-manifest.json` | コピーしたソースと除外理由 |
| `events.jsonl`・`stderr.log` | AGYの出力。障害調査が必要なときに読む |
| `workspace/` | AGYが調べたソースのコピー |

保存した調査結果から原文を再取得するときと、使用量の履歴を見るときは次を使います。

```bash
python3 "$ES/evidence.py" show --root . --handoff "$RUN/explore/handoff.json"
python3 "$ES/budget.py" status --state-dir "$RUN/state"
```

エラー時は返却JSONの`error`を確認し、追加情報が必要なら`metrics.json`を読みます。
起動設定や結果保存など、実行処理の外に出た例外は`invocation_failed`、使用量記録の失敗は`accounting_failed`です。
CLIの引数構文エラーは標準エラー出力に返ります。
`accounting_failed`でも検証済みの`evidence`は返るため、調査をやり直さず記録側の問題を解消します。
ソースが変わっていた場合は、その結果を編集の根拠にせず現在の原文を確認します。
作業後は必要な結果を回収し、`RUN`以下の一時コピーとログを削除します。

## ソースの受け渡しと検証

AGYの回答と保存するhandoffは`version: 2`です。状態は`ready`・`partial`・`not_found`・`blocked`、
不足や障害の説明は`unresolved`にまとめます。各状態で必要な引用と説明は
[回答スキーマ](../payload/.codex/es/agy-handoff.schema.json)に定義します。
引用の行数・原文出力・handoffのバイト数に、スキーマ外の追加上限は設けません。

[agy_snapshot.py](../payload/.codex/es/agy_snapshot.py)は、Git追跡済みファイルの現在の内容をコピーします。
未コミットの変更も含みます。Readerでは指定したファイルを使います。
ルートのエージェント設定、Gitの内部情報、認証情報を含みそうなパス、symlink、UTF-8以外、単体10 MiB超のファイルは除外します。
除外されたファイルは調査対象外です。ファイル数や合計サイズの上限はありません。

AGY終了後は、コピーした全ファイルについて、コピーと元ソースが実行前の内容に一致するか確認します。
引用のハッシュはAGYに生成させず、実行前の記録から付けます。
[evidence.py](../payload/.codex/es/evidence.py)が引用位置と原文を検証し、[locate.py](../payload/.codex/es/locate.py)が原文を含めて返します。
この照合の対象はコピーしたファイルです。調査範囲外のファイルや、回答の意味的な正しさを保証するものではありません。

## AGYの実行と待機

[agy_backend.py](../payload/.codex/es/agy_backend.py)はAGYのCLIを起動し、JSONを1行ずつ送受信します。
質問は標準入力で渡し、最終結果の`structured_output`を受け取ります。
探索用エージェントはファイル読取りと検索を行い、Readerには番号付き原文を渡します。
エージェント定義は[agy_agents](../payload/.codex/es/agy_agents)にあります。

モデルとエージェントの識別情報、実際のツール呼出しを検査します。書込み・shell実行・再委譲は受け付けません。
AGYの全体ツール一覧は、実際に呼び出せるツールの一覧とは別なので、拒否条件に使いません。
この制御はCLIの動作を検査するもので、OSの隔離環境を作るものではありません。
認証・モデル・応答形式の問題で失敗した場合、自動で再実行したり別モデルへ切り替えたりしません。

Codex側の待機方法は[SKILL.mdのWait for the result](../payload/.agents/skills/explore-solve/SKILL.md#wait-for-the-result)に定義しています。
シェルの待機更新を一つのcode-modeセル内で続け、空の進捗をモデルに返しません。
既定の5分のAGY期限に対してセルは10分待ちます。長い期限を指定した場合は、セル側の待機も延ばします。
ホストからセルが返された場合は、そのセルを待ち直します。無期限の完了通知ではありません。

## 同時実行と使用量

[budget.py](../payload/.codex/es/budget.py)は、ソースのコピー前に、同じ`state-dir`のSQLiteトランザクション内で実行枠を確保します。
実行中の記録が1件あれば次の起動を拒否し、終了処理で枠を解放します。別の`state-dir`の実行は制限しません。
強制終了で実行中の記録が残った場合は、実プロセスの終了を確認してから、`budget.py mark-abandoned`で解放します。

呼出し回数とAGY内部のツール回数は無制限です。使用量はAGYの最後の結果を1回だけ記録し、各ステップの値を重ねて加算しません。
使用量が欠けた場合も終了を記録します。合計は不明のままとし、取得済み分は下限として表示します。
Codexの使用量は含まれません。AGYのキャッシュ値をCodexと同じ定義として換算しません。

## テスト・ビルドの出力

[capture.py](../payload/.codex/es/capture.py)はコマンドを1回実行して、完全な標準出力・標準エラーと実行情報を保存します。
正常終了ではログ末尾を短く、異常終了では長めに返します。詳細の確認には保存ログを使います。

```bash
python3 "$ES/capture.py" --repo . --out-dir "$RUN/tests" -- python3 tests/run_tests.py
```

終了コードだけでなく、実際に実行した試験と結果を確認します。
