"""Building blocks shared by dialects: slots, prepared requests, policy, errors (architecture §6).

Dialects translate one provider's JSON payloads into slots and back. They know nothing about
HTTP and perform no I/O.
"""

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Final, Literal, Protocol

from aliasgate.dialects.sse import SSEEvent
from aliasgate.engine import restore
from aliasgate.mapping import Mapping

# A content kind is echoed in error messages and may reach logs, so it must be a short
# label, never free text taken from the request.
_KIND: Final = re.compile(r"[a-z][a-z0-9_]{0,39}")
UNKNOWN_KIND: Final = "unknown"


@dataclass(frozen=True, slots=True)
class Policy:
    """How a dialect treats uninspectable content, end-user identifiers and tool definitions."""

    unsupported_content: Literal["block", "pass"] = "block"
    strip_user_identifiers: bool = True
    tool_definitions: bool = True


@dataclass(slots=True)
class Slot:
    """One string inside a payload: `get` reads its current value, `set` replaces it."""

    get: Callable[[], str]
    set: Callable[[str], None]


class PreparedRequest:
    """The ordered slots of one request body, and the finalizers that complete writing it."""

    __slots__ = ("_finalizers", "_slots")

    def __init__(self, slots: list[Slot], finalizers: list[Callable[[], None]]) -> None:
        self._slots = list(slots)
        self._finalizers = list(finalizers)

    def texts(self) -> list[str]:
        """The current value of every slot, in slot order."""
        return [slot.get() for slot in self._slots]

    def apply(self, new_texts: Sequence[str]) -> None:
        """Set every slot in order, then run finalizers. Raise ValueError on length mismatch.

        Nothing is written unless `new_texts` holds exactly one string per slot.
        """
        if isinstance(new_texts, str):
            raise TypeError("new_texts must be a sequence of strings, not a single string")
        if len(new_texts) != len(self._slots):
            raise ValueError(f"expected {len(self._slots)} texts, got {len(new_texts)}")
        if not all(isinstance(text, str) for text in new_texts):
            raise TypeError("new_texts must contain only strings")
        for slot, text in zip(self._slots, new_texts, strict=True):
            slot.set(text)
        for finalize in self._finalizers:
            finalize()


class UnsupportedContentError(Exception):
    """Content the gateway cannot inspect. `kind` is a short label such as "image_url".

    A kind that does not look like a label (lower-case ASCII letters, digits and underscores,
    at most 40 characters) is replaced by "unknown", so the error never carries request text.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind if _KIND.fullmatch(kind) else UNKNOWN_KIND
        super().__init__(f"unsupported content: {self.kind}")


class MalformedRequestError(Exception):
    """The body does not have the shape this dialect requires. Message has no user text."""


class StreamTransformer(Protocol):
    """Restores one streamed response, event by event, never emitting text it still holds.

    `unresolved` is the running count of placeholders left unrestored in emitted text.
    """

    unresolved: int

    def feed(self, event: SSEEvent) -> list[SSEEvent]:
        """The events to send in place of `event`, in order."""
        ...

    def end(self) -> list[SSEEvent]:
        """Events carrying any held text; called when the upstream closes without [DONE]."""
        ...


class Dialect(Protocol):
    """Translates one provider API into slots and back; contains no HTTP and does no I/O."""

    name: str

    def prepare_request(self, body: dict[str, Any], policy: Policy) -> PreparedRequest: ...

    def restore_response(self, body: dict[str, Any], mapping: Mapping) -> None: ...

    def stream_transformer(self, mapping: Mapping) -> StreamTransformer: ...

    def error_body(self, status: int, code: str, message: str) -> dict[str, Any]: ...


def _string_value(value: object) -> str:
    if not isinstance(value, str):
        # The value is not echoed: it is request content.
        raise TypeError(f"slot value is a {type(value).__name__}, not a string")
    return value


def key_slot(obj: dict[str, Any], key: str) -> Slot:
    """A slot on `obj[key]`; reading it raises TypeError if the value is not a string."""

    def set_value(value: str) -> None:
        obj[key] = value

    return Slot(lambda: _string_value(obj[key]), set_value)


def index_slot(arr: list[Any], index: int) -> Slot:
    """A slot on `arr[index]`; reading it raises TypeError if the item is not a string."""

    def set_value(value: str) -> None:
        arr[index] = value

    return Slot(lambda: _string_value(arr[index]), set_value)


type _Child = tuple[Any, Callable[[], Slot], str | None]


def _children(container: dict[str, Any] | list[Any]) -> Iterator[_Child]:
    if isinstance(container, dict):
        for key in list(container):
            yield container[key], partial(key_slot, container, key), key
    else:
        for index, item in enumerate(container):
            yield item, partial(index_slot, container, index), None


def _string_slots(container: dict[str, Any] | list[Any]) -> Iterator[tuple[Slot, str | None]]:
    """Every string value as a slot, with the dict key holding it (None for a list item).

    Depth-first, dicts in insertion order, lists in index order. Iterative, so deeply nested
    input cannot exhaust the interpreter stack.
    """
    stack = [_children(container)]
    while stack:
        child = next(stack[-1], None)
        if child is None:
            stack.pop()
            continue
        value, make_slot, key = child
        if isinstance(value, str):
            yield make_slot(), key
        elif isinstance(value, dict | list):
            stack.append(_children(value))


def slots_for_json_strings(container: dict[str, Any] | list[Any]) -> list[Slot]:
    """A slot for every string VALUE reachable in the container, depth-first, dicts in insertion
    order, lists in index order. Keys are never slots."""
    return [slot for slot, _ in _string_slots(container)]


def slots_for_key_strings(container: dict[str, Any] | list[Any], key: str) -> list[Slot]:
    """A slot for every string stored directly under `key` in any dict reachable from the
    container, in the order of `slots_for_json_strings`. Other strings are never slots."""
    return [slot for slot, holder in _string_slots(container) if holder == key]


def restore_all_strings(value: Any, mapping: Mapping) -> Any:
    """Return a copy of a JSON value with `restore` applied to every string value."""
    if isinstance(value, str):
        return restore(value, mapping)
    if isinstance(value, dict):
        return {key: restore_all_strings(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [restore_all_strings(item, mapping) for item in value]
    return value
