"""Mutation auditor: measures how strong a test suite is by injecting RANDOM defects.

Each mutant is a copy of engine.py with exactly one small change, chosen by a seeded
random generator from every place a typical coding slip could happen:

  comparison   <  <=  >  >=  ==  !=   swapped with a neighbour      (boundary slips)
  arithmetic   +  -  *  /             swapped                         (formula slips)
  constant     an integer n becomes n + 1                             (off-by-one)
  boolean      and <-> or, `not x` -> `x`                             (logic slips)
  call         min <-> max, round(x) -> int(x)                        (rounding / clamping)

A mutant is "killed" when at least one test fails on it. Mutation score = killed / total.
Nothing is planted by hand: the same procedure runs on any version of the engine.
"""
from __future__ import annotations

import ast
import copy
import random
import types
from dataclasses import dataclass
from pathlib import Path

from . import engine
from .catalog import Catalog
from .models import TestCase
from .runner import run_tests

SOURCE = Path(engine.__file__).read_text()

CMP_SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult}
CALL_SWAP = {"min": "max", "max": "min", "round": "int"}


@dataclass
class Mutant:
    id: str
    kind: str
    line: int
    description: str
    tree: ast.Module


HARNESS = {"run"}  # the dispatcher the test runner bypasses; mutating it measures nothing


def _points(tree: ast.Module):
    """Every mutation point in the engine's business logic, in a stable order."""
    skip = set()
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name in HARNESS:
            skip |= {id(n) for n in ast.walk(fn)}
    pts = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in CMP_SWAP:
                    pts.append(("comparison", node, i))
        elif isinstance(node, ast.BinOp) and type(node.op) in BIN_SWAP:
            pts.append(("arithmetic", node, None))
        elif isinstance(node, ast.Constant) and type(node.value) is int and not isinstance(node.value, bool):
            pts.append(("constant", node, None))
        elif isinstance(node, ast.BoolOp):
            pts.append(("boolean", node, None))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            pts.append(("boolean-not", node, None))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in CALL_SWAP:
            pts.append(("call", node, None))
    pts.sort(key=lambda p: (p[1].lineno, p[1].col_offset, p[0], p[2] or 0))
    return pts


def _mutate(tree: ast.Module, index: int) -> tuple[ast.Module, str, str, int]:
    new = copy.deepcopy(tree)
    kind, node, op_i = _points(new)[index]
    line = node.lineno
    if kind == "comparison":
        old = type(node.ops[op_i]); node.ops[op_i] = CMP_SWAP[old]()
        desc = f"{old.__name__} -> {CMP_SWAP[old].__name__}"
    elif kind == "arithmetic":
        old = type(node.op); node.op = BIN_SWAP[old]()
        desc = f"{old.__name__} -> {BIN_SWAP[old].__name__}"
    elif kind == "constant":
        desc = f"{node.value} -> {node.value + 1}"; node.value = node.value + 1
    elif kind == "boolean":
        old = type(node.op); node.op = ast.Or() if old is ast.And else ast.And()
        desc = f"{old.__name__} -> {'Or' if old is ast.And else 'And'}"
    elif kind == "boolean-not":
        desc = "removed `not`"
        # replace `not x` with `x` by rewriting the parent: simplest is double negation removal via operand copy
        node.op = ast.UAdd()  # +x on a bool behaves like x (True->1, False->0): truthiness preserved, `not` removed
    else:  # call
        old = node.func.id; node.func.id = CALL_SWAP[old]; desc = f"{old}() -> {CALL_SWAP[old]}()"
    ast.fix_missing_locations(new)
    return new, kind, desc, line


def all_mutants() -> list[Mutant]:
    tree = ast.parse(SOURCE)
    out = []
    for i in range(len(_points(tree))):
        t, kind, desc, line = _mutate(tree, i)
        out.append(Mutant(f"M{i + 1:03d}", kind, line, f"line {line}: {desc}", t))
    return out


def sample(n: int | None = None, seed: int = 7) -> list[Mutant]:
    muts = all_mutants()
    if n is None or n >= len(muts):
        return muts
    return random.Random(seed).sample(muts, n)


def load_ops(m: Mutant) -> dict:
    mod = types.ModuleType(f"changeguard.engine_{m.id}")
    mod.__dict__["__package__"] = "changeguard"
    exec(compile(m.tree, f"<mutant {m.id}>", "exec"), mod.__dict__)
    return mod.OPERATIONS


@dataclass
class AuditResult:
    suite: str
    total: int
    killed: int
    survivors: list[str]
    killers: dict[str, list[str]]

    @property
    def score(self) -> float:
        return self.killed / self.total if self.total else 0.0


def audit(cat: Catalog, tests: list[TestCase], mutants: list[Mutant], suite: str = "suite") -> AuditResult:
    killed, survivors, killers = 0, [], {}
    for m in mutants:
        try:
            ops = load_ops(m)
        except Exception:
            killed += 1  # does not even load: any test run would crash
            killers[m.id] = ["(fails to load)"]
            continue
        res = run_tests(cat, tests, ops)
        failing = [r.test_id for r in res if not r.passed]
        if failing:
            killed += 1
            killers[m.id] = failing
        else:
            survivors.append(m.id)
    return AuditResult(suite, len(mutants), killed, survivors, killers)


def baseline_suite(tests: list[TestCase]) -> list[TestCase]:
    """What a typical hand-written suite looks like: one ordinary example per requirement
    (no boundaries, no exclusions), plus the existing regression tests."""
    seen, out = set(), []
    for t in tests:
        if t.origin == "regression":
            out.append(t)
        elif t.technique == "equivalence class" and t.requirement not in seen:
            seen.add(t.requirement)
            out.append(t)
    return out
