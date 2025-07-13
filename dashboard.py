import streamlit as st
import pandas as pd
import smtplib
from email.message import EmailMessage
from datetime import datetime
from supabase import create_client, Client
import os
import logging

logging.basicConfig(level=logging.INFO)

# Supabase configuration
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

# Email config
email_host = os.getenv("EMAIL_HOST")
email_port = int(os.getenv("EMAIL_PORT", "465"))
email_address = os.getenv("EMAIL_ADDRESS")
email_password = os.getenv("EMAIL_PASSWORD")
ENABLE_EMAIL_SENDING = all([email_host, email_port, email_address, email_password])

def send_email(recipient, subject, body):
    try:
        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = email_address
        msg["To"] = recipient

        with smtplib.SMTP_SSL(email_host, email_port) as server:
            server.login(email_address, email_password)
            server.send_message(msg)
        logging.info(f"Email sent to {recipient}")
        return True
    except Exception as e:
        logging.error(f"Failed to send email: {e}", exc_info=True)
        return False

# Your other functions like update_booking_field, Supabase query etc...
# Keep all your original unchanged logic here...

# interpret_and_query patch — timezone-aware
def interpret_and_query(query, df):
    query = query.lower()
    now = pd.Timestamp.now(tz='UTC')
    last_week = now - pd.Timedelta(days=7)
    last_month = now - pd.Timedelta(days=30)

    df["booking_timestamp"] = pd.to_datetime(df["booking_timestamp"], utc=True)

    if "hot" in query and "week" in query:
        count = df[(df["lead_score"] == "Hot") & (df["booking_timestamp"] >= last_week)].shape[0]
        return f"Hot leads in the last 7 days: {count}"
    elif "warm" in query:
        count = df[df["lead_score"] == "Warm"].shape[0]
        return f"Warm leads: {count}"
    elif "cold" in query:
        count = df[df["lead_score"] == "Cold"].shape[0]
        return f"Cold leads: {count}"
    elif "converted" in query:
        count = df[df["action_status"] == "Converted"].shape[0]
        return f"All-time converted leads: {count}"
    elif "lost" in query:
        count = df[df["action_status"] == "Lost"].shape[0]
        return f"Leads marked as lost: {count}"
    elif "total" in query:
        count = df.shape[0]
        return f"Total leads: {count}"
    else:
        return "🤖 Sorry, I couldn’t understand the question."

# App UI
st.set_page_config(page_title="AOE Motors Dashboard", layout="wide")
st.title("AOE Motors Dashboard")
st.markdown("✅ App loaded successfully.")

# Load data, filter by lead status, booking date, etc...
# Show leads, scoring, action dropdowns, follow-up buttons...
# Analytics section where interpret_and_query is used

# Keep all other parts of your dashboard.py unchanged
