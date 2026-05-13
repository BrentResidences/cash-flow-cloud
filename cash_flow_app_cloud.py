import streamlit as st
import pandas as pd
import psycopg
from pathlib import Path
from datetime import date, timedelta
import io
import hashlib

st.set_page_config(page_title="Cash Flow Management App", layout="wide")

DATA_DIR = Path("cloud_database")

COMPANIES_FILE = DATA_DIR / "companies.csv"
FUNDING_FILE = DATA_DIR / "funding.csv"
PROJECTS_FILE = DATA_DIR / "projects.csv"
LABOR_FILE = DATA_DIR / "labor.csv"
LABOR_BUDGET_FILE = DATA_DIR / "labor_budget.csv"
CASHFLOW_FILE = DATA_DIR / "cashflow.csv"
SETTINGS_FILE = DATA_DIR / "settings.csv"
USERS_FILE = DATA_DIR / "users.csv"

STATUSES = ["Active", "Planning", "Completed", "Inactive", "Cancelled"]
RECORD_TYPES = ["Project", "Work Order"]
CASH_OUT_CATEGORIES = ["Materials", "Labor", "Subcontractor", "Equipment", "Permits", "Other"]
USER_COLUMNS = ["Username", "Password Hash", "Active", "Created On"]

COMPANY_COLUMNS = ["Company Name", "Active", "Notes"]
FUNDING_COLUMNS = ["Company Name", "Department", "Project", "Category", "Work Order ID", "Funding Amount", "Funding Source", "Date", "Active", "Notes"]
PROJECT_COLUMNS = ["Company Name", "Department", "Project", "Category", "Work Order ID", "Work Item", "Record Type", "Status", "Start Date", "End Date", "Notes"]
LABOR_COLUMNS = ["Company Name", "Department", "Project", "Category", "Work Order ID", "Work Item", "Worker Name", "Role", "Hourly Rate", "Hours Per Week", "Start Date", "End Date", "Active"]
LABOR_BUDGET_COLUMNS = ["Company Name", "Department", "Project", "Category", "Work Order ID", "Work Item", "Total Labor Amount", "Start Date", "End Date", "Spread Method", "Active", "Notes"]
CASHFLOW_COLUMNS = ["Type", "Company Name", "Department", "Project", "Category", "Work Order ID", "Work Item", "Vendor/Source", "Description", "Amount", "Date", "Spread Method", "Start Date", "End Date"]


def get_database_url():
    if "DATABASE_URL" not in st.secrets:
        st.error("DATABASE_URL is missing from Streamlit Secrets.")
        st.stop()
    return st.secrets["DATABASE_URL"]


def get_conn():
    return psycopg.connect(get_database_url())


def qident(name):
    return '"' + str(name).replace('"', '""') + '"'


def clean_db_value(value):
    if pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, date)):
        return value.isoformat()
    return str(value)


def table_name_for(path):
    name = str(path)
    if name.endswith("companies.csv"):
        return "companies"
    if name.endswith("funding.csv"):
        return "funding"
    if name.endswith("projects.csv"):
        return "projects"
    if name.endswith("labor.csv"):
        return "labor"
    if name.endswith("labor_budget.csv"):
        return "labor_budget"
    if name.endswith("cashflow.csv"):
        return "cashflow"
    if name.endswith("settings.csv"):
        return "settings"
    if name.endswith("users.csv"):
        return "users"
    raise ValueError(f"No database table mapped for {path}")


def ensure_table(path, columns):
    table_name = table_name_for(path)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            column_sql = ", ".join([f"{qident(col)} TEXT" for col in columns])
            cur.execute(f"CREATE TABLE IF NOT EXISTS {qident(table_name)} ({column_sql});")
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = %s
            """, (table_name,))
            existing_cols = {row[0] for row in cur.fetchall()}
            for col in columns:
                if col not in existing_cols:
                    cur.execute(f"ALTER TABLE {qident(table_name)} ADD COLUMN {qident(col)} TEXT;")
        conn.commit()
    finally:
        conn.close()


def load_csv(path, columns):
    ensure_table(path, columns)
    table_name = table_name_for(path)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            col_sql = ", ".join([qident(col) for col in columns])
            cur.execute(f"SELECT {col_sql} FROM {qident(table_name)};")
            rows = cur.fetchall()
        df = pd.DataFrame(rows, columns=columns)
        for col in columns:
            if col not in df.columns:
                df[col] = ""
        return df[columns]
    finally:
        conn.close()


def save_csv(df, path):
    table_name = table_name_for(path)
    ensure_table(path, list(df.columns))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {qident(table_name)};")
            if not df.empty:
                columns = list(df.columns)
                col_sql = ", ".join([qident(col) for col in columns])
                placeholders = ", ".join(["%s"] * len(columns))
                rows = [tuple(clean_db_value(row[col]) for col in columns) for _, row in df.iterrows()]
                cur.executemany(f"INSERT INTO {qident(table_name)} ({col_sql}) VALUES ({placeholders});", rows)
        conn.commit()
    finally:
        conn.close()


def hash_password(password):
    return hashlib.sha256(str(password).encode("utf-8")).hexdigest()


def load_users():
    users = load_csv(USERS_FILE, USER_COLUMNS)
    if "Password Hash" in users.columns:
        users["Password Hash"] = users["Password Hash"].astype(str)
    return users


def save_users(users_df):
    save_csv(users_df, USERS_FILE)


def verify_login(username, password, users_df):
    if users_df.empty:
        return False
    match = users_df[(users_df["Username"].astype(str) == str(username).strip()) & (users_df["Active"].astype(str).str.strip().str.lower() == "yes")]
    if match.empty:
        return False
    return str(match.iloc[0]["Password Hash"]) == hash_password(password)


def require_login():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "logged_in_user" not in st.session_state:
        st.session_state.logged_in_user = ""

    users_df = load_users()

    if users_df.empty:
        st.title("Cash Flow Management App")
        st.subheader("Create First Login")
        st.info("No users exist yet. Create the first username and password to start using the app.")
        with st.form("create_first_user_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            submit_first_user = st.form_submit_button("Create First User")
            if submit_first_user:
                if not username.strip():
                    st.error("Username is required.")
                elif not password:
                    st.error("Password is required.")
                elif password != confirm_password:
                    st.error("Passwords do not match.")
                else:
                    new_user = pd.DataFrame([{"Username": username.strip(), "Password Hash": hash_password(password), "Active": "Yes", "Created On": pd.Timestamp(date.today())}])
                    save_users(new_user)
                    st.success("First user created. Please log in.")
                    st.rerun()
        st.stop()

    if st.session_state.authenticated:
        return

    st.title("Cash Flow Management App")
    st.subheader("Login")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        login_submit = st.form_submit_button("Login")
        if login_submit:
            if verify_login(username, password, users_df):
                st.session_state.authenticated = True
                st.session_state.logged_in_user = username.strip()
                st.rerun()
            else:
                st.error("Invalid username or password.")
    st.stop()


def to_datetime_safe(df, columns):
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def to_numeric_safe(df, columns):
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def get_setting(name, default_value="0"):
    settings = load_csv(SETTINGS_FILE, ["Setting", "Value"])
    match = settings[settings["Setting"] == name]
    if not match.empty:
        return str(match.iloc[0]["Value"])
    return default_value


def set_setting(name, value):
    settings = load_csv(SETTINGS_FILE, ["Setting", "Value"])
    settings = settings[settings["Setting"] != name]
    new_row = pd.DataFrame([{"Setting": name, "Value": str(value)}])
    settings = pd.concat([settings, new_row], ignore_index=True)
    save_csv(settings, SETTINGS_FILE)


def active_rows(df):
    if df.empty or "Active" not in df.columns:
        return df
    active = df[df["Active"].astype(str).str.strip().str.lower().isin(["yes", "true", "1"])]
    return active if not active.empty else df


def unique_nonblank(values):
    out = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if text and text.lower() != "nan" and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def company_options_list(companies_df):
    if companies_df.empty:
        return []
    return unique_nonblank(active_rows(companies_df)["Company Name"].tolist())


def department_options_list(projects_df, funding_df, selected_company=None):
    values = []
    for df in [projects_df, funding_df]:
        if df.empty or "Department" not in df.columns:
            continue
        work = df.copy()
        if selected_company and "Company Name" in work.columns:
            work = work[work["Company Name"].astype(str) == selected_company]
        values.extend(work["Department"].tolist())
    return unique_nonblank(values)


def project_options_list(projects_df, funding_df=None, selected_company=None, selected_department=None):
    values = []
    for df in [projects_df, funding_df if funding_df is not None else pd.DataFrame()]:
        if df.empty or "Project" not in df.columns:
            continue
        work = df.copy()
        if selected_company and "Company Name" in work.columns:
            work = work[work["Company Name"].astype(str) == selected_company]
        if selected_department and "Department" in work.columns:
            work = work[work["Department"].astype(str) == selected_department]
        values.extend(work["Project"].tolist())
    return unique_nonblank(values)


def category_options_list(projects_df, cashflow_df=None, funding_df=None, selected_company=None, selected_project=None):
    values = []
    for df in [projects_df, cashflow_df if cashflow_df is not None else pd.DataFrame(), funding_df if funding_df is not None else pd.DataFrame()]:
        if df.empty or "Category" not in df.columns:
            continue
        work = df.copy()
        if selected_company and "Company Name" in work.columns:
            work = work[work["Company Name"].astype(str) == selected_company]
        if selected_project and "Project" in work.columns:
            work = work[work["Project"].astype(str) == selected_project]
        values.extend(work["Category"].tolist())
    return unique_nonblank(values)


def work_order_options_list(projects_df, selected_company=None, selected_department=None, selected_project=None, selected_category=None):
    if projects_df.empty:
        return []
    work = projects_df.copy()
    if selected_company:
        work = work[work["Company Name"].astype(str) == selected_company]
    if selected_department:
        work = work[work["Department"].astype(str) == selected_department]
    if selected_project:
        work = work[work["Project"].astype(str) == selected_project]
    if selected_category:
        work = work[work["Category"].astype(str) == selected_category]
    labels = []
    for _, row in work.iterrows():
        wo = str(row.get("Work Order ID", "")).strip()
        item = str(row.get("Work Item", "")).strip()
        project = str(row.get("Project", "")).strip()
        if wo:
            labels.append(f"{wo} | {project} | {item}")
    return unique_nonblank(labels)


def parse_work_order_selection(selection):
    if " | " in selection:
        parts = selection.split(" | ")
        work_order_id = parts[0].strip() if len(parts) > 0 else ""
        project = parts[1].strip() if len(parts) > 1 else ""
        work_item = parts[2].strip() if len(parts) > 2 else ""
        return work_order_id, project, work_item
    return selection.strip(), "", ""


def get_work_order_row(projects_df, work_order_id, work_item=None):
    if projects_df.empty or not work_order_id:
        return None
    work = projects_df[projects_df["Work Order ID"].astype(str) == str(work_order_id)]
    if work_item:
        item_match = work[work["Work Item"].astype(str) == str(work_item)]
        if not item_match.empty:
            work = item_match
    if work.empty:
        return None
    return work.iloc[0]


def get_work_order_context(projects_df, work_order_id, work_item=None):
    row = get_work_order_row(projects_df, work_order_id, work_item)
    if row is None:
        return "", "", "", "", "", ""
    return (
        str(row.get("Company Name", "")).strip(),
        str(row.get("Department", "")).strip(),
        str(row.get("Project", "")).strip(),
        str(row.get("Category", "")).strip(),
        str(row.get("Work Order ID", "")).strip(),
        str(row.get("Work Item", "")).strip(),
    )


def get_funding_balance_for_scope(funding_df, selected_company=None, selected_department=None, selected_project=None, selected_category=None, selected_work_order_id=None):
    if funding_df.empty:
        return 0.0
    work = funding_df.copy()
    work["Funding Amount"] = pd.to_numeric(work["Funding Amount"], errors="coerce").fillna(0)
    if selected_company:
        work = work[work["Company Name"].astype(str) == selected_company]
    if selected_department:
        work = work[work["Department"].astype(str) == selected_department]
    if selected_project:
        work = work[work["Project"].astype(str) == selected_project]
    if selected_category:
        work = work[work["Category"].astype(str) == selected_category]
    if selected_work_order_id:
        work = work[work["Work Order ID"].astype(str) == selected_work_order_id]
    if "Active" in work.columns:
        work = work[work["Active"].astype(str).str.strip().str.lower().isin(["yes", "true", "1", ""])]
    return float(work["Funding Amount"].sum()) if not work.empty else 0.0


def filter_by_scope(df, selected_company=None, selected_department=None, selected_project=None, selected_category=None, selected_work_order_id=None):
    work = df.copy()
    if selected_company and "Company Name" in work.columns:
        work = work[work["Company Name"].astype(str) == selected_company]
    if selected_department and "Department" in work.columns:
        work = work[work["Department"].astype(str) == selected_department]
    if selected_project and "Project" in work.columns:
        work = work[work["Project"].astype(str) == selected_project]
    if selected_category and "Category" in work.columns:
        work = work[work["Category"].astype(str) == selected_category]
    if selected_work_order_id and "Work Order ID" in work.columns:
        work = work[work["Work Order ID"].astype(str) == selected_work_order_id]
    return work


def row_to_cashflow_dict(row, amount, entry_date):
    return {"Type": str(row.get("Type", "")).strip(), "Company Name": str(row.get("Company Name", "")).strip(), "Department": str(row.get("Department", "")).strip(), "Project": str(row.get("Project", "")).strip(), "Category": str(row.get("Category", "")).strip(), "Work Order ID": str(row.get("Work Order ID", "")).strip(), "Work Item": str(row.get("Work Item", "")).strip(), "Vendor/Source": str(row.get("Vendor/Source", "")).strip(), "Description": str(row.get("Description", "")).strip(), "Amount": amount, "Date": entry_date, "Spread Method": str(row.get("Spread Method", "")).strip(), "Start Date": row.get("Start Date", ""), "End Date": row.get("End Date", "")}


def build_labor_cash_flow(labor_df, today, six_months_out, selected_company=None, selected_department=None, selected_project=None, selected_category=None, selected_work_order_id=None):
    labor_rows = []
    if labor_df.empty:
        return pd.DataFrame(columns=CASHFLOW_COLUMNS)
    work = labor_df.copy()
    work = to_datetime_safe(work, ["Start Date", "End Date"])
    work = filter_by_scope(work, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    week_starts = pd.date_range(start=today, end=six_months_out, freq="W-MON")
    for _, row in work.iterrows():
        start = row["Start Date"]
        end = row["End Date"]
        if pd.isna(start) or pd.isna(end):
            continue
        if str(row.get("Active", "")).strip().lower() != "yes":
            continue
        hourly_rate = pd.to_numeric(pd.Series([row.get("Hourly Rate", 0)]), errors="coerce").fillna(0).iloc[0]
        hours_per_week = pd.to_numeric(pd.Series([row.get("Hours Per Week", 0)]), errors="coerce").fillna(0).iloc[0]
        weekly_cost = float(hourly_rate) * float(hours_per_week)
        for week in week_starts:
            if start <= week <= end:
                labor_rows.append({"Type": "Cash Out", "Company Name": str(row.get("Company Name", "")).strip(), "Department": str(row.get("Department", "")).strip(), "Project": str(row.get("Project", "")).strip(), "Category": str(row.get("Category", "")).strip() or "Labor", "Work Order ID": str(row.get("Work Order ID", "")).strip(), "Work Item": str(row.get("Work Item", "")).strip(), "Vendor/Source": str(row.get("Worker Name", "")).strip(), "Description": f"Weekly labor - {str(row.get('Role', '')).strip()}", "Amount": weekly_cost, "Date": week, "Spread Method": "", "Start Date": "", "End Date": ""})
    return pd.DataFrame(labor_rows, columns=CASHFLOW_COLUMNS)


def build_quick_labor_budget_cash_flow(labor_budget_df, today, six_months_out, selected_company=None, selected_department=None, selected_project=None, selected_category=None, selected_work_order_id=None):
    rows = []
    if labor_budget_df.empty:
        return pd.DataFrame(columns=CASHFLOW_COLUMNS)
    work = labor_budget_df.copy()
    work = to_datetime_safe(work, ["Start Date", "End Date"])
    work = filter_by_scope(work, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    week_starts = pd.date_range(start=today, end=six_months_out, freq="W-MON")
    for _, row in work.iterrows():
        start = row["Start Date"]
        end = row["End Date"]
        if pd.isna(start) or pd.isna(end):
            continue
        if str(row.get("Active", "")).strip().lower() != "yes":
            continue
        total_amount = pd.to_numeric(pd.Series([row.get("Total Labor Amount", 0)]), errors="coerce").fillna(0).iloc[0]
        if float(total_amount) <= 0:
            continue
        spread_method = str(row.get("Spread Method", "Even Weekly Spread")).strip()
        if spread_method == "One-Time Amount":
            if today <= start <= six_months_out:
                rows.append({"Type": "Cash Out", "Company Name": str(row.get("Company Name", "")).strip(), "Department": str(row.get("Department", "")).strip(), "Project": str(row.get("Project", "")).strip(), "Category": str(row.get("Category", "")).strip() or "Labor", "Work Order ID": str(row.get("Work Order ID", "")).strip(), "Work Item": str(row.get("Work Item", "")).strip(), "Vendor/Source": "Quick Labor Budget", "Description": f"Quick labor budget - {str(row.get('Notes', '')).strip()}".strip(" -"), "Amount": float(total_amount), "Date": start, "Spread Method": "", "Start Date": "", "End Date": ""})
        else:
            applicable_weeks = [week for week in week_starts if start <= week <= end]
            if not applicable_weeks:
                continue
            weekly_amount = float(total_amount) / len(applicable_weeks)
            for week in applicable_weeks:
                rows.append({"Type": "Cash Out", "Company Name": str(row.get("Company Name", "")).strip(), "Department": str(row.get("Department", "")).strip(), "Project": str(row.get("Project", "")).strip(), "Category": str(row.get("Category", "")).strip() or "Labor", "Work Order ID": str(row.get("Work Order ID", "")).strip(), "Work Item": str(row.get("Work Item", "")).strip(), "Vendor/Source": "Quick Labor Budget", "Description": "Quick labor budget - evenly spread", "Amount": weekly_amount, "Date": week, "Spread Method": "", "Start Date": "", "End Date": ""})
    return pd.DataFrame(rows, columns=CASHFLOW_COLUMNS)


def build_other_costs_cash_flow(cost_df, today, six_months_out, selected_company=None, selected_department=None, selected_project=None, selected_category=None, selected_work_order_id=None):
    rows = []
    if cost_df.empty:
        return pd.DataFrame(columns=CASHFLOW_COLUMNS)
    work = cost_df.copy()
    work = to_datetime_safe(work, ["Date", "Start Date", "End Date"])
    work = filter_by_scope(work, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    week_starts = pd.date_range(start=today, end=six_months_out, freq="W-MON")
    for _, row in work.iterrows():
        if str(row.get("Type", "")).strip() != "Cash Out":
            continue
        amount = pd.to_numeric(pd.Series([row.get("Amount", 0)]), errors="coerce").fillna(0).iloc[0]
        if float(amount) <= 0:
            continue
        spread_method = str(row.get("Spread Method", "One-Time Amount")).strip()
        if spread_method == "Even Weekly Spread":
            start = row.get("Start Date")
            end = row.get("End Date")
            if pd.isna(start) or pd.isna(end) or end < start:
                entry_date = pd.to_datetime(row.get("Date"), errors="coerce")
                if pd.isna(entry_date):
                    continue
                if today <= entry_date <= six_months_out:
                    rows.append(row_to_cashflow_dict(row, float(amount), entry_date))
                continue
            applicable_weeks = [week for week in week_starts if start <= week <= end]
            if not applicable_weeks:
                continue
            weekly_amount = float(amount) / len(applicable_weeks)
            for week in applicable_weeks:
                rows.append(row_to_cashflow_dict(row, weekly_amount, week))
        else:
            entry_date = pd.to_datetime(row.get("Date"), errors="coerce")
            if pd.isna(entry_date):
                continue
            if today <= entry_date <= six_months_out:
                rows.append(row_to_cashflow_dict(row, float(amount), entry_date))
    return pd.DataFrame(rows, columns=CASHFLOW_COLUMNS)


def build_funding_cash_flow(funding_df, today, six_months_out, selected_company=None, selected_department=None, selected_project=None, selected_category=None, selected_work_order_id=None):
    rows = []
    if funding_df.empty:
        return pd.DataFrame(columns=CASHFLOW_COLUMNS)
    work = funding_df.copy()
    work = to_datetime_safe(work, ["Date"])
    work = filter_by_scope(work, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    for _, row in work.iterrows():
        if str(row.get("Active", "")).strip().lower() not in ["yes", "true", "1", ""]:
            continue
        amount = pd.to_numeric(pd.Series([row.get("Funding Amount", 0)]), errors="coerce").fillna(0).iloc[0]
        entry_date = pd.to_datetime(row.get("Date"), errors="coerce")
        if float(amount) <= 0 or pd.isna(entry_date):
            continue
        if today <= entry_date <= six_months_out:
            rows.append({"Type": "Cash In", "Company Name": str(row.get("Company Name", "")).strip(), "Department": str(row.get("Department", "")).strip(), "Project": str(row.get("Project", "")).strip(), "Category": str(row.get("Category", "")).strip(), "Work Order ID": str(row.get("Work Order ID", "")).strip(), "Work Item": "", "Vendor/Source": str(row.get("Funding Source", "")).strip(), "Description": str(row.get("Notes", "")).strip(), "Amount": float(amount), "Date": entry_date, "Spread Method": "", "Start Date": "", "End Date": ""})
    return pd.DataFrame(rows, columns=CASHFLOW_COLUMNS)


def cash_with_week_fields(cash_df):
    if cash_df.empty:
        out = cash_df.copy()
        out["Week Start"] = pd.NaT
        out["Cash In"] = 0.0
        out["Cash Out"] = 0.0
        return out
    out = cash_df.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out["Amount"] = pd.to_numeric(out["Amount"], errors="coerce").fillna(0)
    out["Week Start"] = out["Date"] - pd.to_timedelta(out["Date"].dt.weekday, unit="D")
    out["Cash In"] = out.apply(lambda x: x["Amount"] if x["Type"] == "Cash In" else 0, axis=1)
    out["Cash Out"] = out.apply(lambda x: x["Amount"] if x["Type"] == "Cash Out" else 0, axis=1)
    return out


def clean_excel_sheet_name(sheet_name):
    cleaned = str(sheet_name)
    for bad_char in ["\\", "/", "*", "?", ":", "[", "]"]:
        cleaned = cleaned.replace(bad_char, "-")
    cleaned = cleaned.strip()
    if not cleaned:
        cleaned = "Sheet"
    return cleaned[:31]


def to_excel_bytes(dataframes_dict):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        used_names = set()
        for sheet_name, df in dataframes_dict.items():
            clean_name = clean_excel_sheet_name(sheet_name)
            base_name = clean_name
            counter = 1
            while clean_name in used_names:
                suffix = f"_{counter}"
                clean_name = (base_name[:31 - len(suffix)] + suffix)[:31]
                counter += 1
            used_names.add(clean_name)
            df.to_excel(writer, index=False, sheet_name=clean_name)
    output.seek(0)
    return output.getvalue()


companies_df = load_csv(COMPANIES_FILE, COMPANY_COLUMNS)
funding_df = load_csv(FUNDING_FILE, FUNDING_COLUMNS)
projects_df = load_csv(PROJECTS_FILE, PROJECT_COLUMNS)
labor_df = load_csv(LABOR_FILE, LABOR_COLUMNS)
labor_budget_df = load_csv(LABOR_BUDGET_FILE, LABOR_BUDGET_COLUMNS)
cashflow_df = load_csv(CASHFLOW_FILE, CASHFLOW_COLUMNS)

funding_df = to_numeric_safe(funding_df, ["Funding Amount"])
funding_df = to_datetime_safe(funding_df, ["Date"])
projects_df = to_datetime_safe(projects_df, ["Start Date", "End Date"])
labor_df = to_datetime_safe(labor_df, ["Start Date", "End Date"])
labor_df = to_numeric_safe(labor_df, ["Hourly Rate", "Hours Per Week"])
labor_budget_df = to_datetime_safe(labor_budget_df, ["Start Date", "End Date"])
labor_budget_df = to_numeric_safe(labor_budget_df, ["Total Labor Amount"])
cashflow_df = to_datetime_safe(cashflow_df, ["Date", "Start Date", "End Date"])
cashflow_df = to_numeric_safe(cashflow_df, ["Amount"])

require_login()

st.title("Cash Flow Management App")
st.caption(f"Logged in as: {st.session_state.logged_in_user}")

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs(["Dashboard", "Companies", "Funding", "Projects / Work Orders", "Labor", "Materials and Other Costs", "6-Month Weekly Cash Flow", "Reports / Import / Export", "User Access"])

with tab1:
    st.subheader("Dashboard")
    company_filter_options = ["All Companies"] + company_options_list(companies_df)
    dashboard_company_filter = st.selectbox("Company Filter", company_filter_options, key="dashboard_company_filter")
    selected_company = None if dashboard_company_filter == "All Companies" else dashboard_company_filter
    department_filter_options = ["All Departments"] + department_options_list(projects_df, funding_df, selected_company)
    dashboard_department_filter = st.selectbox("Department Filter", department_filter_options, key="dashboard_department_filter")
    selected_department = None if dashboard_department_filter == "All Departments" else dashboard_department_filter
    project_filter_options = ["All Projects"] + project_options_list(projects_df, funding_df, selected_company, selected_department)
    dashboard_project_filter = st.selectbox("Project Filter", project_filter_options, key="dashboard_project_filter")
    selected_project = None if dashboard_project_filter == "All Projects" else dashboard_project_filter
    category_filter_options = ["All Categories"] + category_options_list(projects_df, cashflow_df, funding_df, selected_company, selected_project)
    dashboard_category_filter = st.selectbox("Category Filter", category_filter_options, key="dashboard_category_filter")
    selected_category = None if dashboard_category_filter == "All Categories" else dashboard_category_filter
    work_order_filter_options = ["All Work Orders"] + work_order_options_list(projects_df, selected_company, selected_department, selected_project, selected_category)
    dashboard_work_order_filter = st.selectbox("Work Order Filter", work_order_filter_options, key="dashboard_work_order_filter")
    selected_work_order_id = None
    if dashboard_work_order_filter != "All Work Orders":
        selected_work_order_id, _, _ = parse_work_order_selection(dashboard_work_order_filter)
    funding_balance_scope = get_funding_balance_for_scope(funding_df, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    manual_override_default = float(get_setting("manual_dashboard_starting_balance_override", "0"))
    use_override = st.checkbox("Override calculated funding balance", value=False, key="dashboard_use_override")
    selected_starting_cash = funding_balance_scope
    if use_override:
        selected_starting_cash = st.number_input("Manual Starting Balance Override", min_value=0.0, value=manual_override_default, format="%.2f", key="dashboard_starting_cash_override_input")
        if st.button("Save Manual Override"):
            set_setting("manual_dashboard_starting_balance_override", selected_starting_cash)
            st.success("Manual override saved.")
    else:
        st.info(f"Calculated funding for selected scope: ${funding_balance_scope:,.2f}")
    today = pd.Timestamp(date.today())
    six_months_out = today + pd.Timedelta(days=182)
    funding_cash_df = build_funding_cash_flow(funding_df, today, six_months_out, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    other_costs_cash_df = build_other_costs_cash_flow(cashflow_df, today, six_months_out, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    labor_cash_df = build_labor_cash_flow(labor_df, today, six_months_out, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    quick_labor_cash_df = build_quick_labor_budget_cash_flow(labor_budget_df, today, six_months_out, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    combined_future = pd.concat([funding_cash_df, other_costs_cash_df, labor_cash_df, quick_labor_cash_df], ignore_index=True)
    if not combined_future.empty:
        combined_future["Amount"] = pd.to_numeric(combined_future["Amount"], errors="coerce").fillna(0)
        combined_future = cash_with_week_fields(combined_future)
        weekly_summary = combined_future.groupby(["Week Start"], dropna=False)[["Cash In", "Cash Out"]].sum().reset_index().sort_values("Week Start")
        weekly_summary["Net Cash Flow"] = weekly_summary["Cash In"] - weekly_summary["Cash Out"]
        weekly_summary["Starting Balance"] = 0.0
        weekly_summary["Ending Balance"] = 0.0
        running_balance = float(selected_starting_cash)
        for idx in weekly_summary.index:
            weekly_summary.at[idx, "Starting Balance"] = running_balance
            running_balance += float(weekly_summary.at[idx, "Net Cash Flow"])
            weekly_summary.at[idx, "Ending Balance"] = running_balance
        risk_weeks = weekly_summary[weekly_summary["Ending Balance"] < 0].copy()
        total_in = weekly_summary["Cash In"].sum()
        total_out = weekly_summary["Cash Out"].sum()
        ending_cash = weekly_summary["Ending Balance"].iloc[-1] if not weekly_summary.empty else selected_starting_cash
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Funding Balance", f"${selected_starting_cash:,.2f}")
        c2.metric("6-Month Cash In", f"${total_in:,.2f}")
        c3.metric("6-Month Cash Out", f"${total_out:,.2f}")
        c4.metric("Projected Ending Balance", f"${ending_cash:,.2f}")
        if not risk_weeks.empty:
            st.error("Warning: One or more projected weeks end with a negative balance.")
            st.dataframe(risk_weeks[["Week Start", "Cash In", "Cash Out", "Net Cash Flow", "Starting Balance", "Ending Balance"]], use_container_width=True)
        else:
            st.success("No negative weeks projected in the next 6 months.")
        st.markdown("### Weekly Cash Position")
        st.dataframe(weekly_summary, use_container_width=True)
        chart_df = weekly_summary.set_index("Week Start")[["Starting Balance", "Ending Balance", "Cash In", "Cash Out"]]
        st.line_chart(chart_df)
    else:
        st.info("No projected cash flow data yet for the next 6 months for this scope.")

with tab2:
    st.subheader("Companies")
    with st.form("add_company_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            company_name = st.text_input("Company Name")
            company_active = st.selectbox("Active", ["Yes", "No"])
        with c2:
            company_notes = st.text_input("Notes")
        submitted_company = st.form_submit_button("Add Company")
        if submitted_company:
            if not company_name.strip():
                st.error("Company Name is required.")
            elif company_name.strip() in companies_df["Company Name"].astype(str).tolist():
                st.error("Company Name already exists.")
            else:
                companies_df = pd.concat([companies_df, pd.DataFrame([{"Company Name": company_name.strip(), "Active": company_active, "Notes": company_notes.strip()}])], ignore_index=True)
                save_csv(companies_df, COMPANIES_FILE)
                st.success("Company added.")
                st.rerun()
    if not companies_df.empty:
        st.dataframe(companies_df.reset_index().rename(columns={"index": "Row Number"}), use_container_width=True)
    else:
        st.info("No companies added yet.")

with tab3:
    st.subheader("Funding")
    st.caption("Use this page to assign money to a company/project. Department, category, and work order can be used when you want to break funding down further.")
    company_options = company_options_list(companies_df)
    with st.form("add_funding_form", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            funding_company = st.selectbox("Company (Required)", [""] + company_options, key="funding_company")
            existing_departments = department_options_list(projects_df, funding_df, funding_company if funding_company else None)
            funding_department = st.text_input("Department (Optional)", key="funding_department")
            if existing_departments:
                selected_existing_department = st.selectbox("Use Existing Department", [""] + existing_departments, key="funding_existing_department")
                if selected_existing_department and not funding_department:
                    funding_department = selected_existing_department
        with c2:
            existing_projects = project_options_list(projects_df, funding_df, funding_company if funding_company else None, funding_department if funding_department else None)
            funding_project = st.text_input("Project (Required)", key="funding_project")
            if existing_projects:
                selected_existing_project = st.selectbox("Use Existing Project", [""] + existing_projects, key="funding_existing_project")
                if selected_existing_project and not funding_project:
                    funding_project = selected_existing_project
            funding_category = st.text_input("Category (Optional)", key="funding_category")
        with c3:
            existing_work_orders = work_order_options_list(projects_df, funding_company if funding_company else None, funding_department if funding_department else None, funding_project if funding_project else None, funding_category if funding_category else None)
            selected_funding_work_order = st.selectbox("Work Order (Optional)", [""] + existing_work_orders, key="funding_work_order")
            funding_work_order_id = ""
            if selected_funding_work_order:
                funding_work_order_id, _, _ = parse_work_order_selection(selected_funding_work_order)
            funding_amount = st.number_input("Funding Amount", min_value=0.0, format="%.2f", key="funding_amount")
        with c4:
            funding_source = st.text_input("Funding Source", key="funding_source")
            funding_date = st.date_input("Funding Date", value=date.today(), key="funding_date")
            funding_active = st.selectbox("Active", ["Yes", "No"], key="funding_active")
            funding_notes = st.text_input("Notes", key="funding_notes")
        submitted_funding = st.form_submit_button("Add Funding")
        if submitted_funding:
            if not funding_company:
                st.error("Company is required.")
            elif not funding_project.strip():
                st.error("Project is required.")
            elif funding_amount <= 0:
                st.error("Funding Amount must be greater than zero.")
            else:
                funding_df = pd.concat([funding_df, pd.DataFrame([{"Company Name": funding_company, "Department": funding_department.strip(), "Project": funding_project.strip(), "Category": funding_category.strip(), "Work Order ID": funding_work_order_id.strip(), "Funding Amount": funding_amount, "Funding Source": funding_source.strip(), "Date": funding_date, "Active": funding_active, "Notes": funding_notes.strip()}])], ignore_index=True)
                save_csv(funding_df, FUNDING_FILE)
                st.success("Funding added.")
                st.rerun()
    if not funding_df.empty:
        st.markdown("### Current Funding")
        st.dataframe(funding_df.reset_index().rename(columns={"index": "Row Number"}), use_container_width=True)
    else:
        st.info("No funding added yet.")

with tab4:
    st.subheader("Projects / Work Orders")
    st.caption("Company and Project are required. Department and Category are optional. Work Order ID and Work Item are required for cost budgeting and should match the Renovation Management System when available.")
    company_options = company_options_list(companies_df)
    with st.form("add_project_work_order_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            selected_company = st.selectbox("Company (Required)", [""] + company_options, key="project_company")
            project_department = st.text_input("Department (Optional)", key="project_department")
            project_category = st.text_input("Category (Optional)", key="project_category")
        with c2:
            project_name = st.text_input("Project (Required)", key="project_name")
            work_order_id = st.text_input("Work Order ID (Required)", key="project_work_order_id")
            work_item = st.text_input("Work Item (Required)", key="project_work_item")
        with c3:
            record_type = st.selectbox("Record Type", RECORD_TYPES, key="project_record_type")
            status = st.selectbox("Status", STATUSES, key="project_status")
            start_date = st.date_input("Start Date", value=date.today(), key="project_start_date")
            end_date = st.date_input("End Date", value=date.today() + timedelta(days=180), key="project_end_date")
            notes = st.text_input("Notes", key="project_notes")
        submitted_project = st.form_submit_button("Add Project / Work Order")
        if submitted_project:
            if not selected_company:
                st.error("Company is required.")
            elif not project_name.strip():
                st.error("Project is required.")
            elif not work_order_id.strip():
                st.error("Work Order ID is required.")
            elif not work_item.strip():
                st.error("Work Item is required.")
            elif end_date < start_date:
                st.error("End Date cannot be earlier than Start Date.")
            elif work_order_id.strip() in projects_df["Work Order ID"].astype(str).tolist():
                st.error("Work Order ID already exists.")
            else:
                projects_df = pd.concat([projects_df, pd.DataFrame([{"Company Name": selected_company, "Department": project_department.strip(), "Project": project_name.strip(), "Category": project_category.strip(), "Work Order ID": work_order_id.strip(), "Work Item": work_item.strip(), "Record Type": record_type, "Status": status, "Start Date": start_date, "End Date": end_date, "Notes": notes.strip()}])], ignore_index=True)
                save_csv(projects_df, PROJECTS_FILE)
                st.success("Project / work order added.")
                st.rerun()
    if not projects_df.empty:
        st.dataframe(projects_df.reset_index().rename(columns={"index": "Row Number"}), use_container_width=True)
    else:
        st.info("No projects / work orders added yet.")

with tab5:
    st.subheader("Labor")
    work_order_options = work_order_options_list(projects_df)
    st.markdown("### Quick Labor Budget by Work Order")
    with st.form("add_quick_labor_budget_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            selected_budget_work_order = st.selectbox("Work Order / Work Item", [""] + work_order_options, key="budget_work_order")
            total_labor_amount = st.number_input("Total Labor Amount", min_value=0.0, format="%.2f", key="budget_total_labor_amount")
            spread_method = st.selectbox("Spread Method", ["Even Weekly Spread", "One-Time Amount"], key="budget_spread_method")
        with c2:
            budget_start = st.date_input("Start Date", value=date.today(), key="budget_start_date")
            budget_end = st.date_input("End Date", value=date.today() + timedelta(days=90), key="budget_end_date")
            budget_active = st.selectbox("Active", ["Yes", "No"], key="budget_active")
        with c3:
            budget_notes = st.text_input("Notes", key="budget_notes")
            st.write("")
            st.write("")
        submitted_budget = st.form_submit_button("Add Quick Labor Budget")
        if submitted_budget:
            if not selected_budget_work_order:
                st.error("Please select a work order / work item.")
            elif total_labor_amount <= 0:
                st.error("Total labor amount must be greater than zero.")
            elif budget_end < budget_start:
                st.error("End Date cannot be earlier than Start Date.")
            else:
                work_order_id, _, work_item = parse_work_order_selection(selected_budget_work_order)
                company_name, department, project, category, _, work_item = get_work_order_context(projects_df, work_order_id, work_item)
                labor_budget_df = pd.concat([labor_budget_df, pd.DataFrame([{"Company Name": company_name, "Department": department, "Project": project, "Category": category, "Work Order ID": work_order_id, "Work Item": work_item, "Total Labor Amount": total_labor_amount, "Start Date": budget_start, "End Date": budget_end, "Spread Method": spread_method, "Active": budget_active, "Notes": budget_notes.strip()}])], ignore_index=True)
                save_csv(labor_budget_df, LABOR_BUDGET_FILE)
                st.success("Quick labor budget added.")
                st.rerun()
    if not labor_budget_df.empty:
        st.dataframe(labor_budget_df.reset_index().rename(columns={"index": "Row Number"}), use_container_width=True)
    else:
        st.info("No quick labor budgets yet.")
    st.markdown("---")
    st.markdown("### Detailed Labor Assignments")
    with st.form("add_labor_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            worker_name = st.text_input("Worker Name")
            selected_work_order = st.selectbox("Work Order / Work Item", [""] + work_order_options, key="detailed_labor_work_order")
            role = st.text_input("Role")
        with c2:
            hourly_rate = st.number_input("Hourly Rate", min_value=0.0, format="%.2f")
            hours_per_week = st.number_input("Hours Per Week", min_value=0.0, format="%.2f")
            labor_start = st.date_input("Labor Start Date", value=date.today())
        with c3:
            labor_end = st.date_input("Labor End Date", value=date.today() + timedelta(days=90))
            active = st.selectbox("Active", ["Yes", "No"], key="detailed_labor_active")
        submitted_labor = st.form_submit_button("Add Labor Assignment")
        if submitted_labor:
            if not worker_name.strip():
                st.error("Worker Name is required.")
            elif not selected_work_order:
                st.error("Please select a work order / work item.")
            elif labor_end < labor_start:
                st.error("End Date cannot be earlier than Start Date.")
            else:
                work_order_id, _, work_item = parse_work_order_selection(selected_work_order)
                company_name, department, project, category, _, work_item = get_work_order_context(projects_df, work_order_id, work_item)
                labor_df = pd.concat([labor_df, pd.DataFrame([{"Company Name": company_name, "Department": department, "Project": project, "Category": category, "Work Order ID": work_order_id, "Work Item": work_item, "Worker Name": worker_name.strip(), "Role": role.strip(), "Hourly Rate": hourly_rate, "Hours Per Week": hours_per_week, "Start Date": labor_start, "End Date": labor_end, "Active": active}])], ignore_index=True)
                save_csv(labor_df, LABOR_FILE)
                st.success("Labor assignment added.")
                st.rerun()
    if not labor_df.empty:
        st.dataframe(labor_df.reset_index().rename(columns={"index": "Row Number"}), use_container_width=True)
    else:
        st.info("No labor assignments yet.")

with tab6:
    st.subheader("Materials and Other Costs")
    st.caption("Use this page for non-labor costs such as materials, subcontractors, permits, equipment, and other direct costs. Costs must be tied to a work order and work item.")
    work_order_options = work_order_options_list(projects_df)
    with st.form("other_costs_form", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            selected_work_order_out = st.selectbox("Work Order / Work Item", [""] + work_order_options, key="other_costs_work_order")
            category_out = st.selectbox("Cost Category", CASH_OUT_CATEGORIES, key="other_costs_category")
        with c2:
            vendor_out = st.text_input("Vendor / Source", key="other_costs_vendor")
            amount_out = st.number_input("Amount", min_value=0.0, format="%.2f", key="other_costs_amount")
        with c3:
            spread_method_out = st.selectbox("Spread Method", ["One-Time Amount", "Even Weekly Spread"], key="other_costs_spread_method")
            date_out = st.date_input("One-Time Date", value=date.today(), key="other_costs_date")
        with c4:
            cost_start = st.date_input("Spread Start Date", value=date.today(), key="other_costs_start_date")
            cost_end = st.date_input("Spread End Date", value=date.today() + timedelta(days=60), key="other_costs_end_date")
            description_out = st.text_input("Description", key="other_costs_description")
        submitted_out = st.form_submit_button("Add Material / Other Cost")
        if submitted_out:
            if not selected_work_order_out:
                st.error("Please select a work order / work item.")
            elif amount_out <= 0:
                st.error("Amount must be greater than zero.")
            elif spread_method_out == "Even Weekly Spread" and cost_end < cost_start:
                st.error("Spread End Date cannot be earlier than Spread Start Date.")
            else:
                work_order_id, _, work_item = parse_work_order_selection(selected_work_order_out)
                company_name, department, project, project_category, _, work_item = get_work_order_context(projects_df, work_order_id, work_item)
                record_date = cost_start if spread_method_out == "Even Weekly Spread" else date_out
                start_date_value = cost_start if spread_method_out == "Even Weekly Spread" else pd.NaT
                end_date_value = cost_end if spread_method_out == "Even Weekly Spread" else pd.NaT
                cashflow_df = pd.concat([cashflow_df, pd.DataFrame([{"Type": "Cash Out", "Company Name": company_name, "Department": department, "Project": project, "Category": category_out or project_category, "Work Order ID": work_order_id, "Work Item": work_item, "Vendor/Source": vendor_out.strip(), "Description": description_out.strip(), "Amount": amount_out, "Date": record_date, "Spread Method": spread_method_out, "Start Date": start_date_value, "End Date": end_date_value}])], ignore_index=True)
                save_csv(cashflow_df, CASHFLOW_FILE)
                st.success("Material / other cost added.")
                st.rerun()
    non_labor_costs_df = cashflow_df.copy()
    if not non_labor_costs_df.empty:
        non_labor_costs_df = non_labor_costs_df[non_labor_costs_df["Type"].astype(str) == "Cash Out"].copy()
    if not non_labor_costs_df.empty:
        st.markdown("### Current Materials and Other Costs")
        st.dataframe(non_labor_costs_df.reset_index().rename(columns={"index": "Row Number"}), use_container_width=True)
    else:
        st.info("No materials or other non-labor costs entered yet.")

with tab7:
    st.subheader("6-Month Weekly Cash Flow")
    company_filter_options = ["All Companies"] + company_options_list(companies_df)
    weekly_company_filter = st.selectbox("Company Filter", company_filter_options, key="weekly_company_filter")
    selected_company = None if weekly_company_filter == "All Companies" else weekly_company_filter
    department_filter_options = ["All Departments"] + department_options_list(projects_df, funding_df, selected_company)
    weekly_department_filter = st.selectbox("Department Filter", department_filter_options, key="weekly_department_filter")
    selected_department = None if weekly_department_filter == "All Departments" else weekly_department_filter
    project_filter_options = ["All Projects"] + project_options_list(projects_df, funding_df, selected_company, selected_department)
    weekly_project_filter = st.selectbox("Project Filter", project_filter_options, key="weekly_project_filter")
    selected_project = None if weekly_project_filter == "All Projects" else weekly_project_filter
    category_filter_options = ["All Categories"] + category_options_list(projects_df, cashflow_df, funding_df, selected_company, selected_project)
    weekly_category_filter = st.selectbox("Category Filter", category_filter_options, key="weekly_category_filter")
    selected_category = None if weekly_category_filter == "All Categories" else weekly_category_filter
    work_order_filter_options = ["All Work Orders"] + work_order_options_list(projects_df, selected_company, selected_department, selected_project, selected_category)
    weekly_work_order_filter = st.selectbox("Work Order Filter", work_order_filter_options, key="weekly_work_order_filter")
    selected_work_order_id = None
    if weekly_work_order_filter != "All Work Orders":
        selected_work_order_id, _, _ = parse_work_order_selection(weekly_work_order_filter)
    calculated_funding_balance = get_funding_balance_for_scope(funding_df, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    weekly_starting_cash = st.number_input("Funding Balance for Projection", min_value=0.0, value=calculated_funding_balance, format="%.2f", key="weekly_projection_starting_cash")
    today = pd.Timestamp(date.today())
    six_months_out = today + pd.Timedelta(days=182)
    funding_cash_df = build_funding_cash_flow(funding_df, today, six_months_out, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    other_costs_cash_df = build_other_costs_cash_flow(cashflow_df, today, six_months_out, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    labor_cash_df = build_labor_cash_flow(labor_df, today, six_months_out, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    quick_labor_cash_df = build_quick_labor_budget_cash_flow(labor_budget_df, today, six_months_out, selected_company, selected_department, selected_project, selected_category, selected_work_order_id)
    combined_future = pd.concat([funding_cash_df, other_costs_cash_df, labor_cash_df, quick_labor_cash_df], ignore_index=True)
    if not combined_future.empty:
        combined_future["Amount"] = pd.to_numeric(combined_future["Amount"], errors="coerce").fillna(0)
        combined_future = cash_with_week_fields(combined_future)
        weekly_detail = combined_future.groupby(["Week Start", "Company Name", "Department", "Project", "Category", "Work Order ID", "Work Item"], dropna=False)[["Cash In", "Cash Out"]].sum().reset_index().sort_values(["Week Start", "Company Name", "Project", "Work Order ID"])
        weekly_detail["Net Cash Flow"] = weekly_detail["Cash In"] - weekly_detail["Cash Out"]
        portfolio_weekly = combined_future.groupby(["Week Start"], dropna=False)[["Cash In", "Cash Out"]].sum().reset_index().sort_values("Week Start")
        portfolio_weekly["Net Cash Flow"] = portfolio_weekly["Cash In"] - portfolio_weekly["Cash Out"]
        portfolio_weekly["Starting Balance"] = 0.0
        portfolio_weekly["Ending Balance"] = 0.0
        running_balance = float(weekly_starting_cash)
        for idx in portfolio_weekly.index:
            portfolio_weekly.at[idx, "Starting Balance"] = running_balance
            running_balance += float(portfolio_weekly.at[idx, "Net Cash Flow"])
            portfolio_weekly.at[idx, "Ending Balance"] = running_balance
        risk_weeks = portfolio_weekly[portfolio_weekly["Ending Balance"] < 0].copy()
        st.markdown("### Weekly Detail by Company / Department / Project / Work Order / Work Item")
        st.dataframe(weekly_detail, use_container_width=True)
        st.markdown("### Combined Weekly View")
        st.dataframe(portfolio_weekly, use_container_width=True)
        if not risk_weeks.empty:
            st.error("Negative ending balance appears in one or more projected weeks.")
            st.dataframe(risk_weeks, use_container_width=True)
        else:
            st.success("Projected ending balance stays non-negative across the next 6 months.")
        chart_df = portfolio_weekly.set_index("Week Start")[["Cash In", "Cash Out", "Starting Balance", "Ending Balance"]]
        st.line_chart(chart_df)
    else:
        st.info("No cash flow, funding, or labor records found for the next 6 months.")

with tab8:
    st.subheader("Reports / Import / Export")
    company_filter_options = ["All Companies"] + company_options_list(companies_df)
    report_company_filter = st.selectbox("Company Filter", company_filter_options, key="report_company_filter")
    selected_company = None if report_company_filter == "All Companies" else report_company_filter
    department_filter_options = ["All Departments"] + department_options_list(projects_df, funding_df, selected_company)
    report_department_filter = st.selectbox("Department Filter", department_filter_options, key="report_department_filter")
    selected_department = None if report_department_filter == "All Departments" else report_department_filter
    project_filter_options = ["All Projects"] + project_options_list(projects_df, funding_df, selected_company, selected_department)
    report_project_filter = st.selectbox("Project Filter", project_filter_options, key="report_project_filter")
    selected_project = None if report_project_filter == "All Projects" else report_project_filter
    category_filter_options = ["All Categories"] + category_options_list(projects_df, cashflow_df, funding_df, selected_company, selected_project)
    report_category_filter = st.selectbox("Category Filter", category_filter_options, key="report_category_filter")
    selected_category = None if report_category_filter == "All Categories" else report_category_filter
    work_order_filter_options = ["All Work Orders"] + work_order_options_list(projects_df, selected_company, selected_department, selected_project, selected_category)
    report_work_order_filter = st.selectbox("Work Order Filter", work_order_filter_options, key="report_work_order_filter")
    selected_work_order_id = None
    if report_work_order_filter != "All Work Orders":
        selected_work_order_id, _, _ = parse_work_order_selection(report_work_order_filter)
    report_cash = filter_by_scope(cashflow_df, selected_company, selected_department, selected_project, selected_category, selected_work_order_id).copy()
    if not report_cash.empty:
        report_cash = report_cash[report_cash["Type"].astype(str) == "Cash Out"].copy()
        report_cash["Date"] = pd.to_datetime(report_cash["Date"], errors="coerce")
        report_cash["Amount"] = pd.to_numeric(report_cash["Amount"], errors="coerce").fillna(0)
    report_funding = filter_by_scope(funding_df, selected_company, selected_department, selected_project, selected_category, selected_work_order_id).copy()
    if not report_funding.empty:
        report_funding["Funding Amount"] = pd.to_numeric(report_funding["Funding Amount"], errors="coerce").fillna(0)
    st.markdown("### Funding Entries")
    if not report_funding.empty:
        st.dataframe(report_funding.reset_index().rename(columns={"index": "Row Number"}), use_container_width=True)
    else:
        st.info("No funding entries yet for this filter.")
    st.markdown("### All Non-Labor Cost Entries")
    if not report_cash.empty:
        st.dataframe(report_cash.reset_index().rename(columns={"index": "Row Number"}), use_container_width=True)
    else:
        st.info("No non-labor cost entries yet for this filter.")
    st.markdown("### Summary by Company / Department / Project / Work Order / Work Item")
    if not report_cash.empty:
        summary = report_cash.groupby(["Company Name", "Department", "Project", "Category", "Work Order ID", "Work Item"])[["Amount"]].sum().reset_index()
        summary = summary.rename(columns={"Amount": "Total Cost"})
        st.dataframe(summary, use_container_width=True)
    else:
        st.info("No report data yet.")
    st.markdown("### Export Data")
    export_bytes = to_excel_bytes({"Companies": companies_df.copy(), "Funding": funding_df.copy(), "Projects Work Orders": projects_df.copy(), "Labor": labor_df.copy(), "QuickLaborBudget": labor_budget_df.copy(), "CashFlow": cashflow_df.copy()})
    st.download_button(label="Download Full Data as Excel", data=export_bytes, file_name="cash_flow_app_data.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with tab9:
    st.subheader("User Access")
    st.caption("Anyone with a login has full permissions.")
    users_df = load_users()
    c1, c2 = st.columns(2)
    with c1:
        with st.form("add_user_form", clear_on_submit=True):
            new_username = st.text_input("New Username")
            new_password = st.text_input("New Password", type="password")
            new_password_confirm = st.text_input("Confirm New Password", type="password")
            new_user_active = st.selectbox("Active", ["Yes", "No"], key="new_user_active")
            create_user = st.form_submit_button("Add User")
            if create_user:
                if not new_username.strip():
                    st.error("Username is required.")
                elif new_username.strip() in users_df["Username"].astype(str).tolist():
                    st.error("That username already exists.")
                elif not new_password:
                    st.error("Password is required.")
                elif new_password != new_password_confirm:
                    st.error("Passwords do not match.")
                else:
                    users_df = pd.concat([users_df, pd.DataFrame([{"Username": new_username.strip(), "Password Hash": hash_password(new_password), "Active": new_user_active, "Created On": pd.Timestamp(date.today())}])], ignore_index=True)
                    save_users(users_df)
                    st.success("User added.")
                    st.rerun()
    with c2:
        user_options = users_df["Username"].astype(str).tolist() if not users_df.empty else []
        with st.form("change_password_form"):
            password_user = st.selectbox("User to Change Password", user_options)
            changed_password = st.text_input("New Password for Selected User", type="password")
            changed_password_confirm = st.text_input("Confirm Password", type="password", key="confirm_changed_password")
            save_password_change = st.form_submit_button("Change Password")
            if save_password_change:
                if not user_options:
                    st.error("No users found.")
                elif not changed_password:
                    st.error("Password is required.")
                elif changed_password != changed_password_confirm:
                    st.error("Passwords do not match.")
                else:
                    users_df.loc[users_df["Username"].astype(str) == password_user, "Password Hash"] = hash_password(changed_password)
                    save_users(users_df)
                    st.success("Password updated.")
                    st.rerun()
    st.markdown("### Current Users")
    if not users_df.empty:
        display_users = users_df.copy()
        display_users["Password Hash"] = "Hidden"
        st.dataframe(display_users.reset_index().rename(columns={"index": "Row Number"}), use_container_width=True)
        st.markdown("### Activate / Deactivate User")
        user_row_to_edit = st.selectbox("Select User Row Number", [""] + [str(i) for i in users_df.index.tolist()], key="user_row_to_edit")
        if user_row_to_edit != "":
            selected_idx = int(user_row_to_edit)
            current_active = str(users_df.loc[selected_idx, "Active"])
            updated_active = st.selectbox("Active Setting", ["Yes", "No"], index=0 if current_active == "Yes" else 1, key="updated_active")
            if st.button("Save User Active Setting"):
                users_df.at[selected_idx, "Active"] = updated_active
                save_users(users_df)
                st.success("User updated.")
                st.rerun()
        st.markdown("### Delete User")
        delete_user_row = st.selectbox("Select User Row Number to Delete", [""] + [str(i) for i in users_df.index.tolist()], key="delete_user_row")
        if st.button("Delete Selected User"):
            if delete_user_row == "":
                st.warning("Please select a user row.")
            else:
                delete_idx = int(delete_user_row)
                username_to_delete = str(users_df.loc[delete_idx, "Username"])
                if len(users_df) <= 1:
                    st.error("You must keep at least one user.")
                elif username_to_delete == st.session_state.logged_in_user:
                    st.error("You cannot delete the user currently logged in.")
                else:
                    users_df = users_df.drop(index=delete_idx).reset_index(drop=True)
                    save_users(users_df)
                    st.success("User deleted.")
                    st.rerun()
    else:
        st.info("No users found.")
    if st.button("Logout"):
        st.session_state.authenticated = False
        st.session_state.logged_in_user = ""
        st.rerun()
