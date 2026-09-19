"""PyInstaller entry; guarded for Windows/macOS multiprocessing spawn."""
if __name__ == '__main__':
    from wildfire_data.planning.launcher import main
    main()
