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
    """How a dialect treats uninspectable content, end-user identifiers and location, and tool
    definitions."""

    unsupported_content: Literal["block", "pass"] = "block"
    strip_user_identifiers: bool = True
    strip_user_location: bool = True
    tool_definitions: bool = True


@dataclass(slots=True)
class Slot:
    """One string inside a payload: `get` reads its current value, `set` replaces it.

    A label is a short value written as structure rather than prose, such as an object key.
    """

    get: Callable[[], str]
    set: Callable[[str], None]
    label: bool = False


class PreparedRequest:
    """The ordered slots of one request body, and the finalizers that complete writing it."""

    __slots__ = ("_finalizers", "_slots")

    def __init__(self, slots: list[Slot], finalizers: list[Callable[[], None]]) -> None:
        self._slots = list(slots)
        self._finalizers = list(finalizers)

    def texts(self) -> list[str]:
        """The current value of every slot, in slot order."""
        return [slot.get() for slot in self._slots]

    def labels(self) -> frozenset[int]:
        """The indexes of the label slots, in slot order; contextual detectors skip them."""
        return frozenset(index for index, slot in enumerate(self._slots) if slot.label)

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


def slots_for_string_list(items: list[Any], where: str) -> list[Slot]:
    """A slot per item of `items`, in order. Raises MalformedRequestError naming `where[i]`, and
    nothing of the value, when an item is not a string."""
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise MalformedRequestError(f"{where}[{index}]: expected a string")
    return [index_slot(items, index) for index in range(len(items))]


class _Keys:
    """The keys of one object: as stored in it, and as its key slots last set them."""

    __slots__ = ("names", "obj", "stored", "where")

    def __init__(self, obj: dict[str, Any], where: str) -> None:
        self.obj = obj
        self.where = where
        self.stored = list(obj)
        self.names = list(self.stored)

    def key_slot(self, index: int) -> Slot:
        def set_name(value: str) -> None:
            self.names[index] = value

        return Slot(lambda: _string_value(self.names[index]), set_name, label=True)

    def value_slot(self, index: int) -> Slot:
        def set_value(value: str) -> None:
            self.obj[self.stored[index]] = value

        return Slot(lambda: _string_value(self.obj[self.stored[index]]), set_value)

    def rename(self) -> None:
        """Rename the object's keys in place, in order; never merge two of them."""
        if self.names == self.stored:
            return
        if len(set(self.names)) != len(self.names):
            # Neither key is named: both are request content.
            raise MalformedRequestError(
                f"{self.where}: two keys of one object would become the same"
            )
        values = [self.obj[key] for key in self.stored]
        self.obj.clear()
        self.obj.update(zip(self.names, values, strict=True))
        self.stored = list(self.names)


type _Step = Slot | dict[str, Any] | list[Any]


def _object_steps(
    obj: dict[str, Any], finalizers: list[Callable[[], None]], where: str, skip_under: str | None
) -> Iterator[_Step]:
    keys = _Keys(obj, where)
    finalizers.append(keys.rename)
    for index, key in enumerate(keys.stored):
        yield keys.key_slot(index)
        value = obj[key]
        if isinstance(value, str):
            if key != skip_under:
                yield keys.value_slot(index)
        elif isinstance(value, dict | list):
            yield value


def _list_steps(arr: list[Any]) -> Iterator[_Step]:
    for index, item in enumerate(arr):
        if isinstance(item, str):
            yield index_slot(arr, index)
        elif isinstance(item, dict | list):
            yield item


def slots_for_keys_and_strings(
    container: dict[str, Any] | list[Any],
    finalizers: list[Callable[[], None]],
    where: str,
    *,
    skip_under: str | None = None,
) -> list[Slot]:
    """A slot for every key and every string value reachable in the container, each key right
    before its value, depth-first, dicts in insertion order, lists in index order.

    Key slots are labels. A string stored directly under the key `skip_under` is not a slot,
    but its key is. For every object reached, a step that renames its keys in place, keeping
    their order, is appended to `finalizers`; it raises MalformedRequestError naming `where`,
    and no key, when two keys of the object would become the same, and then changes nothing
    in that object. Iterative, so deeply nested input cannot exhaust the interpreter stack.
    """

    def steps(value: dict[str, Any] | list[Any]) -> Iterator[_Step]:
        if isinstance(value, dict):
            return _object_steps(value, finalizers, where, skip_under)
        return _list_steps(value)

    slots: list[Slot] = []
    stack = [steps(container)]
    while stack:
        step = next(stack[-1], None)
        if step is None:
            stack.pop()
        elif isinstance(step, Slot):
            slots.append(step)
        else:
            stack.append(steps(step))
    return slots


def restore_all_strings(value: Any, mapping: Mapping) -> Any:
    """Return a copy of a JSON value with `restore` applied to every string value and every
    key, keeping the key order.

    Keys are never merged: a key is left as it is when its restored form is already a key of
    the same object, or the restored form of an earlier key.
    """
    if isinstance(value, str):
        return restore(value, mapping)
    if isinstance(value, dict):
        restored: dict[Any, Any] = {}
        for key, item in value.items():
            new_key = restore(key, mapping) if isinstance(key, str) else key
            if new_key != key and (new_key in value or new_key in restored):
                new_key = key
            restored[new_key] = restore_all_strings(item, mapping)
        return restored
    if isinstance(value, list):
        return [restore_all_strings(item, mapping) for item in value]
    return value
