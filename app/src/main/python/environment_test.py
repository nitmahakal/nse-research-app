import importlib


def _check_import(name: str) -> str:
    try:
        importlib.import_module(name)
        return f"OK   - import {name}"
    except Exception as exc:
        return f"FAIL - import {name}: {exc}"


def run_all_checks() -> str:
    lines = []
    lines.append("NSE Research App - Environment Check")
    lines.append("=====================================")

    for pkg in ("pandas", "numpy", "yfinance", "sqlite3"):
        lines.append(_check_import(pkg))

    lines.append("")
    lines.append("Functional checks")
    lines.append("------------------")

    try:
        import sqlite3
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.execute("CREATE TABLE test (id INTEGER, name TEXT)")
        cur.execute("INSERT INTO test VALUES (1, 'hello')")
        conn.commit()
        cur.execute("SELECT * FROM test")
        row = cur.fetchone()
        conn.close()
        lines.append(f"OK   - sqlite3 read/write test: {row}")
    except Exception as exc:
        lines.append(f"FAIL - sqlite3 read/write test: {exc}")

    try:
        import pandas as pd
        df = pd.DataFrame({"a": [1, 2, 3, 4, 5]})
        total = int(df["a"].sum())
        lines.append(f"OK   - pandas DataFrame test: sum={total}")
    except Exception as exc:
        lines.append(f"FAIL - pandas DataFrame test: {exc}")

    try:
        import numpy as np
        arr = np.array([1.0, 2.0, 3.0])
        mean_val = float(arr.mean())
        lines.append(f"OK   - numpy array test: mean={mean_val}")
    except Exception as exc:
        lines.append(f"FAIL - numpy array test: {exc}")

    lines.append("")
    lines.append("Note: this test does not call yfinance over the network yet -")
    lines.append("it only confirms the package imports correctly. Network")
    lines.append("download testing comes in a later milestone.")

    return "\n".join(lines)
