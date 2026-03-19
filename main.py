"""
FastAPI サーバー

カメラ解析の状態確認・データ取得用API
costco-resale-analytics（Next.js）から呼び出される

エンドポイント:
  GET /health     → サーバー状態
  GET /count/now  → リアルタイム人数
  GET /count/today → 本日の集計データ
"""

import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from analyzer import VisitorAnalyzer
from uploader import VisitorUploader

JST = timezone(timedelta(hours=9))

analyzer = VisitorAnalyzer()
uploader = VisitorUploader(analyzer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """サーバー起動時に解析・保存を開始、終了時に停止"""
    # 起動
    uploader.start()
    analyzer_thread = threading.Thread(target=analyzer.start, daemon=True)
    analyzer_thread.start()
    print("[INFO] カメラサーバー起動完了")

    yield

    # 終了
    analyzer.stop()
    uploader.stop()
    print("[INFO] カメラサーバー終了")


app = FastAPI(
    title="Camera Server - コストコ再販店 来客カウント",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS設定（Next.jsから呼び出すため）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """サーバー状態を返す"""
    status = analyzer.get_status()
    uptime_min = status["uptime_seconds"] // 60

    return {
        "status": "ok",
        "camera": "connected" if status["camera_connected"] else "disconnected",
        "camera_id": status["camera_id"],
        "uptime": f"{uptime_min}分",
        "uptime_seconds": status["uptime_seconds"],
    }


@app.get("/count/now")
def count_now():
    """リアルタイムの来客状況を返す"""
    status = analyzer.get_status()
    today = uploader.get_today_total()

    return {
        "current_in_store": status["current_in_store"],
        "current_detections": status["current_detections"],
        "today_total": today["today_total"],
        "in_count": status["in_count"],
        "out_count": status["out_count"],
        "updated_at": datetime.now(JST).isoformat(),
    }


@app.get("/count/today")
def count_today():
    """本日の時系列データを返す"""
    records = uploader.get_today_counts()

    # 30分ごとに集計
    buckets = {}
    for r in records:
        counted_at = r.get("counted_at", "")
        if not counted_at:
            continue
        # 時刻の30分バケットに丸める
        try:
            dt = datetime.fromisoformat(counted_at)
            bucket_key = dt.replace(minute=(dt.minute // 30) * 30, second=0, microsecond=0)
            key = bucket_key.strftime("%H:%M")
        except (ValueError, TypeError):
            continue

        if key not in buckets:
            buckets[key] = {"time": key, "in_count": 0, "out_count": 0}

        buckets[key]["in_count"] += r.get("in_count", 0)
        buckets[key]["out_count"] += r.get("out_count", 0)

    # 時刻順にソート
    result = sorted(buckets.values(), key=lambda x: x["time"])

    today = uploader.get_today_total()
    return {
        "date": datetime.now(JST).strftime("%Y-%m-%d"),
        "today_total": today["today_total"],
        "data": result,
    }
