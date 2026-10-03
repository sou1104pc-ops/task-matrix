# Kindle 出版 自動投稿 Bot

毎朝 AI（Mac mini の Claude Code）が実用書の Kindle 本を1冊（原稿・表紙・タイトル・紹介文・7つのキーワード・カテゴリー）作り、Discord に届けます。
Discord で **[承認して出版]** を押すと、Mac mini のブラウザが KDP（Kindle ダイレクト・パブリッシング）の画面を操作して本を登録し、#レポート に知らせます。

note アフィリエイト Bot（`note-affiliate/`）と同じ流れですが、**別の Bot・別の Discord サーバー**で動く独立したプログラムです。

```
毎朝7:00  テーマリストの次のテーマ → Claude Code で企画（タイトル・キーワード・紹介文・表紙・目次）
            → 章ごとに本文を書く（合計 約25,000字）
            → 表紙（1600×2560 の JPEG）と原稿（EPUB）を作る
            → 自動チェック（タイトルのキーワード・キーワード欄の禁止語・誇大表現・架空の体験・URL・紹介文の字数）
            ↓
Discord #下書き に届く　[承認して出版] [修正依頼] [表紙作り直し] [ボツ]
            ↓ 承認
Mac mini のブラウザ（Playwright）で KDP に登録 → #レポート に通知
```

> ⚠️ **KDP アカウント停止のおそれがあります**
> KDP には公式の登録APIがないため、このBotは KDP の画面を自動で操作します。Amazon は画面の自動操作を認めておらず、見つかると **KDP アカウントの停止・ロイヤリティの支払い停止** になる可能性があります。そのリスクを承知のうえで使う前提の作りです。
> 少しでも危険を減らすため、既定では **KDP の「下書き保存」で止めて**（`AUTO_PUBLISH=false`）、最後の「出版」はあなたが KDP の本棚で押す形にしています。1日に何冊も登録しないでください（毎朝1冊）。

## 本の作り方（Kindle 攻略 PDF の戦略を組み込んでいます）

本が読まれる公式 **PV × CTR × CVR**。直す順番は CTR → CVR → PV。

| 公式 | このBotがやること |
|---|---|
| **CTR（表紙・タイトル）** | 表紙は Bot が自作（外注しない＝何度でも無料で直せる）。検索キーワードを表紙の一番大きな文字に入れ、スマホの小さなサムネでも読める配置。気に入らなければ [表紙作り直し] |
| | タイトルの**一番前に検索キーワード**。売れている本のタイトルのキーワードに寄せる（コバンザメ戦略）。`/テーマ追加` の「売れ筋」に、同じテーマで売れている本のタイトルを入れると参考にします |
| **CVR（紹介文）** | 悩みへの呼びかけ → この本でわかること → **目次**（読みたくなる見出し） → 他の本にない点 → おすすめの人。「はじめに」も試し読みで読まれる前提で書く |
| | 他の本にない点として一番強いのは**あなたの体験**。`/テーマ追加` の「メモ」に書いた体験だけは本に使います（書いていない体験・実績・数字は作りません） |
| **PV（検索）** | **7つのキーワード欄を全部使う**（1つの欄にスペース区切りで関連語を複数）。タイトルの言葉はくり返さない |
| 出版直後 | KDP セレクト（読み放題）に登録。公開されたら**5日間の無料キャンペーン**を設定して SNS で告知（#レポート でお知らせします。キャンペーンの設定は KDP で手で行ってください） |

入れないもの：レビューのお願い・レビュー特典（KDP の規約違反）、キーワード欄の「Kindle Unlimited」「無料」「ベストセラー」・他の著者名や本のタイトル、出典のない数字、「必ず稼げる」のような表現。

KDP の「AI 生成コンテンツ」の質問には、**文章は AI（Claude）で作成、表紙の絵は AI 不使用**と正直に答えます（Amazon には表示されません）。

## Discord でできること

| 操作 | 内容 |
|---|---|
| [承認して出版] | KDP に登録（`AUTO_PUBLISH=false` なら下書き保存まで）。チェックで ❌ があると押せません |
| [修正依頼] | 直してほしい点を書くと、AI が企画を直し、必要な章だけ書き直して再提出 |
| [表紙作り直し] | 表紙の文字・色を作り直す（指示は空でもOK） |
| [ボツ] | その下書きを破棄 |
| `/生成` | 今すぐ1冊作る（テーマ・読者・メモ・売れ筋の指定も可） |
| `/テーマ追加` `/テーマ一覧` | 本のテーマの管理（メモ＝あなたの体験、売れ筋＝参考にする売れている本のタイトル。`\|` 区切りで複数） |
| `/日次レポート` | 今日の登録状況（毎日 `DAILY_REPORT_TIME` にも自動送信） |
| `/レポート` | 直近7日（毎週月曜9時にも自動送信） |
| `/停止` `/再開` | 毎朝の自動生成を止める／再開する |

下書きには、表紙の画像・表紙の原寸（JPEG）・`preview.html`（紹介文・キーワード・本文をスマホで確認）・`.epub`（Kindle アプリや「ブック」で開ける）が付きます。
1冊作るのに 15〜30分かかります（企画1回＋章の数だけ Claude Code を呼ぶため）。

売上（ロイヤリティ・既読ページ数）は KDP のレポート画面で見てください（自動では取りに行きません）。

## セットアップ（Mac mini で1回だけ）

> いちばん簡単なのは、Mac mini のターミナルで Claude Code を開いて
> 「task-matrix リポジトリの kindle-ops/README.md のセットアップをして」と頼む方法です。

### 1. Discord Bot を作る（note・Brain とは別）
1. https://discord.com/developers/applications →「New Application」（名前は例: Kindle秘書）
2. 左メニュー「Bot」→「Reset Token」でトークンをコピー（`.env` の `DISCORD_TOKEN`）
3. 左メニュー「OAuth2」→「URL Generator」
   - SCOPES: `bot` と `applications.commands`
   - BOT PERMISSIONS: `Send Messages` `Embed Links` `Attach Files` `Read Message History` `Use Slash Commands`
   - 下に出たURLを開いて、Kindle 用の新しいサーバーに追加
4. サーバーに `#下書き` と `#レポート` チャンネルを作り、それぞれ右クリック →「IDをコピー」
5. サーバー名を右クリック →「サーバーIDをコピー」

### 2. プログラムを入れる
```bash
cd task-matrix/kindle-ops
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
cp .env.example .env   # .env を開いて、トークン・ID・著者名（ペンネーム可）・フリガナ・ローマ字を書き込む
```

### 3. KDP にログインしておく（1回だけ）
```bash
.venv/bin/python -m src.kdp_client login   # ブラウザが開くので KDP にログイン（2段階認証も）→ ターミナルで Enter
```

### 4. 1冊作って、KDP の画面に入力できるか確かめる
```bash
.venv/bin/python -m src.bot                 # 起動して Discord で /生成 → 本 #1 が届く（Ctrl+C で止める）
.venv/bin/python -m src.kdp_client try 1    # 本 #1 を KDP に全部入力して「出版」の直前で止まる（ブラウザで目視確認）
```
**KDP の画面は、まだ実物では一度も試していません**（ログインしないと見られないため）。`try` で入らない欄があったら：
- `data/screenshots/` に失敗した画面のスクショと、その画面の入力欄の一覧（`fields-*.txt`）が残ります
- `.venv/bin/python -m src.kdp_client dump` で KDP を開き、各画面で Enter を押すと入力欄の一覧を保存できます
- それを見て `config/kdp_selectors.json` の候補を書き足します（Claude Code に「fields のファイルを見て kdp_selectors.json を直して」と頼めます）

### 5. 常駐させる
`launchd/com.kindle-ops.bot.plist` の `YOURNAME` を書き換えて：
```bash
cp launchd/com.kindle-ops.bot.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kindle-ops.bot.plist
```
ログは `data/bot.log`。

## 設定（.env）

| 項目 | 内容 |
|---|---|
| `AUTHOR_*` | KDP に登録する著者名（ペンネーム可）。日本語の本はフリガナ・ローマ字の欄もあります |
| `PRICE_JPY` | 価格（既定 500円）。70%ロイヤリティは 250〜1,250円 |
| `KDP_SELECT` | KDP セレクト（読み放題）に登録する（既定 true）。登録中の本は他のストアで売れません |
| `KDP_DRM` | DRM（コピー防止）をかける（既定 true）。**出版後は変更できません** |
| `BOOK_CHARS` | 本文の目安の文字数（既定 25,000字） |
| `KEYWORD_MAX_CHARS` | キーワード欄1つの最大文字数（既定 50。KDP の欄に上限があればそちらに合わせて自動で切ります） |
| `AUTO_PUBLISH` | true で「出版」まで押す。false（既定）は KDP の下書き保存で止める。`try` で問題なく入るのを何冊か確かめてから true に |

テーマの初期リストは `config/themes.csv`（起動時に読み込み）。
