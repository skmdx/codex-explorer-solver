# v4 — Antigravity CLI / Gemini 3.8 Flash

## 呼出し先の変更

- Codex native subagentと`codex exec` workerを廃止。親だけがCodex。
- `agy --input-format stream-json --output-format stream-json` に1件のuserイベントを送りEOF。
- Explorer/Readerは `gemini-3.8-flash-medium`、追加探索も同じ系列のHigh。
- `agy --agent` で制限付きmain agentを指定。AGY側からの再委譲なし。
- モデル非対応・CLI仕様違い・認証失敗から別モデルへ自動変更しない。

## 制御の変更

- CLIのhelp/version/modelsによる事前確認。
- 実効model/agent/tool listのinit検証。未知のevent、予期しないtool、再委譲、重複resultを拒否。
- Codex親の会話をコピーしない。長い課題・Reader原文はargvでなくstdin。
- 一時exportで探索。Git追跡済みの現在の作業ツリーを既定範囲にし、構成/秘密らしいpathを除外。
- AGYはhashを作らないwire schemaへ変更。ホストが実行前の原文hashを付けて既存validatorへ渡す。
- 全export対象について原文変更を検証。scope外への参照を拒否。
- 外部STATEをモデル実行の必須条件に変更。元の二回までの共通予算を利用。
- 最後のAGY usageを一度だけ記録。reasoning/cache/step別累積を重複計上しない。

## 維持したもの

- 親のexact-source検証、gateway、capture、原文参照schema（親向け）。
- v3 Hook runtime/store/artifacts/configureのコードと既存登録。
- 利用者の実config・AGENTS.md・無関係なエージェント設定。

## 移行

- 標準v1/v2/v3を認識する更新処理。旧native role三件をバックアップして撤去。
- 編集済みの旧roleがあると全更新を停止。自動で捨てない。
- create-only新規導入。親を `codex -c 'agents.enabled=false'` で起動する手順。

## 検証の位置づけ

- AGY/Codexの実機を利用した結合試験や性能測定は行っていない。
- fake AGYはテスト専用で、実際のGoogle応答と誤認しない明示ラベルを付けた。
- `test-results.txt`、`smoke-agy-results.json`、`smoke-hooks-results.json` が検証記録。
- 任意の実機確認用 `tests/smoke_agy_live.py --run-models` を追加（実quotaを消費）。
