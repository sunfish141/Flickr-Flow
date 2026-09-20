"""Automatic link-state reporting, with a hard offline profile for field/QA use.

Link availability is not proof a provider works. The map independently checks
real tile success and retains its offline layers on errors/timeouts.
"""
import ipaddress
import socket
import sys
from threading import RLock


class NetworkPolicy:
    def __init__(self, online=True, *, forced_offline=False):
        self.forced_offline = forced_offline
        self._online = bool(online) and not forced_offline
        self._native_online = None
        self._lock = RLock()

    @property
    def online(self):
        with self._lock:
            return not self.forced_offline and (self._online if self._native_online is None else self._native_online)

    def set_native_online(self, enabled):
        # Native reachability is authoritative: Chromium can keep reporting
        # online while Windows has already lost its internet connection.
        with self._lock:
            self._native_online = enabled

    def set_online(self, enabled):
        with self._lock:
            self._online = bool(enabled) and not self.forced_offline

    def status(self):
        return {'profile': 'desktop', 'online_enabled': self.online,
                'connectivity': 'link-available-unverified' if self.online else 'offline',
                'connection_mode': 'forced-offline' if self.forced_offline else 'automatic',
                'automatic_preparation': False}

    @staticmethod
    def local(host):
        if host in ('localhost', b'localhost', None):
            return True
        try:
            return ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host).is_loopback
        except (ValueError, TypeError):
            return False

    def audit(self, event, args):
        if self.online:
            return
        if event == 'socket.connect':
            sock, address = args
            if sock.family in (socket.AF_INET, socket.AF_INET6) and not self.local(address[0]):
                raise PermissionError('Desktop offline mode blocks outbound connections')
        elif event == 'socket.getaddrinfo' and not self.local(args[0]):
            raise PermissionError('Desktop offline mode blocks remote DNS')

    def install(self):
        # Install only in the desktop entry process, not a library import/test.
        sys.addaudithook(self.audit)
