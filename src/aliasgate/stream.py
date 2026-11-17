"""Restoration of text that arrives in chunks (architecture §5.4)."""

from aliasgate.engine import count_unresolved, restore
from aliasgate.mapping import Mapping
from aliasgate.placeholders import MAX_TOLERANT_LENGTH


def _hold_position(buffer: str) -> int:
    """Where the part of `buffer` that could still grow into a placeholder starts."""
    i = buffer.rfind("[")
    if i != -1 and "]" not in buffer[i:] and len(buffer) - i <= MAX_TOLERANT_LENGTH:
        return i - 1 if i > 0 and buffer[i - 1] == "\\" else i
    if buffer.endswith("\\"):
        return len(buffer) - 1
    return len(buffer)


class StreamRestorer:
    """Restores placeholders in streamed text, emitting everything that is safe as early as
    possible and holding back only a tail that could still become a placeholder.

    For any text and any way of cutting it into chunks, the concatenation of every `feed`
    result and `flush` equals `restore(text)`, and nothing emitted is ever retracted.
    """

    __slots__ = ("_buffer", "_json_escape", "_mapping", "unresolved")

    def __init__(self, mapping: Mapping, *, json_escape: bool = False) -> None:
        self._mapping = mapping
        self._json_escape = json_escape
        self._buffer = ""
        self.unresolved = 0  # tolerant matches left unrestored in emitted text

    def feed(self, chunk: str) -> str:
        """Add `chunk`; return the text that is safe to emit now (possibly "")."""
        self._buffer += chunk
        hold = _hold_position(self._buffer)
        ready, self._buffer = self._buffer[:hold], self._buffer[hold:]
        return self._restore(ready)

    def flush(self) -> str:
        """Return the held text, restored, and empty the buffer. Call at the end of a stream."""
        ready, self._buffer = self._buffer, ""
        return self._restore(ready)

    def _restore(self, text: str) -> str:
        if not text:
            return ""
        self.unresolved += count_unresolved(text, self._mapping)
        return restore(text, self._mapping, json_escape=self._json_escape)
