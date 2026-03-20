"""
.envファイルを正しく作成するスクリプト
実行方法: python make_env.py
"""

content = """SUPABASE_URL=https://icyzvxfoacsdrnysjodc.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImljeXp2eGZvYWNzZHJueXNqb2RjIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MzcyMjU0NSwiZXhwIjoyMDg5Mjk4NTQ1fQ.HsZECHlR7op439_wdAo9UeJYen3HnCF-Qmo1gCWHnt0
TAPO_USERNAME=kaitorisquare@gmail.com
TAPO_PASSWORD=Tessa123.0
CAMERA_IP=192.168.11.62
CAMERA_ID=tapo-c200-01
ROI_X2=1.0
COUNT_MODE=horizontal
COUNT_LINE_RATIO=0.3
COUNT_REVERSE=true
"""

with open(".env", "w", newline="\n") as f:
    f.write(content)

print(".envファイルを作成しました。")
