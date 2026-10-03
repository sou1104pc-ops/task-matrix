以下は note 記事の下書きです。修正指示に従って書き直してください。
元の記事の厳守ルール（PR表記を本文の最後の段落に置く／架空の体験談・出典のない数値・架空の口コミ・断定表現を入れない／紹介案件以外のリンクを入れない）は引き続き守ってください。

# 修正指示
{instruction}

# 紹介してよい案件
{programs}

# 現在の下書き（JSON）
{draft_json}

# 出力形式
元と同じキー（title, main_keyword, search_intent, hashtags, program_ids, summary, thumbnail, figures, body_html）を持つJSONだけを出力してください（前後に説明文やコードフェンスを付けない）。
thumbnail は見出し画像の文字で、{{"label": "短いジャンル名（10字以内）", "catch": "大きな文字（2〜3行、改行は単語の切れ目に\\n、1行13字以内）", "sub": "補足（任意、30字以内）"}} の形にします。
figures は本文中の図解で、body_html の <p>[[FIG:id]]</p> の位置に入ります。元の下書きに無ければ、図を2〜3個（type は checklist / steps / compare）作ってください。
本文を直したら、図や見出し画像の文字も本文と食い違わないように合わせてください。図の type（checklist / steps / compare）と項目の書き方は元の形式のまま使います。
