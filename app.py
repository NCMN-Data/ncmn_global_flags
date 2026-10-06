"""NCMN Global Flags — 전세계 깃발 등록 현황 지도 (Streamlit).

실행: streamlit run app.py
"""

import hmac
import os
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from streamlit_image_coordinates import streamlit_image_coordinates

import db
from countries import COUNTRIES, COUNTRY_NAMES, COUNTRY_NAMES_EN

ADMIN_PASSWORD = os.environ.get("NCMN_ADMIN_PASSWORD", "admin")

# 지도 중심 경도. 150°E면 한국이 화면 중앙 부근에 오고 지도 양 끝이 대서양에서
# 잘려 대륙이 갈라지지 않는다. 127.5로 바꾸면 한국이 정중앙에 온다(브라질·그린란드가 잘림).
MAP_CENTER_LON = 150
MAP_LAT_RANGE = [-58, 84]

INK = "#1f1f1e"
INK_MUTED = "#6b6a66"
SURFACE = "#fcfcfb"
LAND_EMPTY = "#e3e2de"
FLAG_SCALE = [[0.0, "#b7d3f6"], [0.5, "#3987e5"], [1.0, "#104281"]]
PIN_COLOR = "#c8372d"
PIN_PENDING_COLOR = "#256abf"

IMAGE_TYPES = ["png", "jpg", "jpeg", "webp"]
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

PAGE_CSS = """
<style>
    header[data-testid="stHeader"] { display: none; }
    .block-container { padding: 0.5rem 1rem 1rem 1rem; max-width: 100%; }
    div[data-testid="stVerticalBlock"] { gap: 0.5rem; }
    .app-title { font-size: 1.25rem; font-weight: 700; line-height: 2.4rem; white-space: nowrap; }
    .app-summary { line-height: 2.4rem; color: #6b6a66; white-space: nowrap; }
    .app-summary b { color: #1f1f1e; }
    /* 세계지도를 화면 높이에 맞춰 가득 채운다 */
    .st-key-world_map_box {
        flex: 0 0 calc(100vh - 4.6rem) !important;
        height: calc(100vh - 4.6rem) !important;
        min-height: 360px;
    }
</style>
"""


# ---------------------------------------------------------------- 상태·이동


def init_state():
    defaults = {
        "admin": False,
        "country": None,
        "map_nonce": 0,
        "click_nonce": 0,
        "pending_point": None,
        "last_click_time": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def go_country(iso3):
    st.session_state.country = iso3
    st.session_state.pending_point = None
    st.session_state.click_nonce += 1


def go_world():
    st.session_state.country = None
    st.session_state.pending_point = None
    # 지도·선택 위젯의 키를 바꿔 이전 선택이 남아 다시 이동하는 일을 막는다
    st.session_state.map_nonce += 1


def leave_admin():
    st.session_state.admin = False


def render_mode_control():
    if st.session_state.admin:
        with st.popover("🔑 관리자 모드", width="stretch"):
            st.caption("국가별 확대 이미지·필요 깃발 수·시도 목록을 관리합니다.")
            if ADMIN_PASSWORD == "admin":
                st.warning("기본 비밀번호를 사용 중입니다. NCMN_ADMIN_PASSWORD 환경변수로 변경하세요.")
            st.button("사용자 모드로 전환", on_click=leave_admin, width="stretch")
        return
    with st.popover("👤 사용자 모드", width="stretch"):
        with st.form("admin_login", border=False):
            password = st.text_input("관리자 비밀번호", type="password")
            submitted = st.form_submit_button("관리자 모드로 전환", width="stretch")
        if submitted:
            if hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
                st.session_state.admin = True
                st.session_state.pending_point = None
                st.rerun()
            st.error("비밀번호가 올바르지 않습니다.")


# ---------------------------------------------------------------- 세계지도


def build_world_figure(totals, admin):
    by_iso = totals.set_index("iso3")
    isos = [iso3 for iso3, _, _ in COUNTRIES]
    flags = [int(by_iso["flags"].get(iso3, 0)) for iso3 in isos]
    required = [int(by_iso["required_flags"].get(iso3, 0)) for iso3 in isos]
    max_flags = max(flags)

    # 0개인 국가는 중립 회색, 1개 이상은 파란색 단계로 칠한다
    epsilon = 0.5 / max(max_flags, 1)
    colorscale = [[0.0, LAND_EMPTY], [epsilon, LAND_EMPTY]] + [
        [max(stop, epsilon), color] for stop, color in FLAG_SCALE
    ]

    fig = go.Figure(
        go.Choropleth(
            locations=isos,
            z=flags,
            locationmode="ISO-3",
            zmin=0,
            zmax=max(max_flags, 1),
            colorscale=colorscale,
            marker_line_color=SURFACE,
            marker_line_width=0.6,
            customdata=[[iso3, COUNTRY_NAMES[iso3], required[i]] for i, iso3 in enumerate(isos)],
            hovertemplate=(
                "<b>%{customdata[1]}</b><br>등록 깃발 %{z:,}개 · 필요 %{customdata[2]:,}개"
                "<extra></extra>"
            ),
            unselected_marker_opacity=1,
            showscale=max_flags > 0,
            colorbar=dict(
                orientation="h", x=0.015, xanchor="left", y=0.03, yanchor="bottom",
                len=0.16, thickness=10, outlinewidth=0,
                title=dict(text="등록 깃발 수", side="top", font=dict(size=12, color=INK_MUTED)),
                tickfont=dict(size=11, color=INK_MUTED),
            ),
        )
    )

    # 깃발이 등록된 국가 위에 합계 배지를 올린다
    flagged = [(iso3, n) for iso3, n in zip(isos, flags) if n > 0]
    if flagged:
        fig.add_trace(
            go.Scattergeo(
                locations=[iso3 for iso3, _ in flagged],
                locationmode="ISO-3",
                mode="markers+text",
                text=[f"{n:,}" for _, n in flagged],
                textfont=dict(size=12, color=INK),
                marker=dict(
                    size=[16 + 6 * len(f"{n:,}") for _, n in flagged],
                    color=SURFACE,
                    opacity=1,
                    line=dict(color=INK, width=1),
                ),
                customdata=[[iso3, COUNTRY_NAMES[iso3]] for iso3, _ in flagged],
                hovertemplate="<b>%{customdata[1]}</b><br>등록 깃발 %{text}개<extra></extra>",
                unselected=dict(marker_opacity=1, textfont_color=INK),
                showlegend=False,
            )
        )

    hint = "국가를 클릭하면 확대 화면으로 이동합니다"
    if admin:
        hint = "관리자 모드 · 국가를 클릭해 확대 이미지와 필요 깃발 수를 설정하세요"
    fig.add_annotation(
        text=hint, x=0.015, y=0.03, xref="paper", yref="paper",
        xanchor="left", yanchor="bottom", showarrow=False,
        yshift=56 if max_flags > 0 else 0,  # 범례가 있으면 그 위로 올린다
        font=dict(size=13, color=INK_MUTED),
    )
    fig.update_geos(
        projection_type="equirectangular",
        projection_rotation_lon=MAP_CENTER_LON,
        lonaxis_range=[MAP_CENTER_LON - 180, MAP_CENTER_LON + 180],
        lataxis_range=MAP_LAT_RANGE,
        resolution=50,
        showframe=False,
        showcoastlines=False,
        showcountries=False,
        showland=True,
        landcolor=LAND_EMPTY,
        showocean=True,
        oceancolor=SURFACE,
        showlakes=False,
        bgcolor=SURFACE,
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor=SURFACE,
        clickmode="event+select",
        dragmode="pan",
        autosize=True,
        hoverlabel=dict(bgcolor="white", font=dict(color=INK, size=13)),
    )
    return fig


def clicked_country(event):
    points = event.selection.points if event and event.selection else []
    for point in points:
        iso3 = point.get("location")
        if not iso3 and point.get("customdata"):
            iso3 = point["customdata"][0]
        if iso3 in COUNTRY_NAMES:
            return iso3
    return None


def world_table(totals):
    table = totals[(totals["required_flags"] > 0) | (totals["flags"] > 0)].copy()
    table["국가"] = table["iso3"].map(COUNTRY_NAMES)
    table["달성률"] = (table["flags"] / table["required_flags"]).where(table["required_flags"] > 0)
    table = table.sort_values(["flags", "required_flags"], ascending=False)
    return table.rename(
        columns={"required_flags": "필요 깃발 수", "flags": "등록 깃발 수", "entries": "등록 건수"}
    )[["국가", "필요 깃발 수", "등록 깃발 수", "등록 건수", "달성률"]]


def render_world():
    admin = st.session_state.admin
    nonce = st.session_state.map_nonce
    totals = db.country_totals()
    total_flags = int(totals["flags"].sum())
    total_required = int(totals["required_flags"].sum())
    flagged_countries = int((totals["flags"] > 0).sum())

    title_col, summary_col, select_col, mode_col = st.columns(
        [2.4, 4, 2.6, 1.6], vertical_alignment="center"
    )
    title_col.markdown('<div class="app-title">🚩 NCMN Global Flags</div>', unsafe_allow_html=True)
    summary_col.markdown(
        f'<div class="app-summary">등록 깃발 <b>{total_flags:,}</b>개 · '
        f"필요 <b>{total_required:,}</b>개 · 등록 국가 <b>{flagged_countries}</b>개국</div>",
        unsafe_allow_html=True,
    )
    with select_col:
        selected = st.selectbox(
            "국가 선택",
            [iso3 for iso3, _, _ in COUNTRIES],
            index=None,
            format_func=lambda iso3: f"{COUNTRY_NAMES[iso3]} ({COUNTRY_NAMES_EN[iso3]})",
            placeholder="국가 검색·선택",
            label_visibility="collapsed",
            key=f"country_select_{nonce}",
        )
    with mode_col:
        render_mode_control()
    if selected:
        go_country(selected)
        st.rerun()

    with st.container(key="world_map_box"):
        event = st.plotly_chart(
            build_world_figure(totals, admin),
            key=f"world_map_{nonce}",
            on_select="rerun",
            selection_mode="points",
            height="stretch",
            theme=None,
            config={"displayModeBar": False, "scrollZoom": True, "responsive": True},
        )
    iso3 = clicked_country(event)
    if iso3:
        go_country(iso3)
        st.rerun()

    st.subheader("국가별 깃발 현황")
    table = world_table(totals)
    if table.empty:
        st.caption("아직 등록된 깃발이 없습니다. 지도에서 국가를 선택해 등록을 시작하세요.")
        return
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "필요 깃발 수": st.column_config.NumberColumn(format="localized"),
            "등록 깃발 수": st.column_config.NumberColumn(format="localized"),
            "달성률": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
        },
    )
    if admin:
        export = db.list_registrations()
        export.insert(1, "country", export["iso3"].map(COUNTRY_NAMES))
        st.download_button(
            "전체 등록 정보 CSV 내려받기",
            export.to_csv(index=False).encode("utf-8-sig"),
            file_name="ncmn_flag_registrations.csv",
            mime="text/csv",
        )


# ---------------------------------------------------------------- 국가 확대 화면


@st.cache_data(show_spinner=False)
def load_image(path):
    return Image.open(path).convert("RGB")


def draw_pins(base, registrations, pending=None):
    """확대 이미지 위에 등록 위치(깃발 수)와 새로 찍은 위치(+)를 그린다."""
    image = base.copy()
    draw = ImageDraw.Draw(image)
    scale = max(image.width, image.height) / 1000
    font = ImageFont.load_default(size=max(10, round(18 * scale)))

    def pin(x, y, label, color):
        px, py = x * image.width, y * image.height
        text_width = draw.textlength(label, font=font)
        radius = max(16 * scale, text_width / 2 + 6 * scale)
        cy = py - radius - 9 * scale
        outline = max(1, round(2 * scale))
        draw.polygon(
            [(px, py), (px - radius * 0.55, cy + radius * 0.6), (px + radius * 0.55, cy + radius * 0.6)],
            fill=color,
        )
        draw.ellipse(
            [px - radius, cy - radius, px + radius, cy + radius],
            fill=color, outline="white", width=outline,
        )
        draw.text((px, cy), label, fill="white", font=font, anchor="mm")
        dot = 2.5 * scale
        draw.ellipse([px - dot, py - dot, px + dot, py + dot], fill="white", outline=color)

    for row in registrations.itertuples():
        pin(row.x, row.y, f"{row.flag_count:,}", PIN_COLOR)
    if pending:
        pin(pending[0], pending[1], "+", PIN_PENDING_COLOR)
    return image


def render_country_image(iso3, country, registrations, admin):
    image_path = db.resolve_path(country["image_path"])
    if image_path is None:
        if admin:
            st.info("확대 이미지가 아직 없습니다. 오른쪽 국가 설정에서 이미지를 추가하세요.")
        else:
            st.info(
                "이 국가의 확대 이미지가 아직 등록되지 않았습니다. "
                "관리자 모드에서 확대 이미지를 추가하면 위치를 클릭해 깃발을 등록할 수 있습니다."
            )
        return
    annotated = draw_pins(load_image(str(image_path)), registrations, st.session_state.pending_point)
    if admin:
        st.image(annotated, width="stretch")
        st.caption("빨간 핀의 숫자는 해당 위치에 등록된 깃발 수입니다.")
        return
    click = streamlit_image_coordinates(
        annotated,
        key=f"country_click_{iso3}_{st.session_state.click_nonce}",
        width="stretch",
        image_format="JPEG",
        jpeg_quality=88,
        cursor="crosshair",
    )
    st.caption("이미지에서 깃발을 등록할 위치를 클릭하세요. 빨간 핀의 숫자는 등록된 깃발 수입니다.")
    if click and click["unix_time"] != st.session_state.last_click_time and click["width"]:
        st.session_state.last_click_time = click["unix_time"]
        st.session_state.pending_point = (
            min(max(click["x"] / click["width"], 0), 1),
            min(max(click["y"] / click["height"], 0), 1),
        )
        st.rerun()


def render_admin_settings(iso3, country):
    st.subheader("국가 설정")
    with st.form(f"country_settings_{iso3}_{st.session_state.click_nonce}"):
        uploaded = st.file_uploader(
            "국가 확대 이미지" + (" (교체)" if country["image_path"] else ""), type=IMAGE_TYPES
        )
        required_flags = st.number_input(
            "필요 깃발 수", min_value=0, step=1, value=int(country["required_flags"])
        )
        provinces_text = st.text_area(
            "시도 목록 (한 줄에 하나씩)", value="\n".join(country["provinces"]), height=220
        )
        submitted = st.form_submit_button("저장", type="primary", width="stretch")
    if submitted:
        if uploaded is not None:
            db.save_country_image(iso3, uploaded)
        db.save_country_settings(iso3, required_flags, provinces_text.splitlines())
        st.session_state.click_nonce += 1
        st.toast("국가 설정을 저장했습니다.")
        st.rerun()


def render_registration_form(iso3, country):
    st.subheader("깃발 등록")
    pending = st.session_state.pending_point
    if not country["image_path"]:
        st.caption("확대 이미지가 등록된 뒤에 깃발을 등록할 수 있습니다.")
        return
    if pending is None:
        st.info("왼쪽 이미지에서 위치를 클릭하면 등록 양식이 열립니다.")
        return
    with st.form(f"registration_{iso3}_{st.session_state.click_nonce}"):
        st.caption("파란 + 핀 위치에 등록합니다. * 표시는 필수 항목입니다.")
        province = st.selectbox(
            "시도 *", country["provinces"], index=None, accept_new_options=True,
            placeholder="선택하거나 새 시도명을 입력",
        )
        left, right = st.columns(2)
        flag_count = left.number_input("현재 깃발 수 *", min_value=0, step=1, value=1)
        city = right.text_input("도시명")
        address = st.text_input("주소")
        organization = st.text_input("기관명")
        left, right = st.columns(2)
        contact_name = left.text_input("현지 연락자명")
        contact_phone = right.text_input("연락처")
        email = left.text_input("이메일주소")
        registrant_name = right.text_input("등록자명 *")
        photo = st.file_uploader("등록 이미지", type=IMAGE_TYPES)
        submit_col, cancel_col = st.columns(2)
        submitted = submit_col.form_submit_button("등록", type="primary", width="stretch")
        cancelled = cancel_col.form_submit_button("취소", width="stretch")

    if cancelled:
        st.session_state.pending_point = None
        st.session_state.click_nonce += 1
        st.rerun()
    if not submitted:
        return
    province = (province or "").strip()
    errors = []
    if not province:
        errors.append("시도를 선택하거나 입력하세요.")
    if not registrant_name.strip():
        errors.append("등록자명을 입력하세요.")
    if email.strip() and not EMAIL_PATTERN.match(email.strip()):
        errors.append("이메일주소 형식이 올바르지 않습니다.")
    if errors:
        for message in errors:
            st.error(message)
        return
    db.add_registration(
        iso3, pending[0], pending[1], province, flag_count, photo=photo,
        city=city.strip(), address=address.strip(), organization=organization.strip(),
        contact_name=contact_name.strip(), contact_phone=contact_phone.strip(),
        email=email.strip(), registrant_name=registrant_name.strip(),
    )
    st.session_state.pending_point = None
    st.session_state.click_nonce += 1
    st.toast("깃발을 등록했습니다.")
    st.rerun()


def render_province_table(iso3):
    st.subheader("시도별 깃발 수")
    totals = db.province_totals(iso3)
    if totals.empty:
        st.caption("시도 목록이 비어 있습니다.")
        return
    total_row = pd.DataFrame(
        [{"province": "합계", "entries": totals["entries"].sum(), "flags": totals["flags"].sum()}]
    )
    table = pd.concat([totals, total_row], ignore_index=True).rename(
        columns={"province": "시도", "entries": "등록 건수", "flags": "깃발 수"}
    )
    st.dataframe(table, hide_index=True, width="stretch", height=35 * (len(table) + 1) + 3)


def render_registrations(registrations, admin):
    st.subheader(f"등록 목록 ({len(registrations)}건)")
    if registrations.empty:
        st.caption("아직 등록된 깃발이 없습니다.")
        return
    columns = {
        "id": "번호", "province": "시도", "city": "도시명", "address": "주소",
        "organization": "기관명", "flag_count": "깃발 수", "contact_name": "현지 연락자명",
        "contact_phone": "연락처", "email": "이메일주소", "registrant_name": "등록자명",
        "created_at": "등록일시",
    }
    st.dataframe(
        registrations[list(columns)].rename(columns=columns), hide_index=True, width="stretch"
    )
    for row in registrations.itertuples():
        title = f"#{row.id} · {row.province} {row.city or ''} · 깃발 {row.flag_count:,}개 · {row.registrant_name}"
        with st.expander(title):
            photo_col, info_col = st.columns([1, 2])
            photo_path = db.resolve_path(row.photo_path)
            if photo_path:
                photo_col.image(str(photo_path), width="stretch")
            else:
                photo_col.caption("등록 이미지 없음")
            info_col.markdown(
                f"- **기관명**: {row.organization or '-'}\n"
                f"- **주소**: {row.address or '-'}\n"
                f"- **현지 연락자**: {row.contact_name or '-'} / {row.contact_phone or '-'}\n"
                f"- **이메일주소**: {row.email or '-'}\n"
                f"- **등록자명**: {row.registrant_name or '-'} ({row.created_at})"
            )
            if admin:
                with info_col.popover("삭제"):
                    st.write("이 등록 정보와 이미지를 삭제합니다. 되돌릴 수 없습니다.")
                    if st.button("삭제 확인", key=f"delete_{row.id}", type="primary"):
                        db.delete_registration(row.id)
                        st.toast(f"#{row.id} 등록을 삭제했습니다.")
                        st.rerun()


def render_country(iso3):
    admin = st.session_state.admin
    country = db.get_country(iso3)
    registrations = db.list_registrations(iso3)
    registered = int(registrations["flag_count"].sum())
    required = int(country["required_flags"])

    back_col, title_col, mode_col = st.columns([1.4, 7, 1.6], vertical_alignment="center")
    back_col.button("← 세계지도", on_click=go_world, width="stretch")
    title_col.markdown(
        f'<div class="app-title">🚩 {COUNTRY_NAMES[iso3]} '
        f'<span style="font-weight:400;color:{INK_MUTED}">{COUNTRY_NAMES_EN[iso3]}</span></div>',
        unsafe_allow_html=True,
    )
    with mode_col:
        render_mode_control()

    metric_cols = st.columns(4)
    metric_cols[0].metric("필요 깃발 수", f"{required:,}")
    metric_cols[1].metric("등록 깃발 수", f"{registered:,}")
    metric_cols[2].metric("남은 깃발 수", f"{max(required - registered, 0):,}")
    metric_cols[3].metric("등록 건수", f"{len(registrations):,}")

    image_col, side_col = st.columns([3, 2], gap="medium")
    with image_col:
        render_country_image(iso3, country, registrations, admin)
    with side_col:
        if admin:
            render_admin_settings(iso3, country)
        else:
            render_registration_form(iso3, country)
        render_province_table(iso3)

    render_registrations(registrations, admin)


def main():
    st.set_page_config(
        page_title="NCMN Global Flags",
        page_icon="🚩",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(PAGE_CSS, unsafe_allow_html=True)
    db.init_db()
    init_state()
    if st.session_state.country:
        render_country(st.session_state.country)
    else:
        render_world()


main()
