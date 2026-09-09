"""两阶段归档:先生成计划(plan),确认后统一执行(execute)。

验收对照规则 ②:消除"边扫描边移动"的破坏性问题——计划阶段绝不落盘,
执行阶段逐条移动并同步数据库 full_path / moved 状态。
"""

import logging
import os
from dataclasses import dataclass

from .. import db as db_module
from . import fileops

log = logging.getLogger(__name__)


@dataclass
class PlanItem:
    src: str
    dest_dir: str
    action: str  # 'organize' | 'dedupe'
    reason: str


def plan_organize(db_path: str, dest_root: str, config, source_root: str | None = None) -> list:
    """未移动记录 → <dest_root>/<capture_date>/ 的归档计划。

    source_root:只计划该目录下的源文件,缺省时与 dest_root 相同(原地整理)。
    必须限定来源范围,否则数据库里其他已扫描目录的记录会被卷入本次计划,
    表现为「整理 A 目录却把 B 目录的照片也搬走」。
    """
    src_prefix = os.path.normpath(source_root or dest_root) + os.sep
    plan = []
    with db_module.connect(db_path) as conn:
        rows = conn.execute(
            """SELECT full_path, capture_date FROM photos
               WHERE moved = 0 AND capture_date IS NOT NULL AND full_path LIKE ?""",
            (src_prefix + "%",),
        ).fetchall()
    for full_path, capture_date in rows:
        if not capture_date:
            continue  # 防御:无日期一律跳过(与扫描口径一致)
        dest_dir = os.path.join(dest_root, capture_date)
        if os.path.dirname(os.path.abspath(full_path)) == os.path.abspath(dest_dir):
            continue  # 已在目标日期目录下,跳过(防自我归档)
        plan.append(PlanItem(full_path, dest_dir, "organize", f"归档到日期目录 {capture_date}"))
    return plan


def execute(plan: list, db_path: str, config, progress_cb=None) -> dict:
    """执行计划单,逐条移动并同步数据库;单条失败不中断整体。

    progress_cb:可选回调,每条处理完调用 progress_cb(item, ok, error)(供 GUI 进度条)。
    """
    mover = fileops.move if config.mode == "move" else fileops.copy_verify_delete
    moved = 0
    failed = []
    with db_module.connect(db_path) as conn:
        for item in plan:
            ok, error = True, None
            if not os.path.isfile(item.src):
                ok, error = False, "源文件不存在"
                failed.append({"src": item.src, "error": error})
            else:
                try:
                    dest = mover(item.src, item.dest_dir)
                    db_module.update_path_after_move(conn, item.src, str(dest))
                    moved += 1
                except Exception as exc:
                    log.error("移动失败 %s: %s", item.src, exc)
                    ok, error = False, str(exc)
                    failed.append({"src": item.src, "error": error})
            if progress_cb:
                progress_cb(item, ok, error)
    return {"moved": moved, "failed": failed}
