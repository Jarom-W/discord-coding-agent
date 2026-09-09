# Codex 0.153.4 schema evidence

These JSON schemas are selected, unmodified outputs of the installed **codex-cli 0.153.4** command:

```text
codex app-server generate-json-schema --experimental --out DIR
```

`manifest.json` records each schema's SHA-256 and collection date. They contain no live prompts, credentials or repository content. The tests validate exact enums and approval payloads against them. They are not a permissive substitute for the actual-process checks in `test_codex_installed.py`.

Generated from OpenAI Codex, distributed under the upstream Apache License 2.0 in `LICENSE.codex`; see https://github.com/openai/codex/tree/rust-v0.153.4. Bridge source and tests written for this project are MIT licensed.
