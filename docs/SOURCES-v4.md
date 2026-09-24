# v4 実装の一次資料

確認日: 2026-09-24。ローカルCLIのバージョン・モデル提供状況は別途検証する。
リンク先は変更され得るため、キットはhelp/models/init/resultの明示チェックを持つ。

1. Google Antigravity, Headless mode
   https://antigravity.google/docs/cli/headless/
   `--input-format stream-json`, user/message/content, EOF, init/step_update/result,
   cumulative usage, structured_output, --json-schema, --model, model listing,
   --print-timeout、headless認証・失敗時の扱い。
2. Google Antigravity, Custom Subagents
   https://antigravity.google/docs/subagents/
   `.agents/agents/` discovery、tools、mainAgent/subagent、model:inherit、commandExecutionPolicy。
3. Google Antigravity, Introducing Custom Agents (2026-08-12)
   https://antigravity.google/blog/introducing-custom-agents
   CLIの`--agent`でcustom agentをメインとして選択する方法。
4. Google Antigravity, Models
   https://antigravity.google/docs/models/
   Gemini 3.8 Flashの提供。具体的な利用可能slugは`agy models`を優先。
5. Google Antigravity, Choose an execution mode
   https://antigravity.google/docs/cli/modes/
   planは計画指示のprefixであり、read-onlyのOS制約ではない。
6. Google Antigravity, Terminal Sandbox
   https://antigravity.google/docs/sandbox/
   workspaceがread-writeであることと、コマンドの隔離範囲。
7. Google Antigravity, Permissions / Installation & Auth
   https://antigravity.google/docs/permissions/
   https://antigravity.google/docs/cli/install/
   global設定と権限の適用。キットからglobal設定を編集しない。
8. OpenAI, Configuration Reference / Subagents
   https://learn.chatgpt.com/docs/config-file/config-reference
   https://learn.chatgpt.com/docs/agent-configuration/subagents
   `agents.enabled=false`で親Codexのnative multi-agent toolsを無効化。
9. OpenAI, Hooks
   https://developers.openai.com/codex/hooks
   v3の親側Hook設計の参照。AGY内部ツールへの監視を保証するものではない。

研究の背景資料は既存 `SOURCES.md` を歴史的参考として保持している。
v4では研究結果を再現したと主張せず、provider/CLI切替えと機械的制御を実装した。
