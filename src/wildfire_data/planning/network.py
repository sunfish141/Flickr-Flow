"""Process-level outbound denial; preparation is a separate executable workflow."""
import ipaddress
import socket
import sys


def deny_outbound():
    def local(host):
        if host in ('localhost', b'localhost', None):
            return True
        try:
            return ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host).is_loopback
        except (ValueError, TypeError):
            return False

    def audit(event, args):
        if event == 'socket.connect':
            sock, address = args
            if sock.family in (socket.AF_INET, socket.AF_INET6) and not local(address[0]):
                raise PermissionError('Outbound network connections are disabled in the offline planning profile')
        elif event == 'socket.getaddrinfo' and not local(args[0]):
            raise PermissionError('Remote DNS is disabled in the offline planning profile')
    sys.addaudithook(audit)
