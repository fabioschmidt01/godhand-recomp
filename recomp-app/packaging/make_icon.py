"""Gera o ícone do executável (o monograma do launcher) em PNG, para o PyInstaller.

Uso: python make_icon.py <saida.png>
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pasta recomp-app

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from recomp.gui import app_icon  # noqa: E402

app = QApplication([])
app_icon().pixmap(256, 256).save(sys.argv[1])
print("ícone:", sys.argv[1])
