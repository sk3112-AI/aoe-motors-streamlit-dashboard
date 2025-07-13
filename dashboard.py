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
import re  # added for analytics parsing

load_dotenv()

# --- GLOBAL CONFIGURATIONS (ALL AT THE VERY TOP) ---
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    st.error("Supabase URL or Key not found. Please ensure they are set as environment variables.")
    st.stop()

supabase: Client = create_client(supabase_url, supabase_key)
SUPABASE_TABLE_NAME = "bookings"
EMAIL_INTERACTIONS_TABLE_NAME = "email_interactions"

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    st.error("OpenAI API Key not found. Please ensure it is set as an environment variable.")
    st.stop()
openai_client = OpenAI(api_key=openai_api_key)

email_host = os.getenv("EMAIL_HOST")
email_port_str = os.getenv("EMAIL_PORT")
email_port = int(email_port_str) if email_port_str else 0
email_address = os.getenv("EMAIL_ADDRESS")
email_password = os.getenv("EMAIL_PASSWORD")

ENABLE_EMAIL_SENDING = all([email_host, email_port, email_address, email_password])
if not ENABLE_EMAIL_SENDING:
    st.warning("Email credentials not fully configured. Email sending will be disabled.")

BACKEND_API_URL = "https://aoe-agentic-demo.onrender.com"

AOE_VEHICLE_DATA = {
    "AOE Apex": {
        "type": "Luxury Sedan",
        "powertrain": "Gasoline",
        "features": "Premium leather interior, Advanced driver-assistance systems (ADAS), Panoramic sunroof, Bose premium sound system, Adaptive cruise control, Lane-keeping assist, Automated parking, Heated and ventilated seats."
    },
    "AOE Volt": {
        "type": "Electric Compact",
        "powertrain": "Electric",
        "features": "Long-range battery (500 miles), Fast charging (80% in 20 min), Regenerative braking, Solar roof charging, Vehicle-to-Grid (V2G) capability, Digital cockpit, Over-the-air updates, Extensive charging network access."
    },
    "AOE Thunder": {
        "type": "Performance SUV",
        "powertrain": "Gasoline",
        "features": "V8 Twin-Turbo Engine, Adjustable air suspension, Sport Chrono Package, High-performance braking system, Off-road capabilities, Torque vectoring, 360-degree camera, Ambient lighting, Customizable drive modes."
    }
}

COMPETITOR_VEHICLE_DATA = {
    "Ford": {
        "Sedan": {
            "model_name": "Ford Sedan (e.g., Fusion/Taurus equivalent)",
            "features": "2.5L IVCT Atkinson Cycle I-4 Hybrid Engine; 210 Total System Horsepower; Dual-Zone Electronic Automatic Temperature Control; Heated Front Row Seats"
        },
        "SUV": {
            "model_name": "Ford SUV (e.g., Explorer/Expedition equivalent)",
            "features": "Available 440 horsepower 3.5L EcoBoost® V6 High-Output engine, Antilock Brake Systems (ABS), Front-Seat Side-Impact Airbags, SOS Post-Crash Alert System™"
        },
        "EV": {
            "model_name": "Ford EV (e.g., Mustang Mach-E/F-150 Lightning equivalent)",
            "features": "260 miles of EPA-est. range* with standard-range battery and RWD, 387 lb.-ft. of torque† with standard-range battery and RWD, Premium model features (heated/ventilated front seats trimmed with ActiveX® material), SYNC® 4A, over-the-air updates"
        }
    }
}

AOE_TYPE_TO_COMPETITOR_SEGMENT_MAP = {
    "Luxury Sedan": "Sedan",
    "Electric Compact": "EV",
    "Performance SUV": "SUV"
}

ACTION_STATUS_MAP = {
    "Hot": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"],
    "Warm": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"],
    "Cold": ["New Lead", "Lost", "Converted"],
    "New": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"]
}


# --- ALL FUNCTION DEFINITIONS (STRICTLY AFTER CONFIGS AND BEFORE UI RENDERING) ---

@st.cache_data(ttl=30)
def fetch_bookings_data(location_filter=None, start_date_filter=None, end_date_filter=None):
    try:
        query = supabase.from_(SUPABASE_TABLE_NAME).select(
            "request_id, full_name, email, vehicle, booking_date, current_vehicle, location, time_frame, action_status, sales_notes, lead_score, numeric_lead_score, booking_timestamp"
        ).order('booking_timestamp', desc=True)

        if location_filter and location_filter != "All Locations":
            query = query.eq('location', location_filter)
        if start_date_filter:
            query = query.gte('booking_timestamp', start_date_filter.isoformat())
        if end_date_filter:
            query = query.lte('booking_timestamp', (end_date_filter + timedelta(days=1)).isoformat())

        response = query.execute()

        if response.data:
            return response.data
        else:
            return []
    except Exception as e:
        logging.error(f"Error fetching data from Supabase: {e}", exc_info=True)
        st.session_state.error_message = f"Error fetching data from Supabase: {e}"
        return []

def update_booking_field(request_id, field_name, new_value):
    try:
        response = supabase.from_(SUPABASE_TABLE_NAME).update({field_name: new_value}).eq('request_id', request_id).execute()
        if response.data:
            logging.info(f"Successfully updated {field_name} for {request_id}!")
            st.session_state.success_message = f"Successfully updated {field_name} for {request_id}!"
            st.cache_data.clear()
        else:
            logging.error(f"Failed to update {field_name} for {request_id}. Response: {response}")
            st.session_state.error_message = f"Failed to update {field_name} for {request_id}. Response: {response}"
    except Exception as e:
        logging.error(f"Error updating {field_name} in Supabase: {e}", exc_info=True)
        st.session_state.error_message = f"Error updating {field_name} in Supabase: {e}"

# Updated email function with SSL/STARTTLS fallback
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
        # First attempt SSL
        try:
            server = smtplib.SMTP_SSL(email_host, email_port, timeout=10)
        except OSError as ssl_err:
            logging.warning(f"SMTP_SSL failed ({ssl_err}); falling back to STARTTLS on port 587")
            server = smtplib.SMTP(email_host, 587, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
        server.login(email_address, email_password)
        server.send_message(msg)
        server.quit()
        logging.info(f"Email successfully sent to {recipient_email}!")
        st.session_state.success_message = f"Email successfully sent to {recipient_email}!"
        return True
    except OSError as net_err:
        logging.error(f"Network error sending email: {net_err}", exc_info=True)
        st.session_state.error_message = (
            "Network error: Unable to reach SMTP server. "
            "Check your VPC/outbound settings or SMTP host/port."
        )
        return False
    except smtplib.SMTPAuthenticationError as auth_err:
        logging.error(f"SMTP authentication failed: {auth_err}", exc_info=True)
        st.session_state.error_message = "SMTP authentication failed. Check EMAIL_ADDRESS and EMAIL_PASSWORD."
        return False
    except Exception as e:
        logging.error(f"Failed to send email: {e}", exc_info=True)
        st.session_state.error_message = f"Failed to send email: {e}"
        return False


def analyze_sentiment(text):
    # unchanged
    if not text.strip():
        return "NEUTRAL"
    ...

def check_notes_relevance(sales_notes):
    # unchanged
    if not sales_notes.strip():
        return "IRRELEVANT"
    ...

def generate_followup_email(customer_name, customer_email, vehicle_name, sales_notes, vehicle_details, current_vehicle_brand=None, sentiment=None):
    # unchanged
    ...

def generate_lost_email(customer_name, vehicle_name):
    # unchanged
    ...

def generate_welcome_email(customer_name, vehicle_name):
    # unchanged
    ...

def set_expanded_lead(request_id):
    # unchanged
    ...

# --- Text-to-Query Section ---
# --- Analytics Helper Functions: Metric Mapping & NL Query ---
METRIC_FUNCS = {
    "total": lambda df: df.shape[0],
    "hot": lambda df: df[df["lead_score"] == "Hot"].shape[0],
    "warm": lambda df: df[df["lead_score"] == "Warm"].shape[0],
    "cold": lambda df: df[df["lead_score"] == "Cold"].shape[0],
    "lost": lambda df: df[df["action_status"] == "Lost"].shape[0],
    "converted": lambda df: df[df["action_status"] == "Converted"].shape[0],
    "follow up": lambda df: df[df["action_status"] == "Follow Up Required"].shape[0]
}

def interpret_and_query(query_text, df):
    text = query_text.lower()
    now = datetime.now()

    # 1) Metric Extraction
    metric = "total"
    for key in METRIC_FUNCS:
        if key in text and key != "total":
            metric = key
            break

    # 2) Time-Window Parsing
    start_dt, end_dt = None, None
    if "today" in text:
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
    elif "yesterday" in text:
        yd = now - timedelta(days=1)
        start_dt = yd.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = start_dt + timedelta(days=1)

    m = re.search(r"last\s+(\d+)\s+days?", text)
    if m:
        start_dt = now - timedelta(days=int(m.group(1)))
        end_dt = now
    else:
        m = re.search(r"last\s+(\d+)\s+weeks?", text)
        if m:
            start_dt = now - timedelta(weeks=int(m.group(1)))
            end_dt = now
        else:
            m = re.search(r"last\s+(\d+)\s+months?", text)
            if m:
                start_dt = now - timedelta(days=30 * int(m.group(1)))
                end_dt = now

    # 3) Apply filters
    df_filt = df
    if start_dt:
        df_filt = df_filt[df_filt["booking_timestamp"] >= start_dt]
    if end_dt:
        df_filt = df_filt[df_filt["booking_timestamp"] <= end_dt]

    # 4) Compute
    count = METRIC_FUNCS[metric](df_filt)

    # 5) Build human-friendly response
    descriptions = {
        "total": "leads",
        "hot": "hot leads",
        "warm": "warm leads",
        "cold": "cold leads",
        "lost": "lost leads",
        "converted": "converted leads",
        "follow up": "leads requiring follow-up"
    }
    desc = descriptions[metric]

    # Describe time window
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

st.subheader("Analytics - Ask a Question! 🤖")
query_text = st.text_input(
    "Type your question (e.g., 'total leads today', 'hot leads last week', 'total conversions', 'leads lost'):",
    key="nlq_query_input"
)
if query_text:
    result_message = interpret_and_query(query_text, df)
    st.markdown(result_message)
st.markdown("---")

# ... rest of dashboard UI unchanged ...
