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
| `/x参考` | Xで伸びている投稿の本文を貼ると、**型だけ借りて**Threads用の下書きを1つ作る（下の「Xの投稿を参考に作る」） |
| `/予約` | 自分で書いた投稿を予約（チェックに通れば承認済みで登録） |
| `/予約一覧` | 承認待ち・予約済み・失敗の一覧 |
| `/アカウント` | 接続状況・トークン期限・予約数 |
| `/接続` | Threadsのトークンを登録（スマホからでも可。入力はチャンネルに残りません。サーバー管理権限のある人だけ使えます） |
| `/秘書リセット` | 秘書との会話の記憶を消す |
| `/停止` `/再開` | 毎晩の自動生成を止める／再開する（予約済みの投稿は止まりません） |

## Xの投稿を参考に作る（`/x参考`）

Threadsの伸びている投稿を真似すると、言い回しを変えても「パクリ」と見られやすいので、参考はXから持ってきます。

1. `/x参考 アカウント [種類] [日時]` を実行（種類を省くと価値提供、日時を省くとその種類の次の投稿枠）
2. 出てきた画面に **Xの投稿の本文** を貼る（URLだけでは中身を読めません）。補足に「1行目の引きだけ真似したい」などを書いてもOK
3. AIが「なぜ伸びたか」の骨組み（引きの種類・話の順番・締め方）だけを借りて、ネタはアカウントのテーマに置き換えて書きます
4. `#下書き` に「X参考」付きで届くので、いつも通り確認して [承認]

下書きには参考にした投稿も表示されます。自動チェックは参考の文面が残っていないかも見ます
（同じ文字の並びが25字以上続いたら ❌、15字以上か類似度45%以上で ⚠️）。[修正依頼] で書き直すときも、参考の文面は使わないよう指示されます。
`#秘書` に投稿を貼って「これをもとにアカウント1の投稿を作って」と頼んでも同じことができます。

X参考の下書きは毎晩の自動生成とは別枠なので、同じ日に入れても毎晩の生成は止まりません。

## 秘書と会話する（#秘書 チャンネル）

`#秘書` に普通に話しかけると、Claude Code（`claude -p`）が考えて答えます。追加のAPIキーは要りません。

```
あなた: 今の予約状況どうなってる？
秘書  : 承認待ち1件、予約済み0件です。acc01〜acc10はすべて自動生成がオフなので、今夜は下書きが作られません…

あなた: アカウント1のテーマを「30代会社員の副業」にして。投稿は朝7時半と夜9時、夜は誘導ね
秘書  : acc01の運用方針を変更しました。テーマ:（空）→「30代会社員の副業」、投稿枠: 7:30 価値提供 / 21:00 誘導 …
```

秘書ができるのは `src/secretary.py` の `TOOLS` に書いた操作だけです（シェル権限・トークンは渡していません）。

| 種類 | ツール |
|---|---|
| 調べる | `list_accounts` `get_status` `list_queue` `get_draft` `list_posted` |
| 手元の設定・下書きを変える | `update_account`（運用方針の変更）`edit_draft` `reschedule` `reject_draft` `set_paused` |
| **生成・予約・投稿につながる** | `revise_draft` `generate_drafts` `generate_from_x` `approve_draft` `cancel_scheduled` `schedule_post` |

「生成・予約・投稿につながる」操作は、はっきり頼んだときだけ実行します（「投稿して大丈夫？」のような質問では承認しません）。
`/秘書リセット` で会話の記憶を消せます。

使うための設定：
1. Discord Developer Portal → Bot → **Privileged Gateway Intents** → **Message Content Intent** を ON
2. `#秘書` チャンネルを作り、IDを `.env` の `SECRETARY_CHANNEL_ID` に書く（空なら秘書は無効で、Botはメッセージを読みません）

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
| `cta_method` | 誘導方法。`pinned`＝固定投稿へ誘導（本文にURLを入れない。UTAGEのリンクは固定投稿の返信欄に置く）/ `url`＝最後の投稿に `cta_url` を入れる |
| `cta_url` | `cta_method: url` のときのUTAGEの登録経路（トラッキング）URL。**本文に入れてよいURLはこれだけ**（他のURLはチェックでエラー） |
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
Metaのアプリ画面の **「ユーザートークン生成ツール」** でテスターごとにトークンを出し、登録します。
トークンの発行はどの端末のブラウザでもでき、登録は **Discord の `/接続`**（Bot 起動後）でもターミナルでもできます。
```bash
.venv/bin/python -m src.accounts token acc01 <出てきたトークン>   # 60日の長期トークンにして保存
.venv/bin/python -m src.accounts check acc01                      # 使えるか確認（投稿はしない）
.venv/bin/python -m src.accounts list                             # 全アカウントの接続状況
```
（リダイレクトURLを設定している場合は `python -m src.accounts connect acc01` でブラウザから許可する方法も使えます）

トークンは60日で切れますが、Bot が毎朝4時に期限20日前のものを自動で延長します。延長に失敗したら `#通知` に届くので、そのアカウントだけ `/接続` で登録し直してください。

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
