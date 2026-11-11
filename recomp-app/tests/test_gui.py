"""Testes da GUI em modo off-screen (sem janela visível)."""
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from recomp.gui import MainWindow  # noqa: E402
from recomp.pipeline import STAGES  # noqa: E402


class GuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_opens_fills_tables_and_closes(self):
        w = MainWindow()
        w.show()
        deadline = time.monotonic() + 60
        while w.doctor_table.rowCount() == 0 and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
        self.assertGreater(w.doctor_table.rowCount(), 5, "a checagem de ambiente não preencheu a tabela")
        self.assertEqual(w.stage_table.rowCount(), len(STAGES))
        self.assertFalse(w.cancel_btn.isEnabled())
        self.assertTrue(w.close())
        self.assertFalse(w.worker.isRunning())

    def test_close_with_worker_running_waits_for_it(self):
        # A checagem de ambiente começa numa thread ao abrir; fechar na hora derrubava o processo.
        w = MainWindow()
        w.show()
        self.assertTrue(w.close())
        self.assertFalse(w.worker.isRunning())


if __name__ == "__main__":
    unittest.main()
