"""幽灵扫描覆盖成员证明（ghost_scan_coverage）。

领域问题：幽灵扫描只把「格口 occupied 但无 open 占用」一类异常打进搁置，回执却只有
``held_n``；本趟到底扫过哪些格口、命中了哪些格口没有落库，重跑无法证明"哪些格被扫过"，
占用账对账也对不上扫描范围。

本题的落库形态（二选一，本实现选「独立成员表」）：

* ``ghost_scan_runs``    —— 一跑次一行，不可变：run_id / scanned_n / hit_n / held_n /
  完整标记 / 扫描全域快照与摘要；
* ``ghost_scan_members`` —— 一跑次 × 一格口一行，不可变：scanned=1 表示本趟覆盖该格，
  hit=1 时必带本趟搁置键（quarantine_id + fingerprint + kind），与 ``quarantine`` 互证。

提交形态（二选一，本实现选「扫描同事务写满覆盖成员，缺一回滚」）：

* 扫描函数在**同一个 SQLite 事务**里写搁置行与覆盖跑次/成员；任何成员校验失败或写入
  失败都 ``ROLLBACK TO SAVEPOINT``，搁置行与覆盖行一起消失，绝不留半截覆盖；
* 只有覆盖成员完整落库后，扫描才对外报告成功（报告里的 ``coverage.complete``）。

硬约束（全部在 ``write_run`` 落库前校验，并在落库后回读复核）：

1. 禁止扫描成功而覆盖缺员：members 的 cell_id 集合必须与 scanned 集合逐格相等；
2. 禁止命中落在已扫集合外：hits ⊆ scanned；
3. 禁止 held_n 与命中数分叉：held_n == hit_n == hit 成员数；
4. 禁止失败残留半截覆盖：写入包在 SAVEPOINT 内，失败回滚且不释放任何行。

只读核证接口（基线所无）：``coverage`` / ``verify_run`` / ``list_runs``，只有 SELECT。
"""

import hashlib
import json
from datetime import datetime, timezone

STATUS = "implemented"


class CoverageError(RuntimeError):
    """覆盖成员证明校验失败；调用方必须回滚整趟扫描。"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _universe_digest(cell_ids: list[str]) -> str:
    raw = "\n".join(sorted(cell_ids))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _uniq(seq) -> list[str]:
    out, seen = [], set()
    for item in seq or []:
        key = str(item)
        if key in seen:
            raise CoverageError(f"覆盖成员重复：{key}")
        seen.add(key)
        out.append(key)
    return out


def build_members(scanned_cells, hits) -> list[dict]:
    """由已扫集合 + 命中集合生成成员行；命中格必须落在已扫集合内。"""
    scanned = _uniq(scanned_cells)
    scanned_set = set(scanned)
    hit_map: dict[str, dict] = {}
    for h in hits or []:
        cell_id = str(h.get("cell_id"))
        if cell_id in hit_map:
            raise CoverageError(f"命中格重复：{cell_id}")
        if cell_id not in scanned_set:
            raise CoverageError(f"命中格 {cell_id} 不在本趟已扫集合内")
        if h.get("quarantine_id") is None or not h.get("fingerprint"):
            raise CoverageError(f"命中格 {cell_id} 缺本趟搁置键，拒绝写入覆盖")
        hit_map[cell_id] = h
    return [
        {
            "cell_id": cell_id,
            "scanned": 1,
            "hit": 1 if cell_id in hit_map else 0,
            "quarantine_id": hit_map[cell_id]["quarantine_id"] if cell_id in hit_map else None,
            "fingerprint": hit_map[cell_id]["fingerprint"] if cell_id in hit_map else None,
            "kind": hit_map[cell_id].get("kind") if cell_id in hit_map else None,
        }
        for cell_id in scanned
    ]


def validate_run(scanned_cells, members, hits, held_n=None) -> list[tuple[str, bool, str]]:
    """落库前的纯内存校验；返回 [(检查名, 是否通过, 细节)]。"""
    checks: list[tuple[str, bool, str]] = []

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    try:
        scanned = _uniq(scanned_cells)
        scanned_ok = True
    except CoverageError as exc:
        scanned, scanned_ok = [], False
        add("已扫集合无重复", False, str(exc))
    if scanned_ok:
        add("已扫集合无重复", True, f"{len(scanned)} 格")

    scanned_set = set(scanned)
    member_cells = [m.get("cell_id") for m in members or []]
    try:
        _uniq(member_cells)
        members_ok = True
    except CoverageError as exc:
        members_ok = False
        add("成员格无重复", False, str(exc))
    if members_ok:
        add("成员格无重复", True, f"{len(member_cells)} 行")

    member_set = set(member_cells)
    missing = sorted(scanned_set - member_set)
    extra = sorted(member_set - scanned_set)
    add(
        "覆盖无缺员、无越界成员",
        not missing and not extra,
        f"缺员={missing} 越界={extra}",
    )
    add(
        "每行均为已扫",
        all(int(m.get("scanned", 0)) == 1 for m in members or []),
        "",
    )

    hit_cells = [str(h.get("cell_id")) for h in hits or []]
    add("命中格去重", len(hit_cells) == len(set(hit_cells)), f"{len(hit_cells)} 命中")
    outside = sorted(set(hit_cells) - scanned_set)
    add("命中 ⊆ 已扫", not outside, f"越界={outside}")

    hit_members = [m for m in members or [] if int(m.get("hit", 0)) == 1]
    add(
        "命中成员与命中集合一致",
        sorted(m.get("cell_id") for m in hit_members) == sorted(set(hit_cells)),
        f"成员命中={len(hit_members)} 命中集={len(set(hit_cells))}",
    )
    add(
        "命中成员均带搁置键",
        all(m.get("quarantine_id") is not None and m.get("fingerprint") for m in hit_members),
        "",
    )
    expected_held = len(set(hit_cells)) if hits is not None else 0
    if held_n is None:
        held_n = expected_held
    add(
        "held_n 与命中数不分叉",
        int(held_n) == expected_held == len(hit_members),
        f"held_n={held_n} 命中={expected_held} 成员={len(hit_members)}",
    )
    add(
        "命中指纹无重复",
        len({h.get("fingerprint") for h in hits or []}) == len(hit_cells),
        "",
    )
    return checks


def _all_ok(checks) -> bool:
    return all(ok for _, ok, _ in checks)


def write_run(
    conn,
    run_id: str,
    scanned_cells,
    members,
    hits,
    scanned_at: str | None = None,
    held_n: int | None = None,
) -> dict:
    """原子写入一跑次的覆盖跑次 + 全部成员。

    先做内存校验，再在 SAVEPOINT 内写库并回读复核；任一失败 ``ROLLBACK TO``，
    本趟覆盖（以及同一事务里的搁置写入）一并回滚，不留半截覆盖。
    """
    scanned_at = scanned_at or _now()
    scanned = _uniq(scanned_cells)
    members = [dict(m) for m in (members or [])]
    hits = [dict(h) for h in (hits or [])]

    checks = validate_run(scanned, members, hits, held_n)
    if not _all_ok(checks):
        raise CoverageError("覆盖成员校验失败：" + "; ".join(d for _, ok, d in checks if not ok))

    hit_n = len({str(h["cell_id"]) for h in hits})
    if held_n is None:
        held_n = hit_n
    universe_json = json.dumps(sorted(scanned), ensure_ascii=False)
    ledger_json = json.dumps(
        sorted(
            [
                {
                    "cell_id": str(h["cell_id"]),
                    "quarantine_id": h["quarantine_id"],
                    "fingerprint": h["fingerprint"],
                    "kind": h.get("kind"),
                }
                for h in hits
            ],
            key=lambda x: x["cell_id"],
        ),
        ensure_ascii=False,
        sort_keys=True,
    )

    sp = "sp_coverage_" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
    conn.execute(f"SAVEPOINT {sp}")
    try:
        conn.execute(
            "INSERT INTO ghost_scan_runs(run_id, scanned_at, scanned_n, hit_n, held_n, "
            "complete, universe_json, universe_digest, ledger_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                scanned_at,
                len(scanned),
                hit_n,
                int(held_n),
                1,
                universe_json,
                _universe_digest(scanned),
                ledger_json,
                scanned_at,
            ),
        )
        for m in members:
            conn.execute(
                "INSERT INTO ghost_scan_members(run_id, cell_id, scanned, hit, quarantine_id, "
                "fingerprint, kind) VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    m["cell_id"],
                    int(m.get("scanned", 1)),
                    int(m.get("hit", 0)),
                    m.get("quarantine_id"),
                    m.get("fingerprint"),
                    m.get("kind"),
                ),
            )
        # 回读复核：以库里真实行数为准，防止"声明写满、实际缺员"。
        verdict = verify_run(conn, run_id)
        if not verdict["ok"]:
            raise CoverageError(
                "覆盖落库回读不复核：" + "; ".join(c["detail"] for c in verdict["checks"] if not c["ok"])
            )
        conn.execute(f"RELEASE {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO {sp}")
        conn.execute(f"RELEASE {sp}")
        raise

    return coverage(conn, run_id)


def record_run(conn, run_id: str, scanned_cells, hits, scanned_at=None, held_n=None) -> dict:
    """扫描入口调用：由已扫/命中集合生成成员并原子落库。"""
    members = build_members(scanned_cells, hits)
    return write_run(
        conn,
        run_id,
        scanned_cells,
        members,
        hits,
        scanned_at=scanned_at,
        held_n=held_n,
    )


def _run_row(conn, run_id: str):
    return conn.execute(
        "SELECT run_id, scanned_at, scanned_n, hit_n, held_n, complete, universe_json, "
        "universe_digest, ledger_json, created_at FROM ghost_scan_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()


def latest_run_id(conn) -> str | None:
    row = conn.execute(
        "SELECT run_id FROM ghost_scan_runs ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return row["run_id"] if row else None


def _members(conn, run_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT run_id, cell_id, scanned, hit, quarantine_id, fingerprint, kind "
        "FROM ghost_scan_members WHERE run_id=? ORDER BY cell_id",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def verify_run(conn, run_id: str) -> dict:
    """只读核证：从已落库的跑次/成员回读，逐条核对覆盖硬约束。"""
    checks: list[dict] = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    run = _run_row(conn, run_id)
    if run is None:
        add("跑次存在", False, f"run_id={run_id} 未落库")
        return {"run_id": run_id, "ok": False, "checks": checks, "counts": None}

    members = _members(conn, run_id)
    hit_members = [m for m in members if int(m["hit"]) == 1]
    scanned_members = [m for m in members if int(m["scanned"]) == 1]
    universe = set(json.loads(run["universe_json"] or "[]"))
    member_cells = {m["cell_id"] for m in members}

    add("跑次标记完整", int(run["complete"]) == 1, f"complete={run['complete']}")
    add(
        "成员行数=声明已扫数",
        len(members) == int(run["scanned_n"]) == len(universe),
        f"成员={len(members)} scanned_n={run['scanned_n']} 全域={len(universe)}",
    )
    add(
        "覆盖无缺员、无越界成员",
        member_cells == universe,
        f"缺员={sorted(universe - member_cells)} 越界={sorted(member_cells - universe)}",
    )
    add(
        "全部成员均为已扫",
        len(scanned_members) == len(members),
        f"已扫成员={len(scanned_members)} 总成员={len(members)}",
    )
    add(
        "覆盖摘要与成员一致",
        _universe_digest(sorted(member_cells)) == run["universe_digest"],
        f"digest={run['universe_digest'][:12]}…",
    )
    add(
        "hit_n 与命中成员数一致",
        int(run["hit_n"]) == len(hit_members),
        f"hit_n={run['hit_n']} 命中成员={len(hit_members)}",
    )
    add(
        "held_n 与命中数不分叉",
        int(run["held_n"]) == int(run["hit_n"]) == len(hit_members),
        f"held_n={run['held_n']} hit_n={run['hit_n']} 命中成员={len(hit_members)}",
    )
    add(
        "命中 ⊆ 已扫",
        {m["cell_id"] for m in hit_members} <= member_cells,
        "",
    )
    add(
        "命中成员均带搁置键",
        all(m["quarantine_id"] is not None and m["fingerprint"] for m in hit_members),
        "",
    )

    # 与 quarantine 互证：每条命中成员都必须对得上一条 reason='ghost' 的搁置行，
    # 且格口/指纹一致；本趟 ledger 与成员也不得漂移。
    bad = []
    for m in hit_members:
        row = conn.execute(
            "SELECT id, cell_id, fingerprint, reason FROM quarantine WHERE id=?",
            (m["quarantine_id"],),
        ).fetchone()
        if row is None:
            bad.append(f"{m['cell_id']}:搁置行缺失")
            continue
        if row["reason"] != "ghost":
            bad.append(f"{m['cell_id']}:原因={row['reason']}")
        if row["cell_id"] != m["cell_id"]:
            bad.append(f"{m['cell_id']}:搁置格口={row['cell_id']}")
        if row["fingerprint"] != m["fingerprint"]:
            bad.append(f"{m['cell_id']}:指纹不一致")
        if m["kind"] and row["reason"] == "ghost":
            detail = conn.execute(
                "SELECT member_json FROM quarantine WHERE id=?", (m["quarantine_id"],)
            ).fetchone()
            try:
                held_kind = json.loads(detail["member_json"]).get("kind") if detail else None
            except (json.JSONDecodeError, TypeError):
                held_kind = None
            if held_kind and held_kind != m["kind"]:
                bad.append(f"{m['cell_id']}:漂移类型不一致")
    add("命中与搁置键互证", not bad, "; ".join(bad))

    ledger = json.loads(run["ledger_json"] or "[]")
    ledger_cells = {str(e.get("cell_id")) for e in ledger}
    add(
        "跑次台账与命中成员一致",
        ledger_cells == {m["cell_id"] for m in hit_members},
        f"台账={sorted(ledger_cells)} 成员={sorted(m['cell_id'] for m in hit_members)}",
    )

    return {
        "run_id": run_id,
        "ok": _all_ok((c["check"], c["ok"], c["detail"]) for c in checks),
        "checks": checks,
        "counts": {
            "scanned_n": int(run["scanned_n"]),
            "hit_n": int(run["hit_n"]),
            "held_n": int(run["held_n"]),
        },
    }


def coverage(conn, run_id: str | None = None) -> dict:
    """只读覆盖证明接口：返回指定（缺省最新）跑次的已扫/命中成员与核证结果。"""
    resolved = run_id or latest_run_id(conn)
    if resolved is None:
        return {
            "run_id": None,
            "scanned_cells": [],
            "hit_cells": [],
            "scanned_n": 0,
            "hit_n": 0,
            "held_n": 0,
            "complete": False,
            "verification": {"run_id": None, "ok": False, "checks": [], "counts": None},
        }
    run = _run_row(conn, resolved)
    if run is None:
        return {
            "run_id": resolved,
            "scanned_cells": [],
            "hit_cells": [],
            "scanned_n": 0,
            "hit_n": 0,
            "held_n": 0,
            "complete": False,
            "verification": verify_run(conn, resolved),
        }
    members = _members(conn, resolved)
    scanned_cells = [m["cell_id"] for m in members if int(m["scanned"]) == 1]
    hit_cells = [m["cell_id"] for m in members if int(m["hit"]) == 1]
    return {
        "run_id": resolved,
        "scanned_at": run["scanned_at"],
        "scanned_cells": scanned_cells,
        "hit_cells": hit_cells,
        "scanned_n": int(run["scanned_n"]),
        "hit_n": int(run["hit_n"]),
        "held_n": int(run["held_n"]),
        "complete": bool(run["complete"]) and len(scanned_cells) == int(run["scanned_n"]),
        "universe_digest": run["universe_digest"],
        "hits": [
            {
                "cell_id": m["cell_id"],
                "quarantine_id": m["quarantine_id"],
                "fingerprint": m["fingerprint"],
                "kind": m["kind"],
            }
            for m in members
            if int(m["hit"]) == 1
        ],
        "verification": verify_run(conn, resolved),
    }


def list_runs(conn, limit: int = 20) -> list[dict]:
    """只读：覆盖跑次列表（最新在前），每行带核证页码与计数。"""
    rows = conn.execute(
        "SELECT run_id, scanned_at, scanned_n, hit_n, held_n, complete, universe_digest "
        "FROM ghost_scan_runs ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(r) for r in rows]


def scan_window(conn) -> dict:
    """供占用对账/格阵读取：当前库全域格口与本趟应扫范围摘要（只读）。"""
    cells = [r["id"] for r in conn.execute("SELECT id FROM cells ORDER BY id").fetchall()]
    return {"cells": cells, "cells_n": len(cells), "universe_digest": _universe_digest(cells)}
