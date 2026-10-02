# Brain 有料記事 自動投稿 Bot

Discord の #材料 に送った材料（メモ・URL・画像・PDF）をもとに、毎朝 AI（Mac mini の Claude Code）が Brain で販売する有料記事の下書きを作り、Discord に届けます。
サムネの文字は Claude が考え、絵は ChatGPT（OpenAI の画像生成）が描きます。
Brain のアカウントは複数持てます（アカウントごとに特典＝公式LINEを変えられる）。Discord で **[承認して公開申請]** を押すと Brain に投稿（公開申請）され、Brain の審査が終わると公開されます。
毎日夜には、その日の売上と公開申請をまとめたデイリーレポートが #レポート に届きます。

note アフィリエイト Bot（`note-affiliate/`）と同じ仕組みですが、**別の Bot・別の Discord サーバー**で動く独立したプログラムです。

```
#材料 に材料を送る（フォーラムの投稿1つ＝記事1本）
            ↓
毎朝7:00  まだ使っていない材料（無ければテーマリスト）→ Claude Code で有料記事を生成
            → サムネの文字を Claude が考え、ChatGPT が絵を描く
            → 自動チェック（有料ライン・字数・価格・誇大表現・架空の実績・外部リンク）
            ↓
Discord #下書き に届く　[承認して公開申請] [修正依頼] [販売設定] [サムネ作り直し] [ボツ]
            ↓ 承認
Brain に公開申請（100円・紹介料50%・有料部分のはじめと最後に公式LINEの特典案内）→ #レポート に通知
            ↓ Brainの審査が通って公開
1時間ごとに確認して、公開から5日後に1,980円、7日後に2,980円へ自動で値上げ → #レポート に通知
```

## Discord でできること

| 操作 | 内容 |
|---|---|
| [承認して公開申請] | Brain に投稿して公開申請（チェックで ❌ があると押せません） |
| [修正依頼] | 直してほしい点を書くと、AI が書き直して再提出（画像も作り直し） |
| [販売設定] | 公開直後の価格・カテゴリー・サブカテゴリーを変える（その後の値上げは自動） |
| 出すアカウントを変える（選択メニュー） | アカウントが2つ以上あるときだけ出ます。特典もそのアカウントのものに変わります |
| [サムネ作り直し] | サムネの文字・絵の雰囲気を直して、ChatGPT にもう一度描いてもらう |
| [ボツ] | その下書きを破棄 |
| `/生成` | 今すぐ1本作る（テーマ・価格・カテゴリー指定も可） |
| `/材料一覧` | まだ記事にしていない材料 |
| `/テーマ追加` `/テーマ一覧` | 材料が無い日に使うテーマの管理 |
| `/値上げ確認` | 公開状態を今すぐ確認して、予定の日を過ぎた記事を値上げする（普段は1時間ごとに自動） |
| `/日次レポート` | 今日の売上・公開申請（毎日 `DAILY_REPORT_TIME` にも自動送信） |
| `/レポート` | 直近7日の公開申請（毎週月曜9時にも自動送信） |
| `/停止` `/再開` | 毎朝の自動生成を止める／再開する |
| `/秘書リセット` | 秘書との会話の記憶を消す |

## 材料を送る（#材料 フォーラム）

Discord で **フォーラム** チャンネル `#材料` を作り、IDを `.env` の `MATERIAL_CHANNEL_ID` に書きます。

- **新しい投稿1つ＝記事1本ぶん**。投稿のタイトルがテーマになります（例:「レンタルスタジオの物件選び」）
- 投稿に**アカウント名のタグ**を付けると、そのアカウントで出す記事になります（フォーラムの設定でアカウント名と同じタグを作っておく）
- 本文や返信で、思いついたこと・手順・自分の経験・参考URL・スクショ・PDF・テキストファイルをどんどん送る（📥 が付けば保存済み）
- 毎朝の記事作りは、まだ使っていない材料を古い順に使います。今すぐ作りたいときは投稿内の［この材料で今すぐ記事にする］
- 音声・動画は読めません。文字起こししたテキストを送ってください
- 材料に書いた本人の経験は記事に使われます（チェックでは ⚠️ 確認扱い）。材料に無い実績・数字は足しません

## 価格の自動変更

`.env` の `PRICE_SCHEDULE=0:100,5:1980,7:2980`（公開からの日数:円）。公開＝Brainの審査が通って公開された日時です。
Bot は1時間ごとに Brain の記事一覧を見て、公開を確認したら #レポート に知らせ、日数が来たら販売設定の価格だけを変えます。
値上げに失敗したら #レポート に出して、1時間後にもう一度試します。審査で差し戻された記事も #レポート に知らせます。
Brain は値上げしても再審査になりません。

## アカウントと特典（公式LINE）

`config/accounts.example.json` を `config/accounts.json` にコピーして書き換えます（1つ目が既定のアカウント）。

- `name` … #材料 フォーラムの**タグ名と同じ**にする。投稿にタグを付けると、そのアカウントで出す記事になる（タグ無しは1つ目）
- `line_url` `reward_title` `reward_top_text` `reward_bottom_text` `reward_link_text` … 特典の案内。Bot が **有料部分の一番はじめと一番うしろ** に自動で差し込みます（AIの本文とは別なので、毎回同じ文面になります）。`line_url` が空ならそのアカウントは特典なし
- `affiliate_rate` … そのアカウントだけ紹介料を変えるとき（省略で `.env` の 0.5）
- 無料部分には「購入者限定の特典があります」と1文だけ入り、LINEのURLは有料部分にだけ載ります
- 下書きのプレビューHTMLでも、特典の差し込み位置を確認できます

ログインはアカウントごとです：`python -m src.brain_client login <id>`（ログイン状態は `data/browser-profile-<id>/`）。
`config/accounts.json` が無いときは、アカウント「メイン」（id: main）1つ・特典なしで動きます。

## サムネ（ChatGPT）

`.env` の `OPENAI_API_KEY` を入れると、サムネを ChatGPT の画像生成（`gpt-image-1`）で作ります。
文字（サムネ用タイトル・補足）と絵の雰囲気は Claude が記事に合わせて決め、ChatGPT にはその文字をそのまま入れるよう指示します。
**売れるサムネのノウハウは `config/thumbnail_guide.md` に書きます。**「## 文言」は Claude がサムネの文言を考えるときに、「## デザイン」は ChatGPT が絵を描くときに、毎回最優先で守ります（書き換えたら Bot を再起動）。
横長で描いてもらい、Brain の推奨サイズ 1280×670 に切り抜きます。日本語の文字が崩れることがあるので、#下書き の画像を見て、おかしければ［サムネ作り直し］。
キーが無い・生成に失敗したときは仮のサムネ（HTMLで作る）になり、#下書き にその旨が出ます。1枚あたりの料金は OpenAI の料金表を確認してください。

## 秘書と会話する（#秘書 チャンネル）

`#秘書` に話しかけると、`src/secretary.py` の `TOOLS` にある操作だけを使って答えます。

| 種類 | ツール |
|---|---|
| 調べる（アカウント指定可） | `get_sales` `list_brain_articles` `list_materials` `list_themes` `list_recent_posts` `get_status` `list_drafts` |
| 手元のデータを変える | `add_theme` `set_draft_sales` `set_paused` |
| **Brainに反映される** | `generate_draft`（#下書き に出す）`publish_draft`（公開申請） |

使うには Discord Developer Portal → Bot → **MESSAGE CONTENT INTENT** を ON にし、`#秘書` のIDを `.env` の `SECRETARY_CHANNEL_ID` に書きます。

## 記事の形

- 無料部分（1,500〜3,000字）：悩みへの共感 → この記事で手に入るもの → おすすめな人／向かない人 → 有料部分の目次
- `<p>[[PAYWALL]]</p>` の位置が Brain の有料ラインになります
- 有料部分（8,000〜15,000字）：手順・判断基準・テンプレートなど、すぐ使える内容
- 有料部分のはじめと最後に、アカウントごとの特典（公式LINE）の案内が入ります
- カテゴリーは Brain のカテゴリー一覧から記事に合うものを AI が選びます
- メイン画像（ChatGPT）1枚と、図解5〜8枚（無料部分に1〜2枚、有料部分は章ごとに1枚が目安）を自動で作ります。図は確認ポイント・手順・比較表・要点カード（2×2）の4種類（`src/images.py`）

公開前チェック（`src/checker.py`）で ❌ になるもの：有料ラインが無い・2つ以上、有料部分が5,000字未満、価格が100〜100,000円の外、
「誰でも確実に稼げる」などの成果保証、「私はこの方法で月30万円」などの架空の実績、購入者の声の捏造、外部リンク。

## セットアップ（Mac mini で1回だけ）

はじめから丁寧な手順は [SETUP.md](SETUP.md) にあります。以下は要点です。

### 1. Discord Bot を作る（note用とは別に）
1. https://discord.com/developers/applications →「New Application」（名前は例: Brain秘書）
2. 「Bot」→「Reset Token」でトークンをコピー（`.env` の `DISCORD_TOKEN`）
3. 「OAuth2」→「URL Generator」→ SCOPES: `bot` `applications.commands` / PERMISSIONS: `Send Messages` `Embed Links` `Attach Files` `Read Message History` `Use Slash Commands` → URLを開いて Brain 用のサーバーに追加
4. サーバーに `#下書き` `#レポート`、フォーラムの `#材料`（任意で `#秘書`）を作り、IDをコピー。サーバーIDもコピー
5. 「Bot」→ **MESSAGE CONTENT INTENT** を ON（#材料 と #秘書 のメッセージを読むのに必要）

### 2. プログラムを入れる
```bash
cd task-matrix/brain-ops
/opt/homebrew/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # トークン・ID・OPENAI_API_KEY を書き込む
cp config/accounts.example.json config/accounts.json   # アカウントと特典（公式LINE）を書く
```

### 3. Brain にログインしておく（1回だけ）
```bash
python -m src.brain_client login studio   # アカウントごとに。ブラウザが開くので Brain にログイン → ターミナルで Enter
python -m src.brain_client test studio    # テスト用の下書きが Brain に保存されれば OK（公開申請はしません。後で削除してください）
```

### 4. 材料を送る
起動後、`#材料` に投稿します。材料が無い日のために、テーマだけを `config/themes.csv` や `/テーマ追加` で入れておくこともできます。

### 5. 起動・常駐
```bash
python -m src.bot
```
常駐は `launchd/com.brain-ops.bot.plist` のパスを書き換えて `~/Library/LaunchAgents/` に置き、`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.brain-ops.bot.plist`。ログは `data/bot.log`。

## Brain への投稿の仕組み

Brain の画面が裏で呼んでいる API（`api.brain-market.com`）を、ログイン済みブラウザのトークンで直接呼んでいます。
画面のボタンを押さないので、Brain の見た目が変わっても壊れにくい方式です（詳しくは `src/brain_client.py` の先頭）。

- ログインが切れると「Brain（アカウント名）にログインしていません」と出ます → `python -m src.brain_client login <id>` をやり直す
- 紹介料（Brainアフィリエイト）は `.env` の `AFFILIATE_RATE`（既定 0.5 = 50%）。アカウントごとに `accounts.json` で変更可
- `AUTO_PUBLISH=false` にすると Brain の下書き保存で止まります（公開申請は Brain の画面で自分で）

## 売上レポートについて

毎晩、アカウントごとに自分の記事一覧の「販売部数」を記録し、前回の記録との差を「売れた部数」として出します。
金額は「売れた部数 × その時点の価格」の目安です（割引リンク経由などで実際と違うことがあります）。あわせて Brain の「振込前の売上残高」も出します。
初回は記録だけなので、2日目から販売部数が出ます。

## 注意
- 1日1本を目安にしてください。Brain 側に公開申請の回数制限があり、かかった場合は下書きのまま残して知らせます
- 記事の品質と審査ガイドラインへの適合は自動チェックでも見ていますが、最終確認は承認するあなたです
- `.env` と `data/` は Git に入れないでください（`.gitignore` 済み）

## ファイル構成
```
src/bot.py            Discord Bot 本体・レポート
src/generator.py      Claude Code（claude -p）で記事生成・修正
src/checker.py        公開前チェック
src/images.py         図解の PNG 作成（サムネの仮画像も）
src/thumbnail.py      サムネを ChatGPT で作成
src/materials.py      #材料 の保存と、プロンプトへの受け渡し
src/pricing.py        公開後の自動値上げ
src/accounts.py       アカウントと特典の案内
config/accounts.example.json  アカウント・特典の書き方
src/brain_client.py   Brain への投稿（API）・ログイン
src/sales.py          売上APIの読み取り
src/secretary.py      #秘書 の会話エージェント
src/storage.py        SQLite（テーマ・下書き・売上の記録）
config/themes.csv     初期テーマ
prompts/              記事生成プロンプト
```
