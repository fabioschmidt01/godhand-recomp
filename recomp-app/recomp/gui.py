"""Interface gráfica (PySide6) sobre o mesmo núcleo usado pela CLI."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QPlainTextEdit,
    QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .config import load_env
from .doctor import run_doctor
from .mods import load_mods, save_mod
from .pipeline import STAGE_KEYS, STAGES, Context, Reporter, run_pipeline

STATUS = {
    "": ("pendente", "#8a8f98"),
    "running": ("executando…", "#e0a030"),
    "ok": ("concluído", "#4caf7a"),
    "cached": ("sem mudanças (cache)", "#5b9bd5"),
    "error": ("erro", "#e05555"),
}


class Signals(QObject):
    log = Signal(str)
    stage = Signal(str, str, str)
    progress = Signal(str, float)
    finished = Signal(bool)
    doctor = Signal(list)


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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.env = load_env()
        self.signals = Signals()
        self.worker: Worker | None = None
        self.setWindowTitle("PS2 Recomp — God Hand")
        self.resize(1100, 760)

        # --- topo: ISO
        self.iso_edit = QLineEdit(str(self.env.iso))
        browse = QPushButton("Procurar…")
        browse.clicked.connect(self.pick_iso)
        self.run_all = QPushButton("Converter e compilar tudo")
        self.run_all.setDefault(True)
        self.run_all.clicked.connect(lambda: self.start("build", force=False))
        top = QHBoxLayout()
        top.addWidget(QLabel("ISO do jogo:"))
        top.addWidget(self.iso_edit, 1)
        top.addWidget(browse)
        top.addWidget(self.run_all)

        # --- aba pipeline
        self.stage_table = QTableWidget(len(STAGES), 4)
        self.stage_table.setHorizontalHeaderLabels(["Etapa", "Estado", "Resultado", ""])
        self.stage_table.verticalHeader().setVisible(False)
        self.stage_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.stage_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        h = self.stage_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.stage_buttons: list[QPushButton] = []
        for row, (key, title, _fn) in enumerate(STAGES):
            self.stage_table.setItem(row, 0, QTableWidgetItem(f"{row + 1}. {title}"))
            self.stage_table.setItem(row, 1, QTableWidgetItem())
            self.stage_table.setItem(row, 2, QTableWidgetItem())
            btn = QPushButton("Refazer até aqui")
            btn.setToolTip("Roda o pipeline até esta etapa, ignorando o cache dela")
            btn.clicked.connect(lambda _=False, k=key: self.start(k, force=True))
            self.stage_table.setCellWidget(row, 3, btn)
            self.stage_buttons.append(btn)
            self.set_stage(key, "", "")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        pipe = QWidget()
        pl = QVBoxLayout(pipe)
        pl.addWidget(self.stage_table)
        pl.addWidget(self.progress)

        # --- aba ambiente
        self.doctor_table = QTableWidget(0, 3)
        self.doctor_table.setHorizontalHeaderLabels(["Item", "Estado", "Detalhe"])
        self.doctor_table.verticalHeader().setVisible(False)
        self.doctor_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        dh = self.doctor_table.horizontalHeader()
        dh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        dh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        dh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.doctor_btn = QPushButton("Verificar ambiente")
        self.doctor_btn.clicked.connect(self.start_doctor)
        envw = QWidget()
        el = QVBoxLayout(envw)
        el.addWidget(self.doctor_btn, 0, Qt.AlignmentFlag.AlignLeft)
        el.addWidget(self.doctor_table)

        # --- aba mods
        self.mods_table = QTableWidget(0, 5)
        self.mods_table.setHorizontalHeaderLabels(["Ativo", "Ordem", "Tipo", "Nome", "Situação"])
        self.mods_table.verticalHeader().setVisible(False)
        mh = self.mods_table.horizontalHeader()
        mh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        mh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.itemChanged.connect(self.mod_toggled)
        reload_mods = QPushButton("Recarregar")
        reload_mods.clicked.connect(self.load_mods)
        self.mods_hint = QLabel(f"Cada mod é uma pasta em {self.env.mods} com um mod.toml.")
        modw = QWidget()
        ml = QVBoxLayout(modw)
        ml.addWidget(self.mods_hint)
        ml.addWidget(self.mods_table)
        ml.addWidget(reload_mods, 0, Qt.AlignmentFlag.AlignLeft)

        tabs = QTabWidget()
        tabs.addTab(pipe, "Pipeline")
        tabs.addTab(modw, "Mods")
        tabs.addTab(envw, "Ambiente")

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(20000)
        self.log.setFont(QFont("Consolas", 9))

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(tabs)
        split.addWidget(self.log)
        split.setSizes([380, 320])

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        self.setCentralWidget(central)

        self.signals.log.connect(self.log.appendPlainText)
        self.signals.stage.connect(self.set_stage)
        self.signals.progress.connect(lambda _k, f: self.progress.setValue(int(f * 1000)))
        self.signals.finished.connect(self.pipeline_done)
        self.signals.doctor.connect(self.show_doctor)

        self.load_mods()
        self.start_doctor()

    # ------------------------------------------------------------ pipeline
    def pick_iso(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Escolher ISO", str(Path(self.iso_edit.text()).parent),
                                              "Imagem de disco (*.iso);;Todos (*)")
        if path:
            self.iso_edit.setText(path)

    def set_busy(self, busy: bool) -> None:
        for w in (self.run_all, self.doctor_btn, *self.stage_buttons):
            w.setEnabled(not busy)

    def start(self, until: str, force: bool) -> None:
        if self.worker and self.worker.isRunning():
            return
        for key in STAGE_KEYS:
            self.set_stage(key, "", "")
        self.progress.setValue(0)
        ctx = Context(env=self.env, iso=Path(self.iso_edit.text()), reporter=QtReporter(self.signals),
                      force={until} if force else set())
        self.set_busy(True)
        self.worker = Worker(lambda: self.signals.finished.emit(run_pipeline(ctx, until)))
        self.worker.start()

    def set_stage(self, key: str, status: str, detail: str) -> None:
        row = STAGE_KEYS.index(key)
        text, color = STATUS.get(status, (status, "#8a8f98"))
        item = self.stage_table.item(row, 1)
        item.setText(text)
        item.setForeground(QColor(color))
        self.stage_table.item(row, 2).setText(detail)
        if status == "running":
            self.progress.setValue(0)
        self.log_stage(key, status, detail)

    def log_stage(self, key: str, status: str, detail: str) -> None:
        if status:
            self.log.appendPlainText(f"[{key}] {STATUS.get(status, (status,))[0]}{': ' + detail if detail else ''}")

    def pipeline_done(self, ok: bool) -> None:
        self.set_busy(False)
        self.progress.setValue(1000 if ok else 0)
        self.log.appendPlainText("Pipeline concluído." if ok else "Pipeline interrompido por erro.")

    # ------------------------------------------------------------ ambiente
    def start_doctor(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        self.set_busy(True)
        self.worker = Worker(lambda: self.signals.doctor.emit(run_doctor(self.env)))
        self.worker.start()

    def show_doctor(self, checks: list) -> None:
        self.set_busy(False)
        self.doctor_table.setRowCount(len(checks))
        for row, c in enumerate(checks):
            state, color = ("OK", "#4caf7a") if c.ok else (("FALTA", "#e05555") if c.required else ("aviso", "#e0a030"))
            self.doctor_table.setItem(row, 0, QTableWidgetItem(c.name))
            s = QTableWidgetItem(state)
            s.setForeground(QColor(color))
            self.doctor_table.setItem(row, 1, s)
            self.doctor_table.setItem(row, 2, QTableWidgetItem(c.detail))

    # ------------------------------------------------------------ mods
    def load_mods(self) -> None:
        self.mods = load_mods(self.env.mods)
        self.mods_table.blockSignals(True)
        self.mods_table.setRowCount(len(self.mods))
        for row, m in enumerate(self.mods):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if m.enabled else Qt.CheckState.Unchecked)
            self.mods_table.setItem(row, 0, chk)
            note = "aplicado no build" if m.applied else "listado; este tipo ainda não é aplicado"
            for col, text in enumerate([str(m.order), m.type, m.name, note], start=1):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.mods_table.setItem(row, col, it)
        self.mods_table.blockSignals(False)

    def mod_toggled(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        mod = self.mods[item.row()]
        mod.enabled = item.checkState() == Qt.CheckState.Checked
        save_mod(mod)
        self.log.appendPlainText(f"Mod {mod.id}: {'ativado' if mod.enabled else 'desativado'}.")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()
