import asyncio

import pytest

from discord_coding_agent.delivery import INLINE_UNITS, Delivery, payloads
from discord_coding_agent.engine import Pending


class Transport:
    def __init__(self):
        self.messages = {}
        self.sends = 0
        self.ambiguous = False
        self.typing_error = False
        self.disabled = []
        self.controls = []
        self.payloads = []

    async def find(self, marker):
        return self.messages.get(marker)

    async def send(self, text, marker, pending):
        self.sends += 1
        self.messages[marker] = self.sends
        self.controls.append(marker.endswith(":control]"))
        self.payloads.append((text, marker))
        if self.ambiguous:
            raise TimeoutError()
        return self.sends

    async def disable(self, message_id):
        self.disabled.append(message_id)

    async def typing(self):
        if self.typing_error:
            raise TimeoutError()


def test_large_replies_are_lazy_complete_inline_pages_without_a_page_cap():
    content = "😀 — café 中文\n" * 100000
    pages = payloads(content)
    assert iter(pages) is pages
    restored = []
    for page in pages:
        assert len(page.content.encode("utf-16-le")) // 2 <= INLINE_UNITS
        restored.append(page.text)
    assert len(restored) > 500  # No eight-page cap or file fallback.
    assert "".join(restored) == content


@pytest.mark.parametrize(
    "content",
    [
        "!help — café 中文 😀\n" * 150,
        "a" * 1799 + "😀" + "b" * 1800,
        "word " * 800,
        "😀" * 1800,
    ],
)
def test_inline_pages_preserve_unicode_and_discord_limit(content):
    parts = list(payloads(content))
    assert len(parts) > 1
    assert "".join(page.text for page in parts) == content
    for page in parts:
        assert len(page.content.encode("utf-16-le")) // 2 <= INLINE_UNITS
        packet = f"{page.content}\n-# [dca:{'a' * 32}:inline:{'b' * 16}:10000]"
        assert len(packet.encode("utf-16-le")) // 2 <= 2000


def test_pages_prefer_complete_lines_and_handle_empty_text():
    line = "!command — Unicode and complete instructions\n"
    parts = list(payloads(line * 100))
    assert all(page.text.endswith("\n") for page in parts)
    assert [page.content for page in payloads("")] == ["(empty result)"]


@pytest.mark.parametrize("fence", ["```", "~~~~", "  ```"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_code_blocks_close_and_reopen_without_changing_source(fence, newline):
    opener = fence + "python"
    source = (
        "Before"
        + newline
        + opener
        + newline
        + ("print('😀 café —')" + newline) * 300
        + fence
        + newline
        + "After"
    )
    parts = list(payloads(source))
    assert len(parts) > 3
    assert "".join(p.text for p in parts) == source
    assert parts[0].prefix == "" and parts[0].suffix == fence.strip()
    assert parts[-1].prefix == opener + "\n" and parts[-1].suffix == ""
    for page in parts[1:-1]:
        assert page.prefix == opener + "\n" and page.suffix == fence.strip()
        assert page.content.count(fence.strip()) == 2


def test_long_code_lines_and_unclosed_fences_remain_readable():
    source = "```js\n" + "😀" * 4000
    parts = list(payloads(source))
    assert "".join(p.text for p in parts) == source
    assert all(p.content.startswith("```js\n") for p in parts)
    assert all(p.content.endswith("\n```") for p in parts)
    assert all(len(p.content.encode("utf-16-le")) // 2 <= INLINE_UNITS for p in parts)


def test_inline_backticks_are_not_reopened_as_code_blocks():
    source = "Text with ```inline``` markers " * 300
    assert all(not p.prefix and not p.suffix for p in payloads(source))


async def test_inline_pages_reconcile_individually_and_complete_in_order():
    t = Transport()
    t.ambiguous = True
    delivered = []
    content = "!help — one command per line\n" * 100
    d = Delivery(t, 0.03, lambda _: True, lambda *_: True, delivered.append)
    d.start()
    try:
        d.text(content, result_id="help-response")
        await d.queue.join()
    finally:
        await d.close()
    assert len(t.payloads) > 1 and d.failures == 0
    assert "".join(text for text, _ in t.payloads) == content
    assert len({marker for _, marker in t.payloads}) == t.sends
    assert delivered == ["help-response"]


async def test_ambiguous_send_reconciled_without_duplicate():
    t = Transport()
    t.ambiguous = True
    d = Delivery(t, 0.03, lambda _: True, lambda *_: True, lambda _: None)
    await d.send_part("result", "marker", None)
    assert t.sends == 1


async def test_cosmetic_failure_cannot_cancel_task(engine, owner):
    from conftest import begin, complete

    e, _, _ = engine
    await begin(e, owner)
    t = Transport()
    t.typing_error = True
    d = Delivery(t, 0.01, lambda _: True, lambda *_: True, lambda _: None)
    await d.cosmetic()
    assert e.busy
    complete(e, "healthy result")
    await e.worker
    assert e.state.last_result == "healthy result" and not e.state.interrupted


async def test_controls_only_after_details_and_expired_delivery():
    t = Transport()
    bound = []
    d = Delivery(t, 0.03, lambda _: True, lambda *args: bound.append(args) or True, lambda _: None)
    p = Pending(
        "req", 1, "item/fileChange/requestApproval", {}, "task", "turn", details="+diff\n" * 1000
    )
    d.start()
    d.request(p)
    await d.queue.join()
    await d.close()
    assert len(t.controls) > 3 and t.controls == [False] * (t.sends - 1) + [True]
    assert (
        "".join(text for text, _ in t.payloads[:-1])
        == f"## Approval needed\nRequest: `req`\n{p.method}\n\n{p.details}"
    )
    assert bound == [("req", t.sends)]
    before = t.sends
    d = Delivery(t, 0.03, lambda _: False, lambda *_: False, lambda _: None)
    d.start()
    d.request(p)
    await d.queue.join()
    await d.close()
    assert t.sends == before


def test_queue_is_bounded():
    d = Delivery(Transport(), 0.03, lambda _: True, lambda *_: True, lambda _: None)
    for i in range(100):
        d.text(str(i))
    assert d.queue.qsize() == 32 and d.failures == 68


async def test_delivery_failure_does_not_erase_result(engine, owner):
    from conftest import begin, complete

    e, _, rpc = engine
    await begin(e, owner)
    content = "persist me — 😀\n" * 1000
    complete(e, content)
    await e.worker
    calls_before = list(rpc.calls)

    class Failing(Transport):
        async def find(self, marker):
            if self.sends == 2:
                raise OSError("disconnected")
            return await super().find(marker)

    failed = Failing()
    d = Delivery(failed, 0.1, lambda _: True, lambda *_: True, lambda _: None)
    d.start()
    d.text(e.state.last_result, result_id=e.state.result_id)
    await d.queue.join()
    await d.close()
    assert failed.sends == 2
    assert e.store.load().last_result == content
    assert not e.store.load().delivered and d.failures == 1
    recovered = Transport()
    d = Delivery(recovered, 0.1, lambda _: True, lambda *_: True, lambda _: None)
    e.sink = d
    d.start()
    try:
        await e.command(owner, "!last")
        await d.queue.join()
    finally:
        await d.close()
    assert "".join(text for text, _ in recovered.payloads) == content
    assert rpc.calls == calls_before


async def test_approval_and_status_can_interrupt_long_result_delivery():
    started, release = asyncio.Event(), asyncio.Event()

    class Paused(Transport):
        async def send(self, text, marker, pending):
            if self.sends == 0:
                started.set()
                await release.wait()
            return await super().send(text, marker, pending)

    t = Paused()
    completed = []
    d = Delivery(t, 1, lambda _: True, lambda *_: True, completed.append)
    d.start()
    content = "large result\n" * 1000
    try:
        d.text(content, result_id="large")
        await asyncio.wait_for(started.wait(), 1)
        d.text("status reply")
        d.request(
            Pending(
                "req", 1, "item/fileChange/requestApproval", {}, "task", "turn", details="+diff"
            )
        )
        d.disable(99)
        release.set()
        await asyncio.wait_for(d.queue.join(), 2)
    finally:
        await d.close()
    markers = [marker for _, marker in t.payloads]
    assert markers[0].startswith("[dca:large:inline:") and markers[0].endswith(":1]")
    assert markers[1].startswith("[dca:req:inline:") and markers[1].endswith(":1]")
    assert markers[2] == "[dca:req:control]"
    assert t.payloads[3][0] == "status reply"
    assert t.disabled == [99]
    assert (
        "".join(text for text, marker in t.payloads if marker.startswith("[dca:large:")) == content
    )
    assert completed == ["large"]


async def test_full_queue_keeps_room_for_inflight_results_next_page():
    started, release = asyncio.Event(), asyncio.Event()

    class Paused(Transport):
        async def send(self, text, marker, pending):
            if self.sends == 0:
                started.set()
                await release.wait()
            return await super().send(text, marker, pending)

    t = Paused()
    completed = []
    d = Delivery(t, 1, lambda _: True, lambda *_: True, completed.append)
    d.start()
    try:
        d.text("result\n" * 2000, result_id="large")
        await asyncio.wait_for(started.wait(), 1)
        for i in range(40):
            d.text(f"queued {i}")
        assert len(d.keys) == 32 and d.queue.qsize() == 31
        release.set()
        await asyncio.wait_for(d.queue.join(), 2)
    finally:
        await d.close()
    assert completed == ["large"] and not d.keys and d.failures == 9


async def test_expiration_midway_prevents_controls():
    t = Transport()
    d = Delivery(
        t, 1, lambda _: t.sends < 2, lambda *_: pytest.fail("expired request bound"), lambda _: None
    )
    d.start()
    try:
        d.request(
            Pending(
                "req",
                1,
                "item/fileChange/requestApproval",
                {},
                "task",
                "turn",
                details="+diff\n" * 2000,
            )
        )
        await d.queue.join()
    finally:
        await d.close()
    assert t.sends == 2 and not any(t.controls) and d.failures == 1


@pytest.mark.parametrize("legacy", [True, False])
async def test_changed_layout_recovers_complete_text_and_same_layout_reconciles(legacy):
    transport = Transport()
    body = "Full source — café 😀\n" * 300
    key = "saved-result"
    if legacy:
        transport.messages[f"[dca:{key}:inline:1]"] = 123  # Page sent by earlier layout.
    else:
        old = Delivery(transport, 1, lambda _: True, lambda *_: True, lambda _: None)
        old.start()
        old.text("Session: previous name\n" + body, result_id=key)
        await old.queue.join()
        await old.close()
        first = next(iter(transport.messages))
        transport.messages = {first: transport.messages[first]}  # Only a first page survived.
        transport.payloads.clear()
    content = "-# Session: renamed\n## Result\n" + body
    delivered = []
    recovery = Delivery(transport, 1, lambda _: True, lambda *_: True, delivered.append)
    recovery.start()
    try:
        recovery.text(content, result_id=key)
        await recovery.queue.join()
        assert "".join(text for text, _ in transport.payloads) == content
        assert delivered == [key]
        sends = transport.sends
        recovery.text(content, result_id=key)
        await recovery.queue.join()
        assert transport.sends == sends  # Unchanged content reconciles without resending.
    finally:
        await recovery.close()
