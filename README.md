# PhotoTidy(拾光)

帮你把乱成一团的照片/视频自动整理好:去掉重复的,按拍摄日期分文件夹。全程在你电脑上运行,不上传任何数据。

**适合谁**:手机/相机照片越攒越多、重复文件一堆、想按日期归档却懒得手动整理的普通用户。

## 三步上手(图形界面,推荐)

1. **下载安装包**:到本仓库的 [Releases](../../releases) 页下载对应系统的安装包(macOS 为 `.zip`,Windows 为 `.exe`),解压/双击即可,属于绿色软件不写注册表。
2. **第一次打开时点一下放行**:
   - macOS:右键 App → 打开 → 再点「打开」
   - Windows:弹窗时点「更多信息 → 仍要运行」
3. **整理照片**:选择文件夹 → 点一下生成整理计划 → 在预览清单里确认没问题 → 点执行。

> 不会用也没事:不点「执行」,软件一个文件都不会动。预览清单里哪行不想要,双击即可跳过。

## 它会做什么

1. **扫描**:读取照片的拍摄时间(EXIF),建立本地档案库
2. **去重**:找出内容完全一样的文件,每组只留一份,多余的挪进 `duplicate/` 文件夹(不会直接删除)
3. **归档**:按「拍摄日期」分文件夹放好,比如 `2023-01-15/`

重复的文件只是挪走、同名文件自动改名,**永远不会替你删除或覆盖**任何照片。

## 常见问题

| 问题 | 回答 |
|---|---|
| 会联网吗?会偷偷上传吗? | 完全离线,所有数据只存在你自己电脑上 |
| 重复的照片会被删掉吗? | 不会,只是移动到 `duplicate/` 文件夹,随时可找回 |
| 文件名一样会覆盖吗? | 不会,自动改成 `xxx_1.jpg` |
| 没有拍摄时间的文件怎么办? | 原地不动,不打扰你 |
| 整理错了能恢复吗? | 去重只是移动(可移回);归档也只是移动,可手动复原;建议先在小文件夹试一遍 |
| 从手机往电脑整理怕丢数据? | 用「复制模式」:先复制、再校验内容一致、最后才删源 |

## 隐私与卸载

- 所有信息(拍摄时间、照片哈希)只存在你电脑的 `~/.phototidy/` 目录(Windows 为 `C:\Users\<用户名>\.phototidy\`),完全离线。
- 卸载=删掉 App 即可,无残留。想彻底清空,手动删除上面那个目录。

## 进阶:命令行(CLI)

适合批量、NAS、脚本化场景。要求 Python ≥ 3.10:

```bash
pip install -e .          # 命令行版
pip install -e '.[gui]'   # 附带图形界面

phototidy scan /path/to/photos                    # 扫描(只读)
phototidy dedupe /path/to/photos                  # 预览去重计划
phototidy organize /path/to/photos                # 预览归档计划
phototidy organize /path/to/photos --execute      # 确认后执行
phototidy run /path/to/photos --execute           # 一键:扫描+去重+归档
phototidy organize /path/to/photos --dest /path/to/output --execute   # 整理到指定目录
phototidy stats                                   # 数据库统计
phototidy reset-moved /path/to/photos             # 撤销「已移出」标记
```

> 不加 `--execute` 就只输出计划、不动文件。CLI 日志写入运行目录 `logs/`,GUI 日志在 `~/.phototidy/logs/`。

## 进阶:自定义配置

`--config` 传 YAML 文件覆盖默认值(见 [configs/config.example.yaml](configs/config.example.yaml)),可自定义照片/视频格式、归档目录格式、move/copy 模式、数据库位置等。

## 常见问题(进阶)

| 问题 | 说明 |
|---|---|
| 重复扫描会重复计数吗? | 不会,同路径重扫幂等 |
| RAW / GIF 支持吗? | 默认范围见配置文件,可经 `image_formats` 自行扩展 |
| macOS 访问外置盘被拒? | 系统设置 → 隐私与安全性 → 完全磁盘访问,加入终端或 App |
| 旧的 photos.db 能继续用吗? | 可以,缺列自动补齐;必要时重新 `scan` |

## 开发

```bash
pip install -e '.[dev]'
pytest tests/
```

结构:`phototidy/core`(业务,与 UI 无耦合)、`phototidy/db.py`(SQLite 层)、`cli.py` / `gui.py`(薄壳)。

## 安全

请不要在公开 issue 中提交私人照片、视频、日志或完整本地路径。漏洞反馈流程见
[SECURITY.md](SECURITY.md)。

## 许可

本项目代码使用 MIT License,详见 [LICENSE](LICENSE)。

运行和打包时会使用第三方依赖,其许可证独立适用;发布二进制安装包前请核对
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
