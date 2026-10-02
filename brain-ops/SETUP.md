# Brain 自動投稿 Bot セットアップ手順（はじめから）

上から順番に進めれば、Brain の有料記事が毎朝 Discord に届くようになります。
全部で 1〜2 時間ほどです。途中で止めても、次回は続きから再開できます。

**どこで作業するか**

| 印 | 意味 |
|---|---|
| 📱 どこでも | スマホでも PC でも OK（ブラウザか Discord アプリ） |
| 🖥 Mac mini | Mac mini の前で（または画面共有で）作業する |

Mac mini での作業（STEP 7 以降）は、Claude Code に「SETUP.md の STEP 7 をやって」と頼めば代わりに進められます。
**ただし STEP 8（Brain へのログイン）だけは、あなたが Mac mini の画面で操作する必要があります。**

---

## STEP 0　用意するもの

- [ ] Discord のアカウント（note の Bot で使っているものと同じで OK）
- [ ] Brain のアカウント（投稿したいアカウントすべて。ID とパスワードが分かる状態で）
- [ ] アカウントごとの公式 LINE の友だち追加 URL と、LINE で渡す特典の内容
- [ ] OpenAI のアカウント（サムネを ChatGPT で作るため。クレジットカードが必要）
- [ ] 下の「メモ欄」をコピーしたメモ帳（途中で出てくる ID やキーを書き留める）

```
【メモ欄】
Botのトークン        :
サーバーID           :
#下書き のID         :
#レポート のID       :
#材料 のID           :
#秘書 のID           :
OpenAI APIキー       :
```

> ⚠️ トークンと API キーは、パスワードと同じです。人に見せたり、SNS に貼ったりしないでください。

---

## STEP 1　Brain 用の Discord サーバーを作る　📱 どこでも

note 用とは別のサーバーを作ります。

1. Discord を開き、左のサーバー一覧のいちばん下にある **「＋」（サーバーを追加）** を押す
2. **「オリジナルの作成」** → **「自分と友達のため」** を選ぶ
3. サーバー名を入れる（例: `Brain運用`）→ **「新規作成」**

---

## STEP 2　チャンネルを作る　📱 どこでも

### 2-1. 文字のチャンネルを3つ

サーバー名の横の **「＋」**（または「テキストチャンネル」の横の＋）→ **「テキスト」** を選び、次の3つを作ります。

| チャンネル名 | 使い道 |
|---|---|
| `下書き` | 毎朝の記事の下書きが届く。承認ボタンを押す場所 |
| `レポート` | 公開申請・公開・値上げ・毎日の売上の報告が届く |
| `秘書` | AI 秘書と会話する（「今日の売上は？」など） |

### 2-2. 材料を送るフォーラムを1つ

1. 同じく **「＋」** → チャンネルの種類で **「フォーラム」** を選ぶ
2. 名前は `材料` → 作成

> 「フォーラム」が選べないとき：サーバー設定 → **「コミュニティを有効にする」** をオンにしてから、もう一度作ってください。

### 2-3. フォーラムにアカウント名のタグを作る

記事をどの Brain アカウントで出すかを、このタグで決めます。

1. `#材料` の右の歯車（チャンネルの編集）→ **「タグ」**
2. **「タグを作成」** で、Brain のアカウントごとにタグを作る（例: `スタジオ開業`、`サブ`）
3. このタグ名は、あとの STEP 6 で使うので **メモしておく**

---

## STEP 3　ID をコピーする　📱 どこでも

Bot にチャンネルの場所を教えるため、ID（長い数字）をコピーします。

### 3-1. 開発者モードをオンにする（1回だけ）

- **PC**：左下の歯車（ユーザー設定）→ **「詳細設定」** → **「開発者モード」** をオン
- **スマホ**：右下の自分のアイコン → 歯車 → **「詳細設定」** → **「開発者モード」** をオン

### 3-2. ID をコピーしてメモ欄に貼る

| 何の ID | やり方 |
|---|---|
| サーバーID | サーバー名を右クリック（スマホは長押し）→ **「サーバーIDをコピー」** |
| `#下書き` `#レポート` `#材料` `#秘書` | それぞれのチャンネル名を右クリック（スマホは長押し）→ **「チャンネルIDをコピー」** |

---

## STEP 4　Discord Bot を作る　📱 どこでも（PC のブラウザが楽です）

### 4-1. Bot を作る

1. https://discord.com/developers/applications を開き、Discord でログイン
2. 右上の **「New Application」** → 名前（例: `Brain秘書`）→ チェックを入れて **「Create」**
3. 左のメニューの **「Bot」** を開く
4. **「Reset Token」** → **「Yes, do it!」** → 表示されたトークンを **「Copy」** してメモ欄に貼る
   （トークンはこの画面を閉じると二度と表示されません。なくしたら Reset Token でまた作れます）

### 4-2. メッセージを読む許可をオンにする

同じ「Bot」の画面を下にスクロールして、**Privileged Gateway Intents** の

- **MESSAGE CONTENT INTENT** をオン

にして、下に出る **「Save Changes」** を押します。
（これが無いと、`#材料` と `#秘書` のメッセージを Bot が読めません）

### 4-3. Bot をサーバーに招待する

1. 左のメニューの **「OAuth2」** → **「URL Generator」**
2. **SCOPES** で次の2つにチェック
   - `bot`
   - `applications.commands`
3. 下に出る **BOT PERMISSIONS** で次にチェック
   - `View Channels`
   - `Send Messages`
   - `Send Messages in Threads`
   - `Embed Links`
   - `Attach Files`
   - `Read Message History`
   - `Add Reactions`
   - `Use Slash Commands`
4. いちばん下の **GENERATED URL** をコピーしてブラウザで開く
5. 「サーバーに追加」で STEP 1 のサーバーを選ぶ → **「はい」** → **「認証」**
6. Discord のサーバーのメンバー一覧に Bot が（オフラインで）入っていれば OK

---

## STEP 5　OpenAI の API キーを作る　📱 どこでも（PC のブラウザが楽です）

サムネを ChatGPT（OpenAI の画像生成）で作るためのキーです。
**ChatGPT Plus の月額契約とは別のもの**で、使った分だけ料金がかかります。

1. https://platform.openai.com を開いてログイン（ChatGPT と同じアカウントで OK）
2. 右上の歯車 → **「Billing」** → **「Add payment details」** でカードを登録し、クレジットを入れる（最初は 10 ドル程度で十分）
3. 歯車 → **「Organization」→「General」** に **「Verify Organization」** があれば、案内どおり本人確認をする
   （画像生成のモデル `gpt-image-1` は、この確認が済んでいないと使えないことがあります）
4. 左のメニューの **「API keys」** → **「Create new secret key」** → 名前（例: `brain-bot`）→ **「Create secret key」**
5. 表示されたキー（`sk-` で始まる）をコピーしてメモ欄に貼る

> 1枚あたりの料金は https://openai.com/api/pricing の画像生成の欄で確認できます。
> 下書きを作るたびと、［サムネ作り直し］を押すたびに1枚作ります。

---

## STEP 6　アカウントと特典の情報をまとめる　📱 どこでも

Brain のアカウントごとに、次の表を埋めます（STEP 7 で設定ファイルに書き写します）。

| 項目 | 書くこと | 例 |
|---|---|---|
| id | 半角英数字の短い名前（自分で決める。あとで変えない） | `studio` |
| name | STEP 2-3 で作った **フォーラムのタグ名と同じ** | `スタジオ開業` |
| line_url | 公式 LINE の友だち追加 URL | `https://lin.ee/xxxxxxx` |
| reward_title | 特典の名前 | `開業準備チェックリスト（PDF）` |
| reward_top_text | 有料部分の **はじめ** に出す案内文 | `先に特典のご案内です。公式LINEを友だち追加して「特典」と送ると受け取れます。` |
| reward_bottom_text | 有料部分の **最後** に出す案内文 | `最後まで読んでいただきありがとうございます。まだの方は公式LINEから特典を受け取ってください。` |
| reward_link_text | リンクの文字（省略可） | `公式LINEで特典を受け取る` |

**友だち追加 URL の調べ方**：LINE公式アカウントマネージャー（https://manager.line.biz）→ 対象のアカウント → **「友だちを増やす」→「友だち追加ガイド」** → 「URLを作成」のURLをコピー

**1つ目に書いたアカウントが「既定」** になります（タグを付けなかった材料は、このアカウントで出ます）。

---

## STEP 7　Mac mini に設定を書き込む　🖥 Mac mini

> Claude Code に「SETUP.md の STEP 7 をやって」と頼み、メモ欄と STEP 6 の表を渡せば、ここは代わりにやってもらえます。

Bot のプログラムは次の場所にあります（必要なものはインストール済みです）。

```
/Users/tanuki/task-matrix-brain/brain-ops
```

### 7-1. ターミナルを開いてフォルダに移動

```bash
cd /Users/tanuki/task-matrix-brain/brain-ops
```

### 7-2. `.env`（Discord と OpenAI の設定）を作る

```bash
cp .env.example .env
open -e .env
```

テキストエディットが開くので、メモ欄の値を `=` のうしろに貼ります。

```
DISCORD_TOKEN=（Botのトークン）
DISCORD_GUILD_ID=（サーバーID）
DRAFT_CHANNEL_ID=（#下書き のID）
REPORT_CHANNEL_ID=（#レポート のID）
SECRETARY_CHANNEL_ID=（#秘書 のID）
MATERIAL_CHANNEL_ID=（#材料 のID）
OPENAI_API_KEY=（OpenAI APIキー）
```

ほかの行（毎朝の時刻 `DAILY_TIME=07:00`、価格の予定 `PRICE_SCHEDULE=0:100,5:1980,7:2980`、紹介料 `AFFILIATE_RATE=0.5` など）はそのままで OK です。
保存（⌘S）して閉じます。

### 7-3. `config/accounts.json`（アカウントと特典）を作る

```bash
cp config/accounts.example.json config/accounts.json
open -e config/accounts.json
```

見本が2アカウントぶん入っているので、STEP 6 の表の内容に書き換えます。

- アカウントが1つなら、2つ目の `{ ... }` をまるごと消す（直前の `,` も消す）
- 3つ以上なら、`{ ... }` をコピーして `,` でつなげて増やす
- 文字は必ず `"` で囲む。行の最後の `,` の付け忘れ・付けすぎに注意

書けたら保存して、次のコマンドで書き方が正しいか確認します。

```bash
.venv/bin/python -c "from src import accounts; [print(a['id'], a['name'], a.get('line_url')) for a in accounts.ACCOUNTS]"
```

アカウントの一覧が表示されれば OK です。エラーが出たら、`"` や `,` を見直してください。

---

## STEP 8　Brain にログインする（アカウントごと）　🖥 Mac mini の画面で

**ここだけは、あなたが Mac mini の画面で操作します。**

### 8-1. ログイン

`studio` の部分は、STEP 6 で決めた id に置き換えます。

```bash
cd /Users/tanuki/task-matrix-brain/brain-ops
.venv/bin/python -m src.brain_client login studio
```

1. ブラウザ（Chromium）が開き、Brain のトップページが表示されます
2. ブラウザで Brain にログインします（いつも通りの方法で）
3. ログインできたら、**ターミナルに戻って Enter** を押します
4. `ログイン状態を保存しました: （あなたのBrainのユーザー名）` と出れば成功です

アカウントが複数あるときは、id を変えて同じことを繰り返します。

### 8-2. テスト投稿（公開はされません）

```bash
.venv/bin/python -m src.brain_client test studio
```

`Brainに下書きを保存しました` と URL が出たら、Brain の「記事管理」に **【テスト】自動投稿の動作確認** という下書きができています。
確認できたら、**Brain の画面でこの下書きを削除** してください。

### 8-3. 売上データの確認（任意）

```bash
.venv/bin/python -m src.brain_client sales studio
```

記事ごとの価格・販売部数と、振込前の売上残高が表示されれば OK です。

---

## STEP 9　Bot を動かしてみる　🖥 Mac mini

### 9-1. 起動

```bash
cd /Users/tanuki/task-matrix-brain/brain-ops
.venv/bin/python -m src.bot
```

`ログインしました: Brain秘書#xxxx` と出れば起動成功です。Discord のメンバー一覧で Bot がオンラインになります。

### 9-2. Discord で確かめる

1. どこかのチャンネルで `/` を打ち、`/生成` `/材料一覧` などが出てくるか見る
   （出ないときは Discord アプリを再起動）
2. `#材料` に **新しい投稿** を作る
   - タイトル：記事のテーマ（例: `レンタルスタジオの物件選び`）
   - タグ：出したいアカウントのタグ
   - 本文：思いついたこと、手順、経験など（箇条書きで OK）
3. Bot が「📥 受け付けます」と返信し、本文に 📥 が付けば保存されています
4. 返信の **［この材料で今すぐ記事にする］** を押す
5. 5〜15分ほどで `#下書き` に下書き（サムネ・図・プレビュー HTML つき）が届けば成功です
   - サムネの欄に「⚠️ 仮のサムネです」と出たら、OpenAI のキーか STEP 5 の本人確認を見直します
6. 内容を見て、**［承認して公開申請］** を押すと Brain に公開申請されます
   （試すだけなら［ボツ］で OK）

確認できたら、ターミナルで **Control + C** を押して Bot を止めます。

---

## STEP 10　Mac mini で常駐させる　🖥 Mac mini

ターミナルを閉じても、Mac mini を再起動しても Bot が動き続けるようにします。

```bash
cd /Users/tanuki/task-matrix-brain/brain-ops
sed 's#/Users/YOURNAME/task-matrix/brain-ops#/Users/tanuki/task-matrix-brain/brain-ops#g; s#/Users/YOURNAME#/Users/tanuki#g' \
  launchd/com.brain-ops.bot.plist > ~/Library/LaunchAgents/com.brain-ops.bot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.brain-ops.bot.plist
```

動いているかの確認：

```bash
launchctl list | grep brain-ops     # 行が出れば動いている
tail -f data/bot.log                # ログを見る（Control + C で抜ける）
```

止めたいとき／設定（`.env` や `accounts.json`）を変えたあとに再起動したいとき：

```bash
launchctl bootout gui/$(id -u)/com.brain-ops.bot                                   # 止める
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.brain-ops.bot.plist    # 動かす
```

> Mac mini がスリープすると止まります。システム設定 → **エネルギー** で、スリープしない設定にしてください（note の Bot と同じ）。

---

## 毎日の使い方

| いつ | やること |
|---|---|
| 思いついたとき | `#材料` に投稿（1投稿＝記事1本。アカウントのタグを付ける）。あとから返信で材料を足せる |
| 毎朝 7:00 | まだ使っていない材料から1本、`#下書き` に届く |
| 届いたら | 読んで［承認して公開申請］／［修正依頼］／［サムネ作り直し］／［ボツ］ |
| 自動 | Brain の審査が通ったら `#レポート` に通知 → 5日後に 1,980円、7日後に 2,980円へ自動で値上げ |
| 毎晩 21:00 | `#レポート` にアカウントごとの売上 |
| いつでも | `#秘書` に「今日の売上は？」「材料はいくつ残ってる？」など |

---

## 困ったとき

| 症状 | 対処 |
|---|---|
| `/` を打ってもコマンドが出ない | Discord アプリを再起動。STEP 4-3 の `applications.commands` にチェックしたか確認 |
| `#材料` に投稿しても 📥 が付かない | STEP 4-2 の MESSAGE CONTENT INTENT がオンか確認。`.env` の `MATERIAL_CHANNEL_ID` が `#材料` の ID か確認 |
| 「Brain（○○）にログインしていません」 | STEP 8-1 をその id でやり直す |
| サムネが「仮のサムネ」になる | `.env` の `OPENAI_API_KEY`、OpenAI のクレジット残高、STEP 5-3 の本人確認を確認 |
| サムネの日本語が崩れている | 下書きの［サムネ作り直し］。文字を短くすると崩れにくい |
| 毎朝の下書きが来ない | `tail -n 50 data/bot.log` を Claude Code に見せる。材料もテーマも無い日は `#下書き` にその旨が出ます |
| 値上げされない | `/値上げ確認` を実行。結果を `#レポート` で確認 |

分からないことがあれば、エラーの文やログをそのまま Claude Code に貼って聞いてください。
