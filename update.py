"""
analyzer.py を最新版に更新するスクリプト
実行方法: python update.py
"""
import urllib.request
import os

files = ["analyzer.py", "uploader.py", "main.py", "requirements.txt"]
base_url = "https://raw.githubusercontent.com/v1106719921219/camera-server-/main/"

for f in files:
    print(f"更新中: {f} ...", end=" ")
    urllib.request.urlretrieve(base_url + f, f)
    print("完了")

print("\n全ファイルを最新版に更新しました。")
