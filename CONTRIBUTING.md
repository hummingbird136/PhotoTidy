# 贡献指南

## 环境

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
```

GUI 开发需要带 Tk 的 Python(brew 版 Python 通常不带,推荐 python.org 官方安装包或 `python3-tk` 系统包),extras 一步装全:`pip install -e '.[dev,gui]'`。

## 约定

- **core 与 UI 分离**:`phototidy/core/*` 不 import CLI / GUI;`cli.py`、`gui.py` 只做薄壳
- **默认只写数据库**:任何移动/删除文件的能力必须默认 dry-run,用户显式确认(命令行 `--execute` / GUI 勾选确认)后才落盘
- **两阶段执行**:先 `plan_*()` 生成计划,确认后 `execute()` 统一执行;禁止边遍历边移动
- **判重唯一口径**:MD5 hash(`metadata.file_hash`),不允许引入按文件名/路径的判重
- **失败不中断**:单文件移动失败记录进结果,整体继续,最终汇总
- 代码注释用中文

## 提交前

```bash
ruff check . --fix
ruff format .
pytest tests/
python -m py_compile phototidy/*.py phototidy/core/*.py
```

规范极简:仅 ruff 默认规则(`line-length = 100`,`target-version = py310`)。CI 会跑同样的 `ruff check .` 与 `ruff format --check .`,本地先跑可避免 CI 红灯。

## 提交信息

格式为 `<type>: <简短描述>`,type 只能是 `feat / fix / docs / refactor / chore`。仓库已配置提交模板(自动带注释提示);若用 pre-commit,`pre-commit install --hook-type commit-msg` 还可启用强制校验:

```bash
git config commit.template .gitmessage.txt   # 新 clone 后设置一次
```

PR 请附:改动动机、验收方式(手测命令/测试用例)。
