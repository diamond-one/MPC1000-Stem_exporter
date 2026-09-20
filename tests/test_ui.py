import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mpc_stem_exporter.app import MainWindow, STYLE


def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    w = MainWindow(tmp_path / "state.json")
    w.show()
    app.processEvents()
    return app, w


def test_native_grid_bank_persistence_rename_and_keyboard(tmp_path):
    app, w = window(tmp_path)
    w.select_tracks(False, False)
    w.switch_bank("C")
    pad = next(p for p in w.pads if p.track.number == 36)
    pad.setFocus()
    QTest.keyClick(pad, Qt.Key.Key_Space)
    assert w.project.tracks[35].selected
    QTest.keyClick(pad, Qt.Key.Key_F2)
    w.name_edit.setText("Bass")
    QTest.keyClick(w.name_edit, Qt.Key.Key_Return)
    w.switch_bank("A")
    w.switch_bank("C")
    assert pad.track.name == "Bass"
    assert pad.track.selected
    assert w.export_button.text() == "EXPORT 1 STEM"
    w.name_edit.setText("Cancelled name")
    QTest.keyClick(w.name_edit, Qt.Key.Key_Escape)
    QTest.keyClick(w.name_edit, Qt.Key.Key_Return)
    assert w.project.tracks[35].name == "Bass"
    w.close()


def test_mouse_double_click_names_without_toggling(tmp_path):
    app, w = window(tmp_path)
    w.select_tracks(False, False)
    pad = w.pads[-4]
    QTest.mouseClick(pad, Qt.MouseButton.LeftButton)
    QTest.qWait(QApplication.doubleClickInterval() + 30)
    assert pad.track.selected
    QTest.mouseDClick(pad, Qt.MouseButton.LeftButton)
    QTest.qWait(QApplication.doubleClickInterval() + 30)
    assert pad.track.selected
    assert w.name_edit.hasFocus()
    w.close()


def test_demo_export_ui_completes_and_can_browse_banks(tmp_path):
    app, w = window(tmp_path)
    w.select_tracks(False, False)
    w.toggle_track(1)
    w.seq.setValue(0.1)
    w.tail.setValue(0)
    w.folder_edit.setText(str(tmp_path / "stems"))
    w.start_export()
    assert w.busy
    assert not w.name_edit.isEnabled()
    w.switch_bank("D")
    deadline = time.monotonic() + 8
    while w.busy and time.monotonic() < deadline:
        QTest.qWait(20)
    assert not w.busy
    assert w.project.tracks[0].status == "complete"
    assert "1 stems saved" in w.status_label.text()
    assert w.bank == "D"
    assert w.progress.value() == 1000
    w.close()
