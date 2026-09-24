# v3 — Hooksでの出力保存・縮小

## 追加

- `hook_runtime.py`: PostToolUseの限定的な出力保存/置換、任意のnative spawn上限、compaction参照の保存/復元。
- `hook_store.py`: 容量を制限したprivate blob、SHA-256復元検証、SQLiteによるメタデータ/受付回数管理。
- `hook_artifacts.py`: 保存出力のinfo/list/literal search/行読取り/byte slice/report。
- `configure_hooks.py`: dry-run、既存JSONの保持、所有するHookだけの更新/削除、backup必須のapply。
- Hooks用の契約試験と、実コマンドを使った人工Hook-envelope smoke。

## 変更

- `upgrade.py`は未改変のv1とv2 payloadを受け付ける。利用者の編集は拒否して保持する。
- Solverスキルへ、短い返却をコマンド失敗と誤解しないルール、原文復元、native上限の適用範囲を追加。
- 親のモデル、compaction設定、AGENTS.md、sandboxやtrustは自動変更しない。

## 採用しなかったもの

- PreToolUseによるコマンドの自動書換え/権限承認。
- LLMを呼ぶHook、Stopでの強制再試行・自動テスト、未知の機械向けMCP出力の置換。
- 全出力の要約、コード原文の全面置換、意味的回答のキャッシュ、活動中の参照を壊す自動GC。

## 互換性上の注意

Hookはopt-in。初期modeはauditである。新しいHook定義の信頼確認は利用者が`/hooks`で行う。
既存v2 read_guardは消さない。コードモード等で結果が再出力される場合、再流入を防ぐ保証はない。
ローカル試験は実Codex結合/修正品質/費用測定の代わりではない。

旧版設計・導入手順は`docs/V2-DESIGN.ja.md`/`docs/V2-README.ja.md`へ保存した。
