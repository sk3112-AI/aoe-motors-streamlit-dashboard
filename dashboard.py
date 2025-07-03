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

load_dotenv()

# --- Supabase Configuration ---
supabase_url = st.secrets["SUPABASE_URL"] if "SUPABASE_URL" in st.secrets else os.getenv("SUPABASE_URL")
supabase_key = st.secrets["SUPABASE_KEY"] if "SUPABASE_KEY" in st.secrets else os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    st.error("Supabase URL or Key not found. Please set them in your environment variables or Streamlit secrets.")
    st.stop()

supabase: Client = create_client(supabase_url, supabase_key)
SUPABASE_TABLE_NAME = "bookings"

# --- OpenAI Configuration ---
openai_api_key = st.secrets["OPENAI_API_KEY"] if "OPENAI_API_KEY" in st.secrets else os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    st.error("OpenAI API Key not found. Please set it in your environment variables or Streamlit secrets.")
    st.stop()
openai_client = OpenAI(api_key=openai_api_key)

# --- Email Configuration ---
email_host = st.secrets["EMAIL_HOST"] if "EMAIL_HOST" in st.secrets else os.getenv("EMAIL_HOST")
email_port = int(st.secrets["EMAIL_PORT"] if "EMAIL_PORT" in st.secrets else os.getenv("EMAIL_PORT", 0))
email_address = st.secrets["EMAIL_ADDRESS"] if "EMAIL_ADDRESS" in st.secrets else os.getenv("EMAIL_ADDRESS")
email_password = st.secrets["EMAIL_PASSWORD"] if "EMAIL_PASSWORD" in st.secrets else os.getenv("EMAIL_PASSWORD")

ENABLE_EMAIL_SENDING = all([email_host, email_port, email_address, email_password])
if not ENABLE_EMAIL_SENDING:
    st.warning("Email credentials not fully configured. Email sending will be disabled.")

# --- Backend API URL ---
# This URL points to your deployed main.py FastAPI application.
BACKEND_API_URL = "https://aoe-agentic-demo.onrender.com" # <--- YOUR DEPLOYED BACKEND URL

# --- Dashboard Title ---
st.set_page_config(page_title="AOE Motors Test Drive Dashboard", layout="wide")
st.title("🚗 AOE Motors Test Drive Bookings")
st.markdown("---")

# --- Function to Fetch Data from Supabase ---
@st.cache_data(ttl=30)
def fetch_bookings_data():
    """Fetches all booking data from Supabase, including lead_score."""
    try:
        response = supabase.from_(SUPABASE_TABLE_NAME).select(
            "request_id, full_name, email, vehicle, booking_date, current_vehicle, location, time_frame, action_status, sales_notes, lead_score"
        ).order('booking_date', desc=True).execute()

        if response.data:
            return response.data
        else:
            return []
    except Exception as e:
        st.error(f"Error fetching data from Supabase: {e}")
        return []

# --- Function to Fetch Vehicle Data from Backend ---
@st.cache_data(ttl=3600) # Cache vehicle data for 1 hour
def fetch_vehicles_data_from_backend():
    """
    Fetches vehicle data from the main.py backend API.
    This will trigger the scraping logic on the backend if needed.
    """
    try:
        response = requests.get(f"{BACKEND_API_URL}/vehicles-data")
        response.raise_for_status() # Raise an exception for HTTP errors
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching vehicle data from backend: {e}")
        # Fallback to a small hardcoded set if backend is unreachable or fails
        st.warning("Falling back to minimal hardcoded vehicle data due to backend connectivity issue.")
        return {
            "AOE Apex": {"type": "Luxury Sedan", "powertrain": "Gasoline", "features": "Generic features for Apex."},
            "AOE Thunder": {"type": "Performance SUV", "powertrain": "Gasoline", "features": "Generic features for Thunder."},
            "AOE Volt": {"type": "Electric Compact", "powertrain": "Electric", "features": "Generic features for Volt."}
        }


# --- Function to Update Data in Supabase ---
def update_booking_field(request_id, field_name, new_value):
    """Updates a specific field for a booking in Supabase using request_id."""
    try:
        response = supabase.from_(SUPABASE_TABLE_NAME).update({field_name: new_value}).eq('request_id', request_id).execute()
        if response.data:
            st.success(f"Successfully updated {field_name} for {request_id}!")
            st.cache_data.clear() # Clear dashboard cache
            fetch_vehicles_data_from_backend.clear() # Clear vehicle data cache too, if needed for immediate reflection (unlikely)
            st.rerun()
        else:
            st.error(f"Failed to update {field_name} for {request_id}. Response: {response}")
    except Exception as e:
        st.error(f"Error updating {field_name} in Supabase: {e}")

# --- Function to Generate Follow-up Email (AI) ---
def generate_followup_email(customer_name, customer_email, vehicle_name, sales_notes, vehicle_details):
    features_str = vehicle_details.get("features", "cutting-edge technology and a luxurious experience.")
    vehicle_type = vehicle_details.get("type", "vehicle")
    powertrain = vehicle_details.get("powertrain", "advanced performance")

    prompt = f"""
    Draft a polite, helpful, and persuasive follow-up email to a customer who recently test-drove an AOE {vehicle_name}.

    **Customer Information:**
    - Name: {customer_name}
    - Email: {customer_email}
    - Vehicle of Interest: {vehicle_name} ({vehicle_type}, {powertrain} powertrain)
    - Customer Issues/Comments (from sales notes): "{sales_notes}"

    **AOE {vehicle_name} Key Features:**
    - {features_str}

    **Email Instructions:**
    - Start with a polite greeting.
    - Acknowledge their test drive.
    - **Crucially, directly address the customer's stated issues from the sales notes.** For each issue mentioned, explain how specific features of the AOE {vehicle_name} (from the provided list) directly resolve or alleviate that concern.
        - If "high EV cost" is mentioned: Focus on long-term savings, reduced fuel costs, potential tax credits, Vehicle-to-Grid (V2G) if applicable (Volt).
        - If "charging anxiety" is mentioned: Highlight ultra-fast charging, solar integration (Volt), extensive charging network, range.
        - If other issues are mentioned: Adapt relevant features.
    - If no specific issues are mentioned, write a general follow-up highlighting key benefits.
    - End with a call to action to schedule another call or visit to discuss further.
    - Maintain a professional, empathetic, and persuasive tone.
    - **Output only the email content (Subject and Body), in plain text format.** Do NOT use HTML.
    - **Separate Subject and Body with "Subject: " at the beginning of the subject line.**
    """

    try:
        with st.spinner("Drafting email with AI..."):
            completion = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful and persuasive sales assistant for AOE Motors."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=600
            )
            draft = completion.choices[0].message.content.strip()
            if "Subject:" in draft:
                parts = draft.split("Subject:", 1)
                subject_line = parts[1].split("\n", 1)[0].strip()
                body_content = parts[1].split("\n", 1)[1].strip()
            else:
                subject_line = f"Following up on your AOE {vehicle_name} Test Drive"
                body_content = draft

            return subject_line, body_content

    except Exception as e:
        st.error(f"Error drafting email with AI: {e}")
        return None, None

# --- Function to Send Email ---
def send_email(recipient_email, subject, body):
    if not ENABLE_EMAIL_SENDING:
        st.error("Email sending is disabled. Credentials not fully configured.")
        return False

    msg = MIMEMultipart()
    msg["From"] = email_address
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL(email_host, email_port) as server:
            server.login(email_address, email_password)
            server.send_message(msg)
        st.success(f"Email successfully sent to {recipient_email}!")
        return True
    except Exception as e:
        st.error(f"Failed to send email: {e}")
        return False

# --- Main Dashboard Display Logic ---
st.header("Recent Test Drive Leads")

# Define fixed action status options based on lead score
ACTION_STATUS_MAP = {
    "Hot": ["New Lead", "Call Scheduled", "Follow Up Required", "Test Drive Completed", "Converted"],
    "Warm": ["New Lead", "Test Drive Completed"],
    "Cold": ["New Lead", "Review at a later date"],
    "New": ["New Lead", "Call Scheduled", "Follow Up Required", "Test Drive Completed", "Converted", "Review at a later date"] # Fallback for 'New' or any uncategorized
}

# Fetch all data needed for the dashboard
bookings_data = fetch_bookings_data()
# NEW: Fetch vehicle data from the backend
aoe_vehicles_data = fetch_vehicles_data_from_backend()


if bookings_data:
    df = pd.DataFrame(bookings_data)
    df = df.rename(columns={
        "request_id": "Request ID",
        "full_name": "Name",
        "email": "Email",
        "vehicle": "Vehicle",
        "booking_date": "Booking Date",
        "current_vehicle": "Current Vehicle",
        "location": "Location",
        "time_frame": "Time Frame",
        "action_status": "Action",
        "sales_notes": "Sales Notes",
        "lead_score": "Lead Score"
    })

    # Define a custom sort order for lead categories (Lead Score)
    score_order = {"Hot": 1, "Warm": 2, "New": 3, "Cold": 4}
    df['Lead Score Rank'] = df['Lead Score'].map(score_order).fillna(99)
    df = df.sort_values(by=['Lead Score Rank', 'Booking Date'], ascending=[True, False]).drop(columns='Lead Score Rank')

    # Display each booking in an expandable section (card-like)
    for index, row in df.iterrows():
        expander_title = f"**{row['Name']}** - {row['Vehicle']} ({row['Booking Date']}) - Status: **{row['Action']}** (Lead: **{row['Lead Score']}**)"
        with st.expander(expander_title):
            st.write(f"**Email:** {row['Email']}")
            st.write(f"**Location:** {row['Location']}")
            st.write(f"**Current Vehicle:** {row['Current Vehicle']}")
            st.write(f"**Time Frame (Purchase Intent):** {row['Time Frame']}")
            st.write(f"**Lead Score:** {row['Lead Score']}")
            st.write(f"**Request ID:** {row['Request ID']}")

            st.markdown("---") # Separator for updates

            # --- Action Status Update (Conditional based on Lead Score) ---
            current_lead_score = row['Lead Score']
            current_action = row['Action']

            # Get the correct list of action options based on the lead score
            lead_specific_action_options = ACTION_STATUS_MAP.get(current_lead_score, ACTION_STATUS_MAP["New"])

            # Ensure current_action is in the options, otherwise default to 'New Lead'
            try:
                action_index = lead_specific_action_options.index(current_action)
            except ValueError:
                action_index = lead_specific_action_options.index("New Lead") if "New Lead" in lead_specific_action_options else 0

            selected_action = st.selectbox(
                f"Update Action Status for {row['Name']} (Lead: {current_lead_score}):",
                options=lead_specific_action_options,
                index=action_index,
                key=f"action_select_{row['Request ID']}"
            )

            # --- Sales Notes (Conditionally Enabled for Hot Leads + Follow Up Required) ---
            new_sales_notes = row['Sales Notes'] # Initialize with current notes

            is_sales_notes_editable = (current_lead_score == 'Hot' and selected_action == 'Follow Up Required')

            if is_sales_notes_editable:
                st.info("Sales Notes are enabled for 'Hot Leads' with 'Follow Up Required' action.")
                new_sales_notes = st.text_area(
                    "Enter Sales Notes / Customer Issues:",
                    value=row['Sales Notes'],
                    key=f"sales_notes_area_{row['Request ID']}"
                )
            else:
                st.write(f"**Sales Notes:** {row['Sales Notes'] or 'No notes'}")

            # --- Save Updates Button ---
            if st.button(f"Save Updates for {row['Name']}", key=f"save_btn_{row['Request ID']}"):
                updates_made = False
                if selected_action != current_action:
                    update_booking_field(row['Request ID'], 'action_status', selected_action)
                    updates_made = True
                if new_sales_notes != row['Sales Notes']:
                    update_booking_field(row['Request ID'], 'sales_notes', new_sales_notes)
                    updates_made = True

                if not updates_made:
                    st.info("No changes to save.")

            st.markdown("---") # Separator for AI email

            # --- AI Email Drafting and Sending ---
            if st.button(f"Draft Follow-up Email for {row['Name']}", key=f"draft_email_btn_{row['Request ID']}"):
                if new_sales_notes.strip() == "" and current_lead_score == 'Hot' and selected_action == 'Follow Up Required':
                    st.warning("Please add some sales notes/customer issues before drafting an email for a 'Follow Up Required' Hot Lead.")
                else:
                    # NEW: Use data fetched from the backend
                    vehicle_details = aoe_vehicles_data.get(row['Vehicle'], {})
                    subject, body = generate_followup_email(
                        customer_name=row['Name'],
                        customer_email=row['Email'],
                        vehicle_name=row['Vehicle'],
                        sales_notes=new_sales_notes,
                        vehicle_details=vehicle_details
                    )
                    if subject and body:
                        st.session_state[f"draft_subject_{row['Request ID']}"] = subject
                        st.session_state[f"draft_body_{row['Request ID']}"] = body
                        st.success("Email draft generated! Review below.")
                    else:
                        st.error("Could not draft email. Please check notes and try again.")

            # Display drafted email if available in session state
            if f"draft_subject_{row['Request ID']}" in st.session_state and f"draft_body_{row['Request ID']}" in st.session_state:
                draft_subject = st.session_state[f"draft_subject_{row['Request ID']}"]
                draft_body = st.session_state[f"draft_body_{row['Request ID']}"]

                st.subheader("Review Drafted Email:")
                edited_subject = st.text_input("Subject:", value=draft_subject, key=f"reviewed_subject_{row['Request ID']}")
                edited_body = st.text_area("Body:", value=draft_body, height=300, key=f"reviewed_body_{row['Request ID']}")

                if ENABLE_EMAIL_SENDING:
                    if st.button(f"Click to Send Email to {row['Name']}", key=f"send_email_btn_{row['Request ID']}"):
                        if send_email(row['Email'], edited_subject, edited_body):
                            st.session_state.pop(f"draft_subject_{row['Request ID']}", None)
                            st.session_state.pop(f"draft_body_{row['Request ID']}", None)
                else:
                    st.warning("Email sending is not configured. Please add SMTP credentials to secrets.")

else:
    st.info("No test drive bookings to display yet. Submit a booking from your frontend!")

st.markdown("---")
st.caption("Dashboard refreshed periodically. Use unique browser tabs if multiple users are interacting simultaneously.")