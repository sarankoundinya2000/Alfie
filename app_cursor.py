import streamlit as st
import datetime
import re
import pytz
import os
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from groq import Groq
from dotenv import load_dotenv
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

#print("Starting application...")

# Load environment variables
#load_dotenv('groqapi.env')

# Initialize Groq client
client = Groq(api_key=st.secrets['GROQ_API'])
#client = Groq(api_key=os.environ['GROQ_API_KEY'])

# Define scopes for Google APIs
SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/contacts.readonly'
]

# Paths for credentials and token
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"

def init_session_state():
    print("Initializing session state...")
    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    if 'user_email' not in st.session_state:
        st.session_state.user_email = None
    if 'calendar_service' not in st.session_state:
        st.session_state.calendar_service = None
    if 'contacts_service' not in st.session_state:
        st.session_state.contacts_service = None
    if 'contact_options' not in st.session_state:
        st.session_state.contact_options = {}
    if 'needs_email' not in st.session_state:
        st.session_state.needs_email = None
    if 'selected_contact' not in st.session_state:
        st.session_state.selected_contact = None
    print("Session state initialized")

def authenticate_google():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if not os.path.exists(CREDENTIALS_FILE):
            st.warning("Google OAuth credentials.json not found. Please enter your Google OAuth Client details.")
            client_id = st.text_input("Client ID")
            client_secret = st.text_input("Client Secret", type="password")
            redirect_uris = st.text_area("Redirect URIs (comma separated)")
            auth_uri = st.text_input("Auth URI", value="https://accounts.google.com/o/oauth2/auth")
            token_uri = st.text_input("Token URI", value="https://oauth2.googleapis.com/token")
            project_id = st.text_input("Project ID")
            if st.button("Save Credentials and Authenticate"):
                creds_dict = {
                    "installed": {
                        "client_id": client_id,
                        "project_id": project_id,
                        "auth_uri": auth_uri,
                        "token_uri": token_uri,
                        "client_secret": client_secret,
                        "redirect_uris": [uri.strip() for uri in redirect_uris.split(",") if uri.strip()]
                    }
                }
                with open(CREDENTIALS_FILE, 'w') as f:
                    json.dump(creds_dict, f)
                st.success("credentials.json created. Please click Authenticate again.")
                st.stop()
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return creds

def get_contact_suggestions(contacts_service, query):
    """Get contact suggestions based on the query, focusing on email contacts only"""
    try:
        # Search in user's contacts
        results = contacts_service.people().searchDirectoryPeople(
            query=query,
            readMask='names,emailAddresses',
            sources=['DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE', 'DIRECTORY_SOURCE_TYPE_DOMAIN_CONTACT'],
            pageSize=10
        ).execute()
        
        # Also search in user's connections
        connections = contacts_service.people().connections().list(
            resourceName='people/me',
            pageSize=100,
            personFields='names,emailAddresses',
            sortOrder='LAST_MODIFIED_DESCENDING'
        ).execute()
        
        contacts = []
        
        # Process directory search results
        if 'people' in results:
            for person in results['people']:
                if 'emailAddresses' in person and 'names' in person:
                    name = person['names'][0].get('displayName', '')
                    email = person['emailAddresses'][0].get('value', '')
                    if email and query.lower() in name.lower():
                        contacts.append({
                            'name': name,
                            'email': email,
                            'source': 'directory'
                        })
        
        # Process connections (personal contacts)
        if 'connections' in connections:
            for person in connections['connections']:
                if 'emailAddresses' in person and 'names' in person:
                    name = person['names'][0].get('displayName', '')
                    email = person['emailAddresses'][0].get('value', '')
                    if email and query.lower() in name.lower():
                        contacts.append({
                            'name': name,
                            'email': email,
                            'source': 'contacts'
                        })
        
        # Search in calendar history
        calendar_service = st.session_state.calendar_service
        events_result = calendar_service.events().list(
            calendarId='primary',
            maxResults=100,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        # Extract unique attendees from past events
        past_contacts = set()
        for event in events_result.get('items', []):
            attendees = event.get('attendees', [])
            for attendee in attendees:
                email = attendee.get('email', '')
                name = attendee.get('displayName', email.split('@')[0])
                if email and query.lower() in name.lower():
                    past_contacts.add((name, email))
        
        # Add past contacts to suggestions
        for name, email in past_contacts:
            if not any(c['email'] == email for c in contacts):
                contacts.append({
                    'name': name,
                    'email': email,
                    'source': 'calendar'
                })
        
        # Sort contacts by relevance
        # Priority: exact matches first, then starts with, then contains
        def sort_key(contact):
            name = contact['name'].lower()
            query_lower = query.lower()
            if name == query_lower:
                return (0, name)
            elif name.startswith(query_lower):
                return (1, name)
            else:
                return (2, name)
        
        contacts.sort(key=sort_key)
        
        # Remove duplicates while preserving order
        seen_emails = set()
        unique_contacts = []
        for contact in contacts:
            if contact['email'] not in seen_emails:
                seen_emails.add(contact['email'])
                unique_contacts.append(contact)
        
        return unique_contacts[:10]  # Limit to top 10 results
    except Exception as e:
        st.error(f"Error searching contacts: {e}")
        return []

def get_previous_attendee(calendar_service, name):
    """Search for attendee in previous calendar events and return the email directly"""
    options = search_attendee(calendar_service, name)
    
    if options and len(options) > 0:
        # Return the first email by default
        return options[0][0]
    
    # If no previous attendee found, return a placeholder
    return f"{name.lower().replace(' ', '')}@example.com"

def parse_attendees(attendees_input):
    """Parse multiple attendees from input string or list"""
    if isinstance(attendees_input, list):
        # If it's already a list, just clean each name
        return [name.strip() for name in attendees_input if name.strip()]
    elif isinstance(attendees_input, str):
        # If it's a string, split by comma and clean
        return [name.strip() for name in attendees_input.split(',') if name.strip()]
    else:
        # If it's neither, return empty list
        return []

def get_contact_email(contacts_service, name):
    """Enhanced version to get contact email with suggestions"""
    contacts = get_contact_suggestions(contacts_service, name)
    if contacts:
        # If only one contact found, return it directly
        if len(contacts) == 1:
            return contacts[0]['email']
        
        # If multiple contacts found, let user choose
        options = []
        for contact in contacts:
            source_icon = "📧" if contact['source'] == 'contacts' else "👥" if contact['source'] == 'directory' else "📅"
            option = f"{source_icon} {contact['name']} ({contact['email']})"
            options.append(option)
        
        selected = st.selectbox(
            f"Multiple contacts found for '{name}'. Please select:",
            options,
            help="📧: Personal Contact | 👥: Directory | 📅: Calendar History"
        )
        
        if selected:
            email = selected.split('(')[-1].rstrip(')')
            return email
    return None

def normalize_time(time_str):
    """
    Convert various time formats to a standard format for comparison.
    Handles formats like: 2pm, 2:00pm, 2:00 PM, 14:00, etc.
    """
    # Remove any spaces and convert to lowercase
    time_str = time_str.lower().replace(" ", "")
    
    # If time is in 24-hour format (e.g., "14:00")
    if ":" in time_str and ("am" not in time_str and "pm" not in time_str):
        try:
            time_obj = datetime.datetime.strptime(time_str, "%H:%M")
            return time_obj.strftime("%H:%M")
        except:
            return None
    
    # Handle formats like "2pm", "2:00pm"
    try:
        # Add ":00" if minutes are missing
        if ":" not in time_str:
            time_str = time_str.replace("am", ":00am").replace("pm", ":00pm")
        
        # Parse the time
        time_obj = datetime.datetime.strptime(time_str, "%I:%M%p")
        return time_obj.strftime("%H:%M")
    except:
        return None

def check_calendar(calendar_service, specific_date=None, specific_time=None):
    """Check calendar events for a specific date and time"""
    try:
        # Convert specific_date to datetime
        if specific_date:
            try:
                # Handle flexible date formats
                # First, check if we need to standardize the format
                if '/' not in specific_date and '-' not in specific_date:
                    # Try to parse using natural language
                    try:
                        date_obj = datetime.datetime.strptime(specific_date, "%B %d")
                        # If successful, it's a month day format, add current year
                        current_year = datetime.datetime.now().year
                        specific_date = date_obj.strftime(f"%m/%d/{current_year}")
                    except ValueError:
                        pass
                
                # Now handle the standard format
                date_str = specific_date.replace('-', '/')
                
                # If only MM/DD is provided, add current year
                if date_str.count('/') == 1:
                    current_year = datetime.datetime.now().year
                    date_str = f"{date_str}/{current_year}"
                
                date_obj = datetime.datetime.strptime(date_str, "%m/%d/%Y")
                start_of_day = date_obj.replace(hour=0, minute=0, second=0, microsecond=0)
                end_of_day = date_obj.replace(hour=23, minute=59, second=59, microsecond=999999)
                
                # Convert to UTC
                timezone = pytz.timezone("America/New_York")
                start_of_day = timezone.localize(start_of_day).astimezone(pytz.UTC)
                end_of_day = timezone.localize(end_of_day).astimezone(pytz.UTC)
                
                timeMin = start_of_day.isoformat()
                timeMax = end_of_day.isoformat()
            except ValueError:
                st.error("Couldn't understand the date format. For dates outside current year, please use MM/DD/YYYY format.")
                return [], False, None
        else:
            timeMin = datetime.datetime.utcnow().isoformat() + 'Z'
            timeMax = None
        
        events_result = calendar_service.events().list(
            calendarId="primary",
            timeMin=timeMin,
            timeMax=timeMax,
            maxResults=20,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        event_list = []
        has_conflict = False
        conflict_details = None
        
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            
            # Convert to local time
            if 'T' in start:  # This is a datetime
                start_dt = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
                local_tz = pytz.timezone("America/New_York")
                local_dt = start_dt.astimezone(local_tz)
                event_time = local_dt.strftime("%I:%M %p").lstrip('0')
                event_date = local_dt.strftime("%m/%d/%Y")
                
                # Check for conflict if specific_time is provided
                if specific_time:
                    normalized_time = normalize_time(specific_time)
                    event_time_24 = local_dt.strftime("%H:%M")
                    if normalized_time == event_time_24:
                        has_conflict = True
                        conflict_details = {
                            "time": event_time,
                            "summary": event['summary'],
                            "attendees": [attendee.get('email', '') for attendee in event.get('attendees', [])]
                        }
            else:  # This is a date
                event_time = "All day"
                event_date = start
            
            attendees = event.get('attendees', [])
            attendee_list = [f"{a.get('displayName', 'No name')} ({a.get('email', 'No email')})" 
                           for a in attendees if not a.get('self', False)]
            
            event_list.append({
                "date": event_date,
                "time": event_time,
                "event": event['summary'],
                "attendees": attendees,
                "meet_link": event.get('hangoutLink', 'No meet link')
            })
        
        return event_list, has_conflict, conflict_details
    except Exception as e:
        st.error(f"Error checking calendar: {e}")
        return [], False, None

def book_appointment(calendar_service, date, time, attendees, summary="Meeting"):
    timezone = "America/New_York"
    
    # First check for conflicts
    events, has_conflict, conflict_details = check_calendar(calendar_service, date, time)
    
    if has_conflict:
        conflict_message = f"""
        ⚠️ There is already a meeting scheduled at this time:
        - Time: {conflict_details['time']}
        - Meeting: {conflict_details['summary']}
        - Attendees: {', '.join(conflict_details['attendees'])}
        
        Please choose a different time.
        """
        return conflict_message
    
    # If no conflict, proceed with booking
    normalized_time = normalize_time(time)
    if not normalized_time:
        return "❌ Invalid time format. Please use formats like '2pm', '2:00pm', '2:00 PM', or '14:00'"
    
    time_obj = datetime.datetime.strptime(normalized_time, "%H:%M")
    display_time = time_obj.strftime("%I:%M %p").lstrip("0")
    
    start_datetime = datetime.datetime.strptime(f"{date} {display_time}", "%m/%d/%Y %I:%M %p")
    start_datetime = pytz.timezone(timezone).localize(start_datetime)
    end_datetime = start_datetime + datetime.timedelta(hours=1)
    
    # Format attendees for the event
    formatted_attendees = [{'email': email} for email in attendees if email]
    
    # Create event with Google Meet conferencing
    event = {
        'summary': summary,
        'start': {'dateTime': start_datetime.isoformat(), 'timeZone': timezone},
        'end': {'dateTime': end_datetime.isoformat(), 'timeZone': timezone},
        'attendees': formatted_attendees,
        'conferenceData': {
            'createRequest': {
                'requestId': f"{start_datetime.timestamp()}-{','.join(attendees)}",
                'conferenceSolutionKey': {'type': 'hangoutsMeet'}
            }
        }
    }
    
    try:
        event = calendar_service.events().insert(
            calendarId="primary",
            body=event,
            conferenceDataVersion=1,
            sendUpdates='all'
        ).execute()
        
        # Get the meeting link
        meet_link = event.get('hangoutLink', '')
        
        attendees_str = ', '.join(attendees)
        return f"✅ Appointment booked for {summary} at {display_time} on {date} with {attendees_str}.\nMeeting Link: {meet_link}"
    except Exception as e:
        return f"❌ Error creating event: {str(e)}"

def parse_input(user_input, today):
    """Extract meeting details from user input"""
    # First check if it's an events query
    input_lower = user_input.lower()
    
    # Handle events queries
    if "events" in input_lower or "meetings" in input_lower:
        if "today" in input_lower:
            return {
                "type": "events_query",
                "date": today,
                "query_type": "today"
            }
        elif "tomorrow" in input_lower:
            tomorrow = (datetime.datetime.strptime(today, "%m-%d-%Y") + datetime.timedelta(days=1)).strftime("%m-%d-%Y")
            return {
                "type": "events_query",
                "date": tomorrow,
                "query_type": "tomorrow"
            }
        elif "on" in input_lower or "for" in input_lower:
            # Try to extract date from the query
            chat_completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "Extract date from the text. If only month and day are provided (like 'April 8th'), assume it's for the current year and return in MM/DD/YYYY format. Return only the date."},
                    {"role": "user", "content": user_input}
                ],
                model="llama-3.3-70b-versatile",
                temperature=0.5,
                top_p=1,
                stream=False
            )
            extracted_date = chat_completion.choices[0].message.content.strip()
            
            # Check if year is missing and add current year if needed
            if re.match(r'^\d{1,2}/\d{1,2}$', extracted_date):
                current_year = datetime.datetime.now().year
                extracted_date = f"{extracted_date}/{current_year}"
            
            return {
                "type": "events_query",
                "date": extracted_date,
                "query_type": "specific_date"
            }
    
    # Handle meeting scheduling
    # Check for email in the input
    email_pattern = r'[\w\.-]+@[\w\.-]+\.\w+'
    found_email = re.search(email_pattern, user_input)
    
    # Modify the system prompt to better handle multiple attendees
    chat_completion = client.chat.completions.create(
        messages=[
            {
                "role": "system", 
                "content": """Extract meeting details from the text. For multiple attendees, return them as a list.
                If an email is found, use it directly. Format names consistently."""
            },
            {
                "role": "user", 
                "content": f"""Extract meeting details from: '{user_input}' and today's date {today}. 
                Return a JSON object with: 
                - 'Person' (name or email, if multiple names return as list), 
                - 'date' (MM/DD/YYYY), 
                - 'time', 
                - 'email' (if found in input), 
                - 'summary'. 
                Example for multiple people: "Person": ["John", "Sarah"]
                **NOTE - only return json object no other text"""
            }
        ],
        model="llama-3.3-70b-versatile",
        temperature=0.5,
        top_p=1,
        stream=False,
        response_format={"type": "json_object"}
    )
    
    result = json.loads(chat_completion.choices[0].message.content)
    result["type"] = "meeting_request"
    
    # If email was found in input, add it to result
    if found_email:
        result["email"] = found_email.group(0)
    
    # Ensure Person is always a list
    if "Person" in result and isinstance(result["Person"], str):
        result["Person"] = [result["Person"]]
    
    return result

def send_email(to_address, body, meet_link=None):
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        subject = "Event booked"
        
        # Add meeting link to email body if available
        if meet_link:
            body += f"\n\nJoin the meeting at: {meet_link}"
            
        msg = 'Subject: {}\n\n{}'.format(subject, body)
        server.starttls()
        server.login('koundinyasaran@gmail.com', 'gugr gdxr qvwy ilkg')
        server.sendmail('koundinyasaran@gmail.com', to_address, msg)
        server.quit()
        st.success("Email sent successfully")
    except Exception as e:
        st.error(f"Failed to send email: {e}")

def apply_custom_css():
    st.markdown("""
        <style>
        /* Main app styling */
        .stApp {
            max-width: 1200px;
            margin: 0 auto;
            padding: 1rem;
        }
        
        /* Header styling */
        .app-header {
            display: flex;
            align-items: center;
            padding: 1rem 0;
            margin-bottom: 2rem;
        }
        
        .app-logo {
            font-size: 24px;
            font-weight: bold;
            color: #0066FF;
            text-decoration: none;
            display: flex;
            align-items: center;
        }
        
        .app-logo img {
            margin-right: 10px;
        }
        
        /* Card styling */
        .css-1r6slb0 {
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
            padding: 2rem;
            background: white;
        }
        
        /* Button styling */
        .stButton > button {
            background-color: #0066FF;
            color: white;
            border: none;
            border-radius: 5px;
            padding: 0.5rem 1rem;
            font-weight: 500;
        }
        
        .stButton > button:hover {
            background-color: #0052CC;
        }
        
        /* Input field styling */
        .stTextInput > div > div > input {
            border-radius: 5px;
            border: 1px solid #E0E0E0;
            padding: 0.5rem;
        }
        
        /* Feature list styling */
        .feature-list {
            display: flex;
            gap: 1rem;
            margin: 2rem 0;
        }
        
        .feature-item {
            display: flex;
            align-items: center;
            color: #4A4A4A;
        }
        
        .feature-item svg {
            margin-right: 0.5rem;
            color: #00C853;
        }
        
        /* Tag styling */
        .tag {
            background-color: #E3F2FD;
            color: #0066FF;
            padding: 0.25rem 0.75rem;
            border-radius: 15px;
            font-size: 14px;
            display: inline-block;
            margin-bottom: 1rem;
        }
        
        /* Typography */
        h1 {
            font-size: 3rem;
            font-weight: 700;
            margin-bottom: 1rem;
            color: #1A1A1A;
        }
        
        .subtitle {
            font-size: 1.2rem;
            color: #666666;
            margin-bottom: 2rem;
        }
        
        /* How it works section */
        .how-it-works {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 2rem;
            margin: 3rem 0;
        }
        
        .step-card {
            background: white;
            padding: 2rem;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
        }
        
        .step-icon {
            background-color: #E3F2FD;
            width: 48px;
            height: 48px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin-bottom: 1rem;
        }
        </style>
    """, unsafe_allow_html=True)

def main():
    st.set_page_config(
        page_title="Alfie - Smart Meeting Assistant",
        page_icon="📅",
        layout="wide"
    )
    
    apply_custom_css()
    
    # Initialize session state
    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    if 'user_email' not in st.session_state:
        st.session_state.user_email = None
    if 'calendar_service' not in st.session_state:
        st.session_state.calendar_service = None
    if 'contacts_service' not in st.session_state:
        st.session_state.contacts_service = None
    if 'contact_options' not in st.session_state:
        st.session_state.contact_options = {}
    if 'needs_email' not in st.session_state:
        st.session_state.needs_email = None
    if 'selected_contact' not in st.session_state:
        st.session_state.selected_contact = None

    # Header with logo and navigation
    st.markdown("""
        <div class="app-header">
            <a href="#" class="app-logo">
                📅 Alfie
            </a>
        </div>
    """, unsafe_allow_html=True)

    if not st.session_state.authenticated:
        # Landing page content
        st.markdown('<div class="tag">Smart Meeting Scheduling</div>', unsafe_allow_html=True)
        st.markdown("""
            <h1>Schedule meetings with <span style="color: #0066FF;">natural language</span></h1>
            <p class="subtitle">Simply describe when you want to meet, and we'll handle the rest. 
            Alfie makes scheduling as easy as having a conversation.</p>
        """, unsafe_allow_html=True)

        # Feature list
        st.markdown("""
            <div class="feature-list">
                <div class="feature-item">
                    ✓ Google Meet Integration
                </div>
                <div class="feature-item">
                    ✓ Natural Language Processing
                </div>
                <div class="feature-item">
                    ✓ Calendar Syncing
                </div>
            </div>
        """, unsafe_allow_html=True)

        if st.button("Sign in with Google"):
            try:
                credentials = authenticate_google()
                if credentials:
                    st.session_state.calendar_service = build("calendar", "v3", credentials=credentials)
                    st.session_state.contacts_service = build("people", "v1", credentials=credentials)
                    user_info = build("oauth2", "v2", credentials=credentials).userinfo().get().execute()
                    st.session_state.user_email = user_info['email']
                    st.session_state.authenticated = True
                    st.rerun()
            except Exception as e:
                st.error(f"Authentication failed: {e}")

    else:
        # Main application interface after authentication
        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.markdown('<div class="tag">Tell me about your meeting</div>', unsafe_allow_html=True)
            
            # Main input field
            user_input = st.text_input(
                "",
                placeholder="e.g., 'Book a meeting with Aaron around 2pm tomorrow'",
                key="user_input"
            )

            if user_input:
                with st.spinner("Processing your request..."):
                    response = parse_input(user_input, datetime.date.today().strftime("%m-%d-%Y"))
                    
                    if response.get("type") == "events_query":
                        events, _, _ = check_calendar(st.session_state.calendar_service, response.get("date"))
                        if events:
                            query_type = response.get("query_type", "")
                            if query_type == "today":
                                title = "Today's Events"
                            elif query_type == "tomorrow":
                                title = "Tomorrow's Events"
                            else:
                                title = f"Events on {response.get('date')}"
                            
                            st.markdown(f"### {title}")
                            
                            for event in events:
                                with st.expander(f"{event.get('time', 'No time')} - {event.get('event', 'No event')}", expanded=True):
                                    st.write(f"**Time:** {event.get('time', 'No time')}")
                                    st.write(f"**Summary:** {event.get('event', 'No event')}")
                                    if event.get('attendees'):
                                        st.write("**Attendees:**")
                                        for attendee in event['attendees']:
                                            if not attendee.get('self', False):
                                                st.write(f"- {attendee.get('displayName', 'No name')} ({attendee.get('email', 'No email')})")
                                    if event.get('meet_link') != 'No meet link':
                                        st.write(f"**Meet Link:** {event.get('meet_link')}")
                        else:
                            st.info(f"No events found for {response.get('date')}")
                    
                    elif response.get("type") == "meeting_request":
                        # Handle meeting request
                        meeting_details = response
                        st.markdown("### Meeting Details")
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            st.markdown(f"**📅 Date:** {meeting_details.get('date')}")
                            st.markdown(f"**🕒 Time:** {meeting_details.get('time')}")
                        
                        with col2:
                            st.markdown(f"**👤 Person:** {meeting_details.get('Person')}")
                            st.markdown(f"**📝 Summary:** {meeting_details.get('summary', 'Meeting')}")

                        # Handle multiple attendees
                        attendees = []
                        if meeting_details.get("email"):  # If email was found in input
                            attendees.append(meeting_details["email"])
                        elif meeting_details.get("Person"):
                            person_names = parse_attendees(meeting_details["Person"])
                            
                            # For each name, search calendar history and display options immediately
                            contact_emails = {}
                            for name in person_names:
                                # Use search_attendee to get options without adding to session state
                                options = search_attendee(st.session_state.calendar_service, name)
                                
                                if options and len(options) > 0:
                                    # Show options in a dropdown instead of radio buttons
                                    st.write(f"### Select contact for {name}:")
                                    contact_options = {}
                                    # Add a placeholder as first option
                                    contact_options[""] = "-- Select a contact --"
                                    
                                    for email, details in options:
                                        contact_name = details['name']
                                        meeting_count = details['count']
                                        contact_options[email] = f"{contact_name} ({email}) - {meeting_count} meetings"
                                    
                                    selected_email = st.selectbox(
                                        f"Contact for {name}:",
                                        options=list(contact_options.keys()),
                                        format_func=lambda x: contact_options[x],
                                        key=f"contact_{name}"
                                    )
                                    
                                    if selected_email:  # Only add if a non-empty option is selected
                                        contact_emails[name] = selected_email
                                else:
                                    # If no contact found, ask for email
                                    email = st.text_input(
                                        f"Please enter email for {name}:",
                                        key=f"email_{name}"
                                    )
                                    if email and '@' in email:
                                        contact_emails[name] = email
                            
                            # Add all collected emails to attendees
                            for name in person_names:
                                if name in contact_emails and contact_emails[name]:
                                    attendees.append(contact_emails[name])
                        
                        if attendees and len(attendees) == len(parse_attendees(meeting_details["Person"])):
                            if st.button("Schedule Meeting"):
                                response = book_appointment(
                                    st.session_state.calendar_service,
                                    meeting_details["date"],
                                    meeting_details["time"],
                                    attendees,
                                    meeting_details.get("summary", "Meeting")
                                )
                                
                                if "⚠️" in response:
                                    st.warning(response)
                                else:
                                    meet_link = None
                                    if "Meeting Link:" in response:
                                        meet_link = response.split("Meeting Link:")[1].strip()
                                    
                                    st.success(response)
                                    
                                    body = f"""
                                    Hello,
                                    
                                    Your appointment has been scheduled with the following details:
                                    - Date: {meeting_details['date']}
                                    - Time: {meeting_details['time']}
                                    - Summary: {meeting_details.get('summary', 'Meeting')}
                                    - Attendees: {', '.join(attendees)}
                                    
                                    Thank you!
                                    """
                                    for attendee in attendees:
                                        send_email(attendee, body, meet_link)
                        else:
                            if not st.session_state.needs_email:
                                st.info("Please provide email addresses for all attendees to schedule the meeting.")

        with col2:
            if st.button("Sign Out"):
                st.session_state.authenticated = False
                st.session_state.user_email = None
                st.session_state.calendar_service = None
                st.session_state.contacts_service = None
                if os.path.exists(TOKEN_FILE):
                    os.remove(TOKEN_FILE)
                st.rerun()

        # How It Works section
        st.markdown("""
            <div class="card">
                <h2 style="text-align: center; margin-bottom: 2rem;">How It Works</h2>
                <div class="how-it-works">
                    <div class="step-card">
                        <div class="step-icon">📅</div>
                        <h3>View Your Events</h3>
                        <p>Simply ask to see your events for any day:<br>
                        - "Show my events today"<br>
                        - "What meetings do I have tomorrow?"<br>
                        - "Show events on April 8th"<br>
                        - For past/future years, use MM/DD/YYYY format</p>
                    </div>
                    <div class="step-card">
                        <div class="step-icon">👥</div>
                        <h3>Schedule Meetings</h3>
                        <p>Tell us who you want to meet with:<br>
                        - Use names or email addresses<br>
                        - We'll find their contact info<br>
                        - Select from previous contacts</p>
                    </div>
                    <div class="step-card">
                        <div class="step-icon">✨</div>
                        <h3>Instant Setup</h3>
                        <p>We'll handle everything:<br>
                        - Create Google Meet link<br>
                        - Send calendar invites<br>
                        - Notify all participants</p>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)

def search_attendee(calendar_service, name):
    """Search for attendee in previous calendar events within the last year"""
    try:
        # Calculate time range (last year)
        now = datetime.datetime.utcnow()
        one_year_ago = now - datetime.timedelta(days=365)
        
        # Get past calendar events
        events_result = calendar_service.events().list(
            calendarId='primary',
            timeMin=one_year_ago.isoformat() + 'Z',
            timeMax=now.isoformat() + 'Z',
            maxResults=2000,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        # Extract unique attendees from past events
        past_attendees = {}  # email -> {name, count, last_date}
        total_events_checked = 0
        
        for event in events_result.get('items', []):
            total_events_checked += 1
            event_date = event['start'].get('dateTime', event['start'].get('date'))
            attendees = event.get('attendees', [])
            for attendee in attendees:
                email = attendee.get('email', '')
                attendee_name = attendee.get('displayName', email.split('@')[0])
                if email and name.lower() in attendee_name.lower():
                    if email not in past_attendees:
                        past_attendees[email] = {
                            'name': attendee_name,
                            'count': 1,
                            'last_date': event_date,
                            'events': [event['summary']]
                        }
                    else:
                        past_attendees[email]['count'] += 1
                        past_attendees[email]['events'].append(event['summary'])
                        if event_date > past_attendees[email]['last_date']:
                            past_attendees[email]['last_date'] = event_date
        
        if past_attendees:
            # Sort by meeting frequency and recency
            sorted_attendees = sorted(
                past_attendees.items(),
                key=lambda x: (x[1]['count'], x[1]['last_date']),
                reverse=True
            )
            return sorted_attendees
        
        return []
        
    except Exception as e:
        st.error(f"Error searching calendar history: {e}")
        return []

if __name__ == "__main__":
    if not os.path.exists(CREDENTIALS_FILE):
        authenticate_google()  # This will show the UI and stop if file is missing
        exit()
    print("Starting main function...")
    main()
    print("Application finished running")
