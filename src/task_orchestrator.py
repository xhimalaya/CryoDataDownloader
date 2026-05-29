import os
import glob
import json
import asyncio
import datetime
import time
import traceback
import concurrent.futures
from typing import Dict, Any, List, Optional, Callable

# Gracefully import PySide
try:
    from PySide6.QtCore import QObject, Signal
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False

from src.config_manager import ConfigManager
from src.db_manager import DBManager
from src.resource_monitor import ResourceMonitor
from src.download_engine import DownloadEngine
from src.processing_engine import ProcessingEngine, process_single_raster

class OrchestratorSignals:
    """Class holding signals for GUI integration."""
    def __init__(self):
        pass

if HAS_PYSIDE:
    class OrchestratorSignalsQ(QObject):
        task_progress = Signal(int, str, float) # task_id, status, progress (0.0 to 1.0)
        overall_progress = Signal(float)       # percentage (0.0 to 100.0)
        speed_updated = Signal(float)          # speed in MB/s
        eta_updated = Signal(str)              # ETA string
        log_received = Signal(str, str)        # level (INFO/WARNING/ERROR), message
        stats_updated = Signal(dict)           # stats dictionary
        pipeline_finished = Signal(dict)       # final report dict
else:
    class OrchestratorSignalsQ:
        pass

class TaskOrchestrator:
    def __init__(self, config_manager: ConfigManager):
        self.config = config_manager
        self.db = DBManager()
        self.monitor = ResourceMonitor()
        
        # Configure signals
        if HAS_PYSIDE:
            self.signals = OrchestratorSignalsQ()
        else:
            self.signals = None
            
        self.download_engine = None
        self.process_executor = None
        self.running = False
        self.paused = False
        
        # Ingestion rates tracking
        self.start_time = None
        self.downloaded_bytes = 0
        self.active_tasks_count = 0

    def log(self, level: str, msg: str):
        """Standardized logger that prints and sends signals to GUI."""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        log_line = f"[{timestamp}] {msg}"
        print(f"[{level}] {log_line}")
        
        # Write to file
        log_file = "./metadata/logs/pipeline.log"
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, "a") as f:
            f.write(f"{level} | {datetime.datetime.now().isoformat()} | {msg}\n")
            
        if self.signals and HAS_PYSIDE:
            self.signals.log_received.emit(level, log_line)

    def scan_and_generate_tasks(self):
        """Scans inputGeoJson directory and creates PENDING tasks in database."""
        self.log("INFO", "Scanning input GeoJSON directory...")
        geojson_dir = self.config.get("project.input_geojson_folder", self.config.get("project.geojson_dir", "./inputGeoJson"))
        files = glob.glob(os.path.join(geojson_dir, "*.geojson"))
        
        if not files:
            self.log("WARNING", f"No GeoJSON files found in {geojson_dir}. Creating mock files for demo.")
            return

        start_date_str = self.config.get("date_range.start_date", "2024-01-01")
        end_date_str = self.config.get("date_range.end_date", "2024-03-31")
        
        try:
            start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")
            end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d")
        except ValueError:
            self.log("ERROR", "Invalid dates in config, defaulting to 2024-01-01 -> 2024-03-31")
            start_date = datetime.datetime(2024, 1, 1)
            end_date = datetime.datetime(2024, 3, 31)

        glaciers_filter = self.config.get("glaciers_filter", [])

        task_count = 0
        for filepath in files:
            filename = os.path.basename(filepath)
            parts = filename.replace(".geojson", "").split("_")
            
            # Naming convention: <glacier_name>_<platform>_<dataset>.geojson
            if len(parts) >= 3:
                glacier_name = "_".join(parts[:-2])
                platform = parts[-2]
                dataset = parts[-1]
            else:
                glacier_name = parts[0]
                platform = "unknown"
                dataset = "unknown"

            # Filter out glaciers if CLI parameter specified
            if glaciers_filter and glacier_name not in glaciers_filter:
                continue

            # Validate GeoJSON structure (simulate reading geometry bounds)
            try:
                with open(filepath, 'r') as f:
                    geo_data = json.load(f)
                    # Real geometry check can use shape(geo_data['features'][0]['geometry'])
                self.log("INFO", f"Validated boundary polygon for {glacier_name} ({platform.upper()} / {dataset.upper()})")
            except Exception as e:
                self.log("ERROR", f"Failed to validate geometry in {filename}: {e}")
                continue

            # Determine temporal frequency based on dataset
            if dataset in ["sentinel1", "sentinel2"]:
                freq_days = 12 # Sentinel orbit repeat frequency
            elif dataset == "era5":
                freq_days = 7 # Weekly chunks of hourly reanalysis data
            elif dataset == "modis":
                freq_days = 5 # MODIS 5-day tiles
            else:
                freq_days = 30 # Default monthly

            # Generate task intervals chronologically
            current_date = start_date
            while current_date <= end_date:
                date_str = current_date.strftime("%Y-%m-%d")
                
                # Check config sources filter
                source_enabled = self.config.get(f"sources.{dataset}.enabled", True)
                if source_enabled:
                    task_id = self.db.add_task(
                        source=dataset,
                        glacier=glacier_name,
                        date_str=date_str,
                        tile_id=f"T_{glacier_name[:3].upper()}_{date_str.replace('-', '')}",
                        geojson_path=filepath
                    )
                    task_count += 1
                
                current_date += datetime.timedelta(days=freq_days)
                
        self.log("INFO", f"GeoJSON scanning completed. Generated {task_count} download/process tasks in SQLite registry.")

    def update_statistics(self):
        """Pushes database status statistics to the UI."""
        stats = self.db.get_task_statistics()
        if self.signals and HAS_PYSIDE:
            self.signals.stats_updated.emit(stats)

    async def run_pipeline(self):
        """Asynchronous execution loop running downloader and processor."""
        self.running = True
        self.paused = False
        self.start_time = time.time()
        self.downloaded_bytes = 0
        
        self.log("INFO", "Initializing CryoDataDownloader Pipeline...")
        self.db.reset_running_tasks() # Reset tasks crashed in previous run
        self.scan_and_generate_tasks()
        self.update_statistics()

        # Config parameters
        async_downloads = self.config.get("parallelism.async_downloads", self.config.get("download.async_downloads", 100))
        max_retries = self.config.get("download.retries", 8)
        output_dir = self.config.get("project.output_dir", "./data")
        mode = self.config.get("download.mode", "resilient") # resilient or strict
        
        # Max CPU Workers
        cpu_workers_config = self.config.get("parallelism.cpu_workers", "auto")
        if cpu_workers_config == "auto":
            max_cpu_workers = max(1, os.cpu_count() - 2)
        else:
            max_cpu_workers = int(cpu_workers_config)

        # Initialize engines
        self.download_engine = DownloadEngine(
            async_downloads=async_downloads, 
            max_retries=max_retries, 
            db_manager=self.db,
            config=self.config
        )
        self.process_executor = concurrent.futures.ProcessPoolExecutor(max_workers=max_cpu_workers)

        pending_tasks = self.db.get_pending_tasks()
        total_tasks = len(pending_tasks)
        
        if total_tasks == 0:
            self.log("INFO", "All tasks are already completed. Headless run finished.")
            self.running = False
            self.process_executor.shutdown(wait=False)
            self.update_statistics()
            return

        self.log("INFO", f"Starting ingestion queue. Total active tasks: {total_tasks} using {max_cpu_workers} CPU workers.")
        
        completed_tasks = 0
        failed_tasks_count = 0
        skipped_tasks_count = 0

        # Create download tasks mapping
        active_futures = {}
        
        async_tasks = []
        for task in pending_tasks:
            # Checkpoint restart: skip if already COMPLETED / CLIPPED
            if task['status'] in ["DOWNLOADED", "CLIPPED"]:
                skipped_tasks_count += 1
                completed_tasks += 1
                continue
                
            async_tasks.append(task)

        # Bounded processing queue to avoid disk explosion
        # Stream pipeline: download -> verify -> reproject -> clip -> delete temp
        queue = asyncio.Queue()
        for t in async_tasks:
            await queue.put(t)

        # Semaphore to control network concurrency
        network_semaphore = asyncio.Semaphore(async_downloads)

        async def worker():
            nonlocal completed_tasks, failed_tasks_count
            
            while not queue.empty():
                if not self.running:
                    break
                    
                while self.paused:
                    await asyncio.sleep(0.5)

                # DYNAMIC RESOURCE THROTTLING
                # Read hardware health
                telemetry = self.monitor.get_current_telemetry()
                cpu_usage = telemetry["cpu_usage"]
                ram_usage = telemetry["ram_usage"]

                # CPU Throttling (if CPU usage > max configured, add safety backoff)
                max_cpu_limit = self.config.get("resource_control.cpu.max_usage_percent", 70)
                if cpu_usage > max_cpu_limit:
                    self.log("WARNING", f"System CPU exceeded {max_cpu_limit}% limit (Current: {cpu_usage}%). Throttling download engine workers...")
                    await asyncio.sleep(2.0)
                    continue

                # RAM Throttling
                max_ram_limit = self.config.get("resource_control.ram.max_usage_percent", 65)
                emergency_limit = self.config.get("resource_control.ram.emergency_stop_percent", 90)
                
                if ram_usage > emergency_limit:
                    self.log("CAUTION", f"EMERGENCY: RAM usage hit {ram_usage}%! Freezing all ingest processes immediately.")
                    await asyncio.sleep(5.0)
                    continue
                elif ram_usage > max_ram_limit:
                    self.log("WARNING", f"RAM usage exceeded {max_ram_limit}% limit (Current: {ram_usage}%). Throttling raster processing.")
                    await asyncio.sleep(1.5)

                task = await queue.get()
                task_id = task['id']
                source = task['source']
                glacier = task['glacier']
                date_str = task['date']

                self.db.update_task_status(task_id, "RUNNING")
                self.update_statistics()
                if self.signals and HAS_PYSIDE:
                    self.signals.task_progress.emit(task_id, "RUNNING", 0.2)

                # Build output download path
                raw_filename = f"{glacier.lower()}_{source}_{date_str.replace('-', '')}.raw"
                download_path = os.path.join(output_dir, "raw", source, raw_filename)

                # STAGE 1: ASYNC DOWNLOAD
                geojson_path = task.get("geojson_path", "")
                self.log("INFO", f"Downloading {source.upper()} tile for {glacier.title()} [{date_str}]")
                download_success = await self.download_engine.download_file(
                    task_id=task_id,
                    source=source,
                    glacier=glacier,
                    date_str=date_str,
                    output_path=download_path,
                    progress_callback=self.progress_bridge,
                    geojson_path=geojson_path
                )

                if download_success:
                    self.log("INFO", f"Download complete for {source.upper()} [{date_str}]. Routing to multiprocessing clipper...")
                    self.db.update_task_status(task_id, "DOWNLOADED")
                    self.update_statistics()
                    if self.signals and HAS_PYSIDE:
                        self.signals.task_progress.emit(task_id, "DOWNLOADED", 0.6)

                    # STAGE 2: MULTIPROCESS PROCESSING (Reproject, Clip, Compress, Zarr)
                    loop = asyncio.get_running_loop()
                    buffer_km = self.config.get("processing.glacier_buffer_km", 5)
                    geojson_path = task.get("geojson_path", "")
                    
                    try:
                        # Offload process computation to executor pool to prevent blocking the event loop
                        result = await loop.run_in_executor(
                            self.process_executor,
                            process_single_raster,
                            task_id,
                            download_path,
                            output_dir,
                            glacier,
                            source,
                            buffer_km,
                            geojson_path
                        )

                        if result["success"]:
                            # Delete raw file if storage option is set
                            if self.config.get("download.delete_temp_files", self.config.get("storage.delete_temp_files", True)):
                                if os.path.exists(download_path):
                                    os.remove(download_path)

                            self.db.update_task_status(task_id, "CLIPPED", filepath=result["clipped_path"])
                            self.log("INFO", f"Clipping & GeoTIFF save successful for {glacier.title()} [{date_str}]")
                            if self.signals and HAS_PYSIDE:
                                self.signals.task_progress.emit(task_id, "CLIPPED", 1.0)
                        else:
                            raise IOError(result["error"])

                    except Exception as pe_err:
                        self.log("ERROR", f"Geospatial processing failed for {glacier.title()} [{date_str}]: {pe_err}")
                        self.db.update_task_status(task_id, "FAILED", last_error=f"Clipping failed: {pe_err}")
                        failed_tasks_count += 1
                        if self.signals and HAS_PYSIDE:
                            self.signals.task_progress.emit(task_id, "FAILED", -1.0)
                            
                        # If in Strict Mode, immediately abort the whole pipeline
                        if mode == "strict":
                            self.log("ERROR", "Strict mode active. Aborting pipeline on failure.")
                            self.running = False

                else:
                    self.log("ERROR", f"Download failed after retries for {source.upper()} [{date_str}]")
                    failed_tasks_count += 1
                    if self.signals and HAS_PYSIDE:
                        self.signals.task_progress.emit(task_id, "FAILED", -1.0)
                    
                    if mode == "strict":
                        self.log("ERROR", "Strict mode active. Aborting pipeline on failure.")
                        self.running = False

                completed_tasks += 1
                
                # Update progress speeds
                self.downloaded_bytes += os.path.getsize(download_path) if os.path.exists(download_path) else 100 * 1024
                elapsed = time.time() - self.start_time
                avg_speed = (self.downloaded_bytes / (1024 * 1024)) / (elapsed + 0.1) # MB/s
                
                # Progress percentages
                percentage = (completed_tasks / total_tasks) * 100.0
                
                # ETA calculation
                remaining = total_tasks - completed_tasks
                eta_sec = (elapsed / completed_tasks) * remaining if completed_tasks > 0 else 0
                eta_str = str(datetime.timedelta(seconds=int(eta_sec)))

                self.update_statistics()

                if self.signals and HAS_PYSIDE:
                    self.signals.overall_progress.emit(percentage)
                    self.signals.speed_updated.emit(avg_speed)
                    self.signals.eta_updated.emit(eta_str)

                queue.task_done()

        # Run 4 parallel workers in the event loop for concurrent network downloads
        workers = [asyncio.create_task(worker()) for _ in range(min(4, total_tasks))]
        await asyncio.gather(*workers)

        # Generate report file
        elapsed_time = time.time() - self.start_time
        success_rate = (completed_tasks - failed_tasks_count) / completed_tasks * 100 if completed_tasks > 0 else 100.0
        
        report_data = {
            "timestamp": datetime.datetime.now().isoformat(),
            "elapsed_seconds": round(elapsed_time, 2),
            "completed": completed_tasks,
            "failed": failed_tasks_count,
            "skipped": skipped_tasks_count,
            "success_rate": f"{round(success_rate, 1)}%"
        }

        report_file = "./metadata/reports/download_report.json"
        with open(report_file, "w") as rf:
            json.dump(report_data, rf, indent=2)

        self.log("INFO", f"Pipeline completed. Finished: {completed_tasks}, Failed: {failed_tasks_count}. Report exported to {report_file}")
        
        if self.signals and HAS_PYSIDE:
            self.signals.pipeline_finished.emit(report_data)
            
        self.running = False
        self.process_executor.shutdown(wait=True)

    def progress_bridge(self, task_id: int, progress: float):
        """Bridges download events to the GUI signals."""
        if progress < 0:
            if self.signals and HAS_PYSIDE:
                self.signals.task_progress.emit(task_id, "FAILED", -1.0)
        else:
            if self.signals and HAS_PYSIDE:
                # scale download progress to 0.2 -> 0.6 of overall task
                mapped_progress = 0.2 + (progress * 0.4)
                self.signals.task_progress.emit(task_id, "RUNNING", mapped_progress)

    def cancel_pipeline(self):
        """Stops the running pipeline."""
        self.running = False
        self.log("WARNING", "Pipeline cancelled by user action.")
        if self.process_executor:
            self.process_executor.shutdown(wait=False)
