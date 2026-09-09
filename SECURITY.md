# Security policy and limitations

This independent community bridge gives its configured owner access to a real coding agent. It is not a hardened multi-user sandbox, confidential workspace boundary, or official OpenAI/Discord product. Version 0.1.x receives fixes on the maintained main branch; no other release series is currently supported.

## Boundaries

- One numeric owner/server/channel is authorized; DMs, other users and other channels are ignored. Buttons additionally validate request, control message, task, turn, delivery and expiry. A compromised owner account can issue coding commands. Protect Discord with MFA and a private channel.
- Codex executes locally as the bridge's Linux account. Workspace-write limits are **not** a confidential-read boundary around a repository. Other files readable by that account, Git credentials, installed tools, MCP servers and configured plugins may be accessible. Use a dedicated Linux account or host for stronger separation; do not put highly sensitive material on an agent host merely because the selected repository is separate.
- Sandbox exceptions can authorize more access. Both human and automatic-review decisions can be consequential. Auto-review retains the workspace sandbox and managed restrictions; it is not a guarantee that actions are harmless. The bridge never silently grants all requests or switches to full access.
- The owner, Codex model, repository instructions and external content can influence tool execution. Untrusted repository content can contain prompt injection. Review commands, complete attachments and diffs; use disposable branches/repositories and appropriate backups.
- Ordinary permitted workspace edits may happen without a manual prompt. Stopping a task cannot undo edits, network effects or changes already made. A process that intentionally detaches itself can escape a process group outside systemd; the service cgroup provides additional cleanup, not a complete containment boundary.
- Results and request details are sent through Discord and model/context data through Codex's service. Avoid secrets in prompts and output. Server administrators can access server configuration/channel permissions. Outgoing mentions are disabled but text may still contain untrusted links or formatting.
- Tokens/config/state are kept outside the coding repository, mode 600/700. Discord variables are removed from the Codex child environment, but same-account file/proc access is still a concern. Never commit auth caches, logs, transcripts or state. Private backups also contain sensitive data.

## Report a vulnerability

Do not disclose live credentials or exploitable private details in a public issue. Use the repository's **Security → Report a vulnerability** option if private reporting is enabled. If that option is unavailable, open a minimal issue asking the maintainer for a private reporting channel without including exploit details or secrets. There is no guaranteed response SLA or paid security program.

If a token leaks, reset it in Developer Portal → Bot, replace private config locally and restart. Revoke/rotate other affected credentials through their provider. Review repository and external effects; deleting a message or Git commit does not revoke a leaked credential.
