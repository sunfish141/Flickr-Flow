"""Frozen multiprocessing-safe entry point for the whole application."""
if __name__ == '__main__':
    from wildfire_data.desktop.launcher import main
    try:
        main()
    except Exception as exc:
        import logging
        logging.exception('Desktop application stopped')
        import sys
        if '--no-ui' not in sys.argv and '--smoke-window' not in sys.argv:
            from PySide6.QtWidgets import QApplication, QMessageBox
            application = QApplication.instance() or QApplication([])
            QMessageBox.critical(None, 'Wildfire Atlas could not start', str(exc))
        sys.exit(1)  # Avoid an additional bootloader dialog, especially in QA.
