# 一次資料・実装との対応

確認日: 2026-09-24。リンク先の結果を本キットの実測値として使ってはいません。
前版の全参考文献を惰性で継承せず、今回の判断に使ったものを記載します。論文本文はHTML版を参照しました。

## 工学事例

[1] Spotify Engineering. **Portal by Spotify cut my Claude Code token usage by 90%**. 2026-09-03.
https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90
既知ファイルの事実抽出、読み取りの経路制御という着想。見出しの率は本キットの期待値ではない。

[2] Spotify. **portal-ai-plugins / shunt** (著者の実装・README).
https://github.com/spotify/portal-ai-plugins
https://github.com/spotify/portal-ai-plugins/blob/main/plugins/shunt/README.md
確認時点ではPortal側のCodex導入説明はあるが、shuntはClaude Code用と記載。キットへのコード転載はしていない。

## 研究

[3] Alex L. Zhang, Tim Kraska, Omar Khattab. **Recursive Language Models**. arXiv:2512.24601v1.
https://arxiv.org/html/2512.24601v1
長い入力を外部環境で扱う設計、およびsub-callなしのアブレーションを参照。
一般の長文タスク研究であり、このCodexハーネスの不具合修正性能を保証するものではない。

[4] Luzhuo Chen, Jiayu Shi. **An Empirical Cost Attribution of Context-Compression Gateways in Multi-Turn Coding Agents**. arXiv:2609.22114v1.
https://arxiv.org/html/2609.22114v1
圧縮率ではなくキャッシュ・再読取り・turn数を含めて評価する観点。特定gatewayの著者評価であり、
小さい実験・Python中心・5turnのフィット等の制約が本文にある。一般的な節約係数にはしない。

[5] Mohammad Nour Al Awad, Sergey Ivanov. **Cost-Effective Repository Exploration for Agentic Issue Localization**. arXiv:2608.29675v1.
https://arxiv.org/html/2608.29675v1
探索役の独立評価とsoft handoff。条件付き昇格の提案を設計へ反映するが、そのend-to-end修正効果は本キットで未計測。

[6] Brian Sam-Bodden. **What Context Does a Coding Agent Actually Need to Act?**. arXiv:2607.09691v1.
https://arxiv.org/html/2607.09691v1
編集根拠に原文を残す判断の参考。Oracle localization・single-shot条件を一般の長期agentへ無条件に外挿しない。

[7] Tobias Lindenbauer et al. **The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management**. arXiv:2508.21433v3.
https://arxiv.org/html/2508.21433v3
単純な観測管理を基準にする根拠。v2はCodexの過去履歴を直接maskする実装ではない。

[8] Yuhang Wang et al. **SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents**. arXiv:2601.16746v4.
https://arxiv.org/html/2601.16746v4
質問の目的に沿って原文箇所を選ぶ発想。学習済みskimmerは導入せず、同論文の効果量を本キットには割り当てない。

## OpenAI公式仕様

[9] **Hooks**.
https://learn.chatgpt.com/docs/hooks
PreToolUseの入力/deny形式、Bash alias、tool coverage、親子session_id、信頼レビュー。完全な境界ではないとの制約も参照。

[10] **Subagents**.
https://learn.chatgpt.com/docs/agent-configuration/subagents
カスタムrole、設定継承、モデルとreasoning effort、親のruntime override。利用可能モデルはアカウント依存。

[11] **Non-interactive mode**.
https://learn.chatgpt.com/docs/non-interactive-mode
codex exec --json、turn.completedのusage、構造化出力と最終結果ファイル。

[12] **Developer commands** / **Configuration reference**.
https://learn.chatgpt.com/docs/developer-commands
https://learn.chatgpt.com/docs/config-file/config-reference
--strict-config、--ephemeral、--sandbox、-c、設定キー。実CLIの互換性は起動時preflightとローカルsmokeで確認する。

[13] **Build skills**.
https://learn.chatgpt.com/docs/build-skills
明示スキル呼出しとallow_implicit_invocation=false。

## 証拠の強さについて

論文、ベンダー工学記事、公式インターフェース仕様、本キット独自の閾値、ローカルのモック試験を区別する。
特に「API互換性をモックで検査できた」と「実Codexで正常に動いた」は別である。

## v3で再確認した公開仕様

2026-09-24にOpenAI公式Hooksページを再確認した。一次仕様は以下。
https://developers.openai.com/codex/hooks
https://learn.chatgpt.com/docs/hooks

特に参照した箇所:
- Tool coverage / Common input fields: Bash alias、spawn_agent/Agent、親子session_id、未対応経路。
- PostToolUse / Tool calls from code mode: continue:falseとdecision:blockの差、未サポートのフィールド。
- PreToolUse: deny形式。本版はallow/updatedInputによる書換えを採用しない。
- PreCompact / SessionStart: source=compact/resumeと復元案内のタイミング。
- Where Codex looks for hooks / Review and trust hooks: 既存設定との併存、同時実行、定義変更後の信頼レビュー。

Codexの公開ドキュメントは、mainブランチのschemaがリリースより先行し得ると注意している。
本版は、未確認のmain専用フィールドを利用せず、実環境では小さなsmokeで契約を確認する方針である。
旧版に記載した論文/Spotify記事の効果量は、v3の測定結果としては一切使っていない。
