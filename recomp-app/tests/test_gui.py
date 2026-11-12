"""Testes do launcher em modo off-screen (sem janela visível)."""
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from recomp.gui import THEMES, MainWindow, T  # noqa: E402


class GuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _wait_doctor(self, w):
        deadline = time.monotonic() + 60
        while w.system_count == 0 and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.02)

    def test_opens_with_all_pages_and_closes(self):
        w = MainWindow()
        w.show()
        self._wait_doctor(w)
        self.assertGreater(w.system_count, 5, "a checagem de ambiente não preencheu a página Sistema")
        self.assertEqual(w.page_names, ["Início", "Preparação", "Configurações", "Saves", "Patches", "Mods",
                                        "Sistema", "Atualizações", "Feedback"])  # patches e mods separados
        self.assertEqual(w.pages.count(), len(w.page_names))
        self.assertFalse(w.cancel_btn.isEnabled())
        for i in range(w.pages.count()):  # toda página abre sem erro
            w.go(i)
            self.app.processEvents()
            self.assertEqual(w.pages.currentIndex(), i)
        self.assertTrue(w.close())
        self.assertFalse(w.worker.isRunning())

    def test_theme_switch_recolors(self):
        w = MainWindow()
        w.show()
        original = w.settings.get("theme", "auto")
        try:
            w.change_theme("violet")
            self.assertEqual(T["accent"], THEMES["violet"]["accent"])
        finally:
            w.change_theme(original)
        self.assertTrue(w.close())

    def test_close_with_worker_running_waits_for_it(self):
        # A checagem de ambiente começa numa thread ao abrir; fechar na hora derrubava o processo.
        w = MainWindow()
        w.show()
        self.assertTrue(w.close())
        self.assertFalse(w.worker.isRunning())


if __name__ == "__main__":
    unittest.main()
