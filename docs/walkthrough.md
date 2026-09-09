# End-to-end acceptance walkthrough

This is a procedure for your own installation, not a claim that live Discord/model/Pi hardware tests have already passed. Use your configured owner account in the designated private channel. Wait for each result before sending the next ordinary message.

## Prepare a disposable repository — on the Pi

Before bridge setup, make a separate demo repository:

```bash
mkdir -p "$HOME/work/codex-bridge-demo"
cd "$HOME/work/codex-bridge-demo"
git init
printf '# Bridge demo\n' > README.md
printf 'def greet(name):\n    return f"Hello, {name}!"\n' > greet.py
touch .dca-disposable
git add README.md greet.py .dca-disposable
git commit -m 'Add disposable bridge demo'
git switch -c bridge-demo
```

Git requires your own configured author identity. Configure it yourself if Git prompts; the bridge does not invent an identity. Select this directory as `CODEX_REPO` in setup, with a new state directory if previously using another target.

## 1. Ping without a model

**On the Pi:** run `discord-coding-agent run --connection-only` using your virtual environment. **In Discord:** send `!ping`, then `!help`. Expect pong and command help. Stop the foreground process on the Pi with Ctrl+C, run `doctor --probe`, then `run` without `--connection-only`.

## 2. Read-only request and continuity — in Discord

Send:

```text
Read-only: inspect git status and greet.py. Do not change files. Remember the word compass for this conversation.
```

After the result, send:

```text
What word did I ask you to remember, and which file did you inspect?
```

Expect the earlier word and file. `!status` should show the same thread ID. “Read-only” here is an instruction for this particular prompt; the configured sandbox still permits workspace edits. Use a disposable repository when evaluating model behavior.

## 3. Small edit — in Discord, then verify on the Pi

Send:

```text
In this disposable repository, add a module docstring to greet.py. Preserve its behavior. Show the diff and run a small Python check of greet("Pi"). Do not commit or publish.
```

**On the Pi:** inspect `git diff` and `git status` in the demo repository. Expect only the requested local edit and no automatic commit. Manual review mode does not require buttons for ordinary permitted edits inside the sandbox.

## 4. Manual approval buttons — in Discord

Send `!new manual`, then `!approvals`. To exercise a real boundary request, ask:

```text
For this disposable approval test, request escalated sandbox permission to run the read-only command `id` in this repository. Show the proposed command before running it. Do not change files or credentials.
```

When Codex emits a request, verify the displayed working directory, command and complete details. Press red **Deny** first. A second click or text decision on the same ID should be rejected as expired/already decided. Ask for the harmless test again and approve using the green button or `!approve ID`. You must use the newly displayed ID.

The model or managed policy may refuse the test or run an already-permitted operation without requesting escalation; no buttons in that case is not proof of a broken bridge. Do not choose destructive actions just to force a prompt. Unit/fixture tests independently exercise the UI decision path; record actual model outcomes accurately.

If Codex asks multiple questions, copy the supplied question IDs into `!answer REQUEST_ID {"first_id":["answer"],"second_id":["answer"]}`. Do not answer secret-input questions in Discord; the bridge rejects that interface.

## 5. Automatic review — in Discord

Once idle, send `!new auto`. Submit another harmless test request, then use `!approvals`. The process config and effective thread response must verify `auto_review`, `on-request`, and workspace sandboxing before a turn is sent. Eligible escalations are decided by Codex; rejection, timeout or unavailability is reported when provided by the protocol. Residual human requests still appear.

If verification fails, inspect `doctor`/policy/version. `!new manual` is a deliberate fresh-session alternative only if manual is supported by your managed configuration. Auto mode never means every command is accepted and never disables sandboxing.

## 6. Busy handling and interruption — in Discord

Ask Codex to run `sleep 60` in the demo repository and report when it finishes. While active, send another ordinary message: expect **NOT submitted**. Send `!new`: expect rejection with instructions to wait or stop. Send `!status` and confirm the phase/last observed activity, then `!stop`.

The bridge first asks Codex to interrupt; if needed it terminates its owned process group within bounded shutdown stages. Completed edits or external effects remain. Inspect the repository before an explicit continuation. The previous request is never automatically resubmitted.

While idle, send `!run 10s In this disposable repository, run sleep 60 without editing files.` If Codex is still working after ten seconds, expect a full-task deadline error naming the 10-second limit and interruption cleanup. Initialization and human wait count toward this cap, so the command may not start before expiry. If Codex completes or fails sooner, record that outcome instead. Then send `!run unlimited Read-only: inspect git status and report.` The acceptance message and active `!status` should report no task limit; `!stop` and approvals remain effective. Subsequent ordinary text uses the configured default again. Do not interpret a short smoke test as proof of hour-long live uptime.

## 7. Service restart and saved conversation

Complete the [service setup](service.md). **In Discord:** start a new conversation and ask it to remember `lighthouse`. Wait for completion and note the thread ID. **On the Pi:**

```bash
systemctl --user restart discord-coding-agent.service
```

**In Discord:** use `!status`, `!last`, then ask which word it remembers. Expect the persisted thread ID/word. If restart interrupted a task, expect an interruption notice instead of automatic replay. Old buttons cannot approve new requests after restart; they are removed when possible or answer as expired.

## 8. Laptop-disconnect test

With the service enabled, running and linger configured, close your SSH connection and close/disconnect the laptop. Leave the Pi powered and connected. **On your phone in Discord:** send `!ping`, then a short repository question. A reply demonstrates the Pi service does not depend on the laptop session. It does not demonstrate offline inference: the Pi still needs internet.

## 9. Separate deliberate reboot test

Only after the disconnect test, and while no task is running, deliberately reboot the Pi through your normal administration method. Wait for it to boot and reconnect. Send `!ping` from the phone, then check `!status`. If absent, inspect boot service state, linger and Wi-Fi after reconnecting by SSH. Record this result separately from merely closing the laptop.
