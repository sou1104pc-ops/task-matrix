# note アフィリエイト自動投稿 Bot

毎朝 AI（Mac mini の Claude Code）が転職系の note 記事の下書きを作り、Discord に届けます。
Discord で **[承認して投稿]** を押すと note に投稿され、「今日はこういう投稿をしました」と通知されます。
毎日夜には、その日の閲覧数（PV）・投稿・成約をまとめたデイリーレポートが #レポート に届きます。

```
毎朝7:00  テーマ選定 → Claude Code で記事生成 → 自動チェック（PR表記・誇大表現・架空の体験談・リンク）
            ↓
Discord #下書き に届く　[承認して投稿] [修正依頼] [ボツ]
            ↓ 承認
Mac mini のブラウザ（Playwright）で note に投稿 → #レポート に通知
```

## Discord でできること

| 操作 | 内容 |
|---|---|
| [承認して投稿] ボタン | note に投稿（チェックで ❌ があると押せません） |
| [修正依頼] ボタン | 直してほしい点を書くと、AI が書き直して再提出 |
| [ボツ] ボタン | その下書きを破棄 |
| `/生成` | 今すぐ1本作る（テーマ指定も可） |
| `/テーマ追加` `/テーマ一覧` | 記事テーマの管理 |
| `/成約` | A8 の成約を記録（どのテーマが売れるかの分析に使う） |
| `/日次レポート` | 今日のPV・投稿・成約（毎日 `DAILY_REPORT_TIME` にも自動送信） |
| `/レポート` | 直近7日の投稿数・成約・スキ（毎週月曜9時にも自動送信） |
| `/停止` `/再開` | 毎朝の自動生成を止める／再開する |
| `/既存記事修正` | 既存9記事の修正案を1記事ずつ表示 → [この内容でnoteを更新] |
| `/秘書リセット` | 秘書との会話の記憶を消す |

## 秘書と会話する（#秘書 チャンネル）

`#秘書` チャンネルに普通に話しかけると答えます。記事生成と同じ Claude Code（`claude -p`）が頭脳なので、追加のAPIキーは要りません。

```
あなた: 今日のPVどう？
秘書  : 今日（9/27）のPVは0、スキも0です。ただしnote側の15:57時点の集計なので、
        それ以降の閲覧は入っていません。直近7日の推移も見ますか？

あなた: 「SAP FIコンサルの年収実態」ってテーマ足しといて。案件はSAP転職のやつで
秘書  : 「SAP FIコンサルの年収実態」を追加しました（案件: sap_tenshoku）。
```

秘書ができるのは `src/secretary.py` の `TOOLS` に書いた操作だけです（シェル権限は渡していません）。

| 種類 | ツール |
|---|---|
| 調べる | `get_stats` `list_themes` `list_recent_posts` `list_conversions` `get_status` `list_drafts` `list_existing_fixes` |
| 手元のデータを変える | `add_theme` `record_conversion` `set_paused` |
| **noteに反映される** | `generate_draft`（下書きを作って #下書き に出す）`publish_draft`（noteに投稿）`apply_existing_fix`（既存記事を更新） |

`publish_draft` は**公開前チェックでエラーが出ている下書きを投稿できません**（[承認して投稿] ボタンと同じルール）。秘書が投稿すると `#レポート` に報告が出ます。

### 使うための設定
1. Discord Developer Portal → 対象のBot → **Bot** → **Privileged Gateway Intents** → **MESSAGE CONTENT INTENT** を ON（メッセージ本文を読むのに必要）
2. `#秘書` チャンネルを作り、IDを `.env` の `SECRETARY_CHANNEL_ID` に書く

`SECRETARY_CHANNEL_ID` が空なら会話機能は無効になり、Bot はメッセージを一切読みません。

## セットアップ（Mac mini で1回だけ）

> いちばん簡単なのは、Mac mini のターミナルで Claude Code を開いて
> 「task-matrix リポジトリの note-affiliate/README.md のセットアップをして」と頼む方法です。
> 以下は手動で行う場合の手順です。

### 1. Discord Bot を作る
1. https://discord.com/developers/applications →「New Application」（名前は例: note秘書）
2. 左メニュー「Bot」→「Reset Token」でトークンをコピー（`.env` の `DISCORD_TOKEN`）
3. 左メニュー「OAuth2」→「URL Generator」
   - SCOPES: `bot` と `applications.commands`
   - BOT PERMISSIONS: `Send Messages` `Embed Links` `Attach Files` `Read Message History` `Use Slash Commands`
   - 下に出たURLを開いて、自分のサーバーに追加
4. Discord の設定 →「詳細設定」→「開発者モード」をON
5. サーバーに `#下書き` と `#レポート` チャンネルを作り、それぞれ右クリック →「IDをコピー」
6. サーバー名を右クリック →「サーバーIDをコピー」

### 2. プログラムを入れる
```bash
git clone https://github.com/sou1104pc-ops/task-matrix.git
cd task-matrix/note-affiliate
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # .env を開いて、1. で控えたIDとトークンを書き込む
```

### 3. note にログインしておく（1回だけ）
```bash
python -m src.note_client login   # ブラウザが開くので note にログイン → ターミナルで Enter
python -m src.note_client test    # テスト用の下書きが note に保存されれば OK（公開はされません。後で削除してください）
```

### 4. 起動
```bash
python -m src.bot
```
Mac mini を再起動しても自動で立ち上がるようにするには、下の「常駐させる」を参照してください。

## 既存記事の修正

`fixes/` に、既存9記事の修正内容が入っています。

- `fixes/<記事キー>.json` … 修正内容（置き換え前 → 置き換え後、理由）
- `fixes/REPORT.md` … 全記事の修正前後の比較（`python -m src.fix_existing preview` で作り直せます）

修正の中身：
- 冒頭に「※本記事はアフィリエイト広告（PR）を含みます。」を追加（ステマ規制対策）
- 架空の実体験・口コミ、出典のない数値、断定表現を修正
- テーマに合わないリンク（服装記事の CHATSTYLE など）を削除
- ハッシュタグとタイトルの整理

反映方法（どちらか）：
- Discord で `/既存記事修正` →記事ごとに内容を確認して [この内容でnoteを更新]
- ターミナルで1記事ずつ：
  ```bash
  python -m src.fix_existing try n2a45f4c7fd75     # ブラウザ上で修正を入力して止まる（更新しない）。目で確認用
  python -m src.fix_existing apply n2a45f4c7fd75   # 修正して更新
  ```

本文を丸ごと貼り替えると画像やリンクカードが消えるおそれがあるため、**変更する文字だけを画面上で打ち替える**方式にしています。
最初の1記事は `try` で目視確認してから進めてください。

## 見出し画像と図解

記事ごとに、見出し画像（サムネ）1枚と本文の図解2〜3枚を自動で作ります。

- 文字の中身は記事と一緒に Claude が考え、Mac mini のブラウザで PNG にします（外部の画像サービスは使いません）
- 図は3種類：確認ポイント（checklist）・手順（steps）・比較表（compare）。本文の `<p>[[FIG:fig1]]</p>` の位置に入ります
- 図の文字も、本文と同じ公開前チェック（誇大表現・架空の体験談・数値）にかけます
- `#下書き` には画像がそのまま添付されるので、スマホでも確認できます。[修正依頼] で書き直すと画像も作り直します
- 画像は `data/drafts/draft-<番号>/` に保存されます。見た目を変えたいときは `src/images.py` の CSS を直します

## 毎日のレポート

`#レポート` チャンネルに毎日届きます（時刻は `.env` の `DAILY_REPORT_TIME`、既定 21:00）。`/日次レポート` でいつでも出せます。

| 項目 | 中身 |
|---|---|
| 今日のPV | note のダッシュボードの当日集計 |
| 前回からの増分 | 累計PV・スキを毎日記録し、前回との差を出す |
| 累計 | 全期間のPV・スキ |
| 今日よく読まれた記事 | 当日PVの上位5本 |
| 今日の投稿 | Bot 経由で公開した本数 |
| 成約（手入力分） | `/成約` で記録した件数・金額 |

PVは note の公開APIには無いため、`python -m src.note_client login` で保存したログイン状態を使って取得しています。ログインが切れるとPVだけ「取得できませんでした」と表示されます（その場合は `login` をやり直してください）。

> A8.net の成果は現在 `/成約` の手入力のみです。自動取得は未対応です。

## 常駐させる（Mac mini 起動時に自動起動）
`launchd/com.note-affiliate.bot.plist` のパスを自分の環境に合わせて書き換えてから：
```bash
cp launchd/com.note-affiliate.bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.note-affiliate.bot.plist
```
ログは `data/bot.log` に出ます。Mac mini はスリープしない設定にしてください（システム設定 → エネルギー）。

## noteの画面が変わって投稿に失敗したら
失敗時は `data/screenshots/` に画面が保存されます。`config/note_selectors.json`（ボタンや入力欄の場所）を直せば復旧します。
Claude Code に「スクリーンショットを見て note_selectors.json を直して」と頼むのが早いです。

## 注意
- note には公式の投稿APIがないため、ブラウザ操作で投稿しています。短時間に大量投稿すると、アカウントに制限がかかるおそれがあります。1日1〜2本を目安にしてください。
- 記事の品質と法令順守（PR表記、景品表示法）は自動チェックでも確認していますが、最終確認は承認するあなたです。
- `.env` と `data/` は Git に入れないでください（`.gitignore` 済み）。

## ファイル構成
```
src/bot.py            Discord Bot 本体
src/generator.py      Claude Code（claude -p）で記事生成・修正
src/checker.py        公開前チェック
src/images.py         見出し画像・図解の PNG 作成
src/secretary.py      #秘書 チャンネルの会話エージェント（ツール定義もここ）
src/note_client.py    note のブラウザ操作（投稿・既存記事の部分修正）
src/fix_existing.py   既存記事の修正の読み込み・反映
src/storage.py        SQLite（テーマ・下書き・成約）
config/programs.json  A8 の提携案件とリンク
config/themes.csv     初期テーマ（SAP中心に26本）
prompts/              記事生成プロンプト
fixes/                既存記事の修正データ
```
