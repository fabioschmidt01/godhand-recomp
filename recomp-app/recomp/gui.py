"""Launcher (PySide6): a interface do jogador sobre o mesmo núcleo usado pela CLI.

Estrutura no estilo do KarPur Launcher: barra lateral com as páginas e o botão Jogar fixo embaixo, páginas com
título, subtítulo e cartões. A cor de destaque vem do jogo (dourado da God Hand) e pode ser trocada em
Configurações.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import json
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QScrollArea, QSizePolicy, QSlider,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import __version__, sysinfo
from .bios import WHY_TEXT, BiosError, identify_bios
from .config import load_env
from .doctor import run_doctor
from .iso9660 import IsoReader
from .mods import load_mods, load_patches, load_textures, save_item
from .pipeline import STAGE_KEYS, Context, Reporter, StageError, run_game, run_pipeline
from .settings import apply_to_env, load_settings, save_settings

REPO = "fabioschmidt01/godhand-recomp"
# Enquanto o port não chega à tela de título, o launcher diz isso em vez de "pronto para jogar".
PLAYABLE = False
DEV_NOTE = "O port ainda está em desenvolvimento: o jogo ainda não chega à tela de título."

# ------------------------------------------------------------------ temas
# Um matiz por tema; as superfícies só mudam de luminosidade. "auto" é a cor do jogo (o dourado da God Hand).
THEMES = {
    "auto": {"bg": "#13110f", "side": "#1a1714", "card": "#221e1a", "input": "#2b2621", "hover": "#2a2520",
             "text": "#f1e9dd", "text2": "#c9bdac", "muted": "#8f8476", "faint": "#5f574d",
             "accent": "#d9a441", "accent2": "#ecc56b", "ink": "#1b1408", "sel": "rgba(217,164,65,0.15)"},
    "violet": {"bg": "#12101a", "side": "#191626", "card": "#211c33", "input": "#2a2440", "hover": "#272139",
               "text": "#f1eefb", "text2": "#c3bcd9", "muted": "#8e86a8", "faint": "#5d5675",
               "accent": "#8f6cf5", "accent2": "#7f9bff", "ink": "#ffffff", "sel": "rgba(143,108,245,0.18)"},
    "blue": {"bg": "#0f1217", "side": "#151a21", "card": "#1b222b", "input": "#232c37", "hover": "#212a34",
             "text": "#eef3f8", "text2": "#bac6d3", "muted": "#7f8c9b", "faint": "#525e6b",
             "accent": "#4c9bff", "accent2": "#62d0ff", "ink": "#06111f", "sel": "rgba(76,155,255,0.16)"},
}
THEME_NAMES = {"auto": "Automático (do jogo)", "violet": "Violeta", "blue": "Azul"}
OK, WARN, ERR = "#7fbf86", "#e0a050", "#e06a5a"
T: dict[str, str] = dict(THEMES["auto"])  # tema ativo; os widgets desenhados à mão leem daqui

ICON = {"home": "", "setup": "", "settings": "", "saves": "", "patches": "",
        "mods": "", "system": "", "update": "", "feedback": "", "play": "",
        "browse": "", "build": "", "repair": "", "cancel": "", "info": "",
        "check": "", "copy": "", "export": "", "import": "", "folder": "",
        "help": ""}
ICON_FONT = "Segoe MDL2 Assets"
UI = "Segoe UI"
MONO = "Cascadia Mono"
DISPLAY = "Bahnschrift Condensed"

STAGE_TEXT = {
    "ingest": "Identificando a ISO", "patch_iso": "Aplicando os patches", "extract": "Extraindo o disco",
    "analyze": "Preparando o mapa de funções", "recompile": "Convertendo o jogo para C++",
    "build": "Compilando o executável",
}
NINJA_STEP = re.compile(r"^\[(\d+)/(\d+)\]")


def font(px: int, weight: QFont.Weight = QFont.Weight.Normal, family: str = UI) -> QFont:
    f = QFont(family)
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


def chevron_png(color: str) -> str:
    """Seta do combo, desenhada uma vez por cor (o QSS precisa de um arquivo de imagem)."""
    folder = Path(tempfile.gettempdir()) / "godhand-launcher"
    folder.mkdir(exist_ok=True)
    path = folder / f"chevron-{color.strip('#')}.png"
    if not path.is_file():
        pm = QPixmap(24, 24)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(color), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPolyline([QPointF(7, 10), QPointF(12, 15), QPointF(17, 10)])
        p.end()
        pm.save(str(path))
    return path.as_posix()


def qss(t: dict[str, str]) -> str:
    line = "rgba(255,255,255,0.06)"
    line2 = "rgba(255,255,255,0.11)"
    return f"""
QMainWindow, QWidget#content, QStackedWidget, QScrollArea, QWidget#scrollBody {{ background: {t['bg']}; }}
QWidget {{ color: {t['text']}; }}
QLabel {{ background: transparent; }}
QWidget#sidebar {{ background: {t['side']}; border-right: 1px solid {line}; }}
QFrame#card {{ background: {t['card']}; border: 1px solid {line}; border-radius: 12px; }}
QFrame#row {{ background: transparent; border-radius: 8px; }}
QFrame#row:hover {{ background: {t['hover']}; }}
QLabel#title {{ font-size: 30px; font-weight: 700; }}
QLabel#subtitle {{ color: {t['text2']}; font-size: 13px; }}
QLabel#label {{ color: {t['text']}; }}
QLabel#hint {{ color: {t['muted']}; font-size: 12px; }}
QLabel#kicker {{ color: {t['muted']}; font-size: 11px; font-weight: 700; }}
QLabel#status {{ color: {t['text2']}; font-size: 13px; }}
QLabel#ok {{ color: {OK}; font-size: 12px; }}
QLabel#warn {{ color: {WARN}; font-size: 12px; }}
QLabel#err {{ color: {ERR}; font-size: 12px; }}
QLabel#link {{ color: {t['accent']}; font-size: 12px; }}
QLineEdit, QPlainTextEdit#field {{ background: {t['input']}; border: 1px solid {line}; border-radius: 8px;
    padding: 0 12px; min-height: 42px; selection-background-color: {t['accent']}; selection-color: {t['ink']}; }}
QPlainTextEdit#field {{ padding: 8px 10px; }}
QLineEdit:focus, QPlainTextEdit#field:focus {{ border: 1px solid {t['accent']}; }}
QLineEdit:read-only {{ color: {t['text2']}; }}
QComboBox {{ background: {t['input']}; border: 1px solid {line}; border-radius: 8px; padding: 0 12px; min-height: 40px; }}
QComboBox:hover {{ border: 1px solid {line2}; }}
QComboBox:focus {{ border: 1px solid {t['accent']}; }}
QComboBox::drop-down {{ border: none; width: 34px; }}
QComboBox::down-arrow {{ image: url("{chevron_png(t['text2'])}"); width: 16px; height: 16px; }}
QComboBox QAbstractItemView {{ background: {t['card']}; border: 1px solid {line2}; outline: 0; padding: 4px;
    selection-background-color: {t['sel']}; selection-color: {t['text']}; }}
QSlider::groove:horizontal {{ height: 4px; background: {t['input']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t['text2']}; width: 10px; height: 20px; margin: -8px 0; border-radius: 3px; }}
QSlider::handle:horizontal:hover {{ background: {t['text']}; }}
QProgressBar {{ background: {t['input']}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ border-radius: 3px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
    stop:0 {t['accent']}, stop:1 {t['accent2']}); }}
QPlainTextEdit#log {{ background: {t['input']}; color: {t['text2']}; border: 1px solid {line}; border-radius: 8px;
    padding: 8px; font-family: "{MONO}"; font-size: 12px; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.14); border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.24); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{ height: 0; background: none; }}
QToolTip {{ background: {t['card']}; color: {t['text']}; border: 1px solid {line2}; padding: 6px 8px; }}
QMessageBox {{ background: {t['card']}; }}
QMessageBox QLabel {{ min-width: 440px; }}
QMessageBox QPushButton {{ background: {t['input']}; border: 1px solid {line2}; border-radius: 6px;
    padding: 7px 20px; min-width: 70px; }}
"""


# ------------------------------------------------------------------ comunicação com as threads
class Signals(QObject):
    log = Signal(str)
    stage = Signal(str, str, str)
    progress = Signal(str, float)
    finished = Signal(bool)
    doctor = Signal(list)
    update = Signal(str, str)


class QtReporter(Reporter):
    def __init__(self, signals: Signals):
        self.s = signals

    def log(self, msg: str) -> None:
        self.s.log.emit(msg)

    def stage(self, key: str, status: str, detail: str = "") -> None:
        self.s.stage.emit(key, status, detail)

    def progress(self, key: str, fraction: float) -> None:
        self.s.progress.emit(key, fraction)


class Worker(QThread):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        self.fn()


# ------------------------------------------------------------------ componentes
def text(value: str, name: str = "", wrap: bool = False) -> QLabel:
    w = QLabel(value)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def card() -> tuple[QFrame, QVBoxLayout]:
    c = QFrame()
    c.setObjectName("card")
    lay = QVBoxLayout(c)
    lay.setContentsMargins(24, 20, 24, 20)
    lay.setSpacing(12)
    return c, lay


class Button(QAbstractButton):
    """Botão com ícone do Windows e texto. primary: degradê na cor de destaque; secondary: contorno."""

    def __init__(self, glyph: str, label: str, kind: str = "secondary", slot=None):
        super().__init__()
        self.glyph, self.kind = glyph, kind
        self.setText(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._hover = False
        if slot is not None:
            self.clicked.connect(slot)

    def sizeHint(self) -> QSize:
        width = QFontMetrics(font(14, QFont.Weight.DemiBold)).horizontalAdvance(self.text())
        return QSize(width + (58 if self.glyph else 32), 44)

    def setText(self, value: str) -> None:  # noqa: N802 - mesmo nome do Qt
        super().setText(value)
        self.updateGeometry()
        self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        enabled = self.isEnabled()
        if not enabled:
            p.setOpacity(0.42)
        if self.kind == "primary":
            g = QLinearGradient(r.topLeft(), r.topRight())
            a, b = QColor(T["accent"]), QColor(T["accent2"])
            if self._hover and enabled:
                a, b = a.lighter(110), b.lighter(110)
            if self.isDown():
                a, b = a.darker(112), b.darker(112)
            g.setColorAt(0, a)
            g.setColorAt(1, b)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            fg = QColor(T["ink"])
        else:
            bg = QColor(T["hover"] if (self._hover and enabled) else T["card"])
            if self.isDown():
                bg = bg.lighter(120)
            p.setPen(QPen(QColor(255, 255, 255, 30), 1))
            p.setBrush(bg)
            fg = QColor(T["text"])
        p.drawRoundedRect(r, 8, 8)
        if self.hasFocus():
            p.setPen(QPen(QColor(T["accent2"]), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), 7, 7)
        x = 16.0
        p.setPen(fg)
        if self.glyph:
            p.setFont(font(15, family=ICON_FONT))
            p.drawText(QRectF(x, 0, 18, r.height()), Qt.AlignmentFlag.AlignVCenter, self.glyph)
            x += 26
        p.setFont(font(14, QFont.Weight.DemiBold))
        p.drawText(QRectF(x, 0, r.width() - x, r.height()), Qt.AlignmentFlag.AlignVCenter, self.text())


class NavButton(QAbstractButton):
    """Item da barra lateral: ícone + texto; o selecionado ganha fundo e a barrinha na cor de destaque."""

    def __init__(self, glyph: str, label: str):
        super().__init__()
        self.glyph = glyph
        self.setText(label)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(42)
        self._hover = False

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T["sel"]) if not T["sel"].startswith("rgba") else _rgba(T["sel"]))
            p.drawRoundedRect(r, 8, 8)
            p.setBrush(QColor(T["accent"]))
            p.drawRoundedRect(QRectF(0, 11, 3, r.height() - 22), 1.5, 1.5)
        elif self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 10))
            p.drawRoundedRect(r, 8, 8)
        color = QColor(T["text"] if self.isChecked() else T["text2"])
        p.setPen(QColor(T["accent2"]) if self.isChecked() else color)
        p.setFont(font(15, family=ICON_FONT))
        p.drawText(QRectF(16, 0, 20, r.height()), Qt.AlignmentFlag.AlignVCenter, self.glyph)
        p.setPen(color)
        p.setFont(font(14, QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Normal))
        p.drawText(QRectF(48, 0, r.width() - 48, r.height()), Qt.AlignmentFlag.AlignVCenter, self.text())


def _rgba(value: str) -> QColor:
    nums = [float(x) for x in value[value.index("(") + 1:value.index(")")].split(",")]
    return QColor(int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3] * 255))


class PlayButton(QAbstractButton):
    """O botão Jogar fixo no rodapé da barra lateral: contorno enquanto o jogo não está pronto, cheio quando está."""

    def __init__(self, slot):
        super().__init__()
        self.setText("Jogar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(54)
        self.ready = False
        self._hover = False
        self.clicked.connect(slot)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.ready and self.isEnabled():
            g = QLinearGradient(r.topLeft(), r.topRight())
            a, b = QColor(T["accent"]), QColor(T["accent2"])
            if self._hover:
                a, b = a.lighter(110), b.lighter(110)
            g.setColorAt(0, a)
            g.setColorAt(1, b)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            fg = QColor(T["ink"])
        else:
            p.setPen(QPen(QColor(255, 255, 255, 34), 1))
            p.setBrush(QColor(T["card"]) if self._hover else QColor(T["side"]).lighter(112))
            fg = QColor(T["text2"])
        p.drawRoundedRect(r, 10, 10)
        label = self.text()
        lf = font(18, QFont.Weight.DemiBold)
        width = 26 + QFontMetrics(lf).horizontalAdvance(label)
        x = (r.width() - width) / 2
        p.setPen(fg)
        p.setFont(font(16, family=ICON_FONT))
        p.drawText(QRectF(x, 0, 20, r.height()), Qt.AlignmentFlag.AlignVCenter, ICON["play"])
        p.setFont(lf)
        p.drawText(QRectF(x + 26, 0, width, r.height()), Qt.AlignmentFlag.AlignVCenter, label)


class Check(QAbstractButton):
    """Caixa de seleção arredondada, preenchida na cor de destaque quando marcada."""

    def __init__(self, label: str = "", checked: bool = False):
        super().__init__()
        self.setText(label)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self) -> QSize:
        width = QFontMetrics(font(14)).horizontalAdvance(self.text()) if self.text() else 0
        return QSize(24 + (12 + width if width else 0), 32)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(1, (self.height() - 22) / 2, 22, 22)
        if self.isChecked():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T["accent"]))
            p.drawRoundedRect(box, 6, 6)
            p.setPen(QColor(T["ink"]))
            p.setFont(font(12, family=ICON_FONT))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, ICON["check"])
        else:
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.setBrush(QColor(T["input"]))
            p.drawRoundedRect(box, 6, 6)
        if self.hasFocus():
            p.setPen(QPen(QColor(T["accent2"]), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(box.adjusted(-2, -2, 2, 2), 8, 8)
        if self.text():
            p.setPen(QColor(T["text"]))
            p.setFont(font(14))
            p.drawText(QRectF(36, 0, self.width() - 36, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())


class Banner(QWidget):
    """Faixa da página inicial. Com as imagens do usuário em art/ (banner.png e logo.png), mostra a arte do jogo;
    sem elas, um degradê com o nome desenhado. A arte nunca vai para o repositório."""

    def __init__(self, art: Path):
        super().__init__()
        self.setMinimumHeight(330)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.photo = QPixmap(str(art / "banner.png")) if (art / "banner.png").is_file() else QPixmap()
        self.logo = QPixmap(str(art / "logo.png")) if (art / "logo.png").is_file() else QPixmap()
        self._cache: dict[str, tuple[QSize, QPixmap]] = {}

    @property
    def has_art(self) -> bool:
        return not self.photo.isNull()

    def _scaled(self, key: str, source: QPixmap, size: QSize, mode: Qt.AspectRatioMode) -> QPixmap:
        cached = self._cache.get(key)
        if cached is None or cached[0] != size:
            pm = source.scaled(size * self.devicePixelRatioF(), mode, Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(self.devicePixelRatioF())
            self._cache[key] = (size, pm)
        return self._cache[key][1]

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        if self.has_art:
            # cobre a faixa inteira, ancorada à direita: o Gene fica inteiro e o branco à esquerda recebe o logo
            photo = self._scaled("photo", self.photo, self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            dpr = photo.devicePixelRatio()
            pw, ph = photo.width() / dpr, photo.height() / dpr
            p.drawPixmap(QPointF(r.width() - pw, (r.height() - ph) / 2), photo)
            if not self.logo.isNull():
                box = QSize(int(r.width() * 0.32), int(r.height() * 0.56))
                logo = self._scaled("logo", self.logo, box, Qt.AspectRatioMode.KeepAspectRatio)
                p.drawPixmap(QPointF(34, 24), logo)
            p.setPen(QPen(QColor(0, 0, 0, 90), 1))
            p.drawLine(QPointF(0, r.height() - 0.5), QPointF(r.width(), r.height() - 0.5))
            return
        base = QLinearGradient(r.topLeft(), r.bottomRight())
        base.setColorAt(0, QColor(T["side"]).lighter(118))
        base.setColorAt(1, QColor(T["bg"]))
        p.fillRect(r, base)
        glow = QRadialGradient(QPointF(r.width() * 0.74, r.height() * 0.38), r.width() * 0.42)
        c = QColor(T["accent"])
        c.setAlpha(70)
        glow.setColorAt(0, c)
        c.setAlpha(0)
        glow.setColorAt(1, c)
        p.fillRect(r, glow)
        p.setPen(QPen(QColor(255, 255, 255, 8), 1))  # textura de riscos diagonais, bem leve
        for x in range(-int(r.height()), int(r.width()), 14):
            p.drawLine(QPointF(x, r.height()), QPointF(x + r.height(), 0))
        title = QFont(DISPLAY)
        title.setPixelSize(int(min(118, r.width() * 0.13)))
        title.setWeight(QFont.Weight.Bold)
        title.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4)
        metrics = QFontMetrics(title)
        label = "GOD HAND"
        tw = metrics.horizontalAdvance(label)
        x = r.width() - tw - 40
        y = 40 + metrics.ascent()
        path = QPainterPath()
        path.addText(QPointF(x, y), title, label)
        fill = QLinearGradient(QPointF(0, y - metrics.ascent()), QPointF(0, y))
        fill.setColorAt(0, QColor(T["accent2"]).lighter(112))
        fill.setColorAt(1, QColor(T["accent"]))
        p.setPen(QPen(QColor(0, 0, 0, 120), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawPath(path)
        p.setPen(QColor(T["text2"]))
        p.setFont(font(13, QFont.Weight.DemiBold))
        p.drawText(QRectF(x, y + 10, tw, 20), Qt.AlignmentFlag.AlignRight, "PLAYSTATION 2 · PORT NATIVO PARA PC")


class Page(QWidget):
    """Página com rolagem: título, subtítulo e o conteúdo abaixo."""

    def __init__(self, title: str, subtitle: str):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("scrollBody")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(34, 26, 34, 28)
        self.body.setSpacing(14)
        if title:
            head = QVBoxLayout()
            head.setSpacing(2)
            head.addWidget(text(title, "title"))
            head.addWidget(text(subtitle, "subtitle", wrap=True))
            self.body.addLayout(head)
            self.body.addSpacing(4)
        scroll.setWidget(body)
        outer.addWidget(scroll)

    def finish(self) -> None:
        self.body.addStretch(1)


def form_grid() -> QGridLayout:
    g = QGridLayout()
    g.setHorizontalSpacing(16)
    g.setVerticalSpacing(10)
    g.setColumnMinimumWidth(0, 190)
    g.setColumnStretch(1, 1)
    return g


def iso_serial(path: Path) -> str | None:
    """Lê só o SYSTEM.CNF da ISO (alguns setores) para mostrar o serial ao escolher o arquivo."""
    try:
        with IsoReader(path) as iso:
            cnf = iso.read("SYSTEM.CNF").decode("ascii", "replace")
    except (OSError, ValueError, FileNotFoundError):
        return None
    m = re.search(r"BOOT2\s*=\s*cdrom0:\\?([^;\s]+)", cnf, re.I)
    return m.group(1).replace("_", "-").replace(".", "") if m else None


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(path)  # noqa: S606 - abre a pasta no Explorer


# ------------------------------------------------------------------ páginas
class ItemsPage(Page):
    """Patches ou mods: cada um liga e desliga; o estado fica gravado na pasta deles."""

    def __init__(self, title: str, subtitle: str, intro: str, folders: list[tuple[str, Path]], loader, noun: str, log):
        super().__init__(title, subtitle)
        self.loader, self.noun, self.log, self.folders = loader, noun, log, folders
        box, lay = card()
        lay.addWidget(text(intro, "status", wrap=True))
        actions = QHBoxLayout()
        actions.setSpacing(10)
        for label, path in folders:
            actions.addWidget(Button(ICON["folder"], label, slot=lambda _=False, p=path: open_folder(p)))
        actions.addWidget(Button(ICON["repair"], "Atualizar lista", slot=self.reload))
        actions.addStretch(1)
        lay.addLayout(actions)
        self.body.addWidget(box)
        self.list_card, self.rows = card()
        self.rows.setContentsMargins(10, 10, 10, 10)
        self.rows.setSpacing(2)
        self.body.addWidget(self.list_card)
        self.empty = text("", "subtitle", wrap=True)
        self.body.addWidget(self.empty)
        self.finish()

    def reload(self) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        items = self.loader()
        self.list_card.setVisible(bool(items))
        where = " ou ".join(str(p) for _, p in self.folders)
        self.empty.setText("" if items else f"Nenhum {self.noun.lower()} ainda. Solte os arquivos em {where} "
                                            "e clique em Atualizar lista.")
        for item in items:
            self.rows.addWidget(self._row(item))

    def _row(self, item) -> QFrame:
        row = QFrame()
        row.setObjectName("row")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 10, 14, 10)
        lay.setSpacing(14)
        check = Check(checked=item.enabled)
        check.toggled.connect(lambda on, it=item: self.toggled(it, on))
        info = QVBoxLayout()
        info.setSpacing(1)
        info.addWidget(text(item.name, "label"))
        kind = {"xdelta": "patch de ISO (.xdelta)", "pnach": "patch de memória (.pnach)",
                "files": "substituição de arquivos", "textures": "pacote de texturas"}.get(item.type, item.type)
        info.addWidget(text(f"{kind} · {item.id}", "hint"))
        state = text("aplicado ao preparar" if item.applied else "ainda sem efeito", "ok" if item.applied else "hint")
        lay.addWidget(check)
        lay.addLayout(info, 1)
        lay.addWidget(state)
        return row

    def toggled(self, item, on: bool) -> None:
        item.enabled = on
        save_item(item)
        self.log(f"{self.noun} {item.id}: {'ativado' if on else 'desativado'}.")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.env = load_env()
        self.settings = load_settings(self.env)
        self.env = apply_to_env(self.env)
        QApplication.instance().setFont(font(14))
        set_theme(self.settings.get("theme", "auto"))
        self.signals = Signals()
        self.worker: Worker | None = None
        self.ctx: Context | None = None
        self.mode = "idle"  # idle | pipeline | game | error
        self.current_stage = ""
        self.error: tuple[str, str] | None = None
        self.started_at = 0.0
        self.step_text = ""
        self.missing_tools: list[str] = []
        self.setWindowTitle("God Hand Launcher")
        self.setWindowIcon(app_icon())
        self.resize(1240, 820)
        self.setMinimumSize(1000, 680)

        root = QWidget()
        root.setObjectName("content")
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.pages = QStackedWidget()
        self.page_names: list[str] = []
        lay.addWidget(self._sidebar())
        lay.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        self.home_page = self._home_page()
        self.setup_page = self._setup_page()
        self.settings_page = self._settings_page()
        self.saves_page = self._saves_page()
        self.patches_page = ItemsPage(
            "Patches", "Mudam o jogo antes de ele ser preparado. Depois de mudar algo, use Recompilar em Preparação.",
            "Arquivos .xdelta (por exemplo, o Uncensor) são aplicados à ISO antes de preparar o jogo. Os .pnach são "
            "reconhecidos, mas ainda não têm efeito. Quando dois patches mudam a mesma parte, vale o de baixo na lista.",
            [("Abrir pasta de patches", self.env.patches)], lambda: load_patches(self.env.patches), "Patch",
            self.log_line)
        self.mods_page = ItemsPage(
            "Mods (experimental)", "Seus mods para o jogo, cada um ligado ou desligado.",
            "Mods substituem arquivos do disco (uma pasta por mod) e pacotes de texturas trocam as texturas do jogo. "
            "Os dois já são reconhecidos e passam a ter efeito em versões futuras: nada do seu jogo é alterado.",
            [("Abrir pasta de mods", self.env.mods), ("Abrir pasta de texturas", self.env.textures)],
            lambda: load_mods(self.env.mods) + load_textures(self.env.textures), "Mod", self.log_line)
        self.system_page = self._system_page()
        self.update_page = self._update_page()
        self.feedback_page = self._feedback_page()
        for page in (self.home_page, self.setup_page, self.settings_page, self.saves_page, self.patches_page,
                     self.mods_page, self.system_page, self.update_page, self.feedback_page):
            self.pages.addWidget(page)
        self.go(0)

        self.signals.log.connect(self.on_log)
        self.signals.stage.connect(self.set_stage)
        self.signals.progress.connect(self.on_progress)
        self.signals.finished.connect(self.pipeline_done)
        self.signals.doctor.connect(self.show_doctor)
        self.signals.update.connect(self.show_update)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self.refresh)

        self.patches_page.reload()
        self.mods_page.reload()
        self.refresh_files()
        self.refresh()
        self.start_doctor()

    # ---------------------------------------------------------- barra lateral
    def _sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(240)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 20, 14, 22)
        lay.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(12)
        brand.setContentsMargins(6, 0, 0, 0)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(48, 48))
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(text("God Hand", ""))
        names.itemAt(0).widget().setFont(font(22, QFont.Weight.Bold))
        names.addWidget(text(f"Versão {__version__}", "hint"))
        brand.addWidget(logo)
        brand.addLayout(names, 1)
        lay.addLayout(brand)
        lay.addSpacing(26)
        kicker = text("GOD HAND RECOMP", "kicker")
        kicker.setContentsMargins(8, 0, 0, 6)
        lay.addWidget(kicker)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        items = [("home", "Início"), ("setup", "Preparação"), ("settings", "Configurações"), ("saves", "Saves"),
                 ("patches", "Patches"), ("mods", "Mods"), ("system", "Sistema"), ("update", "Atualizações"),
                 ("feedback", "Feedback")]
        for i, (key, label) in enumerate(items):
            b = NavButton(ICON[key], label)
            self.nav.addButton(b, i)
            self.page_names.append(label)
            lay.addWidget(b)
        self.nav.idClicked.connect(self.go)
        lay.addStretch(1)
        self.play_button = PlayButton(self.primary_play)
        lay.addWidget(self.play_button)
        return side

    def go(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.nav.button(index).setChecked(True)

    # ---------------------------------------------------------- páginas
    def _home_page(self) -> Page:
        page = Page("", "")
        page.body.setContentsMargins(0, 0, 0, 28)
        self.banner = Banner(self.env.art)
        banner_lay = QVBoxLayout(self.banner)
        banner_lay.setContentsMargins(34, 0, 34, 28)
        banner_lay.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(24)
        self.home_status = text("", "subtitle", wrap=True)
        self.home_status.setFixedWidth(430)
        if self.banner.has_art:  # texto escuro sobre o estúdio branco da foto
            self.home_status.setStyleSheet("color: #3a332c; font-size: 13px; font-weight: 600;")
        row.addWidget(self.home_status, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        self.home_button = Button(ICON["setup"], "Ir para Preparação", "primary", self.home_action)
        row.addWidget(self.home_button, 0, Qt.AlignmentFlag.AlignBottom)
        banner_lay.addLayout(row)
        page.body.addWidget(self.banner)
        tiles = QHBoxLayout()
        tiles.setContentsMargins(34, 8, 34, 0)
        tiles.setSpacing(14)
        self.tiles = {}
        for key, title in (("iso", "ISO DO JOGO"), ("bios", "BIOS DO PS2"), ("game", "JOGO NATIVO")):
            box, lay = card()
            lay.setSpacing(4)
            lay.addWidget(text(title, "kicker"))
            value = text("", "label")
            value.setFont(font(16, QFont.Weight.DemiBold))
            note = text("", "hint", wrap=True)
            lay.addWidget(value)
            lay.addWidget(note)
            self.tiles[key] = (value, note)
            tiles.addWidget(box, 1)
        page.body.addLayout(tiles)
        page.finish()
        return page

    def _setup_page(self) -> Page:
        page = Page("Preparação", "Escolha a sua ISO e a BIOS do seu PS2. Tudo fica no seu PC.")
        box, lay = card()
        grid = form_grid()
        self.iso_field = QLineEdit()
        self.iso_field.setPlaceholderText("God Hand (USA), SLUS-21503, em .iso")
        self.iso_field.editingFinished.connect(lambda: self.set_iso(self.iso_field.text().strip().strip('"')))
        self.iso_note = text("", "hint")
        self.bios_field = QLineEdit()
        self.bios_field.setPlaceholderText("Arquivo principal da BIOS do seu PS2 (4 MB)")
        self.bios_field.editingFinished.connect(lambda: self.set_bios(self.bios_field.text().strip().strip('"')))
        self.bios_note = text("", "hint")
        folder_field = QLineEdit(str(self.env.root))
        folder_field.setReadOnly(True)
        rows = [
            ("ISO do jogo (.iso)", self.iso_note, self.iso_field, Button(ICON["browse"], "Procurar", slot=self.pick_iso)),
            ("BIOS do PS2", self.bios_note, self.bios_field, Button(ICON["browse"], "Procurar", slot=self.pick_bios)),
            ("Pasta do jogo", text("arquivos preparados e o executável", "hint"), folder_field,
             Button(ICON["folder"], "Abrir", slot=lambda: open_folder(self.env.root))),
        ]
        for i, (label, note, field, action) in enumerate(rows):
            left = QVBoxLayout()
            left.setSpacing(0)
            left.addWidget(text(label, "label"))
            left.addWidget(note)
            grid.addLayout(left, i, 0)
            grid.addWidget(field, i, 1)
            grid.addWidget(action, i, 2)
        lay.addLayout(grid)
        why = QHBoxLayout()
        why.setSpacing(10)
        icon = text(ICON["info"], "hint")
        icon.setFont(font(14, family=ICON_FONT))
        why.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        why.addWidget(text("O jogo é traduzido para código nativo do PC, não é emulado. A BIOS do seu console serve só "
                           "ao processador de entrada e saída (som, controle, cartão de memória). Ela é só lida, nunca "
                           "alterada nem enviada.", "hint", wrap=True), 1)
        self.why_link = text("", "link")
        self.why_link.linkActivated.connect(lambda _: self.why_bios())
        self.why_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.paint_link()
        why.addWidget(self.why_link, 0, Qt.AlignmentFlag.AlignTop)
        lay.addSpacing(4)
        lay.addLayout(why)
        page.body.addWidget(box)

        box, lay = card()
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.build_btn = Button(ICON["build"], "Preparar", "primary", lambda: self.start("build", force_all=False))
        self.repair_btn = Button(ICON["repair"], "Recompilar tudo", slot=lambda: self.start("build", force_all=True))
        self.repair_btn.setToolTip("Refaz todas as etapas do zero, ignorando o que já estava pronto.")
        self.cancel_btn = Button(ICON["cancel"], "Cancelar", slot=self.cancel)
        self.cancel_btn.setEnabled(False)
        self.details = Check("Mostrar detalhes")
        self.details.toggled.connect(lambda on: self.log.setVisible(on))
        for w in (self.build_btn, self.repair_btn, self.cancel_btn):
            actions.addWidget(w)
        actions.addSpacing(10)
        actions.addWidget(self.details)
        actions.addStretch(1)
        lay.addLayout(actions)
        lay.addSpacing(6)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        lay.addWidget(self.progress)
        self.setup_status = text("", "status", wrap=True)
        lay.addWidget(self.setup_status)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(20000)
        self.log.setMinimumHeight(260)
        self.log.hide()
        lay.addWidget(self.log)
        page.body.addWidget(box)
        page.finish()
        return page

    def _settings_page(self) -> Page:
        page = Page("Configurações", "A cor muda na hora; as opções de compilação valem na próxima preparação.")
        box, lay = card()
        grid = form_grid()
        self.theme_box = QComboBox()
        for key, name in THEME_NAMES.items():
            self.theme_box.addItem(name, key)
        self.theme_box.setCurrentIndex(list(THEME_NAMES).index(self.settings.get("theme", "auto")))
        self.theme_box.setMinimumWidth(320)
        self.theme_box.currentIndexChanged.connect(lambda _: self.change_theme(self.theme_box.currentData()))
        grid.addWidget(text("Cor do launcher", "label"), 0, 0)
        grid.addWidget(self.theme_box, 0, 1, Qt.AlignmentFlag.AlignLeft)

        jobs_row = QHBoxLayout()
        self.jobs = QSlider(Qt.Orientation.Horizontal)
        self.jobs.setRange(1, sysinfo.threads())
        self.jobs.setValue(self.env.jobs)
        self.jobs.setFixedWidth(320)
        self.jobs_label = text("", "label")
        self.jobs.valueChanged.connect(self.change_jobs)
        jobs_row.addWidget(self.jobs)
        jobs_row.addSpacing(12)
        jobs_row.addWidget(self.jobs_label)
        jobs_row.addStretch(1)
        left = QVBoxLayout()
        left.setSpacing(0)
        left.addWidget(text("Compilações em paralelo", "label"))
        left.addWidget(text("mais rápido, mas usa mais memória", "hint"))
        grid.addLayout(left, 1, 0)
        grid.addLayout(jobs_row, 1, 1)
        self.change_jobs(self.env.jobs, save=False)

        self.opt_box = QComboBox()
        self.opt_box.addItem("Compilar mais rápido (-O1)", "-O1")
        self.opt_box.addItem("Jogo mais rápido (-O2, compila mais devagar)", "-O2")
        self.opt_box.setCurrentIndex(0 if self.env.opt_level == "-O1" else 1)
        self.opt_box.setMinimumWidth(320)
        self.opt_box.currentIndexChanged.connect(
            lambda _: (save_settings(self.env, opt_level=self.opt_box.currentData()),
                       setattr(self, "env", apply_to_env(self.env))))
        grid.addWidget(text("Otimização do código", "label"), 2, 0)
        grid.addWidget(self.opt_box, 2, 1, Qt.AlignmentFlag.AlignLeft)
        lay.addLayout(grid)
        page.body.addWidget(box)
        page.finish()
        return page

    def _saves_page(self) -> Page:
        page = Page("Saves", "Seu progresso, guardado no cartão de memória virtual na pasta do jogo. Faça cópias, "
                             "leve para outro PC ou traga uma de volta.")
        box, lay = card()
        lay.addWidget(text("CARTÃO DE MEMÓRIA", "kicker"))
        self.saves_title = text("", "label")
        self.saves_title.setFont(font(15, QFont.Weight.DemiBold))
        lay.addWidget(self.saves_title)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.export_btn = Button(ICON["export"], "Exportar…", slot=self.export_saves)
        self.import_btn = Button(ICON["import"], "Importar…", slot=self.import_saves)
        self.saves_open = Button(ICON["folder"], "Abrir pasta", slot=lambda: open_folder(self.memcard_dir()))
        for w in (self.export_btn, self.import_btn, self.saves_open):
            actions.addWidget(w)
        actions.addStretch(1)
        lay.addSpacing(4)
        lay.addLayout(actions)
        lay.addWidget(text("Feche o jogo antes de importar. Importar guarda o cartão atual como cópia de segurança.",
                           "hint", wrap=True))
        page.body.addWidget(box)
        page.finish()
        return page

    def _system_page(self) -> Page:
        page = Page("Sistema", "O que o app precisa encontrar neste PC para preparar o jogo. Itens opcionais só servem "
                               "ao desenvolvimento.")
        box, lay = card()
        top = QHBoxLayout()
        self.check_btn = Button(ICON["repair"], "Verificar de novo", slot=self.start_doctor)
        top.addWidget(self.check_btn)
        top.addSpacing(12)
        self.system_summary = text("", "status")
        top.addWidget(self.system_summary)
        top.addStretch(1)
        lay.addLayout(top)
        page.body.addWidget(box)
        self.checks_card, self.checks = card()
        self.checks.setContentsMargins(10, 10, 10, 10)
        self.checks.setSpacing(0)
        page.body.addWidget(self.checks_card)
        page.finish()
        self.system_count = 0
        return page

    def _update_page(self) -> Page:
        page = Page("Atualizações", "Versões novas vêm das releases do projeto no GitHub. Só o que mudou é refeito.")
        box, lay = card()
        lay.addWidget(text(f"Instalada: versão {__version__}", "label"))
        row = QHBoxLayout()
        self.update_btn = Button(ICON["update"], "Procurar atualizações", "primary", self.check_update)
        row.addWidget(self.update_btn)
        self.update_open = Button(ICON["export"], "Abrir página da versão", slot=lambda: webbrowser.open(self.update_url))
        self.update_open.hide()
        row.addWidget(self.update_open)
        row.addStretch(1)
        lay.addLayout(row)
        self.update_status = text("", "status", wrap=True)
        lay.addWidget(self.update_status)
        self.update_url = f"https://github.com/{REPO}/releases"
        page.body.addWidget(box)
        page.finish()
        return page

    def _feedback_page(self) -> Page:
        page = Page("Feedback", "Conte como o launcher roda no seu PC. Abre como uma issue no GitHub, que você revisa "
                                "antes de enviar.")
        box, lay = card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.fb = {}
        fields = [("Versão do launcher", __version__), ("Windows", sysinfo.windows()), ("CPU", sysinfo.cpu()),
                  ("GPU", sysinfo.gpu()), ("RAM", sysinfo.ram_gb()), ("Jogo", self.settings.get("serial", ""))]
        for i, (label, value) in enumerate(fields):
            field = QLineEdit(value)
            self.fb[label] = field
            grid.addWidget(text(label, "label"), i // 2, (i % 2) * 2)
            grid.addWidget(field, i // 2, (i % 2) * 2 + 1)
        lay.addLayout(grid)
        page.body.addWidget(box)
        box, lay = card()
        head = QHBoxLayout()
        head.addWidget(text("Relato", "label"))
        head.addStretch(1)
        self.fb_count = text("0 / 5000", "hint")
        head.addWidget(self.fb_count)
        lay.addLayout(head)
        self.fb_text = QPlainTextEdit()
        self.fb_text.setObjectName("field")
        self.fb_text.setPlaceholderText("Como roda? O que deu errado? Em que ponto? Logs são bem-vindos.")
        self.fb_text.setMinimumHeight(240)
        self.fb_text.textChanged.connect(self.feedback_changed)
        lay.addWidget(self.fb_text)
        page.body.addWidget(box)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(Button(ICON["export"], "Abrir no GitHub", "primary", self.open_issue))
        row.addWidget(Button(ICON["copy"], "Copiar relatório", slot=self.copy_report))
        row.addWidget(Button(ICON["folder"], "Abrir pasta de logs", slot=lambda: open_folder(self.env.work / "logs")))
        row.addSpacing(10)
        row.addWidget(text("Preenchido a partir do seu PC. Corrija o que estiver errado.", "hint"))
        row.addStretch(1)
        page.body.addLayout(row)
        page.finish()
        return page

    # ---------------------------------------------------------- estado
    def iso_text(self) -> str:
        return self.settings.get("iso") or (str(self.env.iso) if self.env.iso.is_file() else "")

    def game_exe(self) -> Path | None:
        serial = self.settings.get("serial")
        exe = self.env.out / serial / "game.exe" if serial else None
        return exe if exe is not None and exe.is_file() else None

    def memcard_dir(self) -> Path:
        return self.env.out / self.settings.get("serial", "SLUS-21503") / "mc0"

    def refresh_files(self) -> None:
        iso = self.iso_text()
        self.iso_field.setText(iso)
        serial = iso_serial(Path(iso)) if iso and Path(iso).is_file() else None
        if serial:
            self.settings["serial"] = serial
            save_settings(self.env, serial=serial)
        if not iso:
            self.iso_state = ("Nenhuma ISO escolhida", "God Hand (USA), SLUS-21503", "warn")
        elif not Path(iso).is_file():
            self.iso_state = ("Arquivo não encontrado", iso, "err")
        elif serial == "SLUS-21503":
            self.iso_state = ("God Hand (USA)", "SLUS-21503 · reconhecida", "ok")
        else:
            self.iso_state = (serial or "Não reconhecida", "este app é para o God Hand USA (SLUS-21503)", "warn")
        self.iso_note.setText(self.iso_state[1] if iso else "")
        self.iso_note.setObjectName(self.iso_state[2] if iso else "hint")
        bios = self.settings.get("bios", "")
        self.bios_field.setText(bios)
        if not bios:
            self.bios_state = ("Não escolhida", "opcional por enquanto", "hint")
        else:
            try:
                info = identify_bios(Path(bios))
                self.bios_state = (f"BIOS v{info.version}", f"{info.region} · {info.date}", "ok")
            except BiosError as e:
                self.bios_state = ("BIOS inválida", str(e), "err")
        self.bios_note.setText(self.bios_state[1] if bios else "opcional por enquanto")
        self.bios_note.setObjectName(self.bios_state[2])
        for w in (self.iso_note, self.bios_note):
            w.style().unpolish(w)
            w.style().polish(w)

    def refresh(self) -> None:
        exe = self.game_exe()
        busy = self.mode in ("pipeline", "game")
        if self.mode == "pipeline":
            title = STAGE_TEXT.get(self.current_stage, "Preparando")
            n = STAGE_KEYS.index(self.current_stage) + 1 if self.current_stage in STAGE_KEYS else 1
            elapsed = time.monotonic() - self.started_at
            parts = [f"Etapa {n} de {len(STAGE_KEYS)} · {title}…"]
            if self.step_text:
                parts.append(self.step_text)
            parts.append(f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}")
            status = " · ".join(parts)
        elif self.mode == "game":
            status = "O jogo está aberto. Feche a janela do jogo para voltar."
        elif self.mode == "error" and self.error:
            stage, detail = self.error
            status = f"Parou em: {STAGE_TEXT.get(stage, stage).lower()}. {detail}"
        elif exe is not None:
            built = time.strftime("%d/%m/%Y às %H:%M", time.localtime(exe.stat().st_mtime))
            status = (f"Pronto! Jogo preparado em {built}. Clique em Jogar." if PLAYABLE
                      else f"Executável compilado em {built}. {DEV_NOTE}")
        elif not self.iso_text():
            status = "Escolha a ISO (e a BIOS) e clique em Preparar."
        else:
            status = ("Clique em Preparar. O app extrai o disco, converte o código para C++ e compila um executável "
                      "nativo. A primeira vez leva uns 25 minutos; depois, só o que mudar.")
        if self.missing_tools and not busy:
            status += "  Faltam ferramentas: " + ", ".join(self.missing_tools) + " (veja Sistema)."
        self.setup_status.setText(status)
        if self.mode != "pipeline" and not busy:
            self.progress.setValue(1000 if exe is not None and self.mode == "idle" else self.progress.value())
        self.build_btn.setEnabled(not busy and bool(self.iso_text()))
        self.build_btn.setText("Tentar de novo" if self.mode == "error" else ("Recompilar" if exe else "Preparar"))
        self.repair_btn.setEnabled(not busy and bool(self.iso_text()))
        self.cancel_btn.setEnabled(busy)
        self.cancel_btn.setText("Fechar o jogo" if self.mode == "game" else "Cancelar")
        self.check_btn.setEnabled(not busy)
        self.play_button.ready = exe is not None
        self.play_button.setEnabled(not busy)
        self.play_button.setText("Jogando…" if self.mode == "game" else "Jogar")
        self.play_button.update()
        # página inicial
        if self.mode == "pipeline":
            self.home_status.setText(status)
            self.home_button.setText("Ver o progresso")
            self.home_button.glyph = ICON["setup"]
        elif exe is not None:
            self.home_status.setText("Seu jogo está pronto." if PLAYABLE else f"Executável nativo compilado. {DEV_NOTE}")
            self.home_button.setText("Jogar")
            self.home_button.glyph = ICON["play"]
        else:
            self.home_status.setText("Escolha os arquivos do jogo em Preparação para começar.")
            self.home_button.setText("Ir para Preparação")
            self.home_button.glyph = ICON["setup"]
        self.home_button.updateGeometry()
        self.home_button.update()
        self.set_tile("iso", *self.iso_state)
        self.set_tile("bios", *self.bios_state)
        if exe is not None:
            built = time.strftime("%d/%m/%Y %H:%M", time.localtime(exe.stat().st_mtime))
            self.set_tile("game", "Pronto para jogar" if PLAYABLE else "Executável compilado",
                          f"compilado em {built}" if PLAYABLE else f"{built} · ainda não chega ao título",
                          "ok" if PLAYABLE else "warn")
        else:
            self.set_tile("game", "Ainda não preparado", "faça a preparação uma vez", "hint")
        card_dir = self.memcard_dir()
        self.saves_title.setText(str(card_dir) if exe is not None else "Prepare o jogo primeiro")
        for w in (self.export_btn, self.import_btn, self.saves_open):
            w.setEnabled(exe is not None and not busy)

    def set_tile(self, key: str, value: str, note: str, tone: str) -> None:
        v, n = self.tiles[key]
        v.setText(value)
        n.setText(note)
        color = {"ok": OK, "warn": WARN, "err": ERR}.get(tone)
        v.setStyleSheet(f"color: {color};" if color else "")

    # ---------------------------------------------------------- ações
    def home_action(self) -> None:
        if self.mode == "pipeline":
            self.go(1)
        elif self.game_exe() is not None:
            self.play()
        else:
            self.go(1)

    def primary_play(self) -> None:
        if self.game_exe() is not None:
            self.play()
        else:
            self.go(1)

    def set_iso(self, path: str) -> None:
        self.settings["iso"] = path
        save_settings(self.env, iso=path)
        self.refresh_files()
        self.refresh()

    def set_bios(self, path: str) -> None:
        self.settings["bios"] = path
        save_settings(self.env, bios=path)
        self.refresh_files()
        self.refresh()
        self.start_doctor()

    def pick_iso(self) -> None:
        start = str(Path(self.iso_text()).parent) if self.iso_text() else str(self.env.root)
        path, _ = QFileDialog.getOpenFileName(self, "Escolher a ISO do God Hand", start,
                                              "Imagem de disco (*.iso);;Todos os arquivos (*)")
        if path:
            self.set_iso(path)

    def pick_bios(self) -> None:
        current = self.settings.get("bios", "")
        start = str(Path(current).parent) if current else str(self.env.root)
        path, _ = QFileDialog.getOpenFileName(self, "Escolher a BIOS do PS2", start,
                                              "BIOS do PS2 (*.bin *.rom *.rom0);;Todos os arquivos (*)")
        if path:
            self.set_bios(path)

    def paint_link(self) -> None:
        self.why_link.setText(f'<a href="#" style="color:{T["accent"]};text-decoration:none">Por que a BIOS?</a>')

    def why_bios(self) -> None:
        QMessageBox.information(self, "Por que a BIOS?", WHY_TEXT)

    def change_theme(self, key: str) -> None:
        save_settings(self.env, theme=key)
        set_theme(key)
        self.setWindowIcon(app_icon())
        self.paint_link()
        for w in QApplication.allWidgets():
            w.update()

    def change_jobs(self, value: int, save: bool = True) -> None:
        self.jobs_label.setText(f"{value} de {sysinfo.threads()}")
        if save:
            save_settings(self.env, jobs=value)
            self.env = apply_to_env(self.env)

    def export_saves(self) -> None:
        src = self.memcard_dir()
        if not src.is_dir():
            QMessageBox.information(self, "Exportar saves", "Ainda não há saves: jogue e salve uma vez.")
            return
        target, _ = QFileDialog.getSaveFileName(self, "Exportar saves", str(Path.home() / "godhand-saves.zip"),
                                                "Arquivo zip (*.zip)")
        if target:
            shutil.make_archive(str(Path(target).with_suffix("")), "zip", src)
            self.log_line(f"Saves exportados para {target}")

    def import_saves(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "Importar saves", str(Path.home()), "Arquivo zip (*.zip)")
        if not source:
            return
        dest = self.memcard_dir()
        if dest.is_dir() and any(dest.iterdir()):
            backup = dest.with_name(f"mc0-copia-{time.strftime('%Y%m%d-%H%M%S')}")
            shutil.copytree(dest, backup)
            self.log_line(f"Cartão atual guardado em {backup}")
        dest.mkdir(parents=True, exist_ok=True)
        shutil.unpack_archive(source, dest)
        self.log_line(f"Saves importados de {source}")
        QMessageBox.information(self, "Importar saves", "Saves importados.")

    def check_update(self) -> None:
        self.update_status.setText("Procurando…")
        self.update_btn.setEnabled(False)

        def job() -> None:
            try:
                req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                             headers={"User-Agent": "godhand-recomp-launcher",
                                                      "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self.signals.update.emit(data.get("tag_name", ""), data.get("html_url", ""))
            except Exception as e:  # sem rede, limite da API etc.: só informa
                self.signals.update.emit("", f"erro: {e}")

        threading.Thread(target=job, daemon=True).start()

    def show_update(self, tag: str, url: str) -> None:
        self.update_btn.setEnabled(True)
        if not tag:
            self.update_status.setText(f"Não foi possível consultar as releases ({url}).")
            return
        latest = tag.lstrip("v")
        if latest == __version__:
            self.update_status.setText(f"Você está na versão mais recente ({tag}).")
            self.update_open.hide()
        else:
            self.update_status.setText(f"Release mais recente: {tag}.")
            self.update_url = url or self.update_url
            self.update_open.show()

    def feedback_changed(self) -> None:
        n = len(self.fb_text.toPlainText())
        if n > 5000:
            cursor = self.fb_text.textCursor()
            self.fb_text.setPlainText(self.fb_text.toPlainText()[:5000])
            self.fb_text.setTextCursor(cursor)
            n = 5000
        self.fb_count.setText(f"{n} / 5000")

    def report(self) -> str:
        lines = [f"- **{k}:** {v.text()}" for k, v in self.fb.items()]
        return "\n".join(lines) + "\n\n" + self.fb_text.toPlainText()

    def open_issue(self) -> None:
        title = (self.fb_text.toPlainText().strip().splitlines() or ["Relato do launcher"])[0][:80]
        query = urllib.parse.urlencode({"title": title, "body": self.report()[:6000]})
        webbrowser.open(f"https://github.com/{REPO}/issues/new?{query}")

    def copy_report(self) -> None:
        QApplication.clipboard().setText(self.report())
        self.log_line("Relatório copiado.")

    # ---------------------------------------------------------- pipeline e jogo
    def start(self, until: str, force_all: bool = False) -> None:
        if self.worker and self.worker.isRunning():
            return
        self.mode, self.error, self.current_stage, self.step_text = "pipeline", None, "", ""
        self.progress.setValue(0)
        self.started_at = time.monotonic()
        self.clock.start()
        ctx = Context(env=self.env, iso=Path(self.iso_text()), reporter=QtReporter(self.signals),
                      force=set(STAGE_KEYS) if force_all else set())
        self.ctx = ctx
        self.refresh()
        self.worker = Worker(lambda: self.signals.finished.emit(run_pipeline(ctx, until)))
        self.worker.start()

    def play(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        ctx = Context(env=self.env, iso=Path(self.iso_text()), reporter=QtReporter(self.signals))
        self.ctx = ctx
        self.mode = "game"

        def job() -> None:
            try:
                code = run_game(ctx)
                self.signals.log.emit(f"Jogo terminou com código {code}.")
                self.signals.finished.emit(code == 0)
            except StageError as e:
                self.signals.log.emit(f"Não foi possível executar: {e}")
                self.signals.finished.emit(False)

        self.refresh()
        self.worker = Worker(job)
        self.worker.start()

    def cancel(self) -> None:
        if self.ctx is not None and self.worker and self.worker.isRunning():
            self.log_line("Cancelando…")
            self.cancel_btn.setEnabled(False)
            self.ctx.cancel()

    def closeEvent(self, event) -> None:
        """Nunca destrói a janela com uma thread viva: isso derruba o processo."""
        if self.worker and self.worker.isRunning():
            if self.ctx is not None:
                answer = QMessageBox.question(
                    self, "Ainda trabalhando",
                    "Uma etapa ainda está rodando. Fechar agora interrompe essa etapa. Fechar mesmo assim?")
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self.ctx.cancel()
            self.worker.wait()
        event.accept()

    def overall(self, stage_fraction: float = 0.0) -> None:
        if self.current_stage in STAGE_KEYS:
            i = STAGE_KEYS.index(self.current_stage)
            self.progress.setValue(int(1000 * (i + stage_fraction) / len(STAGE_KEYS)))

    def on_log(self, line: str) -> None:
        self.log.appendPlainText(line)
        if self.current_stage == "build":
            m = NINJA_STEP.match(line)
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                self.step_text = f"{done} de {total} partes"
                self.overall(done / total)

    def on_progress(self, key: str, fraction: float) -> None:
        if key == self.current_stage:
            self.step_text = f"{fraction * 100:.0f}%"
            self.overall(fraction)

    def set_stage(self, key: str, status: str, detail: str) -> None:
        if key not in STAGE_TEXT:
            return
        if status == "running":
            self.current_stage, self.step_text = key, ""
            self.overall(0.0)
        elif status == "error":
            self.error = (key, detail)
        elif status in ("ok", "cached"):
            self.overall(1.0)
            if key == "ingest" and detail:
                serial = detail.split("|", 1)[0].strip()
                self.settings["serial"] = serial
                save_settings(self.env, serial=serial)
        if status:
            names = {"running": "começou", "ok": "concluída", "cached": "sem mudanças", "error": "erro"}
            self.log.appendPlainText(f"[{key}] {names.get(status, status)}{': ' + detail if detail else ''}")
        self.refresh()

    def pipeline_done(self, ok: bool) -> None:
        self.clock.stop()
        self.ctx = None
        if self.mode == "pipeline" and not ok:
            self.mode = "error"
            if self.error is None:
                self.error = (self.current_stage or "ingest", "Interrompido.")
        else:
            self.mode = "idle"
            if ok:
                self.progress.setValue(1000)
        self.refresh()

    # ---------------------------------------------------------- ambiente
    def start_doctor(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        self.check_btn.setEnabled(False)
        self.worker = Worker(lambda: self.signals.doctor.emit(run_doctor(self.env)))
        self.worker.start()

    def show_doctor(self, checks: list) -> None:
        while self.checks.count():
            w = self.checks.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for c in checks:
            row = QFrame()
            row.setObjectName("row")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(14, 9, 16, 9)
            lay.setSpacing(14)
            tone = "ok" if c.ok else ("err" if c.required else "warn")
            dot = text("●", tone)
            dot.setFixedWidth(14)
            info = QVBoxLayout()
            info.setSpacing(1)
            info.addWidget(text(c.name, "label"))
            detail = text(c.detail, "hint")
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info.addWidget(detail)
            lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            lay.addLayout(info, 1)
            if not c.ok:
                lay.addWidget(text("falta" if c.required else "opcional", tone))
            self.checks.addWidget(row)
        self.system_count = len(checks)
        missing = [c for c in checks if c.required and not c.ok]
        self.system_summary.setText(f"{len(missing)} item(ns) obrigatório(s) faltando" if missing
                                    else "Tudo pronto para preparar o jogo.")
        self.missing_tools = [c.name for c in missing if c.name != "ISO do jogo"]
        self.check_btn.setEnabled(True)
        self.refresh()

    def log_line(self, line: str) -> None:
        self.log.appendPlainText(line)


# ------------------------------------------------------------------ tema, ícone e Windows
def set_theme(key: str) -> None:
    T.clear()
    T.update(THEMES.get(key, THEMES["auto"]))
    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(qss(T))


def app_icon() -> QIcon:
    """Monograma GH na cor de destaque, desenhado no código (sem arte do jogo)."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(QPointF(0, 0), QPointF(size, size))
        g.setColorAt(0, QColor(T["accent2"]))
        g.setColorAt(1, QColor(T["accent"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        p.setPen(QColor(T["ink"]))
        f = QFont(DISPLAY)
        f.setPixelSize(max(8, int(size * 0.56)))
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(QRectF(0, 0, size, size * 1.04), Qt.AlignmentFlag.AlignCenter, "GH")
        p.end()
        icon.addPixmap(pm)
    return icon


def dark_title_bar(window: QWidget) -> None:
    """Barra de título escura no Windows 10/11, para não destoar do launcher."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (atual e builds antigas do Win10)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), 4) == 0:
                break
    except (AttributeError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if sys.platform == "win32":
        try:  # agrupa na barra de tarefas com o ícone do launcher, não o do Python
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("godhand.recomp.launcher")
        except (AttributeError, OSError):
            pass
    app = QApplication.instance() or QApplication(argv)
    app.setStyle("Fusion")
    win = MainWindow()
    app.setWindowIcon(app_icon())
    dark_title_bar(win)
    win.show()
    if "--smoke-test" in argv:  # usado no CI: abre a janela, espera a checagem de ambiente e fecha
        QTimer.singleShot(3000, win.close)
        QTimer.singleShot(3500, app.quit)
    return app.exec()
