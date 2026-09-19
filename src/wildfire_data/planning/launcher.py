"""Bundled loopback launcher: Python and Node are not end-user requirements."""
import argparse
import multiprocessing
import os
import socket
import threading
import webbrowser


def main():
    multiprocessing.freeze_support()
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[key] = '2'
    os.environ['PROJ_NETWORK'] = 'OFF'
    parser = argparse.ArgumentParser(description='Wildfire offline planner — unsigned internal research build')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--data-dir')
    parser.add_argument('--packs-dir')
    args = parser.parse_args()
    from wildfire_data.planning.network import deny_outbound
    deny_outbound()
    import uvicorn
    from wildfire_data.planning.app import create_app
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', args.port))
    port = listener.getsockname()[1]
    app = create_app(data_root=args.data_dir, pack_root=args.packs_dir)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='info', workers=1))
    if not args.no_browser:
        def open_when_ready():
            import time
            for _ in range(300):
                if server.started:
                    webbrowser.open(f'http://127.0.0.1:{port}')
                    return
                if server.should_exit:
                    return
                time.sleep(.1)
        threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == '__main__':
    main()
