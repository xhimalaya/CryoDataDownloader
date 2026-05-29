import os
import sys
import time
import asyncio
import datetime
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QTableWidget, QTableWidgetItem, QTextEdit, QComboBox, QLineEdit,
    QTabWidget, QHeaderView, QSlider, QSizePolicy, QProgressBar, QCheckBox,
    QScrollArea
)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer
from PySide6.QtGui import QColor, QFont, QIcon

from src.config_manager import ConfigManager
from src.db_manager import DBManager
from src.task_orchestrator import TaskOrchestrator
from src.resource_monitor import ResourceMonitor
from src.ui.theme import STYLE_SHEET
from src.ui.widgets import CircularProgressRing, SpeedHistoryGraph, ResourceGauge

class PipelineThread(QThread):
    """
    Separate worker thread to run the asyncio pipeline.
    Prevents UI freezing and keeps the dashboard highly responsive.
    """
    finished_signal = Signal(dict)

    def __init__(self, orchestrator: TaskOrchestrator):
        super().__init__()
        self.orchestrator = orchestrator

    def run(self):
        # Create a dedicated event loop for this thread's execution
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(self.orchestrator.run_pipeline())
        except Exception as e:
            self.orchestrator.log("ERROR", f"Orchestrator Thread Crash: {e}")
        finally:
            loop.close()
            
        self.finished_signal.emit({"status": "complete"})

class MainWindow(QMainWindow):
    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config = config_manager
        self.db = DBManager()
        self.orchestrator = TaskOrchestrator(self.config)
        self.monitor = ResourceMonitor()

        # Connect orchestrator signals to UI slots
        if self.orchestrator.signals:
            self.orchestrator.signals.task_progress.connect(self.on_task_progress)
            self.orchestrator.signals.overall_progress.connect(self.on_overall_progress)
            self.orchestrator.signals.speed_updated.connect(self.on_speed_updated)
            self.orchestrator.signals.eta_updated.connect(self.on_eta_updated)
            self.orchestrator.signals.log_received.connect(self.on_log_received)
            self.orchestrator.signals.stats_updated.connect(self.on_stats_updated)
            self.orchestrator.signals.pipeline_finished.connect(self.on_pipeline_finished)

        self.setWindowTitle("Cryosphere Intelligence Platform - Data Downloader")
        self.resize(1600, 950)
        self.setMinimumSize(1200, 800)
        self.setStyleSheet(STYLE_SHEET)

        self.pipeline_thread = None
        self.log_filter = "ALL"

        self.init_ui()
        
        # Telemetry Timer (updates at 1Hz)
        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.timeout.connect(self.update_telemetry)
        self.telemetry_timer.start(1000)
        
        # Initial stats pull
        self.refresh_task_table()
        self.on_stats_updated(self.db.get_task_statistics())
        
        self.log_info("ONLINE: System ready for ingestion. Configure options and click START.")

    def init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # ----------------------------------------------------
        # 1. HEADER BAR (Top)
        # ----------------------------------------------------
        header_frame = QFrame(self)
        header_frame.setObjectName("HeaderFrame")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(10, 5, 10, 5)

        title_layout = QVBoxLayout()
        title_lbl = QLabel("GLACIER DATA INGESTION PLATFORM", self)
        title_lbl.setObjectName("HeaderTitle")
        subtitle_lbl = QLabel("Autonomous Multi-Modal Acquisition Engine for Subsurface Melt Depth Modeling", self)
        subtitle_lbl.setObjectName("HeaderSubtitle")
        title_layout.addWidget(title_lbl)
        title_layout.addWidget(subtitle_lbl)
        header_layout.addLayout(title_layout)

        header_layout.addStretch()

        # Status badge indicator
        self.lbl_status_led = QLabel("●  ONLINE", self)
        self.lbl_status_led.setStyleSheet("color: #00C896; font-weight: bold; font-size: 14px; letter-spacing: 1px;")
        header_layout.addWidget(self.lbl_status_led)
        
        main_layout.addWidget(header_frame)

        # ----------------------------------------------------
        # 2. MAIN 3-COLUMN LAYOUT
        # ----------------------------------------------------
        cols_layout = QHBoxLayout()
        cols_layout.setSpacing(15)

        # COLUMN A: Left Control Panel (Width: 320px) wrapped in QScrollArea
        scroll_area = QScrollArea(self)
        scroll_area.setFixedWidth(320)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("background-color: transparent;")

        col_a_widget = QWidget()
        col_a_widget.setObjectName("LeftControlPanel")
        col_a_widget.setStyleSheet("QWidget#LeftControlPanel { background-color: transparent; }")
        col_a_layout = QVBoxLayout(col_a_widget)
        col_a_layout.setContentsMargins(0, 0, 10, 0) # Margin for scrollbar
        col_a_layout.setSpacing(15)

        # Group 1: Temporal Configuration
        grp_time = QFrame(self)
        grp_time.setObjectName("CardFrame")
        time_layout = QVBoxLayout(grp_time)
        time_layout.addWidget(QLabel("TEMPORAL RESOLUTION", grp_time))
        
        row_start = QHBoxLayout()
        row_start.addWidget(QLabel("Start Date:", grp_time))
        self.inp_start_date = QLineEdit(self.config.get("date_range.start_date", "2024-01-01"), grp_time)
        row_start.addWidget(self.inp_start_date)
        time_layout.addLayout(row_start)

        row_end = QHBoxLayout()
        row_end.addWidget(QLabel("End Date:", grp_time))
        self.inp_end_date = QLineEdit(self.config.get("date_range.end_date", "2024-03-31"), grp_time)
        row_end.addWidget(self.inp_end_date)
        time_layout.addLayout(row_end)
        col_a_layout.addWidget(grp_time)

        # Group 1.5: Glacier Selection (Dynamic checkboxes)
        grp_glaciers = QFrame(self)
        grp_glaciers.setObjectName("CardFrame")
        glaciers_layout = QVBoxLayout(grp_glaciers)
        glaciers_layout.addWidget(QLabel("GLACIER SELECTION", grp_glaciers))
        
        self.glacier_checkboxes = {}
        available_glaciers = self.discover_glaciers()
        
        for g in available_glaciers:
            chk = QCheckBox(g.replace("_", " ").title(), grp_glaciers)
            chk.setChecked(True)
            chk.setStyleSheet("color: #ECECEC; font-size: 12px;")
            glaciers_layout.addWidget(chk)
            self.glacier_checkboxes[g] = chk
            
        col_a_layout.addWidget(grp_glaciers)

        # Group 2: Download Modalities
        grp_sources = QFrame(self)
        grp_sources.setObjectName("CardFrame")
        sources_layout = QVBoxLayout(grp_sources)
        sources_layout.addWidget(QLabel("SENSORS & MODALITIES", grp_sources))
        
        self.switches = {}
        for src in ["sentinel1", "sentinel2", "era5", "modis_lst", "landsat_thermal", "dem"]:
            row_src = QHBoxLayout()
            lbl = QLabel(src.upper().replace("_", " "), grp_sources)
            lbl.setObjectName("LabelMuted")
            chk_state = "ON" if self.config.get(f"sources.{src}.enabled", True) else "OFF"
            btn_toggle = QPushButton(chk_state, grp_sources)
            btn_toggle.setFixedWidth(70)
            btn_toggle.setStyleSheet("background-color: #5E3A87;" if chk_state == "ON" else "background-color: #2D1B44;")
            btn_toggle.clicked.connect(lambda checked=False, s=src: self.toggle_source(s))
            
            row_src.addWidget(lbl)
            row_src.addStretch()
            row_src.addWidget(btn_toggle)
            sources_layout.addLayout(row_src)
            self.switches[src] = btn_toggle
            
        col_a_layout.addWidget(grp_sources)

        # Group 3: Resource Allocations
        grp_resources = QFrame(self)
        grp_resources.setObjectName("CardFrame")
        res_layout = QVBoxLayout(grp_resources)
        res_layout.addWidget(QLabel("RESOURCE THRESHOLDS", grp_resources))

        # CPU Core Limiter
        res_layout.addWidget(QLabel("Max CPU Workers:", grp_resources))
        self.inp_cpu = QLineEdit(str(self.config.get("parallelism.cpu_workers", "auto")), grp_resources)
        res_layout.addWidget(self.inp_cpu)
        
        # Parallel downloads slider
        res_layout.addWidget(QLabel("Concurrent Downloads:", grp_resources))
        self.slider_downloads = QSlider(Qt.Horizontal, grp_resources)
        self.slider_downloads.setRange(10, 200)
        self.slider_downloads.setValue(self.config.get("download.async_downloads", 100))
        self.lbl_slider_val = QLabel(f"{self.slider_downloads.value()} connections", grp_resources)
        self.slider_downloads.valueChanged.connect(self.on_slider_changed)
        res_layout.addWidget(self.slider_downloads)
        res_layout.addWidget(self.lbl_slider_val)
        
        col_a_layout.addWidget(grp_resources)

        # Group 4: Pipeline Execution Controls (Start, Pause, Resume, Stop)
        grp_controls = QFrame(self)
        grp_controls.setObjectName("CardFrame")
        controls_layout = QVBoxLayout(grp_controls)
        controls_layout.addWidget(QLabel("PIPELINE ACTIONS", grp_controls))

        self.btn_start = QPushButton("START INGESTION", grp_controls)
        self.btn_start.setObjectName("BtnStart")
        self.btn_start.clicked.connect(self.start_pipeline)
        controls_layout.addWidget(self.btn_start)

        self.btn_pause = QPushButton("PAUSE", grp_controls)
        self.btn_pause.clicked.connect(self.pause_pipeline)
        self.btn_pause.setEnabled(False)
        controls_layout.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("ABORT PIPELINE", grp_controls)
        self.btn_stop.setObjectName("BtnStop")
        self.btn_stop.clicked.connect(self.stop_pipeline)
        self.btn_stop.setEnabled(False)
        controls_layout.addWidget(self.btn_stop)

        self.btn_retry_failed = QPushButton("RETRY FAILED", grp_controls)
        self.btn_retry_failed.clicked.connect(self.retry_failed_tasks)
        controls_layout.addWidget(self.btn_retry_failed)

        col_a_layout.addWidget(grp_controls)
        col_a_layout.addStretch()
        
        scroll_area.setWidget(col_a_widget)
        cols_layout.addWidget(scroll_area)

        # COLUMN B: Center Hero Panel (Overall progress rings, graphs, linear sensor loads)
        col_b_widget = QWidget(self)
        col_b_layout = QVBoxLayout(col_b_widget)
        col_b_layout.setContentsMargins(0, 0, 0, 0)
        col_b_layout.setSpacing(15)

        # Top Center Hero: Circular Progress Ring & Speed Graph side by side
        hero_layout = QHBoxLayout()
        hero_layout.setSpacing(15)

        # Circular Ring
        ring_frame = QFrame(self)
        ring_frame.setObjectName("CardFrame")
        ring_layout = QVBoxLayout(ring_frame)
        ring_layout.setContentsMargins(15, 15, 15, 15)
        ring_layout.addWidget(QLabel("OVERALL PIPELINE PROGRESS", ring_frame), 0, Qt.AlignCenter)
        self.progress_ring = CircularProgressRing(ring_frame)
        ring_layout.addWidget(self.progress_ring, 1, Qt.AlignCenter)
        hero_layout.addWidget(ring_frame)

        # Speed Line Chart
        speed_frame = QFrame(self)
        speed_frame.setObjectName("CardFrame")
        speed_layout = QVBoxLayout(speed_frame)
        speed_layout.addWidget(QLabel("LIVE NETWORK THROUGHPUT (MB/s)", speed_frame))
        self.speed_graph = SpeedHistoryGraph(speed_frame)
        speed_layout.addWidget(self.speed_graph)
        hero_layout.addWidget(speed_frame)

        col_b_layout.addLayout(hero_layout)

        # ETA and stats details card
        self.eta_frame = QFrame(self)
        self.eta_frame.setObjectName("CardFrame")
        eta_layout = QHBoxLayout(self.eta_frame)
        
        lbl_eta_title = QLabel("ESTIMATED COMPLETION:", self.eta_frame)
        lbl_eta_title.setStyleSheet("font-weight: bold; color: #A8A8A8;")
        self.lbl_eta_val = QLabel("--:--:-- remaining", self.eta_frame)
        self.lbl_eta_val.setStyleSheet("color: #D4AF37; font-weight: bold; font-size: 16px;")
        
        eta_layout.addWidget(lbl_eta_title)
        eta_layout.addWidget(self.lbl_eta_val)
        eta_layout.addStretch()
        
        col_b_layout.addWidget(self.eta_frame)

        # Sensor individual progress bars
        sensor_bar_frame = QFrame(self)
        sensor_bar_frame.setObjectName("CardFrame")
        sensor_bar_layout = QVBoxLayout(sensor_bar_frame)
        sensor_bar_layout.addWidget(QLabel("MODALITY COMPLETION METRICS", sensor_bar_frame))
        
        self.sensor_bars = {}
        for src in ["sentinel1", "sentinel2", "era5", "modis_lst"]:
            bar_row = QHBoxLayout()
            bar_row.addWidget(QLabel(src.upper(), sensor_bar_frame), 1)
            pbar = QProgressBar(sensor_bar_frame)
            pbar.setRange(0, 100)
            pbar.setValue(0)
            pbar.setFixedHeight(12)
            bar_row.addWidget(pbar, 4)
            sensor_bar_layout.addLayout(bar_row)
            self.sensor_bars[src] = pbar

        col_b_layout.addWidget(sensor_bar_frame)
        col_b_layout.addStretch()
        cols_layout.addWidget(col_b_widget, 2)

        # COLUMN C: Right Sidebar (System Health: CPU, RAM, GPU, Disk gauges)
        col_c_widget = QWidget(self)
        col_c_widget.setFixedWidth(280)
        col_c_layout = QVBoxLayout(col_c_widget)
        col_c_layout.setContentsMargins(0, 0, 0, 0)
        col_c_layout.setSpacing(15)

        col_c_layout.addWidget(QLabel("SYSTEM RESOURCE HEALTH", col_c_widget))

        self.gauge_cpu = ResourceGauge("CPU UTILIZATION", "%", col_c_widget)
        col_c_layout.addWidget(self.gauge_cpu)

        self.gauge_ram = ResourceGauge("SYSTEM RAM LOADS", "%", col_c_widget)
        col_c_layout.addWidget(self.gauge_ram)

        self.gauge_gpu = ResourceGauge("ACCELERATOR GPU UTIL", "%", col_c_widget)
        col_c_layout.addWidget(self.gauge_gpu)

        self.gauge_disk = ResourceGauge("FREE DISK CAPACITY", "GB", col_c_widget)
        col_c_layout.addWidget(self.gauge_disk)

        # Database Queue counts card
        queue_frame = QFrame(self)
        queue_frame.setObjectName("CardFrame")
        queue_layout = QVBoxLayout(queue_frame)
        queue_layout.addWidget(QLabel("DATABASE QUEUE STATS", queue_frame))
        
        self.lbl_completed = QLabel("Completed: 0", queue_frame)
        self.lbl_pending = QLabel("Pending: 0", queue_frame)
        self.lbl_failed = QLabel("Failed: 0", queue_frame)
        
        queue_layout.addWidget(self.lbl_completed)
        queue_layout.addWidget(self.lbl_pending)
        queue_layout.addWidget(self.lbl_failed)
        col_c_layout.addWidget(queue_frame)

        col_c_layout.addStretch()
        cols_layout.addWidget(col_c_widget)

        main_layout.addLayout(cols_layout, 1)

        # ----------------------------------------------------
        # 3. BOTTOM TABS PANEL (Logs & Task Tables)
        # ----------------------------------------------------
        bottom_tabs = QTabWidget(self)
        bottom_tabs.setFixedHeight(300)
        
        # Tab 1: Live Terminal log stream
        tab_log = QWidget()
        log_layout = QVBoxLayout(tab_log)
        log_layout.setContentsMargins(8, 8, 8, 8)
        
        # Log Filter controls
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter logs:", tab_log))
        
        self.cb_filter = QComboBox(tab_log)
        self.cb_filter.addItems(["ALL", "INFO", "WARNING", "ERROR"])
        self.cb_filter.currentIndexChanged.connect(self.on_filter_changed)
        filter_layout.addWidget(self.cb_filter)
        
        # Clear button
        btn_clear_log = QPushButton("Clear Console", tab_log)
        btn_clear_log.clicked.connect(self.clear_logs)
        filter_layout.addWidget(btn_clear_log)
        filter_layout.addStretch()
        
        log_layout.addLayout(filter_layout)

        self.log_terminal = QTextEdit(tab_log)
        self.log_terminal.setObjectName("LogTerminal")
        self.log_terminal.setReadOnly(True)
        log_layout.addWidget(self.log_terminal)
        
        bottom_tabs.addTab(tab_log, "LIVE SYSTEM LOGS")

        # Tab 2: Detailed registry task grid
        tab_grid = QWidget()
        grid_layout = QVBoxLayout(tab_grid)
        grid_layout.setContentsMargins(8, 8, 8, 8)

        self.task_table = QTableWidget(tab_grid)
        self.task_table.setColumnCount(6)
        self.task_table.setHorizontalHeaderLabels(["ID", "Glacier", "Source", "Date", "Retry Count", "Status"])
        self.task_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.task_table.setStyleSheet("gridline-color: #2D1B44;")
        grid_layout.addWidget(self.task_table)

        bottom_tabs.addTab(tab_grid, "SQLITE REGISTRY TASK MATRIX")

        main_layout.addWidget(bottom_tabs)

    def discover_glaciers(self) -> list:
        import glob
        geojson_dir = self.config.get("project.geojson_dir", "./inputGeoJson")
        files = glob.glob(os.path.join(geojson_dir, "*.geojson"))
        glaciers = set()
        for filepath in files:
            filename = os.path.basename(filepath)
            parts = filename.replace(".geojson", "").split("_")
            if len(parts) >= 3:
                glacier_name = "_".join(parts[:-2])
            else:
                glacier_name = parts[0]
            glaciers.add(glacier_name)
        return sorted(list(glaciers))

    def log_info(self, text: str):
        self.on_log_received("INFO", f"[SYSTEM] {text}")

    def log_warn(self, text: str):
        self.on_log_received("WARNING", f"[SYSTEM] {text}")

    def log_err(self, text: str):
        self.on_log_received("ERROR", f"[SYSTEM] {text}")

    def toggle_source(self, source_name: str):
        btn = self.switches[source_name]
        is_on = btn.text() == "ON"
        new_state = "OFF" if is_on else "ON"
        btn.setText(new_state)
        btn.setStyleSheet("background-color: #2D1B44;" if new_state == "OFF" else "background-color: #5E3A87;")
        self.config.config.setdefault("sources", {}).setdefault(source_name, {})["enabled"] = (new_state == "ON")
        self.log_info(f"Toggled sensor {source_name.upper()} {new_state}")

    def on_slider_changed(self):
        val = self.slider_downloads.value()
        self.lbl_slider_val.setText(f"{val} connections")
        self.config.config.setdefault("download", {})["async_downloads"] = val

    def on_filter_changed(self):
        self.log_filter = self.cb_filter.currentText()
        self.log_info(f"Log filter changed to: {self.log_filter}")

    def clear_logs(self):
        self.log_terminal.clear()

    def update_telemetry(self):
        """Timer callback to fetch and display hardware health."""
        telemetry = self.monitor.get_current_telemetry()
        self.gauge_cpu.set_value(telemetry["cpu_usage"])
        self.gauge_ram.set_value(telemetry["ram_usage"])
        self.gauge_gpu.set_value(telemetry["gpu_usage"])
        self.gauge_disk.set_value(telemetry["disk_free_gb"])
        
        # Periodic grid update
        if not self.orchestrator.running:
            self.refresh_task_table()
            self.on_stats_updated(self.db.get_task_statistics())

    def refresh_task_table(self):
        """Pulls recent task rows from SQLite DB and updates QTableWidget."""
        try:
            tasks = self.db.get_recent_tasks(limit=40)
            self.task_table.setRowCount(len(tasks))
            
            for row_idx, task in enumerate(tasks):
                self.task_table.setItem(row_idx, 0, QTableWidgetItem(str(task['id'])))
                self.task_table.setItem(row_idx, 1, QTableWidgetItem(str(task['glacier']).title()))
                self.task_table.setItem(row_idx, 2, QTableWidgetItem(str(task['source']).upper()))
                self.task_table.setItem(row_idx, 3, QTableWidgetItem(str(task['date'])))
                self.task_table.setItem(row_idx, 4, QTableWidgetItem(str(task['retry_count'])))
                
                status_item = QTableWidgetItem(str(task['status']))
                
                # Apply color highlights to table statuses
                status = task['status']
                if status == "CLIPPED":
                    status_item.setForeground(QColor("#00C896")) # Emerald Green
                elif status == "DOWNLOADED":
                    status_item.setForeground(QColor("#3DA5FF")) # Light Blue
                elif status == "FAILED":
                    status_item.setForeground(QColor("#FF5C5C")) # Crimson
                elif status == "RUNNING":
                    status_item.setForeground(QColor("#D4AF37")) # Gold
                else:
                    status_item.setForeground(QColor("#ECECEC")) # White
                    
                self.task_table.setItem(row_idx, 5, status_item)
        except Exception as e:
            pass

    # ----------------------------------------------------
    # ORCHESTRATOR SIGNAL SLOTS
    # ----------------------------------------------------
    @Slot(int, str, float)
    def on_task_progress(self, task_id: int, status: str, progress: float):
        self.refresh_task_table()

    @Slot(float)
    def on_overall_progress(self, val: float):
        stats = self.db.get_task_statistics()
        completed = stats["DOWNLOADED"] + stats["CLIPPED"]
        failed = stats["FAILED"]
        pending = stats["PENDING"] + stats["RUNNING"]
        self.progress_ring.set_progress(val, completed, pending, failed)

        # Update specific sensor progress bars based on query
        try:
            tasks = self.db.get_all_tasks()
            for src in ["sentinel1", "sentinel2", "era5", "modis_lst"]:
                src_tasks = [t for t in tasks if t['source'] == src]
                if src_tasks:
                    completed_src = len([t for t in src_tasks if t['status'] in ["DOWNLOADED", "CLIPPED"]])
                    pct = (completed_src / len(src_tasks)) * 100.0
                    self.sensor_bars[src].setValue(int(pct))
        except Exception:
            pass

    @Slot(float)
    def on_speed_updated(self, val: float):
        self.speed_graph.add_speed(val)

    @Slot(str)
    def on_eta_updated(self, val: str):
        self.lbl_eta_val.setText(f"{val} remaining")

    @Slot(str, str)
    def on_log_received(self, level: str, log_line: str):
        # Filtering logs
        if self.log_filter != "ALL" and level != self.log_filter:
            return
            
        color_map = {
            "INFO": "#ECECEC",
            "WARNING": "#F4B400",
            "ERROR": "#FF5C5C",
            "CAUTION": "#FF5C5C"
        }
        
        color = color_map.get(level, "#ECECEC")
        formatted = f'<font color="{color}">{log_line}</font>'
        self.log_terminal.append(formatted)
        
        # Scroll to bottom
        self.log_terminal.verticalScrollBar().setValue(
            self.log_terminal.verticalScrollBar().maximum()
        )

    @Slot(dict)
    def on_stats_updated(self, stats: dict):
        comp = stats.get("DOWNLOADED", 0) + stats.get("CLIPPED", 0)
        self.lbl_completed.setText(f"Completed: {comp}")
        self.lbl_pending.setText(f"Pending/Running: {stats.get('PENDING', 0) + stats.get('RUNNING', 0)}")
        self.lbl_failed.setText(f"Failed: {stats.get('FAILED', 0)}")

    @Slot(dict)
    def on_pipeline_finished(self, report: dict):
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.lbl_status_led.setText("●  ONLINE")
        self.lbl_status_led.setStyleSheet("color: #00C896; font-weight: bold; font-size: 14px; letter-spacing: 1px;")
        self.log_info(f"Pipeline Execution Complete. Success Rate: {report['success_rate']}")
        self.refresh_task_table()

    # ----------------------------------------------------
    # PIPELINE ACTIONS
    # ----------------------------------------------------
    def start_pipeline(self):
        # Check overrides
        self.config.config.setdefault("date_range", {})["start_date"] = self.inp_start_date.text()
        self.config.config.setdefault("date_range", {})["end_date"] = self.inp_end_date.text()
        
        cpu_val = self.inp_cpu.text()
        self.config.config.setdefault("parallelism", {})["cpu_workers"] = int(cpu_val) if cpu_val.isdigit() else cpu_val

        # Glacier selection check overrides
        selected_glaciers = [g for g, chk in self.glacier_checkboxes.items() if chk.isChecked()]
        self.config.config["glaciers_filter"] = selected_glaciers

        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        
        self.lbl_status_led.setText("●  RUNNING")
        self.lbl_status_led.setStyleSheet("color: #3DA5FF; font-weight: bold; font-size: 14px; letter-spacing: 1px;")

        # Run orchestrator in a separate thread
        self.pipeline_thread = PipelineThread(self.orchestrator)
        self.pipeline_thread.finished_signal.connect(self.on_thread_finished)
        self.pipeline_thread.start()

    def pause_pipeline(self):
        if self.orchestrator.paused:
            self.orchestrator.paused = False
            self.btn_pause.setText("PAUSE")
            self.lbl_status_led.setText("●  RUNNING")
            self.lbl_status_led.setStyleSheet("color: #3DA5FF; font-weight: bold; font-size: 14px; letter-spacing: 1px;")
            self.log_info("Pipeline Resumed.")
        else:
            self.orchestrator.paused = True
            self.btn_pause.setText("RESUME")
            self.lbl_status_led.setText("●  PAUSED")
            self.lbl_status_led.setStyleSheet("color: #F4B400; font-weight: bold; font-size: 14px; letter-spacing: 1px;")
            self.log_info("Pipeline Paused.")

    def stop_pipeline(self):
        self.orchestrator.cancel_pipeline()
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        
        self.lbl_status_led.setText("●  ABORTED")
        self.lbl_status_led.setStyleSheet("color: #FF5C5C; font-weight: bold; font-size: 14px; letter-spacing: 1px;")
        self.log_warn("Pipeline execution aborted by user.")

    def retry_failed_tasks(self):
        """Sets status of FAILED items back to PENDING to invoke scheduler again."""
        try:
            cursor = self.db.conn.cursor()
            cursor.execute("UPDATE downloads SET status = 'PENDING' WHERE status = 'FAILED'")
            self.db.conn.commit()
            self.log_info("Marked all FAILED tasks as PENDING. Ready to retry.")
            self.refresh_task_table()
            self.on_stats_updated(self.db.get_task_statistics())
        except Exception as e:
            self.log_err(f"Failed to reset failed tasks: {e}")

    def on_thread_finished(self, status):
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.refresh_task_table()
        self.on_stats_updated(self.db.get_task_statistics())

    def closeEvent(self, event):
        """Clean closure to terminate database connections and monitors."""
        self.telemetry_timer.stop()
        self.orchestrator.cancel_pipeline()
        self.db.close()
        event.accept()
