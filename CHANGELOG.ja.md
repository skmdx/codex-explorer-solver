# v4 — Antigravity CLI / Gemini 3.8 Flash

## 呼出し先の変更

- Codex native subagentと`codex exec` workerを廃止。親だけがCodex。
- `agy --input-format stream-json --output-format stream-json` に1件のuserイベントを送りEOF。
- Explorer/Readerは `gemini-3.8-flash-medium`、追加探索も同じ系列のHigh。
- `agy --agent` で制限付きmain agentを指定。AGY側からの再委譲なし。
- モデル非対応・CLI仕様違い・認証失敗から別モデルへ自動変更しない。

## 制御の変更

- CLIのversionを記録し、モデル利用可否は実行時に確認。
- 実効model/agentのinit検証。実際の許可外tool実行、再委譲、重複resultを拒否。
- Codex親の会話をコピーしない。長い課題・Reader原文はargvでなくstdin。
- 一時exportで探索。Git追跡済みの現在の作業ツリーを既定範囲にし、構成/秘密らしいpathを除外。
- AGYはhashを作らないwire schemaへ変更。ホストが実行前の原文hashを付けて既存validatorへ渡す。
- 全export対象について原文変更を検証。scope外への参照を拒否。
- 外部STATEは初回呼出しで作成。同時実行は1件、累計回数・token・tool呼出しの上限なし。
- 最後のAGY usageを一度だけ記録。reasoning/cache/step別累積を重複計上しない。
- 初期化・結果検証・引用原文取得を1回の`locate.py`呼出しに統合。原文合計サイズによる拒否を削除。
- 通常調査は直接検索し、広範な独立調査を委譲。待機はcode-modeセル内で継続し、空の状態確認でモデルを再起動しない。

## 維持したもの

- 親のexact-source検証、gateway、capture、原文参照schema（親向け）。
- v3 Hook runtime/store/artifacts/configureのコードと既存登録。
- 利用者の実config・AGENTS.md・無関係なエージェント設定。

## 移行

- 標準v1/v2/v3を認識する更新処理。旧native role三件をバックアップして撤去。
- 編集済みの旧roleがあると全更新を停止。自動で捨てない。
- create-only新規導入。親を `codex -c 'agents.enabled=false'` で起動する手順。

## 検証の位置づけ

- 現在の実機確認・比較結果への参照はREADMEの検証欄に記載。
- fake AGYはテスト専用で、実際のGoogle応答と誤認しない明示ラベルを付けた。
- `test-results.txt`、`smoke-agy-results.json`、`smoke-hooks-results.json` は初期配布時の検証記録。
- 任意の実機確認用 `tests/smoke_agy_live.py --run-models` を追加（実quotaを消費）。
