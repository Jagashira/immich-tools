# immich-tools

Immich 向けの補助スクリプト集です。  
現在は、ローカルの写真・動画フォルダを対象に、Immich 側に同じ asset がすでに存在するかを確認しながら、安全にアップロード確認を行う Python スクリプトを含みます。

## 目的

Immich の Web アップロードで次のような状況が起きたときに、状態確認をしやすくするためのツールです。

- アップロードに失敗したように見える
- `Duplicated` と出るが、自分では入れた覚えがない
- 実際に Immich 側に asset が存在しているか確認したい
- ローカルフォルダ全体について、どのファイルが未登録かを見たい

## 含まれるスクリプト

### `scripts/immich_verify_tool.py`

ローカルフォルダ内のメディアファイルを走査し、各ファイルについて次を行います。

1. ファイルの SHA-1 を計算
2. Immich の `/api/search/metadata` を使って checksum 検索
3. すでに asset があれば `already_exists` として記録
4. なければアップロードを試行
5. 結果を JSON レポートとして保存

## 前提

- Python 3.10 以上推奨
- Immich の API key が必要
- Immich サーバー URL が必要
- `.venv` の利用を想定

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests
```

## 使い方

### 基本

```bash
python scripts/immich_verify_tool.py "/Users/yourname/Downloads/iCloud Photos" \
  --url "http://YOUR_IMMICH_HOST:2283" \
  --api-key "YOUR_API_KEY"
```

### dry-run で確認だけ行う

```bash
python scripts/immich_verify_tool.py "/Users/yourname/Downloads/iCloud Photos" \
  --url "http://YOUR_IMMICH_HOST:2283" \
  --api-key "YOUR_API_KEY" \
  --dry-run
```

### 拡張子を限定する

```bash
python scripts/immich_verify_tool.py "/Users/yourname/Downloads/iCloud Photos" \
  --url "http://YOUR_IMMICH_HOST:2283" \
  --api-key "YOUR_API_KEY" \
  --only .heic .mov
```

### 各送信の間隔を空ける

```bash
python scripts/immich_verify_tool.py "/Users/yourname/Downloads/iCloud Photos" \
  --url "http://YOUR_IMMICH_HOST:2283" \
  --api-key "YOUR_API_KEY" \
  --sleep 0.5
```

## 出力ステータス

各ファイルごとに次のいずれかの状態になります。

- `already_exists`  
  Immich 側の metadata search で同じ checksum の asset が見つかった状態です。  
  `detail` には asset id などが入ります。

- `uploaded`  
  新規アップロードに成功した状態です。

- `duplicate`  
  事前検索では見つからなかったが、アップロード時にサーバー側で重複と判定された状態です。

- `would_upload`  
  `--dry-run` 時に、未登録なのでアップロード対象になることを示します。

- `failed`  
  サーバー応答は返ったが、正常終了ではなかった状態です。

- `request_error`  
  通信エラーやタイムアウトなど、HTTP リクエスト自体に失敗した状態です。

## レポート

実行後、カレントディレクトリに JSON レポートが出力されます。

例:

- `immich_python_report_20260419_220815.json`

レポートには次が含まれます。

- 対象フォルダ
- API URL
- dry-run かどうか
- ステータスごとの件数
- 各ファイルの詳細結果
  - path
  - status
  - http_status
  - detail
  - bytes
  - sha1
  - elapsed_sec

## 実装方針

このツールでは、まずローカルファイルの SHA-1 を計算し、その値を base64 化して Immich の metadata search API に渡します。  
これにより、Immich 側にすでに同一 checksum の asset が存在するかを事前に確認できます。

そのため、Web UI 上で `Duplicated` と見えた場合でも、

- 実際にすでに asset が存在していたのか
- それとも今回初めて重複判定されたのか

を切り分けやすくなります。

## 注意

- Immich 側の内部重複判定と、このツールの事前検索結果が完全に一致しない場合があります
- Web UI での表示と、実際の asset 登録状態がずれることがあります
- Safari などブラウザ経由アップロードが不安定な場合でも、このスクリプトで状態確認しやすくなります
- API 仕様変更により将来的に動作しなくなる可能性があります

## 今後の改善案

- asset id を使った詳細照会
- 未登録ファイルのみを別リストに書き出す機能
- レポートの CSV 出力
- retry 機能
- 同名・同サイズ・撮影日時ベースの補助チェック

## ライセンス

必要に応じて追加してください。
