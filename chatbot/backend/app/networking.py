"""Forwarded headers are meaningful only behind an explicitly trusted peer."""
from collections import OrderedDict, deque
from ipaddress import ip_address, ip_network
from threading import Lock
import time
from urllib.parse import urlsplit


def trusted(address, networks):
    try:
        address = ip_address(address)
    except ValueError:
        return False
    return any(address in ip_network(network, strict=False) for network in networks)


def client_ip(request, networks):
    peer = request.client.host if request.client else 'local'
    if not trusted(peer, networks):
        return peer
    forwarded = request.headers.get('x-forwarded-for', '')
    for value in reversed(forwarded.split(',')):
        try:
            address = str(ip_address(value.strip()))
        except ValueError:
            # Do not cross a malformed hop into client-controlled header data.
            break
        if not trusted(address, networks):
            return address
    try:
        address = str(ip_address(request.headers.get('x-real-ip', '').strip()))
    except ValueError:
        return peer
    return peer if trusted(address, networks) else address


def origin_allowed(request, settings):
    origin = request.headers.get('origin')
    if origin and origin in settings.allowed_origins:
        return True
    if request.headers.get('sec-fetch-site') == 'cross-site':
        return False
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
        if (parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
            return False
        hosts = [request.headers.get('host', '')]
        peer = request.client.host if request.client else ''
        if trusted(peer, settings.trusted_proxies):
            hosts.append(request.headers.get('x-forwarded-host', '').split(',')[-1].strip())
        # TLS can terminate at another gateway before this HTTP hop. Preserve
        # the existing authority comparison; public aliases use the exact allowlist.
        return parsed.netloc in hosts
    except ValueError:
        return False


class RateLimiter:
    """Thread-safe, globally pruned sliding windows with a hard LRU key bound."""
    def __init__(self, count, window, max_keys=4096, clock=time.monotonic):
        self.count, self.window, self.max_keys, self.clock = count, window, max_keys, clock
        self.windows, self.lock = OrderedDict(), Lock()

    def allow(self, key, *, consume=True):
        with self.lock:
            now = self.clock()
            for old_key, values in list(self.windows.items()):
                while values and values[0] <= now - self.window:
                    values.popleft()
                if not values:
                    del self.windows[old_key]
            values = self.windows.get(key)
            if values is not None and len(values) >= self.count:
                self.windows.move_to_end(key)
                return False
            if consume:
                if values is None:
                    values = self.windows[key] = deque()
                values.append(now)
                self.windows.move_to_end(key)
                while len(self.windows) > self.max_keys:
                    self.windows.popitem(last=False)
            return True

    def clear(self, key):
        with self.lock:
            self.windows.pop(key, None)
