"""ghost_scan_coverage 本地集成测试（SQLite 夹具，无外部服务）。

覆盖本题四条硬约束 + 两条"择一"形态：

1. 扫描标成功 → 同趟写满覆盖成员（run_id / 已扫集合 / 命中集合），回读可证；
2. 命中 ⊆ 已扫，且命中与 quarantine 搁置键互证；
3. held_n == 命中数，回执与库内成员不分叉；
4. 中途失败（人为少写一员 / 命中越界）整趟回滚，不留半截覆盖；
5. 造一幽灵格：覆盖含该格、格阵角标可核；人为少写一员必须失败。
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

_TMP = tempfile.mkdtemp(prefix="lockerbay_coverage_test_")
os.environ["DATA_DIR"] = _TMP
os.environ["FIXTURES_DIR"] = str(BACKEND.parent / "fixtures")

from app.db import connect  # noqa: E402
from app.modules import ghost_occupy_hold as gh  # noqa: E402
from app.modules import ghost_scan_coverage as cov  # noqa: E402
from app.seed import init_db  # noqa: E402
from app.services import bay as bay_svc  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def counts(conn) -> tuple:
    return (
        conn.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"],
        conn.execute("SELECT COUNT(*) AS c FROM ghost_scan_runs").fetchone()["c"],
        conn.execute("SELECT COUNT(*) AS c FROM ghost_scan_members").fetchone()["c"],
    )


print("== 准备：init_db 载入夹具（CAB-A-04 预置幽灵：occupied 但无 open 占用） ==")
init_db()
conn = connect()
ghost_cell = "CAB-A-04"
total_cells = conn.execute("SELECT COUNT(*) AS c FROM cells").fetchone()["c"]
check("夹具全域格口=6", total_cells == 6, f"got {total_cells}")
check("扫描前无覆盖跑次", cov.latest_run_id(conn) is None)

print("-- 要求1/2/3：扫描成功 → 同趟写满覆盖成员，回执可证 --")
report = gh.scan_ghosts(conn)
conn.commit()
run_id = report["scan_id"]
cv = report["coverage"]
check("回执带 run_id", cv["run_id"] == run_id)
check("回执带 scanned_n=全域格口数", report["scanned_n"] == total_cells, str(report["scanned_n"]))
check("命中集合含幽灵格且仅它", cv["hit_cells"] == [ghost_cell], str(cv["hit_cells"]))
check("held_n == 命中数", report["held_n"] == len(cv["hit_cells"]) == 1)
check("回执显式 verified", cv["verified"] is True)
check("回执 complete", cv["complete"] is True)

scanned_db = cov.coverage(conn, run_id)
check("只读接口能按 run_id 取回已扫集合", len(scanned_db["scanned_cells"]) == total_cells)
check("已扫集合逐格 = 全域", set(scanned_db["scanned_cells"]) == {
    r["id"] for r in conn.execute("SELECT id FROM cells")})
check("命中 ⊆ 已扫", set(scanned_db["hit_cells"]) <= set(scanned_db["scanned_cells"]))
check("覆盖含幽灵格", ghost_cell in scanned_db["hit_cells"])
check("核证 overall ok", scanned_db["verification"]["ok"] is True)
check("核证逐项全通过", all(c["ok"] for c in scanned_db["verification"]["checks"]))

m = conn.execute(
    "SELECT * FROM ghost_scan_members WHERE run_id=? AND cell_id=?", (run_id, ghost_cell)
).fetchone()
check("命中成员行 hit=1", m["hit"] == 1 and m["scanned"] == 1)
check("命中成员挂本趟搁置键", m["quarantine_id"] is not None and bool(m["fingerprint"]))
q = conn.execute("SELECT * FROM quarantine WHERE id=?", (m["quarantine_id"],)).fetchone()
check("互证：搁置行格口/指纹/原因一致",
      q["cell_id"] == ghost_cell and q["fingerprint"] == m["fingerprint"] and q["reason"] == "ghost")
check("互证：搁置行 scan_id=本趟", q["scan_id"] == run_id)

print("-- 要求5：造一幽灵格，格阵角标可核 --")
matrix = bay_svc.cabinet_matrix(conn)
flagged = [c for cab in matrix for c in cab["cells"] if c.get("ghost")]
check("格阵标注该幽灵格", len(flagged) == 1 and flagged[0]["id"] == ghost_cell)
flag_cov = flagged[0]["ghost"]["coverage"]
check("角标回链覆盖跑次", flag_cov["run_id"] == run_id)
check("角标标 covered=True（本趟已覆盖该格）", flag_cov["covered"] is True)
check("角标指纹互证", flag_cov["fingerprint_agrees"] is True)
check("角标覆盖完整", flag_cov["complete"] is True)
uncovered = [c for cab in matrix for c in cab["cells"]
             if c.get("ghost") and not c["ghost"]["coverage"]["covered"]]
check("无幽灵格漏覆盖", not uncovered)

print("-- 占用对账：扫描范围与占用账一致 --")
rec = gh.reconcile_coverage(conn)
check("对账 aligned", rec["aligned"] is True, json.dumps(rec, ensure_ascii=False))
check("活态幽灵全在扫描范围内", rec["live_not_scanned"] == [])
check("held_n 与命中数一致（对账侧）", rec["held_n_matches_hits"] is True)

print("-- 重复扫描：再落一趟覆盖，旧跑次不可变 --")
report2 = gh.scan_ghosts(conn)
conn.commit()
check("第二趟也写满覆盖成员", report2["coverage"]["verified"] is True)
check("第二趟 still hit=1（已搁置幂等认领）", report2["coverage"]["hit_cells"] == [ghost_cell])
check("两趟跑次并存、成员各 6 行",
      conn.execute("SELECT COUNT(*) AS c FROM ghost_scan_runs").fetchone()["c"] == 2
      and conn.execute("SELECT COUNT(*) AS c FROM ghost_scan_members").fetchone()["c"] == 12)
first_again = cov.coverage(conn, run_id)
check("旧跑次成员不被改写", first_again["verification"]["ok"] and first_again["hit_n"] == 1)

print("-- 要求4：人为少写一员 → 整趟失败、无半截覆盖 --")
rows = [r["id"] for r in conn.execute("SELECT id FROM cells ORDER BY id")]
try:
    cov.record_run(conn, "RUN-FULL-CONTROL", scanned_cells=rows, hits=[], held_n=0)
    ok_full = True
except cov.CoverageError:
    ok_full = False
check("完整集合可写（对照组）", ok_full)
# 对照组是测试自造的干净跑次，直接清掉，避免干扰后续计数（不属于被测逻辑）。
conn.execute("DELETE FROM ghost_scan_members WHERE run_id='RUN-FULL-CONTROL'")
conn.execute("DELETE FROM ghost_scan_runs WHERE run_id='RUN-FULL-CONTROL'")
conn.commit()

before = counts(conn)
try:
    cov.write_run(
        conn,
        "RUN-MISSING-MEMBER",
        scanned_cells=rows,
        members=[{"cell_id": c, "scanned": 1, "hit": 0} for c in rows[:-1]],  # 少写一员
        hits=[],
        held_n=0,
    )
    raised = False
except cov.CoverageError as exc:
    raised = True
    err = str(exc)
check("缺员被拒（CoverageError）", raised)
check("错误信息点名缺员格", rows[-1] in err)
after = counts(conn)
check("失败后无半截覆盖：跑次/成员计数不变", after == before, f"{before} -> {after}")
check("失败跑次未落库", conn.execute(
    "SELECT 1 FROM ghost_scan_runs WHERE run_id='RUN-MISSING-MEMBER'").fetchone() is None)

print("-- 要求4 续：库内 SQL 约束兜底（命中成员缺搁置键写入失败） --")
try:
    conn.execute(
        "INSERT INTO ghost_scan_members(run_id, cell_id, scanned, hit, quarantine_id, fingerprint, kind) "
        "VALUES (?,?,?,?,?,?,?)",
        (run_id, "CAB-A-01", 1, 1, None, None, None),  # 违反 CHECK(hit=0 OR 搁置键存在)
    )
    blocked = False
except sqlite3.IntegrityError:
    blocked = True
check("CHECK 约束拦住无搁置键的命中成员", blocked)
conn.rollback()

print("-- 命中越界 / held_n 分叉 均被拒 --")
try:
    cov.build_members(rows, [{"cell_id": "CAB-ZZZ-99", "quarantine_id": 1, "fingerprint": "f"}])
    raised = False
except cov.CoverageError:
    raised = True
check("命中落在已扫外被拒", raised)

checks = dict((n, ok) for n, ok, _ in cov.validate_run(
    rows, [{"cell_id": c, "scanned": 1, "hit": 0} for c in rows], [], held_n=3))
check("held_n 与命中数分叉被识别为不通过", checks["held_n 与命中数不分叉"] is False)

print("-- 中途失败回滚：扫描同事务内覆盖写满后抛错 → 搁置也不留 --")
conn2 = connect()
before2 = (
    conn2.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"],
    conn2.execute("SELECT COUNT(*) AS c FROM ghost_scan_runs").fetchone()["c"],
    conn2.execute("SELECT COUNT(*) AS c FROM ghost_scan_members").fetchone()["c"],
)
# 让覆盖层在写库后回读失败：临时删掉成员表里的唯一索引不算数，
# 这里直接在 record_run 里传一个坏成员（命中缺搁置键）触发 build 阶段抛错，
# 模拟"扫描拿到搁置行后、覆盖提交前失败"。
orig = cov.verify_run
cov.verify_run = lambda c, r: {"ok": False, "checks": [{"check": "forced", "ok": False, "detail": "forced"}], "counts": None}
try:
    gh.scan_ghosts(conn2)
    raised = False
except cov.CoverageError:
    raised = True
finally:
    cov.verify_run = orig
after2 = (
    conn2.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"],
    conn2.execute("SELECT COUNT(*) AS c FROM ghost_scan_runs").fetchone()["c"],
    conn2.execute("SELECT COUNT(*) AS c FROM ghost_scan_members").fetchone()["c"],
)
check("覆盖提交失败 → 扫描整趟抛错", raised)
check("同事务搁置行随之回滚", after2[0] == before2[0], f"{before2} -> {after2}")
check("无半截覆盖跑次/成员", after2 == before2, f"{before2} -> {after2}")
check("回滚后连接仍可用", conn2.execute("SELECT 1").fetchone()[0] == 1)

conn.close()
conn2.close()
print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
