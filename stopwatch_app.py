import time
import math
import io
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm
import matplotlib.patheffects as pe
from matplotlib.path import Path
import streamlit as st
from streamlit_image_coordinates import streamlit_image_coordinates
from PIL import Image
import pandas as pd
from datetime import datetime
import streamlit.components.v1 as components

# ================================
# 1. JavaScriptタイマーを生成する関数
# ================================
def js_timer_component(running, current_seconds):
    status = "running" if running else "paused"
    
    html_code = f"""
    <div id="timer-display" style="background-color: #262730; padding: 20px; border-radius: 12px; text-align: center; border: 3px solid #464b5d; margin-bottom: 15px;">
        <p id="time-text" style="color: #00ff00; font-family: 'Courier New', monospace; font-size: 5rem; font-weight: bold; margin: 0; line-height: 1; white-space: nowrap;">00:00</p>
    </div>

    <script>
        let seconds = {current_seconds};
        let status = "{status}";
        const display = document.getElementById('time-text');

        function updateDisplay() {{
            let m = Math.floor(seconds / 60); 
            let s = seconds % 60;
            display.innerText = (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s);
        }}

        if (status === "running") {{
            setInterval(() => {{
                seconds++;
                updateDisplay();
                // Streamlit側に「現在の秒数」をリアルタイムで送る
                window.parent.postMessage({{type: 'streamlit:setComponentValue', value: seconds}}, '*');
            }}, 1000);
        }}
        updateDisplay();
    </script>
    """
    return components.html(html_code, height=170)

# ================================
# 2. 接続・設定の初期化
# ================================
GSHEETS_READY = False
try:
    from streamlit_gsheets import GSheetsConnection
    GSHEETS_READY = True
except ImportError:
    GSHEETS_READY = False

# 分析項目の定義
STAT_ITEMS = [
    ("攻撃成功率", "atk_suc"),
    ("シュート成功率", "sht_suc"),
    ("FB成功率", "fb_suc"),
    ("FBシュート成功率", "fb_sht_suc"),
    ("7m回数", "m7_cnt"),
    ("7mシュート成功率", "m7_sht_suc"),
    ("TF回数", "tf"),
    ("RTF回数", "rtf"),
    ("シュートセーブ率", "sht_sav"),
    ("FBセーブ率", "fb_sav"),
    ("7mセーブ率", "m7_sav")
]

# ================================
# 3. コート定義と数学的ロジック
# ================================
GOAL_Y = 20.0
HALF_GOAL = 1.5
R6 = 6.0
R9 = 9.0

class HandballCourtEngine:
    def _y_on_biarc(self, x: float, r: float):
        if -HALF_GOAL <= x <= HALF_GOAL: return GOAL_Y - r
        cx = -HALF_GOAL if x < -HALF_GOAL else HALF_GOAL
        dx = x - cx
        if abs(dx) > r: return None
        y = GOAL_Y - math.sqrt(r**2 - dx**2)
        return y if y <= GOAL_Y else None

    def get_poly(self, zid):
        def build(xl, xr, ri, ro=None, yb=None):
            xs = np.linspace(xl, xr, 60)
            p = []
            for x in xs:
                y = self._y_on_biarc(x, ri)
                p.append((x, y if y is not None else GOAL_Y))
            if ro:
                for x in reversed(xs):
                    y = self._y_on_biarc(x, ro)
                    p.append((x, y if y is not None else GOAL_Y))
            elif yb is not None:
                p.extend([(xr, yb), (xl, yb)])
            return p

        zones = {
            "1": build(-10.0, -7.0, R6, ro=R9), "2": build(-7.0, -3.0, R6, ro=R9),
            "3": build(-3.0, 3.0, R6, ro=R9), "4": build(3.0, 7.0, R6, ro=R9),
            "5": build(7.0, 10.0, R6, ro=R9), "6": build(-10.0, -3.0, R9, yb=8.0),
            "7": build(-3.0, 3.0, R9, yb=8.0), "8": build(3.0, 10.0, R9, yb=8.0),
            "9": [(-1.5, 19.5), (1.5, 19.5), (1.5, 17.5), (-1.5, 17.5)]
        }
        return zones.get(zid)

    def find_zone_at(self, x, y):
        for zid in [str(i) for i in range(1, 10)]:
            poly = self.get_poly(zid)
            if poly and Path(poly).contains_point((x, y)): return zid
        return None

ZONE_LABELS = {
    "1": (-8.5, 16.2), "2": (-5.2, 13.8), "3": (0, 13.0), "4": (5.2, 13.8), "5": (8.5, 16.2),
    "6": (-7.0, 9.5), "7": (0, 9.5), "8": (7.0, 9.5), "9": (0, 18.5)
}

def draw_court_base(ax, engine):
    ax.plot([-10, 10, 10, -10, -10], [0, 0, 20, 20, 0], color="black", linewidth=2.5) 
    xs = np.linspace(-10, 10, 400)
    ax.plot(xs, [engine._y_on_biarc(x, 6.0) for x in xs], color="black", linewidth=2.2, zorder=3)
    ax.plot(xs, [engine._y_on_biarc(x, 9.0) for x in xs], "--", color="black", alpha=0.5, zorder=3)
    ax.set_xlim(-10.5, 10.5); ax.set_ylim(7.5, 20.5); ax.set_aspect("equal"); ax.axis("off")

# ================================
# 4. セッション管理とCSS
# ================================
st.set_page_config(layout="wide", page_title="Handball analyst")
engine = HandballCourtEngine()

# セッション状態の初期化
if "logs" not in st.session_state: st.session_state.logs = []
if "log_id_counter" not in st.session_state: st.session_state.log_id_counter = 0
if "last_sent_idx" not in st.session_state: st.session_state.last_sent_idx = 0 # 追加：送信済み位置管理
if "ally_players" not in st.session_state: st.session_state.ally_players = []
if "opp_players" not in st.session_state: st.session_state.opp_players = []
if "suspensions" not in st.session_state: st.session_state.suspensions = []
if "selected_zone" not in st.session_state: st.session_state.selected_zone = "未選択"
if "running" not in st.session_state: st.session_state.running = False
if "stopped_time" not in st.session_state: st.session_state.stopped_time = 0
if "start_time" not in st.session_state: st.session_state.start_time = 0
if "half" not in st.session_state: st.session_state.half = "前半"
if "history_df" not in st.session_state: st.session_state.history_df = None

elapsed = (time.time() - st.session_state.start_time) if st.session_state.running else st.session_state.stopped_time
current_time_str = time.strftime('%M:%S', time.gmtime(elapsed))

# --- 追加：JavaScriptからの時間を受け取って同期する ---
result = js_timer_component(st.session_state.running, int(elapsed))

# JavaScriptから「数字」が届いているかチェックして、届いていたら上書きする
if result is not None and isinstance(result, (int, float)):
    elapsed = result
current_time_str = time.strftime('%M:%S', time.gmtime(elapsed))

# CSSの適用：明るめのグレー（#94a3b8）ですべてのボタンを統一
st.markdown("""
    <style>
    button, 
    .stDownloadButton > button, 
    .stFormSubmitButton > button {
        background-color: #94a3b8 !important;
        color: white !important;
        border: none !important;
        font-weight: bold !important;
        transition: 0.2s;
    }
    button:hover, 
    .stDownloadButton > button:hover, 
    .stFormSubmitButton > button:hover {
        background-color: #64748b !important;
    }
    .score-board-container { display: flex; align-items: center; justify-content: center; background: #f8f4e3; border-radius: 15px; border: 1px solid #e2e8f0; margin-bottom: 5px; padding: 15px 0; min-height: 120px; }
    .team-side-a { flex: 1; display: flex; justify-content: space-between; align-items: center; padding-left: 40px; padding-right: 60px; }
    .team-side-o { flex: 1; display: flex; justify-content: space-between; align-items: center; padding-left: 60px; padding-right: 40px; }
    .score-large { font-size: 60px; font-weight: 900; color: #1e3a8a; line-height: 1; }
    .score-large-opp { font-size: 60px; font-weight: 900; color: #991b1b; line-height: 1; }
    .team-name-a { font-size: 26px; font-weight: bold; color: #1e3a8a; }
    .team-name-o { font-size: 26px; font-weight: bold; color: #991b1b; }
    .mid-divider-box { border-left: 2px solid #cbd5e1; border-right: 2px solid #cbd5e1; display: flex; flex-direction: column; justify-content: center; align-items: center; background: #f8f4e3; padding: 5px 0; width: 160px; flex-shrink: 0; }
    .stat-row-container { display: flex; align-items: center; justify-content: center; background: #fff; border-bottom: 1px solid #f1f5f9; min-height: 45px; }
    .stat-label-box { background: #fff; border-left: 2px solid #cbd5e1; border-right: 2px solid #cbd5e1; display: flex; align-items: center; justify-content: center; font-size: 14px; color: #64748b; font-weight: bold; width: 160px; height: 45px; flex-shrink: 0; text-align: center; }
    .stat-val-box-a { flex: 1; text-align: center; font-weight: bold; font-size: 18px; color: #1e3a8a; }
    .stat-val-box-o { flex: 1; text-align: center; font-weight: bold; font-size: 18px; color: #991b1b; }
    div[data-testid="stRadio"] > div { justify-content: center; }
    </style>
""", unsafe_allow_html=True)

# ================================
# 5. サイドバー
# ================================
with st.sidebar:
    st.header("📋 試合情報")
    match_title = st.text_input("試合タイトル", value=f"試合_{datetime.now().strftime('%m%d_%H%M')}")
    match_date = st.date_input("試合日", value=datetime.now())

    st.header("⚙️ チーム登録")
    ally_name_in = st.text_input("味方チーム名", value="味方チーム")
    opp_name_in = st.text_input("相手チーム名", value="相手チーム")
    
    st.divider(); st.header("👤 選手登録")
    reg_team = st.radio("登録チーム", ["味方", "相手"], horizontal=True)
    with st.form("player_reg_form", clear_on_submit=True):
        num = st.text_input("No.")
        p_name = st.text_input("名前") if reg_team == "味方" else st.text_input("名前（任意）", value="相手選手")
        pos = st.radio("Pos", ["GK", "LB", "CB", "RB", "LW", "RW", "PV"], horizontal=True)
        if st.form_submit_button(f"{reg_team}を登録"):
            if num:
                new_p = {"No.": str(int(num)) if num.isdigit() else num, "名前": p_name, "Pos": pos, "🟨 警告": "", "✌退場": "", "🟥 失格": ""}
                if reg_team == "味方": st.session_state.ally_players.append(new_p)
                else: st.session_state.opp_players.append(new_p)
                st.rerun()

    st.divider(); st.header("⚠️ ペナルティ登録")
    pen_type = st.radio("種類", ["🟨 警告", "✌退場", "🟥 失格"], horizontal=True)
    pen_team = st.radio("対象チーム", ["味方", "相手"], horizontal=True, key="pen_team_side")
    p_nums_p = [p["No."] for p in (st.session_state.ally_players if pen_team == "味方" else st.session_state.opp_players)]
    pen_target_num = st.selectbox("No.を選択", sorted(p_nums_p, key=lambda x: int(x) if x.isdigit() else 999) if p_nums_p else ["未登録"])
    if st.button("🚨 ペナルティ登録", use_container_width=True):
        if pen_target_num != "未登録":
            time_label = f"{st.session_state.half} {current_time_str}"
            p_list = st.session_state.ally_players if pen_team == "味方" else st.session_state.opp_players
            for p in p_list:
                if p["No."] == pen_target_num: p[pen_type] = f"{p.get(pen_type, '')}, {time_label}".strip(", ")
            if pen_type in ["✌退場", "🟥 失格"]: st.session_state.suspensions.append({"team": pen_team, "no": pen_target_num, "start_time": elapsed})
            st.rerun()

    st.divider(); st.header("💾 データ管理")
    # スプレッドシート送信ロジック（修正：重複防止機能付き）
    if st.button("🌐 スプレッドシートに蓄積送信", use_container_width=True):
        if not GSHEETS_READY:
            st.error("設定が必要です。")
        elif not st.session_state.logs:
            st.warning("データがありません。")
        else:
            try:
                conn = st.connection("gsheets", type=GSheetsConnection)
                
                # 未送信のログだけを抽出
                current_logs = st.session_state.logs
                new_logs = current_logs[st.session_state.last_sent_idx:]
                
                if not new_logs:
                    st.info("新しく送信するデータはありません。")
                else:
                    df_new = pd.DataFrame(new_logs).drop(columns=['id'], errors='ignore')
                    try:
                        df_old = conn.read(ttl=0)
                        df_final = pd.concat([df_old, df_new], ignore_index=True)
                    except:
                        df_final = df_new
                    
                    conn.update(data=df_final)
                    
                    # 送信済みの位置を更新
                    st.session_state.last_sent_idx = len(current_logs)
                    
                    st.success(f"{len(new_logs)}件の新規データを蓄積しました！")
                    st.balloons()
            except Exception as e:
                st.error(f"送信失敗: {e}")

    if st.button("♻️ 画面をリセット(次の試合へ)", use_container_width=True):
        st.session_state.logs = []
        st.session_state.log_id_counter = 0
        st.session_state.last_sent_idx = 0 # 追加：リセット
        st.session_state.stopped_time = 0
        st.session_state.running = False
        st.rerun()

    has_logs = len(st.session_state.logs) > 0
    csv_data = pd.DataFrame(st.session_state.logs).drop(columns=['id'], errors='ignore').to_csv(index=False).encode('utf-8-sig') if has_logs else b""
    st.download_button(label="📥 現在のログをCSV保存", data=csv_data, file_name=f"match_{match_title}.csv", mime="text/csv", use_container_width=True, disabled=not has_logs)

    st.divider(); st.header("🔄 表示モード")
    display_mode = st.radio("モード切替", ["🔴 リアルタイム試合記録", "📚 過去試合の履歴参照"], index=0)

# ================================
# 6. 分析・表示用共通関数
# ================================
def get_stats_logic(logs_to_calc, team_name, all_logs, target_no=None, is_gk_target=False):
    l_off = [l for l in logs_to_calc if l["チーム"] == team_name]
    if target_no and not is_gk_target: l_off = [l for l in l_off if l["No."] == target_no]
    l_field = [l for l in l_off if l["位置"] != "9" and l["状況"] != "7m"]; l_7m = [l for l in l_off if (l["位置"] == "9" or l["状況"] == "7m")]
    def calc_rate_val(num, den): return round((num/den)*100, 1) if den > 0 else 0.0
    
    shots = [l for l in l_field if l["結果"] in ["G", "O", "Save"]]; goals = [l for l in shots if l["結果"] == "G"]
    fb_all = [l for l in l_field if l["状況"] == "FB"]; fb_shots = [l for l in fb_all if l["結果"] in ["G", "O", "Save"]]; fb_goals = [l for l in fb_shots if l["結果"] == "G"]
    tf_cnt = sum(1 for l in l_off if l["結果"] == "TF"); rtf_cnt = sum(1 for l in l_off if l["結果"] == "RTF")
    
    l_opp_all = [l for l in all_logs if l["チーム"] != team_name]
    if is_gk_target:
        my_def = [l for l in l_opp_all if l.get("vs_gk") == target_no]
        l_def_f = [l for l in my_def if l["位置"] != "9" and l["状況"] != "7m"]; l_def_7 = [l for l in my_def if (l["位置"] == "9" or l["状況"] == "7m")]
        def gk_rate(lst):
            sh = [l for l in lst if l["結果"] in ["G", "O", "Save"]]; sv = [l for l in sh if l["結果"] == "Save"]
            return round((len(sv)/len(sh))*100, 1) if len(sh) > 0 else 0.0
        sht_sav, fb_sav, m7_sav = gk_rate(l_def_f), gk_rate([l for l in l_def_f if l["状況"]=="FB"]), gk_rate(l_def_7)
    else:
        l_opp_f = [l for l in l_opp_all if l["位置"] != "9" and l["状況"] != "7m"]; l_opp_7 = [l for l in l_opp_all if (l["位置"] == "9" or l["状況"] == "7m")]
        def tm_rate(lst):
            sh = [l for l in lst if l["結果"] in ["G", "O", "Save"]]; sv = [l for l in sh if l["結果"] == "Save"]
            return round((len(sv)/len(sh))*100, 1) if len(sh) > 0 else 0.0
        sht_sav, fb_sav, m7_sav = tm_rate(l_opp_f), tm_rate([l for l in l_opp_f if l["状況"]=="FB"]), tm_rate(l_opp_7)
    return {"atk_suc": calc_rate_val(len(shots), len(l_field)), "sht_suc": calc_rate_val(len(goals), len(shots)), "fb_suc": calc_rate_val(len(fb_shots), len(fb_all)), "fb_sht_suc": calc_rate_val(len(fb_goals), len(fb_shots)), "m7_cnt": float(len(l_7m)), "m7_sht_suc": calc_rate_val(sum(1 for l in l_7m if l["結果"]=="G"), len([l for l in l_7m if l["結果"] in ["G", "O", "Save"]])), "tf": float(tf_cnt), "rtf": float(rtf_cnt), "sht_sav": sht_sav, "fb_sav": fb_sav, "m7_sav": m7_sav}

def render_analysis_report(target_logs, a_name, o_name):
    def cg(t, p=None): return sum(1 for l in target_logs if l["チーム"] == t and l["結果"] == "G" and (p is None or l["ピリオド"] == p))
    a_tot, o_tot = cg("味方"), cg("相手"); a_1, o_1, a_2, o_2 = cg("味方", "前半"), cg("相手", "前半"), cg("味方", "後半"), cg("相手", "後半")
    st.markdown(f'<div class="score-board-container"><div class="team-side-a"><div class="team-name-a">{a_name}</div><div class="score-large">{a_tot}</div></div><div class="mid-divider-box"><div style="font-size: 11px; color: #64748b;">前半</div><div style="font-size: 18px; font-weight: bold;">{a_1} - {o_1}</div><div style="font-size: 11px; color: #64748b; margin-top:5px;">後半</div><div style="font-size: 18px; font-weight: bold;">{a_2} - {o_2}</div></div><div class="team-side-o"><div class="score-large-opp">{o_tot}</div><div class="team-name-o">{o_name}</div></div></div>', unsafe_allow_html=True)
    a_res = get_stats_logic(target_logs, "味方", target_logs); o_res = get_stats_logic(target_logs, "相手", target_logs)
    for label, key in STAT_ITEMS:
        av, ov = a_res[key], o_res[key]; a_bg = 'background-color: rgba(30,58,138,0.08);' if av>ov else ''; o_bg = 'background-color: rgba(153,27,27,0.08);' if ov>av else ''
        ad = f"{int(av)}" if "回数" in label or "tf" in key or "rtf" in key else f"{av:.1f}%"
        od = f"{int(ov)}" if "回数" in label or "tf" in key or "rtf" in key else f"{ov:.1f}%"
        st.markdown(f'<div class="stat-row-container"><div class="stat-val-box-a" style="{a_bg}">{ad}</div><div class="stat-label-box">{label}</div><div class="stat-val-box-o" style="{o_bg}">{od}</div></div>', unsafe_allow_html=True)

def render_heatmap_ui(ax, t_name, target_logs):
    draw_court_base(ax, engine); l_list = [l for l in target_logs if l["チーム"] == t_name and l["位置"] != "9"]
    total = max(1, len(l_list)); cmap = plt.cm.Blues if t_name == "味方" else plt.cm.Reds; norm = BoundaryNorm(np.arange(0, 1.2, 0.1), cmap.N)
    for zid, pos in ZONE_LABELS.items():
        if zid == "9": continue
        z_l = [l for l in l_list if l["位置"] == zid]; p = np.array(engine.get_poly(zid)); share = len(z_l)/total
        ax.fill(p[:,0], p[:,1], color=cmap(norm(share)) if z_l else "#fdf2e9", alpha=0.8 if z_l else 0.3, zorder=1)
        ax.plot(list(p[:,0])+[p[0,0]], list(p[:,1])+[p[0,1]], color="gray", linewidth=0.8, linestyle=":", zorder=2)
        z_sh = [l for l in z_l if l["結果"] in ["G", "O", "Save"]]
        if z_sh:
            r = sum(1 for s in z_sh if s["結果"]=="G")/len(z_sh)
            ax.text(pos[0], pos[1], f"{r*100:.0f}", ha='center', va='center', fontsize=10, fontweight='bold', zorder=5).set_path_effects([pe.withStroke(linewidth=2, foreground="white")])

# ================================
# 7. メインUI
# ================================
st.title("🤾 Handball analyst")

if display_mode == "🔴 リアルタイム試合記録":
# JavaScriptタイマーを表示し、現在の秒数を受け取る
    new_seconds = js_timer_component(st.session_state.running, int(elapsed))
    
    # 手元のタブレットから新しい秒数が届いたら、Python側の時間(elapsed)を更新する
    if new_seconds is not None:
        elapsed = new_seconds
        current_time_str = time.strftime('%M:%S', time.gmtime(elapsed))
    # 操作ボタンとピリオド（ここは既存のまま）
    btn_col1, btn_col2 = st.columns([1, 1])

    # 操作ボタンとピリオド（前半/後半）を横並びに配置
    # ここは横に並べても文字数が少ないので崩れません
    btn_col1, btn_col2 = st.columns([1, 1])
    with btn_col1:
        if st.button("Start / Stop", use_container_width=True, key="stopwatch"):
            if not st.session_state.running: 
                st.session_state.start_time = time.time() - st.session_state.stopped_time
                st.session_state.running = True
            else: 
                st.session_state.stopped_time = time.time() - st.session_state.start_time
                st.session_state.running = False
            st.rerun()
    with btn_col2:
        st.session_state.half = st.radio("period", ["前半", "後半"], horizontal=True, label_visibility="collapsed", key="period_toggle")

    st.divider()

    # --- 【修正ポイント2】退場タイマーをタイマーの「下」に配置する ---
    st.markdown('<p style="font-weight: bold; margin-bottom: 8px; font-size: 1.2rem; text-align: center;">⏱ 退場カウントダウン</p>', unsafe_allow_html=True)
    
    active_suspensions = []
    cards_list = []
    
    for s in st.session_state.suspensions:
        remaining = 120 - (elapsed - s["start_time"])
        if remaining > 0:
            color = "#1e3a8a" if s["team"] == "味方" else "#991b1b"
            rem_str = time.strftime('%M:%S', time.gmtime(remaining))
            # カード形式で表示（これも改行を禁止）
            card_item = f'''
            <div style="
                display: inline-block; 
                padding: 12px 20px; 
                border: 3px solid {color}; 
                border-radius: 10px; 
                color: {color}; 
                background-color: white; 
                margin: 5px; 
                min-width: 140px; 
                text-align: center; 
                box-shadow: 2px 2px 8px rgba(0,0,0,0.1);
                white-space: nowrap;">
                <span style="font-weight: 900; font-size: 1.8rem;">No.{s["no"]}</span> 
                <span style="font-weight: 500; font-size: 1.8rem; margin-left:15px;">{rem_str}</span>
            </div>'''
            cards_list.append(card_item)
            active_suspensions.append(s)
            
    if cards_list:
        # 退場者が複数いる場合は、横に並べて入り切らなくなったら自動で折り返します
        st.markdown(f'<div style="display: flex; flex-wrap: wrap; justify-content: center;">{"".join(cards_list)}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="color: #888; font-style: italic; padding: 15px; border: 1px dashed #ccc; border-radius: 10px; text-align: center;">現在退場者はいません</div>', unsafe_allow_html=True)
    
    st.session_state.suspensions = active_suspensions

    st.subheader("選手名簿")
    col_plist1, col_plist2 = st.columns(2)
    def sort_p(l): return sorted(l, key=lambda x: int(x["No."]) if x["No."].isdigit() else 999)
    with col_plist1:
        st.markdown(f"<span style='color: #1e3a8a; font-weight: bold;'>{ally_name_in}</span>", unsafe_allow_html=True)
        if st.session_state.ally_players:
            edited_a = st.data_editor(pd.DataFrame(sort_p(st.session_state.ally_players)), column_order=("No.", "名前", "Pos", "🟨 警告", "✌退場", "🟥 失格"), hide_index=True, use_container_width=True, key="ally_edit", num_rows="dynamic")
            st.session_state.ally_players = edited_a.to_dict('records')
    with col_plist2:
        st.markdown(f"<span style='color: #991b1b; font-weight: bold;'>{opp_name_in}</span>", unsafe_allow_html=True)
        if st.session_state.opp_players:
            edited_o = st.data_editor(pd.DataFrame(sort_p(st.session_state.opp_players)), column_order=("No.", "名前", "Pos", "🟨 警告", "✌退場", "🟥 失格"), hide_index=True, use_container_width=True, key="opp_edit", num_rows="dynamic")
            st.session_state.opp_players = edited_o.to_dict('records')

    st.divider(); st.subheader("記録")
    c_gk1, c_gk2 = st.columns(2)
    with c_gk1:
        st.markdown(f'<p style="color: #1e3a8a; font-weight: bold; margin-bottom: 5px;">出場中の味方GK ({ally_name_in})</p>', unsafe_allow_html=True)
        ally_gk_nums = [p["No."] for p in st.session_state.ally_players if p.get("Pos") == "GK"]
        st.session_state.active_ally_gk = st.selectbox("ally_gk_sel", ["未登録"] + ally_gk_nums, label_visibility="collapsed")
    with c_gk2:
        st.markdown(f'<p style="color: #991b1b; font-weight: bold; margin-bottom: 5px;">出場中の相手GK ({opp_name_in})</p>', unsafe_allow_html=True)
        opp_gk_nums = [p["No."] for p in st.session_state.opp_players if p.get("Pos") == "GK"]
        st.session_state.active_opp_gk = st.selectbox("opp_gk_sel", ["未登録"] + opp_gk_nums, label_visibility="collapsed")

    col_vis, col_rec = st.columns([1.5, 1])
    with col_vis:
        fig_prev, ax_prev = plt.subplots(figsize=(7, 5)); draw_court_base(ax_prev, engine)
        for zid, pos in ZONE_LABELS.items():
            p = np.array(engine.get_poly(zid)); is_selected = st.session_state.selected_zone == zid
            ax_prev.fill(p[:,0], p[:,1], color="#f39c12" if is_selected else "#fdf2e9", alpha=0.8 if is_selected else 0.3, zorder=1)
            ax_prev.plot(list(p[:,0])+[p[0,0]], list(p[:,1])+[p[0,1]], color="gray", linewidth=0.8, linestyle=":", zorder=2)
            ax_prev.text(pos[0], pos[1], "7m" if zid == "9" else zid, ha='center', va='center', fontsize=12, fontweight='bold', color="#2c3e50", zorder=4)
        buf = io.BytesIO(); fig_prev.savefig(buf, format="png", bbox_inches='tight', pad_inches=0.1); buf.seek(0)
        value = streamlit_image_coordinates(Image.open(buf), key="court_click")
        if value:
            click_x, click_y = (value["x"] / value["width"]) * 21 - 10.5, 20.5 - (value["y"] / value["height"]) * 13
            cz = engine.find_zone_at(click_x, click_y)
            if cz and st.session_state.selected_zone != cz: st.session_state.selected_zone = cz; st.rerun()

    with col_rec:
        zone_disp = st.session_state.selected_zone if st.session_state.selected_zone != '9' else '7m'
        st.markdown(f'<div style="background-color: #fff3e0; padding: 10px; border-radius: 5px; border-left: 5px solid #ff9800; color: #e65100; margin-bottom: 20px; font-weight: bold;">選択エリア: {zone_disp if st.session_state.selected_zone != "未選択" else "エリアを選択"}</div>', unsafe_allow_html=True)
        team_rec = st.radio("チーム", ["味方", "相手"], horizontal=True, key="team_r")
        p_nums_r = [p["No."] for p in sort_p(st.session_state.ally_players if team_rec == "味方" else st.session_state.opp_players)]
        p_num_r = st.selectbox("No.", p_nums_r if p_nums_r else ["未登録"], key="num_r")
        
        res_r = st.radio("結果", ["G", "O", "Save", "TF", "RTF"], horizontal=True)
        sit_options = ["7m"] if st.session_state.selected_zone == '9' else ["Set", "FB"]
        sit_r = st.radio("状況", sit_options, horizontal=True)
        
        if st.button("記録を確定", use_container_width=True, key="confirm_btn"):
            if st.session_state.selected_zone != "未選択" and p_num_r != "未登録":
                target_gk = st.session_state.active_opp_gk if team_rec == "味方" else st.session_state.active_ally_gk
                st.session_state.logs.append({
                    "試合名": match_title, "日付": str(match_date), "相手校": opp_name_in,
                    "id": st.session_state.log_id_counter, "時間": current_time_str, 
                    "チーム": team_rec, "No.": p_num_r, "位置": st.session_state.selected_zone, 
                    "結果": res_r, "状況": sit_r, "ピリオド": st.session_state.half, "vs_gk": target_gk
                })
                st.session_state.log_id_counter += 1; st.toast("記録完了！", icon="✅"); time.sleep(0.4); st.rerun()

    st.divider(); st.subheader("分析レポート")
    render_analysis_report(st.session_state.logs, ally_name_in, opp_name_in)

    st.divider(); st.subheader("ヒートマップ")
    c_map1, c_map2 = st.columns(2)
    with c_map1: f1, a1 = plt.subplots(figsize=(5, 4)); render_heatmap_ui(a1, "味方", st.session_state.logs); st.pyplot(f1)
    with c_map2: f2, a2 = plt.subplots(figsize=(5, 4)); render_heatmap_ui(a2, "相手", st.session_state.logs); st.pyplot(f2)

    st.divider(); st.subheader("個人スタッツ"); cs1, cs2 = st.columns(2)
    def draw_p_card(label, team, color, p_list):
        if label == "未選択": return
        no = label.split(" ")[0].replace("No.", ""); p_info = next((p for p in p_list if p['No.'] == no), None); is_gk = p_info.get("Pos") == "GK" if p_info else False
        ps = get_stats_logic(st.session_state.logs, team, st.session_state.logs, target_no=no, is_gk_target=is_gk)
        st.markdown(f'<div style="background:rgba(0,0,0,0.03); padding:15px; border-radius:15px; border:1px solid {color}33;"><div style="text-align:center; font-weight:bold; font-size:1.2rem; color:white; background:{color}; padding:10px; border-radius:10px; margin-bottom:10px;">{label} の成績</div>', unsafe_allow_html=True)
        for sl, sk in STAT_ITEMS:
            if "セーブ率" in sl and not is_gk: continue
            val = ps[sk]; disp = f"{int(val)}" if "回数" in label or "tf" in sk or "rtf" in sk else f"{val:.1f}%"
            st.markdown(f'<div style="display:flex; justify-content:space-between; padding:6px 15px; border-bottom:1px solid #eee;"><span style="color:#666; font-size:0.9rem;">{sl}</span><span style="font-weight:bold;">{disp}</span></div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with cs1:
        sel_a = st.selectbox(f"【{ally_name_in}】選手", ["未選択"] + [f"No.{p['No.']} {p['名前']}" for p in sort_p(st.session_state.ally_players)])
        draw_p_card(sel_a, "味方", "#1e3a8a", st.session_state.ally_players)
    with cs2:
        sel_o = st.selectbox(f"【{opp_name_in}】選手", ["未選択"] + [f"No.{p['No.']} {p['名前']}" for p in sort_p(st.session_state.opp_players)])
        draw_p_card(sel_o, "相手", "#991b1b", st.session_state.opp_players)

    st.divider(); st.subheader("ログ")
    cl1, cl2 = st.columns(2)
    def dl(label, color):
        t = "味方" if label == ally_name_in else "相手"; st.markdown(f"<h3 style='color: {color}; text-align: center; border-bottom: 2px solid {color};'>{label}</h3>", unsafe_allow_html=True)
        for p in ["前半", "後半"]:
            pl = [l for l in st.session_state.logs if l["チーム"] == t and l["ピリオド"] == p]
            if pl: df = pd.DataFrame(pl); df["位置"] = df["位置"].apply(lambda x: "7m" if x == "9" else x); st.data_editor(df, column_order=("時間", "状況", "位置", "No.", "結果", "vs_gk"), hide_index=True, use_container_width=True, key=f"edit_{t}_{p}")
            else: st.caption(f"{p}の記録なし")
    with cl1: dl(ally_name_in, "#1e3a8a")
    with cl2: dl(opp_name_in, "#991b1b")

else:
    st.info("📚 過去試合のデータベースを参照しています。現在の試合記録は保持されています。")
    if GSHEETS_READY:
        if st.button("🔄 全データを読み込む", use_container_width=True):
            try:
                conn = st.connection("gsheets", type=GSheetsConnection)
                st.session_state.history_df = conn.read(ttl=0)
                st.success("成功！")
            except: st.error("失敗")
        if st.session_state.history_df is not None:
            df_h = st.session_state.history_df.copy()
            df_h['label'] = df_h['日付'].astype(str) + " | " + df_h['試合名'] + " (vs " + df_h['相手校'].fillna("不明") + ")"
            sel_match = st.selectbox("試合を選択", ["未選択"] + df_h['label'].unique().tolist())
            if sel_match != "未選択":
                h_logs = df_h[df_h['label'] == sel_match].to_dict('records')
                render_analysis_report(h_logs, "味方", h_logs[0].get("相手校", "相手"))
                hc1, hc2 = st.columns(2)
                with hc1: fh1, ah1 = plt.subplots(figsize=(5, 4)); render_heatmap_ui(ah1, "味方", h_logs); st.pyplot(fh1)
                with hc2: fh2, ah2 = plt.subplots(figsize=(5, 4)); render_heatmap_ui(ah2, "相手", h_logs); st.pyplot(fh2)

# タイマーリラン
if st.session_state.running or len(st.session_state.suspensions) > 0:
    time.sleep(0.1); st.rerun()
