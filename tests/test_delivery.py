from discord_coding_agent.delivery import Delivery, payloads
from discord_coding_agent.engine import Pending


class Transport:
    def __init__(self):
        self.messages = {}
        self.sends = 0
        self.ambiguous = False
        self.typing_error = False
        self.disabled = []
        self.controls = []

    async def find(self, marker):
        return self.messages.get(marker)

    async def send(self, text, data, marker, pending):
        self.sends += 1
        self.messages[marker] = self.sends
        self.controls.append(marker.endswith(":control]"))
        if self.ambiguous:
            raise TimeoutError()
        return self.sends

    async def disable(self, message_id):
        self.disabled.append(message_id)

    async def typing(self):
        if self.typing_error:
            raise TimeoutError()


def test_long_unicode_complete_attachments():
    content = "😀```python\n" * 100000
    parts = payloads(content)
    assert b"".join(data for _, data in parts if data).decode() == content
    assert all(len(data) <= 512 * 1024 for _, data in parts if data)
    assert payloads("😀" * 1000)[0][1] is not None


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
