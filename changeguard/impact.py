"""Rule graph, impact analysis and conflict detection. All deterministic.

Edges come from data flow: if rule A writes a field that rule B reads,
there is an edge A -> B ("A affects B"). Nobody draws the links by hand;
they fall out of each rule's declared inputs and outputs.
"""
from __future__ import annotations

import networkx as nx

from .catalog import Catalog
from .models import Conflict, ImpactItem


def build_graph(cat: Catalog) -> nx.DiGraph:
    g = nx.DiGraph()
    for r in cat.rules.values():
        g.add_node(r.id, area=r.area, name=r.name, is_new=r.is_new)
    for a in cat.rules.values():
        for b in cat.rules.values():
            if a.id == b.id:
                continue
            shared = sorted(set(a.outputs) & set(b.inputs))
            if shared:
                g.add_edge(a.id, b.id, fields=shared)
    return g


def find_impact(cat: Catalog, new_ids: list[str], changed_ids: list[str], max_depth: int = 3) -> list[ImpactItem]:
    g = build_graph(cat)
    starts = list(new_ids) + list(changed_ids)
    items: dict[str, ImpactItem] = {}
    for s in starts:
        r = cat.get(s)
        items[s] = ImpactItem(rule_id=s, area=r.area, name=r.name, distance=0, path=[s],
                              status="new" if s in new_ids else "changed")
    for s in starts:
        lengths = nx.single_source_shortest_path(g, s, cutoff=max_depth)
        for target, path in lengths.items():
            if target in starts:
                continue
            dist = len(path) - 1
            if target not in items or dist < items[target].distance:
                r = cat.get(target)
                items[target] = ImpactItem(rule_id=target, area=r.area, name=r.name,
                                           distance=dist, path=path, status="affected")
    order = {"new": 0, "changed": 0, "affected": 1}
    return sorted(items.values(), key=lambda i: (order[i.status], i.distance, i.area, i.rule_id))


def explain_path(g: nx.DiGraph, path: list[str]) -> str:
    """'ZD-PRICE -[addon_premiums]-> RAT-TOTAL -[net_premium]-> RAT-GST'"""
    if len(path) == 1:
        return path[0]
    out = path[0]
    for a, b in zip(path, path[1:]):
        out += f" -[{', '.join(g.edges[a, b]['fields'])}]-> {b}"
    return out


def find_conflicts(cat: Catalog, new_ids: list[str], changed_ids: list[str] | None = None) -> list[Conflict]:
    """Generic, data-driven conflict checks. Nothing here names a specific rule or add-on:

    1. Broken assumptions: an existing rule records what it takes for granted
       (e.g. "premium fixed for the term"); a new rule declares capabilities
       (e.g. "mid-term premium change"). If a capability breaks an assumption, flag it.
    2. Partial family reads: an existing rule reads some members of a field family
       (e.g. premium components) but not the member the change adds to, so the change
       silently bypasses it. This is how hidden gaps outside the dependency walk surface.
    3. Renewal re-check: a new rule depends on a value that changes at renewal
       (e.g. car age) but nothing re-evaluates it at renewal.
    """
    changed_ids = changed_ids or []
    found: list[Conflict] = []
    new = [cat.get(i) for i in new_ids]
    tags = {t: [r.id for r in new if t in r.tags] for r in new for t in r.tags}

    def add(severity, rules, message, fix):
        found.append(Conflict(id=f"C{len(found) + 1}", severity=severity, rules=rules, message=message, fix=fix))

    # 1. broken assumptions
    for r in cat.rules.values():
        if r.is_new:
            continue
        for a in r.assumptions:
            if a["broken_by"] in tags:
                add("medium" if r.area == "documents" else "high", tags[a["broken_by"]] + [r.id],
                    f"{r.id} ({r.name}): {a['text']} The change introduces '{a['broken_by']}'.", a["fix"])

    # 2. partial family reads
    for fam, spec in cat.families.items():
        members = set(spec["fields"])
        for nr in new:
            for field in sorted(set(nr.outputs) & members):
                for r in cat.rules.values():
                    if r.is_new or field in r.inputs:
                        continue
                    read = sorted(set(r.inputs) & members)
                    if read:
                        add("high", [nr.id, r.id],
                            f"{r.id} ({r.name}) reads {', '.join(read)} but not {field}, so what {nr.id} adds to "
                            f"{field} ({spec['meaning']}) would be left out.",
                            f"Decide whether {r.id} should include {field}; update it or record why not.")

    # 3. renewal re-check
    renewal_rules = [r for r in new if r.area == "renewal"]
    for nr in new:
        varying = sorted(set(nr.inputs) & set(cat.varies_at_renewal))
        if varying and nr.area != "renewal" and not renewal_rules:
            add("high", [nr.id], f"{nr.id} depends on {', '.join(varying)}, which changes at renewal, "
                f"but no renewal rule re-evaluates it.", f"Add a renewal rule that re-applies {nr.id}.")
    return found
