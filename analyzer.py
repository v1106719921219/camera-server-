"""
カメラ映像取得 + YOLOv8人物検出 + 入退店カウント

STEP 1: RTSP映像取得（自動再接続・シグナルハンドリング）
STEP 2: YOLOv8nで人物検出
STEP 3: 仮想ライン通過による入退店カウント
"""

import os
import time
import signal
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import cv2
import numpy as np
from ultralytics import YOLO
from dotenv import load_dotenv

load_dotenv()

JST = timezone(timedelta(hours=9))

# --- 設定 ---
CAMERA_IP = os.getenv("CAMERA_IP", "192.168.11.62")
TAPO_USERNAME = os.getenv("TAPO_USERNAME", "")
TAPO_PASSWORD = os.getenv("TAPO_PASSWORD", "")
CAMERA_ID = os.getenv("CAMERA_ID", "tapo-c200-01")
DETECTION_INTERVAL = int(os.getenv("DETECTION_INTERVAL", "3"))  # N フレームに1回検出
COUNT_LINE_RATIO = float(os.getenv("COUNT_LINE_RATIO", "0.5"))  # カウントラインの位置
# COUNT_MODE: "horizontal"（上下移動を検出）または "vertical"（左右移動を検出）
COUNT_MODE = os.getenv("COUNT_MODE", "vertical")
# COUNT_REVERSE: "true" にすると入退店の方向を逆にする
COUNT_REVERSE = os.getenv("COUNT_REVERSE", "false").lower() == "true"

# ROI（検出エリア）設定 - 外の通路を除外するために検出エリアを絞る
# 例: ROI_X1=0.2, ROI_Y1=0.3, ROI_X2=0.8, ROI_Y2=0.9（画面の割合で指定）
# 0.0〜1.0で指定。デフォルトは画面全体（制限なし）
ROI_X1 = float(os.getenv("ROI_X1", "0.0"))
ROI_Y1 = float(os.getenv("ROI_Y1", "0.0"))
ROI_X2 = float(os.getenv("ROI_X2", "1.0"))
ROI_Y2 = float(os.getenv("ROI_Y2", "1.0"))

RTSP_URL = f"rtsp://{TAPO_USERNAME}:{TAPO_PASSWORD}@{CAMERA_IP}/stream1"
MAX_RECONNECT = 5
RECONNECT_INTERVAL = 5  # 秒

# 重複カウント防止: 同一トラックIDが再カウントされるまでの最小間隔（秒）
RECOUNT_COOLDOWN = 1.0


class PersonTracker:
    """シンプルなセントロイドベースのトラッカー"""

    def __init__(self, max_disappeared=30):
        self.next_id = 0
        self.objects = {}       # id -> (cx, cy)
        self.disappeared = {}   # id -> フレーム数
        self.max_disappeared = max_disappeared

    def update(self, detections):
        """
        detections: [(cx, cy), ...] のリスト
        戻り値: {id: (cx, cy), ...}
        """
        if len(detections) == 0:
            for obj_id in list(self.disappeared.keys()):
                self.disappeared[obj_id] += 1
                if self.disappeared[obj_id] > self.max_disappeared:
                    del self.objects[obj_id]
                    del self.disappeared[obj_id]
            return self.objects

        if len(self.objects) == 0:
            for det in detections:
                self._register(det)
            return self.objects

        object_ids = list(self.objects.keys())
        object_centroids = list(self.objects.values())

        # 距離行列を計算
        D = np.zeros((len(object_centroids), len(detections)))
        for i, oc in enumerate(object_centroids):
            for j, dc in enumerate(detections):
                D[i, j] = np.sqrt((oc[0] - dc[0]) ** 2 + (oc[1] - dc[1]) ** 2)

        # 最小距離でマッチング（簡易版）
        rows = D.min(axis=1).argsort()
        cols = D.argmin(axis=1)[rows]

        used_rows = set()
        used_cols = set()

        for row, col in zip(rows, cols):
            if row in used_rows or col in used_cols:
                continue
            if D[row, col] > 100:  # 距離が遠すぎる場合はマッチしない
                continue

            obj_id = object_ids[row]
            self.objects[obj_id] = detections[col]
            self.disappeared[obj_id] = 0
            used_rows.add(row)
            used_cols.add(col)

        unused_rows = set(range(len(object_centroids))) - used_rows
        unused_cols = set(range(len(detections))) - used_cols

        for row in unused_rows:
            obj_id = object_ids[row]
            self.disappeared[obj_id] += 1
            if self.disappeared[obj_id] > self.max_disappeared:
                del self.objects[obj_id]
                del self.disappeared[obj_id]

        for col in unused_cols:
            self._register(detections[col])

        return self.objects

    def _register(self, centroid):
        self.objects[self.next_id] = centroid
        self.disappeared[self.next_id] = 0
        self.next_id += 1


class VisitorAnalyzer:
    """カメラ映像からリアルタイムで来客カウントを行うメインクラス"""

    def __init__(self):
        self.running = False
        self.cap = None
        self.model = None
        self.tracker = PersonTracker(max_disappeared=30)

        # カウンター
        self.in_count = 0        # 累計入店数
        self.out_count = 0       # 累計退店数
        self.period_in = 0       # 保存期間中の入店数
        self.period_out = 0      # 保存期間中の退店数

        # トラッキング状態
        self.prev_positions = {}  # id -> 前回の座標(x or y)
        self.last_counted = {}    # id -> 最後にカウントした時刻

        # カメラ状態
        self.camera_connected = False
        self.start_time = None
        self.frame_count = 0
        self.current_detections = 0  # 現在検出中の人数

        # ライン位置（初回フレームで設定）
        self.line_y = None
        self.line_x = None
        self.frame_height = None
        self.frame_width = None

        # スレッドロック
        self.lock = threading.Lock()

        # シグナルハンドリング
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        print(f"\n[INFO] シグナル {signum} を受信。安全に終了します...")
        self.stop()

    def _connect_camera(self) -> bool:
        """カメラに接続（リトライあり）"""
        for attempt in range(1, MAX_RECONNECT + 1):
            print(f"[INFO] カメラに接続中... (試行 {attempt}/{MAX_RECONNECT})")
            self.cap = cv2.VideoCapture(RTSP_URL)

            if self.cap.isOpened():
                # バッファサイズを最小に（遅延軽減）
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                print(f"[OK] カメラ接続成功 ({CAMERA_IP})")
                self.camera_connected = True
                return True

            print(f"[WARN] 接続失敗。{RECONNECT_INTERVAL}秒後にリトライ...")
            time.sleep(RECONNECT_INTERVAL)

        print(f"[ERROR] カメラに接続できませんでした（{MAX_RECONNECT}回試行）")
        self.camera_connected = False
        return False

    def _load_model(self):
        """YOLOv8nモデルをロード"""
        print("[INFO] YOLOv8nモデルをロード中...")
        self.model = YOLO("yolov8n.pt")
        print("[OK] モデルロード完了")

    def _detect_persons(self, frame):
        """フレームから人物を検出し、中心座標のリストを返す（ROI制限あり）"""
        h, w = frame.shape[:2]

        # ROIのピクセル座標
        roi_x1 = int(w * ROI_X1)
        roi_y1 = int(h * ROI_Y1)
        roi_x2 = int(w * ROI_X2)
        roi_y2 = int(h * ROI_Y2)

        results = self.model(frame, classes=[0], verbose=False)  # class 0 = person

        centroids = []
        boxes = []

        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                conf = float(box.conf[0])
                if conf < 0.4:
                    continue

                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2

                # ROIの外にいる人は無視
                if not (roi_x1 <= cx <= roi_x2 and roi_y1 <= cy <= roi_y2):
                    continue

                centroids.append((cx, cy))
                boxes.append((x1, y1, x2, y2, conf))

        return centroids, boxes

    def _count_crossings(self, tracked_objects):
        """仮想ラインの通過を検出してカウント"""
        now = time.time()

        for obj_id, (cx, cy) in tracked_objects.items():
            # 縦ライン（左右移動）モード
            if COUNT_MODE == "vertical":
                pos = cx
                line = self.line_x
            else:
                # 横ライン（上下移動）モード
                pos = cy
                line = self.line_y

            if obj_id in self.prev_positions:
                prev_pos = self.prev_positions[obj_id]

                # 重複カウント防止
                if obj_id in self.last_counted:
                    if now - self.last_counted[obj_id] < RECOUNT_COOLDOWN:
                        self.prev_positions[obj_id] = pos
                        continue

                if COUNT_MODE == "vertical":
                    # 左→右（入店）
                    if prev_pos < line and pos >= line:
                        with self.lock:
                            self.in_count += 1
                            self.period_in += 1
                        self.last_counted[obj_id] = now
                        print(f"[COUNT] 入店 (+1) | 累計入店: {self.in_count} | 店内推定: {self.current_in_store}")
                    # 右→左（退店）
                    elif prev_pos > line and pos <= line:
                        with self.lock:
                            self.out_count += 1
                            self.period_out += 1
                        self.last_counted[obj_id] = now
                        print(f"[COUNT] 退店 (+1) | 累計退店: {self.out_count} | 店内推定: {self.current_in_store}")
                else:
                    # 上→下 or 下→上（COUNT_REVERSEで方向切り替え）
                    crossed_forward = prev_pos < line and pos >= line  # 上→下
                    crossed_backward = prev_pos > line and pos <= line  # 下→上

                    if COUNT_REVERSE:
                        is_enter = crossed_forward   # 上→下が入店
                        is_exit = crossed_backward   # 下→上が退店
                    else:
                        is_enter = crossed_backward  # 下→上が入店
                        is_exit = crossed_forward    # 上→下が退店

                    if is_enter:
                        with self.lock:
                            self.in_count += 1
                            self.period_in += 1
                        self.last_counted[obj_id] = now
                        print(f"[COUNT] 入店 (+1) | 累計入店: {self.in_count} | 店内推定: {self.current_in_store}")
                    elif is_exit:
                        with self.lock:
                            self.out_count += 1
                            self.period_out += 1
                        self.last_counted[obj_id] = now
                        print(f"[COUNT] 退店 (+1) | 累計退店: {self.out_count} | 店内推定: {self.current_in_store}")

            self.prev_positions[obj_id] = pos

        # 消えたトラッキングIDをクリーンアップ
        active_ids = set(tracked_objects.keys())
        for old_id in list(self.prev_positions.keys()):
            if old_id not in active_ids:
                del self.prev_positions[old_id]
                self.last_counted.pop(old_id, None)

    @property
    def current_in_store(self):
        """現在の店内推定人数"""
        return max(0, self.in_count - self.out_count)

    def get_status(self):
        """現在の状態を辞書で返す（API用）"""
        with self.lock:
            uptime = 0
            if self.start_time:
                uptime = int(time.time() - self.start_time)

            return {
                "camera_connected": self.camera_connected,
                "camera_id": CAMERA_ID,
                "uptime_seconds": uptime,
                "in_count": self.in_count,
                "out_count": self.out_count,
                "current_in_store": self.current_in_store,
                "current_detections": self.current_detections,
                "frame_count": self.frame_count,
            }

    def get_and_reset_period(self):
        """保存期間のカウントを取得してリセット（uploader用）"""
        with self.lock:
            data = {
                "in_count": self.period_in,
                "out_count": self.period_out,
                "current_in_store": self.current_in_store,
            }
            self.period_in = 0
            self.period_out = 0
            return data

    def start(self):
        """解析を開始（別スレッドで実行される想定）"""
        self.running = True
        self.start_time = time.time()

        self._load_model()

        if not self._connect_camera():
            self.running = False
            return

        print(f"[INFO] 解析開始 (検出間隔: {DETECTION_INTERVAL}フレームに1回)")
        print(f"[INFO] カウントライン位置: 画面高さの {COUNT_LINE_RATIO*100:.0f}%")
        print("[INFO] 終了するには Ctrl+C を押してください")

        frame_idx = 0

        while self.running:
            ret, frame = self.cap.read()

            if not ret:
                print("[WARN] フレーム取得失敗。再接続します...")
                self.camera_connected = False
                if self.cap:
                    self.cap.release()
                if not self._connect_camera():
                    print("[ERROR] 再接続失敗。解析を終了します。")
                    break
                continue

            # 初回フレームでライン位置を設定
            if self.line_y is None:
                self.frame_height, self.frame_width = frame.shape[:2]
                self.line_y = int(self.frame_height * COUNT_LINE_RATIO)
                self.line_x = int(self.frame_width * COUNT_LINE_RATIO)
                print(f"[INFO] フレームサイズ: {self.frame_width}x{self.frame_height}")
                print(f"[INFO] カウントモード: {COUNT_MODE}")
                if COUNT_MODE == "vertical":
                    print(f"[INFO] カウントライン X={self.line_x}")
                else:
                    print(f"[INFO] カウントライン Y={self.line_y}")

            frame_idx += 1
            self.frame_count = frame_idx

            # N フレームに1回検出
            if frame_idx % DETECTION_INTERVAL == 0:
                centroids, boxes = self._detect_persons(frame)
                tracked = self.tracker.update(centroids)
                self.current_detections = len(tracked)
                self._count_crossings(tracked)

                # ターミナル表示（検出フレームのみ）
                if frame_idx % (DETECTION_INTERVAL * 10) == 0:
                    status = self.get_status()
                    now_str = datetime.now(JST).strftime("%H:%M:%S")
                    print(
                        f"[{now_str}] "
                        f"検出: {status['current_detections']}人 | "
                        f"入店: {status['in_count']} | "
                        f"退店: {status['out_count']} | "
                        f"店内: {status['current_in_store']}人"
                    )

        self.stop()

    def stop(self):
        """解析を安全に停止"""
        self.running = False
        if self.cap and self.cap.isOpened():
            self.cap.release()
            print("[INFO] カメラ接続を切断しました")
        self.camera_connected = False
        print("[INFO] 解析を停止しました")


# スタンドアロン実行用
if __name__ == "__main__":
    analyzer = VisitorAnalyzer()
    try:
        analyzer.start()
    except KeyboardInterrupt:
        analyzer.stop()
