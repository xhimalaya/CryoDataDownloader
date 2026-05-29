import time
from typing import List
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QFrame
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QFont, QPainterPath

class CircularProgressRing(QWidget):
    """
    Custom QPainter-drawn vector circular progress ring.
    Features a gorgeous royal gold track and an inner purple glowing circle.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.progress: float = 0.0 # 0.0 to 100.0
        self.setMinimumSize(160, 160)
        self.completed_text = "0"
        self.pending_text = "0"
        self.failed_text = "0"

    def set_progress(self, val: float, completed: int = 0, pending: int = 0, failed: int = 0):
        self.progress = max(0.0, min(100.0, val))
        self.completed_text = str(completed)
        self.pending_text = str(pending)
        self.failed_text = str(failed)
        self.update() # Triggers repaint

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()
        size = min(width, height) - 20
        x = (width - size) / 2
        y = (height - size) / 2

        rect = QRectF(x, y, size, size)

        # 1. Background circle track (Dim Purple)
        pen_bg = QPen(QColor("#2A1E3D"), 12)
        painter.setPen(pen_bg)
        painter.drawEllipse(rect)

        # 2. Foreground progress arc (Metallic Gold)
        pen_fg = QPen(QColor("#D4AF37"), 12)
        pen_fg.setCapStyle(Qt.RoundCap)
        painter.setPen(pen_fg)
        
        # Calculate span angle based on progress (360 degrees = 5760 sixteenths of a degree in Qt)
        span_angle = -int((self.progress / 100.0) * 360 * 16)
        start_angle = 90 * 16 # Start from 12 o'clock
        painter.drawArc(rect, start_angle, span_angle)

        # 3. Inner text details
        # Progress percentage
        painter.setPen(QColor("#ECECEC"))
        font_pct = QFont("Inter", 22, QFont.Bold)
        painter.setFont(font_pct)
        painter.drawText(self.rect(), Qt.AlignCenter, f"{int(self.progress)}%")

        # Small status numbers below
        font_sm = QFont("Inter", 8, QFont.Normal)
        painter.setFont(font_sm)
        painter.setPen(QColor("#A8A8A8"))
        
        # Completed / Pending / Failed layout
        painter.drawText(QRectF(0, y + size/2 + 20, width, 30), 
                         Qt.AlignCenter, 
                         f"OK: {self.completed_text}  |  Err: {self.failed_text}")


class SpeedHistoryGraph(QWidget):
    """
    Self-contained custom live speed line graph drawn via QPainter.
    Excludes external dependencies and yields premium vector animations.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.history: List[float] = [0.0] * 30
        self.setMinimumSize(220, 100)

    def add_speed(self, speed: float):
        self.history.append(speed)
        if len(self.history) > 40:
            self.history.pop(0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Fill background with Deep Black
        painter.fillRect(self.rect(), QColor("#121212"))

        # Draw grid lines
        grid_pen = QPen(QColor("#222222"), 1, Qt.DashLine)
        painter.setPen(grid_pen)
        for i in range(1, 4):
            y = int(h * i / 4)
            painter.drawLine(0, y, w, y)

        if not self.history:
            return

        max_speed = max(self.history)
        if max_speed < 10.0:
            max_speed = 10.0 # Upper bound limit floor
            
        points = []
        n_points = len(self.history)
        
        # Compute coordinates
        for i, val in enumerate(self.history):
            x = int(w * i / (n_points - 1))
            y = int(h - (val / max_speed) * (h - 20) - 10)
            points.append((x, y))

        # 1. Gradient Fill under the curve
        fill_path = QPainterPath()
        fill_path.moveTo(0, h)
        for x, y in points:
            fill_path.lineTo(x, y)
        fill_path.lineTo(w, h)
        fill_path.closeSubpath()
        
        grad = QColor("#5E3A87") # Purple fill
        grad.setAlpha(60)
        painter.fillPath(fill_path, QBrush(grad))

        # 2. Draw line path (Gold glow)
        line_path = QPainterPath()
        line_path.moveTo(points[0][0], points[0][1])
        for x, y in points[1:]:
            line_path.lineTo(x, y)

        pen_line = QPen(QColor("#D4AF37"), 2.5) # Metallic Gold Line
        painter.setPen(pen_line)
        painter.drawPath(line_path)

        # 3. Draw dots on current speed points
        pen_dot = QPen(QColor("#E6C15A"), 5)
        painter.setPen(pen_dot)
        if points:
            # Draw last speed dot
            last_x, last_y = points[-1]
            painter.drawPoint(last_x, last_y)

        # 4. Text overlays
        font = QFont("Courier New", 8, QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#A8A8A8"))
        painter.drawText(10, 15, f"MAX: {round(max_speed, 1)} MB/s")
        painter.drawText(w - 110, 15, f"NOW: {round(self.history[-1], 1)} MB/s")


class ResourceGauge(QFrame):
    """
    Subtle glassmorphism hardware telemetry card for monitoring resources.
    Changes bar colors dynamically (Emerald -> Amber -> Crimson).
    """
    def __init__(self, label: str, unit: str = "%", parent=None):
        super().__init__(parent)
        self.setObjectName("CardFrame")
        self.label_str = label
        self.unit_str = unit

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 8, 12, 8)
        self.layout.setSpacing(4)

        # Header label
        self.header_layout = QHBoxLayout()
        self.lbl_title = QLabel(label, self)
        self.lbl_title.setStyleSheet("font-weight: bold; color: #ECECEC;")
        self.lbl_value = QLabel(f"0{unit}", self)
        self.lbl_value.setStyleSheet("color: #D4AF37; font-weight: bold;")
        self.header_layout.addWidget(self.lbl_title)
        self.header_layout.addStretch()
        self.header_layout.addWidget(self.lbl_value)
        self.layout.addLayout(self.header_layout)

        # Progress bar
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.layout.addWidget(self.progress_bar)

    def set_value(self, val: float):
        int_val = int(val)
        self.progress_bar.setValue(int_val)
        self.lbl_value.setText(f"{round(val, 1)} {self.unit_str}")

        # Dynamic color coding based on severity
        # Success #00C896, Warning #F4B400, Error #FF5C5C
        if int_val >= 90:
            # Crimson alert
            self.progress_bar.setStyleSheet("""
                QProgressBar::chunk {
                    background-color: #FF5C5C;
                    border-radius: 4px;
                }
            """)
            self.lbl_value.setStyleSheet("color: #FF5C5C; font-weight: bold;")
        elif int_val >= 70:
            # Amber warn
            self.progress_bar.setStyleSheet("""
                QProgressBar::chunk {
                    background-color: #F4B400;
                    border-radius: 4px;
                }
            """)
            self.lbl_value.setStyleSheet("color: #F4B400; font-weight: bold;")
        else:
            # Emerald okay
            self.progress_bar.setStyleSheet("""
                QProgressBar::chunk {
                    background-color: #00C896;
                    border-radius: 4px;
                }
            """)
            self.lbl_value.setStyleSheet("color: #D4AF37; font-weight: bold;")
