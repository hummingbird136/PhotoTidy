"""core 层单元与端到端测试。

覆盖验收对照规则(B7:核心行为不回归):
- 保留:EXIF 优先 + 时间戳回退、hash 判重、重复进 duplicate、同名不覆盖
- 修复:occurrence_count 仅重复 hash +1、两阶段执行、日期目录单一、重扫幂等
"""

import os

import pytest
from typer.testing import CliRunner

from phototidy import db as db_module
from phototidy.cli import app as cli_app
from phototidy.config import Config
from phototidy.core import deduplicator, organizer, scanner
from phototidy.core.fileops import sanitize_name, unique_path


@pytest.fixture
def media_tree(tmp_path):
    """两对重复 + 单文件,EXIF 拍摄日期 2023-01-15 / 2023-02-20。"""
    pytest.importorskip("PIL")
    piexif = pytest.importorskip("piexif")
    from PIL import Image

    def mk(path, when, color):
        exif = {"Exif": {piexif.ExifIFD.DateTimeOriginal: when.encode()}}
        Image.new("RGB", (16, 16), color).save(str(path), exif=piexif.dump(exif))

    src = tmp_path / "src"
    src.mkdir()
    mk(src / "a.jpg", "2023:01:15 10:00:00", (10, 10, 10))
    mk(src / "a_dup.jpg", "2023:01:15 10:00:00", (10, 10, 10))
    mk(src / "b.jpg", "2023:02:20 11:00:00", (20, 20, 20))
    (src / "note.txt").write_text("not media")
    return src, tmp_path


@pytest.fixture
def cfg():
    return Config()


def test_scan_registers_media_only(media_tree, cfg, tmp_path):
    src, _ = media_tree
    db = str(tmp_path / "t.db")
    result = scanner.scan(str(src), db, cfg)
    assert result["scanned"] == 3
    assert result["skipped_no_time"] == 0
    with db_module.connect(db) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM photos ORDER BY name")]
    assert names == ["a.jpg", "a_dup.jpg", "b.jpg"]
    # media_type 与元数据 JSON 入库
    with db_module.connect(db) as conn:
        rows = conn.execute("SELECT media_type, metadata FROM photos").fetchall()
    assert all(m == "photo" for m, _ in rows)
    import json

    assert all(json.loads(meta) for _, meta in rows)  # 含 EXIF DateTimeOriginal


def test_rescan_idempotent(media_tree, cfg, tmp_path):
    src, _ = media_tree
    db = str(tmp_path / "t.db")
    scanner.scan(str(src), db, cfg)
    with db_module.connect(db) as conn:
        before = conn.execute("SELECT SUM(occurrence_count) FROM photos").fetchone()[0]
    scanner.scan(str(src), db, cfg)
    with db_module.connect(db) as conn:
        after = conn.execute("SELECT SUM(occurrence_count) FROM photos").fetchone()[0]
    assert before == after  # 重扫不重复计 occurrence_count


def test_occurrence_only_for_duplicate_hash(media_tree, cfg, tmp_path):
    src, _ = media_tree
    db = str(tmp_path / "t.db")
    scanner.scan(str(src), db, cfg)
    with db_module.connect(db) as conn:
        rows = dict(
            conn.execute("SELECT hash, SUM(occurrence_count) FROM photos GROUP BY hash").fetchall()
        )
    # a/a_dup 同 hash:基础 2 条 + 判重 +1 = 3;b 独立 hash = 1
    counts = sorted(rows.values())
    assert counts == [1, 3]


def test_dedupe_keeps_one_and_moves_rest(media_tree, cfg, tmp_path):
    src, _root = media_tree
    db = str(tmp_path / "t.db")
    scanner.scan(str(src), db, cfg)
    plan = deduplicator.plan_duplicates(db, str(src / "duplicate"))
    assert len(plan) == 1
    assert plan[0].src.endswith(("a.jpg", "a_dup.jpg"))
    result = organizer.execute(plan, db, cfg)
    assert result["moved"] == 1 and not result["failed"]
    assert (src / "duplicate").is_dir()
    # 数据库 full_path 已同步
    with db_module.connect(db) as conn:
        dup_paths = [
            r[0]
            for r in conn.execute("SELECT full_path FROM photos WHERE full_path LIKE '%duplicate%'")
        ]
    assert len(dup_paths) == 1


def test_organize_two_phase_and_conflict_rename(media_tree, cfg, tmp_path):
    src, _root = media_tree
    db = str(tmp_path / "t.db")
    scanner.scan(str(src), db, cfg)
    plan = organizer.plan_organize(db, str(src), cfg)
    assert len(plan) == 3
    # 计划阶段不落盘:文件仍在原位
    assert (src / "a.jpg").is_file()
    result = organizer.execute(plan, db, cfg)
    assert result["moved"] == 3 and not result["failed"]
    # 同日期的 a.jpg / a_dup.jpg 同名不覆盖:一个自动改名
    day_dir = src / "2023-01-15"
    moved_names = sorted(p.name for p in day_dir.iterdir())
    assert moved_names == ["a.jpg", "a_dup.jpg"]
    assert (src / "2023-02-20" / "b.jpg").is_file()
    # 幂等:再计划为空
    assert organizer.plan_organize(db, str(src), cfg) == []


def test_same_name_different_content_no_overwrite(tmp_path, cfg):
    pytest.importorskip("PIL")
    piexif = pytest.importorskip("piexif")
    from PIL import Image

    def mk(path, color):
        exif = {"Exif": {piexif.ExifIFD.DateTimeOriginal: b"2023:03:01 08:00:00"}}
        Image.new("RGB", (16, 16), color).save(str(path), exif=piexif.dump(exif))

    src = tmp_path / "src"
    src.mkdir()
    mk(src / "same.jpg", (1, 2, 3))
    sub = src / "other"
    sub.mkdir()
    mk(sub / "same.jpg", (9, 9, 9))
    db = str(tmp_path / "t.db")
    scanner.scan(str(src), db, cfg)
    organizer.execute(organizer.plan_organize(db, str(src), cfg), db, cfg)
    day = src / "2023-03-01"
    files = sorted(p.name for p in day.iterdir())
    assert files == ["same.jpg", "same_1.jpg"]  # 改名而非覆盖


def test_copy_mode_verifies_and_removes_source(tmp_path, cfg):
    pytest.importorskip("PIL")
    piexif = pytest.importorskip("piexif")
    from PIL import Image

    cfg.mode = "copy"
    src = tmp_path / "src"
    src.mkdir()
    exif = {"Exif": {piexif.ExifIFD.DateTimeOriginal: b"2023:04:01 09:00:00"}}
    Image.new("RGB", (16, 16), (7, 7, 7)).save(str(src / "c.jpg"), exif=piexif.dump(exif))
    db = str(tmp_path / "t.db")
    scanner.scan(str(src), db, cfg)
    result = organizer.execute(organizer.plan_organize(db, str(src), cfg), db, cfg)
    assert result["moved"] == 1 and not result["failed"]
    assert not (src / "c.jpg").exists()
    assert (src / "2023-04-01" / "c.jpg").is_file()


def test_sanitize_windows_names(cfg):
    assert sanitize_name("a:b*c?.jpg") == "a_b_c_.jpg"
    assert sanitize_name("CON") == "_CON"
    assert sanitize_name("COM1.txt") == "_COM1.txt"
    assert sanitize_name("...") == "_"


def test_unique_path_retries(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_text("1")
    nxt = unique_path(p)
    assert nxt.name == "x_1.jpg"
    nxt.write_text("2")
    assert unique_path(p).name == "x_2.jpg"


def test_fixtures_pipeline_e2e(tmp_path, cfg):
    """测试素材生成脚本 → scan → run(dry-run 计划)端到端(承接 CI 素材集语义)。"""
    make_fixtures = pytest.importorskip("tests.make_fixtures")  # 内部需 PIL/piexif
    dest = str(tmp_path / "fix")
    make_fixtures.main(dest)

    db = str(tmp_path / "t.db")
    result = scanner.scan(dest, db, cfg)
    assert result["scanned"] == 8  # 2 重复 + 无EXIF + 同名对×2 + 特殊名×2 + PNG
    assert result["skipped_no_time"] == 0

    dup_plan = deduplicator.plan_duplicates(db, os.path.join(dest, "duplicate"))
    assert len(dup_plan) == 1  # 识别一组重复
    org_plan = organizer.plan_organize(db, dest, cfg)
    dup_srcs = {os.path.abspath(i.src) for i in dup_plan}
    org_plan = [i for i in org_plan if os.path.abspath(i.src) not in dup_srcs]
    result = organizer.execute(dup_plan + org_plan, db, cfg)
    # 嵌套目录里的 same.jpg 与根 same.jpg 归档同目录:一个应自动改名不覆盖
    names = sorted(os.listdir(os.path.join(dest, "2021-01-01")))
    assert "same.jpg" in names and "same_1.jpg" in names
    assert result["moved"] == 8 and not result["failed"]  # 归档 7 + 去重 1


def test_rerun_after_move_no_db_bloat(media_tree, cfg, tmp_path):
    """移动后重扫:认领旧记录改路径,不另插新行(库不膨胀)。"""
    src, _root = media_tree
    db = str(tmp_path / "t.db")
    scanner.scan(str(src), db, cfg)
    with db_module.connect(db) as conn:
        before_rows = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        before_occ = conn.execute("SELECT SUM(occurrence_count) FROM photos").fetchone()[0]

    # 一键:去重 + 归档全部执行
    dup_plan = deduplicator.plan_duplicates(db, str(src / "duplicate"))
    org_plan = [
        i
        for i in organizer.plan_organize(db, str(src), cfg)
        if os.path.abspath(i.src) not in {os.path.abspath(d.src) for d in dup_plan}
    ]
    organizer.execute(dup_plan + org_plan, db, cfg)

    # 重扫:新路径应认领旧记录,行数与 occurrence_count 均不增长
    scanner.scan(str(src), db, cfg)
    with db_module.connect(db) as conn:
        after_rows = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        after_occ = conn.execute("SELECT SUM(occurrence_count) FROM photos").fetchone()[0]
        # 每条记录路径与磁盘一一对应,无陈旧残留
        stale = [
            r[0] for r in conn.execute("SELECT full_path FROM photos") if not os.path.exists(r[0])
        ]
    assert before_rows == after_rows
    assert before_occ == after_occ
    assert stale == []


def test_db_migration_adds_missing_columns(tmp_path):
    """旧库缺列自动补齐(A2 迁移雏形)。"""
    import sqlite3

    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY, full_path TEXT UNIQUE, name TEXT)")
    conn.commit()
    conn.close()
    db_module.init_db(db)
    with db_module.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)")}
    assert {"hash", "moved", "media_type", "occurrence_count"} <= cols


def test_reset_moved_under_resets_existing_only(tmp_path):
    """reset_moved_under:只 reset full_path 物理存在的 moved=1 记录(撤销场景)。"""
    db = str(tmp_path / "t.db")
    folder = tmp_path / "src"
    folder.mkdir()
    real = folder / "real.jpg"
    real.write_bytes(b"fake")  # 物理存在
    ghost = folder / "ghost.jpg"  # 物理不存在(用户已清理)

    db_module.init_db(db)
    with db_module.connect(db) as conn:
        for p in (str(real), str(ghost)):
            conn.execute(
                """INSERT INTO photos (full_path, name, size, capture_time, capture_date,
                   hash, media_type, moved) VALUES (?, ?, 0, '', '2024-01-01', 'h', 'photo', 1)""",
                (p, os.path.basename(p)),
            )

    with db_module.connect(db) as conn:
        result = db_module.reset_moved_under(conn, str(folder))
    assert result == {"reset": 1, "skipped_missing": 1}

    with db_module.connect(db) as conn:
        rows = dict(conn.execute("SELECT full_path, moved FROM photos").fetchall())
    assert rows[str(real)] == 0  # 物理在 → 已撤销
    assert rows[str(ghost)] == 1  # 物理不在 → 保持已移出


def test_reset_moved_under_noop_when_none_moved(tmp_path):
    """无 moved=1 时返回 0,不报错。"""
    db = str(tmp_path / "t.db")
    db_module.init_db(db)
    with db_module.connect(db) as conn:
        result = db_module.reset_moved_under(conn, str(tmp_path))
    assert result == {"reset": 0, "skipped_missing": 0}


def _mk_media(path, when, color):
    """生成带 EXIF 拍摄时间的测试图(依赖 PIL/piexif,调用方需 importorskip)。"""
    import piexif
    from PIL import Image

    exif = {"Exif": {piexif.ExifIFD.DateTimeOriginal: when.encode()}}
    Image.new("RGB", (16, 16), color).save(str(path), exif=piexif.dump(exif))


def test_organize_scoped_to_source_root(tmp_path, cfg):
    """plan_organize 只计划 source_root 下的文件:整理 A 不卷入 B 目录的照片。"""
    pytest.importorskip("PIL")
    pytest.importorskip("piexif")
    a = tmp_path / "a"
    a.mkdir()
    _mk_media(a / "a1.jpg", "2023:05:01 09:00:00", (1, 1, 1))
    _mk_media(a / "a2.jpg", "2023:05:01 09:00:00", (2, 2, 2))
    b = tmp_path / "b"
    b.mkdir()
    _mk_media(b / "b1.jpg", "2023:05:01 09:00:00", (3, 3, 3))
    db = str(tmp_path / "t.db")
    scanner.scan(str(a), db, cfg)
    scanner.scan(str(b), db, cfg)  # 同一库里有 A、B 两个目录的记录

    # 对 A 归档:计划只含 A 的 2 张,B 的 b1.jpg 不出现
    plan = organizer.plan_organize(db, str(a), cfg, source_root=str(a))
    assert sorted(os.path.basename(p.src) for p in plan) == ["a1.jpg", "a2.jpg"]
    assert all(p.src.startswith(str(a) + os.sep) for p in plan)

    # --dest 语义:源限定 A,目标可指向独立输出目录
    out = tmp_path / "out"
    out.mkdir()
    plan = organizer.plan_organize(db, str(out), cfg, source_root=str(a))
    assert len(plan) == 2
    assert all(p.dest_dir == str(out / "2023-05-01") for p in plan)

    # B 目录自身归档不受影响
    plan_b = organizer.plan_organize(db, str(b), cfg)
    assert [os.path.basename(p.src) for p in plan_b] == ["b1.jpg"]


def test_dedupe_scoped_to_source_root(tmp_path, cfg):
    """plan_duplicates 限定 source_root:跨目录重复组在范围内不足 2 条时不计划。"""
    pytest.importorskip("PIL")
    pytest.importorskip("piexif")
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    # 跨目录重复:x.jpg 在 A、B 各一份(同内容同 EXIF → 同 hash)
    _mk_media(a / "x.jpg", "2023:06:01 10:00:00", (4, 4, 4))
    _mk_media(b / "x.jpg", "2023:06:01 10:00:00", (4, 4, 4))
    # A 目录内重复对
    _mk_media(a / "y1.jpg", "2023:06:02 10:00:00", (5, 5, 5))
    _mk_media(a / "y2.jpg", "2023:06:02 10:00:00", (5, 5, 5))
    db = str(tmp_path / "t.db")
    scanner.scan(str(a), db, cfg)
    scanner.scan(str(b), db, cfg)

    # 限定 A:x.jpg 的另一份在 B(范围外),A 内只剩 1 份 → 不动;只处理 y 对
    plan = deduplicator.plan_duplicates(db, str(a / "duplicate"), source_root=str(a))
    assert len(plan) == 1
    assert os.path.basename(plan[0].src) in {"y1.jpg", "y2.jpg"}
    assert all(p.src.startswith(str(a) + os.sep) for p in plan)

    # 缺省不限定(兼容旧行为):x.jpg 的 B 副本也会被计划,共 2 项
    plan_all = deduplicator.plan_duplicates(db, str(a / "duplicate"))
    assert len(plan_all) == 2


def test_dedupe_keeps_original_over_copy_suffix(tmp_path, cfg):
    """同 hash 组 capture_date 必相同(字节一致):按路径更短者保留原件,
    macOS「未命名项目 (1).jpeg」这类复制件才应被移入 duplicate(此前纯字母序方向反了)。"""
    pytest.importorskip("PIL")
    pytest.importorskip("piexif")
    src = tmp_path / "src"
    src.mkdir()
    orig = src / "未命名项目.jpeg"
    copy = src / "未命名项目 (1).jpeg"
    _mk_media(orig, "2023:07:01 10:00:00", (8, 8, 8))
    _mk_media(copy, "2023:07:01 10:00:00", (8, 8, 8))
    db = str(tmp_path / "t.db")
    scanner.scan(str(src), db, cfg)

    plan = deduplicator.plan_duplicates(db, str(src / "duplicate"))
    assert len(plan) == 1
    assert plan[0].src == str(copy)  # 复制件被计划移出
    assert str(orig) in plan[0].reason  # 原件被保留,写进移出理由


def test_config_example_loads_public_defaults():
    """公开示例配置应可直接加载,且保持默认安全模式。"""
    cfg = Config.load("configs/config.example.yaml")

    assert cfg.mode == "move"
    assert cfg.duplicate_dir_name == "duplicate"
    assert cfg.date_folder_format == "%Y-%m-%d"
    assert ".heic" in cfg.image_formats
    assert ".mov" in cfg.video_formats
    assert cfg.db_path == ""


def test_config_custom_overrides_and_ignores_unknown_keys(tmp_path):
    """用户 YAML 只覆盖公开字段,未知字段不会污染运行配置。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
image_formats:
  - .jpg
  - .gif
video_formats:
  - .mp4
date_folder_format: '%Y/%m-%d'
duplicate_dir_name: dupes
mode: copy
db_path: custom.db
unknown_key: should_be_ignored
""".strip(),
        encoding="utf-8",
    )

    cfg = Config.load(str(config_path))

    assert cfg.image_formats == [".jpg", ".gif"]
    assert cfg.video_formats == [".mp4"]
    assert cfg.date_folder_format == "%Y/%m-%d"
    assert cfg.duplicate_dir_name == "dupes"
    assert cfg.mode == "copy"
    assert cfg.db_path == "custom.db"
    assert not hasattr(cfg, "unknown_key")
    assert cfg.media_type_for("example.GIF") == "photo"


def test_cli_dry_run_commands_do_not_move_files(media_tree, tmp_path):
    """CLI 默认只预览:dedupe/organize/run 不带 --execute 时不移动文件。"""
    src, _root = media_tree
    db = str(tmp_path / "cli.db")
    runner = CliRunner()

    scan_result = runner.invoke(cli_app, ["--db", db, "scan", str(src)])
    assert scan_result.exit_code == 0, scan_result.output

    dedupe_result = runner.invoke(cli_app, ["--db", db, "dedupe", str(src)])
    assert dedupe_result.exit_code == 0, dedupe_result.output
    assert not (src / "duplicate").exists()
    assert (src / "a.jpg").exists()
    assert (src / "a_dup.jpg").exists()

    organize_result = runner.invoke(cli_app, ["--db", db, "organize", str(src)])
    assert organize_result.exit_code == 0, organize_result.output
    assert not (src / "2023-01-15").exists()
    assert (src / "b.jpg").exists()

    run_result = runner.invoke(cli_app, ["--db", db, "run", str(src)])
    assert run_result.exit_code == 0, run_result.output
    assert not (src / "duplicate").exists()
    assert not (src / "2023-02-20").exists()

    stats_result = runner.invoke(cli_app, ["--db", db, "stats"])
    assert stats_result.exit_code == 0, stats_result.output
    assert "total" in stats_result.output

    reset_result = runner.invoke(cli_app, ["--db", db, "reset-moved", str(src)])
    assert reset_result.exit_code == 0, reset_result.output


def test_cli_execute_dedupe_and_organize_success(media_tree, tmp_path):
    """CLI 执行路径成功后,重复文件进 duplicate,剩余文件按日期归档。"""
    src, _root = media_tree
    db = str(tmp_path / "cli-execute.db")
    runner = CliRunner()

    scan_result = runner.invoke(cli_app, ["--db", db, "scan", str(src)])
    assert scan_result.exit_code == 0, scan_result.output

    dedupe_result = runner.invoke(cli_app, ["--db", db, "dedupe", str(src), "--execute"])
    assert dedupe_result.exit_code == 0, dedupe_result.output
    assert (src / "duplicate").is_dir()
    assert len(list((src / "duplicate").glob("*.jpg"))) == 1

    organize_result = runner.invoke(cli_app, ["--db", db, "organize", str(src), "--execute"])
    assert organize_result.exit_code == 0, organize_result.output
    assert (src / "2023-01-15").is_dir()
    assert (src / "2023-02-20").is_dir()
    assert len(list((src / "2023-01-15").glob("*.jpg"))) == 1
    assert len(list((src / "2023-02-20").glob("*.jpg"))) == 1

    stats_result = runner.invoke(cli_app, ["--db", db, "stats"])
    assert stats_result.exit_code == 0, stats_result.output
    assert '"moved"' in stats_result.output
