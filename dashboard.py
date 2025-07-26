import streamlit as st
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import pandas as pd
from openai import OpenAI
import time
import requests # For making API calls to the new agent service
from datetime import datetime, date, timedelta, timezone
import json
import logging
import sys

# ADDED SendGrid imports (retained for individual email sends from dashboard)
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# ADDED markdown_it for Markdown to HTML conversion
import markdown_it # Ensure 'markdown-it-py' is in your requirements.txt

# Initialize MarkdownIt parser for converting AI output to HTML
md_converter = markdown_it.MarkdownIt()

# For IST timezone conversion for analytics
try:
    from zoneinfo import ZoneInfo
except ImportError:
    logging.warning("zoneinfo (or tzdata) not available. Using fixed offset for IST. Install 'tzdata' for full timezone support.")
    ZoneInfo = None # Fallback or handle differently

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

# --- Email Configuration (for SendGrid - for individual sends from this dashboard) ---
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
email_address = os.getenv("EMAIL_ADDRESS")

ENABLE_EMAIL_SENDING = all([SENDGRID_API_KEY, email_address])
if not ENABLE_EMAIL_SENDING:
    logging.warning("SendGrid API Key or sender email not fully configured. Email sending will be disabled (for individual dashboard sends).")
    st.warning("Email sending is disabled (for individual dashboard sends). Please ensure SENDGRID_API_KEY and EMAIL_ADDRESS env vars are set.")

# --- NEW: URL for the separate Agent Service ---
AUTOMOTIVE_AGENT_SERVICE_URL = os.getenv("AUTOMOTIVE_AGENT_SERVICE_URL")
if not AUTOMOTIVE_AGENT_SERVICE_URL:
    st.warning("AUTOMOTIVE_AGENT_SERVICE_URL is not set. Batch agent functionalities (Analytics NLQ, Batch Follow-up/Offers) will not function.")

BACKEND_API_URL = "https://aoe-agentic-demo.onrender.com" # This might be the old main.py URL, ensure it's still needed or remove


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

# send_email function uses SendGrid API (for individual sends from this dashboard)
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
    powertrain = vehicle_details.get("powertrain", "advanced Inference")

    comparison_context = ""
    # MODIFIED: Prompt instructions now explicitly ask for MARKDOWN, not HTML.
    prompt_instructions = """
    - Start with a polite greeting.
    - Acknowledge their test drive or recent interaction.
    - The entire email body MUST be composed of distinct **Markdown paragraphs**. Use a double newline (`\\n\\n`) to separate paragraphs.
    - For lists, use standard Markdown bullet points (e.g., `- Item 1\\n- Item 2`).
    - Each paragraph should be concise (typically 2-4 sentences maximum).
    - Aim for a total of 5-7 distinct Markdown paragraphs.
    - **DO NOT include any HTML tags** (like <p>, <ul>, <li>, <br>) directly in the output.
    - DO NOT include any section dividers (like '---').
    - Ensure there is no extra blank space before the first paragraph or after the last.
    - Output the email body in valid Markdown format.
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
        with st.spinner("Drafting email with AI..."): # This spinner is for dashboard UI, not agent service
            completion = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful and persuasive sales assistant for AOE Motors."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
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
            
            # Post-processing to convert Markdown to HTML for sending
            if body_content.strip() and not ("<p>" in body_content):
                paragraphs = body_content.split('\n\n')
                html_body_for_sending = "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())
            else:
                html_body_for_sending = body_content
            
            logging.debug(f"Final Generated Body (Markdown for UI, partial): {body_content[:100]}...")
            logging.debug(f"Final HTML Body (for sending, partial): {html_body_for_sending[:100]}...")
            
            return subject_line, body_content, html_body_for_sending # MODIFIED return tuple

    except Exception as e:
        logging.error(f"Error drafting email with AI: {e}", exc_info=True)
        return None, None, None

# MODIFIED: generate_lost_email to use HTML <p> tags
def generate_lost_email_html(customer_name, vehicle_name): # Renamed for clarity that it outputs HTML
    subject = f"We Miss You, {customer_name}!"
    body = f"""<p>Dear {customer_name},</p>
<p>We noticed you haven't moved forward with your interest in the {vehicle_name}. We understand circumstances change, but we'd love to hear from you if you have any feedback or if there's anything we can do to help.</p>
<p>Sincerely,</p>
<p>AOE Motors Team</p>
"""
    return subject, body

# MODIFIED: generate_welcome_email to use HTML <p> tags
def generate_welcome_email_html(customer_name, vehicle_name): # Renamed for clarity that it outputs HTML
    subject = f"Welcome to the AOE Family, {customer_name}!"
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


# NEW: Function to suggest offer for automation agent
def suggest_offer_llm(lead_details: dict, vehicle_data: dict) -> tuple: # Returns (text_output, html_output)
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
        - Suggest a personalized offer type (e.g., "Complimentary Service Package", "Extended Warranty", "EV Charger for Home", "Discount (e.g., 5-10% off accessories)", "Special Financing Option").
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
    - Output ONLY the recommended offer advice.
    - Format the advice clearly using **markdown paragraphs** or **bullet points** for readability.
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
            temperature=0.7, # You can adjust this (0.0 for stricter, higher for more creative)
            max_tokens=200
        )
        raw_output = completion.choices[0].message.content.strip()
        
        # Convert Markdown output to HTML for email sending
        html_output = md_converter.render(raw_output)

        return raw_output, html_output # Return both markdown and html
    except Exception as e:
        logging.error(f"Error suggesting offer: {e}", exc_info=True)
        return "Error generating offer suggestion. Please try again.", "Error generating offer suggestion. Please try again."


# NEW: Function to generate call talking points for automation agent
def generate_call_talking_points_llm(lead_details: dict, vehicle_data: dict) -> str:
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
            temperature=0.7, # You can adjust this
            max_tokens=300
        )
        raw_output = completion.choices[0].message.content.strip()
        # Ensure markdown lists/paragraphs for UI readability
        if not (raw_output.startswith("AI Talking Points:") and ("*" in raw_output or "-" in raw_output or "\n\n" in raw_output)):
            # If no clear markdown formatting, try to convert lines to paragraphs or list items
            lines = raw_output.split('\n')
            formatted_output = "AI Talking Points:\n\n" + "\n".join(f"- {line.strip()}" for line in lines if line.strip())
        else:
            formatted_output = raw_output # Already has some markdown, just return as is
        return formatted_output
    except Exception as e:
        logging.error(f"Error generating talking points: {e}", exc_info=True)
        return "Error generating talking points. Please try again."

# --- Removed interpret_and_query from here, it's now in the new service ---


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
    st.session_state.error_message = None # Corrected typo in key name from 'error' to 'error_message'

# Display messages stored in session state
if st.session_state.info_message:
    st.info(st.session_state.info_message)
    st.session_state.info_message = None
if st.session_state.success_message:
    st.success(st.session_state.success_message)
    st.session_state.success_message = None
if st.session_state.error_message: # Corrected typo in key name from 'error' to 'error_message'
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

    # --- NEW: Batch Automation Agent Triggers ---
    st.subheader("Automated Agent Actions")
    st.markdown("Use these buttons to trigger agents to process leads in the **current filtered view**.")
    
    col_batch_buttons = st.columns(2)
    
    with col_batch_buttons[0]:
        if st.button("Use Agent to Send Follow-ups", key="batch_followup_btn"):
            if AUTOMOTIVE_AGENT_SERVICE_URL:
                leads_to_process = df[df['action_status'] == 'Follow Up Required']['request_id'].tolist()
                if not leads_to_process:
                    st.session_state.info_message = "No leads with 'Follow Up Required' status in the current filtered view."
                else:
                    st.session_state.info_message = f"Dispatching agent to send follow-up emails for {len(leads_to_process)} leads..."
                    try:
                        response = requests.post(
                            f"{AUTOMOTIVE_AGENT_SERVICE_URL}/trigger-batch-followup-email-agent",
                            json={
                                "lead_ids": leads_to_process,
                                "selected_location": selected_location, # Pass context
                                "start_date": start_date.isoformat(),
                                "end_date": end_date.isoformat()
                            },
                            timeout=120 # Give agents more time
                        )
                        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
                        result = response.json()
                        st.session_state.success_message = result.get("message", "Batch follow-up agent triggered successfully.")
                    except requests.exceptions.Timeout:
                        st.session_state.error_message = "Batch follow-up agent timed out. Please check service logs."
                    except requests.exceptions.RequestException as e:
                        st.session_state.error_message = f"Error communicating with batch follow-up agent: {e}"
                    except json.JSONDecodeError:
                        st.session_state.error_message = "Received invalid JSON from batch follow-up agent."
                st.rerun()
            else:
                st.warning("Automated Agent Service URL not configured.")

    with col_batch_buttons[1]:
        if st.button("Use Agent to Send Offers", key="batch_offer_btn"):
            if AUTOMOTIVE_AGENT_SERVICE_URL:
                # Select leads with lead score > 12 and not Lost/Converted
                leads_to_process = df[
                    (df['numeric_lead_score'] > 12) & 
                    (~df['action_status'].isin(['Lost', 'Converted']))
                ]['request_id'].tolist()

                if not leads_to_process:
                    st.session_state.info_message = "No leads with score > 12 (and not Lost/Converted) in the current filtered view."
                else:
                    st.session_state.info_message = f"Dispatching agent to send offers for {len(leads_to_process)} leads..."
                    try:
                        response = requests.post(
                            f"{AUTOMOTIVE_AGENT_SERVICE_URL}/trigger-batch-offer-agent",
                            json={
                                "lead_ids": leads_to_process,
                                "selected_location": selected_location, # Pass context
                                "start_date": start_date.isoformat(),
                                "end_date": end_date.isoformat()
                            },
                            timeout=120
                        )
                        response.raise_for_status()
                        result = response.json()
                        st.session_state.success_message = result.get("message", "Batch offer agent triggered successfully.")
                    except requests.exceptions.Timeout:
                        st.session_state.error_message = "Batch offer agent timed out. Please check service logs."
                    except requests.exceptions.RequestException as e:
                        st.session_state.error_message = f"Error communicating with batch offer agent: {e}"
                    except json.JSONDecodeError:
                        st.session_state.error_message = "Received invalid JSON from batch offer agent."
                st.rerun()
            else:
                st.warning("Automated Agent Service URL not configured.")
    
    st.markdown("---") # Separator after batch buttons


    # --- Text-to-Query Section (NOW CALLS AGENT SERVICE) ---
    st.subheader("Analytics - Ask a Question! 🤖")
    query_text = st.text_input(
        "Type your question (e.g., 'total leads today', 'hot leads last week', 'total conversions', 'leads lost'):",
        key="nlq_query_input"
    )
    if query_text:
        if AUTOMOTIVE_AGENT_SERVICE_URL:
            st.session_state.info_message = "Querying analytics agent..."
            try:
                # Make API call to the new agent service for analytics
                response = requests.post(
                    f"{AUTOMOTIVE_AGENT_SERVICE_URL}/analyze-query",
                    json={
                        "query_text": query_text,
                        "selected_location": selected_location,
                        "start_date": start_date.isoformat(), # Pass date as ISO string
                        "end_date": end_date.isoformat()    # Pass date as ISO string
                    },
                    timeout=60 # Add a timeout
                )
                response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
                result = response.json()
                result_message = result.get("result_message", "No result message from analytics agent.")
                st.markdown(result_message)
                st.session_state.info_message = None # Clear info message
            except requests.exceptions.Timeout:
                st.session_state.error_message = "Analytics query timed out. Please try again."
            except requests.exceptions.RequestException as e:
                st.session_state.error_message = f"Error communicating with analytics agent: {e}"
            except json.JSONDecodeError:
                st.session_state.error_message = "Received invalid JSON from analytics agent."
            st.rerun() # Rerun to display messages or results
        else:
            st.warning("Analytics service URL not configured. Please set AUTOMOTIVE_AGENT_SERVICE_URL.")
    st.markdown("---")

    for index, row in df.iterrows():
        current_action = row['action_status']
        current_numeric_lead_score = row.get('numeric_lead_score', 0)
        current_lead_score_text = row.get('lead_score', "New")


        # NEW: Logic for Lead Insights Agent Indicator
        score_trend_indicator = ""
        initial_numeric_score_map = {
            "0-3-months": 10,
            "3-6-months": 7,
            "6-12-months": 5,
            "exploring-now": 2
        }
        # Safely get initial score, default to current if timeframe is unexpected
        initial_lead_score = initial_numeric_score_map.get(row.get('time_frame'), current_numeric_lead_score) 

        # Only show indicator if there's a meaningful change and score is not zero
        if current_numeric_lead_score > initial_lead_score and current_numeric_lead_score > 0:
            if current_lead_score_text == "Hot":
                score_trend_indicator = " 🔥📈" # Moved to Hot
            elif current_lead_score_text == "Warm":
                score_trend_indicator = " 🟡📈" # Moved to Warm (from Cold)
        elif current_numeric_lead_score < initial_lead_score:
            score_trend_indicator = " ❄️📉" # Dropped in score
        
        # Add a subtle indicator for leads that were initially cold/warm and became hot/warm
        # This part assumes initial_lead_score is correctly inferred from time_frame
        
        available_actions = ACTION_STATUS_MAP.get(current_lead_score_text, ACTION_STATUS_MAP["New"])

        expander_key = f"expander_{row['request_id']}"
        is_expanded = (st.session_state.expanded_lead_id == row['request_id'])

        with st.expander(
            f"**{row['full_name']}** - {row['vehicle']} - Status: **{current_action}** (Score: {current_lead_score_text} - {current_numeric_lead_score} points){score_trend_indicator}", # ADDED indicator
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

                col_buttons_form = st.columns([1,1]) # Buttons inside the form

                with col_buttons_form[0]:
                    save_button = st.form_submit_button("Save Updates")

                if selected_action == 'Follow Up Required':
                    with col_buttons_form[1]:
                        # This button remains inside the form
                        draft_email_button = st.form_submit_button("Draft Follow-up Email")
                
            # --- START AI BUTTONS OUTSIDE THE FORM (Manual Triggers to Local Dashboard Logic) ---
            # These buttons are not tied to the form submission,
            # allowing immediate actions without saving other form inputs.
            # Initialize session state keys for each button click status for this row
            offer_button_clicked_key = f'offer_button_clicked_{row["request_id"]}'
            talking_points_button_clicked_key = f'talking_points_button_clicked_{row["request_id"]}'

            if offer_button_clicked_key not in st.session_state:
                st.session_state[offer_button_clicked_key] = False
            if talking_points_button_clicked_key not in st.session_state:
                st.session_state[talking_points_button_clicked_key] = False
            
            ai_individual_buttons_cols = st.columns([1,1]) # Create new columns for these two buttons outside the form

            with ai_individual_buttons_cols[0]:
                if selected_action not in ['Lost', 'Converted']: # Only show if not Lost/Converted
                    if st.button("Suggest Offer (AI)", key=f"suggest_offer_btn_outside_{row['request_id']}"):
                        st.session_state[offer_button_clicked_key] = True # Set clicked state for this specific row
                        st.session_state.expanded_lead_id = row['request_id'] # Keep expanded
                        st.rerun() # Immediately rerun to process click
                else:
                    st.info("Offer suggestion not applicable for this status.") # Display message if not applicable

            with ai_individual_buttons_cols[1]:
                if selected_action == 'Call Scheduled': # Only show if status is Call Scheduled
                    if st.button("Generate Talking Points (AI)", key=f"generate_talking_points_btn_outside_{row['request_id']}"):
                        st.session_state[talking_points_button_clicked_key] = True # Set clicked state for this specific row
                        st.session_state.expanded_lead_id = row['request_id'] # Keep expanded
                        st.rerun() # Immediately rerun
            # --- END AI BUTTONS OUTSIDE THE FORM ---


            if save_button: # Logic for Save Updates (inside the form)
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

            # EXISTING: Logic for drafting follow-up email (manual send) - triggered by draft_email_button from inside form
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
                            # draft_subject and draft_body will be Markdown from now on
                            followup_subject, followup_body_markdown = generate_followup_email( # Renamed for clarity
                                row['full_name'], row['email'], row['vehicle'], new_sales_notes, vehicle_details,
                                current_vehicle_brand=current_vehicle_brand_val,
                                sentiment=notes_sentiment
                            )
                            if followup_subject and followup_body_markdown:
                                st.session_state[f"draft_subject_{row['request_id']}"] = followup_subject
                                # Store Markdown in session state for UI display
                                st.session_state[f"draft_body_{row['request_id']}"] = followup_body_markdown
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
                # Retrieve Markdown body for UI display
                draft_body_markdown = st.session_state[f"draft_body_{row['request_id']}"]

                st.subheader("Review Drafted Email:")
                edited_subject = st.text_input("Subject:", value=draft_subject, key=f"reviewed_subject_{row['request_id']}")
                
                # MODIFIED: Use st.markdown for displaying draft body for readability
                st.markdown("**Body (Review Draft):**") # Label for markdown display
                st.markdown(draft_body_markdown, unsafe_allow_html=True) # Render markdown here
                
                # Hidden text area to capture edits, if desired (currently disabled)
                # editable_body_input = st.text_area("Edit Body", value=draft_body_markdown, height=300, key=f"editable_body_{row['request_id']}")
                edited_body_html_for_sending = md_converter.render(draft_body_markdown) # Convert Markdown to HTML for sending

                if ENABLE_EMAIL_SENDING:
                    # Send button will use the converted HTML
                    if st.button(f"Click to Send Drafted Email to {row['full_name']}", key=f"send_draft_email_btn_{row['request_id']}"):
                        if send_email(row['email'], edited_subject, edited_body_html_for_sending, request_id=row['request_id'], event_type="email_followup_sent"):
                            st.session_state.pop(f"draft_subject_{row['request_id']}", None)
                            st.session_state.pop(f"draft_body_{row['request_id']}", None)
                            st.session_state.expanded_lead_id = row['request_id']
                            st.rerun()
                else:
                    st.warning("Email sending is not configured. Please add SMTP credentials to secrets.")

            # NEW: Logic for Dynamic Offer Suggestion (triggered by button_clicked from outside form)
            if st.session_state.get(offer_button_clicked_key, False): # Check session state for click
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
                st.session_state[offer_button_clicked_key] = False # Reset click state
                st.rerun()
            
            # Display suggested offer if available in session state
            if f"suggested_offer_{row['request_id']}" in st.session_state:
                st.subheader("AI-Suggested Offer:")
                st.markdown(st.session_state[f"suggested_offer_{row['request_id']}"])
                st.markdown("---")


            # NEW: Logic for Talking Points (triggered by button_clicked from outside form)
            if st.session_state.get(talking_points_button_clicked_key, False): # Check session state for click
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
                st.session_state[talking_points_button_clicked_key] = False # Reset click state
                st.rerun()
            
            # Display talking points if available in session state
            if f"call_talking_points_{row['request_id']}" in st.session_state:
                st.subheader("AI-Generated Talking Points:")
                st.markdown(st.session_state[f"call_talking_points_{row['request_id']}"])
                st.markdown("---")

else:
    st.info("No test drive bookings to display yet. Submit a booking from your frontend!")

st.markdown("---")