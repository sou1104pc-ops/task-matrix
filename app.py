import streamlit as st
from datetime import datetime, date, timedelta
from supabase import create_client, Client

st.set_page_config(
    page_title="TASK·MATRIX",
    page_icon="⬛",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0e0e1a; }
[data-testid="stSidebar"] { background: #14141f; }
section[data-testid="stSidebar"] > div { background: #14141f; }
h1,h2,h3,h4 { color: #e0e0ff !important; }
label, .stMarkdown p { color: #a0a0c0 !important; }

.task-card {
    background: #1a1a2e;
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 10px;
    border-left: 4px solid #7b78ff;
    transition: transform .15s;
}
.task-card:hover { transform: translateX(3px); }
.task-card .task-name { font-size: 14px; font-weight: 600; color: #e0e0ff; }
.task-card .task-meta { font-size: 11px; color: #6060a0; margin-top: 4px; }
.task-card .task-due-ok   { color: #2ecc71; font-size: 11px; }
.task-card .task-due-warn { color: #ffc933; font-size: 11px; }
.task-card .task-due-over { color: #ff5568; font-size: 11px; }

.quad-A { border-left-color: #ff5568 !important; }
.quad-B { border-left-color: #7b78ff !important; }
.quad-C { border-left-color: #ffc933 !important; }
.quad-D { border-left-color: #44445a !important; }

.quad-badge {
    display: inline-block;
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 12px;
    font-weight: 700;
    margin-bottom: 8px;
}
.badge-A { background: #ff556822; color: #ff5568; }
.badge-B { background: #7b78ff22; color: #7b78ff; }
.badge-C { background: #ffc93322; color: #ffc933; }
.badge-D { background: #44445a44; color: #888; }

.status-todo  { background:#2a2a3a; color:#888; border-radius:4px; padding:1px 7px; font-size:11px; }
.status-doing { background:#7b78ff22; color:#7b78ff; border-radius:4px; padding:1px 7px; font-size:11px; }
.status-done  { background:#2ecc7122; color:#2ecc71; border-radius:4px; padding:1px 7px; font-size:11px; }

.proj-card {
    background: #1a1a2e;
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 12px;
    border-top: 3px solid #7b78ff;
}
.col-header {
    text-align: center;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 6px 0;
    border-radius: 6px;
    margin-bottom: 12px;
}
[data-testid="metric-container"] {
    background: #1a1a2e;
    border-radius: 10px;
    padding: 12px 16px;
}
hr { border-color: #2a2a3e !important; }
.stButton > button {
    background: #1a1a2e;
    color: #a0a0c0;
    border: 1px solid #2a2a3e;
    border-radius: 6px;
}
.stButton > button:hover {
    background: #24243a;
    color: #e0e0ff;
    border-color: #7b78ff;
}
</style>
""", unsafe_allow_html=True)

# ── Constants ───────────────────────────────────────────────────────────────
QUAD_COLORS = {"A": "#ff5568", "B": "#7b78ff", "C": "#ffc933", "D": "#44445a"}
QUAD_LABELS = {
    "A": "A — 重要・緊急（すぐやる）",
    "B": "B — 重要・非緊急（計画する）",
    "C": "C — 緊急・非重要（委任する）",
    "D": "D — 非重要・非緊急（排除）",
}
STATUS_LABELS = {"todo": "未着手", "doing": "進行中", "done": "完了"}
STARS = {1: "★☆☆☆☆", 2: "★★☆☆☆", 3: "★★★☆☆", 4: "★★★★☆", 5: "★★★★★"}

DEFAULT_PROJECTS = [
    {"id": "p1", "name": "Brain コンテンツ販売", "description": "レンタルスタジオ開業ガイド教材の制作・販売", "color": "#7b78ff"},
    {"id": "p2", "name": "姫路民泊投資", "description": "姫路城周辺の短期賃貸物件の取得・運営", "color": "#ffc933"},
    {"id": "p3", "name": "M&A・事業売却", "description": "3年以内に約1億円での事業売却を目指す", "color": "#2ecc71"},
    {"id": "p4", "name": "スタジオ運営改善", "description": "各拠点のオペレーション効率化・品質向上", "color": "#48bfe3"},
]

DEFAULT_MEMBERS = [
    {"id": "a", "name": "田中 颯", "color": "#7b78ff"},
    {"id": "b", "name": "鈴木 花", "color": "#2ecc71"},
    {"id": "c", "name": "山本 剛", "color": "#ffc933"},
    {"id": "d", "name": "佐藤 愛", "color": "#ff5568"},
    {"id": "e", "name": "中村 蓮", "color": "#48bfe3"},
]

DEFAULT_TASKS = [
    {"id": 1, "name": "emu-LABO 契約解除通知 最終確認", "description": "法的整合性・支払い根拠の再確認", "quad": "A", "future": 4, "status": "doing", "due": "2026-03-08", "assignees": ["a"], "tag": "法務", "project": ""},
    {"id": 2, "name": "Brainコンテンツ スライド作成（第3章）", "description": "トランスクリプトから図解スライドへ変換", "quad": "A", "future": 5, "status": "todo", "due": "2026-03-10", "assignees": ["a"], "tag": "コンテンツ", "project": "p1"},
    {"id": 3, "name": "神戸スタジオ 顧客対応マニュアル整備", "description": "予約〜退室フローの標準化", "quad": "B", "future": 4, "status": "doing", "due": "2026-03-15", "assignees": ["a", "b"], "tag": "運営", "project": "p4"},
    {"id": 4, "name": "姫路民泊 物件調査レポート", "description": "投資試算・競合分析を含む", "quad": "B", "future": 5, "status": "todo", "due": "2026-03-20", "assignees": ["c"], "tag": "投資", "project": "p2"},
    {"id": 5, "name": "M&A 売却シナリオ見直し（3年計画）", "description": "EBITDA倍率・拠点数目標の更新", "quad": "B", "future": 5, "status": "todo", "due": "2026-04-01", "assignees": ["a"], "tag": "経営", "project": "p3"},
    {"id": 6, "name": "新大阪スタジオ 月次レポート提出", "description": "3月分稼働率・売上集計", "quad": "C", "future": 2, "status": "todo", "due": "2026-03-31", "assignees": ["b", "d"], "tag": "経営", "project": "p4"},
    {"id": 7, "name": "加古川スタジオ 設備点検", "description": "照明・音響・清掃用具チェック", "quad": "C", "future": 1, "status": "todo", "due": "2026-03-18", "assignees": ["d", "e"], "tag": "設備", "project": "p4"},
    {"id": 8, "name": "Brain セールスレター 改稿", "description": "ターゲット訴求を強化", "quad": "B", "future": 5, "status": "todo", "due": "2026-03-22", "assignees": ["a"], "tag": "コンテンツ", "project": "p1"},
    {"id": 9, "name": "競合スタジオ NONO 調査メモ整理", "description": "加古川・高砂・神戸3拠点の料金体系比較", "quad": "B", "future": 4, "status": "done", "due": "2026-02-20", "assignees": ["c", "a"], "tag": "調査", "project": "p4"},
]

# ── Supabase ─────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

def db_load():
    sb = get_supabase()
    tasks    = sb.table("tasks").select("*").execute().data or []
    projects = sb.table("projects").select("*").execute().data or []
    members  = sb.table("members").select("*").execute().data or []

    # 初回: デフォルトデータを投入
    if not tasks and not projects and not members:
        for p in DEFAULT_PROJECTS:
            sb.table("projects").insert(p).execute()
        for m in DEFAULT_MEMBERS:
            sb.table("members").insert(m).execute()
        for t in DEFAULT_TASKS:
            sb.table("tasks").insert(t).execute()
        return DEFAULT_TASKS, DEFAULT_PROJECTS, DEFAULT_MEMBERS

    return tasks, projects, members

def db_upsert_task(task):
    get_supabase().table("tasks").upsert(task).execute()

def db_delete_task(task_id):
    get_supabase().table("tasks").delete().eq("id", task_id).execute()

def db_upsert_project(project):
    get_supabase().table("projects").upsert(project).execute()

def db_delete_project(project_id):
    get_supabase().table("projects").delete().eq("id", project_id).execute()

def db_upsert_member(member):
    get_supabase().table("members").upsert(member).execute()

def db_delete_member(member_id):
    sb = get_supabase()
    sb.table("members").delete().eq("id", member_id).execute()
    # タスクの担当者からも削除
    tasks = sb.table("tasks").select("id, assignees").execute().data or []
    for t in tasks:
        assignees = t.get("assignees") or []
        if member_id in assignees:
            new_assignees = [a for a in assignees if a != member_id]
            sb.table("tasks").update({"assignees": new_assignees}).eq("id", t["id"]).execute()
            # session_stateも更新
            for st_task in st.session_state.tasks:
                if st_task["id"] == t["id"]:
                    st_task["assignees"] = new_assignees

# ── Session state ─────────────────────────────────────────────────────────────
def init_state():
    if "tasks" not in st.session_state:
        tasks, projects, members = db_load()
        st.session_state.tasks    = tasks
        st.session_state.projects = projects
        st.session_state.members  = members
    if "next_id" not in st.session_state:
        ids = [t["id"] for t in st.session_state.tasks]
        st.session_state.next_id = max(ids) + 1 if ids else 1

# ── Helpers ──────────────────────────────────────────────────────────────────
def get_member(mid):
    for m in st.session_state.members:
        if m["id"] == mid:
            return m
    return {"id": mid, "name": mid, "color": "#888"}

def get_project(pid):
    for p in st.session_state.projects:
        if p["id"] == pid:
            return p
    return None

def due_class(due_str):
    if not due_str:
        return "task-due-ok", ""
    try:
        d = datetime.strptime(due_str, "%Y-%m-%d").date()
    except ValueError:
        return "task-due-ok", due_str
    today = date.today()
    if d < today:
        return "task-due-over", f"期限切れ {due_str}"
    if d <= today + timedelta(days=2):
        return "task-due-warn", f"締切間近 {due_str}"
    return "task-due-ok", due_str

def assignee_html(assignees):
    parts = []
    for aid in (assignees or []):
        m = get_member(aid)
        parts.append(f'<span style="color:{m["color"]};font-size:11px;">●{m["name"]}</span>')
    return " ".join(parts)

def task_card_html(t):
    due_cls, due_label = due_class(t.get("due", ""))
    stars = STARS.get(t.get("future", 1), "")
    quad = t.get("quad", "D")
    qcolor = QUAD_COLORS.get(quad, "#44445a")
    status = t.get("status", "todo")
    sc = f"status-{status}"
    sl = STATUS_LABELS.get(status, status)
    ahtml = assignee_html(t.get("assignees") or [])
    proj = get_project(t.get("project", ""))
    proj_html = f'<span style="color:{proj["color"]};font-size:10px;">◆ {proj["name"]}</span> ' if proj else ""
    tag = t.get("tag", "")
    tag_html = f'<span style="background:#1e1e3a;color:#6060a0;border-radius:4px;padding:1px 6px;font-size:10px;">{tag}</span>' if tag else ""
    return f"""
<div class="task-card quad-{quad}" style="border-left-color:{qcolor}">
  <div class="task-name">{t['name']}</div>
  <div class="task-meta" style="margin-top:5px;">{proj_html}{tag_html}</div>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px;">
    <span class="{sc}">{sl}</span>
    <span style="color:#ffc933;font-size:11px;">{stars}</span>
  </div>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px;">
    <span>{ahtml}</span>
    <span class="{due_cls}">{due_label}</span>
  </div>
</div>
"""

def apply_filters(tasks):
    f = st.session_state.get("filters", {})
    result = tasks
    q = f.get("search", "").strip()
    if q:
        ql = q.lower()
        result = [t for t in result if ql in t["name"].lower() or ql in t.get("description", "").lower()]
    st_filter = f.get("status", "すべて")
    status_map = {"未着手": "todo", "進行中": "doing", "完了": "done"}
    if st_filter != "すべて":
        result = [t for t in result if t["status"] == status_map.get(st_filter, st_filter)]
    quads = f.get("quads", [])
    if quads:
        result = [t for t in result if t["quad"] in quads]
    projs = f.get("projects", [])
    if projs:
        result = [t for t in result if t.get("project", "") in projs]
    asns = f.get("assignees", [])
    if asns:
        result = [t for t in result if any(a in (t.get("assignees") or []) for a in asns)]
    fmin = f.get("future_min", 1)
    result = [t for t in result if t.get("future", 1) >= fmin]
    return result

# ── Sidebar ───────────────────────────────────────────────────────────────────
def render_sidebar():
    st.sidebar.markdown("## TASK·MATRIX")
    st.sidebar.markdown("---")
    st.sidebar.markdown("### フィルタ")

    if "filters" not in st.session_state:
        st.session_state.filters = {}
    f = st.session_state.filters

    f["search"] = st.sidebar.text_input("テキスト検索", value=f.get("search", ""))
    status_opts = ["すべて", "未着手", "進行中", "完了"]
    f["status"] = st.sidebar.selectbox("ステータス", status_opts, index=status_opts.index(f.get("status", "すべて")))
    f["quads"] = st.sidebar.multiselect("象限", ["A", "B", "C", "D"], default=f.get("quads", []))

    proj_opts = {p["id"]: p["name"] for p in st.session_state.projects}
    sel_projs = st.sidebar.multiselect("プロジェクト", list(proj_opts.values()),
                                        default=[proj_opts[pid] for pid in f.get("projects", []) if pid in proj_opts])
    f["projects"] = [pid for pid, pname in proj_opts.items() if pname in sel_projs]

    member_opts = {m["id"]: m["name"] for m in st.session_state.members}
    sel_members = st.sidebar.multiselect("担当者", list(member_opts.values()),
                                          default=[member_opts[mid] for mid in f.get("assignees", []) if mid in member_opts])
    f["assignees"] = [mid for mid, mname in member_opts.items() if mname in sel_members]
    f["future_min"] = st.sidebar.slider("未来重要度（最低）", 1, 5, value=f.get("future_min", 1))

    st.sidebar.markdown("---")
    if st.sidebar.button("+ タスクを追加", use_container_width=True, type="primary"):
        st.session_state["show_add_form"] = not st.session_state.get("show_add_form", False)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 管理")
    with st.sidebar.expander("メンバー管理"):
        render_member_manager()
    with st.sidebar.expander("プロジェクト管理"):
        render_project_manager()

# ── Member manager ────────────────────────────────────────────────────────────
def render_member_manager():
    for m in st.session_state.members:
        c1, c2 = st.columns([3, 1])
        c1.markdown(f'<span style="color:{m["color"]}">●</span> {m["name"]}', unsafe_allow_html=True)
        if c2.button("削除", key=f"del_m_{m['id']}"):
            db_delete_member(m["id"])
            st.session_state.members = [x for x in st.session_state.members if x["id"] != m["id"]]
            st.rerun()

    with st.form("add_member_form", clear_on_submit=True):
        nm = st.text_input("名前")
        nc = st.color_picker("カラー", value="#7b78ff")
        if st.form_submit_button("追加"):
            if nm.strip():
                new_id = f"m{len(st.session_state.members)+1}_{nm[:3]}"
                new_member = {"id": new_id, "name": nm.strip(), "color": nc}
                db_upsert_member(new_member)
                st.session_state.members.append(new_member)
                st.rerun()

# ── Project manager ───────────────────────────────────────────────────────────
def render_project_manager():
    for p in st.session_state.projects:
        c1, c2 = st.columns([3, 1])
        c1.markdown(f'<span style="color:{p["color"]}">◆</span> {p["name"]}', unsafe_allow_html=True)
        if c2.button("削除", key=f"del_p_{p['id']}"):
            db_delete_project(p["id"])
            st.session_state.projects = [x for x in st.session_state.projects if x["id"] != p["id"]]
            st.rerun()

    with st.form("add_project_form", clear_on_submit=True):
        pn = st.text_input("プロジェクト名")
        pd_input = st.text_input("説明")
        pc = st.color_picker("カラー", value="#7b78ff")
        if st.form_submit_button("追加"):
            if pn.strip():
                new_pid = f"p{len(st.session_state.projects)+1}"
                new_proj = {"id": new_pid, "name": pn.strip(), "description": pd_input, "color": pc}
                db_upsert_project(new_proj)
                st.session_state.projects.append(new_proj)
                st.rerun()

# ── Task form ─────────────────────────────────────────────────────────────────
def render_task_form(task=None, form_key="add_task"):
    is_edit = task is not None
    with st.form(form_key, clear_on_submit=not is_edit):
        st.markdown(f"#### {'タスク編集' if is_edit else 'タスク追加'}")
        name = st.text_input("タスク名 *", value=task["name"] if is_edit else "")
        desc = st.text_area("説明", value=task.get("description", "") if is_edit else "", height=80)

        col1, col2 = st.columns(2)
        with col1:
            quad = st.selectbox("象限", ["A", "B", "C", "D"],
                                index=["A", "B", "C", "D"].index(task["quad"]) if is_edit else 0)
            status_opts = list(STATUS_LABELS.keys())
            status_labels = list(STATUS_LABELS.values())
            cur_status = task["status"] if is_edit else "todo"
            status_sel = st.selectbox("ステータス", status_labels, index=status_opts.index(cur_status))
            status = status_opts[status_labels.index(status_sel)]
        with col2:
            future = st.slider("未来重要度", 1, 5, value=task.get("future", 3) if is_edit else 3)
            due_val = None
            if is_edit and task.get("due"):
                try:
                    due_val = datetime.strptime(task["due"], "%Y-%m-%d").date()
                except Exception:
                    due_val = None
            due = st.date_input("締切日", value=due_val)

        member_opts = {m["id"]: m["name"] for m in st.session_state.members}
        cur_assignees = [member_opts[a] for a in (task.get("assignees") or []) if a in member_opts] if is_edit else []
        sel_assignees = st.multiselect("担当者", list(member_opts.values()), default=cur_assignees)
        assignees = [mid for mid, mname in member_opts.items() if mname in sel_assignees]

        tag = st.text_input("カテゴリタグ", value=task.get("tag", "") if is_edit else "")

        proj_opts = {"": "なし"} | {p["id"]: p["name"] for p in st.session_state.projects}
        proj_names = list(proj_opts.values())
        proj_ids = list(proj_opts.keys())
        cur_proj = task.get("project", "") if is_edit else ""
        proj_idx = proj_ids.index(cur_proj) if cur_proj in proj_ids else 0
        proj_sel = st.selectbox("プロジェクト", proj_names, index=proj_idx)
        project = proj_ids[proj_names.index(proj_sel)]

        if st.form_submit_button("保存" if is_edit else "追加"):
            if not name.strip():
                st.error("タスク名は必須です")
                return False
            due_str = due.strftime("%Y-%m-%d") if due else ""
            if is_edit:
                task.update({
                    "name": name.strip(), "description": desc, "quad": quad,
                    "future": future, "status": status, "due": due_str,
                    "assignees": assignees, "tag": tag, "project": project,
                })
                db_upsert_task(task)
                return True
            else:
                new_task = {
                    "id": st.session_state.next_id,
                    "name": name.strip(), "description": desc, "quad": quad,
                    "future": future, "status": status, "due": due_str,
                    "assignees": assignees, "tag": tag, "project": project,
                }
                db_upsert_task(new_task)
                st.session_state.tasks.append(new_task)
                st.session_state.next_id += 1
                return True
    return False

# ── Stats ─────────────────────────────────────────────────────────────────────
def render_stats(tasks_all):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("未着手", sum(1 for t in tasks_all if t["status"] == "todo"))
    c2.metric("進行中", sum(1 for t in tasks_all if t["status"] == "doing"))
    c3.metric("完了",   sum(1 for t in tasks_all if t["status"] == "done"))
    c4.metric("A象限 残タスク", sum(1 for t in tasks_all if t["quad"] == "A" and t["status"] != "done"))

# ── Kanban view ───────────────────────────────────────────────────────────────
def render_kanban(tasks):
    cols = st.columns(3)
    for i, (skey, slabel, scolor) in enumerate([("todo", "未着手", "#888"), ("doing", "進行中", "#7b78ff"), ("done", "完了", "#2ecc71")]):
        with cols[i]:
            st.markdown(f'<div class="col-header" style="background:#1a1a2e;color:{scolor};">{slabel}</div>', unsafe_allow_html=True)
            for t in [t for t in tasks if t["status"] == skey]:
                st.markdown(task_card_html(t), unsafe_allow_html=True)
                ec1, ec2 = st.columns([1, 1])
                if ec1.button("編集", key=f"edit_k_{t['id']}"):
                    st.session_state[f"edit_open_{t['id']}"] = True
                if ec2.button("削除", key=f"del_k_{t['id']}"):
                    db_delete_task(t["id"])
                    st.session_state.tasks = [x for x in st.session_state.tasks if x["id"] != t["id"]]
                    st.rerun()
                if st.session_state.get(f"edit_open_{t['id']}", False):
                    if render_task_form(task=t, form_key=f"edit_form_{t['id']}"):
                        st.session_state[f"edit_open_{t['id']}"] = False
                        st.rerun()

# ── Matrix view ───────────────────────────────────────────────────────────────
def render_matrix(tasks):
    for row_quads in [("A", "B"), ("C", "D")]:
        cols = st.columns(2)
        for col_idx, q in enumerate(row_quads):
            with cols[col_idx]:
                st.markdown(f'<div class="quad-badge badge-{q}">{QUAD_LABELS[q]}</div>', unsafe_allow_html=True)
                for t in [t for t in tasks if t["quad"] == q]:
                    st.markdown(task_card_html(t), unsafe_allow_html=True)
                    ec1, ec2 = st.columns([1, 1])
                    if ec1.button("編集", key=f"edit_m_{t['id']}"):
                        st.session_state[f"edit_open_{t['id']}"] = True
                    if ec2.button("削除", key=f"del_m_{t['id']}"):
                        db_delete_task(t["id"])
                        st.session_state.tasks = [x for x in st.session_state.tasks if x["id"] != t["id"]]
                        st.rerun()
                    if st.session_state.get(f"edit_open_{t['id']}", False):
                        if render_task_form(task=t, form_key=f"edit_form_m_{t['id']}"):
                            st.session_state[f"edit_open_{t['id']}"] = False
                            st.rerun()

# ── Project view ──────────────────────────────────────────────────────────────
def render_projects(_tasks):
    for p in st.session_state.projects:
        p_tasks = [t for t in st.session_state.tasks if t.get("project") == p["id"]]
        total = len(p_tasks)
        done  = sum(1 for t in p_tasks if t["status"] == "done")
        ratio = done / total if total > 0 else 0
        st.markdown(f"""
<div class="proj-card" style="border-top-color:{p['color']}">
  <div style="display:flex;justify-content:space-between;">
    <span style="color:{p['color']};font-weight:700;font-size:15px;">◆ {p['name']}</span>
    <span style="color:#888;font-size:12px;">{done}/{total} 完了</span>
  </div>
  <div style="color:#6060a0;font-size:12px;margin-top:4px;">{p.get('description','')}</div>
</div>
""", unsafe_allow_html=True)
        st.progress(ratio, text=f"{int(ratio*100)}%")
        st.markdown("---")

# ── Table view ────────────────────────────────────────────────────────────────
def render_table(tasks):
    member_map = {m["id"]: m["name"] for m in st.session_state.members}
    proj_map   = {p["id"]: p["name"] for p in st.session_state.projects}
    rows = [{
        "ID": t["id"],
        "タスク名": t["name"],
        "象限": t["quad"],
        "ステータス": STATUS_LABELS.get(t["status"], t["status"]),
        "未来重要度": STARS.get(t.get("future", 1), ""),
        "締切日": t.get("due", ""),
        "担当者": ", ".join(member_map.get(a, a) for a in (t.get("assignees") or [])),
        "タグ": t.get("tag", ""),
        "プロジェクト": proj_map.get(t.get("project", ""), ""),
        "説明": t.get("description", ""),
    } for t in tasks]
    if rows:
        st.dataframe(rows, use_container_width=True, height=500)
    else:
        st.info("表示するタスクがありません")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    init_state()
    render_sidebar()

    st.markdown("# TASK·MATRIX")
    st.markdown("アイゼンハワーマトリクス × 未来重要度 タスク管理")
    st.markdown("---")
    render_stats(st.session_state.tasks)
    st.markdown("---")

    # サイドバーの「+ タスクを追加」ボタンで開くフォーム
    if st.session_state.get("show_add_form", False):
        with st.container(border=True):
            if render_task_form(form_key="add_task_sidebar"):
                st.success("タスクを追加しました")
                st.session_state["show_add_form"] = False
                st.rerun()
        st.markdown("---")

    tab1, tab2, tab3, tab4 = st.tabs(["カンバン", "マトリクス", "プロジェクト", "テーブル"])
    filtered = apply_filters(st.session_state.tasks)

    with tab1: render_kanban(filtered)
    with tab2: render_matrix(filtered)
    with tab3: render_projects(filtered)
    with tab4: render_table(filtered)

if __name__ == "__main__":
    main()
