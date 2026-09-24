# v4 設計: Codex親 + AGY evidence workers

## 不変条件

親はCodexで編集・検証を担当する。モデルによる探索はAGY経由のGemini 3.8 Flashに限定する。
Codexのnative child、独立codex exec、AGYの再帰的な子を利用しない。原文が必要な場面を
安価なモデルの説明で代替しない。既知のファイルとliteral検索だけならモデルを呼ばない。
既存Hooksは親のツール出力を管理し、AGY内部の全ツールを観測できるとは扱わない。

## モジュール境界

- `locate.py`: モード選択、事前確認、export、予算受付、呼出し、検証、最小限の返却。
- `agy_backend.py`: 一次仕様に沿ったCLIとNDJSON。初期化・tool・最終resultを監視。
- `agy_snapshot.py`: モデルを使わないexport、原文集合の検証、hash付加。
- `agy.toml`: キット独自の設定。Googleのglobal settingsではない。
- `agy_agents/*.md`: 起動時だけexportの`.agents/agents/`へ配置するmain-agent定義。
- `agy-handoff.schema.json`: モデルが返すhashなしの構造。
- `handoff.schema.json` / `evidence.py`: 親が読む従来互換のhash付き構造と検証。
- `budget.py`: 同じSTATEに対する外部worker受付の直列化と上限。
- `hook_*.py`, `configure_hooks.py`: v3から変更なし。登録の再実施は必須ではない。

## 通信契約

`agy --input-format stream-json --output-format stream-json --model SLUG --agent NAME
--json-schema SCHEMA_PATH --print-timeout 120s` をshellなしのargvで実行する。
stdinに `{event:user,message:{content:...}}` を1行送りEOF。`-p`を併用しない。
resume/continueを使わないため、最終usageは一回のworker内の累積値として扱える。
プロトコル上はinit→step_update*→result。stepのusageは足さない。

CLI出力を原則として親へ表示しない。events.jsonl、stderr.log、request.jsonlを私有に保存する。
最終structured_outputはschemaに加えて独立した形状・参照検証を通す。自由文・Markdownから
推測抽出する経路は持たない。CLIが未知のmodel slugを拒否する仕様に加え、ローカルmodel一覧と
initのmodelを検査する。認識外のCLI版で安全に動いたふりをするより、失敗を明示する。

## Explorer / Readerの境界

Explorerは `view_file` と `grep_search` のみ。実行前に定義のfrontmatterを制限された形式で
検査し、実行時にinit.toolsにも余計な機能がないか確かめる。Readerは原文を番号付きJSONに
まとめ、tool listは空にする。ask_permissionは補助ツールとして両方に許容する。
CLIが別の必須toolを報告する場合、勝手に許可リストを広げず実仕様を確認する。

`mainAgent:true`, `subagent:false`。`model:inherit`とCLIの具体的なslugを組み合わせる。
`commandExecutionPolicy:off`, MCP/skills/pluginsは空。model/agentの実際の解決もinitで確認する。
これはAGYのmain agentであり、Codexの子モデル指定APIを流用した実装ではない。

## 原文snapshot

探索を元の作業ディレクトリから切り離すため、現在のworking treeの対象バイト列を
使い捨てexportへコピーする。Gitオブジェクトのcommit内容ではないので未commit変更が反映される。
既定はtracked files、untrackedは明示追加。Readerのpathsは完全に明示。
metadata/config/credential-like pathを除外するが、秘密の完全検出は主張しない。
上限超過を黙ってtruncateしない。個別除外はmanifestに記録し、不存在の主張は固定export集合に限定。

AGYのwireにはhashがなく、ホストがmanifestの実行前hashを付ける。終了時にexportとoriginal双方の
全対象ファイルを検査し、uncited inputの変更も拒否する。何を根拠にしたかの意味的正確性は
証明できないため、親は原文を読む。実行中の追加ファイルや変更後に元に戻す操作まで捕捉する
原子的な全repo snapshotではない。独立copyの共有利用やatomic read-only mountとも区別する。

## 予算

モデル開始前にSTATEにreserveし、終わればfinishする。強い探索は先行workerが存在するときだけ。
呼出し失敗も受付枠を戻さない。終了時usageの欠落はunknown。通常次の受付を拒否する。
ローカルプロセス終了は遠隔の課金キャンセルを保証しない。観測tool上限も事前課金上限ではない。
2回/タスクは本キットの同一STATE使用経路の制限。直接agyや別STATEまで規制するものではない。

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
