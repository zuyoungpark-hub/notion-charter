import streamlit as st
import requests
import pandas as pd
import altair as alt
import os
from streamlit.errors import StreamlitSecretNotFoundError

def load_local_env(path=".env"):
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def read_secret(name: str):
    env_val = os.getenv(name)
    if env_val:
        return env_val
    try:
        return st.secrets[name]
    except (StreamlitSecretNotFoundError, KeyError):
        return None


def extract_date_value(props):
    # 1) 기존 키 우선
    date_prop = props.get("date")
    if isinstance(date_prop, dict) and date_prop.get("type") == "date":
        date_obj = date_prop.get("date")
        if date_obj:
            return date_obj.get("start")

    # 2) Notion 속성 중 type=date 인 첫 필드 자동 탐색
    for prop in props.values():
        if isinstance(prop, dict) and prop.get("type") == "date":
            date_obj = prop.get("date")
            if date_obj:
                return date_obj.get("start")
    return None


def fetch_notion_rows(headers, database_id):
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    try:
        res = requests.post(url, headers=headers, json={"page_size": 100}, timeout=30)
    except requests.RequestException as e:
        st.error(f"Notion API 연결 실패: {e}")
        return None

    try:
        payload = res.json()
    except ValueError:
        st.error(f"Notion API 응답(JSON 아님): HTTP {res.status_code}")
        st.text(res.text[:500])
        return None

    if res.status_code != 200:
        st.error(f"Notion API 오류: HTTP {res.status_code}")
        st.json(payload)
        return None

    data = payload.get("results")
    if data is None:
        st.error("응답에 results 필드가 없습니다.")
        st.json(payload)
        return None

    rows = []
    for item in data:
        props = item.get("properties", {})
        date_value = extract_date_value(props)

        rows.append(
            {
                "date": date_value,
                "기준MAX": (props.get("기준MAX") or {}).get("number"),
                "수주설계": (props.get("수주설계") or {}).get("number"),
                "조업실적": (props.get("조업실적") or {}).get("number"),
            }
        )
    return rows


def main():
    load_local_env(".env")
    notion_token = read_secret("NOTION_TOKEN")
    database_id = read_secret("DATABASE_ID")

    st.markdown("### 연연주비 Dashboard")

    if not notion_token or not database_id:
        st.error("환경변수가 없습니다: NOTION_TOKEN, DATABASE_ID")
        st.info("로컬 실행 전 `export NOTION_TOKEN=...`와 `export DATABASE_ID=...`를 설정하거나 `.streamlit/secrets.toml`을 사용하세요.")
        return

    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    rows = fetch_notion_rows(headers, database_id)
    if rows is None:
        return

    df = pd.DataFrame(rows)
    if df.empty:
        st.warning("조회된 데이터가 없습니다.")
        return

    if "date" not in df.columns:
        st.warning("date 컬럼이 없습니다. Notion의 날짜 속성(type=date)을 확인하세요.")
        st.dataframe(df.head(5))
        return

    raw_date_count = df["date"].notna().sum()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    if df.empty:
        st.warning("유효한 date 데이터가 없어 그래프를 표시할 수 없습니다. (날짜 원본 값 개수: %d)" % raw_date_count)
        return

    chart_cols = ["기준MAX", "수주설계", "조업실적"]
    daily_avg_df = (
        df.groupby("date", as_index=False)[chart_cols]
        .mean(numeric_only=True)
        .sort_values("date")
    )
    x_domain = [
        daily_avg_df["date"].min() - pd.Timedelta(days=1),
        daily_avg_df["date"].max() + pd.Timedelta(days=1),
    ]

    plot_df = daily_avg_df.melt(id_vars="date", value_vars=chart_cols, var_name="지표", value_name="평균값")

    color_scale = alt.Scale(
        domain=["기준MAX", "수주설계", "조업실적"],
        range=["#1f77b4", "#2ca02c", "#ff7f0e"],
    )

    base = alt.Chart(plot_df).encode(
        x=alt.X(
            "date:T",
            title="날짜",
            scale=alt.Scale(domain=x_domain),
            axis=alt.Axis(format="%Y-%m-%d", labelAngle=-35, grid=True),
        ),
        y=alt.Y("평균값:Q", title="일자별 평균값"),
        color=alt.Color(
            "지표:N",
            title="지표",
            scale=color_scale,
            legend=alt.Legend(orient="bottom"),
        ),
        tooltip=[
            alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
            alt.Tooltip("지표:N", title="지표"),
            alt.Tooltip("평균값:Q", title="평균값", format=".2f"),
        ],
    )

    line_layer = base.mark_line(strokeWidth=3, interpolate="monotone")
    point_layer = base.mark_point(size=55, filled=True)
    label_layer = base.mark_text(
        dy=-10,
        fontSize=16,
        fontWeight="bold",
    ).encode(text=alt.Text("평균값:Q", format=".1f"))

    st.altair_chart(
        (line_layer + point_layer + label_layer).properties(height=420).interactive(),
        use_container_width=True,
    )


main()
