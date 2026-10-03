以下は Kindle 電子書籍の下書きの企画部分（タイトル・キーワード・紹介文・表紙・目次・はじめに・おわりに）です。修正指示に従って書き直してください。
本文の章は別に書き直すので、指示が章の中身に関わるときは、書き直す章の番号を rewrite_chapters に入れ、その章で何を直すかを chapter_notes に書いてください。
章の数を増やす・減らす・順番を変えるときは chapters を直し、内容が変わる章の番号をすべて rewrite_chapters に入れてください（番号は直した後の chapters の 1 始まりの番号）。

元の企画の厳守ルール（main_keyword をタイトルの一番前に／7つのキーワード欄は各{keyword_max}字以内で他の著者名・本のタイトルを入れない／架空の体験談・出典のない数字・結果を約束する言葉を入れない／紹介文に特典・URL・レビューのお願いを入れない）は引き続き守ってください。

# 修正指示
{instruction}

# 現在の企画（JSON）
{plan_json}

# 出力形式
元と同じキー（title, title_kana, subtitle, subtitle_kana, main_keyword, keywords, categories, description_html, cover, chapters, intro_html, outro_html）に加えて、
"rewrite_chapters": [書き直す章の番号], "chapter_notes": "章を書き直すときの指示" を持つJSONだけを出力してください（前後に説明文やコードフェンスを付けない）。
直す必要のない項目は元のまま入れてください。タイトルや章を変えたら、紹介文の目次と表紙の文字も食い違わないように合わせてください。
