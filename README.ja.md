# Explore → Solve — Codex plugin

Codexがコードを調査するとき、必要な部分だけを読んで修正・テストへ進むためのプラグインです。`explore-solve`スキルと実行ツールを同梱します。
小さな調査はCodexが直接行い、大量のソースを読む独立した調査は、Antigravity CLI（`agy`）を通じてExplorerに任せます。
Explorerからは関連するファイルと行番号を受け取り、Codexがその原文を確認して実装します。

目的は、Codexに渡すソース量と、結果を受け取るまでの往復を減らすことです。
場所が分からないだけで毎回Explorerを起動することはありません。

収集はSonnet 5.5 (High)を優先し、利用量上限時のみGemini Flash Highへ会話を引き継ぎます。期限は全試行で共有します。
返された参照のパス・行範囲・形式が不正な場合は、検証エラーと却下した参照をAGYへ返し、同じ会話で最大2回修正を求めます。修正でも収集全体の期限を共有し、上限到達時は最後の検証エラーを返します。ソース改変や実行失敗は修正依頼の対象外です。

## 使い方

この環境では個人marketplaceの`codex-explorer-solver@personal`として導入済みです。新しいスレッドで、コード調査に応じてスキルが自動選択されます。対象のGitリポジトリでCodexを開き、通常どおり依頼してください。明示的に使う場合はスキル一覧から`explore-solve`を選択できます。

```text
$codex-explorer-solver:explore-solve
ログイン失敗時に同じ通知が2回表示される原因を調べて修正してください。
修正後は関連するテストを実行してください。
```

分かっているファイル名や再現手順があれば、一緒に伝えます。毎回、探索用ファイルや設定を手で用意する必要はありません。
`/home/user/codex-work` 以下の各リポジトリで使えます。スキルを指定しただけでは、必ずExplorerを呼ぶわけではありません。
大きな調査を明示的に任せたい場合は、「AGYで調査してください」と依頼に含めます。

AGYを使うには、認証済みの`agy`と`uv`が必要です。プラグインのMCPサーバーは`uv`で依存ライブラリを読み込みます。
調査対象のソースと質問はGoogleのサービスへ送信されます。

独立レビューや実装の委譲には、同梱の[agy-subagentsスキル](skills/agy-subagents/SKILL.md)を使います。
`agy-subagents.run`は`request: {task, scratch_ref, ...}`を受け取り、終了まで待ちます。
レビューは既定のモードで、ファイルを使うときだけ`repo`を指定します。
編集は`mode: "edit"`と`repo`が必須です。設定されたClaudeだけで実行し、Geminiへの切替と呼出しごとのモデル変更は受け付けません。
レビューの`preferred_model`は優先指定で、利用上限・利用不能時には自動切替します。通常は省略してください。
編集が失敗した場合は部分変更を確認し、親エージェントが作業を引き継ぎます。
モデル設定・切替・ログの詳細は[AGYの実行と待機](docs/DESIGN.ja.md#agyの実行と待機)を参照してください。

## 内部で行うこと

1. Codexが手掛かりを検索し、直接読む範囲と、Explorerに任せる調査を決めます。
2. Explorerを使う場合は、対象ソースの一時コピーを作ります。ファイルが決まっていればその全文を渡し、広い調査ではコピー内を検索させます。
3. `collect`が完了まで待機し、ソースの一致を検証して引用位置の一覧を返します。既知のLSP結果は初期入力へ直接渡します。
4. Codexが必要なIDを`read_evidence`で選んで原文を読み、元のリポジトリを修正・テストします。

Explorerは関連する定義・呼出し・条件分岐・テストと、その原文を集めます。
競合シナリオの構成、保証範囲の判断、修正方針、編集・テストはCodexが担当します。
版番号・収集状態・ハッシュ・引用パスの変換はホストが処理し、Explorerには引用と不足事項だけを返させます。
探索範囲はGit追跡済みファイルから選び、`include_untracked`でignoreされていない未追跡ファイルを追加します。
ignore対象や別リポジトリのファイルを直接読む場合は`source: "files"`を使います。
読取り・検索専用のエージェントで権限確認を自動承認し、非対話実行中の確認待ちによる終了を防ぎます。
同じソースを使う問いはまとめて収集し、別の探索範囲が必要な場合に分けます。
収集結果は短い説明と引用位置だけです。必要な原文は選択して取得し、大きい場合は続きの位置を返します。
同じID選択は前回の続きから返します。読み終えた原文は、IDの組合せが変わっても重複表示しません。
Symbolsの`read_symbols`結果を渡した場合も、ファイルの版が一致する取得済み範囲を省略します。説明は収集一覧に置き、原文取得では再掲しません。
原文のインデント・改行は保持し、行番号は本文の外に付けるため、`apply_patch`の照合に利用できます。
コンテキスト消失後の再読は`reread: true`で指定できます。
完全な原文は実行先の`report.json`へ保存します。
文字コードはファイルごとにツールが判定します。ASCII・UTF-8・CP932・EUC-JPが混在していても、Codexが先読みして指定する必要はありません。
ソースの一致を検証しても、Explorerの説明が正しいとは限らないため、Codexは引用原文を読みます。

AGYの処理中は直接のMCP呼出し内で待ちます。プラグイン設定でCode Modeからの呼出しを除外し、
短いセル待機やポーリングをモデルが選択する経路をなくします。

## 待機の制御

`.mcp.json`の`omit_tools_from: ["code_mode"]`で、このMCPをCode Mode内部から除外します。
Codex 0.156.1で対応しています。ユーザーの`config.toml`への追加設定は不要です。

プラグイン更新後は新しいセッションで使用します。
`collect`・`read_evidence`は直接のMCPツールとして公開され、Code Mode内の`tools`と`ALL_TOOLS`には入りません。

`collect`は`request`を必須引数とし、通常収集・明示ファイル・保存済みリクエストの3形式を型で区別します。
必要なフィールドと例は[収集リクエスト](skills/explore-solve/references/agy.md)を参照してください。
Symbolsの結果は`navigation: [{tool, result}]`として渡すと自動保存され、返された`navigation_id`で再利用できます。
失敗時に返された`params_file`は`request: {params_file, updates}`で訂正・再試行できます。
検証済みで完了したリクエストファイルは自動削除します。

Scratchプラグインの`create`が返す英単語IDを`collect`・AGY Subagentsの`scratch_ref`へ渡します。
引数JSONも返されたディレクトリ内へ保存してください。証拠・ログが不要になったら、
Scratchの`delete(refs=[...])`で収集結果・navigation・再試行引数をまとめて削除します。
使用中の参照はロックされ、削除は`skipped_active`となります。他セッションは削除しません。
共通クライアント`scratch_space.py`はworkspaceの`tools/scratch/sync.py`で同期する生成物です。

- 呼び出し回数と、AGY内部のツール使用回数に上限はありません。通常は1件ずつ実行し、異常終了の記録が残っていても次の調査を実行できます。
- 収集はSonnetから開始し、利用上限時はGemini Flash（High）へ切り替えます。設定は`agy.toml`、1回だけの指定は`request.model`を使います。
- AGYの使用量は実行ごとに記録します。取得できなかった値は不明のまま残し、次の実行を妨げません。Codexの使用量は別です。
- テストやビルドの大量出力はファイルに保存し、Codexには終了コードとログ末尾を返します。必要な詳細は保存したログから読みます。

## ファイルの場所

| 用途 | 場所 |
|---|---|
| Codexが読むスキル | [SKILL.md](skills/explore-solve/SKILL.md) |
| 独立レビュー・実装の委譲 | [agy-subagents](skills/agy-subagents/SKILL.md) |
| AGYのモデル設定 | [agy.toml](payload/.codex/es/agy.toml) |
| プラグイン定義 | [.codex-plugin/plugin.json](.codex-plugin/plugin.json) |
| 実行ツール | インストールされたプラグイン内の `payload/.codex/es` |
| 作業中のソースコピー・ログ | `/home/user/codex-work/tmp` 以下のタスク別ディレクトリ |

スキルは自身の配置場所から同梱ツールを参照します。対象リポジトリへのツールコピーや、ワークスペース固定のリンクは不要です。
導入済みプラグインはCodexのキャッシュにあるため、このリポジトリの変更を反映するには再インストールします。
ビルド・テストの実行とログ保存にはBlocking Shellプラグインを使います。
同梱の`PostToolUse` hookは、シェル結果の切り詰めを検出したときだけ、取得方法の見直しとスキル利用の検討を英語で案内します。
元の結果はそのまま返し、スキルの使用は強制しません。導入先では`/hooks`でこのhookを確認・信頼すると有効になります。

## 更新

この環境の個人marketplaceは`~/plugins/codex-explorer-solver`からこのリポジトリを参照しています。
変更後は`.codex-plugin/plugin.json`のバージョン末尾`+codex.YYYYMMDDHHMMSS`を現在のUTC時刻に更新し、`SHA256SUMS`を再生成してから次を実行します。

```bash
codex plugin add codex-explorer-solver@personal
```

新しいスレッドで更新後のスキルが読み込まれます。Python依存ライブラリと認証済みの`agy`はホスト側に必要です。

## 開発・動作確認

このリポジトリのルートで実行します。AGYやGeminiへの通信は発生しません。

```bash
TMPDIR=/home/user/codex-work/tmp uv run --with-requirements requirements.txt python tests/run_tests.py
```

更新済みプラグインを実際のCodex・AGYで検証する場合は、次を実行します。両サービスの利用量を消費します。
実AGYの起動を65秒遅らせ、Code Mode内からの呼出し拒否、直接収集の完了待ち、ポーリング0回、原文取得をセッションログで検査します。

```bash
python3 tests/smoke_direct_collect.py --out-dir /home/user/codex-work/tmp/es-direct-check-run
```

一般の委譲は`python3 tests/smoke_subagents.py --out-dir /home/user/codex-work/tmp/agy-subagents-check`
で検証します。実際のレビューとClaudeによる小さな編集を順に実行し、各起動を65秒遅らせて
直接MCP待機・Code Modeからの呼出し拒否・ポーリング0回・編集結果を確認します。

MCPの引数、返却ファイル、原文検証と使用量記録の実装は、[設計とツールの使い方](docs/DESIGN.ja.md)を参照してください。
