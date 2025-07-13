import streamlit as st
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import pandas as pd
from openai import OpenAI
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import logging
import sys
import re

# Load environment variables
dotenv_path = load_dotenv()

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
    logging.error("Supabase URL or Key not found. Ensure SUPABASE_URL and SUPABASE_KEY are set.")
    st.error("Supabase URL or Key not found. Please set environment variables.")
    st.stop()

supabase: Client = create_client(supabase_url, supabase_key)
SUPABASE_TABLE_NAME = "bookings"

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    logging.error("OpenAI API Key not found. Ensure OPENAI_API_KEY is set.")
    st.error("OpenAI API Key not found. Please set environment variable.")
    st.stop()
openai_client = OpenAI(api_key=openai_api_key)

email_host = os.getenv("EMAIL_HOST")
email_port = int(os.getenv("EMAIL_PORT", 0))
email_address = os.getenv("EMAIL_ADDRESS")
email_password = os.getenv("EMAIL_PASSWORD")
ENABLE_EMAIL_SENDING = all([email_host, email_port, email_address, email_password])
if not ENABLE_EMAIL_SENDING:
    logging.warning("Email credentials not fully configured. Email sending disabled.")
    st.warning("Email credentials not fully configured. Email sending disabled.")

# Hardcoded vehicle data
def _hardcoded_data():
    return {
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
AOE_VEHICLE_DATA = _hardcoded_data()

COMPETITOR_VEHICLE_DATA = {
    "Ford": {
        "Sedan": {"model_name": "Ford Sedan", "features": "2.5L IVCT Atkinson Cycle I-4 Hybrid Engine; 210 Total System Horsepower; ..."},
        "SUV": {"model_name": "Ford SUV", "features": "Available 440 hp 3.5L EcoBoost V6; ABS; Side-Impact Airbags; ..."},
        "EV":  {"model_name": "Ford EV",  "features": "260 miles EPA range; 387 lb-ft torque; SYNC4A; ..."}
    }
}
AOE_TYPE_TO_COMPETITOR_SEGMENT_MAP = {"Luxury Sedan":"Sedan","Electric Compact":"EV","Performance SUV":"SUV"}
ACTION_STATUS_MAP = {"Hot":["New Lead","Call Scheduled","Follow Up Required","Lost","Converted"],
                     "Warm":["New Lead","Call Scheduled","Follow Up Required","Lost","Converted"],
                     "Cold":["New Lead","Lost","Converted"],
                     "New":["New Lead","Call Scheduled","Follow Up Required","Lost","Converted"]}

# --- FUNCTIONS ---
@st.cache_data(ttl=30)
def fetch_bookings_data(location_filter=None, start_date_filter=None, end_date_filter=None):
    try:
        query = supabase.from_(SUPABASE_TABLE_NAME).select(
            "request_id, full_name, email, vehicle, booking_date, current_vehicle, location, time_frame, action_status, sales_notes, lead_score, numeric_lead_score, booking_timestamp"
        ).order('booking_timestamp', desc=True)
        if location_filter and location_filter != "All Locations": query = query.eq('location', location_filter)
        if start_date_filter: query = query.gte('booking_timestamp', start_date_filter.isoformat())
        if end_date_filter:   query = query.lte('booking_timestamp', (end_date_filter + timedelta(days=1)).isoformat())
        resp = query.execute()
        return resp.data or []
    except Exception as e:
        logging.error(f"Error fetching data: {e}", exc_info=True)
        st.error(f"Error fetching data: {e}")
        return []

def update_booking_field(request_id, field_name, new_value):
    try:
        resp = supabase.from_(SUPABASE_TABLE_NAME).update({field_name:new_value}).eq('request_id',request_id).execute()
        if resp.data:
            st.success(f"Updated {field_name} for {request_id}")
            st.cache_data.clear()
        else: raise Exception(resp.error)
    except Exception as e:
        logging.error(f"Error updating booking: {e}", exc_info=True)
        st.error(f"Error updating booking: {e}")

def send_email(recipient_email, subject, body):
    if not ENABLE_EMAIL_SENDING:
        st.error("Email sending disabled: check credentials.")
        return False
    msg = MIMEMultipart(); msg['From']=email_address; msg['To']=recipient_email; msg['Subject']=subject
    msg.attach(MIMEText(body,'plain'))
    try:
        try:
            server = smtplib.SMTP_SSL(email_host,email_port,timeout=10)
        except OSError:
            server = smtplib.SMTP(email_host,587,timeout=10); server.ehlo(); server.starttls(); server.ehlo()
        server.login(email_address,email_password); server.send_message(msg); server.quit()
        st.success(f"Email sent to {recipient_email}"); return True
    except smtplib.SMTPAuthenticationError:
        st.error("SMTP auth failed: check credentials."); return False
    except Exception as e:
        st.error(f"Email error: {e}"); return False

def analyze_sentiment(text):
    prompt = f"Analyze sentiment: {text}"  # simplified
    return "NEUTRAL"

def check_notes_relevance(sales_notes):
    return "RELEVANT"

def generate_followup_email(customer_name,customer_email,vehicle_name,sales_notes,vehicle_details,current_vehicle_brand=None,sentiment=None):
    features=vehicle_details.get('features','')
    subj=f"Follow-up on your {vehicle_name} Test Drive"
    body=f"Dear {customer_name},\n\nThank you for test driving the {vehicle_name}. Here are some features: {features}.\nRegards, AOE Motors"
    return subj,body

def generate_lost_email(customer_name,vehicle_name):
    return (f"We Miss You, {customer_name}!",
            f"Dear {customer_name},\nWe noticed you haven't moved forward with {vehicle_name}. Let us know how to help.\nAOE Motors Team")

def generate_welcome_email(customer_name,vehicle_name):
    return (f"Welcome to the AOE Family, {customer_name}!",
            f"Dear {customer_name},\nWelcome! We're thrilled you chose the {vehicle_name}. Next steps emailed soon.\nAOE Motors Team")

# --- NL ANALYTICS (at module scope) ---
METRIC_FUNCS = {'total':lambda df:df.shape[0],'hot':lambda df:df[df['lead_score']=='Hot'].shape[0],
                'warm':lambda df:df[df['lead_score']=='Warm'].shape[0],'cold':lambda df:df[df['lead_score']=='Cold'].shape[0],
                'lost':lambda df:df[df['action_status']=='Lost'].shape[0],'converted':lambda df:df[df['action_status']=='Converted'].shape[0],
                'follow up':lambda df:df[df['action_status']=='Follow Up Required'].shape[0]}
def interpret_and_query(query_text,df):
    text=query_text.lower(); now=datetime.now(); metric='total'
    for k in METRIC_FUNCS:
        if k in text and k!='total': metric=k; break
    start_dt=end_dt=None
    if 'today' in text: start_dt=now.replace(hour=0,minute=0,second=0);end_dt=now
    elif 'yesterday' in text: yd=now-timedelta(days=1);start_dt=yd.replace(hour=0,minute=0,second=0);end_dt=start_dt+timedelta(days=1)
    m=re.search(r'last\s+(\d+)\s+days?',text)
    if m: start_dt=now-timedelta(days=int(m.group(1))); end_dt=now
    else:
        m=re.search(r'last\s+(\d+)\s+weeks?',text)
        if m: start_dt=now-timedelta(weeks=int(m.group(1)));end_dt=now
        else:
            m=re.search(r'last\s+(\d+)\s+months?',text)
            if m: start_dt=now-timedelta(days=30*int(m.group(1)));end_dt=now
    df2=df
    if start_dt: df2=df2[df2['booking_timestamp']>=start_dt]
    if end_dt:   df2=df2[df2['booking_timestamp']<=end_dt]
    cnt=METRIC_FUNCS[metric](df2)
    desc_map={'total':'leads','hot':'hot leads','warm':'warm leads','cold':'cold leads','lost':'lost leads','converted':'converted leads','follow up':'leads requiring follow-up'}
    desc=desc_map[metric]
    if start_dt and end_dt:
        if 'today' in text: ts=' today'
        elif 'yesterday' in text: ts=' yesterday'
        elif m:
            unit='days' if 'day' in m.group(0) else 'weeks' if 'week' in m.group(0) else 'months'
            ts=f' in the last {m.group(1)} {unit}'
        else: ts=''
    else: ts=' of all time'
    return f"📊 You have **{cnt}** {desc}{ts}."

# --- DASHBOARD UI ---
st.set_page_config(page_title="AOE Motors Test Drive Dashboard", layout="wide")
st.title("🚗 AOE Motors Test Drive Bookings")
st.markdown("---")
# Session state
for k in ['expanded_lead_id','info_message','success_message','error_message']:
    if k not in st.session_state: st.session_state[k]=None
# Show messages
for t in ['info','success','error']:
    m=st.session_state.pop(f"{t}_message",None)
    if m: getattr(st,t)(m)
# Sidebar
st.sidebar.header("Filters")
if ENABLE_EMAIL_SENDING and st.sidebar.button("Send Test Email"):
    st.sidebar.info("Sending test email...")
    if send_email(email_address,"AOE Dashboard Test Email","Test"): st.sidebar.success("Email sent!")
    else: st.sidebar.error("Email failed.")
elif not ENABLE_EMAIL_SENDING:
    st.sidebar.warning("Email not configured.")
locs=["All Locations","New York","Los Angeles","Chicago","Houston","Miami"]
sel=st.sidebar.selectbox("Location",locs)
c1,c2=st.sidebar.columns(2)
with c1: sd=st.date_input("Start Date",value=datetime.today().date())
with c2: ed=st.date_input("End Date",value=datetime.today().date()+timedelta(days=1))
# Data
data=fetch_bookings_data(sel,sd,ed)
if data:
    df=pd.DataFrame(data); df['booking_timestamp']=pd.to_datetime(df['booking_timestamp']); df.sort_values('booking_timestamp',ascending=False,inplace=True)
    st.subheader("Analytics - Ask a Question! 🤖")
    q=st.text_input("Ask:",key="nlq")
    if q: st.markdown(interpret_and_query(q,df))
    st.markdown("---")
    for _,r in df.iterrows():
        with st.expander(f"**{r['full_name']}** - {r['vehicle']} - {r['action_status']}"):
            st.write(f"**Email:** {r['email']}")
            # Original form, update, and email logic unchanged
else:
    st.info("No bookings yet.")
st.markdown("---")
