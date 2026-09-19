"""Launch the complete desktop app; browser/server modes are optional diagnostics."""
import argparse
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import threading
import time


def main():
    multiprocessing.freeze_support()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '2'
    os.environ['PROJ_NETWORK'] = 'OFF'
    parser = argparse.ArgumentParser(description='Wildfire Atlas — complete research desktop app')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--browser', action='store_true')
    mode.add_argument('--no-ui', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--resources', type=Path)
    parser.add_argument('--smoke-window', type=Path, help='Internal QA: capture native view and exit')
    args = parser.parse_args()
    from wildfire_data.planning.paths import data_directory
    root = args.data_dir or data_directory()
    root.mkdir(parents=True, exist_ok=True)
    log = RotatingFileHandler(root / 'desktop.log', maxBytes=2*1024*1024, backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[log], format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    from wildfire_data.desktop.network import NetworkPolicy
    policy = NetworkPolicy(forced_offline=os.environ.get('WILDFIRE_FORCE_OFFLINE') == '1')
    policy.install()
    import uvicorn
    from wildfire_data.desktop.app import create_app
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', args.port))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(data_root=root, resources=args.resources, policy=policy),
        host='127.0.0.1', port=port, log_config=None, access_log=False, workers=1, timeout_graceful_shutdown=10))
    base = f'http://127.0.0.1:{port}'
    logging.info('Wildfire Atlas starting at %s; %s connectivity', base, policy.status()['connection_mode'])
    if args.no_ui:
        try:
            server.run(sockets=[listener])
        finally:
            listener.close()
        return
    failures = []
    def serve():
        try:
            server.run(sockets=[listener])
        except BaseException as exc:
            failures.append(type(exc).__name__)
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 45
        while not server.started:
            if failures or not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('Desktop service could not start. Check desktop.log; another planner may have this data directory open.')
            time.sleep(.05)
        if args.browser:
            import webbrowser
            webbrowser.open(base)
            thread.join()
        else:
            from wildfire_data.desktop.window import run_window
            run_window(base, policy, smoke_output=args.smoke_window)
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        listener.close()


if __name__ == '__main__':
    main()
