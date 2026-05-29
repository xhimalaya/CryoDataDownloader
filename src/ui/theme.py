# Styling definitions for the CryoDataDownloader dashboard.
# Incorporates the user-requested: Matte Black, Matte Purple, and Metallic Gold theme.

STYLE_SHEET = """
QMainWindow {
    background-color: #121212;
    color: #ECECEC;
    font-family: 'Inter', 'Segoe UI', Arial, sans-serif;
}

/* Header Bar */
QFrame#HeaderFrame {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1A1A1A, stop:1 #121212);
    border-bottom: 2px solid #5E3A87;
    border-radius: 0px;
    padding: 10px;
}

QLabel#HeaderTitle {
    color: #D4AF37; /* Metallic Gold */
    font-size: 20px;
    font-weight: bold;
    letter-spacing: 1.5px;
}

QLabel#HeaderSubtitle {
    color: #A8A8A8;
    font-size: 11px;
}

/* Sidebar and Main Cards */
QFrame#CardFrame {
    background-color: #1A1A1A;
    border: 1px solid #5E3A87; /* Matte Purple Border */
    border-radius: 12px;
}

QGroupBox {
    background-color: #1A1A1A;
    border: 1px solid #5E3A87;
    border-radius: 12px;
    margin-top: 15px;
    font-size: 13px;
    font-weight: bold;
    color: #D4AF37;
    padding: 15px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 15px;
    padding: 0 5px;
}

/* Labels and text */
QLabel {
    color: #ECECEC;
    font-size: 12px;
}

QLabel#LabelMuted {
    color: #A8A8A8;
    font-size: 11px;
}

QLabel#MetricValue {
    color: #D4AF37;
    font-size: 24px;
    font-weight: bold;
}

/* Inputs and interactive elements */
QLineEdit, QDateEdit, QComboBox {
    background-color: #121212;
    border: 1px solid #5E3A87;
    border-radius: 6px;
    color: #ECECEC;
    padding: 6px 10px;
    font-size: 12px;
}

QLineEdit:focus, QDateEdit:focus, QComboBox:focus {
    border: 1px solid #D4AF37; /* Gold focus glow */
}

/* Custom modern buttons with 18px curved radius as per design spec */
QPushButton {
    background-color: #1A1A1A;
    border: 1.5px solid #5E3A87;
    border-radius: 18px; /* Curved corners */
    color: #ECECEC;
    font-size: 12px;
    font-weight: bold;
    padding: 8px 20px;
    min-height: 20px;
}

QPushButton:hover {
    background-color: #5E3A87;
    border-color: #D4AF37;
    color: #FFFFFF;
}

QPushButton:pressed {
    background-color: #4B2E6E;
    border-color: #E6C15A;
}

/* Specific Premium Buttons */
QPushButton#BtnStart {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5E3A87, stop:1 #4B2E6E);
    border: 2px solid #D4AF37;
    color: #FFFFFF;
}

QPushButton#BtnStart:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #7A4EA5, stop:1 #5E3A87);
    border-color: #E6C15A;
}

QPushButton#BtnStop {
    background-color: #4A121A;
    border: 1.5px solid #FF5C5C;
    color: #FFFFFF;
}

QPushButton#BtnStop:hover {
    background-color: #FF5C5C;
    color: #000000;
}

/* Progress bars */
QProgressBar {
    background-color: #121212;
    border: 1px solid #5E3A87;
    border-radius: 6px;
    text-align: center;
    color: #FFFFFF;
    font-weight: bold;
    font-size: 10px;
}

QProgressBar::chunk {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5E3A87, stop:1 #D4AF37);
    border-radius: 5px;
}

/* ScrollBars */
QScrollBar:vertical {
    border: none;
    background: #121212;
    width: 8px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background: #5E3A87;
    border-radius: 4px;
    min-height: 20px;
}

QScrollBar::handle:vertical:hover {
    background: #D4AF37;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    border: none;
    background: #121212;
    height: 8px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background: #5E3A87;
    border-radius: 4px;
    min-width: 20px;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* Tables */
QTableWidget {
    background-color: #1A1A1A;
    border: 1px solid #5E3A87;
    gridline-color: #2D1B44;
    border-radius: 8px;
    color: #ECECEC;
}

QTableWidget::item {
    padding: 8px;
    border-bottom: 1px solid #2D1B44;
}

QTableWidget::item:selected {
    background-color: #5E3A87;
    color: #FFFFFF;
}

QHeaderView::section {
    background-color: #121212;
    color: #D4AF37;
    padding: 8px;
    font-weight: bold;
    border: none;
    border-bottom: 2px solid #5E3A87;
}

/* Tabs */
QTabWidget::panel {
    background-color: #1A1A1A;
    border: 1px solid #5E3A87;
    border-radius: 8px;
}

QTabBar::tab {
    background-color: #121212;
    border: 1px solid #5E3A87;
    color: #A8A8A8;
    padding: 6px 15px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}

QTabBar::tab:selected {
    background-color: #1A1A1A;
    color: #D4AF37;
    border-bottom-color: #1A1A1A;
}

QTabBar::tab:hover {
    color: #ECECEC;
}

/* Scrolling Log Terminal styling */
QTextEdit#LogTerminal {
    background-color: #0A0A0A;
    border: 1.5px solid #5E3A87;
    border-radius: 8px;
    font-family: 'Courier New', Courier, monospace;
    font-size: 11px;
    color: #ECECEC;
    padding: 10px;
}

/* Sliders */
QSlider::groove:horizontal {
    border: 1px solid #5E3A87;
    height: 6px;
    background: #121212;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    background: #D4AF37;
    border: 1px solid #D4AF37;
    width: 14px;
    margin: -4px 0;
    border-radius: 7px;
}

QSlider::handle:horizontal:hover {
    background: #E6C15A;
}
"""
