"""streamlit_app.py: ממשק Streamlit לסוכן. רק תצוגה וכפתורים; כל הלוגיקה ב-core.

מריצים מהשורש:
    streamlit run streamlit_app.py

איך זה עובד: Streamlit מריץ את הקובץ הזה מההתחלה בכל לחיצה. לכן הריצה של הסוכן
(AgentSession: הגרף, ה-checkpointer וה-thread_id) נשמרת ב-st.session_state, והמסך
שמוצג נקבע לפי session.step.kind: אין ריצה -> טופס, review_fix -> אישור ראשון,
review_push -> אישור שני, done / error -> תוצאה.
בצד המסך: מפת התהליך (ui/flow.py), שמראה באיזה צומת בגרף הסוכן נמצא.
"""
from __future__ import annotations

import html

import streamlit as st

from core import config
from core.state import APPROVE, NO_PUSH, PUSH, REJECT, REJECT_WITH_NOTE, VERIFY_COMPILE
from ui import session as session_module
from ui.flow import current_round, flow_states, heading_to

st.set_page_config(page_title="Code Review Agent", page_icon=":material/troubleshoot:", layout="wide")

# ------------------------------------------------------------------ עיצוב
# צבעים במקום אחד. RTL לטקסט, LTR לקוד ול-diff.
st.markdown(
    """<style>
    @import url('https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;700;800&display=swap');
    :root{--ink:#0b1020;--text:#1e293b;--muted:#475569;--line:#e2e8f0;--panel:#f8fafc;
          --navy:#0f2a33;--brand:#0e7490;--brand-bg:#ecfeff;
          --ok:#16a34a;--ok-bg:#dcfce7;--wait:#d97706;--wait-bg:#fef3c7;--bad:#dc2626;--bad-bg:#fee2e2;--idle:#94a3b8}
    /* הגופן רק על טקסט, לא על האייקונים (Material Symbols) */
    body,.stMarkdown,.stMarkdown p,h1,h2,h3,label,input,textarea,[data-testid="stMetric"],
    [data-testid="stCaptionContainer"],button p{font-family:"Heebo",system-ui,sans-serif!important}
    .block-container{padding-top:3.2rem;max-width:1200px}
    .stMarkdown,.stAlert,label,.stCaption,h1,h2,h3,[data-testid="stMetric"]{direction:rtl;text-align:right}
    pre,code,.stCode{direction:ltr;text-align:left}
    .stMarkdown p,.stMarkdown li{font-size:1rem;color:var(--text);line-height:1.6}
    [data-testid="stCaptionContainer"]{color:var(--muted)!important;font-size:.92rem}
    h1{font-size:1.85rem!important;font-weight:800!important;color:var(--ink)!important;letter-spacing:-.02em}
    h3{font-size:1.1rem!important;font-weight:700!important;color:var(--ink)!important}
    /* סרגל עליון צבעוני */
    .topbar{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;direction:rtl;
            background:var(--navy);color:#fff;border-radius:10px;padding:12px 18px;margin-bottom:1.2rem}
    .brand{font-weight:800;font-size:1.25rem;letter-spacing:-.01em} .brand span{color:#67e8f9}
    .brand small{display:block;font-weight:500;font-size:.78rem;opacity:.85}
    .meta{display:flex;gap:8px;flex-wrap:wrap}
    .chip{font:500 .76rem ui-monospace,Menlo,monospace;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.22);
          border-radius:8px;padding:3px 9px;color:#fff;direction:ltr}
    /* כרטיסי מדדים: לבנים, מספר בכחול כהה */
    [data-testid="stMetric"]{border-radius:10px;padding:12px 16px;border:1px solid var(--line);background:#fff}
    [data-testid="stMetricValue"]{font-weight:800!important;color:var(--navy)!important}
    [data-testid="stMetricLabel"] p{font-weight:600!important;color:var(--muted)!important}
    /* כפתור ראשי: כחול אחיד */
    button[kind="primary"],button[data-testid^="stBaseButton-primary"]{background:var(--brand)!important;border-color:var(--brand)!important;font-weight:700!important}
    button[kind="primary"]:hover,button[data-testid^="stBaseButton-primary"]:hover{background:#155e75!important}
    button[data-testid="stBaseButton-secondary"]{font-weight:600}
    /* כרטיסי יתרונות במסך הפתיחה */
    .feats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;direction:rtl;margin-top:.4rem}
    .feat{border-radius:10px;padding:14px 16px;border:1px solid var(--line);background:#fff}
    .feat b{display:block;font-weight:800;font-size:1.02rem;margin-bottom:2px}
    .feat span{color:var(--muted);font-size:.92rem}
    .feat b{color:var(--navy)}
    .badge{display:inline-block;font-size:.78rem;font-weight:700;border-radius:999px;padding:2px 10px;white-space:nowrap}
    .b-ok{background:var(--ok-bg);color:#15803d} .b-wait{background:var(--wait-bg);color:#b45309}
    .b-bad{background:var(--bad-bg);color:#b91c1c} .b-idle{background:var(--panel);color:var(--muted);border:1px solid var(--line)}
    .screen-row{direction:rtl;display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--line)}
    .screen-row:last-child{border-bottom:0}
    .screen-row .f{font:600 .9rem ui-monospace,Menlo,monospace;direction:ltr;color:var(--ink)}
    .screen-row .d{color:var(--muted);font-size:.86rem}
    .screen-row .tests{font:.8rem ui-monospace,Menlo,monospace;direction:ltr;text-align:left;color:var(--text)}
    /* מפת התהליך */
    [data-testid="stSidebar"]{background:var(--panel)}
    .flow{direction:rtl;list-style:none;margin:0;padding:0}
    .flow li{position:relative;display:grid;grid-template-columns:24px 1fr;gap:10px;padding-bottom:12px}
    .flow li:not(:last-child)::before{content:"";position:absolute;right:11px;top:24px;bottom:0;width:2px;background:#cbd5e1}
    .flow li.done:not(:last-child)::before{background:var(--ok)}
    .flow .dot{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;font-size:.72rem;font-weight:800;
               border:2px solid var(--idle);color:var(--idle);background:#fff;z-index:1}
    .flow .done .dot{background:var(--ok);border-color:var(--ok);color:#fff}
    .flow .running .dot{background:var(--brand);border-color:var(--brand);color:#fff}
    .flow .waiting .dot{background:var(--wait);border-color:var(--wait);color:#fff}
    .flow .skipped .dot{border-style:dashed}
    .flow .t{font-size:.92rem;font-weight:700;color:var(--ink);line-height:1.25}
    .flow .running .t{color:var(--brand)} .flow .waiting .t{color:#b45309}
    .flow .pending .t,.flow .skipped .t{color:var(--idle);font-weight:500}
    .flow .h{font-size:.76rem;color:var(--muted);line-height:1.3}
    .flow .now{font-size:.68rem;font-weight:800;margin-inline-start:6px;padding:1px 7px;border-radius:999px;color:#fff}
    .flow .running .now{background:var(--brand)} .flow .waiting .now{background:var(--wait)}
    .flow .x{font:700 .7rem ui-monospace,monospace;color:var(--muted);margin-inline-start:4px}
    .round{direction:rtl;display:inline-block;font-weight:700;font-size:.85rem;color:var(--brand);background:var(--brand-bg);
           border:1px solid #a5f3fc;border-radius:999px;padding:2px 12px;margin-bottom:.4rem}
    .legend{direction:rtl;font-size:.76rem;font-weight:600;display:flex;gap:10px;flex-wrap:wrap;margin:.2rem 0 .8rem}
    .legend .l1{color:var(--ok)} .legend .l2{color:var(--brand)} .legend .l3{color:var(--wait)} .legend .l4{color:var(--idle)}
    </style>""",
    unsafe_allow_html=True,
)

SCREEN_LABELS = {  # סטטוס של screen_fixes -> (תווית, סוג תג, הסבר)
    "tests_confirmed": ("אושר בטסטים", "ok", "טסט שנכשל עובר עכשיו, ושום טסט לא נשבר"),
    "unproven": ("לא הוכח", "wait", "לא שבר כלום, אבל גם לא תיקן טסט (או שאין טסטים)"),
    "broke_tests": ("שבר טסטים", "bad", "נפסל"),
    "syntax_error": ("לא מתקמפל", "bad", "נפסל"),
    "invalid_edit": ("תיקון לא תקין", "bad", "המודל החזיר טווח שורות שגוי"),
}
PHASE_TEXT = {"running": "פועל", "waiting": "מחכה לך"}


def _session() -> session_module.AgentSession | None:
    return st.session_state.get("agent")


def badge(text: str, kind: str) -> str:
    return f'<span class="badge b-{kind}">{html.escape(text)}</span>'


# ------------------------------------------------------------------ מפת התהליך


def render_flow(box, agent: session_module.AgentSession | None, phase: str, heading: str | None = None) -> None:
    visited = agent.visited if agent else []
    items = []
    for i, n in enumerate(flow_states(visited, phase, heading), start=1):
        mark = "✓" if n.state == "done" else str(i)
        now = f'<span class="now">{PHASE_TEXT[n.state]}</span>' if n.state in PHASE_TEXT else ""
        runs = f'<span class="x">×{n.runs}</span>' if n.runs > 1 else ""
        items.append(
            f'<li class="{n.state}"><span class="dot">{mark}</span>'
            f'<div><div class="t">{n.label}{runs}{now}</div><div class="h">{html.escape(n.hint)}</div></div></li>'
        )
    box.markdown(f'<ol class="flow">{"".join(items)}</ol>', unsafe_allow_html=True)


def _run(action, *args, heading: str | None = None) -> None:
    """מריץ פעולה של הסוכן. אחרי כל צומת: מעדכן את מפת התהליך ואת שורת הסטטוס."""
    agent = _session()
    with st.status("הסוכן עובד...", expanded=True) as status:

        def on_node(node: str) -> None:
            render_flow(st.session_state["flow_box"], agent, "running")
            status.write(f"✓ {node}")

        render_flow(st.session_state["flow_box"], agent, "running", heading)
        step = action(*args, on_node=on_node)
        status.update(label="שגיאה" if step.kind == "error" else "הסוכן עצר", state="complete", expanded=False)
    st.rerun()  # מציירים מחדש את המסך לפי השלב החדש


def _answer(agent: session_module.AgentSession, answer: dict) -> None:
    _run(agent.answer, answer, heading=heading_to(answer["decision"], current_round(agent.visited)))


# ------------------------------------------------------------------ צד


def sidebar() -> dict:
    agent = _session()
    with st.sidebar:
        st.markdown("### תהליך הסקירה")
        if agent and agent.visited:
            st.markdown(f'<div class="round">סבב {current_round(agent.visited)} מתוך {config.MAX_ROUNDS}</div>',
                        unsafe_allow_html=True)  # fmt: skip
        st.markdown(
            '<div class="legend"><span class="l1">● הושלם</span><span class="l2">● פועל</span><span class="l3">● מחכה לך</span><span class="l4">○ לא רץ</span></div>',
            unsafe_allow_html=True,
        )
        st.session_state["flow_box"] = st.empty()
        phase = "idle"
        if agent and agent.step:
            phase = agent.step.kind if agent.step.kind in ("review_fix", "review_push") else "done"
        render_flow(st.session_state["flow_box"], agent, phase)
        if agent and agent.history:
            st.markdown("### החלטות")
            for item in agent.history:
                note = f" · {item['note']}" if item.get("note") else ""
                st.caption(f"{'אישור ראשון' if item['at'] == 'review_fix' else 'אישור שני'}: {item['decision']}{note}")
        if agent and agent.step and agent.step.kind in ("review_fix", "review_push"):
            if st.button("ביטול הריצה", icon=":material/close:", width="stretch"):
                agent.cancel()  # מוחק את ההעתק; finish לא ירוץ
                st.session_state.pop("agent")
                st.rerun()
        st.divider()
        with st.expander("הגדרות ריצה", expanded=agent is None):
            include = st.text_input("include: רק הקבצים האלה (מופרד ברווח)")
            exclude = st.text_input("exclude: בלי הקבצים האלה")
            workspace = st.text_input("תיקיית אב להעתק (ריק = תיקייה זמנית)")
            extra = st.text_input("חבילות נוספות ל-image")
    return {"include": include.split(), "exclude": exclude.split(), "workspace": workspace.strip(),
            "extra": extra.split()}  # fmt: skip


def topbar(agent: session_module.AgentSession | None) -> None:
    chips = [f"model: {config.MODEL_NAME}"]
    if config.CHECK_MODEL_NAME:
        chips.append(f"checker: {config.CHECK_MODEL_NAME}")
    if agent and agent.step and agent.step.values.get("branch_name"):
        chips.append(f"branch: {agent.step.values['branch_name']}")
    chips_html = "".join(f'<span class="chip">{html.escape(c)}</span>' for c in chips)
    st.markdown(
        f'<div class="topbar"><div class="brand">Code Review <span>Agent</span><small>סקירת קוד פייתון · תיקון · אישור אנושי</small></div>'
        f'<div class="meta">{chips_html}</div></div>',
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------ מסכים


def screen_start(settings: dict) -> None:
    st.title("סקירת קוד חדשה")
    st.caption("איתור באגי off-by-one, None ולוגיקה בקוד פייתון, תיקון בענף נפרד ובדיקה ב-Docker. "
               "שום דבר לא נכתב לריפו בלי שני אישורים שלך.")  # fmt: skip
    with st.container(border=True):
        with st.form("start", border=False):
            source = st.text_input("מקור", placeholder="https://github.com/user/repo  ·  C:\\repos\\project  ·  script.py")
            go = st.form_submit_button("הרץ סקירה", type="primary", icon=":material/play_arrow:")
    st.markdown(
        '<div class="feats">'
        '<div class="feat f1"><b>עבודה על העתק</b><span>המקור לא נפתח לכתיבה. ההעתק נמחק בסוף.</span></div>'
        '<div class="feat f2"><b>הרצה ב-Docker</b><span>קונטיינר חד-פעמי, בלי רשת, עם מגבלות משאבים.</span></div>'
        '<div class="feat f3"><b>שני אישורים</b><span>על ה-diff, ואחרי הבדיקה על הדחיפה.</span></div>'
        "</div>",
        unsafe_allow_html=True,
    )
    if go and source.strip():
        agent = session_module.AgentSession(session_module.make_deps(settings["workspace"], settings["extra"]))
        st.session_state["agent"] = agent
        _run(agent.start, source.strip(), settings["include"], settings["exclude"])


def show_suspects(suspects: list[dict], rejected: list[dict]) -> None:
    st.markdown(f"### חשדות לבאגים ({len(suspects)})")
    rows = [
        {"קובץ": s["file"], "שורה": s["line"], "סוג": s["bug_type"], "ביטחון": s.get("confidence", "-"),
         "הוכחה": s.get("evidence", "-"), "הסבר": s["reason"], "קלט מפיל": s.get("failing_input", "")}
        for s in suspects
    ]  # fmt: skip
    st.dataframe(rows, hide_index=True, width="stretch")
    if rejected:
        with st.expander(f"חשדות שנזרקו בסינון ({len(rejected)})"):
            st.dataframe(
                [{"קובץ": r["file"], "שורה": r["line"], "למה נזרק": r.get("rejected_because", ""), "הסבר": r["reason"]}
                 for r in rejected],
                hide_index=True, width="stretch",
            )  # fmt: skip


def show_screening(info: dict, values: dict) -> None:
    st.markdown("### בדיקת כל תיקון לבד")
    if not values.get("has_tests"):
        st.warning("לא נבדק בטסטים: בריפו אין טסטים, ולכן נבדקה קומפילציה בלבד. ההחלטה לפי ה-diff.")
    rows = []
    for r in info.get("screening", []):
        label, kind, text = SCREEN_LABELS.get(r["status"], (r["status"], "idle", ""))
        tests = ""
        for title, names in (("תיקן", r.get("fixed_tests")), ("שבר", r.get("new_failures"))):
            if names:
                listed = "<br>".join(html.escape(n.split("::")[-1]) for n in names[:3])
                tests += f'<div class="d">{title}:</div><div class="tests">{listed}</div>'
        rows.append(
            f'<div class="screen-row"><div><span class="f">{html.escape(r["file"])}</span>'
            f'<div class="d">{html.escape(text)} · ניסיונות: {r.get("attempts", 1)}</div>{tests}</div>{badge(label, kind)}</div>'
        )
    st.markdown("".join(rows), unsafe_allow_html=True)


def screen_review_fix(agent: session_module.AgentSession) -> None:
    info, values = agent.step.info, agent.step.values
    st.title("אישור ראשון: התיקון המוצע")
    if info.get("round", 1) > 1:
        st.info(f"סבב {info['round']} מתוך {info.get('max_rounds', '-')}: הסוכן חזר לאיתור על הקוד שכבר תוקן, "
                "כי התיקון הקודם שיפר וחלק מהטסטים עדיין נכשלים.")  # fmt: skip
    confirmed = sum(1 for r in info.get("screening", []) if r["status"] == "tests_confirmed")
    m = st.columns(4)
    m[0].metric("חשדות", len(info["suspects"]))
    m[1].metric("קבצים בתיקון", len({e["file"] for e in info["fixes"]}))
    m[2].metric("אושרו בטסטים", confirmed if values.get("has_tests") else "-")
    m[3].metric("ניסיון", f"{info['attempt']} מתוך {info['max_attempts']}")

    left, right = st.columns([3, 2], gap="large")
    with left:
        with st.container(border=True):
            st.markdown("### התיקון (diff)")
            for e in info["fixes"]:
                st.caption(f"`{e['file']}:{e['start_line']}-{e['end_line']}` {e['explanation']}")
            st.code(info["diff"], language="diff")
    with right:
        with st.container(border=True):
            show_screening(info, values)
        with st.container(border=True):
            st.markdown("### ההחלטה שלך")
            can_retry = info["attempt"] < info["max_attempts"]
            note = st.text_area("הערה למודל (לדחייה עם הערה)", disabled=not can_retry, height=80,
                                help=None if can_retry else "נגמרו הניסיונות")  # fmt: skip
            if st.button("אשר והחל בענף", type="primary", icon=":material/check:", width="stretch"):
                _answer(agent, {"decision": APPROVE})
            b1, b2 = st.columns(2)
            if b1.button("דחה עם הערה", icon=":material/edit_note:", disabled=not can_retry, width="stretch"):
                if note.strip():
                    _answer(agent, {"decision": REJECT_WITH_NOTE, "note": note.strip()})
                else:
                    st.error("כתבי הערה, כדי שהמודל ידע מה לשנות.")
            if b2.button("דחה", icon=":material/block:", width="stretch"):
                _answer(agent, {"decision": REJECT})
    with st.container(border=True):
        show_suspects(info["suspects"], values.get("rejected_suspects") or [])


def screen_review_push(agent: session_module.AgentSession) -> None:
    info = agent.step.info
    st.title("אישור שני: דחיפה לענף")
    left, right = st.columns([3, 2], gap="large")
    with left:
        rounds = info.get("rounds") or []
        if rounds:
            with st.container(border=True):
                st.markdown(f"### סבבים ({len(rounds) + 1})")
                rows = [{"סבב": r["round"], "קבצים שתוקנו": ", ".join(r["fixed_files"]),
                         "טסטים נכשלים לפני": r["failing_before"], "אחרי": r["failing_after"]} for r in rounds]  # fmt: skip
                st.dataframe(rows, hide_index=True, width="stretch")
                st.caption("הסבב האחרון מופיע בתוצאת האימות.")
        with st.container(border=True):
            st.markdown("### כל ה-commits בענף" if rounds else "### ה-commit בענף")
            st.code(info["diff"], language="diff")
    with right:
        with st.container(border=True):
            st.markdown("### תוצאת האימות")
            (st.success if info["verify_ok"] else st.error)(info["summary"])
            if info["verification"] == VERIFY_COMPILE:
                st.warning("לא נבדק בטסטים: נבדקה קומפילציה בלבד.")
        with st.container(border=True):
            st.markdown("### ההחלטה שלך")
            st.caption(f"דחיפה יוצרת את הענף `{info['branch']}` בריפו. בלי force, אף פעם לא ל-main.")
            if st.button("דחוף לענף", type="primary", icon=":material/upload:", width="stretch"):
                _answer(agent, {"decision": PUSH})
            if st.button("לא לדחוף", icon=":material/do_not_disturb_on:", width="stretch"):
                _answer(agent, {"decision": NO_PUSH})


def screen_done(agent: session_module.AgentSession) -> None:
    step, values = agent.step, agent.step.values
    st.title("תוצאה")
    if step.kind == "error":
        st.error(step.error)
        st.caption("ההעתק נמחק. אפשר לנסות שוב.")
    elif values.get("pushed"):
        st.success(values.get("result", ""))
    else:
        st.info(values.get("result", "") or "-")
    if values.get("suspects"):
        with st.container(border=True):
            show_suspects(values["suspects"], [])
    if values.get("diff") and not values.get("pushed"):
        with st.container(border=True):
            st.markdown("### ה-diff")
            st.code(values["diff"], language="diff")
            st.download_button("הורדת cra_fix.patch", values["diff"], file_name="cra_fix.patch", icon=":material/download:")

    from scripts.run_cli import write_report  # אותו דוח כמו ב-CLI

    report = write_report(values, values.get("repo_path", ""), agent.deps.reviewer, step.error)
    c1, c2 = st.columns(2)
    c1.download_button("הורדת הדוח המלא", report.read_text(encoding="utf-8"), file_name=report.name,
                       icon=":material/description:", width="stretch")  # fmt: skip
    if c2.button("סקירה חדשה", type="primary", icon=":material/add:", width="stretch"):
        st.session_state.pop("agent")
        st.rerun()


# ------------------------------------------------------------------ ראשי

settings = sidebar()
agent = _session()
topbar(agent)
if agent is None or agent.step is None:
    screen_start(settings)
elif agent.step.kind == "review_fix":
    screen_review_fix(agent)
elif agent.step.kind == "review_push":
    screen_review_push(agent)
else:
    screen_done(agent)
