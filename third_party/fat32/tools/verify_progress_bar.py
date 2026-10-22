# -*- coding: utf-8 -*-
"""Проверка индикатора скачивания на работающем UART-стенде.

Стенд build/unreal-zifi должен быть уже запущен с FAT32_CACHE_SYNC=1.
Скрипт отдаёт файл по HTTP на loopback, запрашивает его штатным путём ZiFi и
следит за позицией ячейки индикатора в RAM эмулятора. Ожидание — 100 шагов
на файл с известной длиной; неподвижный индикатор считается отказом.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import sys
import threading
import time

from unreal_zifi_memory import Memory

SIZE = 655360
NAME = 'progress.bin'
PAYLOAD = bytes((i * 31 + i // 16384 + SIZE) % 251 for i in range(SIZE))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Length', str(len(PAYLOAD)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *args):
        pass


def main():
    # Настоящая плата живёт в локальной сети и до loopback не дотянется.
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1',
                        help='адрес этой машины, видимый плате')
    args = parser.parse_args()
    bind = '127.0.0.1' if args.host == '127.0.0.1' else ''
    server = ThreadingHTTPServer((bind, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    m = Memory()
    try:
        assert m.get('fat_active') == b'\0' and m.get('load_sw', offset=1) == b'\0'
        m.put('cmd_conn2site_adr', args.host.encode() + bytes(1))
        m.put('request_port', server.server_port.to_bytes(2, 'little'))
        m.put('request_path', ('/' + NAME).encode() + b'\0')
        m.put('load_ram_page', bytes([0x20]), offset=1)
        m.put('do_after_load', b'\3', offset=1)
        m.put('load_sw', b'\1', offset=1)
        steps, last, got, began = 0, None, 0, time.monotonic()
        while time.monotonic() - began < 900:
            time.sleep(0.02)
            bar = int.from_bytes(m.get('progress_bar', 2, offset=1), 'little')
            got = int.from_bytes(m.get('readed_len_low', 2, offset=1), 'little') \
                + (m.get('readed_len_high', offset=1)[0] << 16)
            if last is not None and bar != last:
                steps += 1
            last = bar
            if got >= SIZE and m.get('load_sw', offset=1) == b'\0':
                break
        else:
            raise AssertionError(('индикатор: загрузка не завершилась', got))
        if steps == 0:
            raise AssertionError('индикатор не сдвинулся ни разу за всю загрузку')
        # Сервер называет Content-Length, значит индикатор пропорциональный:
        # полоса заполняется ровно BAR_CELLS ячеек за весь файл, а не крутится
        # по ячейке на 256 байт. Режим активности остаётся для ответов без длины.
        expected = 100
        report = {'bytes': got, 'steps': steps, 'expected_steps': expected,
                  'mode': 'пропорциональный (Content-Length известен)',
                  'status': 'PASS' if abs(steps - expected) <= 2 else 'FAIL'}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['status'] == 'PASS' else 1
    finally:
        m.close()
        server.shutdown()


if __name__ == '__main__':
    sys.exit(main())
