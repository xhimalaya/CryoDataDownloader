#!/usr/bin/env python3
import sys
import os
import asyncio

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.config_manager import ConfigManager
from src.task_orchestrator import TaskOrchestrator

def main():
    # 1. Initialize configuration manager with command line argument parsing
    try:
        config = ConfigManager()
    except Exception as e:
        print(f"[ERROR] Failed to load configuration: {e}")
        sys.exit(1)

    is_headless = config.get("headless", False)

    if is_headless:
        # 2. HEADLESS head mode (CLI only)
        print("="*60)
        print("          CRYO DATA DOWNLOAD INGESTION ENGINE (HEADLESS)          ")
        print("="*60)
        
        orchestrator = TaskOrchestrator(config)
        try:
            # Run the asynchronous scheduler pipeline
            asyncio.run(orchestrator.run_pipeline())
        except KeyboardInterrupt:
            print("\n[WARNING] Process interrupted by user keyboard signal. Shutting down...")
            orchestrator.cancel_pipeline()
        except Exception as e:
            print(f"[ERROR] Ingestion pipeline failed: {e}")
            sys.exit(1)
        finally:
            orchestrator.db.close()
    else:
        # 3. INTERACTIVE GUI mode (PySide6 dashboard)
        try:
            from PySide6.QtWidgets import QApplication
            from src.ui.main_window import MainWindow
        except ImportError:
            print("[ERROR] PySide6 is not installed on this environment. Cannot load GUI dashboard.")
            print("[TIP] You can run headless mode by adding the '--headless' CLI flag:")
            print("      python3 run.py --headless")
            sys.exit(1)

        app = QApplication(sys.argv)
        
        # Enable styling modifications on window geometry
        app.setStyle("Fusion")
        
        window = MainWindow(config)
        window.show()
        
        sys.exit(app.exec())

if __name__ == "__main__":
    main()
