# Camera Server - コストコ再販店 来客カウントシステム

TP-Link Tapo C200カメラ + YOLOv8 による来客カウントサーバー。
`costco-resale-analytics`（Next.js + Supabase）と統合前提。

## セットアップ

### 1. Python環境（3.10以上推奨）

```bash
cd camera-server
python -m venv .venv

# Windows
.venv\Scripts\activate

# Mac/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. 環境変数

```bash
cp .env.example .env
```

`.env` を編集：
```
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=xxxx
TAPO_USERNAME=（Tapoアプリで設定したカメラアカウント名）
TAPO_PASSWORD=（Tapoアプリで設定したカメラアカウントのパスワード）
```

### 3. Supabaseテーブル作成

Supabase SQL Editorで実行：

```sql
create table visitor_counts (
  id uuid primary key default gen_random_uuid(),
  counted_at timestamptz not null,
  in_count integer not null default 0,
  out_count integer not null default 0,
  current_in_store integer not null default 0,
  camera_id text default 'tapo-c200-01',
  created_at timestamptz default now()
);

-- インデックス（日付検索の高速化）
create index idx_visitor_counts_counted_at on visitor_counts (counted_at);
create index idx_visitor_counts_camera_id on visitor_counts (camera_id);
```

### 4. 起動

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

ブラウザで確認：
- http://localhost:8000/health
- http://localhost:8000/count/now
- http://localhost:8000/count/today
- http://localhost:8000/docs （Swagger UI）

## API仕様

### GET /health
```json
{
  "status": "ok",
  "camera": "connected",
  "camera_id": "tapo-c200-01",
  "uptime": "5分",
  "uptime_seconds": 300
}
```

### GET /count/now
```json
{
  "current_in_store": 3,
  "current_detections": 2,
  "today_total": 47,
  "in_count": 50,
  "out_count": 47,
  "updated_at": "2026-03-16T14:30:00+09:00"
}
```

### GET /count/today
```json
{
  "date": "2026-03-16",
  "today_total": 47,
  "data": [
    {"time": "09:00", "in_count": 5, "out_count": 3},
    {"time": "09:30", "in_count": 8, "out_count": 4},
    {"time": "10:00", "in_count": 12, "out_count": 7}
  ]
}
```

## costco-resale-analytics との統合

Next.js側の環境変数に追加：
```
CAMERA_SERVER_URL=http://localhost:8000
```

呼び出し例：
```typescript
const res = await fetch(`${process.env.CAMERA_SERVER_URL}/count/now`);
const data = await res.json();
// data.current_in_store → 現在の店内人数
// data.today_total → 本日の来客数
```

Supabaseの `visitor_counts` テーブルには Next.js側から直接クエリも可能。

## 設定の調整

`.env` で調整可能：

| 変数 | デフォルト | 説明 |
|------|-----------|------|
| `DETECTION_INTERVAL` | 3 | N フレームに1回検出（大きくすると軽くなる） |
| `SAVE_INTERVAL` | 30 | Supabase保存間隔（秒） |
| `COUNT_LINE_RATIO` | 0.5 | カウントラインの位置（0.0=上端、1.0=下端） |

## トラブルシューティング

### カメラに接続できない
- Tapoアプリでカメラが映ることを確認
- カメラとPCが同じWi-Fiネットワークにいるか確認
- `ping 192.168.11.62` で疎通確認
- Tapoアプリ → 高度な設定 → カメラアカウントが設定済みか確認

### 検出精度が低い
- `DETECTION_INTERVAL` を小さくする（CPU負荷は上がる）
- カメラの設置角度を調整（真上より斜め上のほうが人物を検出しやすい）
- `COUNT_LINE_RATIO` を設置場所に合わせて調整

### CPU負荷が高い
- `DETECTION_INTERVAL` を大きくする（5〜10）
- RTSP URLを `/stream2`（低解像度）に変更

### カウントがずれる
- カメラの設置位置と `COUNT_LINE_RATIO` を調整
- 出入口以外の動線が映り込んでいないか確認
