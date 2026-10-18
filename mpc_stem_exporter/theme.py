"""Bauhaus visual system for the native recorder; no hardware side effects."""
from pathlib import Path
from PySide6.QtGui import QFontDatabase

PAPER = '#ECE9DC'
SURFACE = '#F5F1E5'
INK = '#1E2731'
MUTED = '#565F60'
BLUE = '#244BA4'
RED = '#B63230'
YELLOW = '#D0A72D'
_fonts_loaded = False


def load_fonts():
    global _fonts_loaded
    if not _fonts_loaded:
        for path in (Path(__file__).parent / 'assets/fonts').glob('*.ttf'):
            QFontDatabase.addApplicationFont(str(path))
        _fonts_loaded = True


STYLE = """
QWidget { background: #ece9dc; color: #1e2731; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #ece9dc; }
QLabel { background: transparent; }
QLabel#muted { color: #565f60; font-size: 12px; }
QLabel#title { font-family: 'Space Grotesk'; font-size: 29px; font-weight: 700; }
QLabel#dialogTitle { font-family: 'Space Grotesk'; font-size: 24px; font-weight: 700; }
QLabel#section { font-family: 'Space Grotesk'; font-size: 19px; font-weight: 700; }
QLabel#trackLabel { font-weight: 700; font-size: 14px; }
QLabel#readiness { color: #244ba4; font-size: 12px; padding: 5px 0; }
QFrame#card, QFrame#sessionPanel { background: #f5f1e5; border: none; border-radius: 0; }
QFrame#trackEditor { background: #f5f1e5; border-top: 2px solid #1e2731; }
QWidget#recordingSettings { background: #f5f1e5; }
QFrame#connectionStrip { background: #244ba4; border: none; }
QFrame#connectionStrip QLabel { color: #fff8e7; background: transparent; font-size: 12px; }
QFrame#connectionStrip QPushButton { color: #244ba4; background: #fff8e7; border-color: #fff8e7; }
QFrame#connectionStrip QPushButton:hover { background: #e4e9f4; }
QPushButton { background: #f5f1e5; color: #1e2731; border: 1px solid #969d99; border-radius: 0; padding: 9px 13px; font-weight: 600; }
QPushButton:hover { background: #deded0; border-color: #1e2731; }
QPushButton:focus { border: 2px solid #244ba4; }
QPushButton:disabled { color: #7b817c; background: #deded2; border-color: #c6c9bd; }
QPushButton#primary { background: #244ba4; color: #fff8e7; border: 1px solid #244ba4; font-size: 15px; font-weight: 700; padding: 15px; }
QPushButton#primary:hover { background: #18377e; }
QPushButton#saveSetup { background: #244ba4; color: #fff8e7; border-color: #244ba4; }
QPushButton#saveSetup:hover { background: #18377e; }
QPushButton#primary:disabled { background: #d5d8d2; color: #737c79; border-color: #d5d8d2; }
QPushButton#bank { padding: 8px 17px; font-size: 16px; font-weight: 700; }
QPushButton#bank:checked { background: #d0a72d; color: #1e2731; border-color: #d0a72d; }
QPushButton#quiet { background: transparent; border-color: transparent; padding: 7px 9px; }
QPushButton#quiet:hover { background: #deded0; color: #1e2731; }
QPushButton#quiet:focus { border-color: #244ba4; }
QPushButton#cancel { background: #f5f1e5; color: #a02b29; border: 1px solid #b63230; }
QPushButton#cancel:hover { background: #f0d9d3; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #fffdf4; border: 1px solid #969d99; border-radius: 0; padding: 7px; color: #1e2731; selection-background-color: #244ba4; selection-color: #fff8e7; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 2px solid #244ba4; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: #737c79; background: #e5e4d8; }
QComboBox QAbstractItemView { background: #fffdf4; color: #1e2731; selection-background-color: #244ba4; selection-color: #fff8e7; border: 1px solid #969d99; }
QListWidget, QPlainTextEdit, QTextBrowser { background: #fffdf4; border: 1px solid #c2c6b9; border-radius: 0; padding: 7px; selection-background-color: #244ba4; selection-color: #fff8e7; }
QListWidget::item { padding: 7px 3px; color: #1e2731; }
QListWidget::item:selected { background: #dce4f3; color: #193a77; }
QProgressBar { background: #d9ddcf; border: none; border-radius: 0; max-height: 6px; min-height: 6px; }
QProgressBar::chunk { background: #244ba4; border-radius: 0; }
QProgressBar#inputMeter { min-height: 15px; max-height: 15px; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #747f7d; border-radius: 0; background: #fffdf4; }
QCheckBox::indicator:checked { background: #244ba4; border: 2px solid #193a77; }
QCheckBox::indicator:focus { border: 2px solid #b63230; }
QToolTip { color: #fff8e7; background: #1e2731; border: 1px solid #1e2731; padding: 6px; }
QScrollArea { background: #f5f1e5; border: none; }
QScrollBar:vertical { width: 10px; background: #e5e4d8; }
QScrollBar::handle:vertical { background: #87938a; border-radius: 0; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QGroupBox { border: 1px solid #b7beb0; margin-top: 20px; padding: 14px 12px 12px; font-family: 'Space Grotesk'; font-size: 16px; font-weight: 700; background: #f5f1e5; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 5px; color: #1e2731; }
QTabWidget::pane { border: 1px solid #b7beb0; background: #f5f1e5; }
QTabBar::tab { background: #e1dfd2; color: #1e2731; padding: 10px 18px; margin-right: 4px; border: none; }
QTabBar::tab:selected { background: #244ba4; color: #fff8e7; }
QTabBar::tab:focus { border-bottom: 3px solid #d0a72d; }
QDialogButtonBox QPushButton { min-width: 88px; }
"""
STYLE += '\nQCheckBox::indicator:checked { image: url("' + (Path(__file__).parent / 'assets/icons/check.svg').as_posix() + '"); }'
