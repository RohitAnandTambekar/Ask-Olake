"""Test doubles."""


class ScriptedLLM:
    """Stands in for a real model: streams a fixed reply, or raises."""

    def __init__(self, reply: str = "", fail: Exception | None = None):
        self.reply = reply
        self.fail = fail
        self.name = "scripted"
        self.calls: list[list[dict]] = []

    def stream(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise self.fail
        for i in range(0, len(self.reply), 7):  # stream in small pieces, like a real model
            yield self.reply[i : i + 7]
