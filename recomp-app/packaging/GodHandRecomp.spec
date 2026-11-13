# PyInstaller: dois executáveis com uma só pasta _internal compartilhada.
#   GodHandRecomp.exe  launcher (sem console)
#   recomp-cli.exe     linha de comando
# Uso (na pasta recomp-app): pyinstaller packaging/GodHandRecomp.spec
import os

HERE = os.path.abspath(SPECPATH)
APP = os.path.dirname(HERE)
ICON = os.path.join(HERE, "icon.png")
EXCLUDES = ["tkinter", "unittest", "pydoc", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
            "PySide6.QtPdf", "PySide6.QtDBus", "PySide6.QtOpenGL"]

gui = Analysis([os.path.join(HERE, "gui_entry.py")], pathex=[APP], excludes=EXCLUDES)
cli = Analysis([os.path.join(HERE, "cli_entry.py")], pathex=[APP], excludes=EXCLUDES)

gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="GodHandRecomp", console=False,
              icon=ICON if os.path.isfile(ICON) else None)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="recomp-cli", console=True,
              icon=ICON if os.path.isfile(ICON) else None)

COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas, name="GodHandRecomp")
