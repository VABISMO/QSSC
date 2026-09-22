"""Minimal s-expression parser / printer for KiCad files.

KiCad's s-expression syntax is tolerant of whitespace; this tokenizer splits on
parens, double-quoted strings, and bare atoms. Parsed forms are nested lists
whose leaves are ``(kind, value)`` tuples from :func:`atom` (``"str"`` for
quoted strings, ``"atom"`` for bare tokens). :func:`emit` renders a parsed tree
back to text with tab indentation matching KiCad's convention. The ``A``/``S``/
``L``/``u`` constructors build the common leaf/list/UUID nodes.
"""
from __future__ import annotations

import re
import uuid

# Split on parens, double-quoted strings, and bare whitespace-delimited atoms.
TOK = re.compile(r'\s*(\(|\)|"[^"]*"|[^\s()]+)')


def tokenize(s):
    """Split an s-expression string into a flat list of token strings."""
    return TOK.findall(s)


def atom(t):
    """Classify one token as a quoted-string or bare-atom leaf."""
    if t.startswith('"'):
        return ("str", t[1:-1])
    return ("atom", t)


def parse(s):
    """Parse an s-expression string into a nested list of leaves."""
    toks = tokenize(s)
    pos = 0

    def plist():
        nonlocal pos
        assert toks[pos] == "(", toks[pos]
        pos += 1
        items = []
        while toks[pos] != ")":
            if toks[pos] == "(":
                items.append(plist())
            else:
                items.append(atom(toks[pos]))
                pos += 1
        pos += 1
        return items

    return plist()


def emit(node, depth=0):
    """Render a parsed s-expression node back to KiCad-formatted text."""
    if isinstance(node, tuple):
        k, v = node
        return '"' + v + '"' if k == "str" else v
    if not node:
        return "()"
    sym = emit(node[0])
    children = node[1:]
    if not any(isinstance(c, list) for c in children):
        return "(" + sym + (" " + " ".join(emit(c) for c in children) if children else "") + ")"
    out = "(" + sym
    for c in children:
        if isinstance(c, list):
            out += "\n" + "\t" * (depth + 1) + emit(c, depth + 1)
        else:
            out += " " + emit(c)
    out += "\n" + "\t" * depth + ")"
    return out


def A(v):
    """Bare-atom leaf (unquoted)."""
    return ("atom", str(v))


def S(v):
    """Quoted-string leaf."""
    return ("str", str(v))


def L(*items):
    """S-expression list node from the given children."""
    return list(items)


def u():
    """Fresh UUID string (unique node identifier)."""
    return str(uuid.uuid4())