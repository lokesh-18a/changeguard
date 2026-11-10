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


def find_conflicts(cat: Catalog, new_ids: list[str]) -> list[Conflict]:
    """Checks that existing rules can cope with the new ones. Each check is
    a plain rule about the catalog, so its verdict is reproducible."""
    found: list[Conflict] = []
    new = [cat.get(i) for i in new_ids]
    has_midterm = any(r.attributes.get("midterm") for r in new)
    new_addon = next((r for r in new if r.id.endswith("-PRICE")), None)
    new_renew = next((r for r in new if r.id.endswith("-RENEW")), None)

    if has_midterm and "addons" not in cat.get("BIL-ENDORSE").attributes.get("applies_to", []):
        found.append(Conflict(
            id="C1", severity="high", rules=[i for i in new_ids if i.endswith("-MIDTERM")] + ["BIL-ENDORSE"],
            message="Mid-term add-on charges flow into endorsement billing, but endorsement billing is configured only for accessory changes.",
            fix="Extend BIL-ENDORSE to accept add-on endorsements, pro-rata for remaining months."))
    if has_midterm and cat.get("BIL-ANNUAL").attributes.get("fixed_annual_premium"):
        found.append(Conflict(
            id="C2", severity="medium", rules=["BIL-ANNUAL"],
            message="The annual invoice assumes the premium is fixed for the whole year; a mid-term add-on changes it.",
            fix="Show the endorsement invoice alongside the annual invoice and in the payment schedule."))
    if new_addon and not cat.get("BIL-CANCEL-REFUND").attributes.get("includes_addons"):
        found.append(Conflict(
            id="C3", severity="high", rules=[new_addon.id, "BIL-CANCEL-REFUND"],
            message="Refund on cancellation reads only OD and TP premium, so the new add-on's premium would never be refunded.",
            fix="Add add-on premiums to the refund calculation (needs a business decision: pro-rata or no refund)."))
    if new_renew and not cat.get("REN-AGE").attributes.get("rechecks_addon_eligibility"):
        found.append(Conflict(
            id="C4", severity="high", rules=[new_renew.id, "REN-AGE"],
            message="Renewal re-rating does not re-check add-on eligibility, so a car that ages past the limit would keep the add-on.",
            fix="Call the add-on's renewal eligibility rule during renewal re-rating."))
    if new_addon:
        found.append(Conflict(
            id="C5", severity="medium", rules=[new_addon.id, "DOC-WORDING", "DOC-SCHEDULE"],
            message="Policy wording and schedule have no clause or line for the new add-on.",
            fix="Draft the add-on clause; add a schedule line showing its premium."))
    return found
