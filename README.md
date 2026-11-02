# ball-drop-prototype

視聴者のコメントでボールが落ちる、プログラム生成の YouTube Live 配信の試作。

## 状況（2026-10-31）
- GCP 時間貸しデモで本番配信に成功：e2-standard-2（東京）上で
  720p/30fps を維持し、ユーザーのチャンネル「りつねぐ」で実際に
  ボールが落ちる映像を配信・確認した（VM は同日削除済み）。
- 現在はボールが 0.7 秒間隔で自動で落ちるデモモード。
  次のステップがコメント連動（下記）。

## 構成
- `ball_drop.py` — 最初の試作（物理なし）
- `ball_drop_obstacles.py` — 障害物（ガルトンボード風ピン）版
- `ball_drop_stack.py` — Pymunk 物理版。ボール同士が衝突して積み上がる
- `gcp-demo/stream_live.py` — 配信用本体。Pymunk＋PIL描画＋ffmpeg で
  RTMP へ実時間配信。100 個で一斉クリア。環境変数 `RTMP_URL` で
  YouTube Live、`OUTPUT` でファイル出力（性能テスト用）
- `gcp-demo/RUNBOOK.md` — GCP デモの手順書と実施記録・学び
- `gcp-demo/startup.sh` / `gcp-demo/run_live.sh` — VM 用スクリプト

※ デモ動画（*.mp4）は Git 管理外（.gitignore 参照）。

## 動かし方（ローカル）
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python ball_drop_stack.py   # ball_drop_stack.mp4 を生成
```

## コメント連動（実チャット検証済み 2026-11-01）
- `gcp-demo/chat_triggers.py`：トリガー語（「ボール」/「ball」＋
  任意で 🔴🔵🟡・赤青黄）を検出して `stream_live.py` の
  `spawn()` を呼ぶ。同一ユーザー30秒クールダウン、キュー上限20
- 受信方式は2つ：gRPC の `liveChatMessages.streamList`
  （`CHAT_MODE=youtube`、低遅延）と REST ポーリング
  （`CHAT_MODE=youtube-poll`）。2026-11-01 にポーリング方式で
  実チャット検証成功。ストリーム形式は実装済みで、実地検証は次回
- 協力型ゲーム：画面上に「みんなで n / 目標」の進捗バーを表示し、
  目標到達で金色ボール＋ミニボールのシャワーと
  「最後の一押し：○○」バナーを出す。ラウンドごとに目標+25
- 住人の白いアヒル：盤面の床をうろうろし、ボール落下で跳ね、
  ゴールで喜び、新規登録者を吹き出しで迎える（PIL描画）
- 新規登録お祝い（`gcp-demo/subscriber_watch.py`、SUBS_MODE）：
  登録者数と公開登録者を監視し、新規に金色ボール＋
  「ようこそ！○○（およそN番前後の仲間）」バナー。番号は丸めた
  概数で、正しさを装わない。同じ人のお祝いは一生1回まで
- `CHAT_MODE=mock` でモックコメントによるオフライン検証が可能
- OAuth 認可は初回のみ（手順は `gcp-demo/RUNBOOK.md`）
- 依存は `requirements-chat.txt`（本体とは分離）
- 配信先は GCP 時間貸し継続か、OCI Always Free（A1）確保後の
  常駐かを検討中
