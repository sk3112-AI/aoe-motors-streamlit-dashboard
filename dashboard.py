import streamlit as st
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import pandas as pd
from openai import OpenAI
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
import requests
from datetime import datetime, date, timedelta
import json
import logging
import sys
import re

# Load environment variables
dotenv_path = load_dotenv()

# --- Logging Setup ---
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG,
                    format="%(asctime)s - %(levelname)s - %(message)s")

# --- GLOBAL CONFIGURATIONS ---
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    logging.error("Supabase URL or Key not found. Ensure they are set as environment variables.")
    st.error("Supabase URL or Key not found. Please set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

supabase: Client = create_client(supabase_url, supabase_key)
SUPABASE_TABLE_NAME = "bookings"
EMAIL_INTERACTIONS_TABLE_NAME = "email_interactions"

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    logging.error("OpenAI API Key not found. Ensure OPENAI_API_KEY is set.")
    st.error("OpenAI API Key not found. Please set OPENAI_API_KEY.")
    st.stop()
openai_client = OpenAI(api_key=openai_api_key)

email_host = os.getenv("EMAIL_HOST")
email_port_str = os.getenv("EMAIL_PORT")
email_port = int(email_port_str) if email_port_str else 0
email_address = os.getenv("EMAIL_ADDRESS")
email_password = os.getenv("EMAIL_PASSWORD")

ENABLE_EMAIL_SENDING = all([email_host, email_port, email_address, email_password])
if not ENABLE_EMAIL_SENDING:
    logging.warning("Email credentials not fully configured. Email sending disabled.")
    st.warning("Email credentials not fully configured. Email sending will be disabled.")

BACKEND_API_URL = "https://aoe-agentic-demo.onrender.com"

# Hardcoded vehicle data
AOE_VEHICLE_DATA = {
    "AOE Apex": {...},  # truncated for brevity
    "AOE Volt": {...},
    "AOE Thunder": {...}
}
COMPETITOR_VEHICLE_DATA = { ... }
AOE_TYPE_TO_COMPETITOR_SEGMENT_MAP = { ... }
ACTION_STATUS_MAP = {
    "Hot": [...],
    "Warm": [...],
    "Cold": [...],
    "New": [...]
}

# --- FUNCTION DEFINITIONS ---
@st.cache_data(ttl=30)
def fetch_bookings_data(location_filter=None, start_date_filter=None, end_date_filter=None):
    # Implementation unchanged
    ...


def update_booking_field(request_id, field_name, new_value):
    # Implementation unchanged
    ...


def send_email(recipient_email, subject, body):
    if not ENABLE_EMAIL_SENDING:
        logging.error("Email sending is disabled. Cannot send email.")
        st.session_state.error_message = "Email sending is disabled."
        return False

    msg = MIMEMultipart()
    msg["From"] = email_address
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        # Try SSL
        try:
            logging.debug(f"Attempting SMTP_SSL to {email_host}:{email_port}")
            server = smtplib.SMTP_SSL(email_host, email_port, timeout=10)
        except OSError as ssl_err:
            logging.warning(f"SMTP_SSL failed ({ssl_err}); falling back to STARTTLS on 587")
            server = smtplib.SMTP(email_host, 587, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(email_address, email_password)
        server.send_message(msg)
        server.quit()

        logging.info(f"Email sent to {recipient_email}")
        st.session_state.success_message = f"Email sent to {recipient_email}"
        return True

    except OSError as net_err:
        logging.error(f"Network error sending email: {net_err}", exc_info=True)
        st.session_state.error_message = (
            "Network error: Unable to reach SMTP server. "
            "Check network/VPC settings and SMTP host/port."
        )
        return False

    except smtplib.SMTPAuthenticationError as auth_err:
        logging.error(f"SMTP auth failed: {auth_err}", exc_info=True)
        st.session_state.error_message = "SMTP authentication failed. Check EMAIL_ADDRESS and EMAIL_PASSWORD."
        return False

    except Exception as e:
        logging.error(f"Failed to send email: {e}", exc_info=True)
        st.session_state.error_message = f"Failed to send email: {e}"
        return False


def analyze_sentiment(text):
    # Unchanged
    ...

def check_notes_relevance(sales_notes):
    # Unchanged
    ...

def generate_followup_email(...):
    # Unchanged
    ...

def generate_lost_email(customer_name, vehicle_name):
    # Unchanged
    ...

def generate_welcome_email(customer_name, vehicle_name):
    # Unchanged
    ...

def set_expanded_lead(request_id):
    # Unchanged
    ...

# --- Interpret & Query Function ---
METRIC_FUNCS = {
    "total":     lambda df: df.shape[0],
    "hot":       lambda df: df[df["lead_score"] == "Hot"].shape[0],
    "warm":      lambda df: df[df["lead_score"] == "Warm"].shape[0],
    "cold":      lambda df: df[df["lead_score"] == "Cold"].shape[0],
    "lost":      lambda df: df[df["action_status"] == "Lost"].shape[0],
    "converted": lambda df: df[df["action_status"] == "Converted"].shape[0],
    "follow up": lambda df: df[df["action_status"] == "Follow Up Required"].shape[0],
}

def interpret_and_query(query_text, df):
    text = query_text.lower()
    now = datetime.now()

    # Metric extraction
    metric = "total"
    for key in METRIC_FUNCS:
        if key in text and key != "total":
            metric = key
            break

    # Time-window parsing
    start_dt = end_dt = None
    if "today" in text:
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt   = now
    elif "yesterday" in text:
        yd = now - timedelta(days=1)
        start_dt = yd.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt   = start_dt + timedelta(days=1)

    m = re.search(r"last\s+(\d+)\s+days?", text)
    if m:
        start_dt = now - timedelta(days=int(m.group(1)))
        end_dt   = now
    else:
        m = re.search(r"last\s+(\d+)\s+weeks?", text)
        if m:
            start_dt = now - timedelta(weeks=int(m.group(1)))
            end_dt   = now
        else:
            m = re.search(r"last\s+(\d+)\s+months?", text)
            if m:
                start_dt = now - timedelta(days=30 * int(m.group(1)))
                end_dt   = now

    # Apply time filters
    df_filt = df
    if start_dt:
        df_filt = df_filt[df_filt["booking_timestamp"] >= start_dt]
    if end_dt:
        df_filt = df_filt[df_filt["booking_timestamp"] <= end_dt]

    # Compute
    count = METRIC_FUNCS[metric](df_filt)

    # Build response
    desc_map = {
        "total":"leads", "hot":"hot leads", "warm":"warm leads", 
        "cold":"cold leads","lost":"lost leads","converted":"converted leads",
        "follow up":"leads requiring follow-up"
    }
    desc = desc_map[metric]

    if start_dt and end_dt:
        if "today" in text:
            time_str = " today"
        elif "yesterday" in text:
            time_str = " yesterday"
        elif m:
            unit = "days" if "day" in m.group(0) else "weeks" if "week" in m.group(0) else "months"
            time_str = f" in the last {m.group(1)} {unit}"
        else:
            time_str = ""
    else:
        time_str = " of all time"

    return f"📊 You have **{count}** {desc}{time_str}."

# --- MAIN DASHBOARD DISPLAY LOGIC ---
st.set_page_config(page_title="AOE Motors Test Drive Dashboard", layout="wide")
st.title("🚗 AOE Motors Test Drive Bookings")
st.markdown("---")

# Initialize session state variables
for key in ['expanded_lead_id','info_message','success_message','error_message']:
    if key not in st.session_state:
        st.session_state[key] = None

# Display any pending messages
if st.session_state.info_message:
    st.info(st.session_state.pop('info_message'))
if st.session_state.success_message:
    st.success(st.session_state.pop('success_message'))
if st.session_state.error_message:
    st.error(st.session_state.pop('error_message'))

# Sidebar filters and test email button
st.sidebar.header("Filters")
if ENABLE_EMAIL_SENDING:
    if st.sidebar.button("Send Test Email"):
        st.sidebar.info("Sending test email...")
        ok = send_email(email_address, "AOE Dashboard Test Email",
                        "This is a test email from your AOE Dashboard.")
        if ok:
            st.sidebar.success("Test email sent successfully!")
        else:
            st.sidebar.error("Test email failed. Check logs.")
else:
    st.sidebar.warning("Email not configured. Cannot send test email.")

locations = ["All Locations","New York","Los Angeles","Chicago","Houston","Miami"]
sel_loc = st.sidebar.selectbox("Filter by Location", locations)
col1, col2 = st.sidebar.columns(2)
with col1:
    start_date = st.date_input("Start Date", value=datetime.today().date())
with col2:
    end_date   = st.date_input("End Date", value=datetime.today().date()+timedelta(days=1))

# Fetch and display data
bookings_data = fetch_bookings_data(sel_loc, start_date, end_date)
if bookings_data:
    df = pd.DataFrame(bookings_data)
    df['booking_timestamp'] = pd.to_datetime(df['booking_timestamp'])
    df = df.sort_values('booking_timestamp', ascending=False)

    # Analytics section
    st.subheader("Analytics - Ask a Question! 🤖")
    query = st.text_input("Type your question (e.g. 'hot leads last week'): ", key="nlq_query_input")
    if query:
        msg = interpret_and_query(query, df)
        st.markdown(msg)
    st.markdown("---")

    # Iterate through bookings
    for _, row in df.iterrows():
        # Existing expander/form/email logic unchanged
        ...
else:
    st.info("No test drive bookings to display yet.")

st.markdown("---")
