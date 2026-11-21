from fastapi import APIRouter, Body, HTTPException

from app.db import connect
from app.modules import ghost_occupy_hold
from app.modules import ghost_scan_coverage as ghost_cov
from app.seed import overdue_hours
from app.services import bay as bay_svc
from app.services import ingest as ingest_svc

router = APIRouter(prefix="/api")


@router.get("/overview")
def overview():
    conn = connect()
    try:
        data = bay_svc.overview(conn)
        data["overdue_hours"] = overdue_hours(conn)
        return data
    finally:
        conn.close()


@router.get("/bay")
def bay_matrix():
    conn = connect()
    try:
        return bay_svc.cabinet_matrix(conn)
    finally:
        conn.close()


@router.get("/occupancies")
def list_occupancies(status: str | None = None):
    conn = connect()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM occupancies WHERE status=? ORDER BY opened_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM occupancies ORDER BY opened_at DESC LIMIT 100").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/events")
def list_events():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, cabinet_id, cell_id, event_type, event_ts, tracking_no, channel FROM parcel_events ORDER BY event_ts, id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/quarantine")
def list_quarantine():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, reason, member_json, created_at, cell_id, scan_id, fingerprint "
            "FROM quarantine ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/ghost/scan")
def ghost_scan():
    """幽灵占用扫描入口：发现即整组入搁置，不自动修正式态。"""
    conn = connect()
    try:
        report = ghost_occupy_hold.scan_ghosts(conn)
        conn.commit()
        return report
    finally:
        conn.close()


@router.get("/ghost")
def ghost_verification():
    """幽灵核证只读接口：列出全部不一致对（活态 + 历史搁置），不含任何写口。"""
    conn = connect()
    try:
        return ghost_occupy_hold.list_ghosts(conn)
    finally:
        conn.close()


@router.get("/occupancy-proofs")
def occupancy_proofs():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM occupancy_proofs ORDER BY id DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/ingest/run")
def run_ingest():
    return ingest_svc.run_batch()


@router.get("/settings")
def get_settings():
    conn = connect()
    try:
        return {"overdue_hours": overdue_hours(conn)}
    finally:
        conn.close()


@router.put("/settings/overdue-hours")
def put_overdue(body: dict = Body(...)):
    hours = int(body.get("overdue_hours", 48))
    if hours < 1:
        raise HTTPException(400, "超时回收至少 1 小时")
    conn = connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('overdue_hours', ?)",
            (str(hours),),
        )
        conn.commit()
        return {"overdue_hours": hours}
    finally:
        conn.close()

# --- 幽灵扫描覆盖证明（只读核证口，基线所无） ---
@router.get("/ghost-scan/coverage")
def ghost_scan_coverage(run_id: str | None = None):
    """覆盖成员证明：指定（缺省最新）跑次的已扫/命中集合 + 逐条核证。只读。"""
    conn = connect()
    try:
        return ghost_cov.coverage(conn, run_id)
    finally:
        conn.close()


@router.get("/ghost-scan/runs")
def ghost_scan_runs(limit: int = 20):
    """覆盖跑次列表（最新在前），供核证页选跑次。只读。"""
    conn = connect()
    try:
        return ghost_cov.list_runs(conn, limit)
    finally:
        conn.close()


@router.get("/ghost-scan/verify")
def ghost_scan_verify(run_id: str | None = None):
    """单跑次覆盖核证：缺员 / 越界 / held_n 分叉 / 搁置互证逐条给结论。只读。"""
    conn = connect()
    try:
        resolved = run_id or ghost_cov.latest_run_id(conn)
        if resolved is None:
            raise HTTPException(404, "尚无覆盖跑次")
        return ghost_cov.verify_run(conn, resolved)
    finally:
        conn.close()


@router.get("/ghost-scan/window")
def ghost_scan_window():
    """扫描范围摘要：当前库全域格口与本趟应扫集合。只读。"""
    conn = connect()
    try:
        return ghost_cov.scan_window(conn)
    finally:
        conn.close()


@router.get("/ghost-scan/reconcile")
def ghost_scan_reconcile():
    """占用对账：活态幽灵 vs 最近跑次覆盖，验证扫描范围与占用账一致。只读。"""
    conn = connect()
    try:
        return ghost_occupy_hold.reconcile_coverage(conn)
    finally:
        conn.close()


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

