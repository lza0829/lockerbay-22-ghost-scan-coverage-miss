"""ghost_occupy_hold 本地集成测试（SQLite 夹具，无外部服务）。

覆盖题目四条要求：
1. 幽灵扫描发现不一致 → 进 quarantine 搁置、保留两侧快照、reason='ghost'，不静默改一侧；
2. 失败闭：扫描只 INSERT 搁置行，cells / occupancies 正式态扫描前后逐行不变，无 DELETE/强改；
3. 只读核证列出不一致对；扫描入口、搁置、格阵、核证四者协同；
4. 预置一格幽灵态：扫描前格阵可见不一致，扫描后搁置可见且格阵仍不一致。
另含：重复扫描幂等、第二类漂移（open 占用但格口 free）、历史搁置审计边、旧库迁移。
"""

import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# 独立临时库 + 仓库夹具，隔离开发库
_TMP = tempfile.mkdtemp(prefix="lockerbay_ghost_test_")
os.environ["DATA_DIR"] = _TMP
os.environ["FIXTURES_DIR"] = str(BACKEND.parent / "fixtures")

from app.db import connect  # noqa: E402
from app.modules import ghost_occupy_hold as gh  # noqa: E402
from app.seed import SCHEMA, _migrate_quarantine, init_db  # noqa: E402
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


def snapshot_formal(conn) -> dict:
    return {
        "cells": [dict(r) for r in conn.execute("SELECT * FROM cells ORDER BY id")],
        "occupancies": [dict(r) for r in conn.execute("SELECT * FROM occupancies ORDER BY id")],
        "events": [dict(r) for r in conn.execute("SELECT * FROM parcel_events ORDER BY id")],
    }


print("== 准备：init_db 载入本地夹具（含预置幽灵态） ==")
init_db()
conn = connect()

print("-- 要求4：预置一格幽灵态，扫描前即可见 --")
ghost_cell = conn.execute("SELECT status FROM cells WHERE id='CAB-A-04'").fetchone()
check("CAB-A-04 被预置为 occupied", ghost_cell["status"] == "occupied")
open_rows = conn.execute(
    "SELECT COUNT(*) AS c FROM occupancies WHERE cell_id='CAB-A-04' AND status='occupied'"
).fetchone()["c"]
check("CAB-A-04 无任何 open 占用行", open_rows == 0)

incons = gh.find_inconsistencies(conn)
check("只读扫描发现 1 个不一致对", len(incons) == 1, f"got {len(incons)}")
check(
    "漂移类型=格口占用态无 open 占用",
    incons[0]["kind"] == gh.KIND_CELL_OCCUPIED_NO_OPEN,
)
check("扫描结果带两侧快照", "cell_snapshot" in incons[0] and "open_occupancies" in incons[0])
check("扫描结果带指纹", len(incons[0]["fingerprint"]) == 64)

matrix = bay_svc.cabinet_matrix(conn)
flagged = [c for cab in matrix for c in cab["cells"] if c.get("ghost")]
check("格阵实时标注幽灵格口", len(flagged) == 1 and flagged[0]["id"] == "CAB-A-04")
ov = bay_svc.overview(conn)
check("overview 活态幽灵计数=1、搁置=0", ov["ghost_live"] == 1 and ov["ghost_holds"] == 0)

print("-- 要求2（失败闭）：扫描不改正式态 --")
before = snapshot_formal(conn)
report = gh.scan_ghosts(conn)
conn.commit()
after = snapshot_formal(conn)
check("扫描后 cells/occupancies/events 逐行不变", before == after)
check("报告显式声明 formal_state_mutated=False", report["formal_state_mutated"] is False)
check("报告 found=1 / 新搁置=1", report["found"] == 1 and report["quarantined_new"] == 1)

print("-- 要求1：搁置落库（quarantine 挂 cell_id、两侧快照、原因=ghost） --")
q = conn.execute(
    "SELECT * FROM quarantine WHERE reason='ghost' ORDER BY id"
).fetchall()
check("quarantine 恰有 1 行 ghost 搁置", len(q) == 1)
row = dict(q[0])
check("搁置行挂 cell_id", row["cell_id"] == "CAB-A-04")
check("搁置行挂 scan_id", bool(row["scan_id"]) and row["scan_id"] == report["scan_id"])
check("搁置行挂 fingerprint", row["fingerprint"] == incons[0]["fingerprint"])
member = json.loads(row["member_json"])
check("member_json 保留格口侧快照", member["cell_side"]["id"] == "CAB-A-04")
check("member_json 保留占用侧快照", "occupancy_side" in member)
check("member_json 记录原因 kind", member["kind"] == gh.KIND_CELL_OCCUPIED_NO_OPEN)
check("不止于日志：确有持久化审计行", row["id"] is not None)

print("-- 要求4 续：扫描后格阵仍能看出不一致（未被抹平） --")
still = gh.find_inconsistencies(conn)
check("正式态仍不一致（活态幽灵还在）", len(still) == 1)
matrix2 = bay_svc.cabinet_matrix(conn)
flagged2 = [c for cab in matrix2 for c in cab["cells"] if c.get("ghost")]
check("格阵仍标注幽灵", len(flagged2) == 1)
check("CAB-A-04 仍是 occupied", flagged2[0]["status"] == "occupied")

print("-- 幂等：重复扫描不产生重复搁置 --")
report2 = gh.scan_ghosts(conn)
conn.commit()
qcount = conn.execute("SELECT COUNT(*) AS c FROM quarantine WHERE reason='ghost'").fetchone()["c"]
check("二次扫描新增搁置=0", report2["quarantined_new"] == 0 and report2["already_held"] == 1)
check("quarantine 仍只有 1 行", qcount == 1)

print("-- 要求3：只读核证接口列出不一致对并认领搁置 --")
verdict = gh.list_ghosts(conn)
check("核证 live=1 / 历史=0 / 总搁置=1", verdict["counts"] == {"live": 1, "historical_only": 0, "total_ghost_holds": 1})
live0 = verdict["live_inconsistent"][0]
check("活态对 currently_inconsistent=true", live0["currently_inconsistent"] is True)
check("活态对回链搁置行 id", live0["quarantine"] and live0["quarantine"]["id"] == row["id"])

print("-- 第二类漂移：有 open 占用但格口已 free --")
conn.execute("UPDATE cells SET status='free' WHERE id='CAB-A-02'")  # CAB-A-02 有 open 占用 OCC-CAB-A-02-E2
kinds = {g["kind"] for g in gh.find_inconsistencies(conn)}
check("两类漂移同时被发现", kinds == {gh.KIND_CELL_OCCUPIED_NO_OPEN, gh.KIND_OPEN_BUT_CELL_FREE})
r3 = gh.scan_ghosts(conn)
conn.commit()
check("第二次扫描新增 1 行搁置（新指纹）", r3["quarantined_new"] == 1 and r3["found"] == 2)
check("quarantine 累计 2 行 ghost", conn.execute(
    "SELECT COUNT(*) AS c FROM quarantine WHERE reason='ghost'").fetchone()["c"] == 2)

print("-- 审计边：状态再变后旧搁置保留为历史 --")
# 外部处置（非扫描）：把 CAB-A-04 补成 free，使其不再活态漂移；旧搁置行不得消失
conn.execute("UPDATE cells SET status='free' WHERE id='CAB-A-04'")
conn.commit()
verdict2 = gh.list_ghosts(conn)
live_cells = {g["cell_id"] for g in verdict2["live_inconsistent"]}
check("CAB-A-04 退出活态（剩 CAB-A-02）", live_cells == {"CAB-A-02"})
hist_cells = {h["cell_id"] for h in verdict2["historical_holds"]}
check("CAB-A-04 旧搁置保留在历史区", "CAB-A-04" in hist_cells)
check("总搁置行数不减少（审计边不断）", verdict2["counts"]["total_ghost_holds"] == 2)
check("历史行带搁置时两侧快照", bool(verdict2["historical_holds"][0]["snapshot_at_hold"].get("cell_side")))

print("-- 迁移：旧版 quarantine（无新列）可平滑升级且数据不丢 --")
conn2 = connect()
conn2.execute("DROP TABLE IF EXISTS quarantine")
conn2.execute(
    "CREATE TABLE quarantine (id INTEGER PRIMARY KEY AUTOINCREMENT, reason TEXT NOT NULL, "
    "member_json TEXT NOT NULL, created_at TEXT NOT NULL)"
)
conn2.execute("INSERT INTO quarantine(reason, member_json, created_at) VALUES ('legacy','{}','2026-11-01T00:00:00Z')")
_migrate_quarantine(conn2)
cols = {r["name"] for r in conn2.execute("PRAGMA table_info(quarantine)")}
check("迁移补齐 cell_id/scan_id/fingerprint 三列", {"cell_id", "scan_id", "fingerprint"} <= cols)
legacy = conn2.execute("SELECT * FROM quarantine WHERE reason='legacy'").fetchone()
check("迁移不丢旧数据", legacy is not None and legacy["member_json"] == "{}")
conn2.close()

print("-- 源码护栏：模块自身不含对正式态表的 DELETE/UPDATE --")
import ast  # noqa: E402

src = (BACKEND / "app" / "modules" / "ghost_occupy_hold" / "__init__.py").read_text(encoding="utf-8")
tree = ast.parse(src)

# 收集所有 conn.execute(...) 调用里实际下发的 SQL 字面量（跳过文档/注释中的文字）
sqls = []
for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
        continue
    if not node.args or not isinstance(node.args[0], ast.Constant):
        continue
    arg0 = node.args[0].value
    if isinstance(arg0, str) and any(k in arg0.upper() for k in ("SELECT", "INSERT", "UPDATE", "DELETE")):
        sqls.append(" ".join(arg0.upper().split()))

check("下发 SQL 中无 DELETE", not any(s.startswith("DELETE") for s in sqls))
check("下发 SQL 中无 UPDATE", not any(s.startswith("UPDATE") for s in sqls))
insert_sqls = [s for s in sqls if s.startswith("INSERT")]
check("仅一条 INSERT 且目标为 quarantine",
      len(insert_sqls) == 1 and insert_sqls[0].startswith("INSERT OR IGNORE INTO QUARANTINE"),
      str(insert_sqls))
check("无 ast.Delete 节点", not any(isinstance(n, ast.Delete) for n in ast.walk(tree)))

conn.close()
print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
