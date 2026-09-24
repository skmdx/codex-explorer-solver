# 設計とツールの使い方

通常の利用方法は[README](../README.ja.md)を参照してください。
ここでは、調査を手動実行するときや、ツール本体を変更するときに必要な内容を説明します。

## 手動でAGYを呼ぶ

対象のGitリポジトリのルートで実行します。Python 3.11以降、Git、認証済みのAGYが必要です。
文字コード判定には `charset-normalizer` を使います。`python3 -m pip install -r requirements.txt` で依存ライブラリを導入します。
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
同じソースや呼出し元を使う証拠収集はまとめ、探索範囲が別になる場合に分けます。
AGYには定義・呼出し・状態更新・条件分岐・テストの位置と原文を収集させます。
競合順序の構成、保証範囲の判断、修正方針はCodexが担当します。
見つかった根拠と未解決の問いは`partial`でも返せます。Codexが結果を統合し、不足分だけを追加調査します。

| 調査方法 | 追加する引数 |
|---|---|
| ディレクトリを絞って検索する | `--scope src --scope tests` |
| 指定ファイルの全文を読ませる（Reader） | `--mode reader --path src/login.py`。複数ファイルは`--path`を繰り返す |
| `deep_model`で検索する | `--deep`。既定値は通常と同じHigh。Readerとは併用しない |
| 未追跡ファイルも検索対象にする | `--include-untracked`。Gitのignore対象は含まない |
| 実行期限を指定する | 例：`--timeout 900`。省略時は5分。長い横断調査では期限を明示する |

Readerは`--scope`・`--include-untracked`と併用せず、明示したファイルだけを渡します。
モデル設定は[agy.toml](../payload/.codex/es/agy.toml)にあり、1回だけ変える場合は`--model`を使います。

### LSPの探索結果を渡す

localizeでは`--navigation-file FILE`でSymbolsの結果を初期入力へ含められます。
JSONは`root`（LSPのworkspacePathの絶対パス）と`queries`（問い合わせ条件と結果の配列）を持ちます。
結果のテキストは解析し直さずそのまま渡します。相対パスは`root`基準で解釈し、
対象リポジトリ内の位置をソースコピー内の同じ相対位置へ読み替えます。
範囲外の候補は未調査の手掛かりであり、元リポジトリを直接読む指示にはしません。

Codexは必要な定義・参照・呼出し関係だけを指定します。LSP結果の保存とAGY起動を同じセルで行い、
結果全文をCodexへ表示して転記する往復を省きます。具体例は[AGY参照手順](../payload/.agents/skills/explore-solve/references/agy.md#optional-lsp-starting-locations)にあります。
LSPだけで回答できる問いにはAGYを使いません。LSP結果からexport範囲を自動縮小せず、
Geminiは足りない条件・呼出し・テストを追加探索します。失敗した問い合わせも結果と区別して渡します。
入力は`RUN/navigation.json`に保存します。その回の探索用で、STATEへの蓄積は行いません。

## 結果を読む

標準出力は、実行状態・収集状態・対象件数・短い観察事実・番号付き原文です。
重なる原文行は表示時に統合し、異なる観察事実は残します。件数や行数で切り捨てません。
その原文を使って判断し、必要なら欠けている呼出し元や周辺を確認します。
機械処理には `--json` を指定します。完全なJSONには `status`・`handoff_status`・`usage`・`scope`・`error`、
ハッシュと原文を含む `evidence` があり、標準出力の形式にかかわらず `report.json` へ保存します。

実行先には次のファイルを保存します。

| ファイル | 内容 |
|---|---|
| `report.txt` | 通常出力と同じ、重複をまとめた原文付きテキスト |
| `report.json` | 原文と使用量を含む完全な実行結果 |
| `handoff.json` | ハッシュ付きの引用位置と調査結果。原文自体は含まない |
| `metrics.json` | 成否、使用モデル、使用量、失敗理由 |
| `source-manifest.json` | コピーしたソースと除外理由 |
| `events.jsonl`・`stderr.log` | AGYの出力。障害調査が必要なときに読む |
| `sources/` | AGYが調べたUTF-8のソースコピー |
| `workspace/` | 調査用エージェントの実行設定 |

出力が切れた場合は `report.txt` の欠けた箇所だけを読みます。保存した引用を現在のソースと再照合するときと、使用量の履歴を見るときは次を使います。

```bash
python3 "$ES/evidence.py" show --root . --handoff "$RUN/explore/handoff.json"
python3 "$ES/budget.py" status --state-dir "$RUN/state"
```

エラー時は出力の `Error` を確認し、追加情報が必要なら`metrics.json`を読みます。
AGYは期限切れでも`SUCCESS`と空の結果を返すことがあります。構造化結果がなければ失敗として扱い、
標準エラーの期限切れ理由も`error`へ返します。追加調査では問いの範囲と`--timeout`を見直します。
起動設定や結果保存など、実行処理の外に出た例外は`invocation_failed`、使用量記録の失敗は`accounting_failed`です。
CLIの引数構文エラーは標準エラー出力に返ります。
`accounting_failed`でも検証済みの`evidence`は返るため、調査をやり直さず記録側の問題を解消します。
ソースが変わっていた場合は、その結果を編集の根拠にせず現在の原文を確認します。
作業後は必要な結果を回収し、`RUN`以下の一時コピーとログを削除します。

## ソースの受け渡しと検証

AGYの回答と保存するhandoffは`version: 3`です。状態は`ready`・`partial`・`not_found`・`blocked`、
不足や障害の説明は`unresolved`にまとめます。[回答スキーマ](../payload/.codex/es/agy-handoff.schema.json)で形式を定義します。
返す状態は実際の引用と未解決事項から求めます。引用あり・未解決事項ありなら`partial`、引用だけなら`ready`、
引用なしなら`not_found`（環境上の障害を報告した場合は`blocked`）です。
モデルの状態ラベルとの不一致を理由に、有効な原文を捨てたり再調査させたりしません。
引用箇所と未解決の問いの件数に固定上限はありません。同じ範囲が別の判断の根拠になる場合も返せます。
引用の行数・原文出力・handoffのバイト数に追加上限は設けません。

[agy_snapshot.py](../payload/.codex/es/agy_snapshot.py)は、Git追跡済みファイルの現在の内容をコピーします。
未コミットの変更も含みます。Readerでは指定したファイルを使います。
ファイル名による一律の除外はありません。Git内部のファイルは追跡済み一覧に含まれないため、
Git hookの調査ではReaderに `.git/hooks/pre-commit` などの実際のパスを渡します。
Readerはリポジトリ外の絶対パスやsymlinkも扱えます。Explorerではリポジトリ外を指すsymlinkは対象外です。
エージェント設定も調査データとしてコピーし、実行設定とは別のディレクトリに置きます。
ファイル数や合計サイズの上限はありません。読み取れなかったファイルと理由はmanifestに記録します。

文字コードはファイルごとに判定し、UTF-8へ変換して渡します。ASCII・UTF-8・CP932・EUC-JPが
混在した入力でも、Codexの先読みや指定は不要です。判定した文字コードをmanifestとhandoffに保存し、
引用の再取得にも使います。短い文字列などでは判定を誤る場合があり、既知の誤判定は
`--encoding path/to/file=cp932` でそのファイルだけ訂正できます。複数指定はオプションを繰り返します。

AGY終了後は、コピーした全ファイルについて、コピーと元ソースが実行前の内容に一致するか確認します。
変換前の原本とUTF-8コピーのハッシュを別々に記録します。引用のハッシュと文字コードはAGYに生成させず、実行前の記録から付けます。
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

Codex側の起動と待機は[AGY参照手順](../payload/.agents/skills/explore-solve/references/agy.md#invoke)の一つのセルで行います。

## 同時実行と使用量

[budget.py](../payload/.codex/es/budget.py)は、実行の開始・終了と使用量をSQLiteに記録します。
実行中の記録が残っていても次の起動を拒否しません。強制終了した記録を終了扱いにするには
`budget.py mark-abandoned --state-dir STATE --job-id ID` を使えます。これは使用量記録の整理であり、再実行の前提ではありません。

呼出し回数とAGY内部のツール回数は無制限です。使用量はAGYの最後の結果を1回だけ記録し、各ステップの値を重ねて加算しません。
使用量が欠けた場合も終了を記録します。合計は不明のままとし、取得済み分は下限として表示します。
Codexの使用量は含まれません。AGYのキャッシュ値をCodexと同じ定義として換算しません。

## テスト・ビルドの出力

[capture.py](../payload/.codex/es/capture.py)はコマンドを1回実行して、完全な標準出力・標準エラーと実行情報を保存します。
既定では時間・ログ量でコマンドを打ち切りません。必要な場合だけ`--timeout`・`--log-limit-bytes`を指定します。
正常終了ではログ末尾を短く、異常終了では長めに返します。詳細の確認には保存ログを使います。

```bash
python3 "$ES/capture.py" --repo . --out-dir "$RUN/tests" -- python3 tests/run_tests.py
```

終了コードだけでなく、実際に実行した試験と結果を確認します。
