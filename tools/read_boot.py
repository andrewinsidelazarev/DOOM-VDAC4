"""Read the launcher's passive FT_SEEN breadcrumb through ESP's TCP console."""
from pathlib import Path
import argparse
import re
import socket
import time
from datetime import datetime, timezone

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('host', nargs='?', default='192.168.1.113')
args = parser.parse_args()
data = bytearray()
out = Path(__file__).resolve().parents[1] / 'build' / 'boot-console.log'
stamp = f'Read at {datetime.now(timezone.utc).isoformat()} from {args.host}:23\n'
try:
    with socket.create_connection((args.host, 23), timeout=3) as sock:
        sock.settimeout(0.3)
        # Drain history separately: only answers to these queries establish
        # the current breadcrumb. Old register replies are not live evidence.
        until = time.monotonic() + 3
        while time.monotonic() < until:
            try:
                part = sock.recv(16384)
                if not part:
                    break
                data.extend(part)
            except TimeoutError:
                break
        reply_at = len(data)
        sock.sendall(b'tang rd 04\ntang rd 50\ntang rd 54\n')
        until = time.monotonic() + 3
        while time.monotonic() < until:
            try:
                part = sock.recv(16384)
                if not part:
                    break
                data.extend(part)
            except TimeoutError:
                if b'tang 54 =' in data[reply_at:] or b'LINK is not ready' in data[reply_at:]:
                    break
except OSError as error:
    message = f'Cannot read current boot error: {error}'
    out.write_text(stamp + message + '\n' + data.decode('utf-8', errors='replace'), encoding='utf-8')
    print(message)
    raise SystemExit(1)
text = data.decode('utf-8', errors='replace')
out.write_text(stamp + text, encoding='utf-8')
reply = data[reply_at:].decode('utf-8', errors='replace')
for line in reply.splitlines():
    if re.search(r'tang (04|50|54) =', line):
        print(line)
match = re.search(r'tang 50 = ([0-9A-Fa-f]{8})', reply)
result = 2
if match:
    value = int(match[1], 16)
    if value >> 16 == 0x4F:
        print(f'DOOM boot stage: {(value >> 8) & 255:02X}, error: {value & 255:02X}')
        result = 0
    else:
        print('No DOOM breadcrumb (the traced launcher has not run, or another FT transaction followed it).')
elif 'LINK is not ready' in reply:
    print('Cannot read current boot error: tang: LINK is not ready')
    for line in text.splitlines():
        if 'LINK skipped:' in line:
            print(line)
else:
    print('Cannot read current boot error: no register reply received')
print(f'Console log: {out}')
raise SystemExit(result)
