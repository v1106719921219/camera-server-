"""
Supabaseへの定期保存処理（REST API直接呼び出し版）

- 30秒ごとにvisitor_countsテーブルへ保存
- costco-resale-analytics（Next.js）と同じSupabaseプロジェクトを共有
- supabaseパッケージの代わりにhttpxでREST APIを直接呼び出す
"""

import os
import threading
import time
from datetime import datetime, timezone, timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()

JST = timezone(timedelta(hours=9))

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SAVE_INTERVAL = int(os.getenv("SAVE_INTERVAL", "30"))  # 秒
CAMERA_ID = os.getenv("CAMERA_ID", "tapo-c200-01")


class VisitorUploader:
    """visitor_countsテーブルへの定期保存"""

    def __init__(self, analyzer):
        self.analyzer = analyzer
        self.client: httpx.Client = None
        self.running = False
        self._thread = None

    def _get_headers(self):
        return {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _init_client(self):
        """HTTPクライアントを初期化"""
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            print("[WARN] Supabase環境変数が未設定です。保存は無効です。")
            return False

        try:
            self.client = httpx.Client(
                base_url=f"{SUPABASE_URL}/rest/v1",
                headers=self._get_headers(),
                timeout=10.0,
            )
            # 接続テスト
            res = self.client.get("/visitor_counts?select=id&limit=1")
            if res.status_code in (200, 206):
                print("[OK] Supabase接続成功")
                return True
            else:
                print(f"[WARN] Supabase接続テスト: status={res.status_code}")
                return True  # テーブルがまだなくても続行
        except Exception as e:
            print(f"[ERROR] Supabase接続失敗: {e}")
            return False

    def _save_loop(self):
        """定期保存ループ"""
        while self.running:
            time.sleep(SAVE_INTERVAL)
            if not self.running:
                break
            self._save()

    def _save(self):
        """現在のカウントをSupabaseに保存"""
        if not self.client:
            return

        period = self.analyzer.get_and_reset_period()
        now = datetime.now(JST).isoformat()

        record = {
            "counted_at": now,
            "in_count": period["in_count"],
            "out_count": period["out_count"],
            "current_in_store": period["current_in_store"],
            "camera_id": CAMERA_ID,
        }

        try:
            res = self.client.post("/visitor_counts", json=record)
            if res.status_code in (200, 201):
                print(
                    f"[SAVE] {now} | "
                    f"入店: {period['in_count']} | "
                    f"退店: {period['out_count']} | "
                    f"店内: {period['current_in_store']}人"
                )
            else:
                print(f"[ERROR] 保存失敗: {res.status_code} {res.text}")
        except Exception as e:
            print(f"[ERROR] 保存失敗: {e}")

    def get_today_counts(self):
        """本日の集計データを取得"""
        if not self.client:
            return []

        today_start = datetime.now(JST).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()

        try:
            res = self.client.get(
                "/visitor_counts",
                params={
                    "select": "*",
                    "counted_at": f"gte.{today_start}",
                    "camera_id": f"eq.{CAMERA_ID}",
                    "order": "counted_at.asc",
                },
            )
            if res.status_code == 200:
                return res.json()
            return []
        except Exception as e:
            print(f"[ERROR] データ取得失敗: {e}")
            return []

    def get_today_total(self):
        """本日の合計入店数を取得"""
        records = self.get_today_counts()
        total_in = sum(r.get("in_count", 0) for r in records)
        total_out = sum(r.get("out_count", 0) for r in records)
        return {
            "today_in": total_in,
            "today_out": total_out,
            "today_total": total_in,
        }

    def start(self):
        """定期保存を開始"""
        if not self._init_client():
            print("[WARN] Supabase保存なしで動作します")
            return

        self.running = True
        self._thread = threading.Thread(target=self._save_loop, daemon=True)
        self._thread.start()
        print(f"[INFO] Supabase保存開始 ({SAVE_INTERVAL}秒間隔)")

    def stop(self):
        """定期保存を停止（最終保存も実行）"""
        self.running = False
        if self.client:
            print("[INFO] 最終保存を実行...")
            self._save()
            self.client.close()
        print("[INFO] Supabase保存を停止しました")
