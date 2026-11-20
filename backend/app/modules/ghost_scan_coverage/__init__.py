
STATUS = "partial"


def coverage(conn, run_id: str | None = None) -> dict:
    held = conn.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"]
    return {
        "run_id": run_id or "latest",
        "scanned_cells": [],
        "hit_cells": [],
        "held_n": held,
        "complete": False,
    }
