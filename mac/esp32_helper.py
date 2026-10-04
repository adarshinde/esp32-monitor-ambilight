#!/usr/bin/env python3
import os
import socket
import threading
import time

import serial

SERIAL_PORT = "/dev/cu.usbserial-0001"
BAUD = 115200
SOCK_PATH = "/tmp/esp32ctl.sock"

lock = threading.Lock()
ser = None


def open_serial():
    global ser
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD, timeout=2)
            print(f"esp32_helper: opened {SERIAL_PORT}")
            return
        except serial.SerialException as e:
            print(f"esp32_helper: failed to open {SERIAL_PORT} ({e}), retrying in 2s")
            time.sleep(2)


def send_command(line):
    with lock:
        for attempt in range(2):
            try:
                ser.write((line + "\n").encode())
                ser.flush()
                return ser.readline().decode().strip()
            except (serial.SerialException, OSError) as e:
                print(f"esp32_helper: serial error ({e}), reconnecting")
                try:
                    ser.close()
                except Exception:
                    pass
                open_serial()
        return "ERR serial connection lost"


if os.path.exists(SOCK_PATH):
    os.remove(SOCK_PATH)

open_serial()

srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(SOCK_PATH)
srv.listen(5)


def handle(conn):
    with conn:
        data = conn.recv(1024).decode().strip()
        if not data:
            return
        resp = send_command(data)
        conn.sendall((resp + "\n").encode())


print(f"esp32_helper: holding {SERIAL_PORT} open, listening on {SOCK_PATH}")
while True:
    conn, _ = srv.accept()
    threading.Thread(target=handle, args=(conn,), daemon=True).start()
