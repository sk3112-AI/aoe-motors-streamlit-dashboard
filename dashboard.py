import streamlit as st
import pandas as pd
import smtplib
from email.message import EmailMessage
from datetime import datetime
from supabase import create_client, Client
import os
import logging

# 🔧 Logging setup
logging.basicConfig(level=logging.INFO)

# 🌐 Supabase config
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

# 📧 Email config
email_host = os.getenv("EMAIL_HOST")
email_port = int(os.getenv("EMAIL_PORT", "465"))
email_address = os.getenv("EMAIL_ADDRESS")
email_password = os.getenv("EMAIL_PASSWORD")
ENABLE_EMAIL_SENDING = all([email_host, email_port, email_address, email_password])

# 📬 Email sending
def send_email(recipient, subject, body):
    try:
        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = email_address
        msg["To"] = recipient

        with smtplib.SMTP_SSL(email_host, email_port) as server:
            server.login(email_address, email_password)
            server.send_message(msg)

        logging.info(f"Email sent to {recipient}")
        return True
    except Exception as e:
        logging.error(f"Failed to send email: {e}", exc_info=True)
        return False

# 🧠 Natural language query interpreter
def interpret_and_query(query, df):
    query = query.lower()
    now = pd.Timestamp.now(tz='UTC')
    last_week = now - pd.Timedelta(days=7)
    last_month = now - pd.Timedelta(days=30)

    df["booking_timestamp"] = pd.to_datetime(df["booking_timestamp"], utc=True)

    if "hot" in query and "week" in query:
        count = df[(df["lead_score"] == "Hot") & (df["booking_timestamp"] >= last_week)].shape[0]
        return f"Hot leads in the last 7 days: {count}"
    elif "warm" in query:
        count = df[df["lead_score"] == "Warm"].shape[0]
        return f"Warm leads: {count}"
    elif "cold" in query:
        count = df[df["lead_score"] == "Cold"].shape[0]
        return f"Cold leads: {count}"
    elif "converted" in query:
        count = df[df["action_status"] == "Converted"].shape[0]
        return f"All-time converted leads: {count}"
    elif "lost" in query:
        count = df[df["action_status"] == "Lost"].shape[0]
        return f"Leads marked as lost: {count}"
    elif "total" in query:
        count = df.shape[0]
        return f"Total leads: {count}"
    else:
        return "🤖 Sorry, I couldn’t understand the question."

# 📊 App UI starts
st.set_page_config(page_title="AOE Motors Dashboard", layout="wide")
st.title("AOE Motors Dashboard")

st.markdown("✅ App loaded successfully.")

# 🧭 Sidebar
st.sidebar.header("Filters")
st.markdown("🧭 Sidebar filters loaded.")

status_filter = st.sidebar.selectbox("Filter by status", ["All", "New", "Converted", "Lost", "Follow-up"])
score_filter = st.sidebar.selectbox("Filter by score", ["All", "Hot", "Warm", "Cold"])

# 🧠 Load data
data = supabase.table("test_drives").select("*").execute()
df = pd.DataFrame(data.data)

if not df.empty:
    df["booking_timestamp"] = pd.to_datetime(df["booking_timestamp"], utc=True)

    # Apply filters
    if status_filter != "All":
        df = df[df["action_status"] == status_filter]
    if score_filter != "All":
        df = df[df["lead_score"] == score_filter]

    st.markdown("📊 Rendering lead dashboard…")

    # Show leads
    for _, row in df.iterrows():
        st.subheader(f"{row['name']} ({row['lead_score']})")
        st.write(f"Status: {row['action_status']}")
        st.write(f"Email: {row['email']}")
        st.write(f"Booking Time: {row['booking_timestamp']}")

        selected_action = st.selectbox("Update action", ["None", "Converted", "Lost", "Follow-up"], key=row['id'])
        if st.button("Update", key=f"update_{row['id']}"):
            try:
                supabase.table("test_drives").update({"action_status": selected_action}).eq("id", row["id"]).execute()
                st.success("Status updated!")

                # Send follow-up emails
                if selected_action in ["Converted", "Lost"]:
                    email_subject = "Thank you!" if selected_action == "Converted" else "We Miss You"
                    email_body = f"Hi {row['name']},\n\nThank you for visiting AOE Motors. We're here if you need anything!"
                    st.markdown("📤 Sending email…")
                    sent = send_email(row["email"], email_subject, email_body)
                    if sent:
                        st.success("Email sent.")
                    else:
                        st.warning("Failed to send email.")

            except Exception as e:
                logging.error(f"Error updating booking: {e}")
                st.error("Could not update status.")

# 🔍 Analytics input
st.markdown("---")
st.subheader("Analytics - Ask a Question! 🤖")
query_text = st.text_input("Type your question (e.g., 'total leads today', 'hot leads last week', 'total conversions', 'leads lost'):")

if query_text:
    st.markdown("🔍 Running analytics query…")
    result_message = interpret_and_query(query_text, df)
    st.success(result_message)
