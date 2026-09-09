"""SQLite 访问层:单一 schema、上下文管理器、迁移检查、批量写入。

对应评审项:A2(双 schema)、A8(并发策略)、B5(资源泄漏)、B9(无索引/无批量)
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_path TEXT UNIQUE,
    name TEXT,
    metadata TEXT,
    size TEXT,
    capture_time TEXT,
    capture_date TEXT,
    hash TEXT,
    occurrence_count INTEGER DEFAULT 1,
    moved INTEGER DEFAULT 0,
    media_type TEXT DEFAULT 'photo'
)
"""

MIGRATIONS = {
    1: [
        CREATE_TABLE_SQL,
        "CREATE INDEX IF NOT EXISTS idx_photos_hash ON photos(hash)",
        "CREATE INDEX IF NOT EXISTS idx_photos_moved ON photos(moved)",
        "CREATE INDEX IF NOT EXISTS idx_photos_capture_date ON photos(capture_date)",
    ],
}


@contextmanager
def connect(db_path: str):
    """统一连接上下文:异常回滚、自动关闭。

    GUI 多线程场景下每线程各持一个连接,不跨线程共享(规避 check_same_thread)。
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    """初始化/迁移数据库到最新 schema。"""
    with connect(db_path) as conn:
        conn.execute(CREATE_TABLE_SQL)
        # 兼容旧库缺列(db_util 时代的 PRAGMA 迁移逻辑收敛于此)。
        # 必须先补列再建索引,否则旧库会因索引引用不存在的列而报错。
        expected = {
            "name": "TEXT",
            "metadata": "TEXT",
            "size": "TEXT",
            "capture_time": "TEXT",
            "capture_date": "TEXT",
            "hash": "TEXT",
            "occurrence_count": "INTEGER DEFAULT 1",
            "moved": "INTEGER DEFAULT 0",
            "media_type": "TEXT DEFAULT 'photo'",
        }
        columns = {row[1] for row in conn.execute("PRAGMA table_info(photos)")}
        for col, ddl in expected.items():
            if col not in columns:
                conn.execute(f"ALTER TABLE photos ADD COLUMN {col} {ddl}")
        for sql in MIGRATIONS[SCHEMA_VERSION]:
            conn.execute(sql)


def upsert_media(conn, record: dict) -> None:
    """按 full_path 插入/更新一条媒体记录(重扫幂等:不重复计 occurrence_count)。"""
    existing = conn.execute(
        "SELECT id FROM photos WHERE full_path = ?", (record["full_path"],)
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE photos SET name = ?, metadata = ?, size = ?,
               capture_time = ?, capture_date = ?, hash = ?, media_type = ?
               WHERE full_path = ?""",
            (
                record["name"],
                record.get("metadata"),
                record.get("size"),
                record.get("capture_time"),
                record.get("capture_date"),
                record.get("hash"),
                record.get("media_type", "photo"),
                record["full_path"],
            ),
        )
    else:
        conn.execute(
            """INSERT INTO photos (full_path, name, metadata, size, capture_time,
               capture_date, hash, media_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["full_path"],
                record["name"],
                record.get("metadata"),
                record.get("size"),
                record.get("capture_time"),
                record.get("capture_date"),
                record.get("hash"),
                record.get("media_type", "photo"),
            ),
        )


def find_by_hash(conn, file_hash: str) -> list:
    return conn.execute(
        "SELECT full_path, moved FROM photos WHERE hash = ?", (file_hash,)
    ).fetchall()


def find_stale_by_hash(conn, file_hash: str, exclude_path: str) -> str | None:
    """同 hash 且已移动、磁盘上已不存在的陈旧记录路径(文件搬家痕迹)。

    用于重扫时"认领"旧记录:文件被 organize/dedupe 移到新路径后,
    旧 full_path 记录应被更新而非另插一行,避免数据库随每次 run 膨胀。
    磁盘上仍存在的同 hash 记录属于真重复,不在认领范围。
    """
    rows = conn.execute(
        "SELECT full_path FROM photos WHERE hash = ? AND full_path != ? AND moved = 1",
        (file_hash, exclude_path),
    ).fetchall()
    for (path,) in rows:
        if not os.path.exists(path):
            return path
    return None


def duplicate_groups(conn, only_unmoved: bool = False) -> list:
    """按 hash 分组返回重复组:[(hash, count, paths), ...]"""
    where = "WHERE moved = 0" if only_unmoved else ""
    return conn.execute(
        f"""SELECT hash, COUNT(*) AS cnt, GROUP_CONCAT(full_path, '||')
            FROM photos {where} GROUP BY hash HAVING cnt > 1 ORDER BY cnt DESC"""
    ).fetchall()


def update_path_after_move(conn, old_path: str, new_path: str) -> None:
    conn.execute(
        "UPDATE photos SET full_path = ?, moved = 1 WHERE full_path = ?",
        (new_path, old_path),
    )


def update_moved_status(conn, path: str, status: int) -> None:
    conn.execute("UPDATE photos SET moved = ? WHERE full_path = ?", (status, path))


def reset_moved_under(conn, folder_prefix: str) -> dict:
    """把 folder 下 moved=1 且 full_path 物理仍存在的记录 reset 回 moved=0。

    安全前提:full_path 物理仍存磁盘(说明用户把执行后文件手动移回原位,
    属于撤销性场景,而非误重置)。磁盘上物理不存在的记录不动。
    返回 {"reset": n, "skipped_missing": m, "skipped_norm": k}。
    """
    prefix = os.path.normpath(folder_prefix) + os.sep
    rows = conn.execute(
        "SELECT full_path FROM photos WHERE moved = 1 AND full_path LIKE ?",
        (prefix + "%",),
    ).fetchall()
    reset = 0
    skipped_missing = 0
    for (path,) in rows:
        if os.path.exists(path):
            conn.execute("UPDATE photos SET moved = 0 WHERE full_path = ?", (path,))
            reset += 1
        else:
            skipped_missing += 1
    return {"reset": reset, "skipped_missing": skipped_missing}


def increment_occurrence(conn, file_hash: str) -> None:
    conn.execute(
        "UPDATE photos SET occurrence_count = occurrence_count + 1 WHERE hash = ?",
        (file_hash,),
    )


def stats(conn) -> dict:
    row = conn.execute(
        """SELECT media_type, COUNT(*), SUM(moved), SUM(occurrence_count)
           FROM photos GROUP BY media_type"""
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    dup = conn.execute(
        "SELECT COUNT(*) FROM (SELECT hash FROM photos GROUP BY hash HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    return {
        "total": total,
        "duplicate_groups": dup,
        "by_type": [
            {"media_type": r[0], "count": r[1], "moved": r[2] or 0, "occurrences": r[3] or 0}
            for r in row
        ],
    }
