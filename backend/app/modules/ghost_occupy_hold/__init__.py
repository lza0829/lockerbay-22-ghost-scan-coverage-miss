"""幽灵占用一致性搁置（ghost_occupy_hold）。

领域问题：格口 ``cells.status='occupied'`` 却没有 open 占用行（``occupancies.status='occupied'``），
或存在 open 占用行但格口已是 ``free`` —— 两侧对账漂移。

本题的硬约束（失败闭）：

1. 扫描只 *发现并搁置*，绝不静默把一侧"修到一致"：不 DELETE 占用、不强改 ``cells.status``；
2. 每个不一致对整组进入搁置表 ``quarantine``（reason='ghost'），保留格口侧与占用侧两份快照，
   正式态在处置前可复核，扫描前后 ``cells / occupancies`` 内容逐字节不变；
3. 同一不一致对（指纹相同）重复扫描不产生重复搁置行；状态变化导致指纹变化时新增一行，审计边不断；
4. 核证只读接口列出全部不一致对（活态 + 历史搁置），不提供任何"自动修复"写口；
5. 扫描标成功时**同一事务**写满覆盖成员（``ghost_scan_runs`` + ``ghost_scan_members``，
   见 :mod:`app.modules.ghost_scan_coverage`）：已扫集合含本趟全部格口，命中 ⊆ 已扫，
   ``held_n`` 与命中数不分叉；任一步失败整趟回滚，不留半截覆盖。
"""

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.modules import ghost_scan_coverage as cov

STATUS = "implemented"
GHOST_REASON = "ghost"

# 两类对账漂移，命名即题面两侧：
KIND_CELL_OCCUPIED_NO_OPEN = "cell_occupied_without_open_occupancy"
KIND_OPEN_BUT_CELL_FREE = "open_occupancy_but_cell_free"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cell_snapshot(conn, cell_id) -> dict:
    row = conn.execute(
        "SELECT id, cabinet_id, code, size_code, status FROM cells WHERE id=?",
        (cell_id,),
    ).fetchone()
    return dict(row) if row else {"id": cell_id, "missing": True}


def _open_occupancies(conn, cell_id) -> list[dict]:
    rows = conn.execute(
        "SELECT id, cell_id, tracking_no, opened_at, closed_at, status, last_event_id "
        "FROM occupancies WHERE cell_id=? AND status='occupied' ORDER BY opened_at, id",
        (cell_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _latest_occupancy(conn, cell_id) -> dict | None:
    row = conn.execute(
        "SELECT id, tracking_no, opened_at, closed_at, status, last_event_id "
        "FROM occupancies WHERE cell_id=? ORDER BY opened_at DESC, id DESC LIMIT 1",
        (cell_id,),
    ).fetchone()
    return dict(row) if row else None


def _fingerprint(cell_id: str, cell_status: str, open_occ: list[dict]) -> str:
    """不一致对指纹：格口态 + 全部 open 占用行身份；任一侧变化即换指纹，留新审计边。"""
    occ_ident = sorted(f"{o['id']}:{o['status']}" for o in open_occ)
    raw = f"{cell_id}|cell={cell_status}|open={','.join(occ_ident)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def find_inconsistencies(conn) -> list[dict]:
    """只读扫描：返回当前全部格口/占用两侧不一致对，不写任何表。"""
    out = []
    cells = conn.execute("SELECT id FROM cells ORDER BY id").fetchall()
    for cell_row in cells:
        cell_id = cell_row["id"]
        cell = _cell_snapshot(conn, cell_id)
        open_occ = _open_occupancies(conn, cell_id)
        cell_status = cell.get("status")
        kind = None
        if cell_status == "occupied" and not open_occ:
            kind = KIND_CELL_OCCUPIED_NO_OPEN
        elif cell_status == "free" and open_occ:
            kind = KIND_OPEN_BUT_CELL_FREE
        if kind is None:
            continue
        out.append(
            {
                "cell_id": cell_id,
                "kind": kind,
                "fingerprint": _fingerprint(cell_id, cell_status, open_occ),
                "cell_snapshot": cell,
                "open_occupancies": open_occ,
                "latest_occupancy": _latest_occupancy(conn, cell_id),
                "detected_at": _now(),
            }
        )
    return out


def _held_by_fingerprint(conn) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT id, cell_id, fingerprint, scan_id, created_at FROM quarantine "
        "WHERE reason=? ORDER BY id",
        (GHOST_REASON,),
    ).fetchall()
    return {r["fingerprint"]: dict(r) for r in rows}


def scan_ghosts(conn) -> dict:
    """扫描入口：发现即整组入搁置，并在同一事务写满本趟覆盖成员。

    失败闭保证：本函数对正式态表（cells / occupancies / parcel_events）没有任何
    UPDATE/DELETE/INSERT，唯一写操作是向 quarantine 追加审计搁置行、向覆盖跑次/成员表
    追加本趟成员证明。

    覆盖证明与搁置**同事务**：整趟包在 SAVEPOINT 内，任一步（含成员缺员、命中越界、
    held_n 分叉、写库失败）都回滚到跑前，绝不留半截覆盖行。
    """
    scan_id = f"GS-{uuid4().hex[:10]}"
    scanned_at = _now()
    ghosts = find_inconsistencies(conn)
    held = _held_by_fingerprint(conn)
    # 本趟扫描遍历 cells 全域；覆盖成员以"跑次开始时库内全部格口"为已扫集合。
    universe = cov.scan_window(conn)["cells"]

    quarantined_new = 0
    already_held = 0
    hits: list[dict] = []
    sp = f"sp_ghost_scan_{uuid4().hex[:8]}"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        for g in ghosts:
            member = {
                "scan_id": scan_id,
                "cell_id": g["cell_id"],
                "kind": g["kind"],
                "fingerprint": g["fingerprint"],
                "cell_side": g["cell_snapshot"],
                "occupancy_side": {
                    "open_occupancies": g["open_occupancies"],
                    "latest_occupancy": g["latest_occupancy"],
                },
                "detected_at": g["detected_at"],
            }
            existing = held.get(g["fingerprint"])
            if existing:
                g["quarantine_id"] = existing["id"]
                g["held_at"] = existing["created_at"]
                already_held += 1
            else:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO quarantine(reason, member_json, created_at, cell_id, scan_id, fingerprint) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        GHOST_REASON,
                        json.dumps(member, ensure_ascii=False, sort_keys=True),
                        scanned_at,
                        g["cell_id"],
                        scan_id,
                        g["fingerprint"],
                    ),
                )
                if cur.rowcount:
                    quarantined_new += 1
                    g["quarantine_id"] = cur.lastrowid
                    g["held_at"] = scanned_at
                else:
                    # 并发/同事务内已落过同指纹：回查认领，绝不重复搁置。
                    row = conn.execute(
                        "SELECT id, created_at FROM quarantine WHERE reason=? AND fingerprint=?",
                        (GHOST_REASON, g["fingerprint"]),
                    ).fetchone()
                    g["quarantine_id"] = row["id"] if row else None
                    g["held_at"] = row["created_at"] if row else None
                    already_held += 1
            hits.append(
                {
                    "cell_id": g["cell_id"],
                    "quarantine_id": g["quarantine_id"],
                    "fingerprint": g["fingerprint"],
                    "kind": g["kind"],
                }
            )

        # 同事务写满覆盖：缺员/越界/held_n 分叉都会抛 CoverageError → 整趟回滚。
        cov_report = cov.record_run(
            conn,
            scan_id,
            scanned_cells=universe,
            hits=hits,
            scanned_at=scanned_at,
            held_n=len(hits),
        )
        conn.execute(f"RELEASE {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO {sp}")
        conn.execute(f"RELEASE {sp}")
        raise

    return {
        "scan_id": scan_id,
        "scanned_at": scanned_at,
        "found": len(ghosts),
        "quarantined_new": quarantined_new,
        "already_held": already_held,
        # 回执不再只有 held_n：已扫/命中成员同趟落库，覆盖可复核。
        "held_n": len(hits),
        "scanned_n": cov_report["scanned_n"],
        "hit_n": cov_report["hit_n"],
        "scan_universe": universe,
        "coverage": {
            "run_id": cov_report["run_id"],
            "complete": cov_report["complete"],
            "scanned_cells": cov_report["scanned_cells"],
            "hit_cells": cov_report["hit_cells"],
            "universe_digest": cov_report["universe_digest"],
            "verified": cov_report["verification"]["ok"],
        },
        # 显式声明：扫描不改正式态，置位失败闭；处置规则是后续题目的边界。
        "formal_state_mutated": False,
        "ghosts": ghosts,
    }


def ghost_cell_flags(conn) -> dict:
    """格阵协同：返回 cell_id -> 幽灵标记（含 open 占用计数），供 /api/bay 实时标注。

    若存在覆盖跑次，标记额外回链最近一趟的覆盖（scanned / 命中成员 / 覆盖是否完整），
    格阵角标据此可核：被扫过、且该格在本趟命中成员里，才算"覆盖已证"。
    """
    latest = cov.latest_run_id(conn)
    report = cov.coverage(conn, latest) if latest else None
    covered = {h["cell_id"]: h for h in report["hits"]} if report else {}
    coverage_ok = bool(report and report["complete"])
    flags = {}
    for g in find_inconsistencies(conn):
        hit = covered.get(g["cell_id"])
        flags[g["cell_id"]] = {
            "ghost": True,
            "kind": g["kind"],
            "fingerprint": g["fingerprint"],
            "open_occupancy_count": len(g["open_occupancies"]),
            "coverage": {
                "run_id": latest,
                "covered": hit is not None,
                "complete": coverage_ok,
                "quarantine_id": hit["quarantine_id"] if hit else None,
                "fingerprint_agrees": bool(hit and hit["fingerprint"] == g["fingerprint"]),
            },
        }
    return flags


def reconcile_coverage(conn) -> dict:
    """占用对账协同（只读）：把活态幽灵与最近跑次覆盖逐格对撞。

    - 活态不一致对必须落在该跑次已扫集合内（扫描范围对得上占用账）；
    - 活态幽灵若已被本趟覆盖命中，则覆盖指纹须与活态指纹一致；
    - 提交 ``held_n`` 必须等于库内该跑次命中成员数（回执不分叉）。
    """
    latest = cov.latest_run_id(conn)
    live = find_inconsistencies(conn)
    live_cells = {g["cell_id"] for g in live}
    if latest is None:
        return {
            "run_id": None,
            "aligned": False,
            "live_inconsistent": len(live),
            "reason": "尚无覆盖跑次，扫描范围无法对账",
            "live_not_scanned": sorted(live_cells),
        }
    report = cov.coverage(conn, latest)
    scanned = set(report["scanned_cells"])
    covered_hits = {h["cell_id"]: h for h in report["hits"]}
    not_scanned = sorted(live_cells - scanned)
    hit_outside = sorted(set(covered_hits) - scanned)
    fp_mismatch = sorted(
        g["cell_id"]
        for g in live
        if g["cell_id"] in covered_hits
        and covered_hits[g["cell_id"]]["fingerprint"] != g["fingerprint"]
    )
    unresolved = sorted(live_cells - set(covered_hits))
    return {
        "run_id": latest,
        "live_inconsistent": len(live),
        "scanned_n": report["scanned_n"],
        "hit_n": report["hit_n"],
        "held_n": report["held_n"],
        "live_not_scanned": not_scanned,
        "hit_outside_scanned": hit_outside,
        "fingerprint_mismatch": fp_mismatch,
        "live_not_covered_this_run": unresolved,
        "held_n_matches_hits": report["held_n"] == report["hit_n"],
        "coverage_complete": report["complete"],
        "verification_ok": report["verification"]["ok"],
        "aligned": (
            not not_scanned
            and not hit_outside
            and not fp_mismatch
            and report["held_n"] == report["hit_n"]
            and report["complete"]
            and report["verification"]["ok"]
        ),
    }


def list_ghosts(conn) -> dict:
    """核证只读接口：活态不一致对 + 全部历史幽灵搁置行。

    - 活态对：currently_inconsistent=true，带两侧实时快照与搁置认领信息；
    - 历史搁置：同一指纹已不在活态（被外部处置或状态再变），currently_inconsistent=false，
      搁置行与快照仍在，审计边不消失。
    """
    live = find_inconsistencies(conn)
    held = _held_by_fingerprint(conn)
    live_fps = {g["fingerprint"] for g in live}

    live_out = []
    for g in live:
        rec = dict(g)
        rec["currently_inconsistent"] = True
        rec["quarantine"] = held.get(g["fingerprint"])
        live_out.append(rec)

    history_out = []
    for fp, q in held.items():
        if fp in live_fps:
            continue
        member = conn.execute(
            "SELECT member_json FROM quarantine WHERE id=?", (q["id"],)
        ).fetchone()
        try:
            detail = json.loads(member["member_json"]) if member else {}
        except json.JSONDecodeError:
            detail = {}
        history_out.append(
            {
                "currently_inconsistent": False,
                "quarantine_id": q["id"],
                "cell_id": q["cell_id"],
                "fingerprint": fp,
                "held_at": q["created_at"],
                "scan_id": q["scan_id"],
                "snapshot_at_hold": detail,
            }
        )

    return {
        "live_inconsistent": live_out,
        "historical_holds": history_out,
        "coverage": cov.coverage(conn),
        "counts": {
            "live": len(live_out),
            "historical_only": len(history_out),
            "total_ghost_holds": len(held),
        },
    }
