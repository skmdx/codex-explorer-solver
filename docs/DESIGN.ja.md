# v4 設計: Codex親 + AGY evidence workers

## 不変条件

親はCodexで編集・検証を担当する。モデルによる探索はAGY経由のGemini 3.8 Flashに限定する。
Codexのnative child、独立codex exec、AGYの再帰的な子を利用しない。原文が必要な場面を
安価なモデルの説明で代替しない。既知のファイルとliteral検索だけならモデルを呼ばない。
既存Hooksは親のツール出力を管理し、AGY内部の全ツールを観測できるとは扱わない。

## モジュール境界

- `locate.py`: モード選択、事前確認、export、実行受付、呼出し、検証、最小限の返却。
- `agy_backend.py`: 一次仕様に沿ったCLIとNDJSON。初期化・tool・最終resultを監視。
- `agy_snapshot.py`: モデルを使わないexport、原文集合の検証、hash付加。
- `agy.toml`: キット独自の設定。Googleのglobal settingsではない。
- `agy_agents/*.md`: 起動時だけexportの`.agents/agents/`へ配置するmain-agent定義。
- `agy-handoff.schema.json`: モデルが返すhashなしの構造。
- `handoff.schema.json` / `evidence.py`: hash付き参照の検証と原文取得。`show`で一括実行する。
- `capture.py`: 完全なログを保存し、正常終了時は短い出力、異常終了時は詳細を返す。
- `budget.py`: 同じSTATEに対する外部worker受付の直列化と使用量の記録。
- `hook_*.py`, `configure_hooks.py`: v3から変更なし。登録の再実施は必須ではない。

## 通信契約

`agy --input-format stream-json --output-format stream-json --model SLUG --agent NAME
--json-schema SCHEMA_PATH` をshellなしのargvで実行する。期限指定時のみ`--print-timeout`を渡す。
stdinに `{event:user,message:{content:...}}` を1行送りEOF。`-p`を併用しない。
resume/continueを使わないため、最終usageは一回のworker内の累積値として扱える。
プロトコル上はinit→step_update*→result。stepのusageは足さない。
num_turnsはworker内部の継続回数であり、resumeの有無ではない。正の整数を受け入れる。

CLI出力を原則として親へ表示しない。events.jsonl、stderr.log、request.jsonlを私有に保存する。
最終structured_outputはschemaに加えて独立した形状・参照検証を通す。自由文・Markdownから
推測抽出する経路は持たない。モデルの利用可否はCLIが判定し、initで実際のmodelを確認する。
未知の通知eventでは処理を中断しない。既知eventの構造と会話IDは検証する。

## Explorer / Readerの境界

Explorerは `view_file` と `grep_search`、応答用の `finish` を使う。
管理下のagent定義はそのままCLIに渡し、独自frontmatter検査は行わない。
AGY 1.2.0のinit.toolsは全体カタログなので受付条件に使わない。
実際のtool eventを検査する。Readerは原文を番号付きJSONにまとめ、tool listはfinishのみ。
ask_permissionとmanage_taskは補助ツールとして両方に許容する。
finishがなければ応答後もCLIが継続を要求する。versionは整数の上下限で1に固定し、
AGYの関数schema変換が拒否する数値enumを使わない。

`mainAgent:true`, `subagent:false`。`model:inherit`とCLIの具体的なslugを組み合わせる。
`commandExecutionPolicy:off`, MCP/skills/pluginsは空。model/agentの実際の解決もinitで確認する。
これはAGYのmain agentであり、Codexの子モデル指定APIを流用した実装ではない。

## 原文snapshot

探索を元の作業ディレクトリから切り離すため、現在のworking treeの対象バイト列を
使い捨てexportへコピーする。Gitオブジェクトのcommit内容ではないので未commit変更が反映される。
既定はtracked files、untrackedは明示追加。Readerのpathsは完全に明示。
ルートで有効なagent設定とmetadata/credential-like pathを除外する。
配布用サブディレクトリにあるagent設定・実装はソースとして扱う。
ファイル数・合計サイズによる打切りは行わず、個別除外はmanifestに記録する。

AGYのwireにはhashがなく、ホストがmanifestの実行前hashを付ける。終了時にexportとoriginal双方の
全対象ファイルを検査し、uncited inputの変更も拒否する。何を根拠にしたかの意味的正確性は
証明できないため、親は原文を読む。実行中の追加ファイルや変更後に元に戻す操作まで捕捉する
原子的な全repo snapshotではない。独立copyの共有利用やatomic read-only mountとも区別する。

## 実行履歴と使用量

モデル開始前にSTATEにreserveし、終わればfinishする。同時実行は1件、回数は無制限。
強い探索も最初から実行でき、繰り返し回数を制限しない。呼出し失敗も履歴に残す。
終了時usageの欠落はunknownとして記録し、後続の受付を妨げない。
ツール回数とログ量による探索停止は行わない。期限は指定時だけラッパーが監視する。
旧STATEに保存された回数・token上限も受付条件として使わない。

## Hooksとの役割分担

CodexのPostToolUseは親から起動したlocate.pyという外側の操作しか直接扱わない。
内側のAGY read/searchはAGYイベント監視の担当。Codexのnative spawnカウンタはAGY予算として使わない。
既存test/search出力のarchive、compaction前後のarchive参照保存・復元はそのまま利用する。
親にはvalidated handoffと必要な原文だけを入れる。AGYのイベントをPostToolUseで要約するために
再度モデルへ送るような迂回を作らない。CLI失敗時だけ短いmetricsを読む。

## 安全性の限界

custom main-agent tools制限が主な機能制限。init監視は開始後の検知であり、実行前のOS境界ではない。
元repoをcwdとせずshell/writeを公開しないが、AGYのバグ、global hookや外部拡張、利用者のOS権限まで
封じるとは主張しない。`--mode=plan`や`--sandbox`をread-only保証として使わず、
`--dangerously-skip-permissions`は使用しない。Codex親の権限・ネットワーク制約も迂回しない。

## 移行と再現性

旧版のファイルhashが一致するときだけ更新する。native role三件は削除操作としてplan/backupへ記録。
独自に編集済みなら全更新を拒否し、config/AGENTS/hooksを自動修正しない。
旧role削除を含めrollback経路を持つが、同時に別プロセスが書き込む状態での実行はサポートしない。
テスト用fake AGYは別ファイルに明示し、既定runtimeへfallbackとして混ぜない。
実機での動作と使用量改善は別に測る。

## 評価

同じCodex親を使い、(A)親のみ、(B)旧Codex探索委譲、(C)AGY探索委譲を比較する場合でも、
成功率、受入条件、親usage、全worker usage、追加I/Oとレイテンシ、provider明細を区別する。
総token/成功は両providerのtokenizer差もあるので、費用の厳密な代用とはしない。
fake CLIのusage=130などはparser検証fixtureであり、モデル性能・使用量実測ではない。
