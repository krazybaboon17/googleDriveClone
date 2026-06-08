"""
ESP32-CAM Face Tracker — PD Controller
pip install opencv-python numpy mediapipe
"""

import socket, struct, threading, time, cv2, numpy as np
import mediapipe as mp

# ─── CONFIG ──────────────────────────────────────────────
ESP32_IP   = "192.168.68.109"
CAM_PORT   = 5005
SERVO_PORT = 5006

FRAME_W = 320
FRAME_H = 240

# Slow, stable tuning to prevent overshoot
# ─── CONFIG ──────────────────────────────────────────────

# Faster movement (KP), with stronger brakes (KD) to prevent wobble
# ─── CONFIG ──────────────────────────────────────────────

# Doubled speed (KP) and increased brakes (KD) to handle the momentum
PAN_KP  = 6.0 
PAN_KD  = 10.0 
TILT_KP = 6.0 
TILT_KD = 10.0 
   
# Widened dead zone to ignore sensor noise/jitter
DEAD_ZONE = 0.15

# Flip to +1 if that servo moves the wrong way
PAN_DIR  = 1
TILT_DIR = 1

# Widened to the absolute limits of standard 180-degree servos
# (Ensure your physical wires or plastic brackets don't snag at extreme angles)
PAN_MIN,  PAN_MAX  = 0,  180
TILT_MIN, TILT_MAX = 0,  180

# ─── STATE ───────────────────────────────────────────────
pan  = 90.0
tilt = 90.0
pan_err_prev  = 0.0
tilt_err_prev = 0.0

# ─── SOCKETS ─────────────────────────────────────────────
cam_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
cam_sock.bind(("0.0.0.0", CAM_PORT))
cam_sock.settimeout(1.0)
servo_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ─── SHARED FRAME ────────────────────────────────────────
_frame      = None
_frame_lock = threading.Lock()
_stop       = threading.Event()

# ─── REASSEMBLY ──────────────────────────────────────────
_buf       = {}
_buf_total = 0
_buf_id    = -1
_buf_lock  = threading.Lock()


def recv_thread():
    global _frame, _buf, _buf_total, _buf_id
    while not _stop.is_set():
        try:
            data, _ = cam_sock.recvfrom(1500)
        except socket.timeout:
            continue
        except OSError:
            break
        if len(data) < 12:
            continue
        fid, cidx, total = struct.unpack_from("<iii", data, 0)
        payload = data[12:]
        with _buf_lock:
            if fid > _buf_id:
                _buf = {}; _buf_id = fid; _buf_total = total
            elif fid < _buf_id:
                continue
            _buf[cidx] = payload
            if len(_buf) != _buf_total:
                continue
            jpeg = b"".join(_buf[i] for i in range(_buf_total))
            _buf = {}
        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is not None:
            with _frame_lock:
                _frame = frame


def send_servo(p, t):
    servo_sock.sendto(struct.pack("<ii", int(p), int(t)),
                      (ESP32_IP, SERVO_PORT))


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def main():
    global pan, tilt, _frame, pan_err_prev, tilt_err_prev

    detector = mp.solutions.face_detection.FaceDetection(
        model_selection=0, min_detection_confidence=0.6)

    cx = FRAME_W // 2
    cy = FRAME_H // 2
    centered = False

    last_pan_sent  = 90
    last_tilt_sent = 90

    threading.Thread(target=recv_thread, daemon=True).start()
    print(f"Listening :{CAM_PORT} → {ESP32_IP}:{SERVO_PORT}  |  Q to quit")

    while True:
        with _frame_lock:
            frame  = _frame
            _frame = None

        if frame is None:
            if cv2.waitKey(5) & 0xFF == ord("q"):
                break
            continue

        if not centered:
            pan, tilt = 90.0, 90.0
            send_servo(90, 90)
            last_pan_sent = last_tilt_sent = 90
            print("Connected — centered.")
            centered = True

        result = detector.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        if result.detections:
            det = max(result.detections, key=lambda d: d.score[0])
            bb  = det.location_data.relative_bounding_box

            fx = clamp(bb.xmin + bb.width  / 2, 0.0, 1.0)
            fy = clamp(bb.ymin + bb.height / 2, 0.0, 1.0)

            # Axes swapped in software to fix physical orientation
            err_x = (fy - 0.5) * 2.0
            err_y = (fx - 0.5) * 2.0

            d_x = err_x - pan_err_prev
            d_y = err_y - tilt_err_prev
            pan_err_prev  = err_x
            tilt_err_prev = err_y

            if abs(err_x) > DEAD_ZONE:
                pan  = clamp(pan  + PAN_DIR  * (PAN_KP  * err_x + PAN_KD  * d_x),
                             PAN_MIN,  PAN_MAX)
            else:
                pan_err_prev = 0.0

            if abs(err_y) > DEAD_ZONE:
                tilt = clamp(tilt + TILT_DIR * (TILT_KP * err_y + TILT_KD * d_y),
                             TILT_MIN, TILT_MAX)
            else:
                tilt_err_prev = 0.0

            p_int = round(pan)
            t_int = round(tilt)
            if p_int != last_pan_sent or t_int != last_tilt_sent:
                send_servo(p_int, t_int)
                last_pan_sent  = p_int
                last_tilt_sent = t_int

            px = int(fx * FRAME_W)
            py = int(fy * FRAME_H)
            x  = int(clamp(bb.xmin, 0, 1) * FRAME_W)
            y  = int(clamp(bb.ymin, 0, 1) * FRAME_H)
            w  = int(clamp(bb.width,  0, 1) * FRAME_W)
            h  = int(clamp(bb.height, 0, 1) * FRAME_H)
            cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 220, 0), 2)
            cv2.circle(frame, (px, py), 4, (0, 220, 0), -1)
            cv2.line(frame, (cx, cy), (px, py), (0, 180, 255), 1)
            cv2.putText(frame, f"pan:{pan:.1f} tilt:{tilt:.1f}",
                        (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 220, 0), 1)
            cv2.putText(frame, f"err x:{err_x:+.2f} y:{err_y:+.2f}",
                        (6, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 180, 255), 1)
        else:
            pan_err_prev  = 0.0
            tilt_err_prev = 0.0
            cv2.putText(frame, "no face", (6, 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 60, 220), 1)

        cv2.line(frame, (cx-14, cy), (cx+14, cy), (150, 150, 150), 1)
        cv2.line(frame, (cx, cy-14), (cx, cy+14), (150, 150, 150), 1)
        cv2.imshow("ESP32 Face Tracker", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    _stop.set()
    cam_sock.close()
    servo_sock.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()