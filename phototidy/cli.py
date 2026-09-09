"""CLI 入口:scan / organize / dedupe / stats / run。

总体原则 4(默认只读,写操作显式确认):
  organize / dedupe / run 默认 dry-run 输出计划清单,需 --execute 显式确认后落盘。
"""

import json
import logging
import sys
from pathlib import Path

import typer

from . import __version__
from . import db as db_module
from .config import Config, default_db_path
from .core import deduplicator, organizer, pipeline, scanner

app = typer.Typer(
    help="PhotoTidy(拾光):照片/视频扫描、去重、按日期归档",
    add_completion=False,  # 自带补全脚本安装项说明为英文且非核心功能,隐藏
)

# 目录参数统一校验:必须真实存在且为目录,resolve 为绝对路径,
# 避免打错路径时静默空跑(rc=0、入库 0)而毫无感知。
_DIR_ARG = typer.Argument(
    ...,
    exists=True,
    file_okay=False,
    dir_okay=True,
    resolve_path=True,
    help="待整理的照片目录",
)
_DEST_OPT = typer.Option(
    None,
    "--dest",
    exists=True,
    file_okay=False,
    dir_okay=True,
    resolve_path=True,
    help="整理结果输出根目录(缺省为源目录,即原地整理)",
)
_FOLDER_ARG = typer.Argument(
    ...,
    exists=True,
    file_okay=False,
    dir_okay=True,
    resolve_path=True,
    help="只重置该目录前缀下的记录",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"PhotoTidy {__version__}")
        raise typer.Exit()


State = {"config": Config(), "db": "", "verbose": False}


def _setup(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


@app.callback()
def main(
    config: str = typer.Option(None, "--config", help="YAML 配置文件路径"),
    db: str = typer.Option(None, "--db", help="数据库路径(默认 ~/.phototidy/photos.db)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    version: bool = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="显示版本号并退出",
    ),
):
    _setup(verbose)
    State["config"] = Config.load(config)
    State["db"] = db or default_db_path()


def _print_plan(plan) -> None:
    for i, item in enumerate(plan, 1):
        lines = f"[{i}] {item.action}\n    {item.src}\n    -> {item.dest_dir}"
        # organize 的 dest_dir 已含日期目录,reason 与之同义,省略避免噪音
        if item.action != "organize":
            lines += f"\n    {item.reason}"
        typer.echo(lines)
    typer.echo(f"共 {len(plan)} 项待执行。加 --execute 确认执行。")


@app.command()
def scan(path: Path = _DIR_ARG):
    """扫描入库(只读,安全)。"""
    result = scanner.scan(str(path), State["db"], State["config"])
    typer.echo(f"扫描完成: 入库 {result['scanned']}, 跳过 {result['skipped_no_time']}")
    typer.echo(f"下一步: phototidy organize {path} 预览归档计划(--execute 执行)")


@app.command()
def organize(
    path: Path = _DIR_ARG,
    dest: Path | None = _DEST_OPT,
    execute: bool = typer.Option(False, "--execute", help="确认执行移动"),
):
    """按日期归档移动:源目录文件归入 <dest>/<日期>/ (默认仅预览计划)。"""
    cfg = State["config"]
    dir, target = str(path), str(dest or path)
    typer.echo(f"整理目标: {target}" + ("" if dest else "(原地整理)"))
    plan = organizer.plan_organize(State["db"], target, cfg, source_root=dir)
    if not plan:
        typer.echo("无待归档文件。")
        raise typer.Exit()
    if not execute:
        _print_plan(plan)
        raise typer.Exit()
    result = organizer.execute(plan, State["db"], cfg)
    _print_result(result)


@app.command()
def dedupe(
    path: Path = _DIR_ARG,
    dest: Path | None = _DEST_OPT,
    execute: bool = typer.Option(False, "--execute", help="确认执行移动"),
):
    """去重:重复 hash 文件移入 <dest>/duplicate(缺省 <path>/duplicate;仅预览)。

    每组保留拍摄日期最早的一张为原件(同日取文件名较短者,排除「原名 (1)」
    这类复制件),其余移入 duplicate 目录。
    """
    cfg = State["config"]
    dir = str(path)
    dup_root = f"{dest or dir}/{cfg.duplicate_dir_name}"  # 执行时自动创建
    plan = deduplicator.plan_duplicates(State["db"], dup_root, source_root=dir)
    if not plan:
        typer.echo("未发现重复文件。")
        raise typer.Exit()
    if not execute:
        _print_plan(plan)
        raise typer.Exit()
    result = organizer.execute(plan, State["db"], cfg)
    _print_result(result)


@app.command()
def run(
    path: Path = _DIR_ARG,
    dest: Path | None = _DEST_OPT,
    execute: bool = typer.Option(False, "--execute", help="确认执行移动"),
):
    """一键:扫描 + 去重 + 归档(默认全程仅预览)。"""
    cfg = State["config"]
    dir, target = str(path), str(dest or path)
    typer.echo(f"整理目标: {target}" + ("" if dest else "(原地整理)"))
    scan_result = scanner.scan(dir, State["db"], cfg)
    typer.echo(f"扫描: 入库 {scan_result['scanned']}, 跳过 {scan_result['skipped_no_time']}")

    # 计划生成收口到 core.pipeline(GUI 预览共用同一实现,行为一致)
    dup_plan, org_plan = pipeline.build_plan(State["db"], dir, target, cfg)
    plan = dup_plan + org_plan
    typer.echo(f"计划: 重复移出 {len(dup_plan)} 项, 归档 {len(org_plan)} 项")
    if not plan:
        raise typer.Exit()
    if not execute:
        _print_plan(plan)
        raise typer.Exit()
    result = organizer.execute(plan, State["db"], cfg)
    _print_result(result)


@app.command()
def reset_moved(
    folder: Path = _FOLDER_ARG,
):
    """重置「已移出」标记:moved=1 但 full_path 物理仍存在的记录回滚为待处理。

    场景:此前执行移动后,手动把文件移回了原目录。重扫不会自动撤销 moved=1,
    plan_organize 会跳过这些记录,表现为「计划为 0 项」。
    """
    db_module.init_db(State["db"])
    with db_module.connect(State["db"]) as conn:
        result = db_module.reset_moved_under(conn, str(folder))
    reset = result["reset"]
    skipped = result["skipped_missing"]
    typer.echo(typer.style(f"已重置 {reset} 项标记。", fg="green"))
    if skipped:
        typer.echo(f"跳过 {skipped} 项(磁盘上不存在,保留 moved=1)。")
    typer.echo("提示:重新预览 organize / dedupe / run 查看更新后的计划。")


@app.command()
def stats():
    """数据库统计报表。"""
    db_module.init_db(State["db"])
    with db_module.connect(State["db"]) as conn:
        s = db_module.stats(conn)
    moved_total = sum(t["moved"] for t in s["by_type"])
    typer.echo(
        f"共 {s['total']} 条记录,重复组 {s['duplicate_groups']} 组,"
        f"已移出归档 {moved_total} 条。明细(JSON):"
    )
    typer.echo(json.dumps(s, ensure_ascii=False, indent=2))


def _print_result(result: dict) -> None:
    typer.echo(typer.style(f"已移动 {result['moved']} 个文件。", fg="green"))
    if result["failed"]:
        typer.echo(typer.style(f"失败 {len(result['failed'])} 个:", fg="red"))
        for f in result["failed"]:
            typer.echo(f"  {f['src']}: {f['error']}")
        typer.echo("提示: 详细堆栈加 -v(--verbose)重跑可见;GUI 运行日志在 ~/.phototidy/logs/")


def entry() -> None:
    app()


if __name__ == "__main__":
    entry()
