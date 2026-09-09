# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- `phototidy` 包化重构:core / CLI / GUI 三层分离;项目定名 PhotoTidy(拾光),CLI 命令 `phototidy` / `phototidy-gui`,数据目录 `~/.phototidy/`
- 五命令 CLI:`scan` / `dedupe` / `organize` / `run` / `stats`(默认 dry-run,`--execute` 确认)
- CustomTkinter GUI:文件夹选择、计划预览(可跳过单项)、后台线程进度、日志落盘
- 统一 SQLite schema + 旧库缺列自动补齐
- 跨平台文件名安全化(Windows 非法字符/保留名)、同名冲突自动改名
- move / copy 双模式(copy:复制 → hash 校验 → 删源)
- 单元测试 10 例(scan 幂等、判重计数、两阶段归档、冲突改名、旧库迁移等)

### Changed
- 判重口径统一为 MD5 hash(替代旧脚本中的文件名 / full_path 判据)
- occurrence_count 仅在新路径撞上已有 hash 组时 +1(重扫幂等)
- 日期目录格式单一来源 `%Y-%m-%d`

### Removed
- `src/` 旧脚本(含 `(1)` 过滤的一次性逻辑、双 schema 代码路径)
