"""Interface gráfica (PySide6) sobre o mesmo núcleo usado pela CLI."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .config import load_env
from .doctor import run_doctor
from .mods import load_mods, load_patches, load_textures, save_item
from .pipeline import STAGE_KEYS, STAGES, Context, Reporter, StageError, run_game, run_pipeline

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


class ItemTable(QWidget):
    """Lista de patches ou de mods com caixa de ativar/desativar. O estado é gravado na pasta do item."""

    def __init__(self, hint: str, loader, label: str, log) -> None:
        super().__init__()
        self.loader, self.label, self.log = loader, label, log
        self.items: list = []
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Ativo", "Ordem", "Tipo", "Nome", "Situação"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self.toggled)
        reload_btn = QPushButton("Recarregar")
        reload_btn.clicked.connect(self.reload)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(hint))
        layout.addWidget(self.table)
        layout.addWidget(reload_btn, 0, Qt.AlignmentFlag.AlignLeft)

    def reload(self) -> None:
        self.items = self.loader()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked if item.enabled else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, chk)
            note = "aplicado no build" if item.applied else "listado; este tipo ainda não é aplicado"
            for col, text in enumerate([str(item.order), item.type, item.name, note], start=1):
                cell = QTableWidgetItem(text)
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row, col, cell)
        self.table.blockSignals(False)

    def toggled(self, cell: QTableWidgetItem) -> None:
        if cell.column() != 0:
            return
        item = self.items[cell.row()]
        item.enabled = cell.checkState() == Qt.CheckState.Checked
        save_item(item)
        self.log(f"{self.label} {item.id}: {'ativado' if item.enabled else 'desativado'}.")


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
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        self.ctx: Context | None = None
        top.addWidget(browse)
        self.play_btn = QPushButton("Executar jogo")
        self.play_btn.setToolTip("Abre o executável compilado usando o disco extraído. A saída aparece no log.")
        self.play_btn.clicked.connect(self.play)
        top.addWidget(self.run_all)
        top.addWidget(self.play_btn)
        top.addWidget(self.cancel_btn)

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

        # --- abas patches e mods: coisas diferentes, cada uma com sua pasta
        self.patches_tab = ItemTable(
            f"Patches ficam em {self.env.patches}: solte ali os arquivos .xdelta (aplicados à ISO) e .pnach.",
            lambda: load_patches(self.env.patches), "Patch", self.log_line)
        self.mods_tab = ItemTable(
            f"Mods ficam em {self.env.mods} (uma pasta por mod) e pacotes de texturas em {self.env.textures}.",
            lambda: load_mods(self.env.mods) + load_textures(self.env.textures), "Mod", self.log_line)

        tabs = self.tabs = QTabWidget()
        tabs.addTab(pipe, "Pipeline")
        tabs.addTab(self.patches_tab, "Patches")
        tabs.addTab(self.mods_tab, "Mods")
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

        self.patches_tab.reload()
        self.mods_tab.reload()
        self.start_doctor()

    # ------------------------------------------------------------ pipeline
    def pick_iso(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Escolher ISO", str(Path(self.iso_edit.text()).parent),
                                              "Imagem de disco (*.iso);;Todos (*)")
        if path:
            self.iso_edit.setText(path)

    def set_busy(self, busy: bool) -> None:
        for w in (self.run_all, self.play_btn, self.doctor_btn, *self.stage_buttons):
            w.setEnabled(not busy)

    def start(self, until: str, force: bool) -> None:
        if self.worker and self.worker.isRunning():
            return
        for key in STAGE_KEYS:
            self.set_stage(key, "", "")
        self.progress.setValue(0)
        ctx = Context(env=self.env, iso=Path(self.iso_edit.text()), reporter=QtReporter(self.signals),
                      force={until} if force else set())
        self.ctx = ctx
        self.set_busy(True)
        self.cancel_btn.setEnabled(True)
        self.worker = Worker(lambda: self.signals.finished.emit(run_pipeline(ctx, until)))
        self.worker.start()

    def play(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        ctx = Context(env=self.env, iso=Path(self.iso_edit.text()), reporter=QtReporter(self.signals))
        self.ctx = ctx

        def job() -> None:
            try:
                code = run_game(ctx)
                self.signals.log.emit(f"Jogo terminou com código {code}.")
                self.signals.finished.emit(code == 0)
            except StageError as e:
                self.signals.log.emit(f"Não foi possível executar: {e}")
                self.signals.finished.emit(False)

        self.set_busy(True)
        self.cancel_btn.setEnabled(True)
        self.worker = Worker(job)
        self.worker.start()

    def cancel(self) -> None:
        if self.ctx is not None and self.worker and self.worker.isRunning():
            self.log.appendPlainText("Cancelando a etapa em execução…")
            self.cancel_btn.setEnabled(False)
            self.ctx.cancel()

    def closeEvent(self, event) -> None:
        """Nunca destrói a janela com uma thread viva: isso derruba o processo."""
        if self.worker and self.worker.isRunning():
            if self.ctx is not None:
                answer = QMessageBox.question(
                    self, "Pipeline em execução",
                    "Uma etapa ainda está rodando. Fechar agora interrompe essa etapa. Fechar mesmo assim?")
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self.ctx.cancel()
            self.worker.wait()
        event.accept()

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
        self.ctx = None
        self.cancel_btn.setEnabled(False)
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

    def log_line(self, text: str) -> None:
        self.log.appendPlainText(text)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()
