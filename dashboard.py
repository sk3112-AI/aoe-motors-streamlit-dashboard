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

# --- Hardcoded Vehicle Data ---
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
    # AOE Aero and AOE Stellar are removed as per discussion
}

# --- Hardcoded Competitor Vehicle Data ---
COMPETITOR_VEHICLE_DATA = {
    "Ford": {
        "Sedan": { # Corresponds to AOE Apex (Luxury Sedan)
            "model_name": "Ford Sedan (e.g., Fusion/Taurus equivalent)", # Placeholder name
            "features": "2.5L IVCT Atkinson Cycle I-4 Hybrid Engine; 210 Total System Horsepower; Dual-Zone Electronic Automatic Temperature Control; Heated Front Row Seats"
        },
        "SUV": { # Corresponds to AOE Thunder (Performance SUV)
            "model_name": "Ford SUV (e.g., Explorer/Expedition equivalent)", # Placeholder name
            "features": "Available 440 horsepower 3.5L EcoBoost® V6 High-Output engine, Antilock Brake Systems (ABS), Front-Seat Side-Impact Airbags, SOS Post-Crash Alert System™"
        },
        "EV": { # Corresponds to AOE Volt (Electric Compact)
            "model_name": "Ford EV (e.g., Mustang Mach-E/F-150 Lightning equivalent)", # Placeholder name
            "features": "260 miles of EPA-est. range* with standard-range battery and RWD, 387 lb.-ft. of torque† with standard-range battery and RWD, Premium model features (heated/ventilated front seats trimmed with ActiveX® material), SYNC® 4A, over-the-air updates"
        }
    }
}

# Mapping AOE vehicle_type to Ford competitor segment
AOE_TYPE_TO_COMPETITOR_SEGMENT_MAP = {
    "Luxury Sedan": "Sedan",
    "Electric Compact": "EV",
    "Performance SUV": "SUV"
}

# --- New Function for AI Sentiment Analysis ---
def analyze_sentiment(text):
    if not text.strip():
        return "NEUTRAL" # Or "IRRELEVANT" if preferred for empty notes

    prompt = f"""
    Analyze the following text and determine its overall sentiment. Respond only with 'POSITIVE', 'NEUTRAL', or 'NEGATIVE'.

    Text: "{text}"
    """
    try:
        completion = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a sentiment analysis AI. Your only output is 'POSITIVE', 'NEUTRAL', or 'NEGATIVE'."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0, # Keep low for deterministic output
            max_tokens=10
        )
        sentiment = completion.choices[0].message.content.strip().upper()
        if sentiment in ["POSITIVE", "NEUTRAL", "NEGATIVE"]:
            return sentiment
        return "NEUTRAL" # Fallback
    except Exception as e:
        st.error(f"Error analyzing sentiment: {e}")
        return "NEUTRAL" # Fallback in case of API error

# --- New Function for AI Relevance Check ---
def check_notes_relevance(sales_notes):
    if not sales_notes.strip():
        return "IRRELEVANT" # Empty notes are irrelevant for email generation

    # Refined prompt to better distinguish between brief but relevant vs. truly irrelevant notes
    prompt = f"""
    Evaluate the following sales notes for their relevance and clarity in the context of generating a follow-up email for a vehicle test drive.

    Consider notes relevant if they provide *any* clear indication of the customer's experience, sentiment, questions, or specific interests related to the vehicle or the test drive, even if brief.

    Respond only with 'RELEVANT' if the notes describe a customer's feeling (e.g., happy, worried), a specific question, a stated interest, or a concrete concern.
    Respond with 'IRRELEVANT' if the notes are:
    - Empty or contain only whitespace.
    - Nonsensical or gibberish (e.g., "asdfasdf", "random words here").
    - Completely unrelated to a vehicle test drive or customer interaction (e.g., "The sky is blue today").

    Sales Notes: "{sales_notes}"
    """
    try:
        completion = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an AI assistant that evaluates the relevance of sales notes for email generation. Your only output is 'RELEVANT' or 'IRRELEVANT'."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0, # Keep low for deterministic output
            max_tokens=10
        )
        relevance = completion.choices[0].message.content.strip().upper()
        if relevance in ["RELEVANT", "IRRELEVANT"]:
            return relevance
        return "IRRELEVANT" # Fallback
    except Exception as e:
        st.error(f"Error checking notes relevance: {e}")
        return "IRRELEVANT" # Fallback in case of API error

# --- Function to Generate Follow-up Email (AI) ---
def generate_followup_email(customer_name, customer_email, vehicle_name, sales_notes, vehicle_details, current_vehicle_brand=None, sentiment=None):
    features_str = vehicle_details.get("features", "cutting-edge technology and a luxurious experience.")
    vehicle_type = vehicle_details.get("type", "vehicle")
    powertrain = vehicle_details.get("powertrain", "advanced performance")

    comparison_context = ""
    prompt_instructions = ""

    # Conditional EV-specific instructions based on powertrain AND sales notes
    ev_instructions = ""
    sales_notes_lower = sales_notes.lower()
    ev_cost_keywords = ["high cost", "expensive", "affordability", "price", "budget", "charging cost", "electricity bill", "cost effective"]
    charging_anxiety_keywords = ["charging", "range anxiety", "where to charge", "how long to charge", "charge time", "battery", "infrastructure"]

    if powertrain and powertrain.lower() == "electric":
        # Check for specific EV concerns in sales notes
        if any(keyword in sales_notes_lower for keyword in ev_cost_keywords):
            ev_instructions += """
            - Address any mentioned "high EV cost" or affordability concerns by focusing on long-term savings, reduced fuel costs, potential tax credits, Vehicle-to-Grid (V2G) capability, and the overall value proposition of electric ownership.
            """
        if any(keyword in sales_notes_lower for keyword in charging_anxiety_keywords):
            ev_instructions += """
            - Address any mentioned "charging anxiety" or range concerns by highlighting ultra-fast charging, solar integration (if applicable for the specific EV model), extensive charging network access, and impressive range.
            """
        # If no specific EV concerns, add general EV benefits if appropriate, or keep empty
        if not ev_instructions.strip(): # If no specific concerns were added
             ev_instructions = """
             - Briefly highlight general advantages of electric vehicles like environmental benefits, quiet ride, and low maintenance, if not specifically contradicted by sales notes.
             """
    else: # For non-EV vehicles
        if any(keyword in sales_notes_lower for keyword in ev_cost_keywords): # Customer might compare to EVs or have general cost concerns
            ev_instructions += """
            - If customer mentioned 'high EV cost' in comparison, or general cost concerns, reframe to discuss the cost-effectiveness and efficiency of the gasoline/hybrid powertrain of the {vehicle_name}, highlighting its long-term value.
            """
        if any(keyword in sales_notes_lower for keyword in charging_anxiety_keywords): # Customer might have charging anxiety from past EV consideration
            ev_instructions += """
            - If customer mentioned 'charging anxiety', emphasize the convenience and widespread availability of traditional fueling for the {vehicle_name}.
            """

    # General instructions for incorporating sales notes
    sales_notes_incorporation_instruction = """
    - Naturally incorporate the customer's experience, sentiment, questions, or interests directly into the email body, as if you learned them during a conversation, without explicitly stating "from our sales notes".
    - Address any *explicitly mentioned* concerns or questions from the sales notes directly.
    - If no specific negative feedback or concerns are explicitly stated in the 'Customer Issues/Comments (from sales notes)', *do not invent or assume any such concerns*. Instead, focus on reinforcing the positive aspects of their experience and the benefits of the vehicle.
    """


    if current_vehicle_brand and current_vehicle_brand.lower() == "ford":
        aoe_segment_key = AOE_TYPE_TO_COMPETITOR_SEGMENT_MAP.get(vehicle_type)
        if aoe_segment_key and aoe_segment_key in COMPETITOR_VEHICLE_DATA["Ford"]:
            ford_competitor = COMPETITOR_VEHICLE_DATA["Ford"][aoe_segment_key]
            comparison_context = f"""
            The customer's current vehicle brand is Ford. The {vehicle_name} falls into the {aoe_segment_key} segment.
            A representative Ford model in this segment is the {ford_competitor['model_name']} with features: {ford_competitor['features']}.
            """
            prompt_instructions = f"""
            - Start with a polite greeting.
            - Acknowledge their test drive.
            {sales_notes_incorporation_instruction}
                {ev_instructions}
            - Given the customer's interest in Ford, compare the {vehicle_name} with the representative Ford {aoe_segment_key} model ({ford_competitor['model_name']}) on 2-3 key differentiating features/specifications. Present this as a concise comparison in a clear, structured list format, under a heading like "Comparison: {vehicle_name} vs. {ford_competitor['model_name']}". For each feature, clearly state the feature name, then list the benefit/spec for {vehicle_name} and then for {ford_competitor['model_name']}.
              Example format:
              **Feature Name:**
              - {vehicle_name}: [Value/Description]
              - {ford_competitor['model_name']}: [Value/Description]
              Highlight where the {vehicle_name} excels or offers a distinct advantage. If a specific comparison point is not available for the Ford competitor from the provided features, infer a general or typical characteristic for that type of Ford vehicle, rather than stating 'not specified' or 'may vary'.
            - When highlighting features, be slightly technical to demonstrate the real value proposition, using terms from the '{vehicle_name} Key Features' list where appropriate. Ensure the benefit is clear and compelling.
            - Do NOT use bolding (e.g., `**text**`) in the email body except for section headings like "Comparison:" or feature names within the comparison.
            - If no specific issues are mentioned, write a general follow-up highlighting key benefits.
            - End with a low-pressure call to action. Instead of demanding a call or visit, offer to provide further specific information (e.g., a detailed digital brochure, a personalized feature comparison, or answers to any specific questions via email) that they can review at their convenience.
            - Maintain a professional, empathetic, and persuasive tone.
            - Output only the email content (Subject and Body), in plain text format. Do NOT use HTML.
            - Separate Subject and Body with "Subject: " at the beginning of the subject line.
            """
        else:
            prompt_instructions = f"""
            - Start with a polite greeting.
            - Acknowledge their test drive.
            {sales_notes_incorporation_instruction}
                {ev_instructions}
            - Position the {vehicle_name} as a compelling, modern alternative by focusing on clear, concise value propositions and AOE's distinct advantages (e.g., innovation, advanced technology, future-proofing) that might appeal to someone considering traditional brands like Ford.
            - When highlighting features, be slightly technical to demonstrate the real value proposition, using terms from the '{vehicle_name} Key Features' list where appropriate. Ensure the benefit is clear and compelling.
            - Do NOT use bolding (e.g., `**text**`) in the email body.
            - If no specific issues are mentioned, write a general follow-up highlighting key benefits.
            - End with a low-pressure call to action. Instead of demanding a call or visit, offer to provide further specific information (e.g., a detailed digital brochure, a personalized feature comparison, or answers to any specific questions via email) that they can review at their convenience.
            - Maintain a professional, empathetic, and persuasive tone.
            - Output only the email content (Subject and Body), in plain text format. Do NOT use HTML.
            - Separate Subject and Body with "Subject: " at the beginning of the subject line.
            """
    elif current_vehicle_brand and current_vehicle_brand.lower() in ["toyota", "hyundai", "chevrolet"]:
        prompt_instructions = f"""
        - Start with a polite greeting.
        - Acknowledge their test drive.
        {sales_notes_incorporation_instruction}
            {ev_instructions}
        - Position the {vehicle_name} as a compelling, modern alternative by focusing on clear, concise value propositions and AOE's distinct advantages (e.g., innovation, advanced technology, future-proofing) that might appeal to someone considering traditional brands like {current_vehicle_brand}.
        - When highlighting features, be slightly technical to demonstrate the real value proposition, using terms from the '{vehicle_name} Key Features' list where appropriate. Ensure the benefit is clear and compelling.
        - Do NOT use bolding (e.g., `**text**`) in the email body.
        - If no specific issues are mentioned, write a general follow-up highlighting key benefits.
        - End with a low-pressure call to action. Instead of demanding a call or visit, offer to provide further specific information (e.g., a detailed digital brochure, a personalized feature comparison, or answers to any specific questions via email) that they can review at their convenience.
        - Maintain a professional, empathetic, and persuasive tone.
        - Output only the email content (Subject and Body), in plain text format. Do NOT use HTML.
        - Separate Subject and Body with "Subject: " at the beginning of the subject line.
        """
    else:
        # Default prompt for no specific brand comparison or general case
        prompt_instructions = f"""
        - Start with a polite greeting.
        - Acknowledge their test drive.
        {sales_notes_incorporation_instruction}
            {ev_instructions}
        - When highlighting features, be slightly technical to demonstrate the real value proposition, using terms from the '{vehicle_name} Key Features' list where appropriate. Ensure the benefit is clear and compelling.
        - Do NOT use bolding (e.g., `**text**`) in the email body.
        - If no specific issues are mentioned, write a general follow-up highlighting key benefits.
        - End with a low-pressure call to action. Instead of demanding a call or visit, offer to provide further specific information (e.g., a detailed digital brochure, a personalized feature comparison, or answers to any specific questions via email) that they can review at their convenience.
        - Maintain a professional, empathetic, and persuasive tone.
        - Output only the email content (Subject and Body), in plain text format. Do NOT use HTML.
        - Separate Subject and Body with "Subject: " at the beginning of the subject line.
        """

    # --- Add positive sentiment instructions if applicable ---
    if sentiment == "POSITIVE":
        prompt_instructions += """
        - Since the customer expressed a positive experience, ensure the email reinforces this positive sentiment.
        - Highlight the exciting nature of the AOE brand and the community they would join.
        - Mention AOE's comprehensive support system, including guidance on flexible financing options, dedicated sales support for any questions, and robust long-term service contracts, ensuring peace of mind throughout their ownership journey.
        - Instead of directly mentioning discounts, subtly hint at "tailored offers" or "value packages" that can be discussed with a sales representative to maximize their value, encouraging them to take the next step.
        - Avoid explicitly discussing specific financing terms or pushing for immediate conversion in this email.
        """

    prompt = f"""
    Draft a polite, helpful, and persuasive follow-up email to a customer who recently test-drove an {vehicle_name}.

    **Customer Information:**
    - Name: {customer_name}
    - Email: {customer_email}
    - Vehicle of Interest: {vehicle_name} ({vehicle_type}, {powertrain} powertrain)
    - Customer Issues/Comments (from sales notes): "{sales_notes}"

    **{vehicle_name} Key Features:**
    - {features_str}

    {comparison_context}

    **Email Instructions:**
    {prompt_instructions}
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
                max_tokens=800
            )
            draft = completion.choices[0].message.content.strip()
            if "Subject:" in draft:
                parts = draft.split("Subject:", 1)
                subject_line = parts[1].split("\n", 1)[0].strip()
                body_content = parts[1].split("\n", 1)[1].strip()
            else:
                subject_line = f"Following up on your {vehicle_name} Test Drive"
                body_content = draft
            return subject_line, body_content
    except Exception as e:
        st.error(f"Error drafting email with AI: {e}")
        return None, None

# --- Main Dashboard Display Logic ---
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
            "request_id, full_name, email, vehicle, booking_date, current_vehicle, location, time_frame, action_status, sales_notes, lead_score, booking_timestamp"
        ).order('booking_timestamp', desc=True)

        if location_filter and location_filter != "All Locations":
            query = query.eq('location', location_filter)
        if start_date_filter:
            query = query.gte('booking_timestamp', start_date_filter.isoformat())
        if end_date_filter:
            query = query.lte('booking_timestamp', end_date_filter.isoformat())

        response = query.execute()

        if response.data:
            return response.data
        else:
            return []
    except Exception as e:
        st.error(f"Error fetching data from Supabase: {e}")
        return []

# --- Function to Update Data in Supabase ---
def update_booking_field(request_id, field_name, new_value):
    """Updates a specific field for a booking in Supabase using request_id."""
    try:
        response = supabase.from_(SUPABASE_TABLE_NAME).update({field_name: new_value}).eq('request_id', request_id).execute()
        if response.data:
            st.success(f"Successfully updated {field_name} for {request_id}!")
            st.cache_data.clear()
        else:
            st.error(f"Failed to update {field_name} for {request_id}. Response: {response}")
    except Exception as e:
        st.error(f"Error updating {field_name} in Supabase: {e}")

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

# Define fixed action status options based on lead score
ACTION_STATUS_MAP = {
    "Hot": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"],
    "Warm": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"],
    "Cold": ["New Lead", "Lost", "Converted"],
    "New": ["New Lead", "Call Scheduled", "Follow Up Required", "Lost", "Converted"]
}

# Filters Section
st.sidebar.header("Filters")

all_locations = ["All Locations", "New York", "Los Angeles", "Chicago", "Houston", "Miami"]
selected_location = st.sidebar.selectbox("Filter by Location", all_locations)

col1, col2 = st.sidebar.columns(2)
with col1:
    start_date = st.date_input("Start Date (Booking Timestamp)", value=datetime.today().date() - pd.Timedelta(days=30))
with col2:
    end_date = st.date_input("End Date (Booking Timestamp)", value=datetime.today().date())

# Fetch all data needed for the dashboard with filters
bookings_data = fetch_bookings_data(selected_location, start_date, end_date)

if bookings_data:
    df = pd.DataFrame(bookings_data)
    df = df.sort_values(by='booking_timestamp', ascending=False)

    def set_expanded_lead(request_id):
        if st.session_state.expanded_lead_id == request_id:
            st.session_state.expanded_lead_id = None
        else:
            st.session_state.expanded_lead_id = request_id

    for index, row in df.iterrows():
        current_action = row['action_status']
        current_lead_score = row['lead_score'] if row['lead_score'] else "New"

        available_actions = ACTION_STATUS_MAP.get(current_lead_score, ACTION_STATUS_MAP["New"])

        expander_key = f"expander_{row['request_id']}"
        is_expanded = (st.session_state.expanded_lead_id == row['request_id'])

        with st.expander(
            f"**{row['full_name']}** - {row['vehicle']} - Status: **{current_action}** (Score: {current_lead_score})",
            expanded=is_expanded
        ):
            if st.button("Toggle Details", key=f"toggle_{row['request_id']}", on_click=set_expanded_lead, args=(row['request_id'],)):
                pass

            st.write(f"**Email:** {row['email']}")
            st.write(f"**Location:** {row['location']}")
            st.write(f"**Booking Date:** {row['booking_date']}")
            st.write(f"**Booking Timestamp:** {row['booking_timestamp']}")
            st.write(f"**Current Vehicle:** {row['current_vehicle'] if row['current_vehicle'] else 'N/A'}")
            st.write(f"**Time Frame:** {row['time_frame']}")

            st.markdown("---")

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

                is_sales_notes_editable = (selected_action == 'Follow Up Required')
                new_sales_notes = st.text_area(
                    "Sales Notes",
                    value=row['sales_notes'] if row['sales_notes'] else "",
                    key=f"sales_notes_{row['request_id']}",
                    help="Add notes for follow-up, customer concerns, or other relevant details.",
                    disabled=not is_sales_notes_editable
                )

                col_buttons = st.columns([1,1,2])

                with col_buttons[0]:
                    save_button = st.form_submit_button("Save Updates")

                if selected_action == 'Follow Up Required':
                    with col_buttons[1]:
                        draft_email_button = st.form_submit_button("Draft Follow-up Email")

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

                if selected_action == 'Lost' and selected_action != current_action and ENABLE_EMAIL_SENDING:
                    st.info(f"Customer {row['full_name']} marked as Lost. Sending 'Lost' email...")
                    # Assuming generate_lost_email exists elsewhere or needs to be added
                    # For now, adding a placeholder for demonstration
                    lost_subject = f"We Miss You, {row['full_name']}!"
                    lost_body = f"Dear {row['full_name']},\n\nWe noticed you haven't moved forward with your interest in the {row['vehicle']}. We understand circumstances change, but we'd love to hear from you if you have any feedback or if there's anything we can do to help. \n\nSincerely,\nAOE Motors Team"

                    if send_email(row['email'], lost_subject, lost_body):
                        st.success(f"'Lost' email sent to {row['full_name']}.")
                    else:
                        st.error("Failed to send 'Lost' email.")

                elif selected_action == 'Converted' and selected_action != current_action and ENABLE_EMAIL_SENDING:
                    st.info(f"Customer {row['full_name']} marked as Converted. Sending welcome email...")
                    # Assuming generate_welcome_email exists elsewhere or needs to be added
                    # For now, adding a placeholder for demonstration
                    welcome_subject = f"Welcome to the AOE Family, {row['full_name']}!"
                    welcome_body = f"Dear {row['full_name']},\n\nWelcome to the AOE Motors family! We're thrilled you chose the {row['vehicle']}. We look forward to providing you with an exceptional ownership experience.\n\nSincerely,\nAOE Motors Team"

                    if welcome_subject and welcome_body:
                        if send_email(row['email'], welcome_subject, welcome_body):
                            st.success(f"Welcome email sent to {row['full_name']}.")
                        else:
                            st.error("Failed to send welcome email.")
                    else:
                        st.error("Could not generate welcome email.")

                if updates_made:
                    st.session_state.expanded_lead_id = row['request_id']
                    st.rerun()

            # Logic for drafting follow-up email (manual send)
            if selected_action == 'Follow Up Required' and 'draft_email_button' in locals() and draft_email_button:
                if new_sales_notes.strip() == "":
                    st.warning("Sales notes are mandatory to draft a follow-up email.")
                else:
                    st.info("Analyzing sales notes for relevance and sentiment...")
                    notes_relevance = check_notes_relevance(new_sales_notes)

                    if notes_relevance == "IRRELEVANT":
                        st.warning("The sales notes provided are unclear or irrelevant. Please update the 'Sales Notes' with more descriptive information (e.g., specific customer concerns, positive feedback, or key discussion points) to enable the AI to draft a relevant email.")
                    else:
                        notes_sentiment = analyze_sentiment(new_sales_notes)

                        vehicle_details = AOE_VEHICLE_DATA.get(row['vehicle'], {})
                        current_vehicle_brand_val = row['current_vehicle'].split(' ')[0] if row['current_vehicle'] else None

                        if vehicle_details:
                            followup_subject, followup_body = generate_followup_email(
                                row['full_name'], row['email'], row['vehicle'], new_sales_notes, vehicle_details,
                                current_vehicle_brand=current_vehicle_brand_val,
                                sentiment=notes_sentiment
                            )
                            if followup_subject and followup_body:
                                st.session_state[f"draft_subject_{row['request_id']}"] = followup_subject
                                st.session_state[f"draft_body_{row['request_id']}"] = followup_body
                                st.session_state.expanded_lead_id = row['request_id']
                                st.rerun()
                            else:
                                st.error("Failed to draft email. Please check sales notes and try again.")
                        else:
                            st.error(f"Vehicle details for {row['vehicle']} not found in hardcoded data. Cannot draft email.")

            if selected_action == 'Follow Up Required' and f"draft_subject_{row['request_id']}" in st.session_state and f"draft_body_{row['request_id']}" in st.session_state:
                draft_subject = st.session_state[f"draft_subject_{row['request_id']}"]
                draft_body = st.session_state[f"draft_body_{row['request_id']}"]

                st.subheader("Review Drafted Email:")
                edited_subject = st.text_input("Subject:", value=draft_subject, key=f"reviewed_subject_{row['request_id']}")
                edited_body = st.text_area("Body:", value=draft_body, height=300, key=f"reviewed_body_{row['request_id']}")

                if ENABLE_EMAIL_SENDing:
                    if st.button(f"Click to Send Drafted Email to {row['full_name']}", key=f"send_draft_email_btn_{row['request_id']}"):
                        if send_email(row['email'], edited_subject, edited_body):
                            st.session_state.pop(f"draft_subject_{row['request_id']}", None)
                            st.session_state.pop(f"draft_body_{row['request_id']}", None)
                            st.session_state.expanded_lead_id = row['request_id']
                            st.rerun()
                else:
                    st.warning("Email sending is not configured. Please add SMTP credentials to secrets.")

else:
    st.info("No test drive bookings to display yet. Submit a booking from your frontend!")

st.markdown("---")