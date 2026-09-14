"""PyInstaller 打包入口脚本(被 scripts/build.py 调用,勿直接手动打包)。

统一打包请用(CI 与本地同源):
    python scripts/build.py

直接手动打包(不推荐,参数以 scripts/build.py 为准)仅用于临时调试。
"""

from phototidy.gui import entry

if __name__ == "__main__":
    entry()
