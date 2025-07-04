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
from datetime import datetime, date

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

# --- Backend API URL (No longer strictly needed for vehicle data, but kept for other potential calls) ---
BACKEND_API_URL = "https://aoe-agentic-demo.onrender.com" # Your deployed backend URL

# --- Hardcoded Vehicle Data (Copied from main.py) ---
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
    },
    "AOE Aero": {
        "type": "Hybrid Crossover",
        "powertrain": "Hybrid",
        "features": "Fuel-efficient hybrid system, All-wheel drive, Spacious cargo, Infotainment with large touchscreen, Wireless charging, Hands-free power liftgate, Remote start, Apple CarPlay/Android Auto."
    },
    "AOE Stellar": {
        "type": "Electric Pickup Truck",
        "powertrain": "Electric",
        "features": "Quad-motor AWD, 0-60 mph in 3 seconds, 10,000 lbs towing capacity, Frunk (front trunk) storage, Integrated air compressor, Worksite power outlets, Customizable bed configurations, Off-road driving modes."
    }
}

# --- Dashboard Title ---
st.set_page_config(page_title="AOE Motors Test Drive Dashboard", layout="wide")
st.title("🚗 AOE Motors Test Drive Bookings")
st.markdown("---")

# Initialize session state for expanded lead
if 'expanded_lead_id' not in st.session_state:
    st.session_state.expanded_lead_id = None

# --- Function to Fetch Data from Supabase ---
@st.cache_data(ttl=30)
def fetch_bookings_data(location_filter=None, start_date_filter=None, end_date_filter=None):
    """Fetches all booking data from Supabase, with optional filters."""
    try:
        query = supabase.from_(SUPABASE_TABLE_NAME).select(
            "request_id, full_name, email, vehicle, booking_date, current_vehicle, location, time_frame, action_status, sales_notes, lead_score, booking_timestamp" # Added booking_timestamp
        ).order('booking_timestamp', desc=True) # Changed order to booking_timestamp

        if location_filter and location_filter != "All Locations":
            query = query.eq('location', location_filter)
        if start_date_filter:
            # Filter by booking_timestamp
            query = query.gte('booking_timestamp', start_date_filter.isoformat())
        if end_date_filter:
            # Filter by booking_timestamp
            query = query.lte('booking_timestamp', end_date_filter.isoformat())

        response = query.execute()

        if response.data:
            return response.data
        else:
            return []
    except Exception as e:
        st.error(f"Error fetching data from Supabase: {e}")
        return []

# --- REMOVED: Function to Fetch Vehicle Data from Backend ---
# @st.cache_data(ttl=3600)
# def fetch_vehicles_data_from_backend():
#    ...

# --- Function to Update Data in Supabase ---
def update_booking_field(request_id, field_name, new_value):
    """Updates a specific field for a booking in Supabase using request_id."""
    try:
        response = supabase.from_(SUPABASE_TABLE_NAME).update({field_name: new_value}).eq('request_id', request_id).execute()
        if response.data:
            st.success(f"Successfully updated {field_name} for {request_id}!")
            st.cache_data.clear() # Clear dashboard cache for immediate reflection
            # No st.rerun() here, as we want to control reruns explicitly for expander state
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
    - Crucially, directly address the customer's stated issues from the sales notes. For each issue mentioned, explain how specific features of the AOE {vehicle_name} (from the provided list) directly resolve or alleviate that concern.
        - If "high EV cost" is mentioned: Focus on long-term savings, reduced fuel costs, potential tax credits, Vehicle-to-Grid (V2G) if applicable (Volt).
        - If "charging anxiety" is mentioned: Highlight ultra-fast charging, solar integration (Volt), extensive charging network, range.
        - If other issues are mentioned: Adapt relevant features.
    - **When highlighting features, you can be slightly technical to demonstrate the real value proposition, using terms from the 'AOE {vehicle_name} Key Features' list where appropriate, but ensure the benefit is clear.**
    - If no specific issues are mentioned, write a general follow-up highlighting key benefits.
    - End with a call to action to schedule another call or visit to discuss further.
    - Maintain a professional, empathetic, and persuasive tone.
    - Output only the email content (Subject and Body), in plain text format. Do NOT use HTML.
    - Separate Subject and Body with "Subject: " at the beginning of the subject line.
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

# --- Function to Generate Lost Email (AI) ---
def generate_lost_email(customer_name, vehicle_name):
    prompt = f"""
    Draft a polite and professional email to a customer, {customer_name}, who was interested in the AOE {vehicle_name} but has decided not to proceed.
    Thank them for their time and interest, acknowledge their decision respectfully, and invite them to consider AOE Motors again in the future.
    Maintain a positive, non-pushy, and welcoming tone.
    Output only the email content (Subject and Body), in plain text format.
    Separate Subject and Body with "Subject: " at the beginning of the subject line.
    """
    try:
        with st.spinner(f"Drafting 'Lost' email for {customer_name} with AI..."):
            completion = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful and respectful sales assistant for AOE Motors, writing emails to customers who decide not to proceed."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=300
            )
            draft = completion.choices[0].message.content.strip()
            if "Subject:" in draft:
                parts = draft.split("Subject:", 1)
                subject_line = parts[1].split("\n", 1)[0].strip()
                body_content = parts[1].split("\n", 1)[1].strip()
            else:
                subject_line = f"Thank you for your interest in AOE Motors, {customer_name}"
                body_content = draft
            return subject_line, body_content
    except Exception as e:
        st.error(f"Error drafting 'Lost' email with AI: {e}")
        return None, None

# --- Function to Generate Welcome Email for Converted Leads (AI) ---
def generate_welcome_email(customer_name, vehicle_name):
    prompt = f"""
    Draft a warm and enthusiastic welcome email to {customer_name}, who has just converted into a customer and is proceeding with the purchase of the AOE {vehicle_name}.
    Welcome them to the AOE Motors family.
    Briefly outline the next steps in the sales process (e.g., paperwork, financing, delivery).
    Emphasize getting documents ready (e.g., ID, proof of address, financial documents).
    Express excitement for their journey with AOE.
    Maintain a friendly, professional, and encouraging tone.
    Output only the email content (Subject and Body), in plain text format.
    Separate Subject and Body with "Subject: " at the beginning of the subject line.
    """
    try:
        with st.spinner(f"Drafting welcome email for {customer_name} with AI..."):
            completion = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful and enthusiastic sales assistant for AOE Motors, welcoming new customers."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=400
            )
            draft = completion.choices[0].message.content.strip()
            if "Subject:" in draft:
                parts = draft.split("Subject:", 1)
                subject_line = parts[1].split("\n", 1)[0].strip()
                body_content = parts[1].split("\n", 1)[1].strip()
            else:
                subject_line = f"Welcome to the AOE Motors Family, {customer_name}!"
                body_content = draft
            return subject_line, body_content
    except Exception as e:
        st.error(f"Error drafting welcome email with AI: {e}")
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
    "Hot": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"],
    "Warm": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"],
    "Cold": ["New Lead", "Lost", "Converted"],
    "New": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"] # Fallback for 'New' or any uncategorized
}

# Filters Section
st.sidebar.header("Filters")

# Location Filter (example static list, populate dynamically from data if needed)
all_locations = ["All Locations", "New York", "Los Angeles", "Chicago", "Houston", "Miami"] # Example
selected_location = st.sidebar.selectbox("Filter by Location", all_locations)

# Date Filter (now filters by booking_timestamp)
col1, col2 = st.sidebar.columns(2)
with col1:
    start_date = st.date_input("Start Date (Booking Timestamp)", value=datetime.today().date() - pd.Timedelta(days=30))
with col2:
    end_date = st.date_input("End Date (Booking Timestamp)", value=datetime.today().date())

# Fetch all data needed for the dashboard with filters
bookings_data = fetch_bookings_data(selected_location, start_date, end_date)
# aoe_vehicles_data is now directly AOE_VEHICLE_DATA

if bookings_data:
    df = pd.DataFrame(bookings_data)
    df = df.sort_values(by='booking_timestamp', ascending=False) # Ensure sorting by timestamp

    # UI/UX: Session state to manage expanded lead
    def set_expanded_lead(request_id):
        if st.session_state.expanded_lead_id == request_id:
            st.session_state.expanded_lead_id = None # Collapse if already expanded
        else:
            st.session_state.expanded_lead_id = request_id

    for index, row in df.iterrows():
        current_action = row['action_status']
        current_lead_score = row['lead_score'] if row['lead_score'] else "New" # Handle potential None

        # Determine available actions based on lead score
        available_actions = ACTION_STATUS_MAP.get(current_lead_score, ACTION_STATUS_MAP["New"])

        # Create a unique key for each expander for session state management
        expander_key = f"expander_{row['request_id']}"
        
        # Check if the current lead should be expanded based on session state
        is_expanded = (st.session_state.expanded_lead_id == row['request_id'])

        with st.expander(
            f"**{row['full_name']}** - {row['vehicle']} - Status: **{current_action}** (Score: {current_lead_score})",
            expanded=is_expanded
        ):
            # When expander is clicked, update session state
            if st.button("Toggle Details", key=f"toggle_{row['request_id']}", on_click=set_expanded_lead, args=(row['request_id'],)):
                pass # This button itself won't do anything beyond updating the state

            st.write(f"**Email:** {row['email']}")
            st.write(f"**Location:** {row['location']}")
            st.write(f"**Booking Date:** {row['booking_date']}") # Keeping display of booking_date
            st.write(f"**Booking Timestamp:** {row['booking_timestamp']}") # Displaying timestamp for clarity
            st.write(f"**Current Vehicle:** {row['current_vehicle'] if row['current_vehicle'] else 'N/A'}")
            st.write(f"**Time Frame:** {row['time_frame']}")

            st.markdown("---")

            # Form for updates
            with st.form(key=f"update_form_{row['request_id']}"):
                col1, col2 = st.columns(2)
                with col1:
                    selected_action = st.selectbox(
                        "Action Status",
                        options=available_actions,
                        index=available_actions.index(current_action) if current_action in available_actions else 0,
                        key=f"action_status_{row['request_id']}"
                    )
                with col2:
                    selected_lead_score = st.selectbox(
                        "Lead Score",
                        options=["New", "Hot", "Warm", "Cold"],
                        index=["New", "Hot", "Warm", "Cold"].index(current_lead_score) if current_lead_score in ["New", "Hot", "Warm", "Cold"] else 0,
                        key=f"lead_score_{row['request_id']}"
                    )

                # Sales notes editable only for 'Follow Up Required'
                is_sales_notes_editable = (selected_action == 'Follow Up Required')
                new_sales_notes = st.text_area(
                    "Sales Notes",
                    value=row['sales_notes'] if row['sales_notes'] else "",
                    key=f"sales_notes_{row['request_id']}",
                    help="Add notes for follow-up, customer concerns, or other relevant details.",
                    disabled=not is_sales_notes_editable
                )

                col_buttons = st.columns([1,1,2]) # Adjust column ratio for button alignment

                with col_buttons[0]:
                    save_button = st.form_submit_button("Save Updates")

                # Conditional button for drafting follow-up email
                if selected_action == 'Follow Up Required':
                    with col_buttons[1]:
                        draft_email_button = st.form_submit_button("Draft Follow-up Email")
                        # Enforce sales notes for drafting
                        if draft_email_button and new_sales_notes.strip() == "":
                            st.warning("Sales notes are mandatory to draft a follow-up email.")
                            draft_email_button = False # Prevent further execution if notes are empty

            # Logic after form submission
            if save_button:
                updates_made = False
                if selected_action != current_action:
                    update_booking_field(row['request_id'], 'action_status', selected_action)
                    updates_made = True
                if selected_lead_score != current_lead_score:
                    update_booking_field(row['request_id'], 'lead_score', selected_lead_score)
                    updates_made = True
                if new_sales_notes != (row['sales_notes'] if row['sales_notes'] else ""):
                    update_booking_field(row['request_id'], 'sales_notes', new_sales_notes)
                    updates_made = True

                # Automatic email sending for 'Lost' and 'Converted' on Save
                if selected_action == 'Lost' and selected_action != current_action and ENABLE_EMAIL_SENDING:
                    st.info(f"Customer {row['full_name']} marked as Lost. Sending 'Lost' email...")
                    lost_subject, lost_body = generate_lost_email(row['full_name'], row['vehicle'])
                    if lost_subject and lost_body:
                        if send_email(row['email'], lost_subject, lost_body):
                            st.success(f"'Lost' email sent to {row['full_name']}.")
                        else:
                            st.error("Failed to send 'Lost' email.")
                    else:
                        st.error("Could not generate 'Lost' email.")
                
                elif selected_action == 'Converted' and selected_action != current_action and ENABLE_EMAIL_SENDING:
                    st.info(f"Customer {row['full_name']} marked as Converted. Sending welcome email...")
                    welcome_subject, welcome_body = generate_welcome_email(row['full_name'], row['vehicle'])
                    if welcome_subject and welcome_body:
                        if send_email(row['email'], welcome_subject, welcome_body):
                            st.success(f"Welcome email sent to {row['full_name']}.")
                        else:
                            st.error("Failed to send welcome email.")
                    else:
                        st.error("Could not generate welcome email.")

                if updates_made:
                    st.session_state.expanded_lead_id = row['request_id'] # Keep current lead expanded
                    st.rerun() # Rerun to reflect updates and maintain state


            # Logic for drafting follow-up email (manual send)
            if selected_action == 'Follow Up Required' and 'draft_email_button' in locals() and draft_email_button and new_sales_notes.strip() != "":
                # Use the hardcoded AOE_VEHICLE_DATA directly
                vehicle_details = AOE_VEHICLE_DATA.get(row['vehicle'], {})
                if vehicle_details:
                    followup_subject, followup_body = generate_followup_email(
                        row['full_name'], row['email'], row['vehicle'], new_sales_notes, vehicle_details
                    )
                    if followup_subject and followup_body:
                        st.session_state[f"draft_subject_{row['request_id']}"] = followup_subject
                        st.session_state[f"draft_body_{row['request_id']}"] = followup_body
                        st.session_state.expanded_lead_id = row['request_id'] # Keep expanded
                        st.rerun() # Rerun to display drafted email

                    else:
                        st.error("Failed to draft email. Please check sales notes and try again.")
                else:
                    st.error(f"Vehicle details for {row['vehicle']} not found in hardcoded data. Cannot draft email.")


            # Display drafted email if available in session state (only for Follow Up Required)
            if selected_action == 'Follow Up Required' and f"draft_subject_{row['request_id']}" in st.session_state and f"draft_body_{row['request_id']}" in st.session_state:
                draft_subject = st.session_state[f"draft_subject_{row['request_id']}"]
                draft_body = st.session_state[f"draft_body_{row['request_id']}"]

                st.subheader("Review Drafted Email:")
                edited_subject = st.text_input("Subject:", value=draft_subject, key=f"reviewed_subject_{row['request_id']}")
                edited_body = st.text_area("Body:", value=draft_body, height=300, key=f"reviewed_body_{row['request_id']}")

                if ENABLE_EMAIL_SENDING:
                    if st.button(f"Click to Send Drafted Email to {row['full_name']}", key=f"send_draft_email_btn_{row['request_id']}"):
                        if send_email(row['email'], edited_subject, edited_body):
                            st.session_state.pop(f"draft_subject_{row['request_id']}", None)
                            st.session_state.pop(f"draft_body_{row['request_id']}", None)
                            st.session_state.expanded_lead_id = row['request_id'] # Keep expanded
                            st.rerun() # Rerun to clear drafted email display
                else:
                    st.warning("Email sending is not configured. Please add SMTP credentials to secrets.")

else:
    st.info("No test drive bookings to display yet. Submit a booking from your frontend!")

st.markdown("---")