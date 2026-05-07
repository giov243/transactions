import streamlit as st
import pandas as pd
import sqlite3
import json
import re
from google.oauth2.service_account import Credentials
import gspread
from google import genai
from google.genai import types

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
st.set_page_config(page_title="💳 Transazioni", layout="wide")

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
DB_PATH = "transactions.db"
TABLE_NAME = "transactions"


# ─────────────────────────────────────────────
# GOOGLE SHEETS
# ─────────────────────────────────────────────
@st.cache_resource
def get_gsheet_client():
    creds_dict = json.loads(st.secrets["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def load_from_sheets(sheet_url: str) -> pd.DataFrame:
    client = get_gsheet_client()
    sheet = client.open_by_url(sheet_url).sheet1
    records = sheet.get_all_records()
    return pd.DataFrame(records)


def save_to_sheets(df: pd.DataFrame, sheet_url: str):
    client = get_gsheet_client()
    sheet = client.open_by_url(sheet_url).sheet1
    sheet.clear()
    values = [df.columns.tolist()] + df.astype(str).values.tolist()
    sheet.update(values)


# ─────────────────────────────────────────────
# SQLITE
# ─────────────────────────────────────────────
def get_conn():
    return sqlite3.connect(DB_PATH)


def load_to_sqlite(df: pd.DataFrame):
    with get_conn() as conn:
        df.to_sql(TABLE_NAME, conn, if_exists="replace", index=False)


def load_from_sqlite() -> pd.DataFrame:
    try:
        with get_conn() as conn:
            return pd.read_sql(f"SELECT * FROM {TABLE_NAME}", conn)
    except Exception:
        return pd.DataFrame()


def get_schema() -> str:
    try:
        with get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({TABLE_NAME})")
            cols = cursor.fetchall()
        return ", ".join([f"{c[1]} ({c[2]})" for c in cols])
    except Exception:
        return "nessuna colonna trovata"


def run_sql(query: str):
    """Esegue una query SQL e restituisce (DataFrame | None, errore | None)."""
    try:
        with get_conn() as conn:
            df = pd.read_sql(query, conn)
        return df, None
    except Exception as e:
        return None, str(e)


def apply_sql_write(query: str):
    """Esegue INSERT / UPDATE / DELETE e restituisce (rowcount, errore)."""
    try:
        with get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            conn.commit()
            return cursor.rowcount, None
    except Exception as e:
        return 0, str(e)


# ─────────────────────────────────────────────
# TEXT-TO-SQL con Gemini
# ─────────────────────────────────────────────
def text_to_sql(question: str) -> str:
    schema = get_schema()
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

    prompt = f"""Sei un esperto SQL...
Domanda: {question}"""

    response = genai.models.generate_content(
        model="gemini-1.5",
        contents=[types.Part.from_text(text=prompt)],
        config=types.GenerateContentConfig(
            temperature=0,
            top_p=0.95,
            top_k=20,
        ),
    )

    sql = response.candidates[0].content[0].text.strip()
    sql = re.sub(r"^```sql|^```|```$", "", sql, flags=re.MULTILINE).strip()
    return sql


# ─────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────
if "df" not in st.session_state:
    st.session_state.df = load_from_sqlite()
if "sql_query" not in st.session_state:
    st.session_state.sql_query = ""
if "sql_result" not in st.session_state:
    st.session_state.sql_result = None


# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
st.title("💳 Gestione Transazioni")
st.caption("Sync Google Sheets ↔ SQLite · Text-to-SQL con Gemini")

# ── SIDEBAR ──────────────────────────────────
with st.sidebar:
    st.header("⚙️ Impostazioni")
    sheet_url = st.text_input(
        "URL Google Sheet",
        placeholder="https://docs.google.com/spreadsheets/d/...",
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("⬇️ Carica da Sheets", use_container_width=True):
            if not sheet_url:
                st.error("Inserisci l'URL del foglio.")
            else:
                with st.spinner("Carico da Google Sheets..."):
                    try:
                        df = load_from_sheets(sheet_url)
                        load_to_sqlite(df)
                        st.session_state.df = df
                        st.success(f"✅ {len(df)} righe caricate!")
                    except Exception as e:
                        st.error(f"Errore: {e}")

    with col2:
        if st.button("⬆️ Salva su Sheets", use_container_width=True):
            if not sheet_url:
                st.error("Inserisci l'URL del foglio.")
            elif st.session_state.df.empty:
                st.warning("Nessun dato da salvare.")
            else:
                with st.spinner("Salvo su Google Sheets..."):
                    try:
                        save_to_sheets(st.session_state.df, sheet_url)
                        st.success("✅ Dati salvati!")
                    except Exception as e:
                        st.error(f"Errore: {e}")

    st.divider()
    st.markdown("**Schema tabella:**")
    schema = get_schema()
    st.code(schema if schema else "Nessuna tabella ancora", language="text")

# ── TABELLA EDITABILE ─────────────────────────
st.subheader("📊 Tabella Transazioni")

if st.session_state.df.empty:
    st.info("Nessun dato. Carica il tuo Google Sheet dalla barra laterale ←")
else:
    edited_df = st.data_editor(
        st.session_state.df,
        use_container_width=True,
        num_rows="dynamic",
        key="data_editor",
    )

    if st.button("💾 Salva modifiche nel DB", type="primary"):
        load_to_sqlite(edited_df)
        st.session_state.df = edited_df
        st.success("✅ Modifiche salvate nel database locale!")

st.divider()

# ── TEXT-TO-SQL ───────────────────────────────
st.subheader("🤖 Text-to-SQL con Gemini")
st.caption("Scrivi in italiano cosa vuoi cercare o modificare nei tuoi dati.")

question = st.text_area(
    "La tua domanda",
    placeholder='Es: "Mostrami tutte le spese di marzo superiori a 50€"\n     "Aggiorna la categoria di Netflix a Abbonamenti"',
    height=80,
)

if st.button("✨ Genera ed esegui SQL", type="primary", disabled=not question):
    with st.spinner("Gemini sta generando la query..."):
        try:
            sql = text_to_sql(question)
            st.session_state.sql_query = sql

            sql_upper = sql.strip().upper()
            if sql_upper.startswith("SELECT"):
                result_df, err = run_sql(sql)
                if err:
                    st.session_state.sql_result = ("error", err)
                else:
                    st.session_state.sql_result = ("select", result_df)
            else:
                rowcount, err = apply_sql_write(sql)
                if err:
                    st.session_state.sql_result = ("error", err)
                else:
                    st.session_state.df = load_from_sqlite()
                    st.session_state.sql_result = ("write", rowcount)
        except Exception as e:
            st.session_state.sql_result = ("error", str(e))

if st.session_state.sql_query:
    st.markdown("**Query generata:**")
    st.code(st.session_state.sql_query, language="sql")

if st.session_state.sql_result:
    kind, payload = st.session_state.sql_result
    if kind == "select":
        st.markdown("**Risultati:**")
        st.dataframe(payload, use_container_width=True)
    elif kind == "write":
        st.success(f"✅ Query eseguita! Righe modificate: {payload}")
        st.info("La tabella in alto è stata aggiornata automaticamente.")
    elif kind == "error":
        st.error(f"❌ Errore SQL: {payload}")