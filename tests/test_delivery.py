import codecs

import pytest

from discord_coding_agent.delivery import INLINE_UNITS, MAX_INLINE_PAGES, Delivery, payloads
from discord_coding_agent.engine import Pending
from discord_coding_agent.errors import BridgeError


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

    async def send(self, text, data, marker, pending):
        self.sends += 1
        self.messages[marker] = self.sends
        self.controls.append(marker.endswith(":control]"))
        self.payloads.append((text, data, marker))
        if self.ambiguous:
            raise TimeoutError()
        return self.sends

    async def disable(self, message_id):
        self.disabled.append(message_id)

    async def typing(self):
        if self.typing_error:
            raise TimeoutError()


def test_long_unicode_complete_attachments():
    content = "😀```python\n— café 中文\n" * 100000
    parts = payloads(content)
    assert "".join(data.decode("utf-8-sig") for _, data in parts if data) == content
    assert all(data.startswith(codecs.BOM_UTF8) for _, data in parts)
    assert all(len(data) <= 512 * 1024 for _, data in parts if data)
    assert payloads("😀" * 1000)[0][1] is not None


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
    parts = payloads(content, inline=True)
    assert len(parts) > 1
    assert "".join(text for text, _ in parts) == content
    for text, data in parts:
        assert data is None
        assert len(text.encode("utf-16-le")) // 2 <= INLINE_UNITS
        packet = f"{text}\n[dca:{'a' * 32}:8/8]"
        assert len(packet.encode("utf-16-le")) // 2 <= 2000


def test_inline_help_prefers_complete_lines_and_has_a_page_bound():
    line = "!command — Unicode and complete instructions\n"
    parts = payloads(line * 100, inline=True)
    assert all(text.endswith("\n") for text, _ in parts)
    assert payloads("", inline=True) == [("(empty result)", None)]
    with pytest.raises(BridgeError, match="eight pages"):
        payloads("x" * (INLINE_UNITS * MAX_INLINE_PAGES + 1), inline=True)


async def test_inline_pages_reconcile_individually_and_complete_in_order():
    t = Transport()
    t.ambiguous = True
    delivered = []
    content = "!help — one command per line\n" * 100
    d = Delivery(t, 0.03, lambda _: True, lambda *_: True, delivered.append)
    d.start()
    try:
        d.text(content, result_id="help-response", inline=True)
        await d.queue.join()
    finally:
        await d.close()
    assert len(t.payloads) > 1 and d.failures == 0
    assert "".join(text for text, _, _ in t.payloads) == content
    assert all(data is None for _, data, _ in t.payloads)
    assert len({marker for _, _, marker in t.payloads}) == t.sends
    assert delivered == ["help-response"]


async def test_ambiguous_send_reconciled_without_duplicate():
    t = Transport()
    t.ambiguous = True
    d = Delivery(t, 0.03, lambda _: True, lambda *_: True, lambda _: None)
    await d.send_part("result", None, "marker", None)
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
    assert t.controls == [False, True] and bound == [("req", 2)]
    d = Delivery(t, 0.03, lambda _: False, lambda *_: False, lambda _: None)
    d.start()
    d.request(p)
    await d.queue.join()
    await d.close()
    assert t.sends == 2


def test_queue_is_bounded():
    d = Delivery(Transport(), 0.03, lambda _: True, lambda *_: True, lambda _: None)
    for i in range(100):
        d.text(str(i))
    assert d.queue.qsize() == 32 and d.failures == 68


async def test_delivery_failure_does_not_erase_result(engine, owner):
    from conftest import begin, complete

    e, _, _ = engine
    await begin(e, owner)
    complete(e, "persist me")
    await e.worker

    class Failing(Transport):
        async def find(self, marker):
            raise OSError("disconnected")

    d = Delivery(Failing(), 0.01, lambda _: True, lambda *_: True, lambda _: None)
    d.start()
    d.text(e.state.last_result, result_id=e.state.result_id)
    await d.queue.join()
    await d.close()
    assert e.store.load().last_result == "persist me"
    assert not e.store.load().delivered and d.failures == 1
