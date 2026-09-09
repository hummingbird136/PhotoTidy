"""「去重 + 归档」计划流水线:CLI run / GUI 预览共用(评审项 A:逻辑收口)。

此前 cli.run 与 gui._task_preview 各自手工实现一遍计划生成(顺序、交集过滤),
两份实现有再次漂移的风险;统一收编到本模块,保证两端行为一致。
"""

import os

from . import deduplicator, organizer


def build_plan(db_path: str, source_root: str, archive_root: str, config) -> tuple[list, list]:
    """生成 (dup_plan, org_plan):先重复移出,再日期归档,两者不重叠。"""
    dup_plan = deduplicator.plan_duplicates(
        db_path, os.path.join(archive_root, config.duplicate_dir_name), source_root=source_root
    )
    org_plan = organizer.plan_organize(db_path, archive_root, config, source_root=source_root)
    # 计划去重:待移入 duplicate 的文件不再参与本轮归档,
    # 否则同一文件先被移走、后归档时报「源文件不存在」。
    dup_srcs = {os.path.abspath(item.src) for item in dup_plan}
    org_plan = [item for item in org_plan if os.path.abspath(item.src) not in dup_srcs]
    return dup_plan, org_plan
