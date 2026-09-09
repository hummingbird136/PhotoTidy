"""重复检测:按 hash 分组,每组保留一个原位,其余移入 duplicate 目录。

验收对照规则 ②:统一 MD5 hash 判重(消除旧脚本两套判重口径)。
重复文件与未重复文件同库管理(moved 状态区分),不再拆 duplicate.db。
"""

import os

from .. import db as db_module
from .organizer import PlanItem


def _pick_keeper(conn, digest: str, paths: list) -> str:
    """每组保留者:拍摄日期最早的一张(用户语义里的「原件」)。

    同 hash 组的 capture_date 通常相同(字节一致),此时按路径长度退避:
    macOS 复制件命名是「原名 (1).jpg」,更短者通常是原件;再相同按字母序,
    保证结果确定性。此前纯字母序会让「未命名项目 (1).jpeg」排在原件前面,
    把原件当重复件移走——方向反了。
    """
    caps = dict(
        conn.execute(
            "SELECT full_path, capture_date FROM photos WHERE hash = ? AND moved = 0",
            (digest,),
        ).fetchall()
    )

    def key(p: str):
        d = caps.get(p)
        return (d is None, d or "", len(p), p)

    return min(paths, key=key)


def plan_duplicates(db_path: str, duplicate_root: str, source_root: str | None = None) -> list:
    """未移动的重复记录 → duplicate_root 的移动计划。

    每组保留拍摄日期最早的一张为原件(同日取路径更短者,字母序兜底),其余移出。
    已在 duplicate_root 内的记录不参与保留评选(避免把 duplicate 里的文件再当原件)。

    source_root:限定只处理该目录下的记录。限定后组内剩余不足 2 条时不生成计划
    (范围外的重复原件保持原位,不动用户的其他目录);缺省为不限定(兼容旧行为)。
    """
    src_prefix = os.path.normpath(source_root) + os.sep if source_root else None
    dup_abs = os.path.abspath(duplicate_root)
    plan = []
    with db_module.connect(db_path) as conn:
        groups = db_module.duplicate_groups(conn, only_unmoved=True)
        for digest, count, all_paths in groups:
            paths = all_paths.split("||")
            if src_prefix:
                paths = [p for p in paths if p.startswith(src_prefix)]
                if len(paths) < 2:
                    continue
            candidates = [p for p in paths if os.path.dirname(os.path.abspath(p)) != dup_abs]
            keep = _pick_keeper(conn, digest, candidates or paths)
            rest = [p for p in paths if p != keep]
            for path in rest:
                plan.append(
                    PlanItem(
                        path, duplicate_root, "dedupe", f"与 {keep} 重复(hash {digest[:8]}...)"
                    )
                )
    return plan
