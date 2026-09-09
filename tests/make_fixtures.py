"""可重复构造的测试素材生成脚本,供 CI 端到端测试复用。

用法: python tests/make_fixtures.py <dest_dir>
产出子集:带 EXIF 照片对(重复)、无 EXIF 照片、同名不同内容对、非媒体干扰文件。
"""

import os
import shutil
import sys


def main(dest: str) -> None:
    import piexif
    from PIL import Image

    os.makedirs(dest, exist_ok=True)

    def make_jpg(path, when=None, color=(200, 30, 30)):
        img = Image.new("RGB", (32, 32), color)
        if when:
            exif = {"Exif": {piexif.ExifIFD.DateTimeOriginal: when.encode()}}
            img.save(path, exif=piexif.dump(exif))
        else:
            img.save(path)

    # 1) EXIF 重复对(同名外不同路径的完全同内容)
    make_jpg(os.path.join(dest, "2020-05-01_a.jpg"), "2020:05:01 08:00:00", (1, 2, 3))
    shutil.copy(os.path.join(dest, "2020-05-01_a.jpg"), os.path.join(dest, "2020-05-01_a_copy.jpg"))

    # 2) 无 EXIF 照片(回退文件时间戳)
    make_jpg(os.path.join(dest, "no_exif.jpg"), None, (0, 200, 0))

    # 3) 同名不同内容(不同 hash):归档时验证自动改名不覆盖
    sub = os.path.join(dest, "nested")
    os.makedirs(sub, exist_ok=True)
    make_jpg(os.path.join(dest, "same.jpg"), "2021:01:01 09:00:00", (255, 0, 0))
    make_jpg(os.path.join(sub, "same.jpg"), "2021:01:01 10:00:00", (0, 0, 255))

    # 4) 特殊文件名(空格、括号、Unicode 与模拟 Windows 非法字符场景)
    make_jpg(os.path.join(dest, "假日 (1).jpg"), "2022:06:15 12:30:00", (99, 99, 0))
    make_jpg(os.path.join(dest, "照片#tag&.jpg"), "2022:06:16 13:00:00", (0, 99, 99))

    # 5) 非媒体干扰文件(应被过滤)
    with open(os.path.join(dest, "readme.txt"), "w") as f:
        f.write("not media")
    with open(os.path.join(dest, "Thumbs.db"), "wb") as f:
        f.write(b"\x00" * 64)

    # 6) PNG(多格式集合由 Config.image_formats 覆盖)
    Image.new("RGB", (16, 16), (5, 5, 250)).save(os.path.join(dest, "sample.png"))

    print(f"fixtures ready: {dest}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/phototidy_fixtures"))
