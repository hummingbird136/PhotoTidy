"""归档计划项:organizer / deduplicator / pipeline 共用的数据结构。"""

from dataclasses import dataclass


@dataclass
class PlanItem:
    src: str
    dest_dir: str
    action: str  # 'organize' | 'dedupe'
    reason: str
