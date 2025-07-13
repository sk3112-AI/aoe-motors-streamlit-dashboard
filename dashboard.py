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

# Load .env variables
load_dotenv()

# --- Logging Setup ---
logging.basicConfig(
    stream=sys.stdout,
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# --- GLOBAL CONFIGURATIONS ---
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
if not supabase_url or not supabase_key:
    logging.error("Supabase URL or Key not found. Set SUPABASE_URL and SUPABASE_KEY.")
    st.error("Supabase URL or Key not found. Please set environment variables.")
    st.stop()

supabase: Client = create_client(supabase_url, supabase_key)
SUPABASE_TABLE_NAME = "bookings"

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    logging.error("OpenAI API Key not found. Set OPENAI_API_KEY.")
    st.error("OpenAI API Key not found. Please set environment variable.")
    st.stop()
openai_client = OpenAI(api_key=openai_api_key)

email_host = os.getenv("EMAIL_HOST")
email_port_raw = os.getenv("EMAIL_PORT")
email_port = int(email_port_raw) if email_port_raw and email_port_raw.isdigit() else None
email_address = os.getenv("EMAIL_ADDRESS")
email_password = os.getenv("EMAIL_PASSWORD")
ENABLE_EMAIL_SENDING = all([email_host, email_port, email_address, email_password])
if not ENABLE_EMAIL_SENDING:
    logging.warning("Email credentials incomplete; email sending disabled.")
    st.warning("Email not configured; email sending disabled.")

# Hardcoded vehicle & competitor data
AOE_VEHICLE_DATA = { ... }  # Full data unchanged
COMPETITOR_VEHICLE_DATA = { ... }
AOE_TYPE_TO_COMPETITOR_SEGMENT_MAP = { ... }
ACTION_STATUS_MAP = {
    "Hot": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"],
    "Warm": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"],
    "Cold": ["New Lead", "Lost", "Converted"],
    "New": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"]
}

# --- FUNCTIONS ---
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
        return response.data or []
    except Exception as e:
        logging.error(f"Error fetching data: {e}", exc_info=True)
        st.error(f"Error fetching data: {e}")
        return []


def update_booking_field(request_id, field_name, new_value):
    try:
        response = supabase.from_(SUPABASE_TABLE_NAME).update({field_name: new_value}).eq('request_id', request_id).execute()
        if response.data:
            logging.info(f"Updated {field_name} for {request_id}")
            st.success(f"Updated {field_name} for {request_id}")
            st.cache_data.clear()
        else:
            raise Exception(response.error or "Unknown error")
    except Exception as e:
        logging.error(f"Error updating booking: {e}", exc_info=True)
        st.error(f"Error updating booking: {e}")


def send_email(recipient_email, subject, body):
    if not ENABLE_EMAIL_SENDING:
        logging.error("Email disabled: incomplete credentials")
        st.error("Email disabled: incomplete credentials")
        return False
    msg = MIMEMultipart()
    msg['From'] = email_address
    msg['To'] = recipient_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    try:
        try:
            server = smtplib.SMTP_SSL(email_host, email_port, timeout=10)
        except OSError as ssl_err:
            logging.warning(f"SMTP_SSL failed ({ssl_err}), trying STARTTLS")
            server = smtplib.SMTP(email_host, 587, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
        server.login(email_address, email_password)
        server.send_message(msg)
        server.quit()
        logging.info(f"Email sent to {recipient_email}")
        return True
    except smtplib.SMTPAuthenticationError:
        logging.error("SMTP auth failed")
        st.error("SMTP auth failed")
        return False
    except Exception as e:
        logging.error(f"Email send error: {e}")
        st.error(f"Email send error: {e}")
        return False


def analyze_sentiment(text):
    # Function body unchanged
    pass

def check_notes_relevance(sales_notes):
    # Function body unchanged
    pass

def generate_followup_email(customer_name, customer_email, vehicle_name, sales_notes, vehicle_details, current_vehicle_brand=None, sentiment=None):
    # Full implementation unchanged
    pass

def generate_lost_email(customer_name, vehicle_name):
    subject = f"We Miss You, {customer_name}!"
    body = f"Dear {customer_name},\n\nWe noticed you haven't moved forward with your interest in the {vehicle_name}.\nWe'd love to help if you have any feedback.\n\nSincerely,\nAOE Motors Team"
    return subject, body

def generate_welcome_email(customer_name, vehicle_name):
    subject = f"Welcome to the AOE Family, {customer_name}!"
    body = f"Dear {customer_name},\n\nWelcome to AOE Motors! We're thrilled you chose the {vehicle_name}.\nNext steps: ...\n\nSincerely,\nAOE Motors Team"
    return subject, body

# --- Interpret & Query at module scope ---
METRIC_FUNCS = {
    'total':     lambda df: df.shape[0],
    'hot':       lambda df: df[df['lead_score']=='Hot'].shape[0],
    'warm':      lambda df[df['lead_score']=='Warm'].shape[0],
    'cold':      lambda df[df['lead_score']=='Cold'].shape[0],
    'lost':      lambda df[df['action_status']=='Lost'].shape[0],
    'converted': lambda df[df['action_status']=='Converted'].shape[0],
    'follow up': lambda df[df['action_status']=='Follow Up Required'].shape[0]
}

def interpret_and_query(query_text, df):
    text = query_text.lower()
    now = datetime.now()
    metric = 'total'
    for key in METRIC_FUNCS:
        if key in text and key!='total':
            metric = key
            break
    start_dt = end_dt = None
    if 'today' in text:
        start_dt = now.replace(hour=0,minute=0,second=0,microsecond=0)
        end_dt = now
    elif 'yesterday' in text:
        yd = now - timedelta(days=1)
        start_dt = yd.replace(hour=0,minute=0,second=0,microsecond=0)
        end_dt = start_dt + timedelta(days=1)
    m = re.search(r'last\s+(\d+)\s+days?', text)
    if m:
        start_dt = now - timedelta(days=int(m.group(1)))
        end_dt = now
    else:
        m = re.search(r'last\s+(\d+)\s+weeks?', text)
        if m:
            start_dt = now - timedelta(weeks=int(m.group(1)))
            end_dt = now
        else:
            m = re.search(r'last\s+(\d+)\s+months?', text)
            if m:
                start_dt = now - timedelta(days=30*int(m.group(1)))
                end_dt = now
    df_filt = df
    if start_dt: df_filt = df_filt[df_filt['booking_timestamp']>=start_dt]
    if end_dt:   df_filt = df_filt[df_filt['booking_timestamp']<=end_dt]
    count = METRIC_FUNCS[metric](df_filt)
    desc_map = {'total':'leads','hot':'hot leads','warm':'warm leads','cold':'cold leads','lost':'lost leads','converted':'converted leads','follow up':'leads requiring follow-up'}
    desc = desc_map[metric]
    if start_dt and end_dt:
        if 'today' in text: time_str=' today'
        elif 'yesterday' in text: time_str=' yesterday'
        elif m: unit='days' if 'day' in m.group(0) else 'weeks' if 'week' in m.group(0) else 'months'; time_str=f' in the last {m.group(1)} {unit}'
        else: time_str=''
    else:
        time_str=' of all time'
    return f"📊 You have **{count}** {desc}{time_str}."

# --- MAIN DASHBOARD DISPLAY LOGIC ---
st.set_page_config(page_title="AOE Motors Test Drive Dashboard", layout="wide")
st.title("🚗 AOE Motors Test Drive Bookings")
st.markdown("---")
if 'expanded_lead_id' not in st.session_state: st.session_state['expanded_lead_id']=None
if 'info_message'    not in st.session_state: st.session_state['info_message']=None
if 'success_message' not in st.session_state: st.session_state['success_message']=None
if 'error_message'   not in st.session_state: st.session_state['error_message']=None

# Show messages
if st.session_state['info_message']: st.info(st.session_state.pop('info_message'))
if st.session_state['success_message']: st.success(st.session_state.pop('success_message'))
if st.session_state['error_message']: st.error(st.session_state.pop('error_message'))

# Sidebar
st.sidebar.header("Filters")
if ENABLE_EMAIL_SENDING:
    if st.sidebar.button("Send Test Email"):
        st.sidebar.info("Sending test email...")
        ok = send_email(email_address, "AOE Dashboard Test Email", "Test email from dashboard.")
        if ok: st.sidebar.success("Test email sent!")
        else: st.sidebar.error("Test email failed.")
else:
    st.sidebar.warning("Email not configured.")
locations=["All Locations","New York","Los Angeles","Chicago","Houston","Miami"]
sel_loc=st.sidebar.selectbox("Filter by Location",locations)
col1,col2=st.sidebar.columns(2)
with col1: start_date=st.date_input("Start Date",datetime.today().date())
with col2: end_date=st.date_input("End Date",datetime.today().date()+timedelta(days=1))

bookings_data=fetch_bookings_data(sel_loc,start_date,end_date)
if bookings_data:
    df=pd.DataFrame(bookings_data)
    df['booking_timestamp']=pd.to_datetime(df['booking_timestamp'])
    df=df.sort_values('booking_timestamp',ascending=False)
    
    st.subheader("Analytics - Ask a Question! 🤖")
    query=st.text_input("Type your question (e.g. 'hot leads last week'):",key="nlq_query_input")
    if query:
        st.markdown(interpret_and_query(query,df))
    st.markdown("---")

    for _,row in df.iterrows():
        with st.expander(f"**{row['full_name']}** - {row['vehicle']} - Status: **{row['action_status']}**"):
            st.write(f"**Email:** {row['email']}")
            # More UI unchanged...
else:
    st.info("No test drive bookings to display yet.")
st.markdown("---")
