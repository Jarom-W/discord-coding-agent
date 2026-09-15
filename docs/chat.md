# Chat controls and live follow-ups

No new setup is needed after installing this release. Continue using the same bot, token, channels and saved sessions. All responses stay inline in chat; there is no text-file fallback.

## Add instructions while Codex works

**In a bound Discord channel:**

```text
You: !run 30m Fix the failing tests and explain the changes.
Bot: Task accepted. Task limit: 1800s including initialization and human wait.
You: Also check the README examples against the updated behavior.
Bot: Follow-up received · #1
Bot: Follow-up #1 accepted by Codex
```

Send ordinary text; no new command is required. The second message adds instructions to the **same active coding task and conversation**. Codex decides when to incorporate it as execution proceeds; acceptance does not mean an already-running command stopped or that the requested work has finished. Use `!stop` for interruption. Send corrections, extra requirements or questions this way, just as you can add input during a Codex CLI turn.

The bridge uses the supported [`turn/steer` method](https://learn.chatgpt.com/docs/app-server#steer-an-active-turn) with the expected active turn ID, checked against the installed Codex 0.153.4 schemas. It starts no parallel coding turn. After completion, ordinary text starts the next turn in the saved conversation as before.

## Understand the receipts

| Receipt | Meaning and next step |
| --- | --- |
| **Follow-up received** | The bridge reserved the message for this task. During startup it waits for the initial turn acknowledgement. Watch for a second receipt confirming Codex acceptance. |
| **Accepted by Codex** | Codex acknowledged the expected active turn. The instruction has been added; it is not a promise of completion or immediate interruption. |
| **NOT submitted** | The bridge did not send it, or Codex explicitly rejected it. Read the reason. No second task was created. |
| **Acceptance unknown** | The acknowledgement was lost, invalid or overtaken by task completion/stop. Codex may already have used the input. Inspect the result and repository before deciding to resend. |

Up to **eight** follow-ups can wait/in flight, each up to **64 KiB**. Extra messages are explicitly rejected. One worker sends them in order. After a follow-up fails, further input to that task is refused and unsent messages are discarded with notices; this prevents later instructions overtaking a failed one. A steer-only timeout leaves healthy original work running. `!status` and `!stop` remain responsive while Codex acknowledges input.

Follow-ups keep the **original task deadline**, including time spent waiting during startup or for a person. They do not reset a `!run 30m` timer, switch approval modes or change repositories. `!run` and session/repository/mode changes remain idle-only. Additional channels retain their own conversations; a busy message there cannot steer another channel's task and is explicitly not submitted.

Ordinary text does not click an approval button or fill a structured Codex question. Continue using Approve/Deny, `!approve ID`, `!deny ID` or `!answer ID ...` for those requests. To answer multiple questions, use a JSON mapping of question IDs to answer lists:

```text
!answer REQUEST_ID {"storage":["SQLite"],"tests":["Unit tests","Integration tests"]}
```

Replace the example IDs with the displayed request/question IDs. Approval settings and sandbox restrictions remain in effect.

## Readable Discord output

- **`!help`** groups chat controls, time limits, approvals and workspace commands under headings.
- **`!status`** combines this channel's selection and the task summary, including last observed activity, pending requests, follow-up counts and running version.
- **`!status full`** adds preparation/RPC timeouts, task ID and metadata for interrupted follow-ups.
- **Task, result and request headings** distinguish receipts and human decisions from coding output. Green Approve and red Deny buttons still follow the complete action/diff details.
- **Small labels** keep session names and delivery markers less prominent. The marker identifies pages and their content for delivery recovery. If a release changes the layout or a session is renamed after partial delivery, recovery sends a complete new copy rather than skipping text against old page boundaries. You may see repeated earlier pages in that case.
- **Long answers and code** continue across inline messages. Nothing is hidden behind a download or a “show more” button, and code fences reopen on the next page.

Discord supports [headings, code blocks and subtext](https://support.discord.com/hc/en-us/articles/210298617-Markdown-Text-101-Chat-Formatting-Bold-Italic-Underline). This release uses those native features, plus the existing approval buttons; it does not add a separate graphical application or dashboard. It does not require Embed Links, Attach Files, a public endpoint or extra intents. A client that does not render a formatting feature still receives the full text.

Discord limits ordinary bot message content to [2,000 characters](https://docs.discord.com/developers/resources/message#create-message). The bridge reserves space for labels and counts UTF-16 units conservatively. Large results take more time under Discord's rate limits, and urgent controls/status can appear between result pages. The bot cannot make an arbitrarily large reply fit on one phone screen without scrolling. Formatting improves scanning; it does not shorten or remove the agent's answer.

## Interrupted input and recovery

The bridge never repeats an uncertain coding message just to recover a lost acknowledgement. On stop, completion or failure, unsent input is discarded. On a process restart, active input is marked interrupted; there is no replay queue. `!status full` shows the most recent 32 follow-up metadata records from an interrupted task. `waiting` means not confirmed sent; `sending` means acceptance is unknown. Bridge state/logs do not retain these prompt bodies; refer to your original Discord messages by their IDs.

If the original task completes, `!last` includes any follow-up delivery problems with the saved result, even if its Discord delivery failed. If the task itself was interrupted, `!last` is still the previous completed result; inspect `!status full` and repository changes before continuing. A quiet log or typing indicator alone does not establish a hang.

For startup/protocol errors use the [troubleshooting guide](troubleshooting.md). Test the feature in a [disposable walkthrough session](walkthrough.md#add-instructions-while-a-task-is-active--in-discord) before relying on it for important work. Non-model protocol and simulated lifecycle tests do not establish live model responsiveness or mobile rendering.
