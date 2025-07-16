import streamlit as st
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import pandas as pd
from openai import OpenAI
import time
import requests
from datetime import datetime, date, timedelta, timezone # ADDED timezone
import json
import logging
import sys

# For IST timezone conversion for analytics
# Ensure 'tzdata' (or 'pytz') is in your requirements.txt for zoneinfo to work with 'Asia/Kolkata'
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback for Python versions < 3.9 or if tzdata is not installed
    # If using older Python or without tzdata, accurate timezone conversion requires pytz
    # For a demo, if exact IST local time filtering is not crucial, timezone.utc is sufficient
    logging.warning("zoneinfo not available directly. Analytics might use system's local time or UTC for 'today', 'yesterday' unless pytz is configured.")
    ZoneInfo = None # Fallback or handle differently

# ADDED SendGrid imports
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

load_dotenv()

# --- Logging Setup ---
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# --- GLOBAL CONFIGURATIONS ---
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")

if not supabase_url or not supabase_key:
    logging.error("Supabase URL or Key not found. Please ensure they are set as environment variables (e.g., in Render Environment Variables or locally in a .env file).")
    st.error("Supabase URL or Key not found. Please ensure they are set as environment variables (e.g., in Render Environment Variables or locally in a .env file).")
    st.stop()

supabase: Client = create_client(supabase_url, supabase_key)
SUPABASE_TABLE_NAME = "bookings"
EMAIL_INTERACTIONS_TABLE_NAME = "email_interactions"

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    logging.error("OpenAI API Key not found. Please ensure it is set as an environment variable (e.g., in Render Environment Variables or locally in a .env file).")
    st.error("OpenAI API Key not found. Please ensure it is set as an environment variable (e.g., in Render Environment Variables or locally in a .env file).")
    st.stop()
openai_client = OpenAI(api_key=openai_api_key)

# --- Email Configuration (for SendGrid) ---
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
email_address = os.getenv("EMAIL_ADDRESS")

ENABLE_EMAIL_SENDING = all([SENDGRID_API_KEY, email_address])
if not ENABLE_EMAIL_SENDING:
    logging.warning("SendGrid API Key or sender email not fully configured. Email sending will be disabled.")
    st.warning("Email sending is disabled. Please ensure SENDGRID_API_KEY and EMAIL_ADDRESS environment variables are set.")

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


# --- ALL FUNCTION DEFINITIONS ---

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

# ADDED: Function to log email interactions to email_interactions table
def log_email_interaction(request_id, event_type):
    try:
        data = {
            "request_id": request_id,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        response = supabase.from_(EMAIL_INTERACTIONS_TABLE_NAME).insert(data).execute()
        if response.data:
            logging.info(f"Logged email interaction: {event_type} for {request_id}.")
        else:
            logging.error(f"Failed to log email interaction {event_type} for {request_id}. Response: {response}")
    except Exception as e:
        logging.error(f"Error logging email interaction for {request_id}: {e}", exc_info=True)

# send_email function uses SendGrid API
def send_email(recipient_email, subject, body, request_id=None, event_type="email_sent_dashboard"): # Added request_id, event_type
    if not ENABLE_EMAIL_SENDING:
        logging.error("SendGrid API Key or sender email not fully configured. Email sending is disabled.")
        st.session_state.error_message = "Email sending is disabled. Please ensure SendGrid API Key and Sender Email are configured."
        return False

    message = Mail(
        from_email=email_address, # Your verified sender email
        to_emails=recipient_email,
        subject=subject,
        html_content=body # Ensure body is HTML with <p> tags for proper rendering
    )
    try:
        sendgrid_client = SendGridAPIClient(SENDGRID_API_KEY)
        response = sendgrid_client.send(message)
        
        # Check SendGrid's API response status code
        if response.status_code >= 200 and response.status_code < 300:
            logging.info(f"Email successfully sent via SendGrid to {recipient_email}! Status Code: {response.status_code}")
            st.session_state.success_message = f"Email successfully sent to {recipient_email}!"
            # Log the email sent event
            if request_id:
                log_email_interaction(request_id, event_type)
            return True
        else:
            logging.error(f"Failed to send email via SendGrid. Status Code: {response.status_code}, Body: {response.body.decode('utf-8') if response.body else 'No body'}")
            st.session_state.error_message = f"Failed to send email. SendGrid Status: {response.status_code}"
            return False
    except Exception as e:
        logging.error(f"Error sending email via SendGrid: {e}", exc_info=True)
        st.session_state.error_message = f"Error sending email: {e}"
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

# MODIFIED: generate_followup_email to request HTML and generate <p> tags
def generate_followup_email(customer_name, customer_email, vehicle_name, sales_notes, vehicle_details, current_vehicle_brand=None, sentiment=None):
    features_str = vehicle_details.get("features", "cutting-edge technology and a luxurious experience.")
    vehicle_type = vehicle_details.get("type", "vehicle")
    powertrain = vehicle_details.get("powertrain", "advanced performance")

    comparison_context = ""
    # MODIFIED: Prompt instructions now explicitly ask for HTML with <p> tags
    prompt_instructions = """
    - Start with a polite greeting.
    - Acknowledge their test drive or recent interaction.
    - The entire email body MUST be composed of distinct HTML paragraph tags (<p>...</p>).
    - Each logical section/paragraph MUST be entirely enclosed within its own <p> and </p> tags.
    - Each paragraph (<p>...</p>) should be concise (typically 2-4 sentences maximum).
    - Aim for a total of 5-7 distinct HTML paragraphs.
    - DO NOT use \\n\\n for spacing; the <p> tags provide the necessary visual separation.
    - DO NOT include any section dividers (like '---').
    - Ensure there is no extra blank space before the first <p> tag or after the last </p> tag.
    - Output the email body in valid HTML format.
    - Separate Subject and Body with "Subject: " at the beginning of the subject line.
    """

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
            prompt_instructions += f"""
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
                temperature=0.0, # Adjusted temperature to 0 for more deterministic prompt response
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
    # MODIFIED: Wrapped body in <p> tags for proper HTML spacing
    body = f"""<p>Dear {customer_name},</p>
<p>We noticed you haven't moved forward with your interest in the {vehicle_name}. We understand circumstances change, but we'd love to hear from you if you have any feedback or if there's anything we can do to help.</p>
<p>Sincerely,</p>
<p>AOE Motors Team</p>
"""
    return subject, body

def generate_welcome_email(customer_name, vehicle_name):
    subject = f"Welcome to the AOE Family, {customer_name}!"
    # MODIFIED: generate_welcome_email to use <p> tags for spacing
    body = f"""<p>Dear {customer_name},</p>
<p>Welcome to the AOE Motors family! We're thrilled you chose the {vehicle_name}.</p>
<p>To help you get started, here are some important next steps and documents:</p>
<ul>
    <li><b>Next Steps:</b> Our sales representative will be in touch shortly to finalize your delivery details and walk you through your new vehicle's features.</li>
    <li><b>Important Documents:</b> You'll find your purchase agreement, warranty information, and a quick-start guide for your {vehicle_name} attached to this email (or accessible via the link below).</li>
</ul>
<p>[Link to Digital Documents/Owner's Manual - e.g., www.aoemotors.com/your-vehicle-docs]</p>
<p>Should you have any questions before then, please don't hesitate to reach out to your sales representative or our customer support team at support@aoemotors.com.</p>
<p>We're excited for you to experience the AOE difference!</p>
<p>Sincerely,</p>
<p>The AOE Motors Team</p>
"""
    return subject, body

def set_expanded_lead(request_id):
    if st.session_state.expanded_lead_id == request_id:
        st.session_state.expanded_lead_id = None
    else:
        st.session_state.expanded_lead_id = request_id

# NEW: Function to suggest offer
def suggest_offer(lead_details: dict, vehicle_data: dict) -> str:
    customer_name = lead_details.get("customer_name", "customer")
    vehicle_name = lead_details.get("vehicle_name", "vehicle")
    current_vehicle = lead_details.get("current_vehicle", "N/A")
    lead_score_text = lead_details.get("lead_score_text", "New")
    numeric_lead_score = lead_details.get("numeric_lead_score", 0)
    sales_notes = lead_details.get("sales_notes", "")
    
    vehicle_features = vehicle_data.get("features", "excellent features")

    # Define prompt instructions based on lead score
    if lead_score_text == "Cold":
        offer_prompt_advice = "Since the lead is Cold, advise to wait and understand interest. Absolutely DO NOT suggest any immediate offers like discounts or financing. Focus on observation."
    else: # Hot or Warm
        offer_prompt_advice = f"""
        - Suggest a personalized offer type (e.g., "Discount", "Financing Option", "Extended Warranty", "Roadside Assistance").
        - Consider the customer's current vehicle ({current_vehicle}), their expressed interest ({vehicle_name}), and any concerns in sales notes ("{sales_notes}").
        - Mention a specific feature of {vehicle_name} ({vehicle_features}) if relevant to the offer.
        - For pricing/cost concerns, focus on financing options or potential discounts. For safety/performance, extended warranty or roadside assistance.
        """

    prompt = f"""
    You are an AI Sales Advisor for AOE Motors. Your task is to suggest the NEXT BEST OFFER TYPE for a customer based on their profile.

    **Customer Profile:**
    - Name: {customer_name}
    - Vehicle of Interest: {vehicle_name}
    - Current Vehicle: {current_vehicle}
    - Lead Status: {lead_score_text} ({numeric_lead_score} points)
    - Sales Notes: "{sales_notes}"
    - Key Features of {vehicle_name}: {vehicle_features}

    **Instructions:**
    - Output ONLY the recommended offer advice, formatted as plain text or markdown.
    - Start with "**AI Offer Suggestion:**"
    {offer_prompt_advice}
    """

    try:
        completion = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a highly analytical AI Sales Advisor. Provide concise, actionable offer suggestions."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=200
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Error suggesting offer: {e}", exc_info=True)
        return "Error generating offer suggestion. Please try again."

# NEW: Function to generate call talking points
def generate_call_talking_points(lead_details: dict, vehicle_data: dict) -> str:
    customer_name = lead_details.get("customer_name", "customer")
    vehicle_name = lead_details.get("vehicle_name", "vehicle")
    current_vehicle = lead_details.get("current_vehicle", "N/A")
    lead_score_text = lead_details.get("lead_score_text", "New")
    numeric_lead_score = lead_details.get("numeric_lead_score", 0)
    sales_notes = lead_details.get("sales_notes", "")

    vehicle_features = vehicle_data.get("features", "excellent features")

    prompt = f"""
    You are an AI Sales Advisor preparing talking points for a sales representative's call with {customer_name}.

    **Customer Profile:**
    - Name: {customer_name}
    - Vehicle Interested: {vehicle_name}
    - Current Vehicle: {current_vehicle}
    - Lead Status: {lead_score_text} ({numeric_lead_score} points)
    - Sales Notes: "{sales_notes}"
    - Key Features of {vehicle_name}: {vehicle_features}

    **Instructions:**
    - Provide concise, actionable bullet points for the sales call.
    - Start with "**AI Talking Points:**"
    - Include points to:
        - Acknowledge their interest in {vehicle_name}.
        - Address any specific concerns from "Sales Notes" directly and empathetically.
        - Highlight 2-3 most relevant features of {vehicle_name} based on profile (e.g., if coming from a different brand, highlight competitive advantages).
        - Suggest questions to ask to understand their needs better.
        - Provide a clear call to action for the call (e.g., schedule next step, clarify doubts).
    - Format output as a markdown list.
    - If sales notes are empty or irrelevant, focus on general re-engagement or discovery.
    """

    try:
        completion = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an AI Sales Advisor that provides clear, actionable talking points for sales calls."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=300
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Error generating talking points: {e}", exc_info=True)
        return "Error generating talking points. Please try again."


def interpret_and_query(query_text, all_bookings_df):
    query = query_text.lower().strip()

    # Time zone conversion for current time (IST - Asia/Kolkata)
    # Ensure current time is also timezone-aware for comparison with timestampz
    # Use ZoneInfo for specific timezones like 'Asia/Kolkata'
    try:
        IST_TZ = timezone(timedelta(hours=5, minutes=30)) # For fixed offset IST
        # If tzdata is installed: IST_TZ = ZoneInfo('Asia/Kolkata')
    except Exception:
        logging.warning("zoneinfo (or pytz) not fully configured for 'Asia/Kolkata'. Using fixed offset IST.")
        IST_TZ = timezone(timedelta(hours=5, minutes=30)) # Fallback

    today_dt_ist = datetime.now(IST_TZ) # Get current time in IST
    today_dt_utc = today_dt_ist.astimezone(timezone.utc) # Convert IST today to UTC for DB comparison

    # Define various timeframes based on UTC date for consistency with DB
    yesterday_dt_utc = (today_dt_ist - timedelta(days=1)).astimezone(timezone.utc)
    last_week_start_dt_utc = (today_dt_ist - timedelta(days=7)).astimezone(timezone.utc)
    last_month_start_dt_utc = (today_dt_ist - timedelta(days=30)).astimezone(timezone.utc)
    last_year_start_dt_utc = (today_dt_ist - timedelta(days=365)).astimezone(timezone.utc)


    if not all_bookings_df.empty:
        # Ensure booking_timestamp is datetime for proper filtering
        if not pd.api.types.is_datetime64_any_dtype(all_bookings_df['booking_timestamp']):
            all_bookings_df['booking_timestamp'] = pd.to_datetime(all_bookings_df['booking_timestamp'])
        
        # Ensure it's converted to UTC for consistent comparison with today_dt_utc
        if all_bookings_df['booking_timestamp'].dt.tz is None:
            all_bookings_df['booking_timestamp'] = all_bookings_df['booking_timestamp'].dt.tz_localize('UTC')
        else:
            all_bookings_df['booking_timestamp'] = all_bookings_df['booking_timestamp'].dt.tz_convert('UTC')

    # Define the types of queries the LLM can interpret and provide few-shot examples
    prompt = f"""
    Analyze the following user query about automotive leads.
    Extract the 'lead_status', 'time_frame', and optionally 'location'.
    If the query cannot be interpreted, set 'query_type' to "UNINTERPRETED".

    Lead Statuses: "Hot", "Warm", "Cold", "New Lead", "Converted", "Lost", "All" (if no specific status is mentioned but asking for total leads).
    Time Frames: "TODAY", "YESTERDAY", "LAST_WEEK" (last 7 days), "LAST_MONTH" (last 30 days), "LAST_YEAR" (last 365 days), "ALL_TIME".
    Locations: "New York", "Los Angeles", "Chicago", "Houston", "Miami", "All Locations" (if no specific location is mentioned).

    Return a JSON object with 'lead_status', 'time_frame', and 'location'.
    If the query cannot be interpreted, return {{"query_type": "UNINTERPRETED"}}.

    Examples:
    - User: "how many hot leads last week in New York?"
    - Output: {{"lead_status": "Hot", "time_frame": "LAST_WEEK", "location": "New York"}}

    - User: "total leads today"
    - Output: {{"lead_status": "All", "time_frame": "TODAY", "location": "All Locations"}}

    - User: "cold leads from Houston"
    - Output: {{"lead_status": "Cold", "time_frame": "ALL_TIME", "location": "Houston"}}

    - User: "total conversions"
    - Output: {{"lead_status": "Converted", "time_frame": "ALL_TIME", "location": "All Locations"}}

    - User: "leads lost yesterday"
    - Output: {{"lead_status": "Lost", "time_frame": "YESTERDAY", "location": "All Locations"}}

    User Query: "{query_text}"
    """
    
    try:
        with st.spinner("Interpreting query..."):
            completion = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful AI assistant that extracts specific filter criteria from user queries and outputs a JSON object. Only use the provided categories and timeframes."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0, # Keep low for deterministic JSON output
                max_tokens=150, # Increased max_tokens slightly for more complex JSON
                response_format={"type": "json_object"}
            )
            response_json = json.loads(completion.choices[0].message.content.strip())
            
            # Extract interpreted filters
            lead_status_filter = response_json.get("lead_status")
            time_frame_filter = response_json.get("time_frame")
            location_filter_nlq = response_json.get("location")

            # Check if LLM returned "UNINTERPRETED" explicitly
            if response_json.get("query_type") == "UNINTERPRETED":
                 return "This cannot be processed now - Restricted for demo. Please try queries about specific lead types (Hot, Warm, Cold, Converted, Lost), locations, or timeframes (today, last week, last month)."

            filtered_df = all_bookings_df.copy()

            # Apply time filter based on the NLQ interpreted timeframe (using UTC dates)
            if time_frame_filter == "TODAY":
                filtered_df = filtered_df[filtered_df['booking_timestamp'].dt.date == today_dt_utc.date()]
            elif time_frame_filter == "YESTERDAY":
                filtered_df = filtered_df[filtered_df['booking_timestamp'].dt.date == yesterday_dt_utc.date()]
            elif time_frame_filter == "LAST_WEEK":
                filtered_df = filtered_df[filtered_df['booking_timestamp'] >= last_week_start_dt_utc]
            elif time_frame_filter == "LAST_MONTH":
                filtered_df = filtered_df[filtered_df['booking_timestamp'] >= last_month_start_dt_utc]
            elif time_frame_filter == "LAST_YEAR":
                filtered_df = filtered_df[filtered_df['booking_timestamp'] >= last_year_start_dt_utc]
            # "ALL_TIME" means no date filter applied here, it relies on sidebar filters

            # Apply lead status filter
            if lead_status_filter and lead_status_filter != "All":
                filtered_df = filtered_df[filtered_df['lead_score'].str.lower() == lead_status_filter.lower()]
                if lead_status_filter in ["Converted", "Lost"]: # Action status specific filtering
                    filtered_df = filtered_df[filtered_df['action_status'] == lead_status_filter]
                
            # Apply location filter
            if location_filter_nlq and location_filter_nlq != "All Locations":
                filtered_df = filtered_df[filtered_df['location'] == location_filter_nlq]
            
            result_count = filtered_df.shape[0]
            
            # --- Format the output message ---
            message_parts = []
            if lead_status_filter and lead_status_filter != "All":
                message_parts.append(f"{lead_status_filter.lower()} leads")
            else:
                message_parts.append("total leads")
            
            if time_frame_filter != "ALL_TIME":
                message_parts.append(f" {time_frame_filter.lower().replace('_', ' ')}")
            
            if location_filter_nlq and location_filter_nlq != "All Locations":
                message_parts.append(f" in {location_filter_nlq}")
            
            # Clarify that results are within the sidebar's filters
            sidebar_date_range_str = ""
            if st.session_state.get('sidebar_start_date') and st.session_state.get('sidebar_end_date'):
                s_date = st.session_state['sidebar_start_date'].strftime('%b %d, %Y')
                e_date = st.session_state['sidebar_end_date'].strftime('%b %d, %Y')
                sidebar_date_range_str = f" (filtered from {s_date} to {e_date})"
            
            result_message = f"📊 {' '.join(message_parts).capitalize()}: **{result_count}**{sidebar_date_range_str}"

            # New: "refine time period" message logic
            if result_count == 0 and (st.session_state.get('sidebar_end_date') - st.session_state.get('sidebar_start_date')).days < 7: # If filter period is less than 7 days
                result_message += "<br>Consider expanding the date range in the sidebar filters if you expect more results."
            
            return result_message

    except json.JSONDecodeError:
        logging.error("LLM did not return a valid JSON.", exc_info=True)
        return "LLM did not return a valid JSON. This cannot be processed now - Restricted for demo. Please try queries like 'total leads today', 'hot leads last week', 'total conversions', or 'leads lost'."
    except Exception as e:
        logging.error(f"Error processing query: {e}", exc_info=True)
        return "An error occurred while processing your query. This cannot be processed now - Restricted for demo."


# --- MAIN DASHBOARD DISPLAY LOGIC (STRICTLY AFTER ALL DEFINITIONS) ---

st.set_page_config(page_title="AOE Motors Test Drive Dashboard", layout="wide")
st.title("🚗 AOE Motors Test Drive Bookings") # Keep this as the single main title
st.markdown("---")

# Initialize session state for expanded lead and messages
if 'expanded_lead_id' not in st.session_state:
    st.session_state.expanded_lead_id = None
if 'info_message' not in st.session_state:
    st.session_state.info_message = None
if 'success_message' not in st.session_state:
    st.session_state.success_message = None
if 'error_message' not in st.session_state:
    st.session_state.error_message = None

# Display messages stored in session state
if st.session_state.info_message:
    st.info(st.session_state.info_message)
    st.session_state.info_message = None
if st.session_state.success_message:
    st.success(st.session_state.success_message)
    st.session_state.success_message = None
if st.session_state.error_message:
    st.error(st.session_state.error_message)
    st.session_state.error_message = None


# Filters Section
st.sidebar.header("Filters")

all_locations = ["All Locations", "New York", "Los Angeles", "Chicago", "Houston", "Miami"]
selected_location = st.sidebar.selectbox("Filter by Location", all_locations)

col_sidebar1, col_sidebar2 = st.sidebar.columns(2)
with col_sidebar1:
    start_date = st.date_input("Start Date (Booking Timestamp)", value=datetime.today().date())
    st.session_state['sidebar_start_date'] = start_date # Store for NLQ context
with col_sidebar2:
    end_date = st.date_input("End Date (Booking Timestamp)", value=datetime.today().date() + timedelta(days=1))
    st.session_state['sidebar_end_date'] = end_date # Store for NLQ context

# Fetch all data needed for the dashboard with filters
bookings_data = fetch_bookings_data(selected_location, start_date, end_date)

if bookings_data:
    df = pd.DataFrame(bookings_data)
    df['booking_timestamp'] = pd.to_datetime(df['booking_timestamp'])
    df = df.sort_values(by='booking_timestamp', ascending=False)

    # --- Text-to-Query Section ---
    st.subheader("Analytics - Ask a Question! 🤖")
    query_text = st.text_input(
        "Type your question (e.g., 'total leads today', 'hot leads last week', 'total conversions', 'leads lost'):",
        key="nlq_query_input"
    )
    if query_text:
        result_message = interpret_and_query(query_text, df)
        st.markdown(result_message)
    st.markdown("---")

    for index, row in df.iterrows():
        current_action = row['action_status']
        current_numeric_lead_score = row.get('numeric_lead_score', 0)
        current_lead_score_text = row.get('lead_score', "New")


        available_actions = ACTION_STATUS_MAP.get(current_lead_score_text, ACTION_STATUS_MAP["New"])

        expander_key = f"expander_{row['request_id']}"
        is_expanded = (st.session_state.expanded_lead_id == row['request_id'])

        with st.expander(
            f"**{row['full_name']}** - {row['vehicle']} - Status: **{current_action}** (Score: {current_lead_score_text} - {current_numeric_lead_score} points)",
            expanded=is_expanded
        ):
            st.button("Toggle Details", key=f"toggle_{row['request_id']}", on_click=set_expanded_lead, args=(row['request_id'],))

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
                    st.markdown(f"<div style='text-align: right;'>**Current Lead Score:** {current_lead_score_text} ({current_numeric_lead_score} points)</div>", unsafe_allow_html=True) 

                is_sales_notes_editable = (selected_action == 'Follow Up Required')
                new_sales_notes = st.text_area(
                    "Sales Notes",
                    value=row['sales_notes'] if row['sales_notes'] else "",
                    key=f"sales_notes_{row['request_id']}",
                    help="Add notes for follow-up, customer concerns, or other relevant details.",
                    disabled=not is_sales_notes_editable
                )

                col_buttons = st.columns([1,1,1,1]) # Expanded columns for more buttons

                with col_buttons[0]:
                    save_button = st.form_submit_button("Save Updates")

                if selected_action == 'Follow Up Required':
                    with col_buttons[1]:
                        draft_email_button = st.form_submit_button("Draft Follow-up Email")
                
                # NEW: Dynamic Offer Suggestion button
                with col_buttons[2]:
                    offer_button = st.form_submit_button("Suggest Offer (AI)")

                # NEW: Talking Points Button
                if selected_action == 'Call Scheduled': # Only show if status is Call Scheduled
                    with col_buttons[3]:
                        generate_talking_points_button = st.form_submit_button("Generate Talking Points (AI)")


            if save_button:
                updates_made = False
                if selected_action != current_action:
                    update_booking_field(row['request_id'], 'action_status', selected_action)
                    updates_made = True
                if new_sales_notes != (row['sales_notes'] if row['sales_notes'] else ""):
                    update_booking_field(row['request_id'], 'sales_notes', new_sales_notes)
                    updates_made = True

                if selected_action == 'Lost' and selected_action != current_action and ENABLE_EMAIL_SENDING:
                    st.session_state.info_message = f"Customer {row['full_name']} marked as Lost. Sending 'Lost' email..."
                    lost_subject, lost_body = generate_lost_email(row['full_name'], row['vehicle'])
                    send_email(row['email'], lost_subject, lost_body, request_id=row['request_id'], event_type="email_lost_sent")
                
                elif selected_action == 'Converted' and selected_action != current_action and ENABLE_EMAIL_SENDING:
                    st.session_state.info_message = f"Customer {row['full_name']} marked as Converted. Sending welcome email..."
                    welcome_subject, welcome_body = generate_welcome_email(row['full_name'], row['vehicle'])
                    send_email(row['email'], welcome_subject, welcome_body, request_id=row['request_id'], event_type="email_converted_sent")

                if updates_made:
                    st.session_state.expanded_lead_id = row['request_id'] 
                    st.rerun()

            if selected_action == 'Follow Up Required' and 'draft_email_button' in locals() and draft_email_button:
                if new_sales_notes.strip() == "":
                    st.warning("Sales notes are mandatory to draft a follow-up email.")
                else:
                    st.session_state.info_message = "Analyzing sales notes for relevance and sentiment..."
                    notes_relevance = check_notes_relevance(new_sales_notes)

                    if notes_relevance == "IRRELEVANT":
                        st.warning("The sales notes provided are unclear or irrelevant. Please update the 'Sales Notes' with more descriptive information (e.g., specific customer concerns, positive feedback, or key discussion points) to enable the AI to draft a relevant email.")
                        st.session_state.info_message = None 
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
                                st.session_state.info_message = None 
                                st.rerun()
                            else:
                                st.session_state.error_message = "Failed to draft email. Please check sales notes and try again."
                                st.session_state.info_message = None 
                        else:
                            st.session_state.error_message = f"Vehicle details for {row['vehicle']} not found in hardcoded data. Cannot draft email."
                            st.session_state.info_message = None 

            if selected_action == 'Follow Up Required' and f"draft_subject_{row['request_id']}" in st.session_state and f"draft_body_{row['request_id']}" in st.session_state:
                draft_subject = st.session_state[f"draft_subject_{row['request_id']}"]
                draft_body = st.session_state[f"draft_body_{row['request_id']}"]

                st.subheader("Review Drafted Email:")
                edited_subject = st.text_input("Subject:", value=draft_subject, key=f"reviewed_subject_{row['request_id']}")
                edited_body = st.text_area("Body:", value=draft_body, height=300, key=f"reviewed_body_{row['request_id']}")

                if ENABLE_EMAIL_SENDING:
                    if st.button(f"Click to Send Drafted Email to {row['full_name']}", key=f"send_draft_email_btn_{row['request_id']}"):
                        if send_email(row['email'], edited_subject, edited_body, request_id=row['request_id'], event_type="email_followup_sent"): # Added logging
                            st.session_state.pop(f"draft_subject_{row['request_id']}", None)
                            st.session_state.pop(f"draft_body_{row['request_id']}", None)
                            st.session_state.expanded_lead_id = row['request_id']
                            st.rerun()
                else:
                    st.warning("Email sending is not configured. Please add SMTP credentials to secrets.")

            # NEW: Logic for Dynamic Offer Suggestion (moved from outside form)
            if 'offer_button' in locals() and offer_button: # Check if offer_button was clicked within the form
                st.session_state.info_message = "Generating personalized offer suggestion..."
                offer_suggestion_details = {
                    "customer_name": row['full_name'],
                    "vehicle_name": row['vehicle'],
                    "current_vehicle": row['current_vehicle'],
                    "lead_score_text": current_lead_score_text,
                    "numeric_lead_score": current_numeric_lead_score,
                    "sales_notes": new_sales_notes # Use the latest notes
                }
                suggested_offer_text = suggest_offer(offer_suggestion_details, AOE_VEHICLE_DATA.get(row['vehicle'], {}))
                st.session_state[f"suggested_offer_{row['request_id']}"] = suggested_offer_text
                st.session_state.expanded_lead_id = row['request_id'] # Keep expanded
                st.session_state.info_message = None # Clear info message
                st.rerun()
            
            # Display suggested offer if available in session state
            if f"suggested_offer_{row['request_id']}" in st.session_state:
                st.subheader("AI-Suggested Offer:")
                st.markdown(st.session_state[f"suggested_offer_{row['request_id']}"])
                st.markdown("---")


            # NEW: Logic for Talking Points (moved from outside form)
            if 'generate_talking_points_button' in locals() and generate_talking_points_button: # Check if button was clicked
                st.session_state.info_message = "Generating talking points..."
                talking_points_details = {
                    "customer_name": row['full_name'],
                    "vehicle_name": row['vehicle'],
                    "current_vehicle": row['current_vehicle'],
                    "lead_score_text": current_lead_score_text,
                    "numeric_lead_score": current_numeric_lead_score,
                    "sales_notes": new_sales_notes # Use the latest notes
                }
                generated_points = generate_call_talking_points(talking_points_details, AOE_VEHICLE_DATA.get(row['vehicle'], {}))
                st.session_state[f"call_talking_points_{row['request_id']}"] = generated_points
                st.session_state.expanded_lead_id = row['request_id']
                st.session_state.info_message = None
                st.rerun()
            
            # Display talking points if available in session state
            if f"call_talking_points_{row['request_id']}" in st.session_state:
                st.subheader("AI-Generated Talking Points:")
                st.markdown(st.session_state[f"call_talking_points_{row['request_id']}"])
                st.markdown("---")

else:
    st.info("No test drive bookings to display yet. Submit a booking from your frontend!")

st.markdown("---")