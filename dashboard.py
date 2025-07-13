import streamlit as st
import pandas as pd
import smtplib
import logging
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configure logging
logging.basicConfig(filename="dashboard_debug.log", level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# Email Configuration (environment or manually set)
email_host = "smtp.gmail.com"
email_port = 465
email_address = "your_email@example.com"
email_password = "your_password"
ENABLE_EMAIL_SENDING = all([email_host, email_port, email_address, email_password])

# Page setup
st.set_page_config(layout="wide")
st.markdown('✅ App loaded successfully.')

# Load data (replace this with your real Supabase fetch)
@st.cache_data
def load_data():
    logging.debug("Loading mock data...")
    data = {
        "name": ["Alice", "Bob", "Charlie", "David"],
        "email": ["a@example.com", "b@example.com", "c@example.com", "d@example.com"],
        "lead_score": ["Hot", "Warm", "Cold", "Hot"],
        "status": ["converted", "follow-up", "lost", "converted"],
        "booking_timestamp": pd.to_datetime([
            datetime.utcnow(),
            datetime.utcnow() - timedelta(days=2),
            datetime.utcnow() - timedelta(days=8),
            datetime.utcnow() - timedelta(days=1)
        ])
    }
    return pd.DataFrame(data)

df = load_data()

# EMAIL FUNCTION (FIXED)
def send_email(recipient_email, subject, body):
    if not ENABLE_EMAIL_SENDING:
        logging.error("Email sending is disabled. Credentials not fully configured.")
        st.session_state.error_message = "Email sending is disabled. Credentials not fully configured."
        return False

    msg = MIMEMultipart()
    msg["From"] = email_address
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL(email_host, email_port) as server:
            logging.debug("Logging into SMTP server…")
            server.login(email_address, email_password)
            logging.debug("Sending email…")
            server.send_message(msg)
            logging.info("Email successfully sent to %s", recipient_email)
        st.session_state.success_message = f"Email successfully sent to {recipient_email}!"
        return True
    except Exception as e:
        logging.exception("Failed to send email: %s", e)
        st.session_state.error_message = f"Failed to send email: {e}"
        return False

# INTERPRET & QUERY FUNCTION
def interpret_and_query(query, df):
    query = query.lower()
    now = pd.Timestamp.utcnow()
    last_week = now - pd.Timedelta(days=7)
    last_month = now - pd.Timedelta(days=30)

    df["booking_timestamp"] = pd.to_datetime(df["booking_timestamp"], utc=True)

    if "total leads today" in query:
        count = df[df["booking_timestamp"].dt.date == now.date()].shape[0]
        return f"Total leads today: {count}"
    elif "total leads last week" in query:
        count = df[(df["booking_timestamp"] >= last_week)].shape[0]
        return f"Total leads in the last 7 days: {count}"
    elif "hot leads last week" in query:
        count = df[
            (df["lead_score"].str.lower() == "hot") &
            (df["booking_timestamp"] >= last_week)
        ].shape[0]
        return f"Hot leads in the last 7 days: {count}"
    elif "warm leads" in query:
        count = df[df["lead_score"].str.lower() == "warm"].shape[0]
        return f"Total warm leads: {count}"
    elif "cold leads" in query:
        count = df[df["lead_score"].str.lower() == "cold"].shape[0]
        return f"Total cold leads: {count}"
    elif "converted" in query:
        count = df[df["status"].str.lower() == "converted"].shape[0]
        return f"All-time converted leads: {count}"
    elif "lost" in query:
        count = df[df["status"].str.lower() == "lost"].shape[0]
        return f"Total lost leads: {count}"
    elif "follow" in query:
        count = df[df["status"].str.contains("follow", case=False)].shape[0]
        return f"Leads needing follow-up: {count}"
    else:
        return "🤖 Sorry, I couldn’t understand the question."

# UI - ANALYTICS
st.markdown('🔍 Loading Analytics section...')
st.subheader("Analytics - Ask a Question! 🤖")
query_text = st.text_input("Type your question (e.g., 'total leads today', 'hot leads last week', 'total conversions', 'leads lost'):")

if query_text:
    logging.debug(f"User query: {query_text}")
    result_message = interpret_and_query(query_text, df)
    st.success(result_message)

# UI - TEST DRIVE FORM
st.markdown('🚗 Rendering Test Drive form...')
st.subheader("Book a Test Drive")
with st.form("test_drive_form"):
    name = st.text_input("Name")
    email = st.text_input("Email")
    selected_vehicle = st.selectbox("Vehicle Model", ["Apex Luxury", "Thunder SUV", "Volt Electric"])
    message = f"Hi {name}, thank you for booking a test drive for {selected_vehicle}!"
    submit = st.form_submit_button("Submit")

    if submit:
        logging.debug(f"Test drive submitted: Name={name}, Email={email}, Vehicle={selected_vehicle}")
        if send_email(email, "Test Drive Confirmation", message):
            st.success("Confirmation email sent.")
        else:
            st.error("Failed to send confirmation.")
