"""Native window with bundled Qt WebEngine; no Node or filesystem JS bridge."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView


def permitted_request(url, base, online):
    parsed, local = urlsplit(url), urlsplit(base)
    if parsed.scheme in ('data', 'blob', 'about'):
        return True
    if (parsed.scheme, parsed.netloc) == (local.scheme, local.netloc):
        return True
    return online and parsed.scheme == 'https' and parsed.netloc == 'tile.openstreetmap.org'


class Filter(QWebEngineUrlRequestInterceptor):
    def __init__(self, base, policy, parent):
        super().__init__(parent)
        self.base, self.policy = base, policy

    def interceptRequest(self, info):
        info.block(not permitted_request(info.requestUrl().toString(), self.base, self.policy.online))


class Page(QWebEnginePage):
    def __init__(self, profile, parent, base, policy):
        super().__init__(profile, parent)
        self.base, self.policy = base, policy
        self.permissionRequested.connect(lambda permission: permission.deny())

    def acceptNavigationRequest(self, url, kind, main):
        if url.toString().startswith(self.base + '/') or url.toString() == 'about:blank':
            return True
        if kind == QWebEnginePage.NavigationType.NavigationTypeLinkClicked and url.scheme() in ('http', 'https'):
            if self.policy.online:
                QDesktopServices.openUrl(url)
            else:
                QMessageBox.information(self.parent(), 'Offline', 'This link needs internet access. Reconnect to open it.')
        return False


def run_window(base, policy, *, smoke_output=None):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName('Wildfire Atlas')
    class Window(QMainWindow):
        def closeEvent(self, event):
            if smoke_output or QMessageBox.question(self, 'Close Wildfire Atlas?',
                'The current map simulation is temporary and will end. Close the app?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                event.accept()
            else:
                event.ignore()
    window = Window()
    window.setWindowTitle('Wildfire Atlas — Research desktop · unsigned internal build')
    window.resize(1440, 1000)
    view = QWebEngineView(window)
    # Off-the-record browser profile: no disk tile cache, cookies or credentials.
    profile = QWebEngineProfile(app)
    profile.setHttpUserAgent(profile.httpUserAgent() + ' WildfireAtlas/0.2-internal')
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    profile.setHttpCacheMaximumSize(64 * 1024 * 1024)
    interceptor = Filter(base, policy, profile)
    profile.setUrlRequestInterceptor(interceptor)
    page = Page(profile, view, base, policy)
    view.setPage(page)
    window.setCentralWidget(view)

    def download(request):
        if not request.url().toString().startswith(base + '/planning/api/scenarios/'):
            request.cancel()
            return
        filename = Path(request.suggestedFileName()).name
        destination, _ = QFileDialog.getSaveFileName(window, 'Export scenario', filename)
        if not destination:
            request.cancel()
            return
        target = Path(destination)
        request.setDownloadDirectory(str(target.parent))
        request.setDownloadFileName(target.name)
        request.accept()
    profile.downloadRequested.connect(download)
    window.show()
    view.load(QUrl(base + '/'))
    result = {'ready': False}
    if smoke_output:
        output = Path(smoke_output)
        output.mkdir(parents=True, exist_ok=True)
        def check_result(value):
            if result['ready']:
                return
            try:
                data = json.loads(value or '{}')
            except (TypeError, ValueError):
                return
            if data.get('explorer') and data.get('overview'):
                result.update(data, ready=True)
                def capture():
                    window.grab().save(str(output / 'native-window.png'))
                    (output / 'native-window.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
                    app.quit()
                # DOM readiness precedes Chromium's first composited frame.
                QTimer.singleShot(2500, capture)
        def check():
            page.runJavaScript("""JSON.stringify({
                explorer: !!document.querySelector('#explorer')?.contentDocument?.querySelector('#installed-regions'),
                overview: document.querySelector('#explorer')?.contentDocument?.querySelector('#map')?.dataset.overview === 'ready',
                planner_visible: !!document.querySelector('#planner'),
                network: document.querySelector('#network-status')?.textContent,
                title: document.title
            })""", check_result)
        timer = QTimer(window)
        timer.timeout.connect(check)
        timer.start(500)
        QTimer.singleShot(45000, app.quit)
    code = app.exec()
    # Destroy the widget/page tree before its profile. In particular, passing
    # None to QWebEngineView.setPage is not a supported shutdown operation.
    from shiboken6 import delete
    delete(window)
    delete(profile)
    if smoke_output and not result['ready']:
        raise RuntimeError('Native window did not render both workspaces before timeout')
    return code
