"""Recipe references as paths and as a graph (#526). Pure: no database, no files.

A reference ``@./basics/simple-syrup{…}`` in ``drinks/daiquiri.cook`` names the
file ``drinks/basics/simple-syrup.cook``: the written path, relative to the
referring file's directory, normalized, with ``.cook`` added. One that
normalizes outside the recipes root (it starts with ``..`` or is absolute) names
nothing, and no file is ever opened for it.

Cycles are strongly connected components of the reference graph (Tarjan's
algorithm, iterative so a long chain never meets the recursion limit). The
components form a DAG, so costing that only follows edges out of a recipe's own
component always ends: the cycle check is the only guard, with no depth limit.
"""

from __future__ import annotations

import posixpath
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass


def target_path(referrer_path: str, ref_path: str) -> str | None:
    """The repository path a reference names, or None when it leaves the root."""
    written = ref_path.strip().removesuffix(".cook")
    if not written or written.startswith("/"):
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(referrer_path), written))
    if joined == ".." or joined.startswith("../") or joined.startswith("/") or joined == ".":
        return None
    return joined + ".cook"


def components[N: Hashable](nodes: Iterable[N], edges: Mapping[N, Iterable[N]]) -> list[list[N]]:
    """Strongly connected components, children first: a component comes after every
    component it has an edge to (Tarjan's order). Every node is in exactly one."""
    index: dict[N, int] = {}
    low: dict[N, int] = {}
    on_stack: set[N] = set()
    stack: list[N] = []
    found: list[list[N]] = []
    counter = 0
    order = list(dict.fromkeys([*nodes, *(n for targets in edges.values() for n in targets)]))
    for root in order:
        if root in index:
            continue
        work: list[tuple[N, Iterable[N]]] = [(root, iter(edges.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(edges.get(child, ()))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: list[N] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                found.append(component)
    return found


def cycles[N: Hashable](nodes: Iterable[N], edges: Mapping[N, Iterable[N]]) -> list[list[N]]:
    """The components that are cycles: two or more members, or one that names itself."""
    return [
        sorted(component, key=str)
        for component in components(nodes, edges)
        if len(component) > 1 or component[0] in set(edges.get(component[0], ()))
    ]


def chain[N: Hashable](cycle: Sequence[N], edges: Mapping[N, Iterable[N]], start: N) -> list[N]:
    """The shortest walk around a cycle from ``start`` back to it, for a message
    ("A → B → A"). ``[start]`` alone when ``start`` is not on a cycle."""
    members = set(cycle)
    previous: dict[N, N] = {}
    frontier = [start]
    seen: set[N] = set()
    while frontier:
        following: list[N] = []
        for node in frontier:
            for nxt in edges.get(node, ()):
                if nxt not in members:
                    continue
                if nxt == start:
                    path = [start]
                    back = node
                    while back != start:
                        path.append(back)
                        back = previous[back]
                    return [start, *reversed(path[1:]), start] if len(path) > 1 else [start, start]
                if nxt not in seen:
                    seen.add(nxt)
                    previous[nxt] = node
                    following.append(nxt)
        frontier = following
    return [start]


# --- lint -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefIssue:
    """One thing `kerp recipes lint` reports about recipe references."""

    kind: str  # "missing", "outside", "cycle", "yield"
    path: str  # the file to fix
    line: int
    message: str


def lint_refs(files: Mapping[str, object]) -> list[RefIssue]:
    """Recipe-reference problems across ``files`` (repository path → parsed recipe).

    A reference whose target is not among the files, or leaves the root; a cycle
    of references; a `yield:` that doesn't parse on a recipe a reference points
    at. Parse errors are skipped (lint lists them on its own).
    """
    from app.recipes.cooklang.model import Recipe  # the parsed shape; still pure

    recipes = {path: parsed for path, parsed in files.items() if isinstance(parsed, Recipe)}
    issues: list[RefIssue] = []
    edges: dict[str, list[str]] = {}
    used: dict[str, tuple[str, int]] = {}
    for path, recipe in sorted(recipes.items()):
        for ref in recipe.ingredients:
            if not ref.ref_path:
                continue
            target = target_path(path, ref.ref_path)
            if target is None:
                issues.append(
                    RefIssue("outside", path, ref.line, f"@{ref.ref_path} leaves the recipes root")
                )
            elif target not in files:
                issues.append(
                    RefIssue("missing", path, ref.line, f"@{ref.ref_path}: no recipe at {target}")
                )
            else:
                edges.setdefault(path, []).append(target)
                used.setdefault(target, (path, ref.line))
    for cycle in cycles(recipes, edges):
        walk = " → ".join(chain(cycle, edges, cycle[0]))
        issues.append(RefIssue("cycle", cycle[0], 0, f"recipe references go round: {walk}"))
    for target, (by, line) in sorted(used.items()):
        recipe = recipes.get(target)
        if recipe is None or recipe.makes_qty is not None:
            continue
        if recipe.makes_text:
            why = f"`yield: {recipe.makes_text}` isn't an amount and a unit"
        else:
            why = "no `yield:`"
        issues.append(
            RefIssue(
                "yield",
                target,
                0,
                f"{why}, so {by}:{line} can't be costed; write it as `yield: 500 ml`",
            )
        )
    return issues
