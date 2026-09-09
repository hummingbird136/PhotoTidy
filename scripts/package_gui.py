"""PyInstaller 打包入口(--windowed macOS App)。

开发安装:
  pip install -e '.[gui,dev]'

打包:
  PYINSTALLER_CONFIG_DIR="$PWD/scripts/.pyinst-cache" python -m PyInstaller \
      --noconfirm --clean --windowed --name PhotoTidy \
      --collect-all customtkinter \
      --distpath scripts/dist --workpath scripts/build --specpath scripts \
      scripts/package_gui.py
"""

from phototidy.gui import entry

if __name__ == "__main__":
    entry()
