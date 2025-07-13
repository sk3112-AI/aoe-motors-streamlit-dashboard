import streamlit as st
import logging
logging.basicConfig(
    filename="dashboard_debug.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logging.debug("Dashboard script started.")
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

load_dotenv()

# --- GLOBAL CONFIGURATIONS (ALL AT THE VERY TOP) ---
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    st.error("Supabase URL or Key not found. Please ensure they are set as environment variables (e.g., in Render Environment Variables or locally in a .env file).")
    st.stop()

supabase: Client = create_client(supabase_url, supabase_key)
SUPABASE_TABLE_NAME = "bookings"
EMAIL_INTERACTIONS_TABLE_NAME = "email_interactions"

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    st.error("OpenAI API Key not found. Please ensure it is set as an environment variable (e.g., in Render Environment Variables or locally in a .env file).")
    st.stop()
openai_client = OpenAI(api_key=openai_api_key)

email_host = os.getenv("EMAIL_HOST")
email_port_str = os.getenv("EMAIL_PORT")
email_port = int(email_port_str) if email_port_str else 0
email_address = os.getenv("EMAIL_ADDRESS")
email_password = os.getenv("EMAIL_PASSWORD")

ENABLE_EMAIL_SENDING = all([email_host, email_port, email_address, email_password])
if not ENABLE_EMAIL_SENDING:
    st.warning("Email credentials not fully configured. Email sending will be disabled. Ensure all EMAIL_* variables are set.")

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
    """Fetches all booking data from Supabase, with optional filters."""
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
    """Updates a specific field for a booking in Supabase using request_id."""
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
            logging.debug('Logging into SMTP server...')
        server.login(email_address, email_password)
            server.send_message(msg)
        logging.info(f"Email successfully sent to {recipient_email}!")
        st.session_state.success_message = f"Email successfully sent to {recipient_email}!"
        return True
    except Exception as e:
        logging.error(f"Failed to send email: {e}", exc_info=True)
        st.session_state.error_message = f"Failed to send email: {e}"
        return False

def analyze_sentiment(text):
    if not text.strip():
        return "NEUTRAL"

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
            temperature=0.0,
            max_tokens=10
        )
        sentiment = completion.choices[0].message.content.strip().upper()
        if sentiment in ["POSITIVE", "NEUTRAL", "NEGATIVE"]:
            return sentiment
        return "NEUTRAL"
    except Exception as e:
        logging.error(f"Error analyzing sentiment: {e}", exc_info=True)
        st.error(f"Error analyzing sentiment: {e}")
        return "NEUTRAL"

def check_notes_relevance(sales_notes):
    if not sales_notes.strip():
        return "IRRELEVANT"

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
            temperature=0.0,
            max_tokens=10
        )
        relevance = completion.choices[0].message.content.strip().upper()
        if relevance in ["RELEVANT", "IRRELEVANT"]:
            return relevance
        return "IRRELEVANT"
    except Exception as e:
        logging.error(f"Error checking notes relevance: {e}", exc_info=True)
        st.error(f"Error checking notes relevance: {e}")
        return "IRRELEVANT"

def generate_followup_email(customer_name, customer_email, vehicle_name, sales_notes, vehicle_details, current_vehicle_brand=None, sentiment=None):
    features_str = vehicle_details.get("features", "cutting-edge technology and a luxurious experience.")
    vehicle_type = vehicle_details.get("type", "vehicle")
    powertrain = vehicle_details.get("powertrain", "advanced performance")

    comparison_context = ""
    prompt_instructions = ""

    ev_instructions = ""
    sales_notes_lower = sales_notes.lower()
    ev_cost_keywords = ["high cost", "expensive", "affordability", "price", "budget", "charging cost", "electricity bill", "cost effective"]
    charging_anxiety_keywords = ["charging", "range anxiety", "where to charge", "how long to charge", "charge time", "battery", "infrastructure"]

    if powertrain and powertrain.lower() == "electric":
        if any(keyword in sales_notes_lower for keyword in ev_cost_keywords):
            ev_instructions += """
            - Address any mentioned "high EV cost" or affordability concerns by focusing on long-term savings, reduced fuel costs, potential tax credits, Vehicle-to-Grid (V2G) capability, and the overall value proposition of electric ownership.
            """
        if any(keyword in sales_notes_lower for keyword in charging_anxiety_keywords):
            ev_instructions += """
            - Address any mentioned "charging anxiety" or range concerns by highlighting ultra-fast charging, solar integration (if applicable for the specific EV model), extensive charging network access, and impressive range.
            """
        if not ev_instructions.strip():
             ev_instructions = """
             - Briefly highlight general advantages of electric vehicles like environmental benefits, quiet ride, and low maintenance, if not specifically contradicted by sales notes.
             """
    else:
        if any(keyword in sales_notes_lower for keyword in ev_cost_keywords):
            ev_instructions += """
            - If customer mentioned 'high EV cost' in comparison, or general cost concerns, reframe to discuss the cost-effectiveness and efficiency of the gasoline/hybrid powertrain of the {vehicle_name}, highlighting its long-term value.
            """
        if any(keyword in sales_notes_lower for keyword in charging_anxiety_keywords):
            ev_instructions += """
            - If customer mentioned 'charging anxiety', emphasize the convenience and widespread availability of traditional fueling for the {vehicle_name}.
            """

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
    elif sentiment == "NEGATIVE":
        prompt_instructions += """
        - The email must be highly empathetic, apologetic, and focused on resolution.
        - Acknowledge their specific frustration or concern (e.g., "frustrated with our process") directly and empathetically in the subject and opening.
        - Apologize sincerely for any inconvenience or dissatisfaction they experienced.
        - **CRITICAL: DO NOT include generic feature lists, technical specifications, or comparisons with other brands (like Ford, Toyota, etc.) in this email.** The primary goal is to address their negative experience, not to sell the car.
        - Offer a clear and actionable path to resolve their issue or address their concerns (e.g., "I'd like to personally ensure this is resolved," "Let's discuss how we can improve," "I'm here to clarify any confusion").
        - Reassure them that their feedback is invaluable and that AOE Motors is committed to an excellent customer experience.
        - Focus entirely on rebuilding trust and resolving the negative point.
        - Keep the tone professional, understanding, and solution-oriented throughout.
        - The call to action should be solely an invitation for a direct conversation to address and resolve the specific issue.
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
        logging.error(f"Error drafting email with AI: {e}", exc_info=True)
        st.error(f"Error drafting email with AI: {e}")
        return None, None

def generate_lost_email(customer_name, vehicle_name):
    subject = f"We Miss You, {customer_name}!"
    body = f"""Dear {customer_name},

We noticed you haven't moved forward with your interest in the {vehicle_name}. We understand circumstances change, but we'd love to hear from you if you have any feedback or if there's anything we can do to help.

Sincerely,
AOE Motors Team
"""
    return subject, body

def generate_welcome_email(customer_name, vehicle_name):
    subject = f"Welcome to the AOE Family, {customer_name}!"
    body = f"""Dear {customer_name},

Welcome to the AOE Motors family! We're thrilled you chose the {vehicle_name}.

To help you get started, here are some important next steps and documents:
* **Next Steps:** Our sales representative will be in touch shortly to finalize your delivery details and walk you through your new vehicle's features.
* **Important Documents:** You'll find your purchase agreement, warranty information, and a quick-start guide for your {vehicle_name} attached to this email (or accessible via the link below).
    [Link to Digital Documents/Owner's Manual - e.g., www.aoemotors.com/your-vehicle-docs]

Should you have any questions before then, please don't hesitate to reach out to your sales representative or our customer support team at support@aoemotors.com.

We're excited for you to experience the AOE difference!

Sincerely,
The AOE Motors Team
"""
    return subject, body

def set_expanded_lead(request_id):
    if st.session_state.expanded_lead_id == request_id:
        st.session_state.expanded_lead_id = None
    else:
        st.session_state.expanded_lead_id = request_id

def interpret_and_query(query_text, df):
    query = query_text.strip().lower()

    now = pd.Timestamp.now(tz="UTC")
    today = now.normalize()
    last_week = today - pd.Timedelta(days=7)
    start_of_month = now.replace(day=1).normalize()

    if "total leads" in query and "today" in query:
        count = df[df['booking_timestamp'].dt.normalize() == today].shape[0]
        return f"📊 Total leads today: **{count}**"

    elif "total leads" in query and "last week" in query:
        count = df[(df['booking_timestamp'] >= last_week) & (df['booking_timestamp'] < today)].shape[0]
        return f"📊 Total leads in the last week: **{count}**"

    elif "hot leads" in query and "last week" in query:
        filtered = df[
            (df['lead_score'].str.lower() == "hot") &
            (df['booking_timestamp'] >= last_week) &
            (df['booking_timestamp'] < today)
        ]
        count = filtered.shape[0]
        return f"🔥 Hot leads last week: **{count}**"

    elif "warm leads" in query:
        count = df[df['lead_score'].str.lower() == "warm"].shape[0]
        return f"🟡 Total warm leads: **{count}**"

    elif "cold leads" in query:
        count = df[df['lead_score'].str.lower() == "cold"].shape[0]
        return f"❄️ Total cold leads: **{count}**"

    elif "total conversions" in query:
        count = df[df['action_status'].str.lower() == "converted"].shape[0]
        return f"✅ Total conversions: **{count}**"

    elif "leads lost" in query:
        count = df[df['action_status'].str.lower() == "lost"].shape[0]
        return f"❌ Leads marked as lost: **{count}**"

    else:
        return "❓ Sorry, I couldn't understand that question. Try asking about 'hot leads last week', 'total leads today', 'total conversions', etc."


st.markdown("---")