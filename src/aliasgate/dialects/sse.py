"""Server-sent events: parse a byte stream into events and encode events back.

Follows the event-stream format of the HTML Living Standard (section 9.2.5): `\\n`, `\\r\\n` and
`\\r` end lines, an empty line ends an event, lines starting with `:` are comments.
"""

import codecs
import re
from dataclasses import dataclass
from typing import Final

_LINE_END: Final = re.compile(r"\r\n|\r|\n")
_BOM: Final = "\ufeff"


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """One event: its `event` field, its `data` lines joined with "\\n", and any other fields
    (id, retry, ...) in order."""

    event: str | None
    data: str
    raw_fields: tuple[tuple[str, str], ...] = ()

    def encode(self) -> bytes:
        """The event in wire format: event, other fields, one `data:` line per line of data."""
        lines = [] if self.event is None else [f"event: {self.event}"]
        lines += [f"{name}: {value}" for name, value in self.raw_fields]
        lines += [f"data: {line}" for line in self.data.split("\n")]
        return ("\n".join(lines) + "\n\n").encode("utf-8")


class SSEParser:
    """Incremental parser: bytes in any chunking, events out as soon as they are complete.

    UTF-8 is decoded strictly and incrementally, so a character split across chunks is fine and
    invalid UTF-8 raises UnicodeDecodeError. Events with neither data nor an event name are not
    emitted. One instance per stream.
    """

    __slots__ = ("_after_cr", "_data", "_decoder", "_event", "_fields", "_pending", "_started")

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._pending = ""  # the current, incomplete line
        self._after_cr = False  # the last line ended with "\r": a leading "\n" belongs to it
        self._started = False
        self._event: str | None = None
        self._data: list[str] | None = None
        self._fields: list[tuple[str, str]] = []

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        """Parse `chunk`; return the events it completes."""
        return self._text(self._decoder.decode(chunk))

    def end(self) -> list[SSEEvent]:
        """Return a final event if the stream ended without a blank line; else []."""
        events = self._text(self._decoder.decode(b"", final=True))
        if self._pending:
            line, self._pending = self._pending, ""
            self._line(line)
        event = self._dispatch()
        return events if event is None else [*events, event]

    def _text(self, text: str) -> list[SSEEvent]:
        if not self._started and text:
            self._started = True
            text = text.removeprefix(_BOM)
        if self._after_cr and text:
            self._after_cr = False
            text = text.removeprefix("\n")
        buffer = self._pending + text
        events: list[SSEEvent] = []
        start = 0
        for match in _LINE_END.finditer(buffer):
            if match.group() == "\r" and match.end() == len(buffer):
                self._after_cr = True
            event = self._line(buffer[start : match.start()])
            if event is not None:
                events.append(event)
            start = match.end()
        self._pending = buffer[start:]
        return events

    def _line(self, line: str) -> SSEEvent | None:
        if not line:
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, colon, value = line.partition(":")
        if colon:
            value = value.removeprefix(" ")
        if name == "data":
            if self._data is None:
                self._data = []
            self._data.append(value)
        elif name == "event":
            self._event = value
        else:
            self._fields.append((name, value))
        return None

    def _dispatch(self) -> SSEEvent | None:
        event = None
        if self._data is not None or self._event is not None:
            event = SSEEvent(self._event, "\n".join(self._data or []), tuple(self._fields))
        self._event, self._data, self._fields = None, None, []
        return event
