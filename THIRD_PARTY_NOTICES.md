# Third-Party Notices

PhotoTidy source code is licensed under the MIT License. Runtime, development, and packaging
dependencies are distributed under their own licenses.

## Runtime Dependencies

Before publishing a release, verify the exact dependency versions installed by CI and keep this file
in sync with the release artifacts.

| Package | Purpose | License note |
|---|---|---|
| Pillow | Image metadata reading | HPND |
| Mutagen | Video metadata reading | GPL-2.0-or-later; binary distributions that bundle this package need GPL compliance review |
| python-dateutil | Date parsing | BSD / Apache dual license |
| Typer | CLI framework | MIT |
| PyYAML | YAML config parsing | MIT |
| CustomTkinter | GUI framework | See upstream package metadata |

## Binary Release Gate

PyInstaller builds can bundle runtime dependencies into the macOS app or Windows executable. Do not
publish binary installers until the release owner has checked the bundled dependency licenses and
included all required notices or source-offer materials.

