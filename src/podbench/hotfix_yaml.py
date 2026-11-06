"""Round-trip YAML editing; comments identify ownership, nodes identify structure."""

from __future__ import annotations

import re
from copy import deepcopy
from io import StringIO

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import MappingNode, SequenceNode
from ruamel.yaml.tokens import CommentToken

from .hotfix_core import HotfixError

MARKER = re.compile(r"# podbench: (begin|end) ([\w.]+)$")

LISTS = ("volumes", "volumeMounts")


def yaml():
    parser = YAML()
    parser.preserve_quotes = True
    parser.indent(mapping=2, sequence=4, offset=2)
    parser.width = 4096
    return parser


def load(text: str) -> CommentedMap:
    try:
        document = yaml().load(text)
    except YAMLError as error:
        raise HotfixError(f"cannot edit YAML: {error}") from error
    if document is None:
        return CommentedMap()
    if not isinstance(document, CommentedMap):
        raise HotfixError("expected a YAML mapping")
    return document


def dump(document: CommentedMap) -> str:
    stream = StringIO()
    yaml().dump(document, stream)
    return stream.getvalue()


def mapping(document: CommentedMap, key: str) -> CommentedMap:
    if document.get(key) is None:
        document[key] = CommentedMap()
    if not isinstance(document[key], CommentedMap):
        raise HotfixError(f"{key} must be a YAML mapping")
    return document[key]


def detached(value):
    """Edit this workload without mutating aliases elsewhere in the document."""
    result, visited = deepcopy(value), set()

    def clear(node):
        if isinstance(node, (CommentedMap, CommentedSeq)) and id(node) not in visited:
            visited.add(id(node))
            node.yaml_set_anchor(None)
            children = (
                [*node.values(), *node.merge]
                if isinstance(node, CommentedMap)
                else node
            )
            for child in children:
                clear(child)

    clear(result)
    return result


def _comments(value):
    if isinstance(value, CommentToken):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _comments(item)


def _spans(text):
    """Read actual YAML comments, excluding marker-like text in shell scalars."""
    lines, comments = text.splitlines(), set()
    for token in yaml().scan(text):
        for comment in _comments(token.comment):
            comments.update(
                range(
                    comment.start_mark.line,
                    comment.start_mark.line
                    + len(comment.value.lstrip("\n").splitlines()),
                )
            )
    opened = None
    for index in sorted(comments):
        line = lines[index].strip()
        match = MARKER.fullmatch(line)
        if not match:
            continue
        action = match[1]
        label = match[2]
        if action == "begin":
            if opened:
                raise HotfixError("nested Podbench ownership markers")
            opened = (index, label)
        else:
            if opened is None or opened[1] != label:
                raise HotfixError("unmatched Podbench ownership end marker")
            yield opened[0], index, label
            opened = None
    if opened:
        raise HotfixError("unclosed Podbench ownership begin marker")


def _field(node, path):
    key = None
    for part in path:
        if not isinstance(node, MappingNode):
            raise HotfixError(f"ownership marker does not identify a mapping: {part}")
        pair = next(((k, v) for k, v in node.value if k.value == part), None)
        if pair is None:
            raise HotfixError(f"ownership marker identifies missing field: {part}")
        key, node = pair
    return key, node


def _end(node):
    """First line after a node's content, excluding following service comments."""
    if (
        isinstance(node, (MappingNode, SequenceNode))
        and node.value
        and not node.flow_style
    ):
        return _end(
            node.value[-1][1] if isinstance(node, MappingNode) else node.value[-1]
        )
    return node.end_mark.line + bool(node.end_mark.column)


def unmark(text: str, prefix: str | None) -> tuple[str, list[str]]:
    """Remove explicitly owned content in this workload; report removed labels."""
    tree = yaml().compose(text)
    spans = list(_spans(text))
    if tree is None:
        return text, []
    path = [prefix] if prefix else []
    if prefix and prefix not in load(text):
        return text, []
    removed = []
    scope_key, scope = _field(tree, path)
    lower = scope_key.start_mark.line if scope_key else 0
    lines = text.splitlines(keepends=True)
    for begin, end, label in reversed(spans):
        if not (lower <= begin < end <= scope.end_mark.line):
            continue
        key, node = _field(tree, [*path, *label.split(".")])
        assert key is not None
        if label in LISTS:
            valid = isinstance(node, SequenceNode) and key.start_mark.line < begin
            owned = (
                [item for item in node.value if begin < item.start_mark.line < end]
                if valid
                else []
            )
            valid = valid and owned and all(_end(item) <= end for item in owned)
            valid = valid and end <= node.end_mark.line
            first = owned[0].start_mark.line if owned else begin
            last = _end(owned[-1]) if owned else end
        else:
            valid = (
                begin < key.start_mark.line
                and _end(node) <= end <= node.end_mark.line + 1
            )
            first, last = key.start_mark.line, _end(node)
        valid = valid and all(
            not line.strip() or line.lstrip().startswith("#")
            for line in lines[begin + 1 : first] + lines[last:end]
        )
        if not valid:
            raise HotfixError(
                f"ownership markers must surround complete {label} entries"
            )
        del lines[begin : end + 1]
        removed.append(label)
    return "".join(lines), removed


def prune(target: CommentedMap, labels: list[str]) -> None:
    """Drop keys left empty by unmark, so null does not delete chart defaults."""
    for label in labels:
        parts = label.split(".")
        for depth in range(len(parts), 0, -1):
            parent = target
            for part in parts[: depth - 1]:
                parent = parent.get(part) if isinstance(parent, CommentedMap) else None
            key = parts[depth - 1]
            if not isinstance(parent, CommentedMap) or key not in parent:
                continue
            if parent[key] is None or parent[key] == {}:
                del parent[key]
            else:
                break


def mark(text: str, prefix: str | None, owned: list[tuple[str, int | None]]) -> str:
    """Insert ownership comments at parsed node boundaries in rendered YAML."""
    tree, insertions = yaml().compose(text), {}
    path = [prefix] if prefix else []
    for label, first in owned:
        key, node = _field(tree, [*path, *label.split(".")])
        assert key is not None
        begin, indent = key.start_mark.line, key.start_mark.column
        if first is not None:
            begin = node.value[first].start_mark.line
            indent = node.value[first].start_mark.column - 2
        insertions.setdefault(begin, []).append(
            " " * indent + f"# podbench: begin {label}\n"
        )
        insertions.setdefault(_end(node), []).insert(
            0, " " * indent + f"# podbench: end {label}\n"
        )
    lines = text.splitlines(keepends=True)
    for index in sorted(insertions, reverse=True):
        lines[index:index] = insertions[index]
    return "".join(lines)


def merge(target: CommentedMap, patch: CommentedMap, owned: list, path="") -> None:
    target.fa.set_block_style()
    for key, value in patch.items():
        label = f"{path}.{key}" if path else key
        if label in LISTS:
            existing = target.get(key)
            if existing is None:
                existing = target[key] = CommentedSeq()
            if not isinstance(existing, CommentedSeq) or not isinstance(
                value, CommentedSeq
            ):
                raise HotfixError(f"{label} must be a YAML list")
            existing = target[key] = detached(existing)
            names = [
                item.get("name") if isinstance(item, dict) else None
                for item in existing
            ]
            if None in names or len(set(names)) != len(names):
                raise HotfixError(f"{label} entries must have unique names")
            for item in value:
                if item["name"] in names:
                    raise HotfixError(
                        f"unmarked {label} entry {item['name']!r} "
                        "conflicts with Podbench"
                    )
            if value:
                owned.append((label, len(existing)))
                existing.extend(value)
                existing.fa.set_block_style()
        elif isinstance(value, CommentedMap):
            target[key] = detached(mapping(target, key))
            merge(target[key], value, owned, label)
        else:
            target[key] = value
            owned.append((label, None))
        target.move_to_end(key)
