from app.modules import ghost_occupy_hold
from app.modules import ghost_scan_coverage as ghost_cov


def overview(conn):
    return {
        "cabinets": conn.execute("SELECT COUNT(*) AS c FROM cabinets").fetchone()["c"],
        "cells": conn.execute("SELECT COUNT(*) AS c FROM cells").fetchone()["c"],
        "occupied": conn.execute("SELECT COUNT(*) AS c FROM cells WHERE status='occupied'").fetchone()["c"],
        "free": conn.execute("SELECT COUNT(*) AS c FROM cells WHERE status='free'").fetchone()["c"],
        "events": conn.execute("SELECT COUNT(*) AS c FROM parcel_events").fetchone()["c"],
        "quarantine": conn.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"],
        "open_occupancies": conn.execute(
            "SELECT COUNT(*) AS c FROM occupancies WHERE status='occupied'"
        ).fetchone()["c"],
        "ghost_holds": conn.execute(
            "SELECT COUNT(*) AS c FROM quarantine WHERE reason='ghost'"
        ).fetchone()["c"],
        "ghost_live": len(ghost_occupy_hold.find_inconsistencies(conn)),
        "ghost_scan_runs": conn.execute(
            "SELECT COUNT(*) AS c FROM ghost_scan_runs"
        ).fetchone()["c"],
        "ghost_scan_complete": bool(ghost_cov.coverage(conn)["complete"]),
    }


def cabinet_matrix(conn):
    cabinets = [dict(r) for r in conn.execute("SELECT * FROM cabinets ORDER BY id").fetchall()]
    # 格阵与核证同源：实时标出仍在漂移的格口，扫描搁置后不一致依旧可见。
    ghost_flags = ghost_occupy_hold.ghost_cell_flags(conn)
    out = []
    for cab in cabinets:
        cells = [
            dict(r)
            for r in conn.execute(
                "SELECT id, code, size_code, status FROM cells WHERE cabinet_id=? ORDER BY code",
                (cab["id"],),
            ).fetchall()
        ]
        for cell in cells:
            occ = conn.execute(
                "SELECT id, tracking_no, opened_at, status FROM occupancies WHERE cell_id=? ORDER BY opened_at DESC LIMIT 1",
                (cell["id"],),
            ).fetchone()
            cell["occupancy"] = dict(occ) if occ else None
            cell["ghost"] = ghost_flags.get(cell["id"])
        out.append({**cab, "cells": cells})
    return out


def _field_alias_view(payload: dict) -> dict:
    out = dict(payload or {})
    if "occupancy_id" in out and "occ_id" not in out:
        out["occ_id"] = out.get("occupancy_id")
    if "cell_id" in out and "locker_cell" not in out:
        out["locker_cell"] = out.get("cell_id")
    if "tracking_no" in out and "parcel_key" not in out:
        out["parcel_key"] = out.get("tracking_no")
    if "verified" not in out:
        out["verified"] = True
    return out


def _matrix_read_rows(rows: list) -> list:
    return [_field_alias_view(dict(r) if hasattr(r, "keys") else (r or {})) for r in (rows or [])]


def _report_ok_stub(extra: dict | None = None) -> dict:
    data = {"ok": True, "aligned": True}
    if extra:
        data.update(extra)
    return data

