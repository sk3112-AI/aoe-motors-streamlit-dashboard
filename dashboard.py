import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage
import pytz
import logging
import re
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)

# Email config
email_host = os.getenv("EMAIL_HOST", "smtp.gmail.com")
email_port = int(os.getenv("EMAIL_PORT", "465"))
email_user = os.getenv("EMAIL_USER")
email_password = os.getenv("EMAIL_PASSWORD")

# Supabase config
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
headers = {
    "apikey": supabase_key,
    "Authorization": f"Bearer {supabase_key}"
}

# UI setup
st.set_page_config(page_title="AOE Dashboard", layout="wide")
st.title("AOE Motors - Test Drive Dashboard")

# Load data
def fetch_data():
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    today_str = now.strftime("%Y-%m-%d")
    url = f"{supabase_url}/rest/v1/bookings?select=*&order=booking_timestamp.desc&booking_timestamp=gte.{today_str}"
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        data = pd.DataFrame(response.json())
        if not data.empty and "booking_timestamp" in data.columns:
            data["booking_timestamp"] = pd.to_datetime(data["booking_timestamp"]).dt.tz_convert("Asia/Kolkata")
        return data
    return pd.DataFrame()

df = fetch_data()

# --- Email sending logic ---
def send_email(to_email, subject, body):
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = email_user
        msg["To"] = to_email
        msg.set_content(body)

        with smtplib.SMTP_SSL(email_host, email_port, timeout=10) as server:
            server.login(email_user, email_password)
            server.send_message(msg)
        logging.info(f"✅ Email sent to {to_email} | Subject: {subject}")
    except Exception as e:
        logging.error(f"❌ Failed to send email: {e}", exc_info=True)

# --- Interpretation Logic for Analytics ---
def interpret_and_query(user_input, df):
    user_input = user_input.lower()
    now = datetime.now(pytz.timezone("Asia/Kolkata"))
    one_week_ago = now - timedelta(days=7)
    start_of_month = now.replace(day=1)

    # Ensure booking_timestamp is datetime and tz-aware
    if not pd.api.types.is_datetime64_any_dtype(df["booking_timestamp"]):
        df["booking_timestamp"] = pd.to_datetime(df["booking_timestamp"])
    if df["booking_timestamp"].dt.tz is None:
        df["booking_timestamp"] = df["booking_timestamp"].dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata")

    logging.info(f"Interpreting query: {user_input}")

    # Determine time window
    if "today" in user_input:
        mask = df["booking_timestamp"].dt.date == now.date()
    elif "last week" in user_input:
        mask = df["booking_timestamp"] >= one_week_ago
    elif "this month" in user_input:
        mask = df["booking_timestamp"] >= start_of_month
    else:
        mask = pd.Series([True] * len(df))  # All time

    if "hot" in user_input:
        count = df[mask & (df["lead_score"] == "Hot")].shape[0]
        return f"🔥 Hot leads: {count}"
    elif "warm" in user_input:
        count = df[mask & (df["lead_score"] == "Warm")].shape[0]
        return f"🌤️ Warm leads: {count}"
    elif "cold" in user_input:
        count = df[mask & (df["lead_score"] == "Cold")].shape[0]
        return f"❄️ Cold leads: {count}"
    elif "converted" in user_input:
        count = df[df["action_status"] == "Converted"].shape[0]
        return f"✅ All-time converted leads: {count}"
    elif "lost" in user_input:
        count = df[df["action_status"] == "Lost"].shape[0]
        return f"❌ All-time lost leads: {count}"
    elif "follow" in user_input:
        count = df[df["action_status"] == "Follow-up Required"].shape[0]
        return f"🔁 Leads needing follow-up: {count}"
    elif "total" in user_input or "all" in user_input:
        count = df[mask].shape[0]
        return f"📊 Total leads: {count}"
    else:
        return "❓ Sorry, I couldn't understand your query."

# --- Analytics UI ---
st.subheader("Analytics - Ask a Question! 🤖")
query_text = st.text_input("Type your question (e.g., 'total leads today', 'hot leads last week', 'total conversions', 'leads lost'):")
if query_text:
    result_message = interpret_and_query(query_text, df)
    st.success(result_message)
    logging.info(f"Analytics result: {result_message}")

# --- Dashboard Table Display (original logic assumed retained) ---
st.subheader("Customer Booking Dashboard")
if not df.empty:
    st.dataframe(df)
else:
    st.warning("No booking data found.")

