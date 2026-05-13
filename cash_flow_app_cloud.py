import streamlit as st
import psycopg
import pandas as pd

st.set_page_config(page_title="Cash Flow Cloud", layout="wide")

st.title("Cash Flow Cloud")

DATABASE_URL = st.secrets["DATABASE_URL"]

def get_conn():
    return psycopg.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    id SERIAL PRIMARY KEY,
                    company_name TEXT NOT NULL
                )
            """)
        conn.commit()
    finally:
        conn.close()

init_db()

st.success("Database connected successfully.")

st.header("Companies")

new_company = st.text_input("Add Company")

if st.button("Save Company"):
    if new_company.strip():
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO companies (company_name) VALUES (%s)",
                    (new_company.strip(),)
                )
            conn.commit()
        finally:
            conn.close()
        st.success("Company added.")
        st.rerun()

conn = get_conn()
try:
    df = pd.read_sql("SELECT * FROM companies ORDER BY id", conn)
finally:
    conn.close()

st.dataframe(df, use_container_width=True)
