# Threads 予約投稿 Bot（複数アカウント・手動承認）

毎晩 AI（Mac mini の Claude Code）が、全アカウントの**翌日分**の投稿案を作って Discord に届けます。
Discord で内容を確認・修正して **[承認]** したものだけが、予約時刻に Threads へ投稿されます。

```
毎日20:00  アカウントごとに翌日の投稿枠ぶん生成（価値提供 / LINE・メルマガ誘導）
             → 自動チェック（500字・URL・誇大表現・他アカウントとの文面の類似）
               ↓
Discord #下書き　[承認] [修正依頼] [直接編集] [時刻変更] [ボツ]
               ↓ 承認
予約時刻（＋0〜6分のゆらぎ）に Threads へ投稿。ツリー（連投）はリプライでつなげる
```

## Discord でできること

| 操作 | 内容 |
|---|---|
| [承認] | 予約確定。予定時刻を過ぎていたら1分以内に投稿（チェックで ❌ があると押せません） |
| [修正依頼] | 「1行目を強く」「短く」など指示を書くと、AIが書き直してメッセージを差し替え |
| [直接編集] | 本文を自分で書き換え。空欄にした投稿はツリーから外れ、空いた欄に書くと投稿を追加 |
| [時刻変更] | `21:00` / `9/30 21:00` / `明日 7:30` の形で入力 |
| [ボツ] | 破棄 |
| [承認を取り消す] / [予約取消] | 承認後、投稿前なら編集に戻す／取り消す |
| [再試行] | 投稿に3回失敗して止まったものをもう一度（ツリーの途中まで出ていれば続きから） |
| `/生成` | 今すぐ下書きを作る（アカウント・今日/明日・ネタを指定可） |
| `/予約` | 自分で書いた投稿を予約（チェックに通れば承認済みで登録） |
| `/予約一覧` | 承認待ち・予約済み・失敗の一覧 |
| `/アカウント` | 接続状況・トークン期限・予約数 |
| `/停止` `/再開` | 毎晩の自動生成を止める／再開する（予約済みの投稿は止まりません） |

投稿が済むと、下書きのメッセージが「投稿済み」になり、投稿URLが付きます。
失敗やトークン切れは `#通知`（`REPORT_CHANNEL_ID`）に届きます。

## アカウントの設定（config/accounts.json）

`config/accounts.example.json` をコピーして `config/accounts.json` を作り、アカウントごとに書きます（このファイルは git に入りません）。

| 項目 | 内容 |
|---|---|
| `id` | 英数字の識別子（例: `acc01`）。接続やコマンドで使う |
| `name` | Discord に表示する名前 |
| `theme` `target` `tone` | 発信テーマ・ターゲット・口調。AIの書き分けに使う |
| `product` `cta_text` | 最終的に届ける商品と、誘導の方向性 |
| `cta_url` | UTAGEの登録経路（トラッキング）URL。**本文に入れてよいURLはこれだけ**（他のURLはチェックでエラー） |
| `topics` | ネタの候補 |
| `rules` | 避けたいこと・守ること |
| `slots` | 1日の投稿枠。`type` は `value`（価値提供）か `cta`（誘導） |
| `channel_id` | （任意）このアカウントの下書きだけ別チャンネルに出したいとき |
| `enabled` | `false` にすると自動生成の対象から外れる |

書き換えたら Bot を再起動してください（`/生成` などのアカウント選択肢に反映されます）。

## セットアップ（Mac mini で1回だけ）

### 1. Meta（Threads API）のアプリを作る — 全アカウント共通で1つ
1. https://developers.facebook.com/apps →「アプリを作成」→ ユースケースで **「Threads APIにアクセス」** を選ぶ
2. ユースケースの「カスタマイズ」→ 権限に `threads_basic` `threads_content_publish` `threads_manage_insights` `threads_read_replies` `threads_manage_replies` を追加
   （分析用の権限も先に付けておくと、あとで再接続せずに済みます）
3. ユースケースの設定画面で **Threads App ID / Threads App Secret** を控える（`.env` の `THREADS_APP_ID` `THREADS_APP_SECRET`）
4. 「アプリの役割」→「ロール」→ **Threadsテスター** に、運用する10アカウントを追加
5. 各アカウントで Threads（Web またはアプリ）→ 設定 → アカウント → **ウェブサイトのアクセス許可** → 招待 を承認

自分のアカウントだけで使うならアプリ審査は不要です。

### 2. Discord Bot を作る — noteの Bot とは別サーバー・別 Bot
1. https://discord.com/developers/applications →「New Application」（例: Threads秘書）
2. 「Bot」→「Reset Token」→ `.env` の `DISCORD_TOKEN`
3. 「OAuth2」→「URL Generator」→ SCOPES: `bot` `applications.commands` / 権限: `Send Messages` `Embed Links` `Read Message History` `Use Slash Commands` → URLを開いてサーバーに追加
4. サーバーに `#下書き` と `#通知` を作り、IDを `DRAFT_CHANNEL_ID` / `REPORT_CHANNEL_ID` に、サーバーIDを `DISCORD_GUILD_ID` に書く

### 3. プログラムを入れる
```bash
cd task-matrix/threads-ops
/opt/homebrew/bin/python3.13 -m venv .venv      # システムの python3（3.9）は使わない
.venv/bin/pip install -r requirements.txt
cp .env.example .env                              # 1. 2. で控えた値を書く
cp config/accounts.example.json config/accounts.json   # アカウントを書く
```

### 4. アカウントを接続する（アカウントごとに1回）
いちばん簡単なのは、Metaのアプリ画面の **「ユーザートークン生成ツール」** でテスターごとにトークンを出して登録する方法です。
```bash
.venv/bin/python -m src.accounts token acc01 <出てきたトークン>   # 60日の長期トークンにして保存
.venv/bin/python -m src.accounts check acc01                      # 使えるか確認（投稿はしない）
.venv/bin/python -m src.accounts list                             # 全アカウントの接続状況
```
（リダイレクトURLを設定している場合は `python -m src.accounts connect acc01` でブラウザから許可する方法も使えます）

トークンは60日で切れますが、Bot が毎朝4時に期限20日前のものを自動で延長します。延長に失敗したら `#通知` に届くので、そのアカウントだけ上の手順で登録し直してください。

### 5. 起動
```bash
.venv/bin/python -m src.bot
```
最初は1アカウントだけ `enabled: true` にして `/生成 今日` → [承認] → 投稿される、まで確認してから残りを増やすのがおすすめです。

## 常駐させる（Mac mini の再起動後も自動で起動）
```bash
sed "s#/Users/YOURNAME#$HOME#g" launchd/com.threads-ops.bot.plist > ~/Library/LaunchAgents/com.threads-ops.bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.threads-ops.bot.plist
```
ログは `data/bot.log`。再起動は `launchctl kickstart -k gui/$(id -u)/com.threads-ops.bot`。

## 運用上の注意
- 同じ運営の別アカウントとの文面の類似を毎回チェックします（60%以上で ⚠️、85%以上で ❌）
- 投稿の上限は Threads 側で1アカウント24時間250件です
- 投稿中に Bot が止まった下書きは、二重投稿を避けるため自動では再開しません。Threads を確認してから [再試行] してください

## これから追加するもの
- 投稿ごと・アカウントごとの分析（閲覧・いいね・返信・フォロワー増）と日報・週報
- UTAGE の登録経路ごとの LINE・メルマガ登録数、売上との突き合わせ
- #秘書 チャンネルでの会話
