# qssc/circuits_user.py
"""AST-sandboxed builder for operator-supplied quantum circuit source.

Extracted from :mod:`qssc.circuits` (pure move, zero behavior change):
:class:`UserCircuitError`, the AST validator + import/call whitelists, the
numpy file-IO stripping proxy, and :func:`build_user_circuit`. The sandbox
gates the ``/api/circuit/custom`` endpoint (API key + localhost only).
``qssc.circuits`` re-exports :class:`UserCircuitError` and
:func:`build_user_circuit` so ``from qssc.circuits import build_user_circuit``
keeps resolving unchanged.

Security note -- this is a documented, NOT absolute, sandbox. The AST validator
rejects dunder-attribute access (the classic ``obj.__init__.__globals__`` escape
to the real builtins / ``os.system``) and calls to dangerous builtins, on top of
an import whitelist. It is NOT a capability-secure isolation boundary: a novel
escape via an allowed object's non-dunder surface cannot be ruled out. The
endpoint is therefore reachable ONLY behind the ``QSSC_ENABLE_CUSTOM_CIRCUIT``
flag + an optional ``X-API-Key`` AND a localhost check (see
:func:`api._guards._is_local` / :func:`api._guards._check_custom_circuit`), so
the trust model is "the local operator may run their own Qiskit snippet" -- not
"untrusted remote code". Do not expose ``/api/circuit/custom`` on a public
deploy without an additional isolation layer (container/process jail).
"""
from __future__ import annotations

import ast as _ast
import math

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister


class UserCircuitError(ValueError):
    """Raised when a user-supplied circuit definition cannot be built."""


# --- AST sandbox for the Python circuit path ------------------------------
# A restricted-builtins dict alone is escapable: any exposed object (e.g.
# QuantumCircuit) leaks the real builtins through `obj.__init__.__globals__`,
# giving arbitrary os/system execution.  We therefore validate the parsed AST
# and reject dunder-attribute access (the escape hatch) and calls to dangerous
# builtins, in addition to the import whitelist.  Normal Qiskit usage
# (`QuantumCircuit(2)`, `qc.h(0)`, `qc.cx(0,1)`, `qc.measure(...)`) uses only
# non-dunder attributes and is unaffected.
_USER_ALLOWED_MODULES = {
    "qiskit", "qiskit.circuit", "qiskit.circuit.library",
    "numpy", "math", "fractions",
}
_USER_DANGEROUS_CALLS = {
    "eval", "exec", "compile", "getattr", "setattr", "delattr", "globals",
    "locals", "vars", "open", "input", "__import__", "breakpoint", "exit",
    "quit", "memoryview", "type", "object", "classmethod", "staticmethod",
    "property", "super",
}


class _UserAstValidator(_ast.NodeVisitor):
    """Reject AST nodes that could escape the restricted namespace."""

    def __init__(self):
        self.errors: list = []

    @staticmethod
    def _is_dunder(attr: str) -> bool:
        return attr.startswith("__") and attr.endswith("__")

    def visit_Attribute(self, node):  # noqa: D401
        # block `x.__globals__`, `x.__init__`, `x.__class__`, `x.__dict__`, ...
        if self._is_dunder(node.attr):
            self.errors.append(
                f"attribute access to '{node.attr}' is not allowed")
        self.generic_visit(node)

    def visit_Call(self, node):  # noqa: D401
        func = node.func
        if isinstance(func, _ast.Name) and func.id in _USER_DANGEROUS_CALLS:
            self.errors.append(f"call to '{func.id}' is not allowed")
        self.generic_visit(node)

    def visit_Import(self, node):  # noqa: D401
        for n in node.names:
            if n.name.split(".")[0] not in _USER_ALLOWED_MODULES:
                self.errors.append(f"import of '{n.name}' is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):  # noqa: D401
        mod = (node.module or "").split(".")[0]
        if mod not in _USER_ALLOWED_MODULES:
            self.errors.append(f"import from '{node.module}' is not allowed")
        self.generic_visit(node)


# numpy file-IO entry points the operator's script must not reach. The AST sandbox
# blocks `open`/dunder escapes, but numpy's own ``np.load``/``np.fromfile``/
#``np.savetxt`` read and write the host filesystem directly, so strip them
# from the exposed ``np``. This is defense in depth for a localhost lab tool,
# not a claim of absolute sandboxing.
_USER_NP_BLOCKED = {
    "load", "save", "savez", "savez_compressed", "savetxt", "fromfile",
    "genfromtxt", "loadtxt", "memmap", "tofile",
}


class _SafeNumpy:
    """Proxy over numpy that raises on the file-IO attributes."""

    def __init__(self, npmod):
        object.__setattr__(self, "_np", npmod)

    def __getattr__(self, name):
        if name in _USER_NP_BLOCKED:
            raise UserCircuitError(f"numpy file I/O '{name}' is not allowed")
        return getattr(self._np, name)


def build_user_circuit(code: str, max_qubits: int = 64) -> QuantumCircuit:
    """Build a Qiskit circuit from user-supplied source.

    Accepts either:

    * **OpenQASM 2.0** — detected when the text contains ``OPENQASM`` or
      starts with a ``qreg``/``creg`` declaration. Parsed with
      :meth:`QuantumCircuit.from_qasm_str`.
    * **Python** — a snippet that constructs a :class:`QuantumCircuit` named
      ``qc`` (or ``circuit``) using a restricted namespace exposing the common
      Qiskit primitives. The snippet is first validated by an **AST sandbox**
      that rejects dunder-attribute access (the route by which a restricted
      builtins dict is normally escaped) and dangerous calls, then executed
      with a restricted builtins namespace and an import whitelist.

    The resulting circuit must not exceed ``max_qubits`` qubits.

    .. note::

       This is a **local lab / demo tool**. The AST sandbox closes the known
       restricted-builtins escape (`obj.__init__.__globals__`) and strips
       numpy's file-IO entry points (``np.load``/``np.fromfile``/``np.savetxt``
       …) from both the convenience ``np`` and a bare ``import numpy``, but no
       pure-Python sandbox is absolute — dotted imports (``import numpy.linalg``)
       still reach the real module. Do not expose this endpoint to untrusted
       remote clients. The API already binds to localhost.
    """
    text = (code or "").strip()
    if not text:
        raise UserCircuitError("empty circuit source")

    # --- OpenQASM 2.0 path -------------------------------------------------
    if "OPENQASM" in text or text.startswith(("qreg", "creg", "gate ", "opaque")):
        # qiskit 2.x qasm2 does not auto-include the standard gate library,
        # so inject qelib1.inc when the operator omitted it (lets plain `h`/`cx`
        # QASM "just work").
        if "qelib1.inc" not in text and "include" not in text:
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("OPENQASM"):
                lines.insert(1, 'include "qelib1.inc";')
            else:
                lines.insert(0, 'OPENQASM 2.0;', 'include "qelib1.inc";')
            text = "\n".join(lines)
        try:
            qc = QuantumCircuit.from_qasm_str(text)
        except Exception as exc:  # noqa: BLE001 - surface as a clean error
            raise UserCircuitError(f"QASM parse failed: {exc}") from exc
        if qc.num_qubits > max_qubits:
            raise UserCircuitError(f"circuit has {qc.num_qubits} qubits > {max_qubits}")
        if qc.num_clbits == 0:  # ensure measurable
            qc.measure_all()
        return qc

    # --- Python path (AST-sandboxed) --------------------------------------
    import builtins as _builtins

    # 1. parse + validate the AST before any execution
    try:
        tree = _ast.parse(text, filename="<user_circuit>")
    except SyntaxError as exc:
        raise UserCircuitError(f"Python parse failed: {exc}") from exc
    validator = _UserAstValidator()
    validator.visit(tree)
    if validator.errors:
        raise UserCircuitError(
            "blocked by sandbox: " + "; ".join(sorted(set(validator.errors))))

    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0 or name not in _USER_ALLOWED_MODULES:
            raise UserCircuitError(f"import of '{name}' is not allowed")
        mod = _builtins.__import__(name, globals, locals, fromlist, level)
        # bare `import numpy` / `import numpy as np` also gets the file-IO
        # stripped proxy. (Dotted imports like `import numpy.linalg` return the
        # real submodule and are a known residual — this is a localhost lab
        # tool, not a hardened sandbox; see the module docstring.)
        if name == "numpy":
            return _SafeNumpy(mod)
        return mod

    namespace = {
        # primitives the operator's script may use directly (no import needed)
        "QuantumCircuit": QuantumCircuit,
        "QuantumRegister": QuantumRegister,
        "ClassicalRegister": ClassicalRegister,
        # convenience aliases matching common tutorials
        "np": _SafeNumpy(np), "math": math, "pi": math.pi,
    }
    safe_builtins = {
        "abs": abs, "min": min, "max": max, "range": range, "len": len,
        "sum": sum, "round": round, "int": int, "float": float, "bool": bool,
        "list": list, "tuple": tuple, "dict": dict, "set": set,
        "enumerate": enumerate, "zip": zip, "sorted": sorted, "str": str,
        "True": True, "False": False, "None": None,
        "__import__": _safe_import, "__build_class__": _builtins.__build_class__,
        "print": print,
    }
    namespace["__builtins__"] = safe_builtins
    try:
        exec(compile(tree, "<user_circuit>", "exec"), namespace)  # noqa: S102
    except UserCircuitError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UserCircuitError(f"Python build failed: {exc}") from exc

    qc = namespace.get("qc") or namespace.get("circuit")
    if not isinstance(qc, QuantumCircuit):
        raise UserCircuitError(
            "script must define a QuantumCircuit named `qc` (or `circuit`)")
    if qc.num_qubits > max_qubits:
        raise UserCircuitError(f"circuit has {qc.num_qubits} qubits > {max_qubits}")
    if qc.num_clbits == 0:
        qc.measure_all()
    return qc