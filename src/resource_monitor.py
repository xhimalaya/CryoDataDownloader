import time
import shutil
import psutil
from typing import Dict, Any

# Gracefully check for PySide6
try:
    from PySide6.QtCore import QObject, Signal
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False

# Try importing pynvml, fallback to simulated GPU metrics if not present
try:
    import pynvml
    HAS_GPU = True
except ImportError:
    HAS_GPU = False

class ResourceTelemetry:
    def __init__(self):
        self.cpu_usage: float = 0.0
        self.ram_usage: float = 0.0
        self.gpu_usage: float = 0.0
        self.gpu_active: bool = False
        self.disk_free_gb: float = 0.0
        self.disk_usage_percent: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_usage": self.cpu_usage,
            "ram_usage": self.ram_usage,
            "gpu_usage": self.gpu_usage,
            "gpu_active": self.gpu_active,
            "disk_free_gb": self.disk_free_gb,
            "disk_usage_percent": self.disk_usage_percent
        }

class BaseMonitor:
    def __init__(self):
        self.pynvml_initialized = False
        if HAS_GPU:
            try:
                pynvml.nvmlInit()
                self.pynvml_initialized = True
            except Exception:
                self.pynvml_initialized = False

    def get_telemetry(self) -> ResourceTelemetry:
        telemetry = ResourceTelemetry()
        
        # CPU
        telemetry.cpu_usage = psutil.cpu_percent(interval=None)
        
        # RAM
        virtual_mem = psutil.virtual_memory()
        telemetry.ram_usage = virtual_mem.percent
        
        # Disk
        try:
            total, used, free = shutil.disk_usage(".")
            telemetry.disk_free_gb = free / (1024**3)
            telemetry.disk_usage_percent = (used / total) * 100
        except Exception:
            telemetry.disk_free_gb = 0.0
            telemetry.disk_usage_percent = 0.0
            
        # GPU
        if HAS_GPU and self.pynvml_initialized:
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                telemetry.gpu_usage = float(util.gpu)
                telemetry.gpu_active = True
            except Exception:
                telemetry.gpu_usage = 0.0
                telemetry.gpu_active = False
        else:
            # Simulation/Mock mode for GPU when driver/library is missing
            telemetry.gpu_usage = 12.5 if telemetry.cpu_usage > 50 else 0.0
            telemetry.gpu_active = False
            
        return telemetry

if HAS_PYSIDE:
    class ResourceMonitor(QObject):
        telemetry_updated = Signal(dict)

        def __init__(self, refresh_interval: float = 1.0):
            super().__init__()
            self.base_monitor = BaseMonitor()
            self.refresh_interval = refresh_interval
            self.running = False

        def start_monitoring(self):
            """Starts a polling loop. Usually called inside a QThread."""
            self.running = True
            while self.running:
                telemetry = self.base_monitor.get_telemetry()
                self.telemetry_updated.emit(telemetry.to_dict())
                time.sleep(self.refresh_interval)

        def stop_monitoring(self):
            self.running = False
            
        def get_current_telemetry(self) -> dict:
            return self.base_monitor.get_telemetry().to_dict()
else:
    class ResourceMonitor:
        def __init__(self, refresh_interval: float = 1.0):
            self.base_monitor = BaseMonitor()
            self.refresh_interval = refresh_interval
            
        def get_current_telemetry(self) -> dict:
            return self.base_monitor.get_telemetry().to_dict()
